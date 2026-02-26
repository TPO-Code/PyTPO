"""Core TDOC parser/index/editor primitives shared by IDE widgets.

Extracted from tdock demo app to avoid runtime dependency on tdock package.
"""

import fnmatch
import os
import re
from collections import defaultdict
from pathlib import Path

from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextImageFormat,
    QTextFormat,
)
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTextEdit,
    QWidget,
)

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
FRONTMATTER_KV_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(?P<value>.*)$")
_WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")

_TDOC_LINT_VISUAL_DEFAULTS = {
    "error_color": "#E35D6A",
    "warning_color": "#D6A54A",
    "info_color": "#6AA1FF",
    "hint_color": "#8F9AA5",
    "line_alpha": 64,
}
_TDOC_OVERVIEW_MARKER_DEFAULTS = {
    "enabled": True,
    "width": 10,
    "search_color": "#4A8FD8",
    "search_active_color": "#D6A853",
    "occurrence_color": "#66A86A",
    "max_occurrence_matches": 12000,
}


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
    def _parse_symbol_definition(line):
        parts = [p.strip() for p in line.split(";")]
        primary = parts[0] if parts else ""
        metadata_parts = parts[1:] if len(parts) > 1 else []

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
            if "=" not in item:
                metadata_issues.append(f"Malformed metadata entry '{item}'. Use 'key=value'.")
                continue
            key, value = item.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if not key:
                metadata_issues.append(f"Metadata key is empty in '{item}'.")
                continue
            if not value:
                metadata_issues.append(f"Metadata value is empty for key '{key}'.")
                continue
            if key in metadata:
                metadata_issues.append(f"Duplicate metadata key '{key}'.")
                continue
            metadata[key] = value

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
    def _section_header_capitalization_warnings(lines):
        warnings = []
        for idx, raw in enumerate(lines, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            rule, _patterns = TDocProjectIndex._parse_rule_line(line)
            if rule:
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
    def iter_doc_paths(root_path, include_patterns=None, ignore_patterns=None):
        root = Path(root_path)
        include_patterns = include_patterns or []
        ignore_patterns = ignore_patterns or []

        for path in root.rglob(f"*{DOC_SUFFIX}"):
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

        for raw in raw_lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            rule, patterns = TDocProjectIndex._parse_rule_line(line)
            if rule:
                if rule == "include":
                    include_patterns.extend(patterns)
                elif rule == "ignore":
                    ignore_patterns.extend(patterns)
                continue

            if TDocProjectIndex._is_section_header(line):
                section_match = SECTION_HEADER_PATTERN.match(line)
                current_section = section_match.group("section").strip() if section_match else ""
                continue

            symbol, alias_items, metadata, _ = TDocProjectIndex._parse_symbol_definition(line)

            if not symbol:
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

        for raw in lines:
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(raw)
                continue

            rule, _ = TDocProjectIndex._parse_rule_line(stripped)
            if rule:
                new_lines.append(raw)
                continue

            if TDocProjectIndex._is_section_header(stripped):
                new_lines.append(raw)
                continue

            symbol, alias_items, metadata, _ = TDocProjectIndex._parse_symbol_definition(stripped)

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

            new_lines.append(TDocProjectIndex._format_symbol_definition(symbol, rewritten_aliases, metadata))

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
    ):
        symbol_refs = defaultdict(set)
        unresolved_refs = defaultdict(set)
        doc_metadata = {}
        frontmatter_issues = []
        normalized_overrides = TDocProjectIndex._normalize_content_overrides(content_overrides)

        for path, rel_path in TDocProjectIndex.iter_doc_paths(root_path, include_patterns, ignore_patterns):
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
                for match in LINK_PATTERN.finditer(line):
                    raw = match.group("label")
                    label = link_effective_target(raw)
                    if not label:
                        continue

                    # [foo.tdoc] and [foo.tdoc#L42] are file links, not symbols.
                    file_path, _ = parse_file_link(label)
                    if file_path:
                        continue

                    symbol = alias_to_symbol.get(label.casefold())
                    ref = (rel_path, line_no)
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
    def validate_project(root_path, content_overrides=None):
        findings = []
        marker = TDocProjectIndex.marker_path(root_path)
        normalized_overrides = TDocProjectIndex._normalize_content_overrides(content_overrides)

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

        for idx, raw in enumerate(lines, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            rule, patterns = TDocProjectIndex._parse_rule_line(line)
            if rule:
                if not patterns:
                    findings.append(
                        {
                            "severity": "warning",
                            "message": f"Rule '{rule}:' has no patterns.",
                            "line": idx,
                        }
                    )
                elif rule == "include":
                    include_patterns.extend(patterns)
                elif rule == "ignore":
                    ignore_patterns.extend(patterns)
                continue

            if TDocProjectIndex._is_section_header(line):
                m = SECTION_HEADER_PATTERN.match(line)
                section = m.group("section").strip() if m else ""
                if not section:
                    findings.append(
                        {"severity": "error", "message": "Empty section header.", "line": idx}
                    )
                    continue
                if section.casefold() in section_line:
                    findings.append(
                        {
                            "severity": "warning",
                            "message": f"Duplicate section header '{section}'.",
                            "line": idx,
                        }
                    )
                else:
                    section_line[section.casefold()] = idx
                lead = section[0]
                if lead.isalpha() and lead != lead.upper():
                    findings.append(
                        {
                            "severity": "warning",
                            "message": f"Section header '{section}' should begin with a capital letter.",
                            "line": idx,
                        }
                    )
                section_count[section.casefold()] += 0
                current_section = section
                continue

            if "=" in line and not ALIAS_LINE_PATTERN.match(line):
                findings.append(
                    {
                        "severity": "error",
                        "message": "Malformed alias definition. Use 'Canonical = Alias1 | Alias2'.",
                        "line": idx,
                    }
                )
                continue

            symbol, alias_items, _, metadata_issues = TDocProjectIndex._parse_symbol_definition(line)
            if not symbol:
                findings.append({"severity": "error", "message": "Empty symbol definition.", "line": idx})
                continue
            for issue in metadata_issues:
                findings.append({"severity": "warning", "message": issue, "line": idx})

            if current_section:
                section_count[current_section.casefold()] += 1

            symbol_key = symbol.casefold()
            if symbol_key in symbol_line:
                findings.append(
                    {
                        "severity": "error",
                        "message": f"Duplicate canonical symbol '{symbol}'.",
                        "line": idx,
                    }
                )
            else:
                symbol_line[symbol_key] = idx

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
                            "line": idx,
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
                            "line": idx,
                        }
                    )
                    continue

                alias_owner[key] = {"symbol": symbol, "line": idx}

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
        _, unresolved_refs, _, frontmatter_issues = TDocProjectIndex.collect_symbol_references(
            root_path,
            alias_to_symbol,
            effective_includes,
            effective_ignores,
            content_overrides=normalized_overrides,
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
            sample = ", ".join(f"{p}#L{ln}" for p, ln in refs[:3])
            extra = f" (+{len(refs) - 3} more)" if len(refs) > 3 else ""
            findings.append(
                {
                    "severity": "warning",
                    "message": f"Unresolved symbol '{unresolved}' used at {sample}{extra}.",
                    "line": None,
                }
            )

        seen_missing_images = set()
        for path, rel_path in TDocProjectIndex.iter_doc_paths(root_path, effective_includes, effective_ignores):
            content, _err = TDocProjectIndex._read_text_with_overrides(path, normalized_overrides)
            if content is None:
                continue
            _, body_lines, body_start_line, _ = parse_doc_frontmatter(content)
            for offset, line in enumerate(body_lines):
                line_no = body_start_line + offset
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
                            "file": str(rel_path),
                        }
                    )

        return findings

    @staticmethod
    def _group_refs_by_file(refs):
        grouped = defaultdict(set)
        for rel_path, line_no in refs:
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

        lines = ["Index"]
        indent = " " * 4

        if not symbol_refs and not unresolved_refs:
            lines.append(f"{indent}No symbols indexed yet.")
        else:
            section_to_symbols = defaultdict(list)
            for symbol in sorted(symbol_refs.keys(), key=str.casefold):
                section = symbol_to_section.get(symbol) or "Uncategorized"
                section_to_symbols[section].append(symbol)

            for section in sorted(section_to_symbols.keys(), key=str.casefold):
                lines.append(f"{indent}{section}:")
                for symbol in section_to_symbols[section]:
                    refs = sorted(symbol_refs[symbol], key=lambda x: (x[0].casefold(), x[1]))
                    aliases = symbol_to_aliases.get(symbol, [symbol])
                    metadata = symbol_to_metadata.get(symbol, {})
                    lines.append(f"{indent * 2}[{symbol}]")
                    lines.append(f"{indent * 3}Aliases: " + ", ".join(f"[{alias}]" for alias in aliases))
                    if metadata:
                        lines.append(
                            f"{indent * 3}Metadata: " + "; ".join(f"{k}={v}" for k, v in metadata.items())
                        )
                    lines.append(f"{indent * 3}References:")
                    for rel_path, line_numbers in TDocProjectIndex._group_refs_by_file(refs):
                        line_links = ", ".join(f"[{ln}|{rel_path}#L{ln}]" for ln in line_numbers)
                        lines.append(f"{indent * 4}[{rel_path}]: {line_links}")
                    lines.append("")
                lines.append("")

            if unresolved_refs:
                lines.append(f"{indent}Unresolved:")
                lines.append(f"{indent * 2}These symbols are used but not defined in .tdocproject.")
                for unresolved in sorted(unresolved_refs.keys(), key=str.casefold):
                    refs = sorted(unresolved_refs[unresolved], key=lambda x: (x[0].casefold(), x[1]))
                    lines.append(f"{indent * 2}[{unresolved}]")
                    lines.append(f"{indent * 3}References:")
                    for rel_path, line_numbers in TDocProjectIndex._group_refs_by_file(refs):
                        line_links = ", ".join(f"[{ln}|{rel_path}#L{ln}]" for ln in line_numbers)
                        lines.append(f"{indent * 4}[{rel_path}]: {line_links}")
                    lines.append("")
                lines.append("")

        if doc_metadata:
            lines.append(f"{indent}Documents:")
            for rel_path in sorted(doc_metadata.keys(), key=str.casefold):
                metadata = doc_metadata[rel_path]
                lines.append(f"{indent * 2}[{rel_path}]")
                if metadata:
                    lines.append(f"{indent * 3}Metadata: " + "; ".join(f"{k}={v}" for k, v in metadata.items()))
                else:
                    lines.append(f"{indent * 3}Metadata: (none)")
                lines.append(f"{indent * 3}Indexing: " + ("on" if is_index_enabled(metadata) else "off"))
            lines.append("")
            lines.append("")

        if section_header_warnings:
            lines.append(f"{indent}Project Warnings:")
            for issue in section_header_warnings:
                lines.append(
                    f"{indent * 2}{PROJECT_MARKER_FILENAME}#L{issue['line']} {issue['message']}"
                )
            lines.append("")
            lines.append("")

        if frontmatter_issues:
            lines.append(f"{indent}Frontmatter Warnings:")
            for issue in frontmatter_issues:
                lines.append(f"{indent * 2}[{issue['file']}#L{issue['line']}] {issue['message']}")
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

        self.find_edit = QLineEdit(self)
        self.find_edit.setPlaceholderText("Find")
        self.find_edit.installEventFilter(self)

        self.replace_edit = QLineEdit(self)
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


class TDocEditorWidget(QTextEdit):
    editorFontSizeStepRequested = Signal(int)  # +1 / -1
    LINK_PROPERTY = QTextCharFormat.UserProperty + 1
    LINK_LABEL_PROPERTY = QTextCharFormat.UserProperty + 2
    LINK_RAW_PROPERTY = QTextCharFormat.UserProperty + 3
    IMAGE_RAW_PROPERTY = QTextCharFormat.UserProperty + 4
    IMAGE_PATH_PROPERTY = QTextCharFormat.UserProperty + 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        font = QFont("Consolas", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._editor_background_color = QColor("#252526")
        self._editor_background_image_path = ""
        self._editor_background_scale_mode = "stretch"
        self._editor_background_image_brightness = 100
        self._editor_background_tint_color = QColor("#000000")
        self._editor_background_tint_strength = 0
        self._editor_background_source_pixmap: QPixmap | None = None
        self._editor_background_cache_size = QSize()
        self._editor_background_cache_pixmap: QPixmap | None = None
        self._apply_editor_background_palette()

        self._lint_visual_cfg = dict(_TDOC_LINT_VISUAL_DEFAULTS)
        self._overview_cfg = dict(_TDOC_OVERVIEW_MARKER_DEFAULTS)
        self._lint_diagnostics: list[dict] = []
        self._lint_line_severity: dict[int, str] = {}
        self._lint_selections: list[QTextEdit.ExtraSelection] = []
        self._overview_search_lines: set[int] = set()
        self._overview_active_search_lines: set[int] = set()
        self._overview_occurrence_lines: set[int] = set()
        self._overview_occurrence_term = ""

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
        self.textChanged.connect(self._on_text_changed_search_refresh)
        self.textChanged.connect(self._schedule_occurrence_marker_refresh)

        self.overviewMarkerArea = _TDocOverviewMarkerArea(self)
        self.verticalScrollBar().rangeChanged.connect(self._on_scrollbar_range_changed)
        self.verticalScrollBar().valueChanged.connect(self._refresh_overview_marker_area)
        self.horizontalScrollBar().rangeChanged.connect(self._on_scrollbar_range_changed)
        self.cursorPositionChanged.connect(self.highlightCurrentLine)
        self.cursorPositionChanged.connect(self._on_cursor_position_changed_for_link_editing)
        self.cursorPositionChanged.connect(self._schedule_occurrence_marker_refresh)
        self.selectionChanged.connect(self._schedule_occurrence_marker_refresh)

        self._is_internal_change = False
        self._last_cursor_pos = int(self.textCursor().position())
        self.open_file_by_name = None
        self.open_symbol = None
        self.resolve_image_path = None
        self.resolve_symbol = None
        self.rename_alias = None
        self.normalize_symbol = None
        self._apply_viewport_margins()
        self._position_search_bar()
        self.highlightCurrentLine()
        self._schedule_occurrence_marker_refresh()

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

    def _handle_editor_shortcut_fallback(self, event: QKeyEvent) -> bool:
        if event.matches(QKeySequence.Find):
            self.show_find_bar()
            return True
        if event.matches(QKeySequence.Replace):
            self.show_replace_bar()
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

    def _search_query(self) -> str:
        return str(self._search_bar.find_edit.text() or "")

    def _replace_query(self) -> str:
        return str(self._search_bar.replace_edit.text() or "")

    def _search_top_margin(self) -> int:
        if not self._search_bar.isVisible():
            return 0
        return max(30, int(self._search_bar.sizeHint().height()))

    def overviewMarkerAreaWidth(self) -> int:
        if not bool(self._overview_cfg.get("enabled", True)):
            return 0
        try:
            return max(6, int(self._overview_cfg.get("width", 10)))
        except Exception:
            return 10

    def set_overview_markers_enabled(self, enabled: bool) -> None:
        self._overview_cfg["enabled"] = bool(enabled)
        self._apply_viewport_margins()
        self._refresh_overview_marker_area()

    def _apply_viewport_margins(self):
        top_margin = self._search_top_margin()
        right_margin = self.overviewMarkerAreaWidth()
        self.setViewportMargins(0, top_margin, right_margin, 0)
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
                vp.right() + 1,
                vp.top(),
                width,
                max(0, vp.height()),
            )
        )
        self.overviewMarkerArea.show()
        self.overviewMarkerArea.raise_()

    def _on_scrollbar_range_changed(self, *_args):
        self._position_overview_marker_area()
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
            self._refresh_overview_marker_area()
            return

        source = self.toPlainText()
        if not source:
            self._overview_occurrence_term = ""
            self._overview_occurrence_lines = set()
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
            self._refresh_overview_marker_area()
            return

        lines: set[int] = set()
        count = 0
        for match in regex.finditer(source):
            start = int(match.start())
            end = int(match.end())
            if end <= start:
                continue
            lines.update(self._line_numbers_for_span(start, end))
            count += 1
            if count >= max_matches:
                break

        self._overview_occurrence_term = term
        self._overview_occurrence_lines = lines
        self._refresh_overview_marker_area()

    def _occurrence_pattern_from_cursor(self) -> tuple[str, str, int]:
        cur = self.textCursor()
        selected = str(cur.selectedText() or "").replace("\u2029", "\n")
        if selected and "\n" not in selected:
            token = selected
            if len(token) < 2:
                return "", "", 0
            if re.match(r"^[A-Za-z_]\w*$", token):
                return token, rf"\b{re.escape(token)}\b", 0
            return token, re.escape(token), 0

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
            normalized.append({"line": max(1, line), "severity": sev})
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

    def _severity_rank(self, severity: str) -> int:
        if severity == "error":
            return 3
        if severity == "warning":
            return 2
        return 1

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

    def _rebuild_lint_selections(self):
        selections: list[QTextEdit.ExtraSelection] = []
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

    def _rebuild_extra_selections(self):
        extra_selections = list(self._lint_selections)
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
    ) -> None:
        base = self._resolve_background_color(background_color, "#252526")
        tint = self._resolve_background_color(background_tint_color, "#000000")
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
        )
        if not changed:
            return

        self._editor_background_color = base
        self._editor_background_tint_color = tint
        self._editor_background_scale_mode = scale_mode
        self._editor_background_image_brightness = brightness
        self._editor_background_tint_strength = tint_strength
        self._editor_background_image_path = image_path
        self._editor_background_source_pixmap = source_pixmap
        self._editor_background_cache_size = QSize()
        self._editor_background_cache_pixmap = None
        self._apply_editor_background_palette()
        self._rebuild_extra_selections()
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
        self._position_overview_marker_area()
        self._refresh_overview_marker_area()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if dx != 0 or dy != 0:
            self._refresh_overview_marker_area()

    def paintEvent(self, event):
        background_painter = QPainter(self.viewport())
        self._paint_editor_background_layer(background_painter, event.rect())
        background_painter.end()
        super().paintEvent(event)

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

    def load_tdoc(self, text):
        """Parses [Label] links and renders only label text as clickable."""
        self._is_internal_change = True
        self.blockSignals(True)
        self.clear()

        cursor = self.textCursor()
        cursor.beginEditBlock()

        last_pos = 0
        for match in INLINE_TOKEN_PATTERN.finditer(text):
            if match.start() < last_pos:
                continue

            pre_text = text[last_pos:match.start()]
            if pre_text:
                cursor.insertText(pre_text, QTextCharFormat())

            raw_image = match.group("image")
            if raw_image is not None:
                if "\n" in raw_image or not self._insert_inline_image_from_tag(cursor, raw_image):
                    cursor.insertText(match.group(0), QTextCharFormat())
                last_pos = match.end()
                continue
            raw_label = match.group("link") or ""
            if "\n" in raw_label:
                cursor.insertText(match.group(0), QTextCharFormat())
                last_pos = match.end()
                continue
            shown = link_display_text(raw_label)
            if not shown:
                cursor.insertText(match.group(0), QTextCharFormat())
                last_pos = match.end()
                continue

            cursor.insertText(shown, self._make_link_char_format(raw_label))
            last_pos = match.end()

        if last_pos < len(text):
            cursor.insertText(text[last_pos:], QTextCharFormat())

        cursor.endEditBlock()
        self.document().setModified(False)
        self.blockSignals(False)
        self._is_internal_change = False
        self._last_cursor_pos = int(self.textCursor().position())
        self._rebuild_extra_selections()
        if self._search_bar.isVisible():
            self._schedule_search_refresh(immediate=True)
        self._schedule_occurrence_marker_refresh()

    def save_tdoc(self):
        """Serializes document back to plain [Label] TDoc syntax."""
        output = []
        doc = self.document()
        block = doc.begin()

        while block.isValid():
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
                self._is_internal_change = was_internal
            self._last_cursor_pos = int(self.textCursor().position())
            return True
        return False

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
            self._is_internal_change = was_internal
        self._last_cursor_pos = int(self.textCursor().position())
        return True

    def _on_cursor_position_changed_for_link_editing(self) -> None:
        if bool(self._is_internal_change):
            self._last_cursor_pos = int(self.textCursor().position())
            return
        old_pos = int(self._last_cursor_pos)
        new_pos = int(self.textCursor().position())
        if old_pos != new_pos:
            self._collapse_bracket_link_on_cursor_move(old_pos, new_pos)
        self._expand_link_for_editing(self.textCursor())
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

        if isinstance(target, str) and target.startswith("symbol:"):
            label = self._get_link_label_at(cursor)
            if label:
                menu.addSeparator()
                if callable(self.rename_alias):
                    rename_action = menu.addAction("Rename Alias...")
                    rename_action.triggered.connect(lambda: self.rename_alias(label))
                if callable(self.normalize_symbol):
                    normalize_action = menu.addAction("Normalize This Symbol")
                    normalize_action.triggered.connect(lambda: self.normalize_symbol(label))

        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen is action_find:
            self.show_find_bar()
            return
        if chosen is action_replace:
            self.show_replace_bar()
            return

    def mouseMoveEvent(self, e):
        cursor = self.cursorForPosition(e.position().toPoint())
        target = self._get_link_target_at(cursor)
        self.viewport().setCursor(Qt.PointingHandCursor if target else Qt.IBeamCursor)
        super().mouseMoveEvent(e)

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

    def keyPressEvent(self, event):
        text = str(event.text() or "")
        mods = event.modifiers()
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
