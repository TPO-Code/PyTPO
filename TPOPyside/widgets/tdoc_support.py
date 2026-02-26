from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Callable

from PySide6.QtGui import QTextCursor

from TPOPyside.widgets.tdoc_core import (
    DOC_SUFFIX,
    PROJECT_MARKER_FILENAME,
    TDocEditorWidget,
    TDocProjectIndex,
    parse_file_link,
)

_TDOC_FRONTMATTER_MSG_RE = re.compile(r"^(?P<rel>[^:\n]+\.tdoc):(?P<line>\d+)\s+-\s+")
_TDOC_REF_RE = re.compile(r"(?P<rel>[^\s,]+\.tdoc)#L(?P<line>\d+)")


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

        self.resolve_symbol = self._resolve_symbol_link
        self.resolve_image_path = self._resolve_image_link_target

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
        aliases, _sym, _sec, _inc, _ign, _meta = TDocProjectIndex.load_aliases(self._tdoc_root)
        self._alias_to_symbol = aliases if isinstance(aliases, dict) else {}

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
