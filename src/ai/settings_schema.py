from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict


AI_TRIGGER_MODES = (
    "manual_only",
    "hybrid",
    "passive_aggressive",
)


class AIAssistSettings(TypedDict, total=False):
    enabled: bool
    base_url: str
    api_key: str
    model: str
    trigger_mode: str
    debounce_ms: int
    max_context_tokens: int
    retrieval_snippets: int
    inline_timeout_ms: int
    min_prefix_chars: int
    max_output_tokens: int
    context_radius_lines: int
    enclosing_block_max_chars: int
    imports_outline_max_imports: int
    imports_outline_max_symbols: int
    retrieval_file_read_cap_chars: int
    retrieval_same_dir_file_limit: int
    retrieval_recent_file_limit: int
    retrieval_walk_file_limit: int
    retrieval_total_candidate_limit: int
    retrieval_snippet_char_cap: int
    retrieval_snippet_segment_limit: int


def default_ai_settings() -> AIAssistSettings:
    return {
        "enabled": False,
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "",
        "trigger_mode": "hybrid",
        "debounce_ms": 220,
        "max_context_tokens": 8000,
        "retrieval_snippets": 4,
        "inline_timeout_ms": 10000,
        "min_prefix_chars": 2,
        "max_output_tokens": 160,
        "context_radius_lines": 75,
        "enclosing_block_max_chars": 7000,
        "imports_outline_max_imports": 50,
        "imports_outline_max_symbols": 120,
        "retrieval_file_read_cap_chars": 18000,
        "retrieval_same_dir_file_limit": 40,
        "retrieval_recent_file_limit": 80,
        "retrieval_walk_file_limit": 120,
        "retrieval_total_candidate_limit": 180,
        "retrieval_snippet_char_cap": 420,
        "retrieval_snippet_segment_limit": 80,
    }


def normalize_ai_settings(raw: Any) -> AIAssistSettings:
    defaults = default_ai_settings()
    data = dict(defaults)
    if isinstance(raw, dict):
        for key, value in raw.items():
            data[str(key)] = value

    trigger_mode = str(data.get("trigger_mode", defaults["trigger_mode"]) or defaults["trigger_mode"]).strip().lower()
    if trigger_mode not in AI_TRIGGER_MODES:
        trigger_mode = defaults["trigger_mode"]

    def _clamp_int(value: Any, low: int, high: int, fallback: int) -> int:
        try:
            return max(low, min(high, int(value)))
        except Exception:
            return fallback

    return {
        "enabled": bool(data.get("enabled", defaults["enabled"])),
        "base_url": str(data.get("base_url", defaults["base_url"]) or "").strip() or defaults["base_url"],
        "api_key": str(data.get("api_key", defaults["api_key"]) or "").strip(),
        "model": str(data.get("model", defaults["model"]) or "").strip(),
        "trigger_mode": trigger_mode,
        "debounce_ms": _clamp_int(data.get("debounce_ms"), 40, 5000, int(defaults["debounce_ms"])),
        "max_context_tokens": _clamp_int(data.get("max_context_tokens"), 512, 32768, int(defaults["max_context_tokens"])),
        "retrieval_snippets": _clamp_int(data.get("retrieval_snippets"), 0, 12, int(defaults["retrieval_snippets"])),
        "inline_timeout_ms": _clamp_int(data.get("inline_timeout_ms"), 1000, 30000, int(defaults["inline_timeout_ms"])),
        "min_prefix_chars": _clamp_int(data.get("min_prefix_chars"), 1, 8, int(defaults["min_prefix_chars"])),
        "max_output_tokens": _clamp_int(data.get("max_output_tokens"), 32, 512, int(defaults["max_output_tokens"])),
        "context_radius_lines": _clamp_int(data.get("context_radius_lines"), 10, 400, int(defaults["context_radius_lines"])),
        "enclosing_block_max_chars": _clamp_int(data.get("enclosing_block_max_chars"), 500, 40000, int(defaults["enclosing_block_max_chars"])),
        "imports_outline_max_imports": _clamp_int(data.get("imports_outline_max_imports"), 0, 500, int(defaults["imports_outline_max_imports"])),
        "imports_outline_max_symbols": _clamp_int(data.get("imports_outline_max_symbols"), 0, 1000, int(defaults["imports_outline_max_symbols"])),
        "retrieval_file_read_cap_chars": _clamp_int(data.get("retrieval_file_read_cap_chars"), 1000, 200000, int(defaults["retrieval_file_read_cap_chars"])),
        "retrieval_same_dir_file_limit": _clamp_int(data.get("retrieval_same_dir_file_limit"), 0, 500, int(defaults["retrieval_same_dir_file_limit"])),
        "retrieval_recent_file_limit": _clamp_int(data.get("retrieval_recent_file_limit"), 0, 500, int(defaults["retrieval_recent_file_limit"])),
        "retrieval_walk_file_limit": _clamp_int(data.get("retrieval_walk_file_limit"), 0, 2000, int(defaults["retrieval_walk_file_limit"])),
        "retrieval_total_candidate_limit": _clamp_int(data.get("retrieval_total_candidate_limit"), 0, 4000, int(defaults["retrieval_total_candidate_limit"])),
        "retrieval_snippet_char_cap": _clamp_int(data.get("retrieval_snippet_char_cap"), 80, 8000, int(defaults["retrieval_snippet_char_cap"])),
        "retrieval_snippet_segment_limit": _clamp_int(data.get("retrieval_snippet_segment_limit"), 1, 400, int(defaults["retrieval_snippet_segment_limit"])),
    }


@dataclass(slots=True)
class NormalizedAIAssistConfig:
    enabled: bool
    base_url: str
    api_key: str
    model: str
    trigger_mode: str
    debounce_ms: int
    max_context_tokens: int
    retrieval_snippets: int
    inline_timeout_ms: int
    min_prefix_chars: int
    max_output_tokens: int
    context_radius_lines: int
    enclosing_block_max_chars: int
    imports_outline_max_imports: int
    imports_outline_max_symbols: int
    retrieval_file_read_cap_chars: int
    retrieval_same_dir_file_limit: int
    retrieval_recent_file_limit: int
    retrieval_walk_file_limit: int
    retrieval_total_candidate_limit: int
    retrieval_snippet_char_cap: int
    retrieval_snippet_segment_limit: int

    @classmethod
    def from_mapping(cls, data: Any) -> "NormalizedAIAssistConfig":
        n = normalize_ai_settings(data)
        return cls(
            enabled=bool(n["enabled"]),
            base_url=str(n["base_url"]),
            api_key=str(n["api_key"]),
            model=str(n["model"]),
            trigger_mode=str(n["trigger_mode"]),
            debounce_ms=int(n["debounce_ms"]),
            max_context_tokens=int(n["max_context_tokens"]),
            retrieval_snippets=int(n["retrieval_snippets"]),
            inline_timeout_ms=int(n["inline_timeout_ms"]),
            min_prefix_chars=int(n["min_prefix_chars"]),
            max_output_tokens=int(n["max_output_tokens"]),
            context_radius_lines=int(n["context_radius_lines"]),
            enclosing_block_max_chars=int(n["enclosing_block_max_chars"]),
            imports_outline_max_imports=int(n["imports_outline_max_imports"]),
            imports_outline_max_symbols=int(n["imports_outline_max_symbols"]),
            retrieval_file_read_cap_chars=int(n["retrieval_file_read_cap_chars"]),
            retrieval_same_dir_file_limit=int(n["retrieval_same_dir_file_limit"]),
            retrieval_recent_file_limit=int(n["retrieval_recent_file_limit"]),
            retrieval_walk_file_limit=int(n["retrieval_walk_file_limit"]),
            retrieval_total_candidate_limit=int(n["retrieval_total_candidate_limit"]),
            retrieval_snippet_char_cap=int(n["retrieval_snippet_char_cap"]),
            retrieval_snippet_segment_limit=int(n["retrieval_snippet_segment_limit"]),
        )
