"""Reusable XDG storage helpers for multi-app suites.

The migration helpers intentionally use "copy if missing" semantics so first-run
migration never overwrites newer user data already written to the new location.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def _slug(value: str, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    out = []
    for char in text:
        if char.isalnum() or char in {"-", "_"}:
            out.append(char)
            continue
        if not out or out[-1] == "-":
            continue
        out.append("-")
    normalized = "".join(out).strip("-")
    return normalized or fallback


def _xdg_home(env_key: str, fallback: Path) -> Path:
    raw = str(os.environ.get(env_key, "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return fallback


@dataclass(frozen=True, slots=True)
class AppStorageNamespace:
    suite_name: str
    app_name: str
    config_dir: Path
    data_dir: Path
    state_dir: Path
    cache_dir: Path

    def ensure_base_dirs(self) -> None:
        for directory in (self.config_dir, self.data_dir, self.state_dir, self.cache_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def config_path(self, *parts: str) -> Path:
        path = self.config_dir.joinpath(*parts)
        return path

    def data_path(self, *parts: str) -> Path:
        path = self.data_dir.joinpath(*parts)
        return path

    def state_path(self, *parts: str) -> Path:
        path = self.state_dir.joinpath(*parts)
        return path

    def cache_path(self, *parts: str) -> Path:
        path = self.cache_dir.joinpath(*parts)
        return path

    def secrets_dir(self) -> Path:
        path = self.config_path("secrets")
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except Exception:
            pass
        return path

    def secrets_path(self, *parts: str) -> Path:
        base = self.secrets_dir()
        return base.joinpath(*parts)


@dataclass(frozen=True, slots=True)
class SuiteStorageNamespace:
    suite_name: str
    config_root: Path
    data_root: Path
    state_root: Path
    cache_root: Path

    def app(self, app_name: str) -> AppStorageNamespace:
        app_slug = _slug(app_name, fallback="app")
        return AppStorageNamespace(
            suite_name=self.suite_name,
            app_name=app_slug,
            config_dir=self.config_root / app_slug,
            data_dir=self.data_root / app_slug,
            state_dir=self.state_root / app_slug,
            cache_dir=self.cache_root / app_slug,
        )


def suite_storage_namespace(suite_name: str) -> SuiteStorageNamespace:
    suite_slug = _slug(suite_name, fallback="suite")
    home = Path.home()
    config_home = _xdg_home("XDG_CONFIG_HOME", home / ".config")
    data_home = _xdg_home("XDG_DATA_HOME", home / ".local" / "share")
    state_home = _xdg_home("XDG_STATE_HOME", home / ".local" / "state")
    cache_home = _xdg_home("XDG_CACHE_HOME", home / ".cache")
    return SuiteStorageNamespace(
        suite_name=suite_slug,
        config_root=config_home / suite_slug,
        data_root=data_home / suite_slug,
        state_root=state_home / suite_slug,
        cache_root=cache_home / suite_slug,
    )


def migrate_file_if_missing(source: Path, destination: Path) -> bool:
    src = Path(source).expanduser()
    dst = Path(destination).expanduser()
    if not src.is_file():
        return False
    if dst.exists():
        return False
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False


def migrate_tree_if_missing(source: Path, destination: Path) -> bool:
    src = Path(source).expanduser()
    dst = Path(destination).expanduser()
    if not src.is_dir():
        return False
    if dst.exists():
        return False
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
        return True
    except Exception:
        return False


def merge_tree_missing(source: Path, destination: Path) -> int:
    src = Path(source).expanduser()
    dst = Path(destination).expanduser()
    if not src.is_dir():
        return 0
    copied = 0
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(src)
        target = dst / relative
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied += 1
        except Exception:
            continue
    return copied
