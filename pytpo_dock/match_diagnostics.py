from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .storage_paths import dock_window_snapshot_json_path, dock_window_snapshot_markdown_path
from .window_matching import match_threshold, score_window_match


def write_window_snapshot(
    *,
    running_windows: list[dict[str, Any]],
    known_apps_by_path: dict[str, dict[str, str]],
    assigned_windows: dict[str, list[dict[str, Any]]],
    unmatched_windows: list[dict[str, Any]],
    target_items: list[dict[str, Any]],
) -> tuple[str, str]:
    assigned_by_window_id = _assigned_by_window_id(assigned_windows)
    app_entries = list(known_apps_by_path.items())
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "match_threshold": match_threshold(),
        "running_window_count": len(running_windows),
        "known_app_count": len(known_apps_by_path),
        "unmatched_window_ids": [str(window.get("id") or "") for window in unmatched_windows],
        "windows": [
            _window_payload(window, assigned_by_window_id, app_entries)
            for window in running_windows
        ],
        "target_items": [_target_item_payload(item) for item in target_items],
    }

    json_path = dock_window_snapshot_json_path()
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown_path = dock_window_snapshot_markdown_path()
    markdown_path.write_text(_render_markdown(payload), encoding="utf-8")
    return str(json_path), str(markdown_path)


def _assigned_by_window_id(assigned_windows: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    assigned: dict[str, str] = {}
    for app_path, windows in assigned_windows.items():
        for window in windows:
            window_id = str(window.get("id") or "").strip()
            if window_id:
                assigned[window_id] = app_path
    return assigned


def _window_payload(
    window: dict[str, Any],
    assigned_by_window_id: dict[str, str],
    app_entries: list[tuple[str, dict[str, str]]],
) -> dict[str, Any]:
    window_id = str(window.get("id") or "").strip()
    candidates = []
    for path, app_data in app_entries:
        score = score_window_match(window, app_data)
        if score <= 0:
            continue
        candidates.append(
            {
                "path": path,
                "name": str(app_data.get("Name") or "").strip(),
                "desktop_id": str(app_data.get("desktop_id") or "").strip(),
                "startup_wm_class": str(app_data.get("StartupWMClass") or "").strip(),
                "exec": str(app_data.get("Exec") or "").strip(),
                "score": score,
            }
        )
    candidates.sort(key=lambda item: (-int(item["score"]), str(item["name"]), str(item["path"])))

    return {
        "window": {
            "id": window_id,
            "title": str(window.get("title") or "").strip(),
            "wm_class": str(window.get("wm_class") or "").strip(),
            "instance": str(window.get("instance") or "").strip(),
            "class": str(window.get("class") or "").strip(),
            "pid": int(window.get("pid") or 0),
            "process_name": str(window.get("process_name") or "").strip(),
            "executable_name": str(window.get("executable_name") or "").strip(),
            "script_name": str(window.get("script_name") or "").strip(),
            "app_name": str(window.get("app_name") or "").strip(),
            "icon": str(window.get("icon") or "").strip(),
            "runtime_id": str(window.get("runtime_id") or "").strip(),
        },
        "chosen_path": assigned_by_window_id.get(window_id, ""),
        "top_candidates": candidates[:8],
    }


def _target_item_payload(item: dict[str, Any]) -> dict[str, Any]:
    app_data = item.get("data", {}) or {}
    return {
        "path": str(item.get("path") or "").strip(),
        "name": str(app_data.get("Name") or "").strip(),
        "icon": str(app_data.get("Icon") or "").strip(),
        "startup_wm_class": str(app_data.get("StartupWMClass") or "").strip(),
        "is_pinned": bool(item.get("is_pinned")),
        "is_running": bool(item.get("is_running")),
        "window_ids": [str(window.get("id") or "") for window in item.get("windows", [])],
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Dock Window Snapshot",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Match threshold: `{payload['match_threshold']}`",
        f"- Running windows: `{payload['running_window_count']}`",
        f"- Known apps: `{payload['known_app_count']}`",
        "",
        "## Target Items",
        "",
        "| App | Path | Icon | StartupWMClass | Running | Windows |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in payload["target_items"]:
        window_ids = ", ".join(item["window_ids"]) or "-"
        lines.append(
            f"| { _md(item['name']) } | `{item['path']}` | `{item['icon'] or '-'}` | "
            f"`{item['startup_wm_class'] or '-'}` | `{item['is_running']}` | { _md(window_ids) } |"
        )

    lines.extend(
        [
            "",
            "## Windows",
            "",
            "| Window | Title | WM_CLASS | Process | Chosen App | Top Candidates |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for entry in payload["windows"]:
        window = entry["window"]
        top_candidates = "<br>".join(
            f"`{candidate['score']}` { _md(candidate['name'] or candidate['path']) }"
            for candidate in entry["top_candidates"][:4]
        ) or "-"
        process = window["script_name"] or window["executable_name"] or window["process_name"] or "-"
        chosen = entry["chosen_path"] or "unmatched"
        lines.append(
            f"| `{window['id']}` | { _md(window['title'] or '-') } | "
            f"`{window['wm_class'] or '-'}` | `{process}` | `{chosen}` | {top_candidates} |"
        )

    lines.append("")
    return "\n".join(lines)


def _md(value: Any) -> str:
    return str(value or "").replace("\n", "<br>").replace("|", "\\|")
