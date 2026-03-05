"""Core TDOC parser/index/editor primitives shared by IDE widgets.

Extracted from tdock demo app to avoid runtime dependency on tdock package.
"""

import fnmatch
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Callable, Mapping

from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QKeySequence,
    QShortcut,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextImageFormat,
    QTextFormat,
)
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTextEdit,
    QToolTip,
    QWidget,
)
from TPOPyside.widgets.editor_change_regions import (
    DEFAULT_EDITOR_DIRTY_BACKGROUND_HEX,
    DEFAULT_EDITOR_UNCOMMITTED_BACKGROUND_HEX,
    build_change_region_selections,
    normalize_line_numbers,
    parse_editor_overlay_color,
    resolve_change_region_layer,
)
from TPOPyside.widgets.code_editor.code_folding import get_fold_provider, normalize_fold_ranges

PROJECT_MARKER_FILENAME = ".tdocproject"
INDEX_FILENAME = "index.tdoc"
DOC_SUFFIX = ".tdoc"
INDEX_SEPARATOR_LINE = "--------------------"
LEGACY_INDEX_AUTO_START = "<!-- TDOC:AUTO START -->"
LEGACY_INDEX_AUTO_END = "<!-- TDOC:AUTO END -->"

LINK_PATTERN = re.compile(r"(?<!\!)\[(?P<label>[^\[\]\n]+?)\]")
IMAGE_PATTERN = re.compile(r"!\[(?P<body>[^\[\]\n]+?)\]")
INLINE_TOKEN_PATTERN = re.compile(r"!\[(?P<image>[^\[\]\n]+?)\]|(?<!\!)\[(?P<link>[^\[\]\n]+?)\]")
ALIAS_LINE_PATTERN = re.compile(r"^(?P<symbol>[^=#]+?)\s*=\s*(?P<aliases>.*)$")
SECTION_HEADER_PATTERN = re.compile(r"^(?P<section>[^=#].*?)\s*:\s*$")
FILE_LINK_PATTERN = re.compile(r"^(?P<path>.+?\.tdoc)(?:#L(?P<line>\d+))?$", re.IGNORECASE)
RULE_LINE_PATTERN = re.compile(r"^(?P<rule>include|ignore)\s*:\s*(?P<patterns>.*)$", re.IGNORECASE)
FRONTMATTER_SCHEMA_RULE_PATTERN = re.compile(
    r"^frontmatter_schema\s*:\s*(?P<path>.*?)\s*$",
    re.IGNORECASE,
)
INDEX_GROUP_BY_RULE_PATTERN = re.compile(
    r"^index_group_by\s*:\s*(?P<mode>.*?)\s*$",
    re.IGNORECASE,
)
FRONTMATTER_KV_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(?P<value>.*)$")
MARKDOWN_HEADING_PATTERN = re.compile(r"^(?P<indent>[ \t]{0,3})(?P<hashes>#{1,3})[ \t]+(?P<title>.+?)\s*$")
MARKDOWN_BULLET_PATTERN = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[\*-])[ \t]+(?P<body>.*)$")
MARKDOWN_NUMBERED_LIST_PATTERN = re.compile(
    r"^(?P<indent>[ \t]*)(?P<number>\d+)(?P<marker>[.)])(?P<space>[ \t]+)(?P<body>.*)$"
)
_WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
BULLET_GLYPH = "•"

_TDOC_LINT_VISUAL_DEFAULTS = {
    "mode": "squiggle",
    "error_color": "#E35D6A",
    "warning_color": "#D6A54A",
    "info_color": "#6AA1FF",
    "hint_color": "#8F9AA5",
    "squiggle_thickness": 2,
    "line_alpha": 64,
}
_TDOC_OVERVIEW_MARKER_DEFAULTS = {
    "enabled": True,
    "width": 10,
    "search_color": "#4A8FD8",
    "search_active_color": "#D6A853",
    "occurrence_color": "#66A86A",
    "max_occurrence_matches": 12000,
    "max_occurrence_highlights": 3000,
    "occurrence_highlight_alpha": 88,
}
_TDOC_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".svg",
}
_TDOC_COMPLETION_INDEX_ROLE = int(Qt.UserRole)
_TDOC_COMPLETION_META_ROLE = int(Qt.UserRole) + 1
_FILE_LINK_EXTENSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_TDOC_FRONTMATTER_KEY_SUGGESTIONS = [
    "title",
    "status",
    "index",
    "tags",
    "summary",
    "author",
    "created",
    "updated",
]
_TDOC_FRONTMATTER_VALUE_SUGGESTIONS = {
    "index": ["on", "off"],
    "status": ["draft", "review", "final", "published", "archived"],
}
_TDOC_FRONTMATTER_GENERIC_VALUE_SUGGESTIONS = [
    "draft",
    "review",
    "final",
    "on",
    "off",
    "true",
    "false",
]
_THEME_EDITOR_SEARCH_TOP_MARGIN_PROP = "theme.editor.search.top_margin_min"
_THEME_EDITOR_OVERVIEW_GAP_PROP = "theme.editor.overview.gap"
_THEME_EDITOR_SEARCH_TOP_MARGIN_DEFAULT = 30
_THEME_EDITOR_OVERVIEW_GAP_DEFAULT = 1
_THEME_PX_RE = re.compile(r"^\s*(-?\d+)\s*(px)?\s*$", re.IGNORECASE)


def _coerce_theme_px(value: object, *, default: int, minimum: int = 0) -> int:
    floor = max(0, int(minimum))
    fallback = max(floor, int(default))
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return max(floor, int(value))
    if isinstance(value, float):
        return max(floor, int(round(value)))
    text = str(value or "").strip()
    if not text:
        return fallback
    match = _THEME_PX_RE.fullmatch(text)
    if not match:
        return fallback
    try:
        return max(floor, int(match.group(1)))
    except Exception:
        return fallback


def _build_search_line_edit(editor: "TDocEditorWidget", parent: QWidget, *, role: str) -> QLineEdit:
    factory = getattr(editor, "create_search_line_edit", None)
    if callable(factory):
        try:
            candidate = factory(parent=parent, role=role)
        except TypeError:
            candidate = factory(parent)
        except Exception:
            candidate = None
        if isinstance(candidate, QLineEdit):
            return candidate
    return QLineEdit(parent)


def _coerce_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n", ""}:
        return False
    return bool(default)


def parse_file_link(label):
    """Parses path-like links from plain or titled link text.

    Supports:
    - relative paths: docs/spec.md, ./src/main.cpp, ../notes.txt
    - absolute paths: /var/log/app.log, C:\\repo\\file.py
    - optional line anchors: #L42
    """
    cleaned = str(link_effective_target(label) or "").strip()
    if not cleaned:
        return None, None

    line = None
    path = cleaned
    m = re.match(r"^(?P<path>.+?)#L(?P<line>\d+)$", cleaned, re.IGNORECASE)
    if not m:
        m = FILE_LINK_PATTERN.match(cleaned)
    if m:
        path = m.group("path").strip()
        line = int(m.group("line")) if m.group("line") else None
    if not _looks_like_file_link_path(path):
        return None, None
    return path, line


def _looks_like_file_link_path(path):
    text = str(path or "").strip()
    if not text:
        return False
    if text in {".", ".."}:
        return False
    if text.startswith("~"):
        return False
    if os.path.isabs(text):
        return False
    if _WINDOWS_DRIVE_PATH_PATTERN.match(text):
        return False
    if "/" in text or "\\" in text:
        return True
    name = os.path.basename(text)
    if not name:
        return False
    if name.startswith(".") and len(name) > 1:
        return True
    dot = name.rfind(".")
    if dot > 0 and dot < len(name) - 1:
        ext_raw = name[dot + 1:]
        if any(ch.isspace() for ch in ext_raw):
            return False
        if not _FILE_LINK_EXTENSION_PATTERN.match(ext_raw):
            return False
        return True
    return False


def parse_link_components(label):
    """Parses TDOC link body to (display_text, target_text|None)."""
    cleaned = str(label or "").strip()
    if not cleaned:
        return "", None
    if "|" not in cleaned:
        return cleaned, None
    display, target = cleaned.split("|", 1)
    display = display.strip()
    target = target.strip()
    if not target:
        return cleaned, None
    if not display:
        display = target
    return display, target


def compose_link_components(display, target):
    display_text = str(display or "").strip()
    target_text = str(target or "").strip()
    if target_text:
        if not display_text:
            display_text = target_text
        return f"{display_text}|{target_text}"
    return display_text


def link_effective_target(label):
    display, target = parse_link_components(label)
    return str(target or display or "").strip()


def link_display_text(label):
    display, target = parse_link_components(label)
    shown = str(display or target or "").strip()
    if shown:
        return shown
    return str(label or "").strip()


def parse_image_components(body):
    cleaned = str(body or "").strip()
    if not cleaned:
        return "", ""
    if "|" not in cleaned:
        return "", cleaned
    caption, path = cleaned.split("|", 1)
    caption = caption.strip()
    path = path.strip()
    if not path:
        return "", cleaned
    return caption, path


def compose_image_components(caption, path):
    caption_text = str(caption or "").strip()
    path_text = str(path or "").strip()
    if not path_text:
        return caption_text
    if caption_text:
        return f"{caption_text}|{path_text}"
    return path_text


def parse_doc_frontmatter(content):
    """
    Parses optional frontmatter at top of file:
    ---
    key: value
    ---
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, lines, 1, []

    metadata = {}
    issues = []
    i = 1
    closed = False
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if stripped == "---":
            closed = True
            i += 1
            break
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        m = FRONTMATTER_KV_PATTERN.match(raw)
        if not m:
            issues.append(
                {
                    "line": i + 1,
                    "message": "Malformed frontmatter line. Use 'key: value'.",
                }
            )
            i += 1
            continue
        key = m.group("key").strip().lower()
        value = m.group("value").strip()
        metadata[key] = value
        i += 1

    if not closed:
        return {}, lines, 1, [{"line": 1, "message": "Unclosed frontmatter block (missing '---')."}]

    body_lines = lines[i:]
    body_start_line = i + 1
    return metadata, body_lines, body_start_line, issues


def is_index_enabled(metadata):
    val = (metadata.get("index") or "").strip().lower()
    if not val:
        return True
    return val not in {"0", "false", "no", "off"}


class TDocProjectIndex:
    """Builds symbol index and resolves aliases for [Symbol] links."""

    @staticmethod
    def marker_path(root_path):
        return Path(root_path) / PROJECT_MARKER_FILENAME

    @staticmethod
    def index_path(root_path):
        return Path(root_path) / INDEX_FILENAME

    @staticmethod
    def has_project_marker(root_path):
        return TDocProjectIndex.marker_path(root_path).exists()

    @staticmethod
    def _is_section_header(line):
        if "=" in line:
            return False
        return SECTION_HEADER_PATTERN.match(line) is not None

    @staticmethod
    def _parse_symbol_line(line):
        symbol, alias_items, _, _ = TDocProjectIndex._parse_symbol_definition(line)
        return symbol, alias_items

    @staticmethod
    def _split_symbol_line_parts(line):
        parts = [p.strip() for p in str(line or "").split(";")]
        primary = parts[0] if parts else ""
        metadata_parts = parts[1:] if len(parts) > 1 else []
        return primary, metadata_parts

    @staticmethod
    def _line_indent_columns(raw_line):
        text = str(raw_line or "")
        cols = 0
        for ch in text:
            if ch == " ":
                cols += 1
            elif ch == "\t":
                cols += 4
            else:
                break
        return cols

    @staticmethod
    def _collect_symbol_continuation_lines(raw_lines, symbol_index):
        lines = raw_lines if isinstance(raw_lines, list) else []
        if not (0 <= int(symbol_index) < len(lines)):
            return [], int(symbol_index) + 1

        head_raw = str(lines[int(symbol_index)] or "")
        head_indent = TDocProjectIndex._line_indent_columns(head_raw)
        collected = []
        idx = int(symbol_index) + 1
        while idx < len(lines):
            raw = str(lines[idx] or "")
            stripped = raw.strip()
            if not stripped:
                break
            if stripped.startswith("#"):
                break
            indent = TDocProjectIndex._line_indent_columns(raw)
            if indent <= head_indent:
                break
            rule, _patterns = TDocProjectIndex._parse_rule_line(stripped)
            if rule:
                break
            if TDocProjectIndex._is_section_header(stripped):
                break
            collected.append(raw)
            idx += 1
        return collected, idx

    @staticmethod
    def _parse_metadata_item(item, metadata, metadata_issues):
        if "=" not in item:
            metadata_issues.append(f"Malformed metadata entry '{item}'. Use 'key=value'.")
            return
        key, value = item.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key:
            metadata_issues.append(f"Metadata key is empty in '{item}'.")
            return
        if not value:
            metadata_issues.append(f"Metadata value is empty for key '{key}'.")
            return
        if key in metadata:
            metadata_issues.append(f"Duplicate metadata key '{key}'.")
            return
        metadata[key] = value

    @staticmethod
    def _parse_symbol_definition(line, continuation_metadata_lines=None):
        primary, metadata_parts = TDocProjectIndex._split_symbol_line_parts(line)

        m = ALIAS_LINE_PATTERN.match(primary)
        if m:
            symbol = m.group("symbol").strip()
            alias_items = [x.strip() for x in m.group("aliases").split("|") if x.strip()]
        else:
            symbol = primary.strip()
            alias_items = []

        metadata = {}
        metadata_issues = []
        for item in metadata_parts:
            if not item:
                continue
            TDocProjectIndex._parse_metadata_item(item, metadata, metadata_issues)

        extra_lines = continuation_metadata_lines if isinstance(continuation_metadata_lines, list) else []
        for raw_meta in extra_lines:
            text = str(raw_meta or "").strip()
            if not text:
                continue
            if text.startswith(";"):
                text = text[1:].strip()
            if text.endswith(";"):
                text = text[:-1].strip()
            if not text:
                continue
            for item in [part.strip() for part in text.split(";") if part.strip()]:
                TDocProjectIndex._parse_metadata_item(item, metadata, metadata_issues)

        return symbol, alias_items, metadata, metadata_issues

    @staticmethod
    def _format_symbol_definition(symbol, alias_items, metadata):
        if alias_items:
            base = f"{symbol} = {' | '.join(alias_items)}"
        else:
            base = symbol
        if metadata:
            meta_text = " ; ".join(f"{k}={v}" for k, v in metadata.items())
            return f"{base} ; {meta_text}"
        return base

    @staticmethod
    def _parse_rule_line(line):
        m = RULE_LINE_PATTERN.match(line)
        if not m:
            return None, None
        rule = m.group("rule").lower()
        patterns_raw = m.group("patterns").strip()
        patterns = [x.strip() for x in patterns_raw.split("|") if x.strip()]
        return rule, patterns

    @staticmethod
    def _parse_frontmatter_schema_rule(line):
        m = FRONTMATTER_SCHEMA_RULE_PATTERN.match(str(line or ""))
        if not m:
            return None
        raw = str(m.group("path") or "").strip()
        if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
            raw = raw[1:-1].strip()
        return raw

    @staticmethod
    def _parse_index_group_by_rule(line):
        m = INDEX_GROUP_BY_RULE_PATTERN.match(str(line or ""))
        if not m:
            return None
        raw = str(m.group("mode") or "").strip().lower()
        if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
            raw = raw[1:-1].strip().lower()
        return raw

    @staticmethod
    def _index_group_by_config_from_lines(lines):
        mode = "none"
        issues: list[dict] = []
        for idx, raw in enumerate(lines if isinstance(lines, list) else [], start=1):
            line = str(raw or "").strip()
            if not line or line.startswith("#"):
                continue
            parsed = TDocProjectIndex._parse_index_group_by_rule(line)
            if parsed is None:
                continue
            if parsed not in {"none", "folder"}:
                issues.append(
                    {
                        "severity": "warning",
                        "message": "Rule 'index_group_by:' supports only 'none' or 'folder'.",
                        "line": int(idx),
                        "file": PROJECT_MARKER_FILENAME,
                    }
                )
                continue
            mode = parsed
        return mode, issues

    @staticmethod
    def _frontmatter_schema_config_from_lines(lines):
        path_value = ""
        line_no = 0
        issues: list[dict] = []
        for idx, raw in enumerate(lines if isinstance(lines, list) else [], start=1):
            line = str(raw or "").strip()
            if not line or line.startswith("#"):
                continue
            parsed = TDocProjectIndex._parse_frontmatter_schema_rule(line)
            if parsed is None:
                continue
            if not parsed:
                issues.append(
                    {
                        "severity": "warning",
                        "message": "Rule 'frontmatter_schema:' requires a JSON path value.",
                        "line": int(idx),
                        "file": PROJECT_MARKER_FILENAME,
                    }
                )
                continue
            if path_value and parsed != path_value:
                issues.append(
                    {
                        "severity": "warning",
                        "message": (
                            f"Multiple frontmatter_schema rules found; using last value '{parsed}'."
                        ),
                        "line": int(idx),
                        "file": PROJECT_MARKER_FILENAME,
                    }
                )
            path_value = parsed
            line_no = int(idx)
        return path_value, line_no, issues

    @staticmethod
    def _normalize_frontmatter_schema_payload(raw_schema):
        data = raw_schema if isinstance(raw_schema, dict) else {}

        keys_out: list[str] = []
        seen_keys: set[str] = set()
        required_out: list[str] = []
        values_out: dict[str, list[str]] = {}

        props = data.get("properties")
        if isinstance(props, dict):
            for raw_key, raw_cfg in props.items():
                key = str(raw_key or "").strip()
                if not key:
                    continue
                folded = key.casefold()
                if folded not in seen_keys:
                    seen_keys.add(folded)
                    keys_out.append(key)
                cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
                enums: list[str] = []
                raw_enum = cfg.get("enum")
                if isinstance(raw_enum, list):
                    for item in raw_enum:
                        text = str(item).strip()
                        if text:
                            enums.append(text)
                elif "const" in cfg:
                    text = str(cfg.get("const")).strip()
                    if text:
                        enums.append(text)
                if enums:
                    values_out[key] = enums

        raw_keys = data.get("keys")
        if isinstance(raw_keys, list):
            for raw_key in raw_keys:
                key = str(raw_key or "").strip()
                if not key:
                    continue
                folded = key.casefold()
                if folded in seen_keys:
                    continue
                seen_keys.add(folded)
                keys_out.append(key)

        raw_values = data.get("values")
        if isinstance(raw_values, dict):
            for raw_key, raw_items in raw_values.items():
                key = str(raw_key or "").strip()
                if not key:
                    continue
                values: list[str] = []
                if isinstance(raw_items, list):
                    for item in raw_items:
                        text = str(item).strip()
                        if text:
                            values.append(text)
                elif raw_items is not None:
                    text = str(raw_items).strip()
                    if text:
                        values.append(text)
                if values:
                    values_out[key] = values

        raw_required = data.get("required")
        if isinstance(raw_required, list):
            seen_required: set[str] = set()
            for raw_key in raw_required:
                key = str(raw_key or "").strip()
                if not key:
                    continue
                folded = key.casefold()
                if folded in seen_required:
                    continue
                seen_required.add(folded)
                required_out.append(key)

        allow_unknown = True
        if "allow_unknown_keys" in data:
            allow_unknown = _coerce_bool(data.get("allow_unknown_keys"), default=True)
        elif "additionalProperties" in data:
            allow_unknown = _coerce_bool(data.get("additionalProperties"), default=True)

        return {
            "keys": keys_out,
            "required": required_out,
            "values_by_key": values_out,
            "allow_unknown_keys": bool(allow_unknown),
        }

    @staticmethod
    def _relative_parent_folder(rel_path):
        text = str(rel_path or "").replace("\\", "/").strip()
        if not text:
            return "."
        parent = str(Path(text).parent).replace("\\", "/").strip()
        if not parent or parent == ".":
            return "."
        return parent

    @staticmethod
    def load_frontmatter_schema(root_path, *, content_overrides=None):
        normalized_overrides = TDocProjectIndex._normalize_content_overrides(content_overrides)
        marker = TDocProjectIndex.marker_path(root_path)
        issues: list[dict] = []
        if not marker.exists():
            return {}, issues

        marker_content, marker_err = TDocProjectIndex._read_text_with_overrides(marker, normalized_overrides)
        if marker_err is not None:
            issues.append(
                {
                    "severity": "warning",
                    "message": f"Cannot read {PROJECT_MARKER_FILENAME}: {marker_err}",
                    "line": None,
                    "file": PROJECT_MARKER_FILENAME,
                }
            )
            return {}, issues

        marker_lines = str(marker_content or "").splitlines()
        schema_rel, schema_rule_line, schema_rule_issues = TDocProjectIndex._frontmatter_schema_config_from_lines(
            marker_lines
        )
        issues.extend(schema_rule_issues)
        if not schema_rel:
            return {}, issues

        schema_path_obj = Path(str(schema_rel).strip())
        if schema_path_obj.is_absolute() or str(schema_rel).startswith("~") or _WINDOWS_DRIVE_PATH_PATTERN.match(str(schema_rel)):
            issues.append(
                {
                    "severity": "warning",
                    "message": "frontmatter_schema should be a path relative to TDOC root.",
                    "line": int(schema_rule_line) if schema_rule_line else None,
                    "file": PROJECT_MARKER_FILENAME,
                }
            )
            schema_abs = schema_path_obj.expanduser()
        else:
            schema_abs = Path(root_path) / schema_path_obj

        schema_abs_text = str(schema_abs)
        schema_content, schema_err = TDocProjectIndex._read_text_with_overrides(schema_abs_text, normalized_overrides)
        if schema_err is not None:
            issues.append(
                {
                    "severity": "warning",
                    "message": f"Cannot read frontmatter schema '{schema_rel}': {schema_err}",
                    "line": int(schema_rule_line) if schema_rule_line else None,
                    "file": PROJECT_MARKER_FILENAME,
                }
            )
            return {}, issues

        try:
            payload = json.loads(str(schema_content or ""))
        except Exception as exc:
            line_no = int(getattr(exc, "lineno", 0) or 0)
            issues.append(
                {
                    "severity": "warning",
                    "message": f"Invalid frontmatter schema JSON in '{schema_rel}': {exc}",
                    "line": line_no if line_no > 0 else None,
                    "file": str(schema_rel).replace("\\", "/"),
                }
            )
            return {}, issues

        if not isinstance(payload, dict):
            issues.append(
                {
                    "severity": "warning",
                    "message": f"Frontmatter schema '{schema_rel}' must be a JSON object.",
                    "line": 1,
                    "file": str(schema_rel).replace("\\", "/"),
                }
            )
            return {}, issues

        normalized = TDocProjectIndex._normalize_frontmatter_schema_payload(payload)
        normalized["source"] = str(schema_rel).replace("\\", "/")
        return normalized, issues

    @staticmethod
    def _section_header_capitalization_warnings(lines):
        warnings = []
        for idx, raw in enumerate(lines, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            rule, _patterns = TDocProjectIndex._parse_rule_line(line)
            if rule:
                continue
            if TDocProjectIndex._parse_frontmatter_schema_rule(line) is not None:
                continue
            if TDocProjectIndex._parse_index_group_by_rule(line) is not None:
                continue
            if not TDocProjectIndex._is_section_header(line):
                continue
            section_match = SECTION_HEADER_PATTERN.match(line)
            section = section_match.group("section").strip() if section_match else ""
            if not section:
                continue
            lead = section[0]
            if lead.isalpha() and lead != lead.upper():
                warnings.append(
                    {
                        "line": idx,
                        "section": section,
                        "message": f"Section header '{section}' should begin with a capital letter.",
                    }
                )
        return warnings

    @staticmethod
    def _path_matches_patterns(rel_path, patterns):
        rel = rel_path.replace("\\", "/")
        return any(fnmatch.fnmatch(rel, pattern.replace("\\", "/")) for pattern in patterns)

    @staticmethod
    def _should_scan_doc(rel_path, include_patterns, ignore_patterns):
        if include_patterns and not TDocProjectIndex._path_matches_patterns(rel_path, include_patterns):
            return False
        if ignore_patterns and TDocProjectIndex._path_matches_patterns(rel_path, ignore_patterns):
            return False
        return True

    @staticmethod
    def iter_doc_paths(root_path, include_patterns=None, ignore_patterns=None, rel_path_filter=None):
        root = Path(root_path)
        include_patterns = include_patterns or []
        ignore_patterns = ignore_patterns or []
        rel_filter: set[str] | None = None
        if rel_path_filter is not None:
            rel_filter = set()
            for raw_rel in rel_path_filter:
                rel = str(raw_rel or "").strip().replace("\\", "/").lstrip("/")
                if not rel:
                    continue
                if any(part == ".." for part in Path(rel).parts):
                    continue
                if not rel.lower().endswith(DOC_SUFFIX):
                    continue
                rel_filter.add(rel)

        if rel_filter is not None:
            for rel_path in sorted(rel_filter, key=str.casefold):
                if Path(rel_path).name == INDEX_FILENAME:
                    continue
                if not TDocProjectIndex._should_scan_doc(rel_path, include_patterns, ignore_patterns):
                    continue
                path = root / rel_path
                if not path.exists() or not path.is_file():
                    continue
                yield path, rel_path
            return

        for path in root.rglob(f"*{DOC_SUFFIX}"):
            if not path.is_file():
                continue
            if path.name == INDEX_FILENAME:
                continue
            rel_path = str(path.relative_to(root)).replace("\\", "/")
            if not TDocProjectIndex._should_scan_doc(rel_path, include_patterns, ignore_patterns):
                continue
            yield path, rel_path

    @staticmethod
    def _canonical_text_path(path_value):
        text = str(path_value or "").strip()
        if not text:
            return ""
        try:
            return str(Path(text).expanduser().resolve())
        except Exception:
            return os.path.abspath(os.path.expanduser(text))

    @staticmethod
    def _normalize_content_overrides(content_overrides):
        normalized = {}
        if not isinstance(content_overrides, dict):
            return normalized
        for raw_path, raw_text in content_overrides.items():
            key = TDocProjectIndex._canonical_text_path(raw_path)
            if not key:
                continue
            normalized[key] = str(raw_text if raw_text is not None else "")
        return normalized

    @staticmethod
    def _read_text_with_overrides(path, content_overrides):
        key = TDocProjectIndex._canonical_text_path(path)
        if key and isinstance(content_overrides, dict) and key in content_overrides:
            return content_overrides.get(key), None
        try:
            return Path(path).read_text(encoding="utf-8"), None
        except Exception as e:
            return None, e

    @staticmethod
    def load_aliases(root_path):
        alias_to_symbol = {}
        symbol_to_aliases = {}
        symbol_to_section = {}
        symbol_to_metadata = {}
        include_patterns = []
        ignore_patterns = []

        marker = TDocProjectIndex.marker_path(root_path)
        if not marker.exists():
            return (
                alias_to_symbol,
                symbol_to_aliases,
                symbol_to_section,
                include_patterns,
                ignore_patterns,
                symbol_to_metadata,
            )

        try:
            raw_lines = marker.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            print(f"Error reading {marker}: {e}")
            return (
                alias_to_symbol,
                symbol_to_aliases,
                symbol_to_section,
                include_patterns,
                ignore_patterns,
                symbol_to_metadata,
            )

        current_section = ""
        idx = 0
        while idx < len(raw_lines):
            raw = raw_lines[idx]
            line = raw.strip()
            if not line or line.startswith("#"):
                idx += 1
                continue

            rule, patterns = TDocProjectIndex._parse_rule_line(line)
            if rule:
                if rule == "include":
                    include_patterns.extend(patterns)
                elif rule == "ignore":
                    ignore_patterns.extend(patterns)
                idx += 1
                continue

            schema_rule = TDocProjectIndex._parse_frontmatter_schema_rule(line)
            if schema_rule is not None:
                idx += 1
                continue
            index_group_rule = TDocProjectIndex._parse_index_group_by_rule(line)
            if index_group_rule is not None:
                idx += 1
                continue

            if TDocProjectIndex._is_section_header(line):
                section_match = SECTION_HEADER_PATTERN.match(line)
                current_section = section_match.group("section").strip() if section_match else ""
                idx += 1
                continue

            cont_lines, next_idx = TDocProjectIndex._collect_symbol_continuation_lines(raw_lines, idx)
            symbol, alias_items, metadata, _ = TDocProjectIndex._parse_symbol_definition(line, cont_lines)

            if not symbol:
                idx = next_idx
                continue

            aliases = []
            seen = set()
            for alias in [symbol] + alias_items:
                key = alias.casefold()
                if key in seen:
                    continue
                seen.add(key)
                aliases.append(alias)

            symbol_to_aliases[symbol] = aliases
            symbol_to_section[symbol] = current_section
            symbol_to_metadata[symbol] = metadata
            for alias in aliases:
                alias_to_symbol[alias.casefold()] = symbol
            idx = next_idx

        return (
            alias_to_symbol,
            symbol_to_aliases,
            symbol_to_section,
            include_patterns,
            ignore_patterns,
            symbol_to_metadata,
        )

    @staticmethod
    def resolve_symbol(label, alias_to_symbol):
        cleaned = label.strip()
        if not cleaned:
            return cleaned
        return alias_to_symbol.get(cleaned.casefold(), cleaned)

    @staticmethod
    def find_symbol_definition_in_marker(root_path, symbol_or_alias):
        marker = TDocProjectIndex.marker_path(root_path)
        query = str(symbol_or_alias or "").strip()
        if not marker.exists() or not query:
            return "", None, ""

        try:
            lines = marker.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            print(f"Error reading {marker}: {e}")
            return str(marker), None, ""

        query_cf = query.casefold()
        idx = 0
        while idx < len(lines):
            raw = lines[idx]
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                idx += 1
                continue

            rule, _ = TDocProjectIndex._parse_rule_line(stripped)
            if rule:
                idx += 1
                continue
            if TDocProjectIndex._parse_frontmatter_schema_rule(stripped) is not None:
                idx += 1
                continue
            if TDocProjectIndex._parse_index_group_by_rule(stripped) is not None:
                idx += 1
                continue

            if TDocProjectIndex._is_section_header(stripped):
                idx += 1
                continue

            cont_lines, next_idx = TDocProjectIndex._collect_symbol_continuation_lines(lines, idx)
            symbol, alias_items, _metadata, _issues = TDocProjectIndex._parse_symbol_definition(stripped, cont_lines)
            if not symbol:
                idx = next_idx
                continue

            if symbol.casefold() == query_cf:
                return str(marker), idx + 1, symbol

            for alias in alias_items:
                if str(alias or "").casefold() == query_cf:
                    return str(marker), idx + 1, symbol
            idx = next_idx

        return str(marker), None, ""

    @staticmethod
    def symbol_or_alias_at_marker_position(line_text, column_1based):
        raw_line = str(line_text or "")
        if not raw_line:
            return ""

        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            return ""

        rule, _ = TDocProjectIndex._parse_rule_line(stripped)
        if rule:
            return ""
        if TDocProjectIndex._parse_frontmatter_schema_rule(stripped) is not None:
            return ""
        if TDocProjectIndex._parse_index_group_by_rule(stripped) is not None:
            return ""
        if TDocProjectIndex._is_section_header(stripped):
            return ""

        try:
            col = max(1, int(column_1based or 1))
        except Exception:
            col = 1
        idx = max(0, min(col - 1, len(raw_line)))

        metadata_sep = raw_line.find(";")
        base_end = metadata_sep if metadata_sep >= 0 else len(raw_line)
        base = raw_line[:base_end]
        if not base.strip():
            return ""

        spans: list[tuple[int, int, str]] = []
        eq_idx = base.find("=")
        if eq_idx < 0:
            part = base
            token = part.strip()
            if token:
                left = len(part) - len(part.lstrip())
                right = len(part.rstrip())
                spans.append((left, right, token))
        else:
            symbol_part = base[:eq_idx]
            symbol = symbol_part.strip()
            if symbol:
                left = len(symbol_part) - len(symbol_part.lstrip())
                right = len(symbol_part.rstrip())
                spans.append((left, right, symbol))

            pos = eq_idx + 1
            while pos <= len(base):
                pipe_idx = base.find("|", pos)
                seg_end = pipe_idx if pipe_idx >= 0 else len(base)
                segment = base[pos:seg_end]
                token = segment.strip()
                if token:
                    left = pos + (len(segment) - len(segment.lstrip()))
                    right = seg_end - (len(segment) - len(segment.rstrip()))
                    spans.append((left, right, token))
                if pipe_idx < 0:
                    break
                pos = pipe_idx + 1

        if not spans:
            return ""

        for start, end, token in spans:
            if start <= idx < end:
                return token

        if idx >= base_end:
            return spans[-1][2]
        return spans[0][2]

    @staticmethod
    def rename_alias_in_marker(root_path, old_alias, new_alias):
        marker = TDocProjectIndex.marker_path(root_path)
        if not marker.exists():
            return False

        old_cf = old_alias.casefold()
        changed = False
        new_lines = []

        try:
            lines = marker.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            print(f"Error reading {marker}: {e}")
            return False

        idx = 0
        while idx < len(lines):
            raw = lines[idx]
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(raw)
                idx += 1
                continue

            rule, _ = TDocProjectIndex._parse_rule_line(stripped)
            if rule:
                new_lines.append(raw)
                idx += 1
                continue
            if TDocProjectIndex._parse_frontmatter_schema_rule(stripped) is not None:
                new_lines.append(raw)
                idx += 1
                continue
            if TDocProjectIndex._parse_index_group_by_rule(stripped) is not None:
                new_lines.append(raw)
                idx += 1
                continue

            if TDocProjectIndex._is_section_header(stripped):
                new_lines.append(raw)
                idx += 1
                continue

            cont_lines, next_idx = TDocProjectIndex._collect_symbol_continuation_lines(lines, idx)
            symbol, alias_items, metadata, _ = TDocProjectIndex._parse_symbol_definition(stripped)
            original_symbol = symbol
            original_alias_items = list(alias_items)

            if symbol.casefold() == old_cf:
                symbol = new_alias
                changed = True

            rewritten_aliases = []
            seen = set()
            for alias in alias_items:
                candidate = new_alias if alias.casefold() == old_cf else alias
                if candidate.casefold() == old_cf:
                    candidate = new_alias
                if alias.casefold() == old_cf:
                    changed = True
                if candidate.casefold() == symbol.casefold():
                    continue
                if candidate.casefold() in seen:
                    continue
                seen.add(candidate.casefold())
                rewritten_aliases.append(candidate)

            if symbol != original_symbol or rewritten_aliases != original_alias_items:
                leading = raw[: len(raw) - len(raw.lstrip(" \t"))]
                new_lines.append(
                    leading + TDocProjectIndex._format_symbol_definition(symbol, rewritten_aliases, metadata)
                )
            else:
                new_lines.append(raw)
            if cont_lines:
                new_lines.extend(lines[idx + 1:next_idx])
            idx = next_idx

        if not changed:
            return False

        try:
            marker.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
        except Exception as e:
            print(f"Error writing {marker}: {e}")
            return False

        return True

    @staticmethod
    def rename_alias_in_documents(root_path, old_alias, new_alias):
        old_cf = old_alias.casefold()
        touched = 0

        def replace_link(match):
            raw = match.group("label")
            display, target = parse_link_components(raw)
            resolved = link_effective_target(raw)
            if resolved.casefold() != old_cf:
                return match.group(0)
            file_path, _ = parse_file_link(resolved)
            if file_path:
                return match.group(0)
            if target is not None:
                new_display = display
                if display.casefold() == old_cf:
                    new_display = new_alias
                return f"[{compose_link_components(new_display, new_alias)}]"
            return f"[{new_alias}]"

        for path, _ in TDocProjectIndex.iter_doc_paths(root_path):
            try:
                content = path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"Error reading {path}: {e}")
                continue

            updated = LINK_PATTERN.sub(replace_link, content)
            if updated == content:
                continue

            try:
                path.write_text(updated, encoding="utf-8")
                touched += 1
            except Exception as e:
                print(f"Error writing {path}: {e}")

        return touched

    @staticmethod
    def collect_symbol_references(
        root_path,
        alias_to_symbol,
        include_patterns=None,
        ignore_patterns=None,
        content_overrides=None,
        rel_path_filter=None,
    ):
        symbol_refs = defaultdict(set)
        unresolved_refs = defaultdict(set)
        doc_metadata = {}
        frontmatter_issues = []
        normalized_overrides = TDocProjectIndex._normalize_content_overrides(content_overrides)

        for path, rel_path in TDocProjectIndex.iter_doc_paths(
            root_path,
            include_patterns,
            ignore_patterns,
            rel_path_filter=rel_path_filter,
        ):
            content, err = TDocProjectIndex._read_text_with_overrides(path, normalized_overrides)
            if err is not None:
                print(f"Error reading {path}: {err}")
                continue
            if content is None:
                continue

            metadata, body_lines, body_start_line, fm_issues = parse_doc_frontmatter(content)
            doc_metadata[rel_path] = metadata
            for issue in fm_issues:
                frontmatter_issues.append(
                    {"file": rel_path, "line": issue["line"], "message": issue["message"]}
                )

            if not is_index_enabled(metadata):
                continue

            for offset, line in enumerate(body_lines):
                line_no = body_start_line + offset
                rendered_col = 0
                last_raw_pos = 0
                for match in LINK_PATTERN.finditer(line):
                    pre_text = line[last_raw_pos:match.start()]
                    if pre_text:
                        rendered_col += len(pre_text)
                    raw = match.group("label")
                    label = link_effective_target(raw)
                    shown = link_display_text(raw)
                    if not shown:
                        shown = match.group(0)
                    if not label:
                        rendered_col += len(shown)
                        last_raw_pos = match.end()
                        continue

                    # [foo.tdoc] and [foo.tdoc#L42] are file links, not symbols.
                    file_path, _ = parse_file_link(label)
                    start_col = int(rendered_col + 1)
                    end_col = int(rendered_col + len(shown) + 1)
                    rendered_col += len(shown)
                    last_raw_pos = match.end()
                    if file_path:
                        continue

                    symbol = alias_to_symbol.get(label.casefold())
                    ref = (rel_path, line_no, start_col, end_col)
                    if symbol:
                        symbol_refs[symbol].add(ref)
                    else:
                        unresolved_refs[label].add(ref)

        return symbol_refs, unresolved_refs, doc_metadata, frontmatter_issues

    @staticmethod
    def normalize_symbol_in_documents(root_path, alias_to_symbol, canonical_symbol):
        touched_files = 0
        replacements = 0
        canonical_key = canonical_symbol.casefold()

        for path, _ in TDocProjectIndex.iter_doc_paths(root_path):

            try:
                content = path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"Error reading {path}: {e}")
                continue

            changed = False

            def replace_link(match):
                nonlocal changed, replacements
                raw = match.group("label")
                display, target = parse_link_components(raw)
                label = link_effective_target(raw)
                if not label:
                    return match.group(0)

                file_path, _ = parse_file_link(label)
                if file_path:
                    return match.group(0)

                symbol = alias_to_symbol.get(label.casefold())
                if not symbol:
                    return match.group(0)
                if symbol.casefold() != canonical_key:
                    return match.group(0)
                if label == canonical_symbol:
                    return match.group(0)

                changed = True
                replacements += 1
                if target is not None:
                    new_display = display
                    if display.casefold() == label.casefold():
                        new_display = canonical_symbol
                    return f"[{compose_link_components(new_display, canonical_symbol)}]"
                return f"[{canonical_symbol}]"

            updated = LINK_PATTERN.sub(replace_link, content)
            if not changed or updated == content:
                continue

            try:
                path.write_text(updated, encoding="utf-8")
                touched_files += 1
            except Exception as e:
                print(f"Error writing {path}: {e}")

        return touched_files, replacements

    @staticmethod
    def validate_project(root_path, content_overrides=None, doc_rel_paths=None):
        findings = []
        marker = TDocProjectIndex.marker_path(root_path)
        normalized_overrides = TDocProjectIndex._normalize_content_overrides(content_overrides)
        rel_filter = None
        if doc_rel_paths is not None:
            rel_filter = {
                str(raw_rel or "").strip().replace("\\", "/").lstrip("/")
                for raw_rel in doc_rel_paths
                if str(raw_rel or "").strip()
            }

        if not marker.exists():
            findings.append(
                {
                    "severity": "error",
                    "message": f"Missing project marker file '{PROJECT_MARKER_FILENAME}'.",
                    "line": None,
                }
            )
            return findings

        marker_content, marker_err = TDocProjectIndex._read_text_with_overrides(marker, normalized_overrides)
        if marker_err is not None:
            findings.append(
                {
                    "severity": "error",
                    "message": f"Cannot read {PROJECT_MARKER_FILENAME}: {marker_err}",
                    "line": None,
                }
            )
            return findings
        lines = str(marker_content or "").splitlines()

        section_line = {}
        section_count = defaultdict(int)
        symbol_line = {}
        alias_owner = {}
        current_section = ""
        include_patterns = []
        ignore_patterns = []

        idx = 0
        while idx < len(lines):
            raw = lines[idx]
            line = raw.strip()
            if not line or line.startswith("#"):
                idx += 1
                continue

            rule, patterns = TDocProjectIndex._parse_rule_line(line)
            if rule:
                if not patterns:
                    findings.append(
                        {
                            "severity": "warning",
                            "message": f"Rule '{rule}:' has no patterns.",
                            "line": idx + 1,
                        }
                    )
                elif rule == "include":
                    include_patterns.extend(patterns)
                elif rule == "ignore":
                    ignore_patterns.extend(patterns)
                idx += 1
                continue

            schema_rule = TDocProjectIndex._parse_frontmatter_schema_rule(line)
            if schema_rule is not None:
                if not schema_rule:
                    findings.append(
                        {
                            "severity": "warning",
                            "message": "Rule 'frontmatter_schema:' requires a JSON path value.",
                            "line": idx + 1,
                        }
                    )
                idx += 1
                continue
            index_group_rule = TDocProjectIndex._parse_index_group_by_rule(line)
            if index_group_rule is not None:
                if index_group_rule not in {"none", "folder"}:
                    findings.append(
                        {
                            "severity": "warning",
                            "message": "Rule 'index_group_by:' supports only 'none' or 'folder'.",
                            "line": idx + 1,
                        }
                    )
                idx += 1
                continue

            if TDocProjectIndex._is_section_header(line):
                m = SECTION_HEADER_PATTERN.match(line)
                section = m.group("section").strip() if m else ""
                if not section:
                    findings.append(
                        {"severity": "error", "message": "Empty section header.", "line": idx + 1}
                    )
                    idx += 1
                    continue
                if section.casefold() in section_line:
                    findings.append(
                        {
                            "severity": "warning",
                            "message": f"Duplicate section header '{section}'.",
                            "line": idx + 1,
                        }
                    )
                else:
                    section_line[section.casefold()] = idx + 1
                lead = section[0]
                if lead.isalpha() and lead != lead.upper():
                    findings.append(
                        {
                            "severity": "warning",
                            "message": f"Section header '{section}' should begin with a capital letter.",
                            "line": idx + 1,
                        }
                    )
                section_count[section.casefold()] += 0
                current_section = section
                idx += 1
                continue

            cont_lines, next_idx = TDocProjectIndex._collect_symbol_continuation_lines(lines, idx)
            primary, _meta_parts = TDocProjectIndex._split_symbol_line_parts(line)
            if "=" in primary and not ALIAS_LINE_PATTERN.match(primary):
                findings.append(
                    {
                        "severity": "error",
                        "message": "Malformed alias definition. Use 'Canonical = Alias1 | Alias2'.",
                        "line": idx + 1,
                    }
                )
                idx = next_idx
                continue

            symbol, alias_items, _, metadata_issues = TDocProjectIndex._parse_symbol_definition(line, cont_lines)
            if not symbol:
                findings.append({"severity": "error", "message": "Empty symbol definition.", "line": idx + 1})
                idx = next_idx
                continue
            for issue in metadata_issues:
                findings.append({"severity": "warning", "message": issue, "line": idx + 1})

            if current_section:
                section_count[current_section.casefold()] += 1

            symbol_key = symbol.casefold()
            if symbol_key in symbol_line:
                findings.append(
                    {
                        "severity": "error",
                        "message": f"Duplicate canonical symbol '{symbol}'.",
                        "line": idx + 1,
                    }
                )
            else:
                symbol_line[symbol_key] = idx + 1

            local_seen = set()
            all_aliases = [symbol] + alias_items
            for alias in all_aliases:
                candidate = alias.strip()
                if not candidate:
                    continue

                key = candidate.casefold()
                if key in local_seen:
                    findings.append(
                        {
                            "severity": "warning",
                            "message": f"Duplicate alias '{candidate}' in one symbol definition.",
                            "line": idx + 1,
                        }
                    )
                    continue
                local_seen.add(key)

                owner = alias_owner.get(key)
                if owner and owner["symbol"].casefold() != symbol_key:
                    findings.append(
                        {
                            "severity": "error",
                            "message": (
                                f"Alias collision '{candidate}' between '{owner['symbol']}' "
                                f"(line {owner['line']}) and '{symbol}'."
                            ),
                            "line": idx + 1,
                        }
                    )
                    continue

                alias_owner[key] = {"symbol": symbol, "line": idx + 1}
            idx = next_idx

        for section_key, line_no in section_line.items():
            if section_count.get(section_key, 0) == 0:
                findings.append(
                    {
                        "severity": "warning",
                        "message": f"Section '{lines[line_no - 1].strip()}' has no symbols.",
                        "line": line_no,
                    }
                )

        (
            alias_to_symbol,
            _,
            _,
            include_patterns_loaded,
            ignore_patterns_loaded,
            _,
        ) = TDocProjectIndex.load_aliases(root_path)
        effective_includes = include_patterns_loaded or include_patterns
        effective_ignores = ignore_patterns_loaded or ignore_patterns
        frontmatter_schema, schema_issues = TDocProjectIndex.load_frontmatter_schema(
            root_path,
            content_overrides=normalized_overrides,
        )
        for issue in schema_issues:
            findings.append(
                {
                    "severity": str(issue.get("severity") or "warning").strip().lower() or "warning",
                    "message": str(issue.get("message") or "").strip(),
                    "line": issue.get("line"),
                    "file": str(issue.get("file") or "").strip() or None,
                }
            )
        _, unresolved_refs, _, frontmatter_issues = TDocProjectIndex.collect_symbol_references(
            root_path,
            alias_to_symbol,
            effective_includes,
            effective_ignores,
            content_overrides=normalized_overrides,
            rel_path_filter=rel_filter,
        )
        for issue in frontmatter_issues:
            findings.append(
                {
                    "severity": "warning",
                    "message": f"{issue['file']}:{issue['line']} - {issue['message']}",
                    "line": None,
                }
        )
        for unresolved in sorted(unresolved_refs.keys(), key=str.casefold):
            refs = sorted(unresolved_refs[unresolved], key=lambda x: (x[0].casefold(), x[1]))
            sample = ", ".join(f"{str(row[0])}#L{int(row[1])}" for row in refs[:3])
            extra = f" (+{len(refs) - 3} more)" if len(refs) > 3 else ""
            finding = {
                "severity": "warning",
                "message": f"Unresolved symbol '{unresolved}' used at {sample}{extra}.",
                "line": None,
            }
            if refs:
                first = refs[0]
                finding["file"] = str(first[0])
                finding["line"] = int(first[1])
                finding["column"] = int(first[2])
                finding["end_line"] = int(first[1])
                finding["end_column"] = int(first[3])
            findings.append(finding)

        raw_schema_keys = frontmatter_schema.get("keys") if isinstance(frontmatter_schema, dict) else []
        schema_key_names = [
            str(raw).strip()
            for raw in (raw_schema_keys if isinstance(raw_schema_keys, list) else [])
            if str(raw).strip()
        ]
        schema_key_set = {name.casefold() for name in schema_key_names}
        raw_schema_required = frontmatter_schema.get("required") if isinstance(frontmatter_schema, dict) else []
        schema_required = [
            str(raw).strip()
            for raw in (raw_schema_required if isinstance(raw_schema_required, list) else [])
            if str(raw).strip()
        ]
        schema_values_raw = frontmatter_schema.get("values_by_key") if isinstance(frontmatter_schema, dict) else {}
        schema_values_by_key: dict[str, tuple[str, set[str]]] = {}
        if isinstance(schema_values_raw, dict):
            for raw_key, raw_items in schema_values_raw.items():
                key_name = str(raw_key or "").strip()
                if not key_name:
                    continue
                allowed = {
                    str(item).strip()
                    for item in (raw_items if isinstance(raw_items, list) else [])
                    if str(item).strip()
                }
                if allowed:
                    schema_values_by_key[key_name.casefold()] = (key_name, allowed)
        allow_unknown_keys = True
        if isinstance(frontmatter_schema, dict):
            allow_unknown_keys = _coerce_bool(frontmatter_schema.get("allow_unknown_keys"), default=True)

        seen_missing_images = set()
        for path, rel_path in TDocProjectIndex.iter_doc_paths(
            root_path,
            effective_includes,
            effective_ignores,
            rel_path_filter=rel_filter,
        ):
            content, _err = TDocProjectIndex._read_text_with_overrides(path, normalized_overrides)
            if content is None:
                continue
            metadata, body_lines, body_start_line, _ = parse_doc_frontmatter(content)
            metadata_map = metadata if isinstance(metadata, dict) else {}
            metadata_keys_cf = {str(k or "").strip().casefold() for k in metadata_map.keys() if str(k or "").strip()}
            key_line_map: dict[str, int] = {}
            raw_lines = str(content or "").splitlines()
            if raw_lines and raw_lines[0].strip() == "---":
                for idx_line, raw_line in enumerate(raw_lines[1:], start=2):
                    if raw_line.strip() == "---":
                        break
                    match = FRONTMATTER_KV_PATTERN.match(raw_line)
                    if not match:
                        continue
                    key = str(match.group("key") or "").strip()
                    if not key:
                        continue
                    key_line_map.setdefault(key.casefold(), int(idx_line))

            if schema_required:
                for req in schema_required:
                    req_cf = req.casefold()
                    if req_cf in metadata_keys_cf:
                        continue
                    findings.append(
                        {
                            "severity": "warning",
                            "message": f"Missing required frontmatter key '{req}' in {rel_path}.",
                            "line": int(key_line_map.get(req_cf, 1)),
                            "file": str(rel_path),
                        }
                    )

            if not allow_unknown_keys and schema_key_set:
                for meta_key in metadata_map.keys():
                    key_text = str(meta_key or "").strip()
                    if not key_text:
                        continue
                    if key_text.casefold() in schema_key_set:
                        continue
                    findings.append(
                        {
                            "severity": "warning",
                            "message": f"Unknown frontmatter key '{key_text}' in {rel_path}.",
                            "line": int(key_line_map.get(key_text.casefold(), 1)),
                            "file": str(rel_path),
                        }
                    )

            if schema_values_by_key:
                for meta_key, meta_value in metadata_map.items():
                    key_cf = str(meta_key or "").strip().casefold()
                    if not key_cf:
                        continue
                    allowed_info = schema_values_by_key.get(key_cf)
                    if not allowed_info:
                        continue
                    key_name, allowed_values = allowed_info
                    value_text = str(meta_value or "").strip()
                    if not value_text:
                        continue
                    if value_text in allowed_values:
                        continue
                    allowed_preview = ", ".join(sorted(allowed_values, key=str.casefold)[:8])
                    findings.append(
                        {
                            "severity": "warning",
                            "message": (
                                f"Frontmatter key '{key_name}' has invalid value '{value_text}' in {rel_path}. "
                                f"Expected one of: {allowed_preview}."
                            ),
                            "line": int(key_line_map.get(key_cf, 1)),
                            "file": str(rel_path),
                        }
                    )

            active_numbered_list: dict[str, object] | None = None
            for offset, line in enumerate(body_lines):
                line_no = body_start_line + offset
                numbered_match = MARKDOWN_NUMBERED_LIST_PATTERN.match(str(line or ""))
                if numbered_match:
                    indent = str(numbered_match.group("indent") or "")
                    marker = str(numbered_match.group("marker") or ".")
                    try:
                        current_number = int(numbered_match.group("number") or 0)
                    except Exception:
                        current_number = 0
                    if (
                        isinstance(active_numbered_list, dict)
                        and str(active_numbered_list.get("indent") or "") == indent
                        and str(active_numbered_list.get("marker") or ".") == marker
                    ):
                        previous_number = int(active_numbered_list.get("number") or 0)
                        expected_number = previous_number + 1
                        if current_number != expected_number:
                            findings.append(
                                {
                                    "severity": "warning",
                                    "message": (
                                        f"Numbered list item {current_number} is out of sequence "
                                        f"(expected {expected_number}) at {rel_path}#L{line_no}."
                                    ),
                                    "line": int(line_no),
                                    "column": int(numbered_match.start("number") + 1),
                                    "end_line": int(line_no),
                                    "end_column": int(numbered_match.end("number") + 1),
                                    "file": str(rel_path),
                                }
                            )
                    active_numbered_list = {
                        "indent": indent,
                        "marker": marker,
                        "number": current_number,
                    }
                else:
                    active_numbered_list = None

                for match in IMAGE_PATTERN.finditer(line):
                    raw = str(match.group("body") or "").strip()
                    if not raw:
                        continue
                    _caption, raw_path = parse_image_components(raw)
                    rel_image = str(raw_path or "").strip()
                    if not rel_image:
                        continue
                    if rel_image.startswith("~") or os.path.isabs(rel_image) or _WINDOWS_DRIVE_PATH_PATTERN.match(rel_image):
                        continue
                    abs_image = os.path.normpath(os.path.join(str(root_path), rel_image))
                    if os.path.exists(abs_image):
                        continue
                    key = (str(rel_path), int(line_no), rel_image.casefold())
                    if key in seen_missing_images:
                        continue
                    seen_missing_images.add(key)
                    findings.append(
                        {
                            "severity": "warning",
                            "message": f"Missing image file '{rel_image}' used at {rel_path}#L{line_no}.",
                            "line": int(line_no),
                            "column": int(match.start("body") + 1),
                            "end_line": int(line_no),
                            "end_column": int(match.end("body") + 1),
                            "file": str(rel_path),
                        }
                    )

        return findings

    @staticmethod
    def _group_refs_by_file(refs):
        grouped = defaultdict(set)
        for row in refs:
            if not isinstance(row, (tuple, list)) or len(row) < 2:
                continue
            rel_path = row[0]
            line_no = row[1]
            try:
                ln = max(1, int(line_no))
            except Exception:
                continue
            grouped[str(rel_path)].add(ln)
        rows = []
        for rel_path in sorted(grouped.keys(), key=str.casefold):
            rows.append((rel_path, sorted(grouped[rel_path])))
        return rows

    @staticmethod
    def _group_refs_by_folder(refs):
        grouped = defaultdict(lambda: defaultdict(set))
        for row in refs:
            if not isinstance(row, (tuple, list)) or len(row) < 2:
                continue
            rel_path = str(row[0] or "")
            line_no = row[1]
            try:
                ln = max(1, int(line_no))
            except Exception:
                continue
            folder = TDocProjectIndex._relative_parent_folder(rel_path)
            grouped[folder][rel_path].add(ln)

        rows = []
        folder_order = sorted(
            grouped.keys(),
            key=lambda name: (0 if str(name) == "." else 1, str(name).casefold()),
        )
        for folder in folder_order:
            files = []
            for rel_path in sorted(grouped[folder].keys(), key=str.casefold):
                files.append((rel_path, sorted(grouped[folder][rel_path])))
            rows.append((folder, files))
        return rows

    @staticmethod
    def build_index(root_path):
        """Generates index.tdoc at project root if .tdocproject marker exists.

        Managed index content is stored under a dashed separator line so
        users can keep comments/notes above it.
        """
        root = Path(root_path)
        if not TDocProjectIndex.has_project_marker(root):
            return None

        (
            alias_to_symbol,
            symbol_to_aliases,
            symbol_to_section,
            include_patterns,
            ignore_patterns,
            symbol_to_metadata,
        ) = TDocProjectIndex.load_aliases(root)
        symbol_refs, unresolved_refs, doc_metadata, frontmatter_issues = TDocProjectIndex.collect_symbol_references(
            root, alias_to_symbol, include_patterns, ignore_patterns
        )
        marker_lines = []
        marker_path = TDocProjectIndex.marker_path(root)
        try:
            marker_lines = marker_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            marker_lines = []
        section_header_warnings = TDocProjectIndex._section_header_capitalization_warnings(marker_lines)
        index_group_mode, index_group_warnings = TDocProjectIndex._index_group_by_config_from_lines(marker_lines)

        lines = ["# Index", ""]
        indent = " " * 4

        if not symbol_refs and not unresolved_refs:
            lines.append(f"{indent}No symbols indexed yet.")
        else:
            section_to_symbols = defaultdict(list)
            section_order: list[str] = []
            seen_symbols: set[str] = set()
            for symbol in symbol_to_aliases.keys():
                if symbol not in symbol_refs:
                    continue
                section = symbol_to_section.get(symbol) or "Uncategorized"
                if section not in section_to_symbols:
                    section_order.append(section)
                section_to_symbols[section].append(symbol)
                seen_symbols.add(symbol)

            for symbol in sorted(symbol_refs.keys(), key=str.casefold):
                if symbol in seen_symbols:
                    continue
                section = symbol_to_section.get(symbol) or "Uncategorized"
                if section not in section_to_symbols:
                    section_order.append(section)
                section_to_symbols[section].append(symbol)

            for section in section_order:
                lines.append(f"## {section}")
                for symbol in section_to_symbols[section]:
                    refs = sorted(symbol_refs[symbol], key=lambda x: (x[0].casefold(), x[1]))
                    aliases = symbol_to_aliases.get(symbol, [symbol])
                    metadata = symbol_to_metadata.get(symbol, {})
                    lines.append(f"{indent}[{symbol}]")
                    alias_links: list[str] = []
                    alias_seen: set[str] = set()
                    for alias in aliases:
                        alias_text = str(alias or "").strip()
                        if not alias_text:
                            continue
                        alias_key = alias_text.casefold()
                        if alias_key == symbol.casefold() or alias_key in alias_seen:
                            continue
                        alias_seen.add(alias_key)
                        alias_links.append(f"[{alias_text}]")
                    if alias_links:
                        lines.append(f"{indent * 2}Aliases: " + ", ".join(alias_links))
                    if isinstance(metadata, dict) and metadata:
                        metadata_items = []
                        for key, value in metadata.items():
                            meta_key = str(key or "").strip()
                            meta_value = str(value or "").strip()
                            if not meta_key or not meta_value:
                                continue
                            metadata_items.append(f"{meta_key}={meta_value}")
                        if metadata_items:
                            lines.append(f"{indent * 2}Metadata: " + "; ".join(metadata_items))
                    if index_group_mode == "folder":
                        for folder, folder_files in TDocProjectIndex._group_refs_by_folder(refs):
                            folder_label = "(root)" if folder == "." else folder
                            lines.append(f"{indent * 2}Folder: {folder_label}")
                            for rel_path, line_numbers in folder_files:
                                line_links = ", ".join(f"[{ln}|{rel_path}#L{ln}]" for ln in line_numbers)
                                lines.append(f"{indent * 3}[{rel_path}]: {line_links}")
                    else:
                        for rel_path, line_numbers in TDocProjectIndex._group_refs_by_file(refs):
                            line_links = ", ".join(f"[{ln}|{rel_path}#L{ln}]" for ln in line_numbers)
                            lines.append(f"{indent * 2}[{rel_path}]: {line_links}")
                    lines.append("")
                lines.append("")

            if unresolved_refs:
                lines.append("## Unresolved")
                lines.append(f"{indent}These symbols are used but not defined in .tdocproject.")
                for unresolved in sorted(unresolved_refs.keys(), key=str.casefold):
                    refs = sorted(unresolved_refs[unresolved], key=lambda x: (x[0].casefold(), x[1]))
                    lines.append(f"{indent}[{unresolved}]")
                    if index_group_mode == "folder":
                        for folder, folder_files in TDocProjectIndex._group_refs_by_folder(refs):
                            folder_label = "(root)" if folder == "." else folder
                            lines.append(f"{indent * 2}Folder: {folder_label}")
                            for rel_path, line_numbers in folder_files:
                                line_links = ", ".join(f"[{ln}|{rel_path}#L{ln}]" for ln in line_numbers)
                                lines.append(f"{indent * 3}[{rel_path}]: {line_links}")
                    else:
                        for rel_path, line_numbers in TDocProjectIndex._group_refs_by_file(refs):
                            line_links = ", ".join(f"[{ln}|{rel_path}#L{ln}]" for ln in line_numbers)
                            lines.append(f"{indent * 2}[{rel_path}]: {line_links}")
                    lines.append("")
                lines.append("")

        if doc_metadata:
            lines.append("## Documents")
            if index_group_mode == "folder":
                docs_by_folder = defaultdict(list)
                for rel_path in sorted(doc_metadata.keys(), key=str.casefold):
                    folder = TDocProjectIndex._relative_parent_folder(rel_path)
                    docs_by_folder[folder].append(rel_path)
                for folder in sorted(docs_by_folder.keys(), key=lambda name: (0 if name == "." else 1, name.casefold())):
                    folder_label = "(root)" if folder == "." else folder
                    lines.append(f"{indent}Folder: {folder_label}")
                    for rel_path in docs_by_folder[folder]:
                        metadata = doc_metadata[rel_path]
                        lines.append(f"{indent * 2}[{rel_path}]")
                        if metadata:
                            lines.append(f"{indent * 3}Metadata: " + "; ".join(f"{k}={v}" for k, v in metadata.items()))
                        lines.append(f"{indent * 3}Indexing: " + ("on" if is_index_enabled(metadata) else "off"))
            else:
                for rel_path in sorted(doc_metadata.keys(), key=str.casefold):
                    metadata = doc_metadata[rel_path]
                    lines.append(f"{indent}[{rel_path}]")
                    if metadata:
                        lines.append(f"{indent * 2}Metadata: " + "; ".join(f"{k}={v}" for k, v in metadata.items()))
                    lines.append(f"{indent * 2}Indexing: " + ("on" if is_index_enabled(metadata) else "off"))
            lines.append("")
            lines.append("")

        project_warnings = list(section_header_warnings) + list(index_group_warnings)
        if project_warnings:
            lines.append("## Project Warnings")
            for issue in project_warnings:
                lines.append(
                    f"{indent}{PROJECT_MARKER_FILENAME}#L{issue['line']} {issue['message']}"
                )
            lines.append("")
            lines.append("")

        if frontmatter_issues:
            lines.append("## Frontmatter Warnings")
            for issue in frontmatter_issues:
                lines.append(f"{indent}[{issue['file']}#L{issue['line']}] {issue['message']}")
            lines.append("")
            lines.append("")

        index_path = TDocProjectIndex.index_path(root)
        generated_text = "\n".join(lines).rstrip() + "\n"

        existing = ""
        if index_path.exists():
            try:
                existing = index_path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"Error reading {index_path}: {e}")
                return None

        try:
            manual_text = str(existing or "")
            start = manual_text.find(LEGACY_INDEX_AUTO_START)
            end = manual_text.find(LEGACY_INDEX_AUTO_END)
            if start != -1 and end != -1 and start < end:
                end_after = end + len(LEGACY_INDEX_AUTO_END)
                manual_text = (manual_text[:start] + manual_text[end_after:]).strip()

            manual_lines = manual_text.splitlines()
            sep_idx = -1
            for idx, raw in enumerate(manual_lines):
                stripped = raw.strip()
                if stripped and all(ch == "-" for ch in stripped) and len(stripped) >= 4:
                    sep_idx = idx
                    break
            if sep_idx >= 0:
                manual_text = "\n".join(manual_lines[:sep_idx]).rstrip()
            else:
                manual_text = manual_text.rstrip()

            if manual_text:
                merged = (
                    f"{manual_text}\n\n"
                    f"{INDEX_SEPARATOR_LINE}\n"
                    f"{generated_text}"
                )
            else:
                merged = f"{INDEX_SEPARATOR_LINE}\n{generated_text}"

            index_path.write_text(merged.rstrip() + "\n", encoding="utf-8")
        except Exception as e:
            print(f"Error writing {index_path}: {e}")
            return None

        return index_path


class _TDocSearchBar(QFrame):
    def __init__(self, editor: "TDocEditorWidget"):
        super().__init__(editor)
        self._editor = editor
        self._replace_visible = False
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("tdocSearchBar")
        self.setStyleSheet(
            """
            QFrame#tdocSearchBar {
                background: #252526;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
            }
            QLineEdit, QPushButton, QCheckBox {
                font-size: 10pt;
            }
            QLineEdit {
                min-height: 24px;
                padding: 2px 6px;
                border: 1px solid #4a4a4a;
                background: #1e1e1e;
            }
            QPushButton {
                min-height: 24px;
                padding: 0 8px;
            }
            QCheckBox {
                spacing: 4px;
            }
            """
        )

        self.find_edit = _build_search_line_edit(self._editor, self, role="find")
        self.find_edit.setPlaceholderText("Find")
        self.find_edit.installEventFilter(self)

        self.replace_edit = _build_search_line_edit(self._editor, self, role="replace")
        self.replace_edit.setPlaceholderText("Replace")
        self.replace_edit.installEventFilter(self)

        self.prev_btn = QPushButton("Prev", self)
        self.next_btn = QPushButton("Next", self)
        self.replace_btn = QPushButton("Replace", self)
        self.replace_all_btn = QPushButton("Replace All", self)
        self.case_box = QCheckBox("Case", self)
        self.word_box = QCheckBox("Word", self)
        self.regex_box = QCheckBox("Regex", self)
        self.selection_box = QCheckBox("Selection", self)
        self.count_lbl = QLabel("0 / 0", self)
        self.close_btn = QPushButton("X", self)
        self.close_btn.setFixedWidth(24)

        self.prev_btn.clicked.connect(self._editor.search_previous)
        self.next_btn.clicked.connect(self._editor.search_next)
        self.replace_btn.clicked.connect(self._editor.replace_current_or_next)
        self.replace_all_btn.clicked.connect(self._editor.replace_all_matches)
        self.close_btn.clicked.connect(self._editor.hide_search_bar)

        self.find_edit.textChanged.connect(self._editor._on_search_query_changed)
        self.replace_edit.textChanged.connect(self._editor._on_replace_query_changed)
        self.case_box.toggled.connect(self._editor._on_search_option_changed)
        self.word_box.toggled.connect(self._editor._on_search_option_changed)
        self.regex_box.toggled.connect(self._editor._on_search_option_changed)
        self.selection_box.toggled.connect(self._editor._on_search_option_changed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)
        lay.addWidget(self.find_edit, 2)
        lay.addWidget(self.replace_edit, 2)
        lay.addWidget(self.prev_btn)
        lay.addWidget(self.next_btn)
        lay.addWidget(self.replace_btn)
        lay.addWidget(self.replace_all_btn)
        lay.addWidget(self.case_box)
        lay.addWidget(self.word_box)
        lay.addWidget(self.regex_box)
        lay.addWidget(self.selection_box)
        lay.addWidget(self.count_lbl)
        lay.addWidget(self.close_btn)
        self.set_replace_visible(False)

    def set_replace_visible(self, visible: bool):
        self._replace_visible = bool(visible)
        self.replace_edit.setVisible(self._replace_visible)
        self.replace_btn.setVisible(self._replace_visible)
        self.replace_all_btn.setVisible(self._replace_visible)
        self.adjustSize()

    def set_count_text(self, text: str):
        self.count_lbl.setText(str(text or "0 / 0"))

    def set_in_selection_checked(self, checked: bool):
        blocker = self.selection_box.blockSignals(True)
        self.selection_box.setChecked(bool(checked))
        self.selection_box.blockSignals(blocker)

    def focus_find(self):
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def eventFilter(self, watched, event):
        if watched in {self.find_edit, self.replace_edit} and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    self._editor.search_previous()
                else:
                    self._editor.search_next()
                return True
            if key == Qt.Key_Escape:
                self._editor.hide_search_bar()
                return True
        return super().eventFilter(watched, event)


class _TDocOverviewMarkerArea(QWidget):
    def __init__(self, editor: "TDocEditorWidget"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return QSize(self._editor.overviewMarkerAreaWidth(), 0)

    def paintEvent(self, event):
        self._editor.overviewMarkerAreaPaintEvent(event)

    def mousePressEvent(self, event):
        self._editor.overviewMarkerAreaMousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._editor.overviewMarkerAreaMousePressEvent(event)


class _TDocLineNumberArea(QWidget):
    def __init__(self, editor: "TDocEditorWidget"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return QSize(self._editor.lineNumberAreaWidth(), 0)

    def paintEvent(self, event):
        self._editor.lineNumberAreaPaintEvent(event)

    def mousePressEvent(self, event):
        self._editor.lineNumberAreaMousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._editor.lineNumberAreaMousePressEvent(event)


class _TDocCompletionItemDelegate(QStyledItemDelegate):
    def __init__(self, editor: "TDocEditorWidget"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        row_h = max(base.height(), self._editor.fontMetrics().height() + 8)
        return QSize(base.width(), row_h)

    @staticmethod
    def _kind_color(kind: str, palette: QPalette, selected: bool) -> QColor:
        if selected:
            return palette.color(QPalette.HighlightedText)
        k = str(kind or "").strip().lower()
        if k == "symbol":
            return QColor("#DCDCAA")
        if k == "folder":
            return QColor("#4EC9B0")
        if k == "file":
            return QColor("#9CDCFE")
        return palette.color(QPalette.Text)

    def paint(self, painter, option, index):
        meta = index.data(_TDOC_COMPLETION_META_ROLE)
        if not isinstance(meta, dict):
            super().paint(painter, option, index)
            return

        style = option.widget.style() if option.widget is not None else QApplication.style()
        style_opt = QStyleOptionViewItem(option)
        style_opt.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, style_opt, painter, option.widget)

        rect = option.rect.adjusted(8, 0, -8, 0)
        if rect.width() <= 0:
            return

        primary = str(meta.get("primary") or "")
        right = str(meta.get("right") or "")
        kind = str(meta.get("kind") or "item")
        fm = option.fontMetrics
        selected = bool(option.state & QStyle.State_Selected)

        right_width = 0
        if right:
            right_width = min(max(36, fm.horizontalAdvance(right) + 8), int(rect.width() * 0.42))

        right_rect = QRect(rect.right() - right_width + 1, rect.top(), right_width, rect.height())
        main_rect = QRect(rect.left(), rect.top(), max(0, rect.width() - right_width - 10), rect.height())

        painter.save()
        if right and right_rect.width() > 0:
            right_pen = (
                option.palette.color(QPalette.HighlightedText)
                if selected
                else option.palette.color(QPalette.PlaceholderText)
            )
            painter.setPen(right_pen)
            painter.drawText(
                right_rect.adjusted(0, 0, -2, 0),
                Qt.AlignRight | Qt.AlignVCenter,
                fm.elidedText(right, Qt.ElideRight, right_rect.width()),
            )

        painter.setPen(self._kind_color(kind, option.palette, selected))
        painter.drawText(
            main_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            fm.elidedText(primary, Qt.ElideRight, main_rect.width()),
        )
        painter.restore()


class TDocEditorWidget(QTextEdit):
    editorFontSizeStepRequested = Signal(int)  # +1 / -1
    aiAssistRequested = Signal(str)  # reason: manual | passive
    LINK_PROPERTY = QTextCharFormat.UserProperty + 1
    LINK_LABEL_PROPERTY = QTextCharFormat.UserProperty + 2
    LINK_RAW_PROPERTY = QTextCharFormat.UserProperty + 3
    IMAGE_RAW_PROPERTY = QTextCharFormat.UserProperty + 4
    IMAGE_PATH_PROPERTY = QTextCharFormat.UserProperty + 5
    HEADING_MARKDOWN_PROPERTY = QTextCharFormat.UserProperty + 6
    HEADING_LEVEL_PROPERTY = QTextCharFormat.UserProperty + 7
    HEADING_RAW_PROPERTY = QTextCharFormat.UserProperty + 8
    BULLET_MARKDOWN_PROPERTY = QTextCharFormat.UserProperty + 9
    BULLET_RAW_PROPERTY = QTextCharFormat.UserProperty + 10
    _default_keybindings: dict[str, dict[str, list[str]]] = {
        "general": {
            "action.find": ["Ctrl+F"],
            "action.replace": ["Ctrl+H"],
            "action.trigger_completion": ["Ctrl+Space"],
            "action.ai_inline_assist": ["Alt+\\"],
            "action.ai_inline_assist_alt_space": ["Alt+Space"],
            "action.ai_inline_assist_ctrl_alt_space": ["Ctrl+Alt+Space"],
            "action.duplicate_selection_or_line": ["Ctrl+D"],
        },
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        font = QFont("Consolas", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.use_tabs = False
        self.indent_width = 4
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._editor_background_color = QColor("#252526")
        self._editor_background_image_path = ""
        self._editor_background_scale_mode = "stretch"
        self._editor_background_image_brightness = 100
        self._editor_background_tint_color = QColor("#000000")
        self._editor_background_tint_strength = 0
        self._gutter_background_color = QColor()
        self._gutter_foreground_color = QColor()
        self._gutter_active_foreground_color = QColor()
        self._gutter_fold_marker_color = QColor()
        self._editor_background_source_pixmap: QPixmap | None = None
        self._editor_background_cache_size = QSize()
        self._editor_background_cache_pixmap: QPixmap | None = None
        self._change_region_dirty_lines: set[int] = set()
        self._change_region_uncommitted_lines: set[int] = set()
        self._change_region_dirty_color = parse_editor_overlay_color(
            DEFAULT_EDITOR_DIRTY_BACKGROUND_HEX,
            DEFAULT_EDITOR_DIRTY_BACKGROUND_HEX,
        )
        self._change_region_uncommitted_color = parse_editor_overlay_color(
            DEFAULT_EDITOR_UNCOMMITTED_BACKGROUND_HEX,
            DEFAULT_EDITOR_UNCOMMITTED_BACKGROUND_HEX,
        )
        self._change_region_selections: list[QTextEdit.ExtraSelection] = []
        self._overview_change_region_dirty_lines: set[int] = set()
        self._overview_change_region_uncommitted_lines: set[int] = set()
        self._configured_keybindings = {
            "general": {
                key: list(value)
                for key, value in self._default_keybindings.get("general", {}).items()
            },
        }
        self._configured_shortcuts: list[QShortcut] = []
        self._apply_editor_background_palette()

        self._lint_visual_cfg = dict(_TDOC_LINT_VISUAL_DEFAULTS)
        self._overview_cfg = dict(_TDOC_OVERVIEW_MARKER_DEFAULTS)
        self._lint_diagnostics: list[dict] = []
        self._lint_line_severity: dict[int, str] = {}
        self._lint_selections: list[QTextEdit.ExtraSelection] = []
        self._spellcheck_diagnostics: list[dict[str, int]] = []
        self._spellcheck_selections: list[QTextEdit.ExtraSelection] = []
        self._spellcheck_visual_cfg: dict[str, object] = {
            "enabled": False,
            "color": "#66C07A",
        }
        self._overview_search_lines: set[int] = set()
        self._overview_active_search_lines: set[int] = set()
        self._overview_occurrence_lines: set[int] = set()
        self._overview_occurrence_term = ""
        self._occurrence_highlight_selections: list[QTextEdit.ExtraSelection] = []

        self._search_bar = _TDocSearchBar(self)
        self._search_bar.hide()
        self._search_selection_range: tuple[int, int] | None = None
        self._search_matches: list[tuple[int, int]] = []
        self._search_current_index = -1
        self._search_highlight_selections: list[QTextEdit.ExtraSelection] = []
        self._search_active_selection: QTextEdit.ExtraSelection | None = None
        self._search_last_error = ""
        self._search_refresh_timer = QTimer(self)
        self._search_refresh_timer.setSingleShot(True)
        self._search_refresh_timer.setInterval(110)
        self._search_refresh_timer.timeout.connect(self._refresh_search_matches)
        self._occurrence_refresh_timer = QTimer(self)
        self._occurrence_refresh_timer.setSingleShot(True)
        self._occurrence_refresh_timer.setInterval(90)
        self._occurrence_refresh_timer.timeout.connect(self._refresh_occurrence_markers)

        self._completion_popup = QListWidget(self)
        self._completion_popup.setFocusPolicy(Qt.NoFocus)
        self._completion_popup.setMouseTracking(True)
        self._completion_popup.viewport().setMouseTracking(True)
        self._completion_popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._completion_popup.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._completion_popup.setUniformItemSizes(True)
        self._completion_popup.setSelectionMode(QListWidget.SingleSelection)
        self._completion_popup.setItemDelegate(_TDocCompletionItemDelegate(self))
        self._completion_popup.setStyleSheet(
            """
            QListWidget {
                background: #1f1f1f;
                border: 1px solid #3a3a3a;
                padding: 2px;
            }
            QListWidget::item {
                padding: 3px 4px;
            }
            QListWidget::item:selected {
                background: #264f78;
            }
            """
        )
        self._completion_popup.hide()
        self._completion_candidates: list[dict] = []
        self._completion_refresh_timer = QTimer(self)
        self._completion_refresh_timer.setSingleShot(True)
        self._completion_refresh_timer.setInterval(60)
        self._completion_refresh_timer.timeout.connect(self._refresh_tdoc_completion_popup)
        self._inline_image_rescale_timer = QTimer(self)
        self._inline_image_rescale_timer.setSingleShot(True)
        self._inline_image_rescale_timer.setInterval(90)
        self._inline_image_rescale_timer.timeout.connect(self._rescale_inline_images_to_viewport)
        self._completion_manual_request = False
        self._completion_refresh_from_text_change = False
        self._completion_auto_trigger = True
        self._completion_auto_min_chars = 2
        self._frontmatter_visible = False
        self._frontmatter_fold_gutter_width = 12
        self._fold_provider: Callable[[str], list[tuple[int, int]]] | None = None
        self._fold_ranges: dict[int, int] = {}
        self._folded_starts: set[int] = set()
        self._fold_refresh_timer = QTimer(self)
        self._fold_refresh_timer.setSingleShot(True)
        self._fold_refresh_timer.setInterval(140)
        self._fold_refresh_timer.timeout.connect(self._refresh_fold_ranges)

        self.textChanged.connect(self._on_text_changed_search_refresh)
        self.textChanged.connect(self._schedule_occurrence_marker_refresh)
        self.textChanged.connect(self._on_text_changed_tdoc_completion_refresh)
        self.textChanged.connect(self._on_text_changed_frontmatter_visibility)
        self.textChanged.connect(self._refresh_line_number_area)
        self.textChanged.connect(self._schedule_fold_refresh)
        self._completion_popup.itemClicked.connect(lambda _item: self._accept_tdoc_completion())

        self.lineNumberArea = _TDocLineNumberArea(self)
        self.overviewMarkerArea = _TDocOverviewMarkerArea(self)
        self.document().blockCountChanged.connect(self.updateLineNumberAreaWidth)
        self.verticalScrollBar().rangeChanged.connect(self._on_scrollbar_range_changed)
        self.verticalScrollBar().valueChanged.connect(self._refresh_line_number_area)
        self.verticalScrollBar().valueChanged.connect(self._refresh_overview_marker_area)
        self.horizontalScrollBar().rangeChanged.connect(self._on_scrollbar_range_changed)
        self.cursorPositionChanged.connect(self.highlightCurrentLine)
        self.cursorPositionChanged.connect(self._on_cursor_position_changed_for_link_editing)
        self.cursorPositionChanged.connect(self._schedule_occurrence_marker_refresh)
        self.cursorPositionChanged.connect(self._on_cursor_position_changed_tdoc_completion_refresh)
        self.cursorPositionChanged.connect(self._refresh_line_number_area)
        self.selectionChanged.connect(self._schedule_occurrence_marker_refresh)

        self._is_internal_change = False
        self._last_cursor_pos = int(self.textCursor().position())
        self._inline_suggestion_text = ""
        self._inline_suggestion_anchor_pos = -1
        self._inline_suggestion_anchor_revision = -1
        self.open_file_by_name = None
        self.open_symbol = None
        self.list_symbol_completion_candidates = None
        self.list_path_completion_candidates = None
        self.list_frontmatter_completion_candidates = None
        self.resolve_image_path = None
        self.resolve_symbol = None
        self.resolve_link_tooltip = None
        self.rename_alias = None
        self.normalize_symbol = None
        self.go_to_symbol_definition = None
        self.populate_context_menu = None
        self._hover_tooltip_target = ""
        self._hover_tooltip_text = ""
        self._apply_viewport_margins()
        self._position_search_bar()
        self.highlightCurrentLine()
        self.updateLineNumberAreaWidth(0)
        self._apply_fold_provider()
        self._schedule_occurrence_marker_refresh()
        self._rebuild_configured_shortcuts()

    @classmethod
    def set_default_keybindings(cls, keybindings: Mapping[str, Mapping[str, list[str]]] | None) -> None:
        merged = {
            "general": {
                key: list(value)
                for key, value in cls._default_keybindings.get("general", {}).items()
            },
        }
        payload = keybindings if isinstance(keybindings, Mapping) else {}
        scoped = payload.get("general")
        if isinstance(scoped, Mapping):
            scope_map = merged.setdefault("general", {})
            for action_id, sequence in scoped.items():
                if not isinstance(sequence, list):
                    continue
                normalized = [str(item).strip() for item in sequence if str(item).strip()]
                if normalized:
                    scope_map[str(action_id)] = normalized
        cls._default_keybindings = merged

    def configure_keybindings(self, keybindings: Mapping[str, Mapping[str, list[str]]] | None) -> None:
        self.__class__.set_default_keybindings(keybindings)
        self._configured_keybindings = {
            "general": {
                key: list(value)
                for key, value in self._default_keybindings.get("general", {}).items()
            },
        }
        self._rebuild_configured_shortcuts()

    def _action_sequence(self, scope: str, action_id: str) -> list[str]:
        scoped = self._configured_keybindings.get(str(scope or "").strip().lower(), {})
        if not isinstance(scoped, dict):
            return []
        sequence = scoped.get(str(action_id or "").strip())
        if not isinstance(sequence, list):
            return []
        return [str(item).strip() for item in sequence if str(item).strip()]

    @staticmethod
    def _sequence_to_qkeysequence(sequence: list[str]) -> QKeySequence:
        return QKeySequence(", ".join(str(item).strip() for item in sequence if str(item).strip()))

    def _event_matches_action_shortcut(self, event: QKeyEvent, scope: str, action_id: str) -> bool:
        sequence = self._action_sequence(scope, action_id)
        if not sequence:
            return False
        chord = str(sequence[0] or "").strip()
        if not chord:
            return False
        target = QKeySequence(chord)
        if target.isEmpty():
            return False
        try:
            mods = event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.MetaModifier
            )
            pressed = QKeySequence(int(mods) | int(event.key()))
        except Exception:
            return False
        return bool(pressed.matches(target) == QKeySequence.SequenceMatch.ExactMatch)

    def _clear_configured_shortcuts(self) -> None:
        for shortcut in self._configured_shortcuts:
            try:
                shortcut.activated.disconnect()
            except Exception:
                pass
            try:
                shortcut.deleteLater()
            except Exception:
                pass
        self._configured_shortcuts.clear()

    def _install_shortcut(self, sequence: list[str], callback) -> None:
        qseq = self._sequence_to_qkeysequence(sequence)
        if qseq.isEmpty():
            return
        shortcut = QShortcut(qseq, self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)
        self._configured_shortcuts.append(shortcut)

    def _rebuild_configured_shortcuts(self) -> None:
        self._clear_configured_shortcuts()
        self._install_shortcut(
            self._action_sequence("general", "action.duplicate_selection_or_line"),
            self._on_duplicate_selection_or_line_shortcut,
        )

    def _on_duplicate_selection_or_line_shortcut(self) -> None:
        self.duplicate_selection_or_line()

    def update_completion_ui_settings(self, cfg: dict | None) -> None:
        payload = cfg if isinstance(cfg, dict) else {}
        self._completion_auto_trigger = bool(payload.get("auto_trigger", True))
        try:
            self._completion_auto_min_chars = max(1, int(payload.get("auto_trigger_min_chars", 2)))
        except Exception:
            self._completion_auto_min_chars = 2

    def update_lint_visual_settings(self, lint_visual_cfg: dict | None) -> None:
        cfg = lint_visual_cfg if isinstance(lint_visual_cfg, dict) else {}
        merged = dict(_TDOC_LINT_VISUAL_DEFAULTS)
        for key in (
            "mode",
            "error_color",
            "warning_color",
            "info_color",
            "hint_color",
            "squiggle_thickness",
            "line_alpha",
        ):
            if key in cfg:
                merged[key] = cfg.get(key)

        mode = str(merged.get("mode") or "squiggle").strip().lower()
        if mode not in {"squiggle", "line", "both"}:
            mode = "squiggle"
        merged["mode"] = mode

        for color_key, fallback in (
            ("error_color", _TDOC_LINT_VISUAL_DEFAULTS["error_color"]),
            ("warning_color", _TDOC_LINT_VISUAL_DEFAULTS["warning_color"]),
            ("info_color", _TDOC_LINT_VISUAL_DEFAULTS["info_color"]),
            ("hint_color", _TDOC_LINT_VISUAL_DEFAULTS["hint_color"]),
        ):
            color = QColor(str(merged.get(color_key) or "").strip())
            if not color.isValid():
                color = QColor(str(fallback))
            merged[color_key] = color.name(QColor.HexRgb) if color.isValid() else str(fallback)

        try:
            merged["squiggle_thickness"] = max(1, min(6, int(merged.get("squiggle_thickness", 2))))
        except Exception:
            merged["squiggle_thickness"] = 2
        try:
            merged["line_alpha"] = max(0, min(255, int(merged.get("line_alpha", 64))))
        except Exception:
            merged["line_alpha"] = 64

        if merged == self._lint_visual_cfg:
            return

        self._lint_visual_cfg = merged
        self._rebuild_lint_selections()
        self._rebuild_extra_selections()
        self.viewport().update()
        self._refresh_overview_marker_area()

    def set_editor_font_preferences(self, *, family: str | None = None, point_size: int | None = None) -> None:
        font = self.font()
        if isinstance(family, str) and family.strip():
            font.setFamily(family.strip())
        font.setStyleHint(QFont.StyleHint.Monospace)
        if point_size is not None:
            try:
                size = max(1, int(point_size))
            except Exception:
                size = int(font.pointSize()) if int(font.pointSize()) > 0 else 10
            font.setPointSize(size)
        self.setFont(font)
        self._restyle_collapsed_markdown_headings()

    def set_editor_indent_preferences(self, *, use_tabs: bool | None = None, indent_width: int | None = None) -> None:
        if indent_width is not None:
            try:
                self.indent_width = max(1, min(8, int(indent_width)))
            except Exception:
                self.indent_width = 4
        if use_tabs is not None:
            self.use_tabs = _coerce_bool(use_tabs, default=False)

    def _active_indent_width(self) -> int:
        try:
            return max(1, int(getattr(self, "indent_width", 4)))
        except Exception:
            return 4

    def _indent_unit(self) -> str:
        use_tabs = _coerce_bool(getattr(self, "use_tabs", False), default=False)
        return "\t" if use_tabs else (" " * self._active_indent_width())

    def _handle_editor_shortcut_fallback(self, event: QKeyEvent) -> bool:
        if self._event_matches_action_shortcut(event, "general", "action.find"):
            self.show_find_bar()
            return True
        if self._event_matches_action_shortcut(event, "general", "action.replace"):
            self.show_replace_bar()
            return True
        if self._event_matches_action_shortcut(event, "general", "action.trigger_completion"):
            self.request_manual_completion()
            return True
        if (
            self._event_matches_action_shortcut(event, "general", "action.ai_inline_assist")
            or self._event_matches_action_shortcut(event, "general", "action.ai_inline_assist_ctrl_alt_space")
            or self._event_matches_action_shortcut(event, "general", "action.ai_inline_assist_alt_space")
        ):
            self.aiAssistRequested.emit("manual")
            return True
        if event.key() == Qt.Key_F3:
            if event.modifiers() & Qt.ShiftModifier:
                self.search_previous()
            else:
                self.search_next()
            return True
        if event.key() == Qt.Key_Escape and self._search_bar.isVisible():
            self.hide_search_bar()
            return True
        return False

    def event(self, event):
        if event is not None and event.type() == QEvent.ShortcutOverride and isinstance(event, QKeyEvent):
            if self._handle_editor_shortcut_fallback(event):
                event.accept()
                return True
        return super().event(event)

    def request_manual_completion(self) -> None:
        self._completion_manual_request = True
        if not self.hasFocus():
            self.setFocus(Qt.ShortcutFocusReason)
        QTimer.singleShot(0, self._refresh_tdoc_completion_popup)

    def _is_tdoc_completion_popup_visible(self) -> bool:
        return bool(self._completion_popup.isVisible())

    def _hide_tdoc_completion_popup(self) -> None:
        self._completion_popup.hide()
        self._completion_popup.clear()
        self._completion_candidates = []

    def _schedule_tdoc_completion_refresh(self, immediate: bool = False) -> None:
        if immediate:
            self._completion_refresh_timer.stop()
            self._refresh_tdoc_completion_popup()
            return
        self._completion_refresh_timer.start()

    def _on_text_changed_tdoc_completion_refresh(self) -> None:
        self._completion_refresh_from_text_change = True
        self._schedule_tdoc_completion_refresh()

    def _on_cursor_position_changed_tdoc_completion_refresh(self) -> None:
        if self._is_tdoc_completion_popup_visible():
            self._schedule_tdoc_completion_refresh(immediate=True)

    def _position_tdoc_completion_popup(self) -> None:
        if not self._is_tdoc_completion_popup_visible():
            return
        row_h = max(20, self._completion_popup.sizeHintForRow(0), self.fontMetrics().height() + 6)
        max_rows = min(self._completion_popup.count(), 10)
        w = max(320, min(760, int(self.width() * 0.78)))

        cursor_rect = self.cursorRect()
        anchor_bottom = self.viewport().mapTo(self, cursor_rect.bottomLeft())
        anchor_top = self.viewport().mapTo(self, cursor_rect.topLeft())
        below_top = int(anchor_bottom.y() + 2)
        available_below = max(0, self.height() - below_top)
        available_above = max(0, int(anchor_top.y()) - 2)

        desired_h = max_rows * row_h + 6
        min_h = row_h * min(3, max_rows) + 6
        place_above = available_above > available_below
        available_primary = available_above if place_above else available_below
        h = min(desired_h, max(min_h, available_primary))

        if place_above:
            y = max(0, int(anchor_top.y()) - h - 2)
        else:
            y = below_top
        x = int(anchor_bottom.x())
        if x + w > self.width():
            x = max(0, self.width() - w - 2)

        self._completion_popup.setGeometry(x, y, w, h)

    def _move_tdoc_completion_selection(self, delta: int) -> None:
        if not self._is_tdoc_completion_popup_visible():
            return
        count = self._completion_popup.count()
        if count <= 0:
            return
        row = self._completion_popup.currentRow()
        if row < 0:
            row = 0
        row = (row + int(delta)) % count
        self._completion_popup.setCurrentRow(row)

    def _frontmatter_block_bounds(self) -> tuple[int, int] | None:
        doc = self.document()
        if doc is None:
            return None
        start_block = doc.findBlockByNumber(0)
        if not start_block.isValid():
            return None

        # Tolerate UTF-8 BOM and optional leading blank lines before frontmatter.
        scanned = 0
        while start_block.isValid() and scanned < 64:
            first_line = str(self._serialize_block_to_tdoc(start_block) or "")
            normalized = first_line.lstrip("\ufeff").strip()
            if not normalized:
                start_block = start_block.next()
                scanned += 1
                continue
            if normalized != "---":
                return None
            break
        if not start_block.isValid():
            return None

        start_no = int(start_block.blockNumber())
        block = start_block.next()
        scanned = 0
        while block.isValid() and scanned < 512:
            raw = str(self._serialize_block_to_tdoc(block) or "")
            if raw.lstrip("\ufeff").strip() == "---":
                return start_no, int(block.blockNumber())
            block = block.next()
            scanned += 1
        return None

    def _frontmatter_existing_keys(self, *, exclude_block_no: int | None = None) -> set[str]:
        bounds = self._frontmatter_block_bounds()
        if not bounds:
            return set()
        start_no, end_no = bounds
        keys: set[str] = set()
        doc = self.document()
        block = doc.findBlockByNumber(start_no + 1)
        while block.isValid():
            block_no = int(block.blockNumber())
            if block_no >= end_no:
                break
            if exclude_block_no is not None and block_no == int(exclude_block_no):
                block = block.next()
                continue
            raw = str(self._serialize_block_to_tdoc(block) or "").strip()
            if not raw or raw.startswith("#"):
                block = block.next()
                continue
            match = FRONTMATTER_KV_PATTERN.match(raw)
            if match:
                key = str(match.group("key") or "").strip().lower()
                if key:
                    keys.add(key)
            block = block.next()
        return keys

    def _frontmatter_completion_context(self, cursor: QTextCursor) -> dict | None:
        bounds = self._frontmatter_block_bounds()
        if not bounds:
            return None
        start_no, end_no = bounds
        block = cursor.block()
        if not block.isValid():
            return None
        block_no = int(block.blockNumber())
        if block_no <= start_no or block_no >= end_no:
            return None

        line = str(block.text() or "")
        col = int(cursor.positionInBlock())
        if col < 0 or col > len(line):
            return None

        stripped = line.strip()
        if stripped.startswith("#"):
            return None

        abs_block_pos = int(block.position())
        colon_idx = line.find(":")
        if colon_idx < 0 or col <= colon_idx:
            prefix = line[:col]
            leading = len(prefix) - len(prefix.lstrip(" \t"))
            query = str(prefix[leading:] or "").strip()
            target_start = abs_block_pos + leading
            replace_end = abs_block_pos + col
            line_key = str(line[:colon_idx] if colon_idx >= 0 else prefix).strip().lower()
            return {
                "mode": "frontmatter_key",
                "query": query,
                "target_start_abs": target_start,
                "replace_end_abs": replace_end,
                "line_has_colon": bool(colon_idx >= 0),
                "line_key": line_key,
                "existing_keys": sorted(self._frontmatter_existing_keys(exclude_block_no=block_no)),
            }

        key = str(line[:colon_idx] or "").strip().lower()
        value_start = colon_idx + 1
        while value_start < len(line) and line[value_start] in {" ", "\t"}:
            value_start += 1
        if col < value_start:
            col = value_start
        query = str(line[value_start:col] or "")
        return {
            "mode": "frontmatter_value",
            "query": query,
            "frontmatter_key": key,
            "target_start_abs": abs_block_pos + value_start,
            "replace_end_abs": abs_block_pos + col,
        }

    def _link_completion_context(self, cursor: QTextCursor) -> dict | None:
        block = cursor.block()
        if not block.isValid():
            return None
        block_text = str(block.text() or "")
        col = int(cursor.positionInBlock())
        if col < 0 or col > len(block_text):
            return None

        open_idx = block_text.rfind("[", 0, col)
        if open_idx < 0:
            return None
        if "]" in block_text[open_idx:col]:
            return None

        is_image = bool(open_idx > 0 and block_text[open_idx - 1] == "!")
        raw_body = block_text[open_idx + 1:col]
        if not raw_body:
            target = ""
            target_start_local = open_idx + 1
        else:
            if "\n" in raw_body or "\r" in raw_body or "[" in raw_body:
                return None
            pipe_idx = raw_body.find("|")
            if pipe_idx >= 0:
                target = raw_body[pipe_idx + 1:]
                target_start_local = open_idx + 1 + pipe_idx + 1
            else:
                target = raw_body
                target_start_local = open_idx + 1
        target = str(target or "").replace("\\", "/")
        target = target.replace('"', "").replace("'", "")
        if "#" in target:
            return None

        return {
            "mode": "link",
            "is_image": is_image,
            "path_only": bool(is_image or "/" in target),
            "query": target,
            "target_start_abs": int(block.position()) + int(target_start_local),
        }

    def _tdoc_completion_context(self) -> dict | None:
        cursor = self.textCursor()
        if cursor.hasSelection():
            return None

        link_ctx = self._link_completion_context(cursor)
        if isinstance(link_ctx, dict):
            return link_ctx
        return self._frontmatter_completion_context(cursor)

    def _symbol_completion_candidates(self, prefix: str) -> list[str]:
        provider = self.list_symbol_completion_candidates
        if not callable(provider):
            return []
        try:
            rows = provider()
        except Exception:
            rows = []
        out = []
        seen = set()
        needle = str(prefix or "").casefold()
        for raw in rows if isinstance(rows, list) else []:
            text = str(raw or "").strip()
            if not text:
                continue
            if needle and not text.casefold().startswith(needle):
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    def _path_completion_candidates(self, prefix: str, *, image_only: bool) -> list[str]:
        provider = self.list_path_completion_candidates
        if not callable(provider):
            return []
        rows = []
        try:
            rows = provider(prefix=prefix, image_only=bool(image_only))
        except TypeError:
            try:
                rows = provider(prefix, bool(image_only))
            except Exception:
                rows = []
        except Exception:
            rows = []
        out = []
        seen = set()
        for raw in rows if isinstance(rows, list) else []:
            text = str(raw or "").strip().replace("\\", "/")
            if not text:
                continue
            if image_only and not text.endswith("/") and Path(text).suffix.lower() not in _TDOC_IMAGE_SUFFIXES:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    def _frontmatter_key_completion_candidates(self, ctx: dict) -> list[str]:
        query = str(ctx.get("query") or "").strip().casefold()
        existing_raw = ctx.get("existing_keys")
        existing = {
            str(raw).strip().lower()
            for raw in (existing_raw if isinstance(existing_raw, list) else [])
            if str(raw or "").strip()
        }
        line_key = str(ctx.get("line_key") or "").strip().lower()
        has_colon = bool(ctx.get("line_has_colon", False))

        out: list[str] = []
        seen: set[str] = set()
        provider = self.list_frontmatter_completion_candidates
        provider_had_rows = False
        if callable(provider):
            provided = []
            try:
                provided = provider(
                    mode="key",
                    key="",
                    query=str(ctx.get("query") or ""),
                    existing_keys=sorted(existing),
                )
            except TypeError:
                try:
                    provided = provider("key", str(ctx.get("query") or ""))
                except Exception:
                    provided = []
            except Exception:
                provided = []
            for raw in provided if isinstance(provided, list) else []:
                clean = str(raw or "").strip().lower()
                if not clean:
                    continue
                provider_had_rows = True
                if query and not clean.startswith(query):
                    continue
                if clean in existing and clean != line_key:
                    continue
                if clean in seen:
                    continue
                seen.add(clean)
                out.append(clean if has_colon else f"{clean}: ")
        if not provider_had_rows:
            for key in _TDOC_FRONTMATTER_KEY_SUGGESTIONS:
                clean = str(key or "").strip().lower()
                if not clean:
                    continue
                if query and not clean.startswith(query):
                    continue
                if clean in existing and clean != line_key:
                    continue
                if clean in seen:
                    continue
                seen.add(clean)
                out.append(clean if has_colon else f"{clean}: ")
        if not out:
            for key in _TDOC_FRONTMATTER_KEY_SUGGESTIONS:
                clean = str(key or "").strip().lower()
                if not clean or clean in seen:
                    continue
                seen.add(clean)
                out.append(clean if has_colon else f"{clean}: ")
        return out

    def _frontmatter_value_completion_candidates(self, ctx: dict) -> list[str]:
        key = str(ctx.get("frontmatter_key") or "").strip().lower()
        query = str(ctx.get("query") or "").strip().casefold()
        values: list[str] = []
        provider = self.list_frontmatter_completion_candidates
        provider_had_rows = False
        if callable(provider):
            provided = []
            try:
                provided = provider(
                    mode="value",
                    key=key,
                    query=str(ctx.get("query") or ""),
                    existing_keys=[],
                )
            except TypeError:
                try:
                    provided = provider("value", key)
                except Exception:
                    provided = []
            except Exception:
                provided = []
            for raw in provided if isinstance(provided, list) else []:
                text = str(raw or "").strip()
                if text:
                    provider_had_rows = True
                    values.append(text)
        if not provider_had_rows:
            values.extend(_TDOC_FRONTMATTER_VALUE_SUGGESTIONS.get(key, []))
        if not values and key.endswith(("_enabled", "_visible", "_active")):
            values = ["true", "false"]
        if not values and key.endswith(("_count", "_limit")):
            values = ["1", "10", "100"]

        out: list[str] = []
        seen: set[str] = set()
        for raw in values:
            text = str(raw or "").strip()
            if not text:
                continue
            if query and not text.casefold().startswith(query):
                continue
            folded = text.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            out.append(text)
        if not out:
            for raw in _TDOC_FRONTMATTER_GENERIC_VALUE_SUGGESTIONS:
                text = str(raw or "").strip()
                if not text:
                    continue
                folded = text.casefold()
                if folded in seen:
                    continue
                seen.add(folded)
                out.append(text)
        return out

    def _collect_tdoc_completion_candidates(self, ctx: dict) -> list[dict]:
        mode = str(ctx.get("mode") or "link").strip().lower()
        query = str(ctx.get("query") or "")
        is_image = bool(ctx.get("is_image", False))
        path_only = bool(ctx.get("path_only", False))

        rows: list[dict] = []
        if mode == "frontmatter_key":
            for text in self._frontmatter_key_completion_candidates(ctx):
                rows.append({"insert": text, "kind": "frontmatter-key"})
        elif mode == "frontmatter_value":
            for text in self._frontmatter_value_completion_candidates(ctx):
                rows.append({"insert": text, "kind": "frontmatter-value"})
        else:
            if not is_image and not path_only:
                for text in self._symbol_completion_candidates(query):
                    rows.append({"insert": text, "kind": "symbol"})

            for text in self._path_completion_candidates(query, image_only=is_image):
                rows.append({"insert": text, "kind": "path"})

        deduped: list[dict] = []
        seen = set()
        for row in rows:
            insert = str(row.get("insert") or "").strip()
            if not insert:
                continue
            key = insert.casefold()
            if key in seen:
                continue
            seen.add(key)
            deduped.append({"insert": insert, "kind": str(row.get("kind") or "item")})
        return deduped[:240]

    def _refresh_tdoc_completion_popup(self) -> None:
        ctx = self._tdoc_completion_context()
        manual = bool(self._completion_manual_request)
        from_text_change = bool(self._completion_refresh_from_text_change)
        self._completion_manual_request = False
        self._completion_refresh_from_text_change = False
        if not isinstance(ctx, dict):
            self._hide_tdoc_completion_popup()
            return

        if not manual:
            if not bool(self._completion_auto_trigger):
                self._hide_tdoc_completion_popup()
                return
            mode = str(ctx.get("mode") or "link").strip().lower()
            query = str(ctx.get("query") or "")
            if mode == "frontmatter_value":
                should_auto_show = bool(from_text_change) or bool(query.strip())
            elif mode == "frontmatter_key":
                min_chars = max(1, int(self._completion_auto_min_chars))
                should_auto_show = len(query.strip()) >= min_chars
            else:
                path_only = bool(ctx.get("path_only", False))
                min_chars = max(1, int(self._completion_auto_min_chars))
                auto_len = len(query)
                if path_only and "/" in query:
                    auto_len = len(query.rsplit("/", 1)[-1])
                should_auto_show = auto_len >= min_chars
                if path_only and query.endswith("/") and from_text_change:
                    should_auto_show = True
            if not should_auto_show:
                self._hide_tdoc_completion_popup()
                return

        candidates = self._collect_tdoc_completion_candidates(ctx)
        if not candidates:
            self._hide_tdoc_completion_popup()
            return

        query = str(ctx.get("query") or "").strip().casefold()
        if not manual and len(candidates) == 1:
            only = str(candidates[0].get("insert") or "").strip().casefold()
            if only == query:
                self._hide_tdoc_completion_popup()
                return

        self._completion_popup.clear()
        self._completion_candidates = candidates
        for idx, candidate in enumerate(candidates):
            insert = str(candidate.get("insert") or "").strip()
            kind = str(candidate.get("kind") or "item")
            if kind == "symbol":
                kind_label = "symbol"
            elif kind == "path" and insert.endswith("/"):
                kind_label = "folder"
            elif kind == "path":
                kind_label = "file"
            elif kind == "frontmatter-key":
                kind_label = "fm key"
            elif kind == "frontmatter-value":
                kind_label = "fm value"
            else:
                kind_label = kind
            row = QListWidgetItem(insert)
            row.setData(
                _TDOC_COMPLETION_META_ROLE,
                {
                    "primary": insert,
                    "right": kind_label,
                    "kind": kind_label,
                },
            )
            row.setData(_TDOC_COMPLETION_INDEX_ROLE, idx)
            self._completion_popup.addItem(row)

        if self._completion_popup.count() <= 0:
            self._hide_tdoc_completion_popup()
            return
        self._completion_popup.setCurrentRow(0)
        self._position_tdoc_completion_popup()
        self._completion_popup.show()
        self._completion_popup.raise_()
        QTimer.singleShot(0, self._position_tdoc_completion_popup)
        QTimer.singleShot(16, self._position_tdoc_completion_popup)

    def _accept_tdoc_completion(self) -> bool:
        if not self._is_tdoc_completion_popup_visible():
            return False
        item = self._completion_popup.currentItem()
        if item is None:
            return False
        raw_idx = item.data(_TDOC_COMPLETION_INDEX_ROLE)
        try:
            idx = int(raw_idx)
        except Exception:
            idx = -1
        if idx < 0 or idx >= len(self._completion_candidates):
            return False
        candidate = self._completion_candidates[idx]
        insert_text = str(candidate.get("insert") or "")
        if not insert_text:
            return False

        ctx = self._tdoc_completion_context()
        if not isinstance(ctx, dict):
            self._hide_tdoc_completion_popup()
            return False
        start = int(ctx.get("target_start_abs") or 0)
        end = int(ctx.get("replace_end_abs") or self.textCursor().position())
        if end < start:
            self._hide_tdoc_completion_popup()
            return False

        was_internal = bool(self._is_internal_change)
        self._is_internal_change = True
        try:
            edit = QTextCursor(self.document())
            edit.beginEditBlock()
            edit.setPosition(start)
            edit.setPosition(end, QTextCursor.KeepAnchor)
            edit.removeSelectedText()
            edit.insertText(insert_text, QTextCharFormat())
            edit.endEditBlock()

            cur = self.textCursor()
            cur.setPosition(start + len(insert_text))
            self.setTextCursor(cur)
        finally:
            self._is_internal_change = was_internal

        reopen_for_path = bool(str(ctx.get("mode") or "link").strip().lower() == "link" and insert_text.endswith("/"))
        self._hide_tdoc_completion_popup()
        if reopen_for_path:
            self._schedule_tdoc_completion_refresh(immediate=True)
        return True

    def is_frontmatter_visible(self) -> bool:
        start_no = self._frontmatter_start_block_number()
        if start_no is None:
            return True
        return bool(start_no not in self._folded_starts)

    def set_frontmatter_visible(self, visible: bool) -> bool:
        desired = bool(visible)
        self._frontmatter_visible = desired
        changed = bool(desired != self.is_frontmatter_visible())
        self._apply_frontmatter_visibility()
        return changed

    def toggle_frontmatter_visibility(self) -> bool:
        self._frontmatter_visible = not bool(self.is_frontmatter_visible())
        self._apply_frontmatter_visibility()
        return bool(self.is_frontmatter_visible())

    def _on_text_changed_frontmatter_visibility(self) -> None:
        if bool(self._frontmatter_visible):
            return
        start_no = self._frontmatter_start_block_number()
        if start_no is None or start_no in self._folded_starts:
            return
        self._apply_frontmatter_visibility()

    def _apply_frontmatter_visibility(self) -> None:
        start_no = self._frontmatter_start_block_number()
        if start_no is None:
            return
        if self._fold_provider is not None and start_no not in self._fold_ranges:
            self._refresh_fold_ranges()
        if self._frontmatter_visible:
            self._folded_starts.discard(start_no)
        elif start_no in self._fold_ranges:
            self._folded_starts.add(start_no)
        self._apply_fold_visibility()

    def _search_query(self) -> str:
        return str(self._search_bar.find_edit.text() or "")

    def _replace_query(self) -> str:
        return str(self._search_bar.replace_edit.text() or "")

    def _theme_search_top_margin_min(self) -> int:
        app = QApplication.instance()
        value = app.property(_THEME_EDITOR_SEARCH_TOP_MARGIN_PROP) if app is not None else None
        return _coerce_theme_px(
            value,
            default=_THEME_EDITOR_SEARCH_TOP_MARGIN_DEFAULT,
            minimum=0,
        )

    def _theme_overview_gap(self) -> int:
        app = QApplication.instance()
        value = app.property(_THEME_EDITOR_OVERVIEW_GAP_PROP) if app is not None else None
        return _coerce_theme_px(
            value,
            default=_THEME_EDITOR_OVERVIEW_GAP_DEFAULT,
            minimum=0,
        )

    def _search_top_margin(self) -> int:
        if not self._search_bar.isVisible():
            return 0
        return max(self._theme_search_top_margin_min(), int(self._search_bar.sizeHint().height()))

    def overviewMarkerAreaWidth(self) -> int:
        if not bool(self._overview_cfg.get("enabled", True)):
            return 0
        try:
            return max(6, int(self._overview_cfg.get("width", 10)))
        except Exception:
            return 10

    def _frontmatter_start_block_number(self) -> int | None:
        bounds = self._frontmatter_block_bounds()
        if not bounds:
            return None
        return int(bounds[0])

    def _apply_fold_provider(self) -> None:
        self._fold_provider = get_fold_provider("tdoc")
        if self._fold_provider is None:
            self._clear_folding()
            self.updateLineNumberAreaWidth(0)
            self._refresh_line_number_area()
            return
        self._schedule_fold_refresh(immediate=True)

    def _clear_folding(self) -> None:
        self._fold_refresh_timer.stop()
        self._fold_ranges = {}
        self._folded_starts = set()
        self._set_all_blocks_visible()
        self._refresh_fold_layout()

    def _schedule_fold_refresh(self, immediate: bool = False) -> None:
        if self._fold_provider is None:
            return
        if immediate:
            self._fold_refresh_timer.stop()
            self._refresh_fold_ranges()
            return
        self._fold_refresh_timer.start()

    def _refresh_fold_ranges(self) -> None:
        provider = self._fold_provider
        if provider is None:
            self._clear_folding()
            return
        try:
            source_text = self.save_tdoc()
        except Exception:
            source_text = self.toPlainText()
        try:
            raw_ranges = provider(source_text)
        except Exception:
            raw_ranges = []

        line_count = max(1, int(self.document().blockCount()))
        normalized = normalize_fold_ranges(list(raw_ranges or []), line_count)
        fold_ranges: dict[int, int] = {}
        for start_line, end_line in normalized:
            start_block = int(start_line) - 1
            end_block = int(end_line) - 1
            if end_block <= start_block:
                continue
            prev = fold_ranges.get(start_block)
            if prev is None or end_block > prev:
                fold_ranges[start_block] = end_block
        self._fold_ranges = fold_ranges
        self._folded_starts = {line for line in self._folded_starts if line in self._fold_ranges}

        frontmatter_start = self._frontmatter_start_block_number()
        if frontmatter_start is not None and frontmatter_start in self._fold_ranges:
            if self._frontmatter_visible:
                self._folded_starts.discard(frontmatter_start)
            else:
                self._folded_starts.add(frontmatter_start)
        self._apply_fold_visibility()

    def _set_all_blocks_visible(self) -> None:
        block = self.document().firstBlock()
        while block.isValid():
            if not bool(block.isVisible()):
                block.setVisible(True)
            if int(block.lineCount()) <= 0:
                block.setLineCount(1)
            block = block.next()

    def _apply_fold_visibility(self) -> None:
        doc = self.document()
        if doc is None:
            return
        was_modified = bool(doc.isModified())
        self._set_all_blocks_visible()
        for start_block in sorted(self._folded_starts):
            end_block = self._fold_ranges.get(int(start_block))
            if end_block is None or int(end_block) <= int(start_block):
                continue
            block = doc.findBlockByNumber(int(start_block)).next()
            while block.isValid() and int(block.blockNumber()) <= int(end_block):
                block.setVisible(False)
                block.setLineCount(0)
                block = block.next()
        self._refresh_fold_layout(was_modified=was_modified)

    def _refresh_fold_layout(self, *, was_modified: bool | None = None) -> None:
        doc = self.document()
        if doc is None:
            return
        try:
            doc.markContentsDirty(0, max(1, int(doc.characterCount())))
        except Exception:
            pass
        if was_modified is not None:
            self._restore_document_modified_state(bool(was_modified))
        self._refresh_line_number_area()
        self._refresh_overview_marker_area()
        self._apply_viewport_margins()
        self.viewport().update()

    def _toggle_fold_at_block(self, block_number: int) -> bool:
        block_no = int(block_number)
        if block_no not in self._fold_ranges:
            return False
        if block_no in self._folded_starts:
            self._folded_starts.discard(block_no)
        else:
            self._folded_starts.add(block_no)
        frontmatter_start = self._frontmatter_start_block_number()
        if frontmatter_start is not None and block_no == int(frontmatter_start):
            self._frontmatter_visible = block_no not in self._folded_starts
        self._apply_fold_visibility()
        return True

    def _block_number_at_y(self, y: int) -> int:
        block = self.document().firstBlock()
        probe_y = int(y)
        while block.isValid():
            if not bool(block.isVisible()):
                block = block.next()
                continue
            block_rect = self.cursorRect(QTextCursor(block))
            top = int(block_rect.top())
            bottom = int(block_rect.bottom())
            if top <= probe_y <= bottom:
                return int(block.blockNumber())
            if top > probe_y:
                break
            block = block.next()
        return -1

    def lineNumberAreaWidth(self) -> int:
        fold_gutter = self._frontmatter_fold_gutter_width if self._fold_provider is not None else 0
        digits = 1
        max_lines = max(1, int(self.document().blockCount()))
        while max_lines >= 10:
            max_lines //= 10
            digits += 1
        return fold_gutter + 8 + self.fontMetrics().horizontalAdvance("9") * digits + 6

    def _frontmatter_fold_marker_rect(self, top: int, line_height: int) -> QRect:
        gutter_width = max(0, int(self._frontmatter_fold_gutter_width))
        size = max(7, min(10, max(7, int(line_height) - 4)))
        x = max(1, int((gutter_width - size) / 2))
        y = int(top) + max(0, int((line_height - size) / 2))
        return QRect(x, y, size, size)

    def updateLineNumberAreaWidth(self, _value: int = 0) -> None:
        self._apply_viewport_margins()
        self._refresh_line_number_area()

    def _position_line_number_area(self) -> None:
        if not hasattr(self, "lineNumberArea") or not isinstance(self.lineNumberArea, QWidget):
            return
        width = self.lineNumberAreaWidth()
        if width <= 0:
            self.lineNumberArea.hide()
            return
        vp = self.viewport().geometry()
        self.lineNumberArea.setGeometry(
            QRect(
                max(0, vp.left() - width),
                vp.top(),
                width,
                max(0, vp.height()),
            )
        )
        self.lineNumberArea.show()
        self.lineNumberArea.raise_()

    def _refresh_line_number_area(self) -> None:
        if hasattr(self, "lineNumberArea") and isinstance(self.lineNumberArea, QWidget):
            self.lineNumberArea.update()

    def lineNumberAreaPaintEvent(self, event) -> None:
        if not hasattr(self, "lineNumberArea") or not isinstance(self.lineNumberArea, QWidget):
            return

        painter = QPainter(self.lineNumberArea)
        gutter = self._resolved_gutter_background_color()
        painter.fillRect(event.rect(), gutter)

        current_block_no = int(self.textCursor().blockNumber())
        rect = event.rect()
        fold_enabled = bool(self._fold_provider is not None)
        fold_gutter = self._frontmatter_fold_gutter_width if fold_enabled else 0
        number_left = max(0, int(fold_gutter))
        number_right = max(0, self.lineNumberArea.width() - number_left - 4)
        block = self.document().firstBlock()

        while block.isValid():
            if not bool(block.isVisible()):
                block = block.next()
                continue
            block_cursor = QTextCursor(block)
            block_rect = self.cursorRect(block_cursor)
            top = int(block_rect.top())
            height = max(1, int(block_rect.height()))
            if top > rect.bottom():
                break
            if top + height >= rect.top():
                number_color = self._resolved_gutter_number_color(
                    gutter,
                    active=bool(int(block.blockNumber()) == current_block_no),
                )
                painter.setPen(number_color)
                painter.drawText(
                    number_left,
                    top,
                    number_right,
                    height,
                    Qt.AlignRight | Qt.AlignVCenter,
                    str(int(block.blockNumber()) + 1),
                )
                block_no = int(block.blockNumber())
                if fold_enabled and block_no in self._fold_ranges:
                    marker_rect = self._frontmatter_fold_marker_rect(top, height)
                    marker_color = self._resolved_gutter_fold_marker_color(number_color)
                    path = QPainterPath()
                    if block_no in self._folded_starts:
                        path.moveTo(marker_rect.left(), marker_rect.top())
                        path.lineTo(marker_rect.left(), marker_rect.bottom())
                        path.lineTo(marker_rect.right(), marker_rect.center().y())
                    else:
                        path.moveTo(marker_rect.left(), marker_rect.top())
                        path.lineTo(marker_rect.right(), marker_rect.top())
                        path.lineTo(marker_rect.center().x(), marker_rect.bottom())
                    path.closeSubpath()
                    painter.save()
                    painter.setRenderHint(QPainter.Antialiasing, True)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(marker_color)
                    painter.drawPath(path)
                    painter.restore()
            block = block.next()

    def lineNumberAreaMousePressEvent(self, event) -> None:
        button = getattr(event, "button", None)
        if not callable(button) or button() != Qt.LeftButton:
            event.ignore()
            return
        if self._fold_provider is None:
            event.ignore()
            return
        if hasattr(event, "position"):
            raw_pos = event.position()
            pos = raw_pos.toPoint() if hasattr(raw_pos, "toPoint") else QPoint(int(raw_pos.x()), int(raw_pos.y()))
        else:
            pos = event.pos()
        if int(pos.x()) > int(self._frontmatter_fold_gutter_width):
            event.ignore()
            return
        block_no = self._block_number_at_y(int(pos.y()))
        if block_no < 0 or block_no not in self._fold_ranges:
            event.ignore()
            return
        if self._toggle_fold_at_block(int(block_no)):
            event.accept()
            return
        event.ignore()

    def update_overview_marker_settings(self, overview_cfg: dict | None) -> None:
        cfg = overview_cfg if isinstance(overview_cfg, dict) else {}
        merged = dict(_TDOC_OVERVIEW_MARKER_DEFAULTS)
        for key in (
            "enabled",
            "width",
            "search_color",
            "search_active_color",
            "occurrence_color",
            "max_occurrence_matches",
            "max_occurrence_highlights",
            "occurrence_highlight_alpha",
        ):
            if key in cfg:
                merged[key] = cfg.get(key)

        merged["enabled"] = _coerce_bool(merged.get("enabled", True), default=True)
        try:
            merged["width"] = max(6, min(24, int(merged.get("width", 10))))
        except Exception:
            merged["width"] = 10

        for color_key, fallback in (
            ("search_color", _TDOC_OVERVIEW_MARKER_DEFAULTS["search_color"]),
            ("search_active_color", _TDOC_OVERVIEW_MARKER_DEFAULTS["search_active_color"]),
            ("occurrence_color", _TDOC_OVERVIEW_MARKER_DEFAULTS["occurrence_color"]),
        ):
            color = QColor(str(merged.get(color_key) or "").strip())
            if not color.isValid():
                color = QColor(str(fallback))
            merged[color_key] = color.name(QColor.HexRgb) if color.isValid() else str(fallback)

        try:
            merged["max_occurrence_matches"] = max(1000, min(200000, int(merged.get("max_occurrence_matches", 12000))))
        except Exception:
            merged["max_occurrence_matches"] = 12000
        try:
            merged["max_occurrence_highlights"] = max(
                0,
                min(20000, int(merged.get("max_occurrence_highlights", 3000))),
            )
        except Exception:
            merged["max_occurrence_highlights"] = 3000
        try:
            merged["occurrence_highlight_alpha"] = max(
                0,
                min(255, int(merged.get("occurrence_highlight_alpha", 88))),
            )
        except Exception:
            merged["occurrence_highlight_alpha"] = 88

        if merged == self._overview_cfg:
            return

        self._overview_cfg = merged
        self._apply_viewport_margins()
        if not bool(merged.get("enabled", True)):
            self._overview_occurrence_term = ""
            self._overview_occurrence_lines = set()
            self._occurrence_highlight_selections = []
            self._rebuild_extra_selections()
            self._refresh_overview_marker_area()
            return
        self._refresh_occurrence_markers()

    def set_overview_markers_enabled(self, enabled: bool) -> None:
        self.update_overview_marker_settings({"enabled": bool(enabled)})

    def _apply_viewport_margins(self):
        top_margin = self._search_top_margin()
        left_margin = self.lineNumberAreaWidth()
        right_margin = self.overviewMarkerAreaWidth()
        self.setViewportMargins(left_margin, top_margin, right_margin, 0)
        self._position_line_number_area()
        self._position_overview_marker_area()

    def _position_overview_marker_area(self):
        if not hasattr(self, "overviewMarkerArea") or not isinstance(self.overviewMarkerArea, QWidget):
            return
        width = self.overviewMarkerAreaWidth()
        if width <= 0:
            self.overviewMarkerArea.hide()
            return
        vp = self.viewport().geometry()
        self.overviewMarkerArea.setGeometry(
            QRect(
                vp.right() + self._theme_overview_gap(),
                vp.top(),
                width,
                max(0, vp.height()),
            )
        )
        self.overviewMarkerArea.show()
        self.overviewMarkerArea.raise_()

    def _on_scrollbar_range_changed(self, *_args):
        self._position_line_number_area()
        self._position_overview_marker_area()
        self._refresh_line_number_area()
        self._refresh_overview_marker_area()

    def _position_search_bar(self):
        if not self._search_bar.isVisible():
            return
        cr = self.contentsRect()
        h = self._search_top_margin()
        self._search_bar.setGeometry(cr.left(), cr.top(), cr.width(), h)
        self._search_bar.raise_()

    def show_find_bar(self):
        self._search_bar.set_replace_visible(False)
        if not self._search_bar.isVisible():
            selected = str(self.textCursor().selectedText() or "").replace("\u2029", "\n")
            if selected and "\n" not in selected:
                self._search_bar.find_edit.setText(selected)
            self._search_bar.show()
        self._apply_viewport_margins()
        self._position_search_bar()
        self._search_bar.focus_find()
        self._schedule_search_refresh(immediate=True)

    def show_replace_bar(self):
        self._search_bar.set_replace_visible(True)
        if not self._search_bar.isVisible():
            selected = str(self.textCursor().selectedText() or "").replace("\u2029", "\n")
            if selected and "\n" not in selected:
                self._search_bar.find_edit.setText(selected)
            self._search_bar.show()
        self._apply_viewport_margins()
        self._position_search_bar()
        self._search_bar.focus_find()
        self._schedule_search_refresh(immediate=True)

    def hide_search_bar(self):
        if not self._search_bar.isVisible():
            return
        self._search_refresh_timer.stop()
        self._search_bar.hide()
        self._search_matches = []
        self._search_current_index = -1
        self._search_highlight_selections = []
        self._search_active_selection = None
        self._overview_search_lines = set()
        self._overview_active_search_lines = set()
        self._search_last_error = ""
        self._search_selection_range = None
        self._search_bar.set_count_text("0 / 0")
        self._search_bar.set_in_selection_checked(False)
        self._apply_viewport_margins()
        self._rebuild_extra_selections()
        self._refresh_overview_marker_area()

    def _on_search_query_changed(self, _text: str):
        self._schedule_search_refresh(immediate=False)

    def _on_replace_query_changed(self, _text: str):
        pass

    def _on_search_option_changed(self, _value):
        if self._search_bar.selection_box.isChecked():
            cursor = self.textCursor()
            if cursor.hasSelection():
                self._search_selection_range = (cursor.selectionStart(), cursor.selectionEnd())
            else:
                self._search_selection_range = None
                self._search_bar.set_in_selection_checked(False)
        else:
            self._search_selection_range = None
        self._schedule_search_refresh(immediate=True)

    def _on_text_changed_search_refresh(self):
        if self._search_bar.isVisible():
            self._schedule_search_refresh(immediate=False)

    @staticmethod
    def _is_identifier_char(char: str) -> bool:
        return bool(char) and (char.isalnum() or char == "_")

    def _schedule_occurrence_marker_refresh(self):
        if not bool(self._overview_cfg.get("enabled", True)):
            return
        self._occurrence_refresh_timer.start()

    def _refresh_occurrence_markers(self):
        term, pattern, flags = self._occurrence_pattern_from_cursor()
        if not term or not pattern:
            self._overview_occurrence_term = ""
            self._overview_occurrence_lines = set()
            self._occurrence_highlight_selections = []
            self._rebuild_extra_selections()
            self._refresh_overview_marker_area()
            return

        source = self.toPlainText()
        if not source:
            self._overview_occurrence_term = ""
            self._overview_occurrence_lines = set()
            self._occurrence_highlight_selections = []
            self._rebuild_extra_selections()
            self._refresh_overview_marker_area()
            return

        try:
            max_matches = max(1000, int(self._overview_cfg.get("max_occurrence_matches", 12000)))
        except Exception:
            max_matches = 12000

        try:
            regex = re.compile(pattern, flags)
        except Exception:
            self._overview_occurrence_term = ""
            self._overview_occurrence_lines = set()
            self._occurrence_highlight_selections = []
            self._rebuild_extra_selections()
            self._refresh_overview_marker_area()
            return

        try:
            max_highlights = max(0, int(self._overview_cfg.get("max_occurrence_highlights", 3000)))
        except Exception:
            max_highlights = 3000
        highlight_color = QColor(str(self._overview_cfg.get("occurrence_color", "#66A86A")))
        if not highlight_color.isValid():
            highlight_color = QColor("#66A86A")
        try:
            highlight_alpha = int(self._overview_cfg.get("occurrence_highlight_alpha", 88))
        except Exception:
            highlight_alpha = 88
        highlight_color.setAlpha(max(16, min(255, highlight_alpha)))

        lines: set[int] = set()
        highlights: list[QTextEdit.ExtraSelection] = []
        count = 0
        for match in regex.finditer(source):
            start = int(match.start())
            end = int(match.end())
            if end <= start:
                continue
            lines.update(self._line_numbers_for_span(start, end))
            if len(highlights) < max_highlights:
                sel = QTextEdit.ExtraSelection()
                cur = QTextCursor(self.document())
                cur.setPosition(start)
                cur.setPosition(end, QTextCursor.KeepAnchor)
                sel.cursor = cur
                sel.format.setBackground(highlight_color)
                highlights.append(sel)
            count += 1
            if count >= max_matches:
                break

        self._overview_occurrence_term = term
        self._overview_occurrence_lines = lines
        self._occurrence_highlight_selections = highlights
        self._rebuild_extra_selections()
        self._refresh_overview_marker_area()

    def _occurrence_pattern_from_cursor(self) -> tuple[str, str, int]:
        cur = self.textCursor()
        selected = str(cur.selectedText() or "").replace("\u2029", "\n")
        if selected and "\n" not in selected:
            if len(selected) < 2:
                return "", "", 0
            # Explicit text selection should match the exact selected substring
            # (including partial identifier selections like "fil" in "filtered").
            return selected, re.escape(selected), 0

        token = self._identifier_token_under_cursor(cur)
        if len(token) < 2:
            return "", "", 0
        return token, rf"\b{re.escape(token)}\b", 0

    def _identifier_token_under_cursor(self, cursor: QTextCursor | None = None) -> str:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        block_text = cur.block().text()
        if not block_text:
            return ""

        col = int(cur.positionInBlock())
        if col >= len(block_text) and col > 0:
            col -= 1
        if col < 0:
            return ""
        if col < len(block_text) and not self._is_identifier_char(block_text[col]):
            if col > 0 and self._is_identifier_char(block_text[col - 1]):
                col -= 1
            else:
                return ""

        start = max(0, min(col, len(block_text)))
        while start > 0 and self._is_identifier_char(block_text[start - 1]):
            start -= 1
        end = start
        while end < len(block_text) and self._is_identifier_char(block_text[end]):
            end += 1
        token = block_text[start:end].strip()
        if not token:
            return ""
        return token

    def _line_numbers_for_span(self, start: int, end: int) -> set[int]:
        out: set[int] = set()
        s = max(0, int(start))
        e = max(s, int(end))
        if e <= s:
            return out
        first = self.document().findBlock(s)
        last = self.document().findBlock(max(s, e - 1))
        if not first.isValid() or not last.isValid():
            return out
        first_line = int(first.blockNumber() + 1)
        last_line = int(last.blockNumber() + 1)
        for line_no in range(first_line, last_line + 1):
            out.add(line_no)
        return out

    def _schedule_search_refresh(self, *, immediate: bool):
        if not self._search_bar.isVisible():
            return
        self._search_refresh_timer.stop()
        if immediate:
            self._refresh_search_matches()
            return
        self._search_refresh_timer.start()

    def _search_range(self, text_length: int) -> tuple[int, int]:
        if not self._search_bar.selection_box.isChecked() or self._search_selection_range is None:
            return 0, text_length
        start, end = self._search_selection_range
        start = max(0, min(int(start), text_length))
        end = max(0, min(int(end), text_length))
        if end <= start:
            return 0, text_length
        return start, end

    def _compile_search_pattern(self):
        query = self._search_query()
        if not query:
            return None
        flags = 0 if self._search_bar.case_box.isChecked() else re.IGNORECASE
        if self._search_bar.regex_box.isChecked():
            pattern = query
        else:
            pattern = re.escape(query)
        if self._search_bar.word_box.isChecked():
            pattern = rf"\b(?:{pattern})\b"
        try:
            return re.compile(pattern, flags)
        except re.error as exc:
            self._search_last_error = str(exc)
            return None

    def _refresh_search_matches(self):
        if not self._search_bar.isVisible():
            return
        self._search_last_error = ""
        query = self._search_query()
        if not query:
            self._search_matches = []
            self._search_current_index = -1
            self._search_highlight_selections = []
            self._search_active_selection = None
            self._overview_search_lines = set()
            self._overview_active_search_lines = set()
            self._search_bar.set_count_text("0 / 0")
            self._rebuild_extra_selections()
            self._refresh_overview_marker_area()
            return

        pattern = self._compile_search_pattern()
        if pattern is None:
            self._search_matches = []
            self._search_current_index = -1
            self._search_highlight_selections = []
            self._search_active_selection = None
            self._overview_search_lines = set()
            self._overview_active_search_lines = set()
            self._search_bar.set_count_text("0 / 0")
            self._rebuild_extra_selections()
            self._refresh_overview_marker_area()
            return

        source = self.toPlainText()
        start, end = self._search_range(len(source))
        segment = source[start:end]

        matches: list[tuple[int, int]] = []
        for match in pattern.finditer(segment):
            s = start + int(match.start())
            e = start + int(match.end())
            if e <= s:
                continue
            matches.append((s, e))
            if len(matches) >= 10000:
                break
        self._search_matches = matches
        self._refresh_search_marker_lines()
        self._refresh_search_current_index()

    def _search_index_for_cursor(self) -> int:
        if not self._search_matches:
            return -1
        cur = self.textCursor()
        if cur.hasSelection():
            ss = cur.selectionStart()
            se = cur.selectionEnd()
            for idx, (start, end) in enumerate(self._search_matches):
                if start == ss and end == se:
                    return idx
        pos = cur.position()
        best_idx = -1
        for idx, (start, end) in enumerate(self._search_matches):
            if start <= pos <= end:
                return idx
            if start < pos:
                best_idx = idx
            else:
                break
        return best_idx

    def _refresh_search_current_index(self):
        if not self._search_bar.isVisible():
            return
        self._search_current_index = self._search_index_for_cursor()

        hl: list[QTextEdit.ExtraSelection] = []
        for start, end in self._search_matches[:3000]:
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(self.document())
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.KeepAnchor)
            sel.cursor = cur
            sel.format.setBackground(QColor(74, 92, 126, 110))
            hl.append(sel)
        self._search_highlight_selections = hl

        self._search_active_selection = None
        self._overview_active_search_lines = set()
        if 0 <= self._search_current_index < len(self._search_matches):
            start, end = self._search_matches[self._search_current_index]
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(self.document())
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.KeepAnchor)
            sel.cursor = cur
            sel.format.setBackground(QColor(214, 168, 83, 170))
            self._search_active_selection = sel
            self._overview_active_search_lines = self._line_numbers_for_span(start, end)
            self._search_bar.set_count_text(f"{self._search_current_index + 1} / {len(self._search_matches)}")
        elif self._search_last_error:
            self._search_bar.set_count_text("0 / 0")
        else:
            self._search_bar.set_count_text(f"0 / {len(self._search_matches)}")

        self._rebuild_extra_selections()
        self._refresh_overview_marker_area()

    def _refresh_search_marker_lines(self):
        lines: set[int] = set()
        for start, end in self._search_matches[:5000]:
            lines.update(self._line_numbers_for_span(start, end))
        self._overview_search_lines = lines

    def _goto_search_index(self, idx: int):
        if idx < 0 or idx >= len(self._search_matches):
            return
        start, end = self._search_matches[idx]
        cur = self.textCursor()
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.KeepAnchor)
        self.setTextCursor(cur)
        self.ensureCursorVisible()
        self._search_current_index = idx
        self._refresh_search_current_index()

    def search_next(self):
        if not self._search_bar.isVisible():
            self.show_find_bar()
            return
        if not self._search_matches:
            self._refresh_search_matches()
            if not self._search_matches:
                return

        if self._search_current_index >= 0:
            idx = (self._search_current_index + 1) % len(self._search_matches)
            self._goto_search_index(idx)
            return

        pos = self.textCursor().position()
        for idx, (start, _end) in enumerate(self._search_matches):
            if start >= pos:
                self._goto_search_index(idx)
                return
        self._goto_search_index(0)

    def search_previous(self):
        if not self._search_bar.isVisible():
            self.show_find_bar()
            return
        if not self._search_matches:
            self._refresh_search_matches()
            if not self._search_matches:
                return

        if self._search_current_index >= 0:
            idx = (self._search_current_index - 1) % len(self._search_matches)
            self._goto_search_index(idx)
            return

        pos = self.textCursor().position()
        for idx in range(len(self._search_matches) - 1, -1, -1):
            start, _end = self._search_matches[idx]
            if start <= pos:
                self._goto_search_index(idx)
                return
        self._goto_search_index(len(self._search_matches) - 1)

    def _replacement_text_for_span(self, start: int, end: int) -> str:
        source = self.toPlainText()
        selected = source[start:end]
        if self._search_bar.regex_box.isChecked():
            pattern = self._compile_search_pattern()
            if pattern is not None:
                match = pattern.match(selected)
                if match is not None:
                    return str(match.expand(self._replace_query()))
        return self._replace_query()

    def replace_current_or_next(self):
        if not self._search_bar.isVisible():
            self.show_replace_bar()
            return
        if not self._search_matches:
            self._refresh_search_matches()
            if not self._search_matches:
                return

        idx = self._search_index_for_cursor()
        if idx < 0:
            pos = self.textCursor().position()
            idx = 0
            for i, (start, _end) in enumerate(self._search_matches):
                if start >= pos:
                    idx = i
                    break

        start, end = self._search_matches[idx]
        repl = self._replacement_text_for_span(start, end)
        cur = self.textCursor()
        cur.beginEditBlock()
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.KeepAnchor)
        cur.insertText(repl)
        cur.endEditBlock()
        self.setTextCursor(cur)
        self._schedule_search_refresh(immediate=True)
        self.search_next()

    def replace_all_matches(self):
        if not self._search_bar.isVisible():
            self.show_replace_bar()
            return
        if not self._search_matches:
            self._refresh_search_matches()
            if not self._search_matches:
                return

        cur = self.textCursor()
        cur.beginEditBlock()
        for start, end in reversed(self._search_matches):
            repl = self._replacement_text_for_span(start, end)
            span_cursor = QTextCursor(self.document())
            span_cursor.setPosition(start)
            span_cursor.setPosition(end, QTextCursor.KeepAnchor)
            span_cursor.insertText(repl)
        cur.endEditBlock()
        self._schedule_search_refresh(immediate=True)

    def set_lint_diagnostics(self, diagnostics: list[dict]):
        normalized: list[dict] = []
        line_severity: dict[int, str] = {}
        for item in diagnostics or []:
            if not isinstance(item, dict):
                continue
            try:
                line = int(item.get("line") or 0)
            except Exception:
                continue
            if line <= 0:
                continue
            sev = str(item.get("severity") or "warning").lower()
            row: dict[str, object] = {"line": max(1, line), "severity": sev}
            try:
                column = int(item.get("column") or 0)
            except Exception:
                column = 0
            if column > 0:
                row["column"] = max(1, column)
            try:
                end_line = int(item.get("end_line") or 0)
            except Exception:
                end_line = 0
            if end_line > 0:
                row["end_line"] = max(1, end_line)
            try:
                end_column = int(item.get("end_column") or item.get("end_col") or 0)
            except Exception:
                end_column = 0
            if end_column > 0:
                row["end_column"] = max(1, end_column)
            normalized.append(row)
            prev = line_severity.get(line)
            if prev is None or self._severity_rank(sev) > self._severity_rank(prev):
                line_severity[line] = sev

        self._lint_diagnostics = normalized
        self._lint_line_severity = line_severity
        self._rebuild_lint_selections()
        self._rebuild_extra_selections()
        self.viewport().update()
        self._refresh_overview_marker_area()

    def clear_lint_diagnostics(self):
        self._lint_diagnostics = []
        self._lint_line_severity = {}
        self._lint_selections = []
        self._rebuild_extra_selections()
        self.viewport().update()
        self._refresh_overview_marker_area()

    def update_spellcheck_visual_settings(self, cfg: dict | None) -> None:
        payload = cfg if isinstance(cfg, dict) else {}
        merged: dict[str, object] = {
            "enabled": bool(payload.get("enabled", False)),
            "color": "#66C07A",
        }
        color = QColor(str(payload.get("color") or "#66C07A").strip())
        if color.isValid():
            merged["color"] = color.name(QColor.HexRgb)
        if merged == self._spellcheck_visual_cfg:
            return
        self._spellcheck_visual_cfg = merged
        self._rebuild_spellcheck_selections()
        self._rebuild_extra_selections()
        self.viewport().update()

    def set_spellcheck_diagnostics(self, diagnostics: list[dict] | None) -> None:
        normalized: list[dict[str, int]] = []
        doc_len = len(self.toPlainText())
        for item in diagnostics or []:
            if not isinstance(item, dict):
                continue
            try:
                start = int(item.get("start", -1))
                end = int(item.get("end", -1))
            except Exception:
                continue
            if start < 0 or end <= start:
                continue
            start = max(0, min(start, doc_len))
            end = max(start + 1, min(end, doc_len))
            normalized.append({"start": start, "end": end})
            if len(normalized) >= 3000:
                break
        self._spellcheck_diagnostics = normalized
        self._rebuild_spellcheck_selections()
        self._rebuild_extra_selections()
        self.viewport().update()

    def clear_spellcheck_diagnostics(self) -> None:
        self._spellcheck_diagnostics = []
        self._spellcheck_selections = []
        self._rebuild_extra_selections()
        self.viewport().update()

    def _severity_rank(self, severity: str) -> int:
        if severity == "error":
            return 3
        if severity == "warning":
            return 2
        return 1

    def _document_position_for_line_column(self, line: int, column: int) -> int:
        block = self.document().findBlockByNumber(max(0, int(line) - 1))
        if not block.isValid():
            return -1
        text = block.text()
        col0 = max(0, int(column) - 1)
        col0 = min(col0, len(text))
        return int(block.position() + col0)

    def _lint_underline_color(self, severity: str) -> QColor:
        return QColor(self._lint_color_hex_for_severity(severity))

    def _lint_line_background_color(self, severity: str) -> QColor:
        color = QColor(self._lint_color_hex_for_severity(severity))
        try:
            alpha = int(self._lint_visual_cfg.get("line_alpha", 64))
        except Exception:
            alpha = 64
        color.setAlpha(max(0, min(255, alpha)))
        return color

    def _lint_color_hex_for_severity(self, severity: str) -> str:
        sev = str(severity or "").lower()
        if sev == "error":
            return str(self._lint_visual_cfg.get("error_color") or _TDOC_LINT_VISUAL_DEFAULTS["error_color"])
        if sev == "warning":
            return str(self._lint_visual_cfg.get("warning_color") or _TDOC_LINT_VISUAL_DEFAULTS["warning_color"])
        if sev == "hint":
            return str(self._lint_visual_cfg.get("hint_color") or _TDOC_LINT_VISUAL_DEFAULTS["hint_color"])
        return str(self._lint_visual_cfg.get("info_color") or _TDOC_LINT_VISUAL_DEFAULTS["info_color"])

    def _lint_visual_mode(self) -> str:
        mode = str(self._lint_visual_cfg.get("mode") or "squiggle").strip().lower()
        if mode not in {"squiggle", "line", "both"}:
            return "squiggle"
        return mode

    def set_change_region_colors(
        self,
        *,
        dirty_background: str | QColor = DEFAULT_EDITOR_DIRTY_BACKGROUND_HEX,
        uncommitted_background: str | QColor = DEFAULT_EDITOR_UNCOMMITTED_BACKGROUND_HEX,
    ) -> None:
        dirty = parse_editor_overlay_color(dirty_background, DEFAULT_EDITOR_DIRTY_BACKGROUND_HEX)
        uncommitted = parse_editor_overlay_color(uncommitted_background, DEFAULT_EDITOR_UNCOMMITTED_BACKGROUND_HEX)
        if (
            dirty == self._change_region_dirty_color
            and uncommitted == self._change_region_uncommitted_color
        ):
            return
        self._change_region_dirty_color = dirty
        self._change_region_uncommitted_color = uncommitted
        self._rebuild_change_region_selections()
        self._rebuild_extra_selections()

    def set_change_region_highlights(
        self,
        *,
        dirty_lines: set[int] | list[int] | tuple[int, ...] | None = None,
        uncommitted_lines: set[int] | list[int] | tuple[int, ...] | None = None,
        dirty_background: str | QColor | None = None,
        uncommitted_background: str | QColor | None = None,
    ) -> None:
        next_dirty = normalize_line_numbers(dirty_lines)
        next_uncommitted = normalize_line_numbers(uncommitted_lines)

        if dirty_background is not None or uncommitted_background is not None:
            self.set_change_region_colors(
                dirty_background=(
                    dirty_background
                    if dirty_background is not None
                    else self._change_region_dirty_color
                ),
                uncommitted_background=(
                    uncommitted_background
                    if uncommitted_background is not None
                    else self._change_region_uncommitted_color
                ),
            )

        if (
            next_dirty == self._change_region_dirty_lines
            and next_uncommitted == self._change_region_uncommitted_lines
        ):
            return

        self._change_region_dirty_lines = next_dirty
        self._change_region_uncommitted_lines = next_uncommitted
        self._rebuild_change_region_selections()
        self._rebuild_extra_selections()

    def clear_change_region_highlights(self) -> None:
        if not self._change_region_dirty_lines and not self._change_region_uncommitted_lines:
            return
        self._change_region_dirty_lines = set()
        self._change_region_uncommitted_lines = set()
        self._change_region_selections = []
        self._refresh_overview_change_region_lines()
        self._rebuild_extra_selections()
        self._refresh_overview_marker_area()

    def change_region_layer_for_line(self, line_number: int) -> str:
        return resolve_change_region_layer(
            int(line_number),
            dirty_lines=self._change_region_dirty_lines,
            uncommitted_lines=self._change_region_uncommitted_lines,
        )

    def _rebuild_change_region_selections(self) -> None:
        self._change_region_selections = build_change_region_selections(
            self.document(),
            dirty_lines=self._change_region_dirty_lines,
            uncommitted_lines=self._change_region_uncommitted_lines,
            dirty_color=self._change_region_dirty_color,
            uncommitted_color=self._change_region_uncommitted_color,
        )
        self._refresh_overview_change_region_lines()
        self._refresh_overview_marker_area()

    def _refresh_overview_change_region_lines(self) -> None:
        dirty = {max(1, int(line)) for line in self._change_region_dirty_lines}
        uncommitted: set[int] = set()
        for line in self._change_region_uncommitted_lines:
            line_number = max(1, int(line))
            resolved = resolve_change_region_layer(
                line_number,
                dirty_lines=dirty,
                uncommitted_lines=self._change_region_uncommitted_lines,
            )
            if resolved == "uncommitted":
                uncommitted.add(line_number)
        self._overview_change_region_dirty_lines = dirty
        self._overview_change_region_uncommitted_lines = uncommitted

    def _overview_change_region_lines_for_layer(self, layer: str) -> set[int]:
        name = str(layer or "").strip().lower()
        if name == "dirty":
            return set(self._overview_change_region_dirty_lines)
        if name != "uncommitted":
            return set()
        return set(self._overview_change_region_uncommitted_lines)

    def _rebuild_lint_selections(self):
        selections: list[QTextEdit.ExtraSelection] = []
        if self._lint_visual_mode() in {"line", "both"}:
            for line, severity in sorted(self._lint_line_severity.items()):
                block = self.document().findBlockByNumber(line - 1)
                if not block.isValid():
                    continue
                sel = QTextEdit.ExtraSelection()
                cursor = QTextCursor(block)
                cursor.clearSelection()
                sel.cursor = cursor
                sel.format.setProperty(QTextFormat.FullWidthSelection, True)
                sel.format.setBackground(self._lint_line_background_color(severity))
                selections.append(sel)
        self._lint_selections = selections

    def _paint_lint_squiggles(self, event) -> None:
        if self._lint_visual_mode() not in {"squiggle", "both"}:
            return
        if not self._lint_diagnostics:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipRect(event.rect())

        try:
            thickness = max(1, min(6, int(self._lint_visual_cfg.get("squiggle_thickness", 2))))
        except Exception:
            thickness = 2
        amplitude = 1.4 + (float(thickness) * 0.32)
        step = 3.8

        for diag in self._lint_diagnostics:
            if not isinstance(diag, dict):
                continue
            severity = str(diag.get("severity") or "warning").lower()
            pen = QPen(self._lint_underline_color(severity))
            pen.setWidth(max(1, int(thickness)))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)

            try:
                start_line = max(1, int(diag.get("line") or 1))
                end_line = max(start_line, int(diag.get("end_line") or start_line))
            except Exception:
                continue

            has_column = "column" in diag and diag.get("column") is not None
            has_end_column = "end_column" in diag and diag.get("end_column") is not None
            try:
                start_col = max(1, int(diag.get("column") or 1))
            except Exception:
                start_col = 1
            try:
                end_col = max(1, int(diag.get("end_column") or (start_col + 1)))
            except Exception:
                end_col = start_col + 1

            for line in range(start_line, end_line + 1):
                block = self.document().findBlockByNumber(line - 1)
                if not block.isValid():
                    continue
                block_cursor = QTextCursor(block)
                block_rect = self.cursorRect(block_cursor)
                if block_rect.bottom() < event.rect().top() or block_rect.top() > event.rect().bottom():
                    continue

                block_text = str(block.text() or "")
                block_len = len(block_text)
                if not has_column:
                    stripped = block_text.lstrip(" \t")
                    seg_start_col = (len(block_text) - len(stripped)) + 1
                    seg_end_col = block_len + 1
                else:
                    seg_start_col = start_col if line == start_line else 1
                    if line == end_line:
                        if has_end_column:
                            seg_end_col = end_col
                        else:
                            seg_end_col = start_col + 1
                    else:
                        seg_end_col = block_len + 1
                seg_start_col = max(1, min(seg_start_col, block_len + 1))
                seg_end_col = max(seg_start_col + 1, min(seg_end_col, block_len + 1))

                start_pos = self._document_position_for_line_column(line, seg_start_col)
                end_pos = self._document_position_for_line_column(line, seg_end_col)
                if start_pos < 0 or end_pos < 0:
                    continue

                start_cursor = QTextCursor(self.document())
                start_cursor.setPosition(start_pos)
                start_rect = self.cursorRect(start_cursor)
                x1 = float(start_rect.left())
                if end_pos > start_pos:
                    # Use the last covered character rectangle for x2 so line-end
                    # ranges don't collapse to the next-line cursor position.
                    end_cursor = QTextCursor(self.document())
                    end_cursor.setPosition(max(start_pos, end_pos - 1))
                    end_rect = self.cursorRect(end_cursor)
                    x2 = float(end_rect.right() + 1)
                else:
                    x2 = x1
                if x2 <= x1:
                    x2 = x1 + float(max(4, self.fontMetrics().horizontalAdvance(" ")))
                y = float(start_rect.bottom() - 1)
                self._draw_wave_segment(painter, x1, x2, y, amplitude=amplitude, step=step)

        painter.end()

    @staticmethod
    def _draw_wave_segment(painter: QPainter, x1: float, x2: float, y: float, *, amplitude: float, step: float) -> None:
        if x2 <= x1:
            return
        path = QPainterPath(QPointF(x1, y))
        x = float(x1)
        up = True
        while x < x2:
            nx = min(x2, x + step)
            mid = (x + nx) / 2.0
            if up:
                path.quadTo(QPointF(mid, y - amplitude), QPointF(nx, y))
            else:
                path.quadTo(QPointF(mid, y + amplitude), QPointF(nx, y))
            up = not up
            x = nx
        painter.drawPath(path)

    def _rebuild_spellcheck_selections(self) -> None:
        enabled = bool(self._spellcheck_visual_cfg.get("enabled", False))
        if not enabled:
            self._spellcheck_selections = []
            return
        color = QColor(str(self._spellcheck_visual_cfg.get("color") or "#66C07A"))
        if not color.isValid():
            color = QColor("#66C07A")
        selections: list[QTextEdit.ExtraSelection] = []
        for item in self._spellcheck_diagnostics:
            try:
                start = int(item.get("start", -1))
                end = int(item.get("end", -1))
            except Exception:
                continue
            if start < 0 or end <= start:
                continue
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(self.document())
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.KeepAnchor)
            sel.cursor = cur
            sel.format.setUnderlineStyle(QTextCharFormat.WaveUnderline)
            sel.format.setUnderlineColor(color)
            selections.append(sel)
        self._spellcheck_selections = selections

    def _rebuild_extra_selections(self):
        extra_selections = list(self._change_region_selections)
        extra_selections.extend(self._lint_selections)
        extra_selections.extend(self._spellcheck_selections)
        extra_selections.extend(self._occurrence_highlight_selections)
        extra_selections.extend(self._search_highlight_selections)
        if self._search_active_selection is not None:
            extra_selections.append(self._search_active_selection)
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            line_color = QColor(self._editor_background_color)
            if line_color.lightness() < 128:
                line_color = line_color.lighter(130)
            else:
                line_color = line_color.darker(112)
            line_color.setAlpha(140)
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def highlightCurrentLine(self):
        if self._search_bar.isVisible():
            self._refresh_search_current_index()
            return
        self._rebuild_extra_selections()

    def _overview_lint_lines_for_severity(self, severity: str) -> set[int]:
        sev = str(severity or "").lower()
        out: set[int] = set()
        for line, line_sev in self._lint_line_severity.items():
            if str(line_sev or "").lower() != sev:
                continue
            out.add(max(1, int(line)))
        return out

    def _refresh_overview_marker_area(self) -> None:
        if hasattr(self, "overviewMarkerArea") and isinstance(self.overviewMarkerArea, QWidget):
            self.overviewMarkerArea.update()

    def overviewMarkerAreaPaintEvent(self, event):
        if not hasattr(self, "overviewMarkerArea") or not isinstance(self.overviewMarkerArea, QWidget):
            return
        painter = QPainter(self.overviewMarkerArea)
        base = QColor(self._editor_background_color)
        if base.lightness() < 128:
            base = base.lighter(110)
        else:
            base = base.darker(106)
        base.setAlpha(210)
        painter.fillRect(event.rect(), base)
        border = QColor(base)
        border.setAlpha(255)
        painter.setPen(border)
        painter.drawLine(0, 0, 0, max(0, self.overviewMarkerArea.height() - 1))

        content_h = int(self.overviewMarkerArea.height())
        if content_h <= 0:
            return
        total_lines = max(1, int(self.document().blockCount()))
        marker_w = max(2, int(self.overviewMarkerArea.width()) - 2)
        x = 1
        self._paint_overview_line_set(
            painter,
            self._overview_change_region_lines_for_layer("uncommitted"),
            color=QColor(self._change_region_uncommitted_color),
            x=x,
            width=marker_w,
            total_lines=total_lines,
            content_h=content_h,
        )
        self._paint_overview_line_set(
            painter,
            self._overview_change_region_lines_for_layer("dirty"),
            color=QColor(self._change_region_dirty_color),
            x=x,
            width=marker_w,
            total_lines=total_lines,
            content_h=content_h,
        )
        self._paint_overview_line_set(
            painter,
            self._overview_occurrence_lines,
            color=QColor(str(self._overview_cfg.get("occurrence_color", "#66A86A"))),
            x=x,
            width=marker_w,
            total_lines=total_lines,
            content_h=content_h,
        )
        self._paint_overview_line_set(
            painter,
            self._overview_search_lines,
            color=QColor(str(self._overview_cfg.get("search_color", "#4A8FD8"))),
            x=x,
            width=marker_w,
            total_lines=total_lines,
            content_h=content_h,
        )
        self._paint_overview_line_set(
            painter,
            self._overview_active_search_lines,
            color=QColor(str(self._overview_cfg.get("search_active_color", "#D6A853"))),
            x=x,
            width=marker_w,
            total_lines=total_lines,
            content_h=content_h,
        )
        for severity in ("info", "hint", "warning", "error"):
            lines = self._overview_lint_lines_for_severity(severity)
            if not lines:
                continue
            self._paint_overview_line_set(
                painter,
                lines,
                color=QColor(self._lint_color_hex_for_severity(severity)),
                x=x,
                width=marker_w,
                total_lines=total_lines,
                content_h=content_h,
            )

    @staticmethod
    def _paint_overview_line_set(
        painter: QPainter,
        lines: set[int],
        *,
        color: QColor,
        x: int,
        width: int,
        total_lines: int,
        content_h: int,
    ) -> None:
        if not lines or total_lines <= 0 or content_h <= 0 or width <= 0:
            return
        if not color.isValid():
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        line_max = max(1, total_lines - 1)
        max_y = max(0, content_h - 2)
        for line in lines:
            ln = max(1, min(int(line), total_lines))
            ratio = 0.0 if line_max <= 0 else (float(ln - 1) / float(line_max))
            y = int(round(ratio * float(max_y)))
            painter.drawRect(x, y, width, 2)

    def overviewMarkerAreaMousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        h = max(1, int(self.overviewMarkerArea.height()))
        y = int(event.position().y()) if hasattr(event, "position") else int(event.pos().y())
        y = max(0, min(y, h - 1))
        line_count = max(1, int(self.document().blockCount()))
        ratio = float(y) / float(max(1, h - 1))
        line_no = int(round(ratio * float(max(0, line_count - 1)))) + 1
        block = self.document().findBlockByNumber(max(0, line_no - 1))
        if not block.isValid():
            event.ignore()
            return
        cursor = self.textCursor()
        cursor.setPosition(int(block.position()))
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        event.accept()

    def set_editor_background(
        self,
        *,
        background_color: str | QColor = "#252526",
        background_image_path: str = "",
        background_image_scale_mode: str = "stretch",
        background_image_brightness: int = 100,
        background_tint_color: str | QColor = "#000000",
        background_tint_strength: int = 0,
        gutter_background_color: str | QColor | None = None,
        gutter_foreground_color: str | QColor | None = None,
        gutter_active_foreground_color: str | QColor | None = None,
        gutter_fold_marker_color: str | QColor | None = None,
    ) -> None:
        base = self._resolve_background_color(background_color, "#252526")
        tint = self._resolve_background_color(background_tint_color, "#000000")
        gutter_bg = self._resolve_optional_color(gutter_background_color)
        gutter_fg = self._resolve_optional_color(gutter_foreground_color)
        gutter_fg_active = self._resolve_optional_color(gutter_active_foreground_color)
        gutter_fold = self._resolve_optional_color(gutter_fold_marker_color)
        scale_mode = str(background_image_scale_mode or "stretch").strip().lower()
        if scale_mode not in {"stretch", "fit_width", "fit_height", "tile"}:
            scale_mode = "stretch"
        brightness = max(0, min(200, int(background_image_brightness)))
        tint_strength = max(0, min(100, int(background_tint_strength)))
        image_path = str(background_image_path or "").strip()

        source_pixmap: QPixmap | None = None
        if image_path:
            candidate = Path(image_path).expanduser()
            if candidate.exists():
                loaded = QPixmap(str(candidate))
                if not loaded.isNull():
                    source_pixmap = loaded
                    image_path = str(candidate)
                else:
                    image_path = ""
            else:
                image_path = ""

        changed = (
            base != self._editor_background_color
            or tint != self._editor_background_tint_color
            or scale_mode != self._editor_background_scale_mode
            or brightness != self._editor_background_image_brightness
            or tint_strength != self._editor_background_tint_strength
            or image_path != self._editor_background_image_path
            or source_pixmap is not self._editor_background_source_pixmap
            or gutter_bg != self._gutter_background_color
            or gutter_fg != self._gutter_foreground_color
            or gutter_fg_active != self._gutter_active_foreground_color
            or gutter_fold != self._gutter_fold_marker_color
        )
        if not changed:
            return

        self._editor_background_color = base
        self._editor_background_tint_color = tint
        self._gutter_background_color = gutter_bg
        self._gutter_foreground_color = gutter_fg
        self._gutter_active_foreground_color = gutter_fg_active
        self._gutter_fold_marker_color = gutter_fold
        self._editor_background_scale_mode = scale_mode
        self._editor_background_image_brightness = brightness
        self._editor_background_tint_strength = tint_strength
        self._editor_background_image_path = image_path
        self._editor_background_source_pixmap = source_pixmap
        self._editor_background_cache_size = QSize()
        self._editor_background_cache_pixmap = None
        self._apply_editor_background_palette()
        self._rebuild_extra_selections()
        self._refresh_line_number_area()
        self._refresh_overview_marker_area()
        self.viewport().update()

    @staticmethod
    def _resolve_background_color(value: str | QColor, fallback: str) -> QColor:
        if isinstance(value, QColor):
            color = QColor(value)
        else:
            color = QColor(str(value or "").strip())
        if not color.isValid():
            color = QColor(fallback)
        return color

    @staticmethod
    def _resolve_optional_color(value: str | QColor | None) -> QColor:
        if value is None:
            return QColor()
        if isinstance(value, QColor):
            color = QColor(value)
        else:
            text = str(value or "").strip()
            if not text:
                return QColor()
            color = QColor(text)
        return color if color.isValid() else QColor()

    def _resolved_gutter_background_color(self) -> QColor:
        if self._gutter_background_color.isValid():
            return QColor(self._gutter_background_color)
        gutter = QColor(self._editor_background_color)
        if gutter.lightness() < 128:
            return gutter.darker(125)
        return gutter.darker(108)

    def _resolved_gutter_number_color(self, gutter: QColor, *, active: bool = False) -> QColor:
        if active and self._gutter_active_foreground_color.isValid():
            return QColor(self._gutter_active_foreground_color)
        if self._gutter_foreground_color.isValid():
            return QColor(self._gutter_foreground_color)
        number_color = QColor(gutter)
        if active:
            return number_color.lighter(205) if gutter.lightness() < 128 else number_color.darker(215)
        return number_color.lighter(145) if gutter.lightness() < 128 else number_color.darker(180)

    def _resolved_gutter_fold_marker_color(self, fallback: QColor) -> QColor:
        if self._gutter_fold_marker_color.isValid():
            return QColor(self._gutter_fold_marker_color)
        color = QColor(fallback)
        if not color.isValid():
            color = self._resolved_gutter_number_color(self._resolved_gutter_background_color(), active=True)
        return color.lighter(120) if self._resolved_gutter_background_color().lightness() < 128 else color.darker(115)

    def _build_editor_background_pixmap(self, size: QSize) -> QPixmap | None:
        source = self._editor_background_source_pixmap
        if source is None or source.isNull() or size.width() <= 0 or size.height() <= 0:
            return None

        pixmap = QPixmap(size)
        pixmap.fill(self._editor_background_color)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        mode = self._editor_background_scale_mode
        if mode == "tile":
            painter.drawTiledPixmap(pixmap.rect(), source)
        elif mode == "fit_width":
            scaled = source.scaledToWidth(size.width(), Qt.TransformationMode.SmoothTransformation)
            x = int((size.width() - scaled.width()) / 2)
            y = int((size.height() - scaled.height()) / 2)
            painter.drawPixmap(x, y, scaled)
        elif mode == "fit_height":
            scaled = source.scaledToHeight(size.height(), Qt.TransformationMode.SmoothTransformation)
            x = int((size.width() - scaled.width()) / 2)
            y = int((size.height() - scaled.height()) / 2)
            painter.drawPixmap(x, y, scaled)
        else:
            scaled = source.scaled(
                size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(0, 0, scaled)

        brightness = self._editor_background_image_brightness
        if brightness < 100:
            alpha = int(((100 - brightness) / 100.0) * 220)
            if alpha > 0:
                painter.fillRect(pixmap.rect(), QColor(0, 0, 0, min(alpha, 220)))
        elif brightness > 100:
            alpha = int(((brightness - 100) / 100.0) * 180)
            if alpha > 0:
                painter.fillRect(pixmap.rect(), QColor(255, 255, 255, min(alpha, 180)))

        tint_alpha = int((self._editor_background_tint_strength / 100.0) * 255.0)
        if tint_alpha > 0:
            tint = QColor(self._editor_background_tint_color)
            tint.setAlpha(max(0, min(255, tint_alpha)))
            painter.fillRect(pixmap.rect(), tint)

        painter.end()
        return pixmap

    def _background_texture_for_viewport(self) -> QPixmap | None:
        viewport_size = self.viewport().size() if self.viewport() is not None else QSize()
        if viewport_size.width() <= 0 or viewport_size.height() <= 0:
            return None
        if (
            self._editor_background_cache_pixmap is not None
            and self._editor_background_cache_size == viewport_size
        ):
            return self._editor_background_cache_pixmap
        self._editor_background_cache_size = QSize(viewport_size)
        self._editor_background_cache_pixmap = self._build_editor_background_pixmap(viewport_size)
        return self._editor_background_cache_pixmap

    def _apply_editor_background_palette(self) -> None:
        transparent = QColor(0, 0, 0, 0)
        palette = self.palette()
        palette.setColor(QPalette.Base, transparent)
        palette.setColor(QPalette.Window, transparent)
        self.setPalette(palette)
        self.setAutoFillBackground(False)

        viewport = self.viewport()
        if viewport is not None:
            vp = viewport.palette()
            vp.setColor(QPalette.Base, transparent)
            vp.setColor(QPalette.Window, transparent)
            viewport.setPalette(vp)
            viewport.setAutoFillBackground(False)

    def _paint_editor_background_layer(self, painter: QPainter, rect: QRect) -> None:
        target = self.viewport().rect() if self.viewport() is not None else QRect()
        if target.isEmpty():
            return
        painter.setClipRect(rect)
        texture = self._background_texture_for_viewport()
        if texture is not None:
            painter.drawPixmap(target.topLeft(), texture)
            return
        painter.fillRect(target, self._editor_background_color)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._editor_background_cache_size = QSize()
        self._editor_background_cache_pixmap = None
        self._apply_editor_background_palette()
        self._apply_viewport_margins()
        self._position_search_bar()
        self._position_line_number_area()
        self._position_overview_marker_area()
        self._refresh_line_number_area()
        self._refresh_overview_marker_area()
        self._position_tdoc_completion_popup()
        self._schedule_inline_image_rescale()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_inline_image_rescale(immediate=True)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if dx != 0 or dy != 0:
            self._refresh_line_number_area()
            self._refresh_overview_marker_area()
            self._position_tdoc_completion_popup()

    def paintEvent(self, event):
        background_painter = QPainter(self.viewport())
        self._paint_editor_background_layer(background_painter, event.rect())
        background_painter.end()
        super().paintEvent(event)
        self._paint_lint_squiggles(event)
        self._paint_inline_suggestion()

    def focusOutEvent(self, event):
        self._hide_tdoc_completion_popup()
        if self._hover_tooltip_target:
            QToolTip.hideText()
            self._hover_tooltip_target = ""
            self._hover_tooltip_text = ""
        self.clear_inline_suggestion()
        super().focusOutEvent(event)

    @staticmethod
    def _is_identifier_char(ch: str) -> bool:
        return bool(ch) and (ch.isalnum() or ch == "_")

    def completion_context(self) -> dict:
        cursor = self.textCursor()
        abs_pos = int(cursor.position())
        col = int(cursor.positionInBlock())
        line = int(cursor.blockNumber() + 1)
        text = self.toPlainText()

        start = abs_pos
        while start > 0 and self._is_identifier_char(text[start - 1]):
            start -= 1
        prefix = text[start:abs_pos]
        prev_char = text[abs_pos - 1] if abs_pos > 0 else ""
        return {
            "line": line,
            "column": col,
            "prefix": prefix,
            "prefix_start": int(start),
            "cursor_pos": abs_pos,
            "previous_char": prev_char,
        }

    def is_completion_popup_visible(self) -> bool:
        return False

    def clear_completion_ai_suggestion(self) -> None:
        self.clear_inline_suggestion()

    def set_completion_ai_suggestion(self, text: str) -> None:
        self.set_inline_suggestion(text)

    def has_inline_suggestion(self) -> bool:
        return bool(self._inline_suggestion_text)

    def clear_inline_suggestion(self) -> None:
        if not self._inline_suggestion_text:
            return
        self._inline_suggestion_text = ""
        self._inline_suggestion_anchor_pos = -1
        self._inline_suggestion_anchor_revision = -1
        self.viewport().update()

    def set_inline_suggestion(self, text: str) -> None:
        value = str(text or "").replace("\r", "")
        if "\n" not in value and "\\n" in value:
            value = value.replace("\\n", "\n")
        if not value.strip():
            self.clear_inline_suggestion()
            return
        self._inline_suggestion_text = value
        self._inline_suggestion_anchor_pos = int(self.textCursor().position())
        self._inline_suggestion_anchor_revision = int(self.document().revision())
        self.viewport().update()

    def accept_inline_suggestion(self) -> bool:
        if not self._inline_suggestion_text:
            return False
        text = self._dedupe_ai_suggestion_for_cursor(self._inline_suggestion_text)
        self.clear_inline_suggestion()
        if not text:
            return True
        cursor = self.textCursor()
        cursor.insertText(text)
        self.setTextCursor(cursor)
        return True

    def _dedupe_ai_suggestion_for_cursor(self, suggestion: str) -> str:
        text = str(suggestion or "").replace("\r", "")
        if not text:
            return ""
        cursor = self.textCursor()
        source = self.toPlainText()
        pos = int(cursor.position())
        if pos <= 0:
            return text

        left = source[max(0, pos - 400):pos]
        if not left:
            return text

        overlap = 0
        max_overlap = min(len(text), len(left), 160)
        for k in range(max_overlap, 0, -1):
            if left.endswith(text[:k]):
                overlap = k
                break
        if overlap > 0:
            text = text[overlap:]
        return text

    def _on_cursor_moved_inline_suggestion(self) -> None:
        if not self._inline_suggestion_text:
            return
        if int(self.textCursor().position()) != int(self._inline_suggestion_anchor_pos):
            self.clear_inline_suggestion()

    def _paint_inline_suggestion(self) -> None:
        if not self._inline_suggestion_text:
            return
        if self.is_completion_popup_visible():
            return
        if int(self.document().revision()) != int(self._inline_suggestion_anchor_revision):
            self.clear_inline_suggestion()
            return
        if int(self.textCursor().position()) != int(self._inline_suggestion_anchor_pos):
            return

        rect = self.cursorRect()
        fm = self.fontMetrics()
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(self.palette().color(QPalette.PlaceholderText))
        color.setAlpha(180)
        lines = self._inline_suggestion_text.split("\n")
        if not lines:
            return

        if len(lines) == 1:
            preview = lines[0]
            if not preview.strip():
                return
            y = int(rect.top() + max(0, (rect.height() - fm.height()) // 2) + fm.ascent())
            x = int(rect.left())
            max_w = max(8, self.viewport().width() - x - 8)
            text = fm.elidedText(preview, Qt.TextElideMode.ElideRight, max_w)
            painter.setPen(color)
            painter.drawText(x, y, text)
            return

        preview_lines = list(lines[:10])
        if len(lines) > 10:
            preview_lines[-1] = f"{preview_lines[-1]} ..."

        line_h = max(int(fm.lineSpacing()), int(rect.height()))
        pad_x = 6
        pad_y = 4
        x = max(0, int(rect.left()))
        max_panel_w = max(100, self.viewport().width() - x - 8)

        widest = 0
        for line in preview_lines:
            widest = max(widest, int(fm.horizontalAdvance(line)))
        panel_w = min(max_panel_w, widest + (pad_x * 2))
        text_w = max(20, panel_w - (pad_x * 2))
        draw_lines = [
            fm.elidedText(str(line or ""), Qt.TextElideMode.ElideRight, text_w)
            for line in preview_lines
        ]

        panel_h = (line_h * len(draw_lines)) + (pad_y * 2)
        y = int(rect.bottom() + 2)
        if y + panel_h > self.viewport().height():
            y = max(0, int(rect.top() - panel_h - 2))

        panel_rect = QRect(x, y, panel_w, panel_h)
        bg = QColor(self.palette().color(QPalette.Base))
        bg.setAlpha(224)
        border = QColor(self.palette().color(QPalette.Mid))
        border.setAlpha(200)

        painter.setPen(border)
        painter.setBrush(bg)
        painter.drawRoundedRect(panel_rect, 4, 4)

        painter.setPen(color)
        base_x = panel_rect.left() + pad_x
        base_y = panel_rect.top() + pad_y + fm.ascent()
        for idx, line in enumerate(draw_lines):
            painter.drawText(base_x, base_y + (idx * line_h), line)

    def _make_link_target(self, label):
        cleaned = link_effective_target(label)
        file_path, line_no = parse_file_link(cleaned)
        if file_path:
            if line_no:
                return f"file:{file_path}#L{line_no}"
            return f"file:{file_path}"

        symbol = cleaned
        if callable(self.resolve_symbol):
            symbol = self.resolve_symbol(cleaned)
        return f"symbol:{symbol}"

    def _resolve_image_target_path(self, target: str) -> str:
        text = str(target or "").strip()
        if not text:
            return ""
        if text.startswith("~") or os.path.isabs(text) or _WINDOWS_DRIVE_PATH_PATTERN.match(text):
            return ""
        resolver = self.resolve_image_path
        if callable(resolver):
            try:
                resolved = str(resolver(text) or "").strip()
            except Exception:
                resolved = ""
            return resolved
        return os.path.abspath(text)

    def _inline_image_max_width(self) -> int:
        vp = self.viewport()
        if vp is None:
            return 720
        width = int(vp.width())
        if width <= 0:
            return 720
        return max(96, width - 32)

    def _schedule_inline_image_rescale(self, *, immediate: bool = False) -> None:
        if immediate:
            self._inline_image_rescale_timer.start(0)
            return
        self._inline_image_rescale_timer.start()

    def _restore_document_modified_state(self, was_modified: bool) -> None:
        doc = self.document()
        if doc is None:
            return
        try:
            if bool(doc.isModified()) != bool(was_modified):
                doc.setModified(bool(was_modified))
        except Exception:
            pass

    def _rescale_inline_images_to_viewport(self) -> None:
        doc = self.document()
        if doc is None:
            return
        max_w = self._inline_image_max_width()
        if max_w <= 0:
            return

        updates: list[tuple[int, int, QTextImageFormat]] = []
        block = doc.begin()
        while block.isValid():
            iter_ = block.begin()
            while not iter_.atEnd():
                fragment = iter_.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    if fmt.isImageFormat():
                        image_fmt = fmt.toImageFormat()
                        name = str(image_fmt.name() or "").strip()
                        if name:
                            pixmap = QPixmap(name)
                            if not pixmap.isNull():
                                width = int(pixmap.width())
                                height = int(pixmap.height())
                                if width > 0 and height > 0:
                                    target_w = width
                                    target_h = height
                                    if target_w > max_w:
                                        scale = float(max_w) / float(target_w)
                                        target_w = max_w
                                        target_h = max(1, int(round(float(target_h) * scale)))
                                    current_w = int(round(float(image_fmt.width()))) if image_fmt.width() > 0 else 0
                                    current_h = int(round(float(image_fmt.height()))) if image_fmt.height() > 0 else 0
                                    if current_w != target_w or current_h != target_h:
                                        updated_fmt = QTextImageFormat(image_fmt)
                                        updated_fmt.setWidth(target_w)
                                        updated_fmt.setHeight(target_h)
                                        frag_start = int(fragment.position())
                                        frag_len = max(1, len(str(fragment.text() or "")))
                                        updates.append((frag_start, frag_start + frag_len, updated_fmt))
                iter_ += 1
            block = block.next()

        if not updates:
            return

        was_internal = bool(self._is_internal_change)
        was_undo_enabled = bool(self.isUndoRedoEnabled())
        was_blocked = bool(self.signalsBlocked())
        was_modified = bool(doc.isModified())
        self._is_internal_change = True
        if was_undo_enabled:
            self.setUndoRedoEnabled(False)
        if not was_blocked:
            self.blockSignals(True)
        try:
            edit = QTextCursor(doc)
            edit.beginEditBlock()
            for start, end, updated_fmt in updates:
                edit.setPosition(start)
                edit.setPosition(end, QTextCursor.KeepAnchor)
                edit.setCharFormat(updated_fmt)
            edit.endEditBlock()
            doc.setModified(was_modified)
        finally:
            if not was_blocked:
                self.blockSignals(False)
            if was_undo_enabled:
                self.setUndoRedoEnabled(True)
            self._is_internal_change = was_internal

    def _insert_inline_image_from_tag(self, cursor: QTextCursor, raw_body: str) -> bool:
        caption, rel_path = parse_image_components(raw_body)
        if not rel_path:
            return False
        abs_path = self._resolve_image_target_path(rel_path)
        if not abs_path:
            return False

        pixmap = QPixmap(abs_path)
        if pixmap.isNull():
            return False

        width = int(pixmap.width())
        height = int(pixmap.height())
        if width <= 0 or height <= 0:
            return False
        max_w = self._inline_image_max_width()
        if max_w > 0 and width > max_w:
            scale = float(max_w) / float(width)
            width = max_w
            height = max(1, int(round(float(height) * scale)))

        image_fmt = QTextImageFormat()
        image_fmt.setName(abs_path)
        image_fmt.setWidth(width)
        image_fmt.setHeight(height)
        normalized_raw = compose_image_components(caption, rel_path)
        image_fmt.setProperty(self.IMAGE_RAW_PROPERTY, normalized_raw)
        image_fmt.setProperty(self.IMAGE_PATH_PROPERTY, rel_path)
        cursor.insertImage(image_fmt)
        return True

    @staticmethod
    def _parse_markdown_heading_line(line: str) -> tuple[int, str, int] | None:
        match = MARKDOWN_HEADING_PATTERN.match(str(line or ""))
        if not match:
            return None
        title = str(match.group("title") or "").strip()
        if not title:
            return None
        hashes = str(match.group("hashes") or "")
        return len(hashes), title, int(match.start("title"))

    @staticmethod
    def _parse_markdown_bullet_line(line: str) -> tuple[str, str, int] | None:
        match = MARKDOWN_BULLET_PATTERN.match(str(line or ""))
        if not match:
            return None
        indent = str(match.group("indent") or "")
        body = str(match.group("body") or "")
        return indent, body, int(match.end("marker") + 1)

    def _heading_point_size_for_level(self, level: int) -> float:
        try:
            base = float(self.font().pointSizeF())
        except Exception:
            base = -1.0
        if base <= 0:
            try:
                base = float(self.font().pointSize())
            except Exception:
                base = 11.0
        if base <= 0:
            base = 11.0
        scale = {1: 1.60, 2: 1.35, 3: 1.18}.get(int(level), 1.0)
        return max(base, base * scale)

    def _make_markdown_heading_char_format(self, raw_heading: str, level: int) -> QTextCharFormat:
        lvl = max(1, min(3, int(level)))
        fmt = QTextCharFormat()
        heading_font = QFont(self.font())
        heading_font.setBold(True)
        heading_font.setPointSizeF(self._heading_point_size_for_level(lvl))
        fmt.setFont(heading_font)
        fmt.setProperty(self.HEADING_MARKDOWN_PROPERTY, True)
        fmt.setProperty(self.HEADING_LEVEL_PROPERTY, lvl)
        fmt.setProperty(self.HEADING_RAW_PROPERTY, str(raw_heading or ""))
        return fmt

    def _merge_markdown_heading_char_format(
        self,
        base_format: QTextCharFormat | None,
        *,
        raw_heading: str,
        level: int,
    ) -> QTextCharFormat:
        merged = QTextCharFormat(base_format) if isinstance(base_format, QTextCharFormat) else QTextCharFormat()
        heading = self._make_markdown_heading_char_format(raw_heading, level)
        merged.setFont(heading.font())
        merged.setProperty(self.HEADING_MARKDOWN_PROPERTY, heading.property(self.HEADING_MARKDOWN_PROPERTY))
        merged.setProperty(self.HEADING_LEVEL_PROPERTY, heading.property(self.HEADING_LEVEL_PROPERTY))
        merged.setProperty(self.HEADING_RAW_PROPERTY, heading.property(self.HEADING_RAW_PROPERTY))
        return merged

    def _make_markdown_bullet_char_format(self, raw_bullet: str) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setProperty(self.BULLET_MARKDOWN_PROPERTY, True)
        fmt.setProperty(self.BULLET_RAW_PROPERTY, str(raw_bullet or ""))
        return fmt

    def _merge_markdown_bullet_char_format(
        self,
        base_format: QTextCharFormat | None,
        *,
        raw_bullet: str,
    ) -> QTextCharFormat:
        merged = QTextCharFormat(base_format) if isinstance(base_format, QTextCharFormat) else QTextCharFormat()
        bullet = self._make_markdown_bullet_char_format(raw_bullet)
        merged.setProperty(self.BULLET_MARKDOWN_PROPERTY, bullet.property(self.BULLET_MARKDOWN_PROPERTY))
        merged.setProperty(self.BULLET_RAW_PROPERTY, bullet.property(self.BULLET_RAW_PROPERTY))
        return merged

    def _render_markdown_bullet_as_text(self, raw_line: str) -> str:
        parsed = self._parse_markdown_bullet_line(raw_line)
        if not parsed:
            return str(raw_line or "")
        indent, body, _prefix_len = parsed
        return f"{indent}{BULLET_GLYPH} {self._render_inline_tokens_as_text(body)}"

    def _render_inline_tokens_as_text(self, line_text: str) -> str:
        line = str(line_text or "")
        output: list[str] = []
        last_pos = 0
        for match in INLINE_TOKEN_PATTERN.finditer(line):
            if match.start() < last_pos:
                continue
            if match.start() > last_pos:
                output.append(line[last_pos:match.start()])
            raw_image = match.group("image")
            if raw_image is not None:
                output.append(match.group(0))
                last_pos = match.end()
                continue
            raw_label = match.group("link") or ""
            shown = link_display_text(raw_label)
            output.append(shown if shown else match.group(0))
            last_pos = match.end()
        if last_pos < len(line):
            output.append(line[last_pos:])
        return "".join(output)

    def _map_inline_display_offset_to_raw_offset(self, raw_text: str, display_offset: int) -> int:
        line = str(raw_text or "")
        target = max(0, int(display_offset))
        display_pos = 0
        raw_pos = 0
        last_pos = 0
        for match in INLINE_TOKEN_PATTERN.finditer(line):
            if match.start() < last_pos:
                continue
            pre_text = line[last_pos:match.start()]
            if target <= display_pos + len(pre_text):
                return raw_pos + (target - display_pos)
            display_pos += len(pre_text)
            raw_pos += len(pre_text)

            raw_token = str(match.group(0) or "")
            raw_image = match.group("image")
            if raw_image is not None:
                shown = raw_token
                if target <= display_pos + len(shown):
                    return raw_pos + (target - display_pos)
                display_pos += len(shown)
                raw_pos += len(raw_token)
                last_pos = match.end()
                continue

            raw_label = match.group("link") or ""
            shown = link_display_text(raw_label)
            if not shown:
                shown = raw_token
            if target <= display_pos + len(shown):
                local = max(0, target - display_pos)
                if shown == raw_token:
                    return raw_pos + local
                if len(raw_token) >= 2:
                    return raw_pos + 1 + min(local, max(0, len(raw_token) - 2))
                return raw_pos + min(local, len(raw_token))
            display_pos += len(shown)
            raw_pos += len(raw_token)
            last_pos = match.end()

        tail = line[last_pos:]
        if target <= display_pos + len(tail):
            return raw_pos + (target - display_pos)
        return len(line)

    def _insert_inline_tokens_for_plain_line(self, cursor: QTextCursor, line_text: str) -> None:
        line = str(line_text or "")
        last_pos = 0
        for match in INLINE_TOKEN_PATTERN.finditer(line):
            if match.start() < last_pos:
                continue

            pre_text = line[last_pos:match.start()]
            if pre_text:
                cursor.insertText(pre_text, QTextCharFormat())

            raw_image = match.group("image")
            if raw_image is not None:
                if not self._insert_inline_image_from_tag(cursor, raw_image):
                    cursor.insertText(match.group(0), QTextCharFormat())
                last_pos = match.end()
                continue

            raw_label = match.group("link") or ""
            shown = link_display_text(raw_label)
            if shown:
                cursor.insertText(shown, self._make_link_char_format(raw_label))
            else:
                cursor.insertText(match.group(0), QTextCharFormat())
            last_pos = match.end()

        if last_pos < len(line):
            cursor.insertText(line[last_pos:], QTextCharFormat())

    def _insert_inline_tokens_for_heading_line(
        self,
        cursor: QTextCursor,
        title_text: str,
        *,
        raw_heading: str,
        level: int,
    ) -> None:
        line = str(title_text or "")
        heading_plain_fmt = self._merge_markdown_heading_char_format(
            QTextCharFormat(),
            raw_heading=raw_heading,
            level=level,
        )
        last_pos = 0
        for match in INLINE_TOKEN_PATTERN.finditer(line):
            if match.start() < last_pos:
                continue

            pre_text = line[last_pos:match.start()]
            if pre_text:
                cursor.insertText(pre_text, heading_plain_fmt)

            raw_image = match.group("image")
            if raw_image is not None:
                # Keep heading content textual; don't embed inline images in heading runs.
                cursor.insertText(str(match.group(0) or ""), heading_plain_fmt)
                last_pos = match.end()
                continue

            raw_label = match.group("link") or ""
            shown = link_display_text(raw_label)
            if shown:
                link_fmt = self._make_link_char_format(raw_label)
                heading_link_fmt = self._merge_markdown_heading_char_format(
                    link_fmt,
                    raw_heading=raw_heading,
                    level=level,
                )
                cursor.insertText(shown, heading_link_fmt)
            else:
                cursor.insertText(str(match.group(0) or ""), heading_plain_fmt)
            last_pos = match.end()

        if last_pos < len(line):
            cursor.insertText(line[last_pos:], heading_plain_fmt)

    def _insert_inline_tokens_for_bullet_line(
        self,
        cursor: QTextCursor,
        body_text: str,
        *,
        raw_bullet: str,
        indent: str,
    ) -> None:
        line = str(body_text or "")
        bullet_plain_fmt = self._merge_markdown_bullet_char_format(
            QTextCharFormat(),
            raw_bullet=raw_bullet,
        )
        cursor.insertText(f"{str(indent or '')}{BULLET_GLYPH} ", bullet_plain_fmt)

        last_pos = 0
        for match in INLINE_TOKEN_PATTERN.finditer(line):
            if match.start() < last_pos:
                continue

            pre_text = line[last_pos:match.start()]
            if pre_text:
                cursor.insertText(pre_text, bullet_plain_fmt)

            raw_image = match.group("image")
            if raw_image is not None:
                # Keep bullet content textual; don't embed inline images in bullet runs.
                cursor.insertText(str(match.group(0) or ""), bullet_plain_fmt)
                last_pos = match.end()
                continue

            raw_label = match.group("link") or ""
            shown = link_display_text(raw_label)
            if shown:
                link_fmt = self._make_link_char_format(raw_label)
                bullet_link_fmt = self._merge_markdown_bullet_char_format(
                    link_fmt,
                    raw_bullet=raw_bullet,
                )
                cursor.insertText(shown, bullet_link_fmt)
            else:
                cursor.insertText(str(match.group(0) or ""), bullet_plain_fmt)
            last_pos = match.end()

        if last_pos < len(line):
            cursor.insertText(line[last_pos:], bullet_plain_fmt)

    def _insert_tdoc_line(self, cursor: QTextCursor, raw_line: str) -> None:
        line = str(raw_line or "")
        parsed_heading = self._parse_markdown_heading_line(line)
        if parsed_heading:
            level, title, _prefix_len = parsed_heading
            self._insert_inline_tokens_for_heading_line(cursor, title, raw_heading=line, level=level)
            return
        parsed_bullet = self._parse_markdown_bullet_line(line)
        if parsed_bullet:
            indent, body, _prefix_len = parsed_bullet
            self._insert_inline_tokens_for_bullet_line(cursor, body, raw_bullet=line, indent=indent)
            return
        self._insert_inline_tokens_for_plain_line(cursor, line)

    def _heading_raw_at_doc_pos(self, pos: int) -> str | None:
        doc = self.document()
        max_pos = max(0, int(doc.characterCount()) - 1)
        p = int(pos)
        if p < 0 or p >= max_pos:
            return None
        tc = QTextCursor(doc)
        tc.setPosition(p)
        tc.setPosition(p + 1, QTextCursor.KeepAnchor)
        if not tc.hasSelection():
            return None
        selected_text = str(tc.selectedText() or "")
        if selected_text in {"\n", "\r", "\u2029"}:
            return None
        fmt = tc.charFormat()
        if not fmt.hasProperty(self.HEADING_MARKDOWN_PROPERTY):
            return None
        value = fmt.property(self.HEADING_RAW_PROPERTY)
        if not isinstance(value, str):
            return None
        return value

    def _heading_level_at_doc_pos(self, pos: int) -> int:
        doc = self.document()
        max_pos = max(0, int(doc.characterCount()) - 1)
        p = int(pos)
        if p < 0 or p >= max_pos:
            return 0
        tc = QTextCursor(doc)
        tc.setPosition(p)
        tc.setPosition(p + 1, QTextCursor.KeepAnchor)
        if not tc.hasSelection():
            return 0
        fmt = tc.charFormat()
        if not fmt.hasProperty(self.HEADING_MARKDOWN_PROPERTY):
            return 0
        try:
            return max(1, min(3, int(fmt.property(self.HEADING_LEVEL_PROPERTY))))
        except Exception:
            return 0

    def _bullet_raw_at_doc_pos(self, pos: int) -> str | None:
        doc = self.document()
        max_pos = max(0, int(doc.characterCount()) - 1)
        p = int(pos)
        if p < 0 or p >= max_pos:
            return None
        tc = QTextCursor(doc)
        tc.setPosition(p)
        tc.setPosition(p + 1, QTextCursor.KeepAnchor)
        if not tc.hasSelection():
            return None
        selected_text = str(tc.selectedText() or "")
        if selected_text in {"\n", "\r", "\u2029"}:
            return None
        fmt = tc.charFormat()
        if not fmt.hasProperty(self.BULLET_MARKDOWN_PROPERTY):
            return None
        value = fmt.property(self.BULLET_RAW_PROPERTY)
        if not isinstance(value, str):
            return None
        return value

    def _collapsed_markdown_heading_info_for_block(self, block) -> tuple[str, int] | None:
        if not block.isValid():
            return None
        text = str(block.text() or "")
        if not text:
            return None
        start = int(block.position())
        raw = self._heading_raw_at_doc_pos(start)
        level = self._heading_level_at_doc_pos(start)
        if not raw or level <= 0:
            return None
        for offset in range(len(text)):
            if self._heading_raw_at_doc_pos(start + offset) != raw:
                return None
        return raw, level

    def _collapsed_markdown_bullet_raw_for_block(self, block) -> str | None:
        if not block.isValid():
            return None
        text = str(block.text() or "")
        if not text:
            return None
        start = int(block.position())
        raw = self._bullet_raw_at_doc_pos(start)
        if not raw:
            return None
        for offset in range(len(text)):
            if self._bullet_raw_at_doc_pos(start + offset) != raw:
                return None
        return raw

    def _serialize_block_to_tdoc(self, block) -> str:
        heading_info = self._collapsed_markdown_heading_info_for_block(block)
        if heading_info:
            return str(heading_info[0])
        bullet_raw = self._collapsed_markdown_bullet_raw_for_block(block)
        if bullet_raw:
            return str(bullet_raw)

        output: list[str] = []
        iter_ = block.begin()
        while not iter_.atEnd():
            fragment = iter_.fragment()
            text = fragment.text()
            fmt = fragment.charFormat()

            if fmt.isImageFormat():
                raw = fmt.property(self.IMAGE_RAW_PROPERTY) if fmt.hasProperty(self.IMAGE_RAW_PROPERTY) else ""
                raw_text = str(raw or "").strip()
                if not raw_text:
                    try:
                        raw_text = str(fmt.toImageFormat().name() or "").strip()
                    except Exception:
                        raw_text = ""
                if raw_text:
                    output.append(f"![{raw_text}]")
                else:
                    output.append(text)
            elif fmt.hasProperty(self.LINK_PROPERTY):
                raw = fmt.property(self.LINK_RAW_PROPERTY) if fmt.hasProperty(self.LINK_RAW_PROPERTY) else text
                raw_text = str(raw) if isinstance(raw, str) else str(text)
                output.append(f"[{raw_text}]")
            else:
                output.append(text)
            iter_ += 1
        return "".join(output)

    def _restyle_collapsed_markdown_headings(self) -> None:
        doc = self.document()
        if doc is None:
            return
        was_internal = bool(self._is_internal_change)
        was_modified = bool(doc.isModified())
        self._is_internal_change = True
        try:
            edit = QTextCursor(doc)
            edit.beginEditBlock()
            block = doc.begin()
            while block.isValid():
                info = self._collapsed_markdown_heading_info_for_block(block)
                if info:
                    raw, level = info
                    iter_ = block.begin()
                    while not iter_.atEnd():
                        fragment = iter_.fragment()
                        frag_text = str(fragment.text() or "")
                        frag_start = int(fragment.position())
                        frag_end = frag_start + len(frag_text)
                        if frag_end > frag_start:
                            merged = self._merge_markdown_heading_char_format(
                                fragment.charFormat(),
                                raw_heading=raw,
                                level=level,
                            )
                            edit.setPosition(frag_start)
                            edit.setPosition(frag_end, QTextCursor.KeepAnchor)
                            edit.setCharFormat(merged)
                        iter_ += 1
                block = block.next()
            edit.endEditBlock()
        finally:
            self._restore_document_modified_state(was_modified)
            self._is_internal_change = was_internal

    def load_tdoc(self, text):
        """Parses TDOC links/images and markdown-style headings for display."""
        was_undo_enabled = bool(self.isUndoRedoEnabled())
        if was_undo_enabled:
            self.setUndoRedoEnabled(False)
        self._is_internal_change = True
        self.blockSignals(True)
        self.clear()

        cursor = self.textCursor()
        cursor.beginEditBlock()

        raw_text = str(text or "")
        lines = raw_text.split("\n")
        for idx, line in enumerate(lines):
            raw_line = line[:-1] if line.endswith("\r") else line
            self._insert_tdoc_line(cursor, raw_line)
            if idx < len(lines) - 1:
                cursor.insertText("\n", QTextCharFormat())

        cursor.endEditBlock()
        top_cursor = QTextCursor(self.document())
        top_cursor.setPosition(0)
        self.setTextCursor(top_cursor)
        vbar = self.verticalScrollBar()
        if vbar is not None:
            vbar.setValue(int(vbar.minimum()))
        hbar = self.horizontalScrollBar()
        if hbar is not None:
            hbar.setValue(int(hbar.minimum()))
        self.document().setModified(False)
        self.blockSignals(False)
        self._is_internal_change = False
        if was_undo_enabled:
            self.setUndoRedoEnabled(True)
        self._last_cursor_pos = int(self.textCursor().position())
        self._rebuild_extra_selections()
        if self._search_bar.isVisible():
            self._schedule_search_refresh(immediate=True)
        self._schedule_occurrence_marker_refresh()
        self._schedule_inline_image_rescale(immediate=True)
        self._schedule_fold_refresh(immediate=True)

    def save_tdoc(self):
        """Serializes document back to TDOC syntax."""
        output = []
        doc = self.document()
        block = doc.begin()

        while block.isValid():
            output.append(self._serialize_block_to_tdoc(block))
            block = block.next()
            if block.isValid():
                output.append("\n")

        return "".join(output)

    def _get_link_target_at(self, cursor):
        if not cursor.atBlockStart():
            fmt = cursor.charFormat()
            if fmt.hasProperty(self.LINK_PROPERTY):
                return fmt.property(self.LINK_PROPERTY)

        if not cursor.atBlockEnd():
            tc = QTextCursor(cursor)
            tc.movePosition(QTextCursor.Right)
            fmt = tc.charFormat()
            if fmt.hasProperty(self.LINK_PROPERTY):
                return fmt.property(self.LINK_PROPERTY)

        return None

    def _get_link_label_at(self, cursor):
        if not cursor.atBlockStart():
            fmt = cursor.charFormat()
            if fmt.hasProperty(self.LINK_LABEL_PROPERTY):
                return fmt.property(self.LINK_LABEL_PROPERTY)

        if not cursor.atBlockEnd():
            tc = QTextCursor(cursor)
            tc.movePosition(QTextCursor.Right)
            fmt = tc.charFormat()
            if fmt.hasProperty(self.LINK_LABEL_PROPERTY):
                return fmt.property(self.LINK_LABEL_PROPERTY)

        return None

    def _link_target_at_doc_pos(self, pos: int) -> str | None:
        doc = self.document()
        max_pos = max(0, int(doc.characterCount()) - 1)
        p = int(pos)
        if p < 0 or p >= max_pos:
            return None
        tc = QTextCursor(doc)
        tc.setPosition(p)
        tc.setPosition(p + 1, QTextCursor.KeepAnchor)
        if not tc.hasSelection():
            return None
        selected_text = str(tc.selectedText() or "")
        if selected_text in {"\n", "\r", "\u2029"}:
            return None
        fmt = tc.charFormat()
        if not fmt.hasProperty(self.LINK_PROPERTY):
            return None
        value = fmt.property(self.LINK_PROPERTY)
        return str(value) if isinstance(value, str) else None

    def _link_label_at_doc_pos(self, pos: int) -> str | None:
        doc = self.document()
        max_pos = max(0, int(doc.characterCount()) - 1)
        p = int(pos)
        if p < 0 or p >= max_pos:
            return None
        tc = QTextCursor(doc)
        tc.setPosition(p)
        tc.setPosition(p + 1, QTextCursor.KeepAnchor)
        if not tc.hasSelection():
            return None
        selected_text = str(tc.selectedText() or "")
        if selected_text in {"\n", "\r", "\u2029"}:
            return None
        fmt = tc.charFormat()
        if not fmt.hasProperty(self.LINK_LABEL_PROPERTY):
            return None
        value = fmt.property(self.LINK_LABEL_PROPERTY)
        return str(value) if isinstance(value, str) else None

    def _link_raw_at_doc_pos(self, pos: int) -> str | None:
        doc = self.document()
        max_pos = max(0, int(doc.characterCount()) - 1)
        p = int(pos)
        if p < 0 or p >= max_pos:
            return None
        tc = QTextCursor(doc)
        tc.setPosition(p)
        tc.setPosition(p + 1, QTextCursor.KeepAnchor)
        if not tc.hasSelection():
            return None
        selected_text = str(tc.selectedText() or "")
        if selected_text in {"\n", "\r", "\u2029"}:
            return None
        fmt = tc.charFormat()
        if not fmt.hasProperty(self.LINK_RAW_PROPERTY):
            return None
        value = fmt.property(self.LINK_RAW_PROPERTY)
        return str(value) if isinstance(value, str) else None

    def _image_raw_at_doc_pos(self, pos: int) -> str | None:
        doc = self.document()
        max_pos = max(0, int(doc.characterCount()) - 1)
        p = int(pos)
        if p < 0 or p >= max_pos:
            return None
        tc = QTextCursor(doc)
        tc.setPosition(p)
        tc.setPosition(p + 1, QTextCursor.KeepAnchor)
        if not tc.hasSelection():
            return None
        fmt = tc.charFormat()
        if not fmt.isImageFormat():
            return None
        if fmt.hasProperty(self.IMAGE_RAW_PROPERTY):
            value = fmt.property(self.IMAGE_RAW_PROPERTY)
            if isinstance(value, str) and value.strip():
                return value.strip()
        try:
            image_fmt = fmt.toImageFormat()
            name = str(image_fmt.name() or "").strip()
        except Exception:
            name = ""
        return name or None

    def _expand_inline_image_for_editing(self, cursor: QTextCursor | None = None) -> bool:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        if cur.hasSelection():
            return False
        pos = int(cur.position())
        candidates = [pos, pos - 1]
        for candidate in candidates:
            raw = self._image_raw_at_doc_pos(candidate)
            if not raw:
                continue
            tag_text = f"![{raw}]"
            was_internal = bool(self._is_internal_change)
            was_modified = bool(self.document().isModified())
            self._is_internal_change = True
            try:
                edit = QTextCursor(self.document())
                edit.beginEditBlock()
                edit.setPosition(candidate)
                edit.setPosition(candidate + 1, QTextCursor.KeepAnchor)
                edit.removeSelectedText()
                edit.insertText(tag_text, QTextCharFormat())
                edit.endEditBlock()

                new_cursor = self.textCursor()
                new_cursor.setPosition(candidate + max(2, len(tag_text) - 1))
                self.setTextCursor(new_cursor)
            finally:
                self._restore_document_modified_state(was_modified)
                self._is_internal_change = was_internal
            self._last_cursor_pos = int(self.textCursor().position())
            return True
        return False

    def _image_tag_span_containing_doc_pos(self, pos: int) -> tuple[int, int, str] | None:
        doc = self.document()
        max_pos = max(0, int(doc.characterCount()) - 1)
        p = int(pos)
        if p < 0 or p > max_pos:
            return None
        block = doc.findBlock(p)
        if not block.isValid():
            return None
        text = block.text()
        if not text or "![" not in text or "]" not in text:
            return None

        block_start = int(block.position())
        local = p - block_start
        for match in IMAGE_PATTERN.finditer(text):
            m_start = int(match.start())
            m_end = int(match.end())
            if local <= m_start or local >= m_end:
                continue
            body = str(match.group("body") or "")
            if not body:
                continue
            return block_start + m_start, block_start + m_end, body
        return None

    def _collapse_inline_image_tag_on_cursor_move(self, old_pos: int, new_pos: int) -> bool:
        old_i = int(old_pos)
        new_i = int(new_pos)
        candidates = [old_i]
        if new_i > old_i:
            candidates.append(old_i - 1)
        elif new_i < old_i:
            candidates.append(old_i + 1)

        span = None
        for candidate in candidates:
            span = self._image_tag_span_containing_doc_pos(candidate)
            if span:
                break
        if not span:
            return False

        start, end, raw_body = span
        if start < new_i < end:
            return False

        adjusted_pos = new_i
        if adjusted_pos >= end:
            adjusted_pos = start + 1 + max(0, adjusted_pos - end)

        was_internal = bool(self._is_internal_change)
        was_modified = bool(self.document().isModified())
        self._is_internal_change = True
        try:
            edit = QTextCursor(self.document())
            edit.beginEditBlock()
            edit.setPosition(start)
            edit.setPosition(end, QTextCursor.KeepAnchor)
            edit.removeSelectedText()
            ok = self._insert_inline_image_from_tag(edit, raw_body)
            if not ok:
                edit.insertText(f"![{raw_body}]", QTextCharFormat())
            edit.endEditBlock()
            if not ok:
                return False

            max_pos = max(0, int(self.document().characterCount()) - 1)
            safe_pos = max(0, min(adjusted_pos, max_pos))
            new_cursor = self.textCursor()
            new_cursor.setPosition(safe_pos)
            self.setTextCursor(new_cursor)
        finally:
            self._restore_document_modified_state(was_modified)
            self._is_internal_change = was_internal
        self._schedule_inline_image_rescale(immediate=True)
        self._last_cursor_pos = int(self.textCursor().position())
        return True

    def _link_span_at_cursor(
        self, cursor: QTextCursor | None = None
    ) -> tuple[int, int, str] | None:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        if cur.hasSelection():
            return None
        pos = int(cur.position())

        left_target = self._link_target_at_doc_pos(pos - 1)
        right_target = self._link_target_at_doc_pos(pos)
        if not left_target and not right_target:
            return None
        target = left_target or right_target
        if not isinstance(target, str):
            return None

        if left_target:
            probe = pos - 1
        else:
            probe = pos
        if probe < 0:
            return None
        probe_block = self.document().findBlock(probe)
        if not probe_block.isValid():
            return None
        block_start = int(probe_block.position())
        block_end = block_start + len(str(probe_block.text() or ""))
        if probe < block_start or probe >= block_end:
            return None

        raw = self._link_raw_at_doc_pos(probe)
        if not raw:
            fallback = self._link_label_at_doc_pos(probe)
            raw = str(fallback or "").strip()
        if not raw:
            return None

        start = probe
        while start > block_start:
            prev_target = self._link_target_at_doc_pos(start - 1)
            if prev_target != target:
                break
            prev_raw = self._link_raw_at_doc_pos(start - 1)
            if not prev_raw:
                prev_raw = self._link_label_at_doc_pos(start - 1)
            if str(prev_raw or "") != raw:
                break
            start -= 1

        end = probe + 1
        while end < block_end:
            next_target = self._link_target_at_doc_pos(end)
            if next_target != target:
                break
            next_raw = self._link_raw_at_doc_pos(end)
            if not next_raw:
                next_raw = self._link_label_at_doc_pos(end)
            if str(next_raw or "") != raw:
                break
            end += 1

        if end <= start:
            return None
        return start, end, raw

    def _make_link_char_format(self, raw_link_text: str) -> QTextCharFormat:
        normalized_raw = compose_link_components(*parse_link_components(raw_link_text))
        effective = link_effective_target(normalized_raw)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("grey"))
        fmt.setFontUnderline(True)
        fmt.setProperty(self.LINK_PROPERTY, self._make_link_target(effective))
        fmt.setProperty(self.LINK_LABEL_PROPERTY, effective)
        fmt.setProperty(self.LINK_RAW_PROPERTY, normalized_raw)
        return fmt

    def _replace_range_with_link_label(
        self,
        *,
        start: int,
        end: int,
        raw_link: str,
        new_cursor_pos: int | None = None,
    ) -> None:
        normalized_raw = compose_link_components(*parse_link_components(raw_link))
        shown = link_display_text(normalized_raw)
        if not shown:
            shown = normalized_raw
        was_internal = bool(self._is_internal_change)
        was_modified = bool(self.document().isModified())
        self._is_internal_change = True
        try:
            edit = QTextCursor(self.document())
            edit.beginEditBlock()
            edit.setPosition(start)
            edit.setPosition(end, QTextCursor.KeepAnchor)
            edit.removeSelectedText()
            edit.insertText(shown, self._make_link_char_format(normalized_raw))
            edit.endEditBlock()

            if new_cursor_pos is not None:
                max_pos = max(0, int(self.document().characterCount()) - 1)
                safe_pos = max(0, min(int(new_cursor_pos), max_pos))
                cur = self.textCursor()
                cur.setPosition(safe_pos)
                self.setTextCursor(cur)
        finally:
            self._restore_document_modified_state(was_modified)
            self._is_internal_change = was_internal
        self._last_cursor_pos = int(self.textCursor().position())

    def _bracket_link_span_containing_doc_pos(self, pos: int) -> tuple[int, int, str] | None:
        doc = self.document()
        max_pos = max(0, int(doc.characterCount()) - 1)
        p = int(pos)
        if p < 0 or p > max_pos:
            return None
        block = doc.findBlock(p)
        if not block.isValid():
            return None
        text = block.text()
        if not text or "[" not in text or "]" not in text:
            return None

        block_start = int(block.position())
        local = p - block_start
        for match in LINK_PATTERN.finditer(text):
            m_start = int(match.start())
            m_end = int(match.end())
            if local <= m_start or local >= m_end:
                continue
            label = str(match.group("label") or "")
            if not label:
                continue
            return block_start + m_start, block_start + m_end, label
        return None

    def _collapse_bracket_link_on_cursor_move(self, old_pos: int, new_pos: int) -> bool:
        old_i = int(old_pos)
        new_i = int(new_pos)
        candidates = [old_i]
        if new_i > old_i:
            candidates.append(old_i - 1)
        elif new_i < old_i:
            candidates.append(old_i + 1)

        span = None
        for candidate in candidates:
            span = self._bracket_link_span_containing_doc_pos(candidate)
            if span:
                break
        if not span:
            return False
        start, end, raw = span
        if start < new_i < end:
            return False

        adjusted_pos = new_i
        if adjusted_pos >= end:
            shown = link_display_text(raw)
            adjusted_pos = start + len(shown) + max(0, adjusted_pos - end)
        self._replace_range_with_link_label(start=start, end=end, raw_link=raw, new_cursor_pos=adjusted_pos)
        return True

    def _expand_link_for_editing(
        self,
        cursor: QTextCursor | None = None,
    ) -> bool:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        span = self._link_span_at_cursor(cur)
        if not span:
            return False
        start, end, raw = span
        pos = int(cur.position())

        # Expand only when caret is strictly inside rendered link text.
        # Boundaries (before first char / after last char) stay collapsed.
        if pos <= start or pos >= end:
            return False

        replacement = f"[{raw}]"
        relative = pos - start

        was_internal = bool(self._is_internal_change)
        was_modified = bool(self.document().isModified())
        self._is_internal_change = True
        try:
            edit = QTextCursor(self.document())
            edit.beginEditBlock()
            edit.setPosition(start)
            edit.setPosition(end, QTextCursor.KeepAnchor)
            edit.removeSelectedText()
            edit.insertText(replacement, QTextCharFormat())
            edit.endEditBlock()

            new_cursor = self.textCursor()
            new_cursor.setPosition(start + 1 + relative)
            self.setTextCursor(new_cursor)
        finally:
            self._restore_document_modified_state(was_modified)
            self._is_internal_change = was_internal
        self._last_cursor_pos = int(self.textCursor().position())
        return True

    def _bullet_span_at_cursor(
        self,
        cursor: QTextCursor | None = None,
    ) -> tuple[int, int, str] | None:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        if cur.hasSelection():
            return None
        pos = int(cur.position())

        left_raw = self._bullet_raw_at_doc_pos(pos - 1)
        right_raw = self._bullet_raw_at_doc_pos(pos)
        if not left_raw and not right_raw:
            return None
        raw = str(left_raw or right_raw or "")
        if not raw:
            return None
        probe = pos - 1 if left_raw else pos

        probe_block = self.document().findBlock(probe)
        if not probe_block.isValid():
            return None
        block_start = int(probe_block.position())
        block_end = block_start + len(str(probe_block.text() or ""))
        if probe < block_start or probe >= block_end:
            return None

        start = probe
        while start > block_start:
            if self._bullet_raw_at_doc_pos(start - 1) != raw:
                break
            start -= 1

        end = probe + 1
        while end < block_end:
            if self._bullet_raw_at_doc_pos(end) != raw:
                break
            end += 1

        if end <= start:
            return None
        return start, end, raw

    def _expand_markdown_bullet_for_editing(
        self,
        cursor: QTextCursor | None = None,
    ) -> bool:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        span = self._bullet_span_at_cursor(cur)
        if not span:
            return False
        start, end, raw = span
        pos = int(cur.position())
        if pos <= start or pos >= end:
            return False

        parsed = self._parse_markdown_bullet_line(raw)
        if not parsed:
            return False
        indent, body, prefix_len = parsed
        rendered_body = self._render_inline_tokens_as_text(body)
        rendered_len = len(indent) + 2 + len(rendered_body)
        relative = max(0, min(pos - start, end - start))
        if relative >= rendered_len:
            mapped = len(raw)
        elif relative < prefix_len:
            mapped = relative
        else:
            mapped = prefix_len + self._map_inline_display_offset_to_raw_offset(body, relative - prefix_len)

        was_internal = bool(self._is_internal_change)
        was_modified = bool(self.document().isModified())
        self._is_internal_change = True
        try:
            edit = QTextCursor(self.document())
            edit.beginEditBlock()
            edit.setPosition(start)
            edit.setPosition(end, QTextCursor.KeepAnchor)
            edit.removeSelectedText()
            edit.insertText(raw, QTextCharFormat())
            edit.endEditBlock()

            new_cursor = self.textCursor()
            max_pos = max(0, int(self.document().characterCount()) - 1)
            new_cursor.setPosition(max(0, min(start + mapped, max_pos)))
            self.setTextCursor(new_cursor)
        finally:
            self._restore_document_modified_state(was_modified)
            self._is_internal_change = was_internal
        self._last_cursor_pos = int(self.textCursor().position())
        return True

    def _collapse_markdown_bullet_on_cursor_move(self, old_pos: int, new_pos: int) -> bool:
        old_i = int(old_pos)
        new_i = int(new_pos)
        candidates = [old_i]
        if new_i > old_i:
            candidates.append(old_i - 1)
        elif new_i < old_i:
            candidates.append(old_i + 1)

        checked_blocks: set[int] = set()
        for candidate in candidates:
            if candidate < 0:
                continue
            block = self.document().findBlock(candidate)
            if not block.isValid():
                continue
            block_no = int(block.blockNumber())
            if block_no in checked_blocks:
                continue
            checked_blocks.add(block_no)

            if self._collapsed_markdown_bullet_raw_for_block(block):
                continue

            raw_line = self._serialize_block_to_tdoc(block)
            parsed = self._parse_markdown_bullet_line(raw_line)
            if not parsed:
                continue
            indent, body, _prefix_len = parsed
            rendered_line = self._render_markdown_bullet_as_text(raw_line)

            block_start = int(block.position())
            block_end = block_start + len(str(block.text() or ""))
            if block_start <= new_i <= block_end:
                return False

            adjusted_pos = new_i
            current_len = max(0, block_end - block_start)
            delta = len(rendered_line) - current_len
            if adjusted_pos > block_end:
                adjusted_pos += delta

            was_internal = bool(self._is_internal_change)
            was_modified = bool(self.document().isModified())
            self._is_internal_change = True
            try:
                edit = QTextCursor(self.document())
                edit.beginEditBlock()
                edit.setPosition(block_start)
                edit.setPosition(block_end, QTextCursor.KeepAnchor)
                edit.removeSelectedText()
                self._insert_inline_tokens_for_bullet_line(
                    edit,
                    body,
                    raw_bullet=raw_line,
                    indent=indent,
                )
                edit.endEditBlock()

                max_pos = max(0, int(self.document().characterCount()) - 1)
                safe_pos = max(0, min(adjusted_pos, max_pos))
                new_cursor = self.textCursor()
                new_cursor.setPosition(safe_pos)
                self.setTextCursor(new_cursor)
            finally:
                self._restore_document_modified_state(was_modified)
                self._is_internal_change = was_internal
            self._last_cursor_pos = int(self.textCursor().position())
            return True
        return False

    def _heading_span_at_cursor(
        self,
        cursor: QTextCursor | None = None,
    ) -> tuple[int, int, str, int] | None:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        if cur.hasSelection():
            return None
        pos = int(cur.position())

        left_raw = self._heading_raw_at_doc_pos(pos - 1)
        right_raw = self._heading_raw_at_doc_pos(pos)
        if not left_raw and not right_raw:
            return None
        raw = str(left_raw or right_raw or "")
        if not raw:
            return None
        probe = pos - 1 if left_raw else pos
        level = self._heading_level_at_doc_pos(probe)
        if level <= 0:
            return None

        probe_block = self.document().findBlock(probe)
        if not probe_block.isValid():
            return None
        block_start = int(probe_block.position())
        block_end = block_start + len(str(probe_block.text() or ""))
        if probe < block_start or probe >= block_end:
            return None

        start = probe
        while start > block_start:
            if self._heading_raw_at_doc_pos(start - 1) != raw:
                break
            start -= 1

        end = probe + 1
        while end < block_end:
            if self._heading_raw_at_doc_pos(end) != raw:
                break
            end += 1

        if end <= start:
            return None
        return start, end, raw, level

    def _expand_markdown_heading_for_editing(
        self,
        cursor: QTextCursor | None = None,
    ) -> bool:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        span = self._heading_span_at_cursor(cur)
        if not span:
            return False
        start, end, raw, _level = span
        pos = int(cur.position())
        if pos <= start or pos >= end:
            return False

        parsed = self._parse_markdown_heading_line(raw)
        if not parsed:
            return False
        _raw_level, title, prefix_len = parsed
        rendered_title = self._render_inline_tokens_as_text(title)
        title_len = len(rendered_title)
        relative = max(0, min(pos - start, end - start))
        if relative >= title_len:
            mapped = len(raw)
        else:
            mapped = prefix_len + self._map_inline_display_offset_to_raw_offset(title, relative)

        was_internal = bool(self._is_internal_change)
        was_modified = bool(self.document().isModified())
        self._is_internal_change = True
        try:
            edit = QTextCursor(self.document())
            edit.beginEditBlock()
            edit.setPosition(start)
            edit.setPosition(end, QTextCursor.KeepAnchor)
            edit.removeSelectedText()
            edit.insertText(raw, QTextCharFormat())
            edit.endEditBlock()

            new_cursor = self.textCursor()
            max_pos = max(0, int(self.document().characterCount()) - 1)
            new_cursor.setPosition(max(0, min(start + mapped, max_pos)))
            self.setTextCursor(new_cursor)
        finally:
            self._restore_document_modified_state(was_modified)
            self._is_internal_change = was_internal
        self._last_cursor_pos = int(self.textCursor().position())
        return True

    def _collapse_markdown_heading_on_cursor_move(self, old_pos: int, new_pos: int) -> bool:
        old_i = int(old_pos)
        new_i = int(new_pos)
        candidates = [old_i]
        if new_i > old_i:
            candidates.append(old_i - 1)
        elif new_i < old_i:
            candidates.append(old_i + 1)

        checked_blocks: set[int] = set()
        for candidate in candidates:
            if candidate < 0:
                continue
            block = self.document().findBlock(candidate)
            if not block.isValid():
                continue
            block_no = int(block.blockNumber())
            if block_no in checked_blocks:
                continue
            checked_blocks.add(block_no)

            # Already-collapsed headings don't need another collapse pass.
            if self._collapsed_markdown_heading_info_for_block(block):
                continue

            raw_line = self._serialize_block_to_tdoc(block)
            parsed = self._parse_markdown_heading_line(raw_line)
            if not parsed:
                continue

            level, title, _prefix_len = parsed
            if not title:
                continue
            rendered_title = self._render_inline_tokens_as_text(title)

            block_start = int(block.position())
            block_end = block_start + len(str(block.text() or ""))
            if block_start <= new_i <= block_end:
                return False

            adjusted_pos = new_i
            current_len = max(0, block_end - block_start)
            delta = len(rendered_title) - current_len
            if adjusted_pos > block_end:
                adjusted_pos += delta

            was_internal = bool(self._is_internal_change)
            was_modified = bool(self.document().isModified())
            self._is_internal_change = True
            try:
                edit = QTextCursor(self.document())
                edit.beginEditBlock()
                edit.setPosition(block_start)
                edit.setPosition(block_end, QTextCursor.KeepAnchor)
                edit.removeSelectedText()
                self._insert_inline_tokens_for_heading_line(
                    edit,
                    title,
                    raw_heading=raw_line,
                    level=level,
                )
                edit.endEditBlock()

                max_pos = max(0, int(self.document().characterCount()) - 1)
                safe_pos = max(0, min(adjusted_pos, max_pos))
                new_cursor = self.textCursor()
                new_cursor.setPosition(safe_pos)
                self.setTextCursor(new_cursor)
            finally:
                self._restore_document_modified_state(was_modified)
                self._is_internal_change = was_internal
            self._last_cursor_pos = int(self.textCursor().position())
            return True
        return False

    def _on_cursor_position_changed_for_link_editing(self) -> None:
        if bool(self._is_internal_change):
            self._last_cursor_pos = int(self.textCursor().position())
            return
        old_pos = int(self._last_cursor_pos)
        new_pos = int(self.textCursor().position())
        if old_pos != new_pos:
            self._collapse_markdown_heading_on_cursor_move(old_pos, new_pos)
            new_pos = int(self.textCursor().position())
            self._collapse_markdown_bullet_on_cursor_move(old_pos, new_pos)
            new_pos = int(self.textCursor().position())
            self._collapse_inline_image_tag_on_cursor_move(old_pos, new_pos)
            new_pos = int(self.textCursor().position())
            self._collapse_bracket_link_on_cursor_move(old_pos, new_pos)
            new_pos = int(self.textCursor().position())
        self._expand_markdown_heading_for_editing(self.textCursor())
        self._expand_markdown_bullet_for_editing(self.textCursor())
        self._expand_link_for_editing(self.textCursor())
        self._on_cursor_moved_inline_suggestion()
        self._last_cursor_pos = int(self.textCursor().position())

    def _cursor_is_appending_to_link(self, cursor: QTextCursor | None = None) -> bool:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        pos = int(cur.position())
        left = self._link_target_at_doc_pos(pos - 1)
        if not left:
            return False
        right = self._link_target_at_doc_pos(pos)
        if right and right == left:
            return False
        return True

    def _cursor_is_prepending_to_link(self, cursor: QTextCursor | None = None) -> bool:
        cur = QTextCursor(cursor) if isinstance(cursor, QTextCursor) else self.textCursor()
        pos = int(cur.position())
        right = self._link_target_at_doc_pos(pos)
        if not right:
            return False
        left = self._link_target_at_doc_pos(pos - 1)
        if left and left == right:
            return False
        return True

    def _try_convert_recent_bracket_link(self) -> bool:
        cur = self.textCursor()
        block = cur.block()
        if not block.isValid():
            return False

        block_text = block.text()
        col = int(cur.positionInBlock())
        end_idx = col - 1
        if end_idx < 1 or end_idx >= len(block_text):
            return False
        if block_text[end_idx] != "]":
            return False

        start_idx = block_text.rfind("[", 0, end_idx)
        if start_idx < 0:
            return False
        if start_idx > 0 and block_text[start_idx - 1] == "!":
            return False
        if "]" in block_text[start_idx + 1:end_idx]:
            return False

        label = block_text[start_idx + 1:end_idx]
        if not label:
            return False
        if "\n" in label or "\r" in label or "[" in label or "]" in label:
            return False

        abs_start = int(block.position()) + start_idx
        abs_end = int(block.position()) + end_idx + 1
        self._replace_range_with_link_label(
            start=abs_start,
            end=abs_end,
            raw_link=label,
            new_cursor_pos=abs_start + len(link_display_text(label)),
        )
        return True

    def _try_convert_recent_image_tag(self) -> bool:
        cur = self.textCursor()
        block = cur.block()
        if not block.isValid():
            return False

        block_text = block.text()
        col = int(cur.positionInBlock())
        end_idx = col - 1
        if end_idx < 2 or end_idx >= len(block_text):
            return False
        if block_text[end_idx] != "]":
            return False

        bracket_idx = block_text.rfind("[", 0, end_idx)
        if bracket_idx <= 0:
            return False
        if block_text[bracket_idx - 1] != "!":
            return False
        if "]" in block_text[bracket_idx + 1:end_idx]:
            return False

        body = block_text[bracket_idx + 1:end_idx]
        if not body:
            return False
        if "\n" in body or "\r" in body or "[" in body or "]" in body:
            return False

        abs_start = int(block.position()) + bracket_idx - 1
        abs_end = int(block.position()) + end_idx + 1
        raw_tag = f"![{body}]"

        was_internal = bool(self._is_internal_change)
        self._is_internal_change = True
        try:
            edit = QTextCursor(self.document())
            edit.beginEditBlock()
            edit.setPosition(abs_start)
            edit.setPosition(abs_end, QTextCursor.KeepAnchor)
            edit.removeSelectedText()
            ok = self._insert_inline_image_from_tag(edit, body)
            if not ok:
                edit.insertText(raw_tag, QTextCharFormat())
            edit.endEditBlock()

            if ok:
                new_cursor = self.textCursor()
                new_cursor.setPosition(int(edit.position()))
                self.setTextCursor(new_cursor)
                self._last_cursor_pos = int(self.textCursor().position())
                return True
            return False
        finally:
            self._is_internal_change = was_internal

    def _show_context_menu(self, pos):
        menu = self.createStandardContextMenu()
        cursor = self.cursorForPosition(pos)
        target = self._get_link_target_at(cursor)
        menu.addSeparator()
        action_find = menu.addAction("Find")
        action_replace = menu.addAction("Replace")
        action_ai = menu.addAction("AI Inline Assist")
        action_frontmatter_toggle = menu.addAction(
            "Fold Frontmatter" if self.is_frontmatter_visible() else "Unfold Frontmatter"
        )
        action_frontmatter_toggle.setEnabled(self._frontmatter_block_bounds() is not None)

        if isinstance(target, str) and target.startswith("symbol:"):
            label = self._get_link_label_at(cursor)
            symbol = target[len("symbol:"):].strip()
            menu.addSeparator()
            if callable(self.go_to_symbol_definition):
                go_to_definition_action = menu.addAction("Go to Definition")
                go_to_definition_action.triggered.connect(
                    lambda s=symbol, label_text=label: self.go_to_symbol_definition(s or label_text)
                )
            if label:
                if callable(self.rename_alias):
                    rename_action = menu.addAction("Rename Alias...")
                    rename_action.triggered.connect(lambda: self.rename_alias(label))
                if callable(self.normalize_symbol):
                    normalize_action = menu.addAction("Normalize This Symbol")
                    normalize_action.triggered.connect(lambda: self.normalize_symbol(label))

        payload = {
            "line": int(cursor.blockNumber() + 1),
            "column": int(cursor.positionInBlock() + 1),
            "cursor_pos": int(cursor.position()),
            "local_pos": QPoint(pos),
            "global_pos": QPoint(self.viewport().mapToGlobal(pos)),
        }
        context_populator = getattr(self, "populate_context_menu", None)
        if callable(context_populator):
            try:
                context_populator(menu, payload)
            except Exception:
                pass

        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen is action_find:
            self.show_find_bar()
            return
        if chosen is action_replace:
            self.show_replace_bar()
            return
        if chosen is action_ai:
            self.aiAssistRequested.emit("manual")
            return
        if chosen is action_frontmatter_toggle:
            self.toggle_frontmatter_visibility()
            return

    def mouseMoveEvent(self, e):
        cursor = self.cursorForPosition(e.position().toPoint())
        target = self._get_link_target_at(cursor)
        has_target = bool(target)
        self.viewport().setCursor(Qt.PointingHandCursor if has_target else Qt.IBeamCursor)
        if has_target and callable(self.resolve_link_tooltip):
            target_text = str(target)
            tip_text = ""
            try:
                tip_text = str(self.resolve_link_tooltip(target_text) or "").strip()
            except Exception:
                tip_text = ""
            if tip_text:
                if tip_text != self._hover_tooltip_text or target_text != self._hover_tooltip_target:
                    offset = QPoint(16, 18)
                    global_pos = self.viewport().mapToGlobal(e.position().toPoint() + offset)
                    QToolTip.showText(global_pos, tip_text, self.viewport())
                    self._hover_tooltip_target = target_text
                    self._hover_tooltip_text = tip_text
            elif self._hover_tooltip_target:
                QToolTip.hideText()
                self._hover_tooltip_target = ""
                self._hover_tooltip_text = ""
        elif self._hover_tooltip_target:
            QToolTip.hideText()
            self._hover_tooltip_target = ""
            self._hover_tooltip_text = ""
        super().mouseMoveEvent(e)

    def leaveEvent(self, event):
        if self._hover_tooltip_target:
            QToolTip.hideText()
            self._hover_tooltip_target = ""
            self._hover_tooltip_text = ""
        super().leaveEvent(event)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and (e.modifiers() & Qt.ControlModifier):
            cursor = self.cursorForPosition(e.position().toPoint())
            target = self._get_link_target_at(cursor)

            if isinstance(target, str):
                if target.startswith("file:") and self.open_file_by_name:
                    self.open_file_by_name(target[len("file:"):])
                    return
                if target.startswith("symbol:") and self.open_symbol:
                    self.open_symbol(target[len("symbol:"):])
                    return
        elif e.button() == Qt.LeftButton and not bool(e.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            cursor = self.cursorForPosition(e.position().toPoint())
            if self._expand_inline_image_for_editing(cursor):
                self._rebuild_extra_selections()
                self._schedule_occurrence_marker_refresh()
                if self._search_bar.isVisible():
                    self._schedule_search_refresh(immediate=True)
                self._refresh_overview_marker_area()
                e.accept()
                return

        super().mousePressEvent(e)

    def _indent_selection_or_insert(self) -> None:
        cursor = self.textCursor()
        unit = self._indent_unit()
        if not cursor.hasSelection():
            cursor.insertText(unit, QTextCharFormat())
            return

        start = int(cursor.selectionStart())
        end = int(cursor.selectionEnd())
        doc = self.document()
        first_block = doc.findBlock(start)
        last_block = doc.findBlock(max(start, end - 1))
        if not first_block.isValid() or not last_block.isValid():
            return

        cursor.beginEditBlock()
        try:
            block = first_block
            while block.isValid():
                c = QTextCursor(block)
                c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                c.insertText(unit, QTextCharFormat())
                if block.blockNumber() >= last_block.blockNumber():
                    break
                block = block.next()
        finally:
            cursor.endEditBlock()

    def _unindent_selection_or_line(self) -> None:
        cursor = self.textCursor()
        unit = self._indent_unit()
        indent_width = self._active_indent_width()
        doc = self.document()
        has_selection = cursor.hasSelection()

        if has_selection:
            start = int(cursor.selectionStart())
            end = int(cursor.selectionEnd())
            first_block = doc.findBlock(start)
            last_block = doc.findBlock(max(start, end - 1))
        else:
            first_block = cursor.block()
            last_block = cursor.block()
        if not first_block.isValid() or not last_block.isValid():
            return

        cursor.beginEditBlock()
        try:
            block = first_block
            while block.isValid():
                text = str(block.text() or "")
                remove_n = 0
                if text.startswith(unit):
                    remove_n = len(unit)
                elif text.startswith("\t"):
                    remove_n = 1
                else:
                    while remove_n < min(indent_width, len(text)) and text[remove_n] == " ":
                        remove_n += 1
                if remove_n > 0:
                    c = QTextCursor(block)
                    c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                    c.movePosition(
                        QTextCursor.MoveOperation.Right,
                        QTextCursor.MoveMode.KeepAnchor,
                        remove_n,
                    )
                    c.removeSelectedText()
                if block.blockNumber() >= last_block.blockNumber():
                    break
                block = block.next()
        finally:
            cursor.endEditBlock()

    def duplicate_selection_or_line(self) -> bool:
        cursor = self.textCursor()
        doc = self.document()

        if cursor.hasSelection():
            end = int(cursor.selectionEnd())
            selected_text = str(cursor.selectedText() or "").replace("\u2029", "\n")
            if not selected_text:
                return False

            cursor.beginEditBlock()
            try:
                insert_cursor = QTextCursor(doc)
                insert_cursor.setPosition(end)
                insert_cursor.insertText(selected_text)

                restored = QTextCursor(doc)
                restored.setPosition(end)
                restored.setPosition(end + len(selected_text), QTextCursor.KeepAnchor)
                self.setTextCursor(restored)
            finally:
                cursor.endEditBlock()
            return True

        block = cursor.block()
        if not block.isValid():
            return False

        line_text = block.text()
        current_col = int(cursor.positionInBlock())
        next_block = block.next()
        insert_at = int(next_block.position()) if next_block.isValid() else max(0, int(doc.characterCount()) - 1)
        insert_text = (line_text + "\n") if next_block.isValid() else ("\n" + line_text)
        target_block_no = int(block.blockNumber()) + 1

        cursor.beginEditBlock()
        try:
            insert_cursor = QTextCursor(doc)
            insert_cursor.setPosition(insert_at)
            insert_cursor.insertText(insert_text)

            dup_block = doc.findBlockByNumber(target_block_no)
            if dup_block.isValid():
                new_pos = int(dup_block.position()) + min(current_col, len(dup_block.text()))
            else:
                new_pos = insert_at

            restored = QTextCursor(doc)
            restored.setPosition(max(0, new_pos))
            self.setTextCursor(restored)
            self.ensureCursorVisible()
        finally:
            cursor.endEditBlock()
        return True

    def keyPressEvent(self, event):
        text = str(event.text() or "")
        mods = event.modifiers()
        key = int(event.key())

        if self._is_tdoc_completion_popup_visible():
            if key == Qt.Key_Escape:
                self._hide_tdoc_completion_popup()
                return
            if key == Qt.Key_Up:
                self._move_tdoc_completion_selection(-1)
                return
            if key == Qt.Key_Down:
                self._move_tdoc_completion_selection(1)
                return
            if key == Qt.Key_Tab:
                if self._accept_tdoc_completion():
                    return
                # Never insert a literal tab while completion UI is active.
                self._hide_tdoc_completion_popup()
                return
            if key in {Qt.Key_Return, Qt.Key_Enter}:
                if self._accept_tdoc_completion():
                    return

        if self.has_inline_suggestion():
            if key == Qt.Key_Escape:
                self.clear_inline_suggestion()
                return
            if key == Qt.Key_Tab:
                if self.accept_inline_suggestion():
                    return
            if key in {
                Qt.Key_Left,
                Qt.Key_Right,
                Qt.Key_Up,
                Qt.Key_Down,
                Qt.Key_Home,
                Qt.Key_End,
                Qt.Key_PageUp,
                Qt.Key_PageDown,
                Qt.Key_Backspace,
                Qt.Key_Delete,
                Qt.Key_Return,
                Qt.Key_Enter,
            } or (text and not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))):
                self.clear_inline_suggestion()

        if key in {Qt.Key_Tab, Qt.Key_Backtab} and not bool(mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            if key == Qt.Key_Backtab or bool(mods & Qt.ShiftModifier):
                self._unindent_selection_or_line()
            else:
                self._indent_selection_or_insert()
            return

        should_break_link_boundary = bool(
            text
            and not bool(mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
            and (
                self._cursor_is_appending_to_link(self.textCursor())
                or self._cursor_is_prepending_to_link(self.textCursor())
            )
        )
        if should_break_link_boundary:
            self.setCurrentCharFormat(QTextCharFormat())

        was_internal = bool(self._is_internal_change)
        super().keyPressEvent(event)
        if was_internal:
            return

        close_bracket_intent = bool(
            (text == "]" or event.key() == Qt.Key_BracketRight)
            and not bool(mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
        )
        if close_bracket_intent:
            converted = self._try_convert_recent_image_tag()
            if not converted:
                converted = self._try_convert_recent_bracket_link()
            if converted:
                self._rebuild_extra_selections()
                self._schedule_occurrence_marker_refresh()
                if self._search_bar.isVisible():
                    self._schedule_search_refresh(immediate=True)
                self._refresh_overview_marker_area()

        if key in {
            Qt.Key_Backspace,
            Qt.Key_Delete,
            Qt.Key_Left,
            Qt.Key_Right,
            Qt.Key_Up,
            Qt.Key_Down,
            Qt.Key_Home,
            Qt.Key_End,
            Qt.Key_PageUp,
            Qt.Key_PageDown,
        } or (text and not bool(mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))):
            self._schedule_tdoc_completion_refresh(immediate=True)

    def wheelEvent(self, event):
        mods = event.modifiers()
        ctrl_zoom = bool(mods & Qt.ControlModifier) and not bool(mods & (Qt.AltModifier | Qt.MetaModifier))
        if ctrl_zoom:
            delta_y = int(event.angleDelta().y())
            if delta_y == 0:
                delta_y = int(event.pixelDelta().y())
            if delta_y != 0:
                self.editorFontSizeStepRequested.emit(1 if delta_y > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)
