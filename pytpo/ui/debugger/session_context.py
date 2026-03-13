from __future__ import annotations

import os
from dataclasses import dataclass, field

from .backend import DebugLaunchKind


@dataclass(slots=True)
class DebugSessionContext:
    file_path: str = ""
    launch_kind: DebugLaunchKind = DebugLaunchKind.SCRIPT
    module_name: str = ""
    program_path: str = ""
    working_directory: str = ""
    interpreter: str = ""
    arguments: tuple[str, ...] = field(default_factory=tuple)
    environment: dict[str, str] = field(default_factory=dict)

    def resolved_working_directory(self) -> str:
        if self.working_directory:
            return self.working_directory
        if self.file_path:
            return os.path.dirname(self.file_path)
        return ""
