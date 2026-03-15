# pytpo-session-workbench/backend/project.py
import json
from pathlib import Path
from datetime import datetime

_TRACKED_INDEX_VERSION = 1


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _normalize_tracked_files(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    clean: list[str] = []
    for item in values:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        clean.append(key)
    return clean

def create_project(project_root: Path, project_name: str, description: str = ""):
    project_root = Path(project_root)
    project_root.mkdir(parents=True, exist_ok=True)
    pj = {
        "project_name": project_name,
        "slug": project_name.lower().replace(" ", "-"),
        "description": description,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "notes": "",
        "tracked_files": [],
        "tracked_index_version": _TRACKED_INDEX_VERSION,
        "tags": []
    }
    (project_root / "project.json").write_text(json.dumps(pj, indent=2))
    (project_root / "files").mkdir(exist_ok=True)
    return project_root

def load_project(project_root: Path):
    pjfile = Path(project_root) / "project.json"
    if not pjfile.exists():
        raise FileNotFoundError("project.json not found")
    data = json.loads(pjfile.read_text())
    data["tracked_files"] = _normalize_tracked_files(data.get("tracked_files"))
    return data

def save_project(project_root: Path, data: dict):
    pjfile = Path(project_root) / "project.json"
    data["tracked_files"] = _normalize_tracked_files(data.get("tracked_files"))
    data["tracked_index_version"] = _TRACKED_INDEX_VERSION
    data["updated_at"] = _now_iso()
    pjfile.write_text(json.dumps(data, indent=2))


def tracked_file_keys(project_root: Path) -> list[str]:
    project_root = Path(project_root)
    data = load_project(project_root)
    tracked_files = _normalize_tracked_files(data.get("tracked_files"))
    files_dir = project_root / "files"
    existing_dirs = sorted(item.name for item in files_dir.iterdir() if item.is_dir()) if files_dir.exists() else []

    if int(data.get("tracked_index_version") or 0) < _TRACKED_INDEX_VERSION:
        if existing_dirs and not tracked_files:
            tracked_files = existing_dirs
        data["tracked_files"] = tracked_files
        save_project(project_root, data)

    return tracked_files


def tracked_file_dirs(project_root: Path) -> list[Path]:
    project_root = Path(project_root)
    files_dir = project_root / "files"
    out: list[Path] = []
    for key in tracked_file_keys(project_root):
        candidate = files_dir / key
        if candidate.is_dir():
            out.append(candidate)
    return out


def add_tracked_file(project_root: Path, tracked_key: str) -> None:
    key = str(tracked_key or "").strip()
    if not key:
        return
    data = load_project(project_root)
    tracked_files = _normalize_tracked_files(data.get("tracked_files"))
    if key not in tracked_files:
        tracked_files.append(key)
    data["tracked_files"] = tracked_files
    save_project(project_root, data)


def remove_tracked_file(project_root: Path, tracked_key: str) -> None:
    key = str(tracked_key or "").strip()
    data = load_project(project_root)
    tracked_files = [item for item in _normalize_tracked_files(data.get("tracked_files")) if item != key]
    data["tracked_files"] = tracked_files
    save_project(project_root, data)
