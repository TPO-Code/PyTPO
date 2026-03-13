"""Shared project/session context passed to UI controllers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class ProjectContext:
    project_root: str
    settings_manager: object
    canonicalize: Callable[[str], str]
    rel_to_project: Callable[[str], str]
    is_path_excluded: Callable[[str, str], bool]
    lint_follow_symlinks_provider: Callable[[], bool]
    config_provider: Callable[[], dict]
    resolve_folder_policy: Callable[[str], dict]
    resolve_interpreter: Callable[[str], str]

    def config(self) -> dict:
        cfg = self.config_provider()
        return cfg if isinstance(cfg, dict) else {}

    def lint_follow_symlinks(self) -> bool:
        return bool(self.lint_follow_symlinks_provider())
