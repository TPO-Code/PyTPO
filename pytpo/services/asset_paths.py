from __future__ import annotations

import sys
import sysconfig
from pathlib import Path


def _asset_root_candidates() -> list[Path]:
    module_path = Path(__file__).resolve()
    package_root = module_path.parents[1]
    source_root = module_path.parents[2]
    data_root = Path(sysconfig.get_path("data") or sys.prefix)
    return [
        source_root,
        package_root,
        data_root / "share" / "pytpo",
    ]


def _asset_candidates(relative_path: str | Path) -> list[Path]:
    relative = Path(relative_path)
    return [root / relative for root in _asset_root_candidates()]


def preferred_shared_asset_dir(relative_dir: str | Path) -> Path:
    candidates = _asset_candidates(relative_dir)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def shared_asset_search_dirs(relative_dir: str | Path) -> list[Path]:
    seen: set[str] = set()
    found: list[Path] = []
    for candidate in _asset_candidates(relative_dir):
        try:
            key = str(candidate.resolve()).lower()
        except Exception:
            key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_dir():
            found.append(candidate)
    return found


def preferred_shared_asset_path(relative_path: str | Path) -> Path:
    candidates = _asset_candidates(relative_path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]
