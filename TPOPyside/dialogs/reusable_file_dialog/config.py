from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class SidebarLocation:
    """A user-visible location entry for the dialog sidebar."""

    label: str
    path: str | Path
    icon_name: str = "folder"


@dataclass(slots=True)
class BackgroundOptions:
    """Background and stylesheet options for ReusableFileDialog."""

    image_path: str | Path | None = None
    brightness: float = 1.0
    scale_mode: str = "stretch"
    tint_color: str | None = None
    tint_strength: float = 0.0
    qss_path: str | Path | None = None
    extra_qss: str = ""


@dataclass(slots=True)
class FileDialogResult:
    """Dialog result payload with selected files and the latest starred list."""

    accepted: bool
    selected_files: list[str] = field(default_factory=list)
    selected_name_filter: str = ""
    starred_paths: list[str] = field(default_factory=list)
