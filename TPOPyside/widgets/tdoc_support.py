from __future__ import annotations

import html
import os
import re
import uuid
from pathlib import Path
from typing import Callable

from PySide6.QtGui import QColor, QPalette, QTextCursor

from TPOPyside.widgets.tdoc_core import (
    DOC_SUFFIX,
    PROJECT_MARKER_FILENAME,
    TDocEditorWidget,
    TDocProjectIndex,
    parse_doc_frontmatter,
    parse_file_link,
)

_TDOC_FRONTMATTER_MSG_RE = re.compile(r"^(?P<rel>[^:\n]+\.tdoc):(?P<line>\d+)\s+-\s+")
_TDOC_REF_RE = re.compile(r"(?P<rel>[^\s,]+\.tdoc)#L(?P<line>\d+)")
_TDOC_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}


def is_tdoc_document_path(path: str | None) -> bool:
    text = str(path or "").strip().lower()
    return bool(text) and text.endswith(DOC_SUFFIX)


def is_tdoc_project_marker_path(path: str | None) -> bool:
    text = str(path or "").strip()
    return bool(text) and Path(text).name == PROJECT_MARKER_FILENAME


def is_tdoc_related_path(path: str | None) -> bool:
    return is_tdoc_document_path(path) or is_tdoc_project_marker_path(path)


def resolve_tdoc_root_for_path(file_path: str | None, *, project_root: str | None = None) -> str:
    """Resolve TDOC root for a file.

    Priority:
    1) nearest ancestor named '.tdocprojects'
    2) nearest ancestor containing '.tdocprojects/' directory
    3) nearest ancestor containing '.tdocproject' marker
    4) project root '.tdocprojects/'
    5) project root '.tdocproject'
    6) file directory (or project root fallback)
    """

    project = str(project_root or "").strip()
    project_path = Path(project) if project else None

    text = str(file_path or "").strip()
    if text:
        anchor = Path(text)
        if anchor.is_file() or (anchor.suffix and not anchor.exists()):
            anchor = anchor.parent
    elif project_path is not None:
        anchor = project_path
    else:
        anchor = Path.cwd()

    try:
        anchor = anchor.resolve()
    except Exception:
        anchor = anchor.absolute()

    for candidate in [anchor, *anchor.parents]:
        if candidate.name == ".tdocprojects":
            return str(candidate)
        hidden_root = candidate / ".tdocprojects"
        if hidden_root.is_dir():
            return str(hidden_root)
        marker = candidate / PROJECT_MARKER_FILENAME
        if marker.is_file():
            return str(candidate)

    if project_path is not None:
        try:
            project_resolved = project_path.resolve()
        except Exception:
            project_resolved = project_path.absolute()
        hidden = project_resolved / ".tdocprojects"
        if hidden.is_dir():
            return str(hidden)
        marker = project_resolved / PROJECT_MARKER_FILENAME
        if marker.is_file():
            return str(project_resolved)

    if text:
        return str(anchor)
    if project_path is not None:
        return str(project_path)
    return str(Path.cwd())


class TDocDocumentWidget(TDocEditorWidget):
    """Reusable TDOC document view/editor for IDE tab integration."""

    def __init__(
        self,
        *,
        file_path: str | None = None,
        project_root: str | None = None,
        canonicalize: Callable[[str], str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.editor_id = str(uuid.uuid4())

        self._canonicalize = canonicalize or (lambda p: str(Path(p).resolve()))
        self._project_root = str(project_root or "").strip()

        self.file_path: str | None = None
        self._tdoc_root = ""
        self._alias_to_symbol: dict[str, str] = {}
        self._symbol_to_aliases: dict[str, list[str]] = {}
        self._symbol_to_section: dict[str, str] = {}
        self._symbol_to_metadata: dict[str, dict[str, str]] = {}
        self._frontmatter_cache: dict[str, tuple[int, int, dict[str, str], list[dict]]] = {}

        self.resolve_symbol = self._resolve_symbol_link
        self.resolve_image_path = self._resolve_image_link_target
        self.resolve_link_tooltip = self._resolve_link_tooltip
        self.list_symbol_completion_candidates = self._list_symbol_completion_candidates
        self.list_path_completion_candidates = self._list_path_completion_candidates

        if file_path:
            self.load_file(file_path)

    @property
    def tdoc_root(self) -> str:
        return str(self._tdoc_root or "")

    def display_name(self) -> str:
        return os.path.basename(self.file_path) if self.file_path else "File"

    def set_file_path(self, path: str | None) -> None:
        clean = str(path).strip() if isinstance(path, str) and path.strip() else None
        self.file_path = self._canonicalize(clean) if clean else None
        self.refresh_project_context()

    def refresh_project_context(self) -> None:
        root = resolve_tdoc_root_for_path(self.file_path, project_root=self._project_root)
        self._tdoc_root = self._canonicalize(root)
        aliases, symbol_to_aliases, symbol_to_section, _inc, _ign, symbol_to_metadata = TDocProjectIndex.load_aliases(
            self._tdoc_root
        )
        self._alias_to_symbol = aliases if isinstance(aliases, dict) else {}
        self._symbol_to_aliases = (
            symbol_to_aliases if isinstance(symbol_to_aliases, dict) else {}
        )
        self._symbol_to_section = symbol_to_section if isinstance(symbol_to_section, dict) else {}
        self._symbol_to_metadata = symbol_to_metadata if isinstance(symbol_to_metadata, dict) else {}

    def _resolve_symbol_link(self, label: str) -> str:
        if not isinstance(label, str):
            return ""
        self.refresh_project_context()
        if not self._alias_to_symbol:
            return label.strip()
        return TDocProjectIndex.resolve_symbol(label, self._alias_to_symbol)

    def load_file(self, path: str) -> bool:
        target = str(path or "").strip()
        if not target:
            return False
        cpath = self._canonicalize(target)
        if not os.path.exists(cpath):
            return False
        try:
            content = Path(cpath).read_text(encoding="utf-8")
        except Exception:
            return False

        self.set_file_path(cpath)
        self.load_tdoc(content)
        self.document().setModified(False)
        return True

    def save_file(self) -> bool:
        if not isinstance(self.file_path, str) or not self.file_path.strip():
            return False
        try:
            Path(self.file_path).write_text(self.save_tdoc(), encoding="utf-8")
        except Exception:
            return False
        self.document().setModified(False)
        return True

    def serialized_text(self) -> str:
        return self.save_tdoc()

    def resolve_file_link_target(self, target: str) -> tuple[str, int | None]:
        rel, line_no = parse_file_link(str(target or ""))
        if not rel:
            rel = str(target or "").strip()
        if not rel:
            return "", line_no
        base = self.tdoc_root or os.path.dirname(self.file_path or self._project_root or os.getcwd())
        abs_path = self._canonicalize(os.path.join(base, rel))
        return abs_path, line_no

    def _resolve_image_link_target(self, target: str) -> str:
        rel = str(target or "").strip()
        if not rel:
            return ""
        if rel.startswith("~") or os.path.isabs(rel):
            return ""
        base = self.tdoc_root or os.path.dirname(self.file_path or self._project_root or os.getcwd())
        return self._canonicalize(os.path.join(base, rel))

    def _cached_frontmatter(self, abs_path: str) -> tuple[dict[str, str], list[dict]]:
        file_path = str(abs_path or "").strip()
        if not file_path:
            return {}, []
        path_obj = Path(file_path)
        try:
            stat = path_obj.stat()
        except Exception:
            self._frontmatter_cache.pop(file_path, None)
            return {}, []
        sig = (int(stat.st_mtime_ns), int(stat.st_size))
        cached = self._frontmatter_cache.get(file_path)
        if cached and cached[0] == sig[0] and cached[1] == sig[1]:
            return dict(cached[2]), list(cached[3])
        try:
            content = path_obj.read_text(encoding="utf-8")
        except Exception:
            self._frontmatter_cache.pop(file_path, None)
            return {}, []
        metadata, _body_lines, _body_start_line, issues = parse_doc_frontmatter(content)
        clean_meta = metadata if isinstance(metadata, dict) else {}
        clean_issues = issues if isinstance(issues, list) else []
        self._frontmatter_cache[file_path] = (
            sig[0],
            sig[1],
            dict(clean_meta),
            list(clean_issues),
        )
        return clean_meta, clean_issues

    def _format_rel_path(self, abs_path: str) -> str:
        path = str(abs_path or "").strip()
        if not path:
            return ""
        root = str(self.tdoc_root or "").strip()
        if root:
            try:
                rel = os.path.relpath(path, root)
                if rel and not rel.startswith(".."):
                    return rel.replace("\\", "/")
            except Exception:
                pass
        return path.replace("\\", "/")

    def _tooltip_label_color(self) -> str:
        try:
            pal = self.palette()
            text = QColor(pal.color(QPalette.ColorRole.Text))
            base = QColor(pal.color(QPalette.ColorRole.Base))
            # Blend toward base for a softer neutral gray label tone.
            ratio = 0.60
            r = int((text.red() * ratio) + (base.red() * (1.0 - ratio)))
            g = int((text.green() * ratio) + (base.green() * (1.0 - ratio)))
            b = int((text.blue() * ratio) + (base.blue() * (1.0 - ratio)))
            return QColor(r, g, b).name(QColor.NameFormat.HexRgb)
        except Exception:
            return "#9AA0A6"

    @staticmethod
    def _tooltip_escape(value: str) -> str:
        return html.escape(str(value or ""), quote=True).replace("\n", "<br/>")

    def _tooltip_html(self, rows: list[tuple[str, str]], notes: list[str] | None = None) -> str:
        label_color = self._tooltip_label_color()
        safe_rows = []
        for label, value in rows:
            left = self._tooltip_escape(label)
            right = self._tooltip_escape(value)
            if not left or not right:
                continue
            safe_rows.append(
                (
                    f"<tr>"
                    f"<td style='color:{label_color};font-weight:600;padding:0 8px 1px 0;white-space:nowrap;'>"
                    f"{left}:"
                    f"</td>"
                    f"<td style='font-style:italic;padding:0 0 1px 0;'>"
                    f"{right}"
                    f"</td>"
                    f"</tr>"
                )
            )
        notes_html = ""
        clean_notes = [self._tooltip_escape(n) for n in (notes or []) if str(n or "").strip()]
        if clean_notes:
            notes_html = "<div style='margin-top:4px;'><i>" + "<br/>".join(clean_notes) + "</i></div>"
        return "<qt><table cellspacing='0' cellpadding='0'>" + "".join(safe_rows) + "</table>" + notes_html + "</qt>"

    def _symbol_tooltip(self, symbol_name: str) -> str:
        name = str(symbol_name or "").strip()
        if not name:
            return ""
        self.refresh_project_context()
        aliases = self._symbol_to_aliases.get(name, [])
        section = str(self._symbol_to_section.get(name) or "").strip()
        metadata = self._symbol_to_metadata.get(name, {})
        rows: list[tuple[str, str]] = [("Symbol", name)]
        if section:
            rows.append(("Section", section))
        if aliases:
            alias_text = ", ".join(str(a) for a in aliases if str(a).strip())
            if alias_text:
                rows.append(("Aliases", alias_text))
        if isinstance(metadata, dict) and metadata:
            meta_items = []
            for key in sorted(metadata.keys(), key=str.casefold):
                value = str(metadata.get(key) or "").strip()
                if value:
                    meta_items.append(f"{key}={value}")
            if meta_items:
                rows.append(("Metadata", "; ".join(meta_items)))
        return self._tooltip_html(rows)

    def _file_tooltip(self, raw_target: str) -> str:
        abs_path, line_no = self.resolve_file_link_target(raw_target)
        path = str(abs_path or "").strip()
        if not path:
            return ""
        rel_path = self._format_rel_path(path)
        rows: list[tuple[str, str]] = []
        if isinstance(line_no, int) and int(line_no) > 0:
            line_text = str(int(line_no))
        else:
            line_text = ""
        if not os.path.exists(path):
            rows.append(("Page", rel_path))
            if line_text:
                rows.append(("Line", line_text))
            return self._tooltip_html(rows, notes=["Missing file."])
        if not path.lower().endswith(DOC_SUFFIX):
            rows.append(("File", rel_path))
            if line_text:
                rows.append(("Line", line_text))
            return self._tooltip_html(rows)

        metadata, issues = self._cached_frontmatter(path)
        rows.append(("Page", rel_path))
        if line_text:
            rows.append(("Line", line_text))
        if metadata:
            for key in sorted(metadata.keys(), key=str.casefold):
                value = str(metadata.get(key) or "").strip()
                if value:
                    rows.append((key, value))
        else:
            rows.append(("Frontmatter", "none"))
        notes: list[str] = []
        if issues:
            notes.append("Frontmatter has warnings.")
        return self._tooltip_html(rows, notes=notes)

    def _resolve_link_tooltip(self, target: str) -> str:
        text = str(target or "").strip()
        if not text:
            return ""
        if text.startswith("symbol:"):
            symbol = text[len("symbol:"):].strip()
            return self._symbol_tooltip(symbol)
        if text.startswith("file:"):
            raw = text[len("file:"):].strip()
            return self._file_tooltip(raw)
        return ""

    def _list_symbol_completion_candidates(self) -> list[str]:
        self.refresh_project_context()
        out: list[str] = []
        seen: set[str] = set()
        for symbol in sorted(self._symbol_to_aliases.keys(), key=str.casefold):
            aliases = self._symbol_to_aliases.get(symbol, [symbol])
            for candidate in aliases:
                text = str(candidate or "").strip()
                if not text:
                    continue
                key = text.casefold()
                if key in seen:
                    continue
                seen.add(key)
                out.append(text)
        return out

    def _list_path_completion_candidates(self, prefix: str, image_only: bool) -> list[str]:
        base = self.tdoc_root or os.path.dirname(self.file_path or self._project_root or os.getcwd())
        root = Path(base)
        try:
            root = root.resolve()
        except Exception:
            root = Path(os.path.abspath(str(root)))

        query = str(prefix or "").strip().replace("\\", "/")
        while query.startswith("./"):
            query = query[2:]
        if not root.exists() or not root.is_dir():
            return []
        if query.startswith("~") or query.startswith("/"):
            return []
        if any(part == ".." for part in query.split("/") if part):
            return []

        rel_dir = ""
        if "/" in query:
            rel_dir = query.rsplit("/", 1)[0]
        abs_dir = root / rel_dir if rel_dir else root
        try:
            abs_dir = abs_dir.resolve()
        except Exception:
            abs_dir = Path(os.path.abspath(str(abs_dir)))
        try:
            if os.path.commonpath([str(abs_dir), str(root)]) != str(root):
                return []
        except Exception:
            return []
        if not abs_dir.exists() or not abs_dir.is_dir():
            return []

        entries: list[tuple[int, str, str]] = []
        for child in abs_dir.iterdir():
            name = str(child.name or "")
            if not name:
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if child.is_dir():
                candidate = rel + "/"
                rank = 0
            else:
                if image_only and child.suffix.lower() not in _TDOC_IMAGE_SUFFIXES:
                    continue
                candidate = rel
                rank = 1
            if query and not candidate.casefold().startswith(query.casefold()):
                continue
            entries.append((rank, candidate.casefold(), candidate))
        entries.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in entries[:240]]

    def ensure_index_file(self) -> str | None:
        self.refresh_project_context()
        if not self.tdoc_root:
            return None
        out = TDocProjectIndex.build_index(self.tdoc_root)
        if out is None:
            return None
        return self._canonicalize(str(out))

    def jump_to_line(self, line: int, column: int = 1) -> bool:
        line_num = max(1, int(line or 1))
        col_num = max(1, int(column or 1))
        block = self.document().findBlockByNumber(line_num - 1)
        if not block.isValid():
            return False
        cursor = QTextCursor(block)
        cursor.movePosition(
            QTextCursor.MoveOperation.Right,
            QTextCursor.MoveMode.MoveAnchor,
            col_num - 1,
        )
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        return True

    def jump_to_symbol(self, symbol: str) -> bool:
        needle = str(symbol or "").strip()
        if not needle:
            return False
        cursor = self.document().find(f"[{needle}]")
        if cursor.isNull():
            cursor = self.document().find(f"        {needle}\n")
        if cursor.isNull():
            cursor = self.document().find(f"Symbol: [{needle}]")
        if cursor.isNull():
            cursor = self.document().find(f"### [{needle}]")
        if cursor.isNull():
            cursor = self.document().find(f"## [{needle}]")
        if cursor.isNull():
            cursor = self.document().find(needle)
        if cursor.isNull():
            return False
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        return True


def collect_tdoc_diagnostics(
    *,
    file_path: str,
    project_root: str,
    canonicalize: Callable[[str], str],
    source: str = "tdoc",
    content_overrides: dict[str, str] | None = None,
) -> tuple[str, dict[str, list[dict]]]:
    """Return `(resolved_root, diagnostics_by_file)` for TDOC validation."""

    cpath = canonicalize(file_path)
    root = canonicalize(resolve_tdoc_root_for_path(cpath, project_root=project_root))

    findings = TDocProjectIndex.validate_project(root, content_overrides=content_overrides)
    marker_path = canonicalize(str(Path(root) / PROJECT_MARKER_FILENAME))

    by_file: dict[str, list[dict]] = {}

    def _push(path: str, line: int, severity: str, message: str) -> None:
        key = canonicalize(path)
        rows = by_file.setdefault(key, [])
        line_num = max(1, int(line or 1))
        rows.append(
            {
                "file_path": key,
                "line": line_num,
                "column": 1,
                "end_line": line_num,
                "end_column": 1,
                "severity": str(severity or "warning").strip().lower() or "warning",
                "source": source,
                "code": "TDOC",
                "message": str(message or "").strip(),
            }
        )

    for finding in findings:
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity") or "warning").strip().lower()
        if severity not in {"error", "warning", "info", "hint"}:
            severity = "warning"

        message = str(finding.get("message") or "").strip()
        if not message:
            continue

        line_value = finding.get("line")
        marker_line = int(line_value) if isinstance(line_value, int) else None
        finding_file = str(finding.get("file") or "").strip()

        diag_path = ""
        diag_line = 1

        if finding_file and marker_line is not None:
            diag_path = canonicalize(str(Path(root) / finding_file))
            diag_line = marker_line
        elif marker_line is not None:
            diag_path = marker_path
            diag_line = marker_line
        else:
            frontmatter_match = _TDOC_FRONTMATTER_MSG_RE.match(message)
            if frontmatter_match:
                rel = frontmatter_match.group("rel").strip()
                diag_path = canonicalize(str(Path(root) / rel))
                diag_line = int(frontmatter_match.group("line"))
            else:
                ref_match = _TDOC_REF_RE.search(message)
                if ref_match:
                    rel = ref_match.group("rel").strip()
                    diag_path = canonicalize(str(Path(root) / rel))
                    diag_line = int(ref_match.group("line"))

        if not diag_path:
            if "Missing project marker file" in message:
                diag_path = cpath
                diag_line = 1
            else:
                diag_path = marker_path if os.path.exists(marker_path) else cpath
                diag_line = 1

        _push(diag_path, diag_line, severity, message)

    return root, by_file


__all__ = [
    "DOC_SUFFIX",
    "PROJECT_MARKER_FILENAME",
    "TDocProjectIndex",
    "TDocDocumentWidget",
    "collect_tdoc_diagnostics",
    "is_tdoc_document_path",
    "is_tdoc_project_marker_path",
    "is_tdoc_related_path",
    "parse_file_link",
    "resolve_tdoc_root_for_path",
]
