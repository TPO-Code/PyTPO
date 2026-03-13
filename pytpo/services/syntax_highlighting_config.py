from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Callable

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
_TOKEN_KEY_RE = re.compile(r"^[a-z0-9_]+$")
_LANGUAGE_KEY_RE = re.compile(r"^[a-z0-9_+\-]+$")
_LANGUAGE_ALIASES: dict[str, str] = {
    "python": "python",
    "html": "html",
    "xml": "html",
    "javascript": "javascript",
    "javascriptreact": "javascript",
    "php": "php",
    "c": "cpp",
    "cpp": "cpp",
    "make": "cpp",
    "json": "json",
    "jsonc": "json",
    "toml": "toml",
    "rust": "rust",
    "css": "css",
    "scss": "css",
    "less": "css",
    "shell": "shell",
    "bash": "shell",
    "zsh": "shell",
    "markdown": "markdown",
    "todo": "todo",
}


def canonicalize_syntax_language(language_id: object) -> str:
    raw = str(language_id or "").strip().lower()
    return _LANGUAGE_ALIASES.get(raw, raw)


def default_syntax_highlighting_settings() -> dict[str, Any]:
    return {
        "global_tokens": {},
        "language_overrides": {},
    }


def is_valid_syntax_color(value: object) -> bool:
    return bool(_HEX_COLOR_RE.fullmatch(str(value or "").strip()))


def normalize_syntax_highlighting_settings(raw: object) -> dict[str, Any]:
    defaults = default_syntax_highlighting_settings()
    if not isinstance(raw, dict):
        return deepcopy(defaults)

    normalized: dict[str, Any] = deepcopy(defaults)
    global_tokens_raw = raw.get("global_tokens")
    if isinstance(global_tokens_raw, dict):
        clean_global: dict[str, str] = {}
        for token_key, color_value in global_tokens_raw.items():
            token = str(token_key or "").strip().lower()
            color = str(color_value or "").strip()
            if not _TOKEN_KEY_RE.fullmatch(token):
                continue
            if not is_valid_syntax_color(color):
                continue
            clean_global[token] = color
        normalized["global_tokens"] = clean_global

    language_raw = raw.get("language_overrides")
    if isinstance(language_raw, dict):
        clean_languages: dict[str, dict[str, str]] = {}
        for lang_key, token_map in language_raw.items():
            language = canonicalize_syntax_language(str(lang_key or "").strip().lower())
            if not _LANGUAGE_KEY_RE.fullmatch(language):
                continue
            if not isinstance(token_map, dict):
                continue
            clean_tokens: dict[str, str] = {}
            for token_key, color_value in token_map.items():
                token = str(token_key or "").strip().lower()
                color = str(color_value or "").strip()
                if not _TOKEN_KEY_RE.fullmatch(token):
                    continue
                if not is_valid_syntax_color(color):
                    continue
                clean_tokens[token] = color
            if clean_tokens:
                clean_languages[language] = clean_tokens
        normalized["language_overrides"] = clean_languages

    return normalized


def build_syntax_color_resolver(raw: object) -> Callable[[str, str, str], str | None]:
    normalized = normalize_syntax_highlighting_settings(raw)
    global_tokens = dict(normalized.get("global_tokens") or {})
    language_overrides = dict(normalized.get("language_overrides") or {})

    def _resolve(language: str, token: str, _default: str) -> str | None:
        lang = canonicalize_syntax_language(str(language or "").strip().lower())
        token_key = str(token or "").strip().lower()
        if not token_key:
            return None
        per_language = language_overrides.get(lang)
        if isinstance(per_language, dict):
            candidate = per_language.get(token_key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        fallback = global_tokens.get(token_key)
        return fallback if isinstance(fallback, str) and fallback.strip() else None

    return _resolve
