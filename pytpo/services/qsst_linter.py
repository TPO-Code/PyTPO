"""Custom diagnostics for `.qsst` theme files."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
import tomllib
from typing import Any, Callable


_TOKEN_REF_RE = re.compile(r"\$\{([A-Za-z0-9_.-]+)\}")
_TABLE_RE = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*$")
_ARRAY_TABLE_RE = re.compile(r"^\s*\[\[([A-Za-z0-9_.-]+)\]\]\s*$")
_KEY_ASSIGN_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=.*)$")


@dataclass(frozen=True, slots=True)
class _TokenDefinition:
    name: str
    line: int
    column: int
    end_column: int


@dataclass(frozen=True, slots=True)
class _TokenReference:
    name: str
    line: int
    column: int
    end_column: int


@dataclass(frozen=True, slots=True)
class _SourceIndex:
    token_definitions: dict[str, _TokenDefinition]
    token_references: list[_TokenReference]
    rule_header_lines: list[int]


def is_qsst_path(file_path: str | None) -> bool:
    return str(file_path or "").strip().lower().endswith(".qsst")


def collect_qsst_diagnostics(
    *,
    file_path: str,
    source_text: str | None = None,
    canonicalize: Callable[[str], str] | None = None,
    source: str = "qsst",
) -> list[dict]:
    cpath = canonicalize(file_path) if callable(canonicalize) else str(file_path or "")
    text = str(source_text or "")
    if source_text is None:
        try:
            with open(cpath, "r", encoding="utf-8") as handle:
                text = handle.read()
        except Exception as exc:
            return [
                _diag(
                    file_path=cpath,
                    code="read-error",
                    message=f"Could not read .qsst file ({exc}).",
                    severity="error",
                    source=source,
                    line=1,
                    column=1,
                )
            ]

    source_index = _index_qsst_source(text)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return [
                _diag(
                    file_path=cpath,
                    code="syntax-error",
                    message=str(exc),
                    severity="error",
                    source=source,
                    line=max(1, int(getattr(exc, "lineno", 1) or 1)),
                    column=max(1, int(getattr(exc, "colno", 1) or 1)),
                    end_column=max(1, int(getattr(exc, "colno", 1) or 1) + 1),
                )
            ]

    diagnostics: list[dict] = []
    if not isinstance(data, dict):
        diagnostics.append(
            _diag(
                file_path=cpath,
                code="invalid-root",
                message="Theme root must be a TOML table.",
                severity="error",
                source=source,
                line=1,
                column=1,
            )
        )
        return diagnostics

    rules_obj = data.get("rules")
    if not isinstance(rules_obj, list) or not rules_obj:
        diagnostics.append(
            _diag(
                file_path=cpath,
                code="missing-rules",
                message="Expected non-empty [[rules]] entries.",
                severity="error",
                source=source,
                line=1,
                column=1,
            )
        )
    else:
        for idx, rule_obj in enumerate(rules_obj, start=1):
            line = source_index.rule_header_lines[idx - 1] if idx - 1 < len(source_index.rule_header_lines) else 1
            if not isinstance(rule_obj, dict):
                diagnostics.append(
                    _diag(
                        file_path=cpath,
                        code="invalid-rule",
                        message=f"Rule #{idx} must be a TOML table.",
                        severity="error",
                        source=source,
                        line=line,
                        column=1,
                    )
                )
                continue
            selector = str(rule_obj.get("selector") or "").strip()
            if not selector:
                diagnostics.append(
                    _diag(
                        file_path=cpath,
                        code="missing-selector",
                        message=f"Rule #{idx} is missing `selector`.",
                        severity="error",
                        source=source,
                        line=line,
                        column=1,
                    )
                )
            if not _rule_properties(rule_obj):
                diagnostics.append(
                    _diag(
                        file_path=cpath,
                        code="rule-without-properties",
                        message=f"Rule #{idx} has no properties.",
                        severity="error",
                        source=source,
                        line=line,
                        column=1,
                    )
                )

    raw_tokens: dict[str, Any] = {}
    for key, value in data.items():
        if str(key) == "rules":
            continue
        _flatten_tokens(str(key), value, raw_tokens)
    known_tokens = set(raw_tokens.keys())

    seen_unknown_refs: set[tuple[str, int, int]] = set()
    ref_counts = Counter()
    for ref in source_index.token_references:
        if ref.name in known_tokens:
            ref_counts[ref.name] += 1
            continue
        marker = (ref.name, ref.line, ref.column)
        if marker in seen_unknown_refs:
            continue
        seen_unknown_refs.add(marker)
        diagnostics.append(
            _diag(
                file_path=cpath,
                code="unknown-token",
                message=f"Unknown token reference `{ref.name}`.",
                severity="error",
                source=source,
                line=ref.line,
                column=ref.column,
                end_column=ref.end_column,
                token_name=ref.name,
            )
        )

    cycle_tokens = _find_cyclic_tokens(raw_tokens)
    for token_name in sorted(cycle_tokens):
        definition = source_index.token_definitions.get(token_name)
        line = definition.line if definition is not None else 1
        column = definition.column if definition is not None else 1
        end_column = definition.end_column if definition is not None else column + max(1, len(token_name))
        diagnostics.append(
            _diag(
                file_path=cpath,
                code="cyclic-token",
                message=f"Token `{token_name}` participates in a cyclic reference.",
                severity="error",
                source=source,
                line=line,
                column=column,
                end_column=end_column,
                token_name=token_name,
            )
        )

    for token_name in sorted(known_tokens):
        if ref_counts.get(token_name, 0) > 0:
            continue
        definition = source_index.token_definitions.get(token_name)
        if definition is None:
            continue
        diagnostics.append(
            _diag(
                file_path=cpath,
                code="unused-token",
                message=f"Token `{token_name}` is never referenced.",
                severity="warning",
                source=source,
                line=definition.line,
                column=definition.column,
                end_column=definition.end_column,
                token_name=token_name,
            )
        )

    diagnostics.sort(
        key=lambda diag: (
            0 if str(diag.get("severity") or "") == "error" else 1,
            int(diag.get("line") or 1),
            int(diag.get("column") or 1),
            str(diag.get("message") or ""),
        )
    )
    return diagnostics


def remove_unused_qsst_token_from_text(source_text: str, token_name: str, *, line_hint: int = 0) -> tuple[str, str]:
    target = str(token_name or "").strip()
    if not target or "." not in target:
        return str(source_text or ""), "invalid-token"

    lines = str(source_text or "").splitlines(keepends=True)
    line_index = _find_token_definition_line(source_text, target)
    hint_idx = int(line_hint) - 1
    if 0 <= hint_idx < len(lines):
        hinted = _token_name_for_definition_line(source_text, hint_idx)
        if hinted == target:
            line_index = hint_idx
    if line_index < 0 or line_index >= len(lines):
        return str(source_text or ""), "not-found"

    del lines[line_index]
    updated = "".join(lines)
    if updated and not updated.endswith("\n"):
        updated += "\n"
    return updated, "removed"


def _diag(
    *,
    file_path: str,
    code: str,
    message: str,
    severity: str,
    source: str,
    line: int,
    column: int,
    end_column: int | None = None,
    token_name: str = "",
) -> dict:
    diag = {
        "file_path": str(file_path or ""),
        "code": str(code or ""),
        "message": str(message or ""),
        "severity": str(severity or "warning"),
        "source": str(source or "qsst"),
        "line": max(1, int(line or 1)),
        "column": max(1, int(column or 1)),
        "end_column": max(1, int(end_column or max(1, int(column or 1) + 1))),
    }
    if token_name:
        diag["token_name"] = str(token_name)
    return diag


def _index_qsst_source(source_text: str) -> _SourceIndex:
    token_definitions: dict[str, _TokenDefinition] = {}
    token_references: list[_TokenReference] = []
    rule_header_lines: list[int] = []
    namespace = ""
    in_array_table = False

    for idx, raw_line in enumerate(str(source_text or "").splitlines(), start=1):
        line = str(raw_line or "")
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            array_header = _ARRAY_TABLE_RE.match(stripped)
            if array_header is not None:
                in_array_table = True
                namespace = str(array_header.group(1) or "").strip()
                if namespace == "rules":
                    rule_header_lines.append(idx)
            else:
                table_header = _TABLE_RE.match(stripped)
                if table_header is not None:
                    in_array_table = False
                    namespace = str(table_header.group(1) or "").strip()
                else:
                    key_match = _KEY_ASSIGN_RE.match(line)
                    if key_match is not None and (not in_array_table) and namespace and namespace != "rules":
                        token_name = f"{namespace}.{str(key_match.group(2) or '').strip()}"
                        token_definitions.setdefault(
                            token_name,
                            _TokenDefinition(
                                name=token_name,
                                line=idx,
                                column=int(key_match.start(2)) + 1,
                                end_column=int(key_match.end(2)) + 1,
                            ),
                        )
        for match in _TOKEN_REF_RE.finditer(line):
            name = str(match.group(1) or "").strip()
            if not name:
                continue
            token_references.append(
                _TokenReference(
                    name=name,
                    line=idx,
                    column=int(match.start(1)) + 1,
                    end_column=int(match.end(1)) + 1,
                )
            )

    return _SourceIndex(
        token_definitions=token_definitions,
        token_references=token_references,
        rule_header_lines=rule_header_lines,
    )


def _find_token_definition_line(source_text: str, token_name: str) -> int:
    target = str(token_name or "").strip()
    if not target or "." not in target:
        return -1
    namespace = ""
    in_array_table = False
    for idx, raw_line in enumerate(str(source_text or "").splitlines(), start=0):
        line = str(raw_line or "")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        array_header = _ARRAY_TABLE_RE.match(stripped)
        if array_header is not None:
            in_array_table = True
            namespace = str(array_header.group(1) or "").strip()
            continue
        table_header = _TABLE_RE.match(stripped)
        if table_header is not None:
            in_array_table = False
            namespace = str(table_header.group(1) or "").strip()
            continue
        if in_array_table or not namespace or namespace == "rules":
            continue
        key_match = _KEY_ASSIGN_RE.match(line)
        if key_match is None:
            continue
        candidate = f"{namespace}.{str(key_match.group(2) or '').strip()}"
        if candidate == target:
            return idx
    return -1


def _token_name_for_definition_line(source_text: str, line_index: int) -> str:
    namespace = ""
    in_array_table = False
    for idx, raw_line in enumerate(str(source_text or "").splitlines(), start=0):
        line = str(raw_line or "")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        array_header = _ARRAY_TABLE_RE.match(stripped)
        if array_header is not None:
            in_array_table = True
            namespace = str(array_header.group(1) or "").strip()
            continue
        table_header = _TABLE_RE.match(stripped)
        if table_header is not None:
            in_array_table = False
            namespace = str(table_header.group(1) or "").strip()
            continue
        if idx != line_index or in_array_table or not namespace or namespace == "rules":
            continue
        key_match = _KEY_ASSIGN_RE.match(line)
        if key_match is None:
            return ""
        return f"{namespace}.{str(key_match.group(2) or '').strip()}"
    return ""


def _flatten_tokens(prefix: str, value: Any, out: dict[str, Any]) -> None:
    token_prefix = str(prefix or "").strip()
    if not token_prefix:
        return
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            child = str(child_key or "").strip()
            if not child:
                continue
            _flatten_tokens(f"{token_prefix}.{child}", child_value, out)
        return
    out[token_prefix] = value


def _rule_properties(rule_obj: dict[str, Any]) -> dict[str, Any]:
    direct = rule_obj.get("properties")
    if direct is not None:
        return dict(direct) if isinstance(direct, dict) else {}

    properties: dict[str, Any] = {}
    for key, value in rule_obj.items():
        if key in {"selector", "comment", "disabled", "properties"}:
            continue
        properties[str(key)] = value
    return properties


def _token_refs_in_value(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str):
        for match in _TOKEN_REF_RE.finditer(value):
            token_name = str(match.group(1) or "").strip()
            if token_name:
                refs.add(token_name)
        return refs
    if isinstance(value, (list, tuple)):
        for item in value:
            refs.update(_token_refs_in_value(item))
        return refs
    if isinstance(value, dict):
        for item in value.values():
            refs.update(_token_refs_in_value(item))
        return refs
    return refs


def _find_cyclic_tokens(raw_tokens: dict[str, Any]) -> set[str]:
    graph = {
        token_name: {ref for ref in _token_refs_in_value(token_value) if ref in raw_tokens}
        for token_name, token_value in raw_tokens.items()
    }
    state: dict[str, int] = {}
    stack: list[str] = []
    cycle_tokens: set[str] = set()

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for neighbor in graph.get(node, set()):
            neighbor_state = state.get(neighbor, 0)
            if neighbor_state == 0:
                visit(neighbor)
                continue
            if neighbor_state != 1:
                continue
            try:
                start = stack.index(neighbor)
            except ValueError:
                start = 0
            cycle_tokens.update(stack[start:])
        stack.pop()
        state[node] = 2

    for node in graph.keys():
        if state.get(node, 0) == 0:
            visit(node)
    return cycle_tokens
