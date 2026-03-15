from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

from .apps import prettify_wm_class


_INTERPRETER_NAMES = {
    "bash",
    "env",
    "fish",
    "node",
    "perl",
    "php",
    "pypy",
    "pypy3",
    "python",
    "python3",
    "ruby",
    "sh",
    "zsh",
}
_GENERIC_SCRIPT_NAMES = {"__main__", "app", "client", "launcher", "main", "run", "server", "start"}
_WINDOW_MATCH_THRESHOLD = 120


def parse_wmctrl_windows(
    output: str,
    *,
    is_own_window: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    records = []
    for line in output.splitlines():
        parsed = _parse_wmctrl_line(line)
        if parsed:
            records.append(parsed)
    return finalize_window_records(records, is_own_window=is_own_window)


def finalize_window_records(
    records: list[dict[str, Any]],
    *,
    is_own_window: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    own_window_check = is_own_window or (lambda _win_id: False)

    for parsed in records:
        if parsed["desktop"] == "-1":
            continue
        if own_window_check(parsed["id"]):
            continue
        if not parsed.get("wm_class"):
            parsed.update(_read_x_window_identity(parsed["id"]))
        parsed.update(_process_identity(int(parsed.get("pid") or 0)))
        parsed["app_name"] = _runtime_app_name(parsed)
        parsed["icon"] = _runtime_icon_name(parsed)
        parsed["runtime_id"] = _runtime_group_id(parsed)
        windows.append(parsed)

    return windows


def score_window_match(window: dict[str, Any], app_data: dict[str, str]) -> int:
    app_profile = _app_profile(app_data)
    if not any(group["full"] or group["parts"] for group in app_profile.values()):
        return 0

    window_profile = _window_profile(window)
    window_full_tokens = (
        window_profile["wm_class"]["full"]
        | window_profile["class"]["full"]
        | window_profile["instance"]["full"]
        | window_profile["process"]["full"]
    )
    window_part_tokens = (
        window_profile["wm_class"]["parts"]
        | window_profile["class"]["parts"]
        | window_profile["instance"]["parts"]
        | window_profile["process"]["parts"]
    )
    primary_app_full_tokens = (
        app_profile["startup"]["full"]
        | app_profile["desktop"]["full"]
        | app_profile["exec"]["full"]
        | app_profile["icon"]["full"]
    )

    score = 0
    if app_profile["startup"]["full"] & (window_full_tokens | window_part_tokens):
        score += 200
    elif app_profile["startup"]["parts"] & window_part_tokens:
        score += 80

    if app_profile["desktop"]["full"] & window_full_tokens:
        score += 180
    elif app_profile["desktop"]["full"] & window_part_tokens:
        score += 60

    if app_profile["exec"]["full"] & window_full_tokens:
        score += 170
    elif app_profile["exec"]["full"] & window_part_tokens:
        score += 50

    if app_profile["icon"]["full"] & (window_full_tokens | window_part_tokens):
        score += 40
    if app_profile["name"]["full"] & (window_full_tokens | window_part_tokens):
        score += 30
    if primary_app_full_tokens & window_profile["title"]["parts"]:
        score += 15

    strong_matches = app_profile["startup"]["full"] | app_profile["desktop"]["full"] | app_profile["exec"]["full"]
    score += min(2, len(strong_matches & window_full_tokens)) * 10
    return score


def match_threshold() -> int:
    return _WINDOW_MATCH_THRESHOLD


def runtime_group_path(window: dict[str, Any]) -> str:
    runtime_id = str(window.get("runtime_id") or "").strip()
    if not runtime_id:
        runtime_id = str(window.get("id") or "").strip().lower() or "unknown"
    return f"runtime://{runtime_id}"


def _parse_wmctrl_line(line: str) -> dict[str, Any] | None:
    raw = str(line or "").strip()
    if not raw:
        return None

    parts = raw.split(maxsplit=4)
    if len(parts) >= 5 and parts[2].lstrip("-").isdigit():
        win_id, desktop_id, pid_text, host, title = parts[:5]
    else:
        legacy_parts = raw.split(maxsplit=3)
        if len(legacy_parts) < 4:
            return None
        win_id, desktop_id, host, title = legacy_parts
        pid_text = ""

    return {
        "id": win_id,
        "desktop": desktop_id,
        "pid": _parse_pid(pid_text),
        "host": host,
        "wm_class": "",
        "instance": "",
        "class": "",
        "title": str(title or "").strip(),
    }


def _parse_pid(value: str) -> int:
    try:
        return max(0, int(str(value or "").strip()))
    except ValueError:
        return 0


def _split_wm_class(wm_class_text: str) -> tuple[str, str]:
    parts = [segment.strip().lower() for segment in str(wm_class_text or "").split(".") if segment.strip()]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], parts[-1]


def _read_x_window_identity(win_id: str) -> dict[str, str]:
    try:
        output = subprocess.check_output(
            ["xprop", "-id", str(win_id), "WM_CLASS"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return {
            "wm_class": "",
            "instance": "",
            "class": "",
        }

    values = [match.strip().lower() for match in re.findall(r'"([^"]*)"', output) if match.strip()]
    if not values:
        return {
            "wm_class": "",
            "instance": "",
            "class": "",
        }
    if len(values) == 1:
        instance_name, class_name = _split_wm_class(values[0])
        wm_class = values[0]
    else:
        instance_name = values[0]
        class_name = values[-1]
        wm_class = ".".join(part for part in (instance_name, class_name) if part)
    return {
        "wm_class": wm_class,
        "instance": instance_name,
        "class": class_name,
    }


def _process_identity(pid: int) -> dict[str, str]:
    if pid <= 0:
        return {
            "process_name": "",
            "executable_name": "",
            "script_name": "",
        }

    proc_root = Path("/proc") / str(pid)
    process_name = _read_text(proc_root / "comm").strip().lower()
    executable_name = _proc_basename(proc_root / "exe")
    script_name = _script_name_from_cmdline(_read_cmdline(proc_root / "cmdline"))
    return {
        "process_name": process_name,
        "executable_name": executable_name,
        "script_name": script_name,
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _read_cmdline(path: Path) -> list[str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    return [part for part in raw.decode("utf-8", errors="ignore").split("\0") if part]


def _proc_basename(path: Path) -> str:
    try:
        return os.path.basename(os.path.realpath(path)).strip().lower()
    except OSError:
        return ""


def _script_name_from_cmdline(argv: list[str]) -> str:
    if not argv:
        return ""

    launcher_name = os.path.basename(argv[0]).strip().lower()
    if launcher_name not in _INTERPRETER_NAMES and not any(launcher_name.startswith(f"{name}") for name in ("python", "pypy")):
        return ""

    index = 1
    while index < len(argv):
        value = str(argv[index] or "").strip()
        if not value:
            index += 1
            continue
        if value == "-m" and index + 1 < len(argv):
            return _basename_without_suffix(argv[index + 1].replace(".", "/"))
        if value.startswith("-"):
            index += 1
            continue
        return _path_identity(value)
    return ""


def _runtime_app_name(window: dict[str, Any]) -> str:
    for candidate in (
        window.get("script_name"),
        window.get("class"),
        window.get("instance"),
        window.get("executable_name"),
        window.get("process_name"),
    ):
        pretty = prettify_wm_class(str(candidate or ""))
        if pretty != "Unknown App":
            return pretty

    title = str(window.get("title") or "").strip()
    if title:
        return title.split(" - ", 1)[0].strip()
    return "Unknown App"


def _runtime_icon_name(window: dict[str, Any]) -> str:
    for candidate in (
        window.get("class"),
        window.get("instance"),
        window.get("script_name"),
        window.get("executable_name"),
        window.get("process_name"),
    ):
        icon_name = _icon_candidate(candidate)
        if icon_name:
            return icon_name
    return ""


def _runtime_group_id(window: dict[str, Any]) -> str:
    for candidate in (
        window.get("script_name"),
        window.get("wm_class"),
        window.get("class"),
        window.get("instance"),
        window.get("executable_name"),
        window.get("process_name"),
    ):
        canonical = _canonical_token(candidate)
        if canonical and canonical not in {"python", "python3"}:
            return canonical
    return _canonical_token(window.get("id"))


def _icon_candidate(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "/" in text:
        text = os.path.basename(text)
    text = text.split(".")[-1]
    cleaned = re.sub(r"[-_.]?\d+(?:\.\d+)*$", "", text).strip("-_.")
    return cleaned or text


def _app_profile(app_data: dict[str, str]) -> dict[str, set[str]]:
    desktop_id = str(app_data.get("desktop_id") or Path(str(app_data.get("path", "") or "")).name).strip()
    return {
        "startup": _merge_token_groups(_identity_tokens(app_data.get("StartupWMClass"), split_parts=True)),
        "desktop": _merge_token_groups(
            _identity_tokens(desktop_id, split_parts=False),
            _identity_tokens(Path(desktop_id).stem, split_parts=False),
        ),
        "exec": _merge_token_groups(_identity_tokens(_exec_identity(app_data.get("Exec")), split_parts=False)),
        "icon": _merge_token_groups(_identity_tokens(app_data.get("Icon"), split_parts=False)),
        "name": _merge_token_groups(
            _identity_tokens(app_data.get("Name"), split_parts=False),
            _identity_tokens(app_data.get("GenericName"), split_parts=False),
        ),
    }


def _window_profile(window: dict[str, Any]) -> dict[str, set[str]]:
    title = str(window.get("title") or "").strip()
    title_head = title.split(" - ", 1)[0].strip() if title else ""
    return {
        "wm_class": _identity_tokens(window.get("wm_class"), split_parts=True),
        "class": _identity_tokens(window.get("class"), split_parts=False),
        "instance": _identity_tokens(window.get("instance"), split_parts=False),
        "process": (
            _merge_token_groups(
                _identity_tokens(window.get("process_name"), split_parts=False),
                _identity_tokens(window.get("executable_name"), split_parts=False),
                _identity_tokens(window.get("script_name"), split_parts=False),
            )
        ),
        "title": _identity_tokens(title_head, split_parts=True),
    }


def _exec_identity(exec_value: Any) -> str:
    text = str(exec_value or "").strip()
    if not text:
        return ""
    try:
        parts = shlex.split(text)
    except ValueError:
        return ""
    if not parts:
        return ""
    launcher_name = os.path.basename(parts[0]).strip().lower()
    if launcher_name in _INTERPRETER_NAMES or any(launcher_name.startswith(f"{name}") for name in ("python", "pypy")):
        index = 1
        while index < len(parts):
            part = parts[index].strip()
            if not part:
                index += 1
                continue
            if part == "-m" and index + 1 < len(parts):
                return parts[index + 1]
            if part.startswith("-") or part.startswith("%"):
                index += 1
                continue
            return _path_identity(part)
    return _path_identity(parts[0])


def _identity_tokens(value: Any, *, split_parts: bool) -> dict[str, set[str]]:
    text = str(value or "").strip().lower()
    if not text:
        return {"full": set(), "parts": set()}

    candidates = {
        text,
        os.path.basename(text),
        _basename_without_suffix(text),
    }
    if text.endswith(".desktop"):
        candidates.add(text.removesuffix(".desktop"))

    full_tokens: set[str] = set()
    part_tokens: set[str] = set()
    for candidate in candidates:
        canonical = _canonical_token(candidate)
        if canonical:
            full_tokens.add(canonical)
        binary_alias = _binary_alias(candidate)
        if binary_alias:
            full_tokens.add(binary_alias)
        if split_parts:
            for part in re.split(r"[^a-z0-9]+", candidate):
                canonical_part = _canonical_token(part)
                if canonical_part:
                    part_tokens.add(canonical_part)
    return {"full": full_tokens, "parts": part_tokens}


def _basename_without_suffix(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    basename = os.path.basename(text)
    stem = Path(basename).stem
    return stem.strip().lower()


def _path_identity(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    candidates = [path.stem.lower()]
    candidates.extend(parent.name.lower() for parent in path.parents[:3] if parent.name)
    for candidate in candidates:
        cleaned = candidate.strip()
        if cleaned and cleaned not in _GENERIC_SCRIPT_NAMES:
            return cleaned
    return _basename_without_suffix(text)


def _binary_alias(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    alias = re.sub(r"[-_.]?bin$", "", os.path.basename(text)).strip().lower()
    if alias == os.path.basename(text).strip().lower():
        return ""
    return _canonical_token(alias)


def _merge_token_groups(*groups: dict[str, set[str]]) -> dict[str, set[str]]:
    merged_full: set[str] = set()
    merged_parts: set[str] = set()
    for group in groups:
        merged_full.update(group.get("full", set()))
        merged_parts.update(group.get("parts", set()))
    return {"full": merged_full, "parts": merged_parts}


def _canonical_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
