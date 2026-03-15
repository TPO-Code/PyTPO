# pytpo-session-workbench/backend/project.py
import json
from pathlib import Path
from datetime import datetime

def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

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
        "tags": []
    }
    (project_root / "project.json").write_text(json.dumps(pj, indent=2))
    (project_root / "files").mkdir(exist_ok=True)
    return project_root

def load_project(project_root: Path):
    pjfile = Path(project_root) / "project.json"
    if not pjfile.exists():
        raise FileNotFoundError("project.json not found")
    return json.loads(pjfile.read_text())

def save_project(project_root: Path, data: dict):
    pjfile = Path(project_root) / "project.json"
    data["updated_at"] = _now_iso()
    pjfile.write_text(json.dumps(data, indent=2))