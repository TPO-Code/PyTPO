"""Structured theme compiler for `.qsst` theme files."""

from __future__ import annotations

from dataclasses import dataclass
import re
import tomllib
from pathlib import Path
from typing import Any


STRUCTURED_THEME_EXTENSION = ".qsst"
_TOKEN_REF_RE = re.compile(r"\$\{([A-Za-z0-9_.-]+)\}")


class ThemeCompileError(RuntimeError):
    """Raised when a structured theme cannot be parsed or compiled."""


@dataclass(frozen=True, slots=True)
class CompiledStructuredTheme:
    """Compiled stylesheet and resolved token values for a `.qsst` theme."""

    stylesheet: str
    resolved_tokens: dict[str, Any]


def compile_qsst_file(theme_path: str | Path) -> str:
    return compile_qsst_file_with_tokens(theme_path).stylesheet


def compile_qsst_file_with_tokens(theme_path: str | Path) -> CompiledStructuredTheme:
    path = Path(theme_path)
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise ThemeCompileError(f"Could not read theme file: {path}") from exc
    return compile_qsst_text_with_tokens(source, source_name=str(path))


def compile_qsst_text(theme_text: str, *, source_name: str = "<memory>") -> str:
    return compile_qsst_text_with_tokens(theme_text, source_name=source_name).stylesheet


def compile_qsst_text_with_tokens(theme_text: str, *, source_name: str = "<memory>") -> CompiledStructuredTheme:
    return _compile_qsst(theme_text, source_name=source_name)


def _compile_qsst(theme_text: str, *, source_name: str) -> CompiledStructuredTheme:
    try:
        data = tomllib.loads(str(theme_text or ""))
    except tomllib.TOMLDecodeError as exc:
        raise ThemeCompileError(f"{source_name}: invalid TOML ({exc})") from exc

    if not isinstance(data, dict):
        raise ThemeCompileError(f"{source_name}: theme root must be a TOML table.")

    rules_obj = data.get("rules")
    if not isinstance(rules_obj, list) or not rules_obj:
        raise ThemeCompileError(f"{source_name}: expected non-empty `[[rules]]` entries.")

    raw_tokens: dict[str, Any] = {}
    for key, value in data.items():
        if str(key) == "rules":
            continue
        _flatten_tokens(str(key), value, raw_tokens)

    resolved_tokens: dict[str, Any] = {}
    resolving: set[str] = set()

    def resolve_token(name: str) -> Any:
        key = str(name or "").strip()
        if not key:
            raise ThemeCompileError(f"{source_name}: empty token reference.")
        if key in resolved_tokens:
            return resolved_tokens[key]
        if key in resolving:
            chain = " -> ".join([*sorted(resolving), key])
            raise ThemeCompileError(f"{source_name}: cyclic token reference ({chain}).")
        if key not in raw_tokens:
            raise ThemeCompileError(f"{source_name}: unknown token `{key}`.")
        resolving.add(key)
        try:
            resolved = _resolve_value(raw_tokens[key], resolve_token=resolve_token, source_name=source_name)
            resolved_tokens[key] = resolved
            return resolved
        finally:
            resolving.discard(key)

    for token_name in list(raw_tokens.keys()):
        resolve_token(token_name)

    lines: list[str] = []
    theme_name = str(data.get("name") or "").strip()
    header_name = theme_name or Path(source_name).name
    lines.append(f"/* Generated from {header_name} ({source_name}) */")
    lines.append("/* Source format: .qsst */")
    lines.append("")

    for idx, rule_obj in enumerate(rules_obj, start=1):
        if not isinstance(rule_obj, dict):
            raise ThemeCompileError(f"{source_name}: rule #{idx} must be a table.")

        if bool(rule_obj.get("disabled", False)):
            continue

        selector = str(rule_obj.get("selector") or "").strip()
        if not selector:
            raise ThemeCompileError(f"{source_name}: rule #{idx} is missing `selector`.")

        properties = _rule_properties(rule_obj, source_name=source_name, rule_index=idx)
        if not properties:
            raise ThemeCompileError(f"{source_name}: rule #{idx} has no properties.")

        comment = str(rule_obj.get("comment") or "").strip()
        if comment:
            lines.append(f"/* {comment} */")
        lines.append(f"{selector} {{")
        for prop_name, prop_value in properties.items():
            name = str(prop_name or "").strip()
            if not name:
                raise ThemeCompileError(f"{source_name}: rule #{idx} has an empty property name.")
            resolved_value = _resolve_value(prop_value, resolve_token=resolve_token, source_name=source_name)
            lines.append(f"    {name}: {_to_qss_literal(resolved_value)};")
        lines.append("}")
        lines.append("")

    stylesheet = "\n".join(lines).rstrip() + "\n"
    return CompiledStructuredTheme(stylesheet=stylesheet, resolved_tokens=dict(resolved_tokens))


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


def _rule_properties(rule_obj: dict[str, Any], *, source_name: str, rule_index: int) -> dict[str, Any]:
    direct = rule_obj.get("properties")
    if direct is not None:
        if not isinstance(direct, dict):
            raise ThemeCompileError(
                f"{source_name}: rule #{rule_index} `properties` must be a table/dict."
            )
        return dict(direct)

    properties: dict[str, Any] = {}
    for key, value in rule_obj.items():
        if key in {"selector", "comment", "disabled", "properties"}:
            continue
        properties[str(key)] = value
    return properties


def _resolve_value(value: Any, *, resolve_token, source_name: str) -> Any:
    if isinstance(value, str):
        def _replace_token(match: re.Match[str]) -> str:
            token_name = str(match.group(1) or "").strip()
            if not token_name:
                raise ThemeCompileError(f"{source_name}: empty token reference.")
            resolved = resolve_token(token_name)
            return _to_qss_literal(resolved)

        return _TOKEN_REF_RE.sub(_replace_token, value)

    if isinstance(value, list):
        return [_resolve_value(item, resolve_token=resolve_token, source_name=source_name) for item in value]

    if isinstance(value, tuple):
        return tuple(_resolve_value(item, resolve_token=resolve_token, source_name=source_name) for item in value)

    if isinstance(value, dict):
        return {
            str(key): _resolve_value(item, resolve_token=resolve_token, source_name=source_name)
            for key, item in value.items()
        }

    return value


def _to_qss_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, "g")
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(_to_qss_literal(part) for part in value)
    if value is None:
        return ""
    return str(value)
