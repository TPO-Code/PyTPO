# pytpo-session-workbench/backend/fs.py
import shutil
from pathlib import Path
import json
import hashlib
import time
import difflib

# Configured live roots (can be extended)
LIVE_ROOTS = [
    "/usr/share/xsessions",
    "/usr/share/wayland-sessions",
    "/usr/share/gnome-session/sessions",
    "/usr/share/applications",
    "/etc/xdg/autostart",
    str(Path.home() / ".config" / "autostart"),
]


# Add to backend/fs.py (below existing functions)

import stat
from datetime import datetime
import subprocess

def _fmt_mtime(path: Path):
    try:
        return datetime.utcfromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%SZ")
    except Exception:
        return None

def list_backups(tracked_dir: str):
    """
    Return a list of backup entries for a tracked dir.
    Each entry is a dict: {"name": "<basename>", "path": "<full>", "mtime": "<iso>", "size": <bytes>}
    """
    t = Path(tracked_dir)
    backup_dir = t / "backups"
    if not backup_dir.exists():
        return []
    items = []
    for p in sorted(backup_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if p.is_file():
            items.append({
                "name": p.name,
                "path": str(p),
                "mtime": _fmt_mtime(p),
                "size": p.stat().st_size
            })
    return items

def restore_backup_to_draft(tracked_dir: str, backup_path: str):
    """
    Restore the selected backup file into workspace_draft (no privileges required).
    Returns {"ok": True, "message": "..."} or {"ok": False, "message": "..."}.
    """
    t = Path(tracked_dir)
    b = Path(backup_path)
    if not t.exists() or not b.exists():
        return {"ok": False, "message": "Tracked directory or backup path not found"}
    draft = t / "workspace_draft"
    try:
        shutil.copy2(str(b), str(draft))
        # update meta.json hash if present
        meta_path = t / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            try:
                meta["hash_workspace_draft"] = _hash_text(draft.read_text())
            except Exception:
                meta["hash_workspace_draft"] = None
            meta["last_restore_to_draft_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            meta_path.write_text(json.dumps(meta, indent=2))
        return {"ok": True, "message": f"Restored backup {b.name} into workspace draft"}
    except Exception as e:
        return {"ok": False, "message": f"Failed to restore backup to draft: {e}"}

def restore_backup_to_live(tracked_dir: str, backup_path: str, escalate_helper: str = None, try_escalate: bool = False):
    """
    Install a backup directly to the original live path.
    If permission denied and escalate_helper is provided, and try_escalate=True, attempt pkexec with helper.
    Structured returns similar to push_draft_to_live.
    """
    t = Path(tracked_dir)
    meta_path = t / "meta.json"
    if not meta_path.exists():
        return {"ok": False, "message": "meta.json missing"}
    meta = json.loads(meta_path.read_text())
    live_path = Path(meta.get("original_live_path", ""))
    b = Path(backup_path)
    if not b.exists():
        return {"ok": False, "message": "backup file missing"}

    try:
        # create pre-install backup of live path (if exists)
        if live_path.exists():
            backup_dir = t / "backups"
            backup_dir.mkdir(exist_ok=True)
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            shutil.copy2(str(live_path), str(backup_dir / f"live_backup_{ts}"))

        # try normal copy
        try:
            shutil.copy2(str(b), str(live_path))
            meta["last_pushed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            meta_path.write_text(json.dumps(meta, indent=2))
            return {"ok": True, "message": f"Installed backup to {live_path}"}
        except PermissionError:
            # fallthrough to escalate or instruct GUI
            if escalate_helper and try_escalate:
                helper_path = Path(escalate_helper)
                if not helper_path.exists():
                    return {"ok": False, "message": f"Escalation helper not found at {escalate_helper}"}
                cmd = ["pkexec", "/usr/bin/python3", str(helper_path), "--src", str(b), "--dst", str(live_path)]
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if p.returncode == 0:
                    meta["last_pushed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    meta_path.write_text(json.dumps(meta, indent=2))
                    return {"ok": True, "message": f"Installed backup to {live_path} (via pkexec)"}
                else:
                    return {"ok": False, "message": f"Escalation helper failed: {p.returncode}\nstdout: {p.stdout}\nstderr: {p.stderr}"}
            else:
                return {
                    "ok": False,
                    "needs_elevation": True,
                    "message": f"Permission denied when installing to {live_path}. Use a privileged helper (pkexec).",
                    "live_path": str(live_path),
                    "helper_suggestion": escalate_helper
                }
    except Exception as e:
        return {"ok": False, "message": f"Failed to install backup to live: {e}"}

def list_root_children(root):
    """Return list of Path objects (one level) for a live root."""
    p = Path(root)
    if not p.exists():
        return []
    try:
        items = sorted([x for x in p.iterdir()], key=lambda s: (not s.is_dir(), s.name.lower()))
    except PermissionError:
        items = []
    return items

def read_live_file(path):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    text = p.read_text(errors="replace")
    stat = p.stat()
    return {"path": str(p), "contents": text, "mtime": stat.st_mtime, "size": stat.st_size}

def _hash_text(s: str):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def import_file_to_project(live_path: str, project_root: Path):
    p = Path(live_path)
    if not p.exists():
        raise FileNotFoundError(live_path)
    # key-safe directory name
    key = str(p).replace("/", "_").lstrip("_")
    dest = Path(project_root) / "files" / key
    dest.mkdir(parents=True, exist_ok=True)

    # Save original path
    (dest / "original.live.path.txt").write_text(str(p))

    # Copy snapshot
    import_snapshot = dest / "import_snapshot"
    shutil.copy2(str(p), str(import_snapshot))

    # Workspace draft initially identical
    (dest / "workspace_draft").write_bytes(import_snapshot.read_bytes())

    # create meta.json
    meta = {
        "original_live_path": str(p),
        "imported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "last_opened_at": None,
        "last_pushed_at": None,
        "file_type_guess": p.suffix.lstrip("."),
        "editable": True,
        "exists_live": True,
        "hash_import_snapshot": _hash_text(import_snapshot.read_text()),
        "hash_workspace_draft": _hash_text((dest / "workspace_draft").read_text()),
        "last_backup_path": None,
        "notes": ""
    }
    (dest / "meta.json").write_text(json.dumps(meta, indent=2))
    return str(dest)

def save_draft(tracked_dir: str, contents: str):
    dest = Path(tracked_dir)
    if not dest.exists():
        raise FileNotFoundError(tracked_dir)
    draft = dest / "workspace_draft"
    draft.write_text(contents)
    # update meta
    meta_path = dest / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        meta["hash_workspace_draft"] = _hash_text(contents)
        meta["last_opened_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        meta_path.write_text(json.dumps(meta, indent=2))
    return True

def read_draft(tracked_dir: str):
    p = Path(tracked_dir) / "workspace_draft"
    if not p.exists():
        return ""
    return p.read_text()

def diff_text(a: str, b: str, a_label="A", b_label="B"):
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    ud = difflib.unified_diff(a_lines, b_lines, fromfile=a_label, tofile=b_label)
    return "".join(ud)

def create_backup(tracked_dir: str, backup_root=None):
    t = Path(tracked_dir)
    if not t.exists():
        raise FileNotFoundError(tracked_dir)
    backup_root = Path(backup_root or t / "backups")
    backup_root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    src = t / "workspace_draft"
    if not src.exists():
        raise FileNotFoundError("no workspace_draft to backup")
    dest = backup_root / f"backup_{ts}"
    shutil.copy2(str(src), str(dest))
    # update meta
    meta_path = t / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        meta["last_backup_path"] = str(dest)
        meta["last_backup_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        meta_path.write_text(json.dumps(meta, indent=2))
    return str(dest)

def push_draft_to_live(tracked_dir: str, escalate_helper: str = None, try_escalate: bool = False):
    """
    Attempts to copy workspace_draft -> original_live_path.
    If permission denied, returns structured error explaining that elevation is required.
    If `escalate_helper` is set and `try_escalate`==True, attempt to call pkexec with the helper script.
    """
    import subprocess
    t = Path(tracked_dir)
    meta_path = t / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError("meta.json missing")
    meta = json.loads(meta_path.read_text())
    live_path = Path(meta["original_live_path"])
    draft = t / "workspace_draft"
    if not draft.exists():
        raise FileNotFoundError("workspace_draft missing")
    try:
        # create pre-push backup of live file (if exists)
        if live_path.exists():
            backup_dir = t / "backups"
            backup_dir.mkdir(exist_ok=True)
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            shutil.copy2(str(live_path), str(backup_dir / f"live_backup_{ts}"))
        # attempt to copy (may raise PermissionError)
        shutil.copy2(str(draft), str(live_path))
        meta["last_pushed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        meta_path.write_text(json.dumps(meta, indent=2))
        return {"ok": True, "message": f"Pushed to {live_path}"}
    except PermissionError as e:
        # if caller explicitly asks to try escalation, attempt pkexec
        if escalate_helper and try_escalate:
            # attempt to run: pkexec python3 <escalate_helper> --src <draft> --dst <live_path>
            try:
                # ensure helper exists
                helper_path = Path(escalate_helper)
                if not helper_path.exists():
                    return {"ok": False, "message": f"Escalation helper not found at {escalate_helper}"}
                cmd = ["pkexec", "/usr/bin/python3", str(helper_path), "--src", str(draft), "--dst", str(live_path)]
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if p.returncode == 0:
                    # successful; update meta
                    meta["last_pushed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    meta_path.write_text(json.dumps(meta, indent=2))
                    return {"ok": True, "message": f"Pushed to {live_path} (via pkexec)"}
                else:
                    return {"ok": False, "message": f"Escalation helper failed: {p.returncode}\nstdout: {p.stdout}\nstderr: {p.stderr}"}
            except Exception as ex:
                return {"ok": False, "message": f"Escalation attempt failed: {ex}"}
        # otherwise return structured response telling GUI to escalate
        return {
            "ok": False,
            "needs_elevation": True,
            "message": f"Permission denied when writing {live_path}. Use a privileged helper (pkexec) to complete the install.",
            "live_path": str(live_path),
            "helper_suggestion": escalate_helper
        }
    except Exception as e:
        return {"ok": False, "message": f"Failed to push: {e}"}