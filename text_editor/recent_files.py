from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths

APP_DIR_NAME = "pytpo-text-editor"
RECENT_FILES_LIMIT = 15
RECENT_FILES_FILENAME = "recent-files.json"


def recent_files_store_path() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation).strip()
    if location:
        root = Path(location)
    else:
        root = Path.home() / ".local" / "share" / APP_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root / RECENT_FILES_FILENAME


def load_recent_files() -> list[str]:
    path = recent_files_store_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    items = payload.get("recent_files")
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    results: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        results.append(text)
    return results[:RECENT_FILES_LIMIT]


def save_recent_files(paths: list[str]) -> None:
    store_path = recent_files_store_path()
    payload = {"recent_files": [str(path).strip() for path in paths if str(path).strip()][:RECENT_FILES_LIMIT]}
    store_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def record_recent_file(path: Path | str, existing: list[str] | None = None) -> list[str]:
    text = str(path).strip()
    if not text:
        return list(existing or [])
    items = [text]
    for candidate in list(existing or []):
        normalized = str(candidate or "").strip()
        if normalized and normalized != text:
            items.append(normalized)
    return items[:RECENT_FILES_LIMIT]
