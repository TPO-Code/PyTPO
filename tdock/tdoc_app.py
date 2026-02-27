import sys
import os
import re
import fnmatch
from collections import defaultdict
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QTreeView,
    QFileSystemModel,
    QTabWidget,
    QTextEdit,
    QInputDialog,
    QMessageBox,
    QFileDialog,
    QMenu,
    QSplitter,
)
from PySide6.QtGui import QAction, QFont, QTextCursor, QTextCharFormat, QColor, QKeySequence
from PySide6.QtCore import Qt

PROJECT_MARKER_FILENAME = ".tdocproject"
INDEX_FILENAME = "index.tdoc"
DOC_SUFFIX = ".tdoc"
INDEX_AUTO_START = "<!-- TDOC:AUTO START -->"
INDEX_AUTO_END = "<!-- TDOC:AUTO END -->"

LINK_PATTERN = re.compile(r"\[(?P<label>[^\[\]\n]+?)\]")
ALIAS_LINE_PATTERN = re.compile(r"^(?P<symbol>[^=#]+?)\s*=\s*(?P<aliases>.*)$")
SECTION_HEADER_PATTERN = re.compile(r"^(?P<section>[^=#].*?)\s*:\s*$")
FILE_LINK_PATTERN = re.compile(r"^(?P<path>.+?\.tdoc)(?:#L(?P<line>\d+))?$", re.IGNORECASE)
RULE_LINE_PATTERN = re.compile(r"^(?P<rule>include|ignore)\s*:\s*(?P<patterns>.*)$", re.IGNORECASE)
FRONTMATTER_KV_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(?P<value>.*)$")


def parse_file_link(label):
    """Parses 'path.tdoc' or 'path.tdoc#L42'."""
    cleaned = label.strip()
    m = FILE_LINK_PATTERN.match(cleaned)
    if not m:
        return None, None
    path = m.group("path").strip()
    line = int(m.group("line")) if m.group("line") else None
    return path, line


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
            label = match.group("label")
            cleaned = label.strip()
            if cleaned.casefold() != old_cf:
                return match.group(0)
            file_path, _ = parse_file_link(cleaned)
            if file_path:
                return match.group(0)
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
    def collect_symbol_references(root_path, alias_to_symbol, include_patterns=None, ignore_patterns=None):
        symbol_refs = defaultdict(set)
        unresolved_refs = defaultdict(set)
        doc_metadata = {}
        frontmatter_issues = []

        for path, rel_path in TDocProjectIndex.iter_doc_paths(root_path, include_patterns, ignore_patterns):
            try:
                content = path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"Error reading {path}: {e}")
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
                    label = match.group("label").strip()
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
                label = match.group("label").strip()
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
    def validate_project(root_path):
        findings = []
        marker = TDocProjectIndex.marker_path(root_path)

        if not marker.exists():
            findings.append(
                {
                    "severity": "error",
                    "message": f"Missing project marker file '{PROJECT_MARKER_FILENAME}'.",
                    "line": None,
                }
            )
            return findings

        try:
            lines = marker.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            findings.append(
                {
                    "severity": "error",
                    "message": f"Cannot read {PROJECT_MARKER_FILENAME}: {e}",
                    "line": None,
                }
            )
            return findings

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
            root_path, alias_to_symbol, effective_includes, effective_ignores
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

        return findings

    @staticmethod
    def build_index(root_path):
        """Generates index.tdoc at project root if .tdocproject marker exists."""
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

        lines = [
            "# Project Index",
            "",
            "Auto-generated from [symbol] links with line references.",
            "",
        ]

        if not symbol_refs and not unresolved_refs:
            lines.append("No symbols indexed yet.")
        else:
            section_to_symbols = defaultdict(list)
            for symbol in sorted(symbol_refs.keys(), key=str.casefold):
                section = symbol_to_section.get(symbol) or "Uncategorized"
                section_to_symbols[section].append(symbol)

            for section in sorted(section_to_symbols.keys(), key=str.casefold):
                lines.append(f"## {section}")
                lines.append("")
                for symbol in section_to_symbols[section]:
                    refs = sorted(symbol_refs[symbol], key=lambda x: (x[0].casefold(), x[1]))
                    aliases = symbol_to_aliases.get(symbol, [symbol])
                    metadata = symbol_to_metadata.get(symbol, {})
                    lines.append(f"### [{symbol}]")
                    lines.append("Aliases: " + ", ".join(f"[{alias}]" for alias in aliases))
                    if metadata:
                        lines.append("Metadata: " + "; ".join(f"{k}={v}" for k, v in metadata.items()))
                    lines.append("References: " + ", ".join(f"[{p}#L{ln}]" for p, ln in refs))
                    lines.append("")

            if unresolved_refs:
                lines.append("## Unresolved")
                lines.append("")
                lines.append("These symbols are used but not defined in .tdocproject.")
                lines.append("")
                for unresolved in sorted(unresolved_refs.keys(), key=str.casefold):
                    refs = sorted(unresolved_refs[unresolved], key=lambda x: (x[0].casefold(), x[1]))
                    lines.append(f"### [{unresolved}]")
                    lines.append("References: " + ", ".join(f"[{p}#L{ln}]" for p, ln in refs))
                    lines.append("")

        if doc_metadata:
            lines.append("## Documents")
            lines.append("")
            for rel_path in sorted(doc_metadata.keys(), key=str.casefold):
                metadata = doc_metadata[rel_path]
                lines.append(f"### [{rel_path}]")
                if metadata:
                    lines.append("Metadata: " + "; ".join(f"{k}={v}" for k, v in metadata.items()))
                else:
                    lines.append("Metadata: (none)")
                lines.append("Indexing: " + ("on" if is_index_enabled(metadata) else "off"))
                lines.append("")

        if frontmatter_issues:
            lines.append("## Frontmatter Warnings")
            lines.append("")
            for issue in frontmatter_issues:
                lines.append(f"- [{issue['file']}#L{issue['line']}] {issue['message']}")
            lines.append("")

        index_path = TDocProjectIndex.index_path(root)
        auto_block = [INDEX_AUTO_START, ""]
        auto_block.extend(lines)
        auto_block.extend(["", INDEX_AUTO_END])
        auto_text = "\n".join(auto_block).rstrip() + "\n"

        existing = ""
        if index_path.exists():
            try:
                existing = index_path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"Error reading {index_path}: {e}")
                return None

        try:
            if not existing.strip():
                merged = auto_text
            else:
                start = existing.find(INDEX_AUTO_START)
                end = existing.find(INDEX_AUTO_END)
                if start != -1 and end != -1 and start < end:
                    end_after = end + len(INDEX_AUTO_END)
                    merged = existing[:start] + auto_text + existing[end_after:]
                else:
                    # Keep existing content as manual notes and append managed auto block.
                    merged = existing.rstrip() + "\n\n" + auto_text

            index_path.write_text(merged.rstrip() + "\n", encoding="utf-8")
        except Exception as e:
            print(f"Error writing {index_path}: {e}")
            return None

        return index_path


class TDocEditorWidget(QTextEdit):
    LINK_PROPERTY = QTextCharFormat.UserProperty + 1
    LINK_LABEL_PROPERTY = QTextCharFormat.UserProperty + 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setFont(QFont("Consolas", 11))
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        self._is_internal_change = False
        self.open_file_by_name = None
        self.open_symbol = None
        self.resolve_symbol = None
        self.rename_alias = None
        self.normalize_symbol = None

    def _make_link_target(self, label):
        cleaned = label.strip()
        file_path, line_no = parse_file_link(cleaned)
        if file_path:
            if line_no:
                return f"file:{file_path}#L{line_no}"
            return f"file:{file_path}"

        symbol = cleaned
        if callable(self.resolve_symbol):
            symbol = self.resolve_symbol(cleaned)
        return f"symbol:{symbol}"

    def load_tdoc(self, text):
        """Parses [Label] links and renders only label text as clickable."""
        self._is_internal_change = True
        self.blockSignals(True)
        self.clear()

        cursor = self.textCursor()
        cursor.beginEditBlock()

        last_pos = 0
        for match in LINK_PATTERN.finditer(text):
            if match.start() < last_pos:
                continue

            pre_text = text[last_pos:match.start()]
            if pre_text:
                cursor.insertText(pre_text, QTextCharFormat())

            label = match.group("label")
            if "\n" in label:
                cursor.insertText(match.group(0), QTextCharFormat())
                last_pos = match.end()
                continue

            fmt = QTextCharFormat()
            fmt.setForeground(QColor("blue"))
            fmt.setFontUnderline(True)
            fmt.setProperty(self.LINK_PROPERTY, self._make_link_target(label))
            fmt.setProperty(self.LINK_LABEL_PROPERTY, label.strip())

            cursor.insertText(label, fmt)
            last_pos = match.end()

        if last_pos < len(text):
            cursor.insertText(text[last_pos:], QTextCharFormat())

        cursor.endEditBlock()
        self.document().setModified(False)
        self.blockSignals(False)
        self._is_internal_change = False

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

                if fmt.hasProperty(self.LINK_PROPERTY):
                    output.append(f"[{text}]")
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

    def _show_context_menu(self, pos):
        menu = self.createStandardContextMenu()
        cursor = self.cursorForPosition(pos)
        target = self._get_link_target_at(cursor)

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

        menu.exec(self.viewport().mapToGlobal(pos))

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

        super().mousePressEvent(e)


class MainWindow(QMainWindow):
    def __init__(self, root_dir):
        super().__init__()
        self.root_dir = ""
        self.alias_to_symbol = {}
        self.symbol_to_aliases = {}
        self.symbol_to_section = {}
        self.symbol_to_metadata = {}
        self.include_patterns = []
        self.ignore_patterns = []

        self.setWindowTitle("TDoc Editor Prototype")
        self.resize(1000, 700)

        self.setup_ui()
        self.setup_actions()

        self.model = QFileSystemModel()
        self.tree.setModel(self.model)
        for i in range(1, 4):
            self.tree.hideColumn(i)

        self.load_project(root_dir, prompt_unsaved=False)

    def setup_ui(self):
        main_widget = QWidget()
        main_layout = QHBoxLayout(main_widget)
        self.setCentralWidget(main_widget)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.doubleClicked.connect(self.on_file_double_clicked)
        splitter.addWidget(self.tree)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        splitter.addWidget(self.tabs)

        splitter.setSizes([230, 770])

    def setup_actions(self):
        file_menu = self.menuBar().addMenu("File")

        open_project_act = QAction("Open Project...", self)
        open_project_act.setShortcut(QKeySequence.Open)
        open_project_act.triggered.connect(self.open_project_dialog)
        file_menu.addAction(open_project_act)

        save_act = QAction("Save", self)
        save_act.setShortcut(QKeySequence.Save)
        save_act.triggered.connect(self.save_current_tab)
        file_menu.addAction(save_act)

        save_all_act = QAction("Save All", self)
        save_all_act.triggered.connect(self.save_all)
        file_menu.addAction(save_all_act)

        tools_menu = self.menuBar().addMenu("Tools")
        index_act = QAction("Index Project", self)
        index_act.triggered.connect(self.generate_project_index)
        tools_menu.addAction(index_act)

        validate_act = QAction("Validate Project", self)
        validate_act.triggered.connect(self.validate_project)
        tools_menu.addAction(validate_act)

        normalize_act = QAction("Normalize One Symbol to Canonical...", self)
        normalize_act.triggered.connect(lambda: self.normalize_symbol_to_canonical())
        tools_menu.addAction(normalize_act)

        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_tree_context_menu)

    def has_unsaved_tabs(self):
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith("*"):
                return True
        return False

    def confirm_switch_project(self):
        if not self.has_unsaved_tabs():
            return True

        res = QMessageBox.question(
            self,
            "Unsaved Changes",
            "Save changes before opening another project?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
        )
        if res == QMessageBox.Cancel:
            return False
        if res == QMessageBox.Yes:
            self.save_all()
        return True

    def load_project(self, root_dir, prompt_unsaved=True):
        project_root = os.path.abspath(str(root_dir))
        if not os.path.isdir(project_root):
            QMessageBox.warning(self, "Open Project", f"Not a directory:\n{project_root}")
            return False

        if prompt_unsaved and not self.confirm_switch_project():
            return False

        self.tabs.clear()
        self.root_dir = project_root

        self.model.setRootPath(self.root_dir)
        self.tree.setRootIndex(self.model.index(self.root_dir))

        self.refresh_aliases()
        self.generate_project_index(reload_if_open=False)
        self.setWindowTitle(f"TDoc Editor Prototype - {self.root_dir}")
        return True

    def open_project_dialog(self):
        start_dir = self.root_dir or os.getcwd()
        selected = QFileDialog.getExistingDirectory(self, "Open Project", start_dir)
        if selected:
            self.load_project(selected, prompt_unsaved=True)

    def refresh_aliases(self):
        (
            self.alias_to_symbol,
            self.symbol_to_aliases,
            self.symbol_to_section,
            self.include_patterns,
            self.ignore_patterns,
            self.symbol_to_metadata,
        ) = TDocProjectIndex.load_aliases(self.root_dir)

    def resolve_symbol(self, label):
        return TDocProjectIndex.resolve_symbol(label, self.alias_to_symbol)

    def _index_tab_path(self):
        return str(TDocProjectIndex.index_path(self.root_dir))

    def generate_project_index(self, reload_if_open=True):
        self.refresh_aliases()
        index_path = TDocProjectIndex.build_index(self.root_dir)
        if not index_path:
            return None

        index_path = str(index_path)
        if reload_if_open:
            self.reload_tab_from_disk(index_path)
        return index_path

    def validate_project(self):
        self.save_all()
        findings = TDocProjectIndex.validate_project(self.root_dir)

        if not findings:
            QMessageBox.information(self, "Validation", "No validation issues found.")
            return

        errors = [f for f in findings if f["severity"] == "error"]
        warnings = [f for f in findings if f["severity"] == "warning"]
        ordered = errors + warnings
        preview = ordered[:20]

        lines = [
            f"Validation complete: {len(errors)} error(s), {len(warnings)} warning(s).",
            "",
        ]
        for item in preview:
            where = f" (line {item['line']})" if item.get("line") else ""
            lines.append(f"- [{item['severity'].upper()}]{where} {item['message']}")
        if len(ordered) > len(preview):
            lines.append("")
            lines.append(f"... and {len(ordered) - len(preview)} more issue(s).")

        text = "\n".join(lines)
        if errors:
            QMessageBox.warning(self, "Validation", text)
        else:
            QMessageBox.information(self, "Validation", text)

    def normalize_symbol_to_canonical(self, symbol_or_alias=None):
        if not TDocProjectIndex.has_project_marker(self.root_dir):
            QMessageBox.information(
                self,
                "Normalize Symbol",
                "No project marker found. Create a .tdocproject file at the project root first.",
            )
            return

        self.save_all()
        self.refresh_aliases()

        current = (symbol_or_alias or "").strip()
        if not current:
            current, ok = QInputDialog.getText(
                self,
                "Normalize Symbol",
                "Enter symbol or alias to normalize:",
            )
            if not ok:
                return
            current = current.strip()

        if not current:
            QMessageBox.warning(self, "Normalize Symbol", "Symbol/alias cannot be empty.")
            return

        file_path, _ = parse_file_link(current)
        if file_path:
            return

        canonical_symbol = self.alias_to_symbol.get(current.casefold())
        if not canonical_symbol:
            QMessageBox.information(
                self,
                "Normalize Symbol",
                f"'{current}' is not defined in .tdocproject.",
            )
            return

        touched_files, replacements = TDocProjectIndex.normalize_symbol_in_documents(
            self.root_dir, self.alias_to_symbol, canonical_symbol
        )

        self.generate_project_index(reload_if_open=False)
        self.reload_all_open_tabs()

        QMessageBox.information(
            self,
            "Normalize Symbol",
            (
                f"Normalized '{canonical_symbol}'. Updated {replacements} link(s) "
                f"across {touched_files} file(s)."
            ),
        )

    def reload_tab_from_disk(self, path):
        for i in range(self.tabs.count()):
            if self.tabs.tabToolTip(i) != path:
                continue

            editor = self.tabs.widget(i)
            try:
                content = Path(path).read_text(encoding="utf-8")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
                return

            editor.load_tdoc(content)
            self.set_tab_dirty(editor, False)
            return

    def show_tree_context_menu(self, pos):
        menu = QMenu()
        new_file_act = menu.addAction("New .tdoc File")
        action = menu.exec(self.tree.mapToGlobal(pos))

        if action == new_file_act:
            name, ok = QInputDialog.getText(self, "New File", "Filename (.tdoc):")
            if ok and name:
                if not name.endswith(DOC_SUFFIX):
                    name += DOC_SUFFIX
                path = os.path.join(self.root_dir, name)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("New document.\n")
                self.open_file(path)

    def on_file_double_clicked(self, index):
        path = self.model.filePath(index)
        if path.endswith(DOC_SUFFIX):
            self.open_file(path)

    def open_file(self, path, jump_symbol=None, jump_line=None):
        for i in range(self.tabs.count()):
            if self.tabs.tabToolTip(i) == path:
                self.tabs.setCurrentIndex(i)
                if jump_symbol:
                    self.jump_to_symbol(self.tabs.widget(i), jump_symbol)
                if jump_line:
                    self.jump_to_line(self.tabs.widget(i), jump_line)
                return

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        editor = TDocEditorWidget()
        editor.resolve_symbol = self.resolve_symbol
        editor.open_file_by_name = self.open_file_relative
        editor.open_symbol = self.open_symbol_in_index
        editor.rename_alias = self.rename_alias_across_project
        editor.normalize_symbol = self.normalize_symbol_to_canonical
        editor.load_tdoc(content)
        editor.textChanged.connect(lambda: self.set_tab_dirty(editor, True))

        filename = os.path.basename(path)
        idx = self.tabs.addTab(editor, filename)
        self.tabs.setTabToolTip(idx, path)
        self.tabs.setCurrentIndex(idx)
        editor.setFocus()

        if jump_symbol:
            self.jump_to_symbol(editor, jump_symbol)
        if jump_line:
            self.jump_to_line(editor, jump_line)

    def jump_to_symbol(self, editor, symbol):
        cursor = editor.document().find(f"### [{symbol}]")
        if cursor.isNull():
            cursor = editor.document().find(f"## [{symbol}]")
        if cursor.isNull():
            cursor = editor.document().find(symbol)
        if not cursor.isNull():
            editor.setTextCursor(cursor)
            editor.ensureCursorVisible()

    def jump_to_line(self, editor, line_number):
        if line_number is None or line_number < 1:
            return
        block = editor.document().findBlockByNumber(line_number - 1)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()

    def open_symbol_in_index(self, symbol):
        index_path = self.generate_project_index(reload_if_open=True)
        if not index_path:
            QMessageBox.information(
                self,
                "Index Unavailable",
                "No project marker found. Create a .tdocproject file at the project root first.",
            )
            return

        self.open_file(index_path, jump_symbol=symbol)

    def reload_all_open_tabs(self):
        paths = [self.tabs.tabToolTip(i) for i in range(self.tabs.count())]
        for path in paths:
            self.reload_tab_from_disk(path)

    def rename_alias_across_project(self, old_alias):
        old_alias = old_alias.strip()
        if not old_alias:
            return

        file_path, _ = parse_file_link(old_alias)
        if file_path:
            return

        if not TDocProjectIndex.has_project_marker(self.root_dir):
            QMessageBox.information(
                self,
                "Rename Alias",
                "No project marker found. Create a .tdocproject file at the project root first.",
            )
            return

        new_alias, ok = QInputDialog.getText(
            self,
            "Rename Alias",
            f"Rename alias '{old_alias}' to:",
            text=old_alias,
        )
        if not ok:
            return

        new_alias = new_alias.strip()
        if not new_alias:
            QMessageBox.warning(self, "Rename Alias", "Alias cannot be empty.")
            return
        if new_alias.lower().endswith(DOC_SUFFIX):
            QMessageBox.warning(self, "Rename Alias", "Alias cannot end with .tdoc.")
            return
        if new_alias.casefold() == old_alias.casefold():
            return

        self.save_all()

        marker_changed = TDocProjectIndex.rename_alias_in_marker(self.root_dir, old_alias, new_alias)
        docs_changed = TDocProjectIndex.rename_alias_in_documents(self.root_dir, old_alias, new_alias)

        if not marker_changed and docs_changed == 0:
            QMessageBox.information(self, "Rename Alias", f"No matches found for '{old_alias}'.")
            return

        self.refresh_aliases()
        self.generate_project_index(reload_if_open=False)
        self.reload_all_open_tabs()

        QMessageBox.information(
            self,
            "Rename Alias",
            f"Renamed '{old_alias}' to '{new_alias}'. Updated {docs_changed} document file(s).",
        )

    def open_file_relative(self, target):
        file_target, jump_line = parse_file_link(target)
        path = os.path.join(self.root_dir, file_target or target)
        if not os.path.exists(path):
            res = QMessageBox.question(
                self,
                "Missing File",
                f"File {target} does not exist. Create it?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if res == QMessageBox.Yes:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"Created for file link {target}.\n")
            else:
                return

        self.open_file(path, jump_line=jump_line)

    def save_current_tab(self):
        idx = self.tabs.currentIndex()
        if idx < 0:
            return

        self.save_tab(idx)
        self.generate_project_index(reload_if_open=True)

    def save_tab(self, idx):
        editor = self.tabs.widget(idx)
        path = self.tabs.tabToolTip(idx)
        content = editor.save_tdoc()

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self.set_tab_dirty(editor, False)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def save_all(self):
        for i in range(self.tabs.count()):
            self.save_tab(i)
        self.generate_project_index(reload_if_open=True)

    def close_tab(self, idx):
        editor = self.tabs.widget(idx)
        if self.tabs.tabText(idx).startswith("*"):
            res = QMessageBox.question(
                self,
                "Unsaved Changes",
                "Save changes before closing?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if res == QMessageBox.Cancel:
                return
            if res == QMessageBox.Yes:
                self.save_tab(idx)
                self.generate_project_index(reload_if_open=True)

        self.tabs.removeTab(idx)

    def set_tab_dirty(self, editor, dirty):
        idx = self.tabs.indexOf(editor)
        if idx < 0:
            return

        title = self.tabs.tabText(idx)
        if dirty and not title.startswith("*"):
            self.tabs.setTabText(idx, "*" + title)
        elif not dirty and title.startswith("*"):
            self.tabs.setTabText(idx, title[1:])


if __name__ == "__main__":
    app = QApplication(sys.argv)

    initial = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
    if os.path.isfile(initial):
        initial = os.path.dirname(initial)

    window = MainWindow(initial)
    window.show()

    exit_code = app.exec()

    sys.exit(exit_code)
