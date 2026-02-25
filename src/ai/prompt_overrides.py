from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any


DEFAULT_INLINE_SYSTEM_PROMPT = (
    "You are an inline coding assistant. "
    "Generate only the minimal continuation text that should appear at the cursor. "
    "Never include explanations."
)


def infer_language_for_path(file_path: str) -> str:
    suffix = Path(str(file_path or "")).suffix.lower()
    mapping = {
        ".py": "python",
        ".pyw": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".json": "json",
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".scss": "scss",
        ".less": "less",
        ".sh": "bash",
        ".zsh": "bash",
        ".ksh": "bash",
        ".bash": "bash",
        ".php": "php",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".hpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".rs": "rust",
        ".md": "markdown",
        ".xml": "xml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".ini": "ini",
        ".qss": "css",
    }
    return mapping.get(suffix, "text")


def profile_matches_file(
    profile: dict[str, Any],
    *,
    file_path: str,
    language: str,
    project_root: str = "",
) -> bool:
    kind = str(profile.get("target_kind") or "extension").strip().lower()
    value = str(profile.get("target_value") or "").strip()
    if not value:
        return False

    path_norm = str(file_path or "").replace("\\", "/")
    path_lc = path_norm.lower()
    rel_lc = _project_relative_path(path_norm, project_root=project_root).lower()
    base_lc = os.path.basename(path_lc)

    if kind == "language":
        return str(language or "").strip().lower() == value.lower()

    if kind == "glob":
        pattern = value.replace("\\", "/").lower()
        return (
            fnmatch.fnmatch(path_lc, pattern)
            or fnmatch.fnmatch(base_lc, pattern)
            or (bool(rel_lc) and fnmatch.fnmatch(rel_lc, pattern))
        )

    ext = os.path.splitext(path_lc)[1]
    wanted = value.lower()
    if not wanted.startswith("."):
        wanted = f".{wanted}"
    return ext == wanted


def resolve_system_prompt(
    base_system_prompt: str,
    profiles: list[dict[str, Any]] | None,
    *,
    file_path: str,
    language: str,
    project_root: str = "",
) -> tuple[str, dict[str, Any]]:
    profile_list = list(profiles or [])
    if not profile_list:
        return str(base_system_prompt or ""), {}

    matched: list[tuple[int, int, dict[str, Any]]] = []
    for index, raw_profile in enumerate(profile_list):
        if not isinstance(raw_profile, dict):
            continue
        if not bool(raw_profile.get("enabled", True)):
            continue
        prompt = str(raw_profile.get("prompt") or "").strip()
        if not prompt:
            continue
        if not profile_matches_file(
            raw_profile,
            file_path=file_path,
            language=language,
            project_root=project_root,
        ):
            continue
        try:
            priority = int(raw_profile.get("priority", 0))
        except Exception:
            priority = 0
        matched.append((priority, index, raw_profile))

    if not matched:
        return str(base_system_prompt or ""), {}

    matched.sort(key=lambda item: (-item[0], item[1]))
    replace_profiles = [profile for _priority, _index, profile in matched if str(profile.get("mode") or "append").lower() == "replace"]
    append_profiles = [profile for _priority, _index, profile in matched if str(profile.get("mode") or "append").lower() == "append"]

    resolved_prompt = str(base_system_prompt or "")
    replace_name = ""
    if replace_profiles:
        selected = replace_profiles[0]
        replace_name = str(selected.get("name") or "Profile")
        resolved_prompt = str(selected.get("prompt") or "").strip()

    append_parts = [str(profile.get("prompt") or "").strip() for profile in append_profiles]
    append_parts = [part for part in append_parts if part]
    if append_parts:
        merged_append = "\n\n".join(append_parts)
        if resolved_prompt.strip():
            resolved_prompt = f"{resolved_prompt.rstrip()}\n\n{merged_append}"
        else:
            resolved_prompt = merged_append

    return resolved_prompt, {
        "matched_count": len(matched),
        "applied": [str(profile.get("name") or "Profile") for _priority, _index, profile in matched],
        "replace_profile": replace_name,
        "append_profiles": [str(profile.get("name") or "Profile") for profile in append_profiles],
    }


def _project_relative_path(file_path: str, *, project_root: str) -> str:
    root = str(project_root or "").strip()
    if not root:
        return ""
    try:
        abs_root = os.path.abspath(root)
        abs_path = os.path.abspath(file_path)
        rel = os.path.relpath(abs_path, abs_root)
        if rel.startswith(".."):
            return ""
        return rel.replace("\\", "/")
    except Exception:
        return ""
