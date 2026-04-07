"""Cargo project/workspace discovery helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


def discover_workspace_root_for_file(
    *,
    file_path: str,
    project_root: str,
    canonicalize: Callable[[str], str],
    path_has_prefix: Callable[[str, str], bool],
) -> str:
    cpath = canonicalize(file_path)
    if not cpath:
        return ""
    start_dir = cpath if os.path.isdir(cpath) else canonicalize(os.path.dirname(cpath))
    if not start_dir:
        return ""

    cproject_root = canonicalize(project_root)
    stop_dir = cproject_root if cproject_root and path_has_prefix(start_dir, cproject_root) else ""
    manifests = _collect_manifest_dirs(start_dir=start_dir, stop_dir=stop_dir)
    if not manifests:
        return ""

    nearest_manifest = manifests[0]
    nearest_manifest_file = os.path.join(nearest_manifest, "Cargo.toml")
    if _manifest_declares_workspace(nearest_manifest_file):
        return canonicalize(nearest_manifest)

    for directory in manifests[1:]:
        manifest_file = os.path.join(directory, "Cargo.toml")
        if _manifest_declares_workspace(manifest_file):
            return canonicalize(directory)

    return canonicalize(nearest_manifest)


def find_nearest_cargo_project_dir(
    *,
    file_path: str,
    project_root: str,
    canonicalize: Callable[[str], str],
    path_has_prefix: Callable[[str, str], bool],
) -> str:
    cpath = canonicalize(file_path)
    if not cpath:
        return ""
    start_dir = cpath if os.path.isdir(cpath) else canonicalize(os.path.dirname(cpath))
    if not start_dir:
        return ""

    cproject_root = canonicalize(project_root)
    stop_dir = cproject_root if cproject_root and path_has_prefix(start_dir, cproject_root) else ""
    manifests = _collect_manifest_dirs(start_dir=start_dir, stop_dir=stop_dir)
    if not manifests:
        return ""
    return canonicalize(manifests[0])


def _collect_manifest_dirs(*, start_dir: str, stop_dir: str = "") -> list[str]:
    out: list[str] = []
    current = Path(start_dir)
    stop = Path(stop_dir) if stop_dir else None

    while True:
        manifest = current / "Cargo.toml"
        if manifest.is_file():
            out.append(str(current))
        if stop is not None and current == stop:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return out


def _manifest_declares_workspace(manifest_path: str) -> bool:
    path = Path(str(manifest_path or ""))
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    in_multiline = False
    for raw in text.splitlines():
        line = str(raw or "").strip()
        if not line:
            continue
        if line.startswith('"""') or line.startswith("'''"):
            in_multiline = not in_multiline
            continue
        if in_multiline:
            continue
        if line.startswith("#"):
            continue
        if line.lower().startswith("[workspace]"):
            return True
    return False

