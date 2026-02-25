from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from src.ai.settings_schema import AIAssistSettings, default_ai_settings
from src.core.keybindings import default_keybindings

SettingsScope = Literal["project", "ide"]


class InterpreterOverride(TypedDict, total=False):
    path: str
    python: str
    exclude_from_indexing: bool


class RunSettings(TypedDict, total=False):
    default_cwd: str
    auto_save_before_run: bool
    reuse_existing_output_tab: bool
    clear_output_before_run: bool
    focus_output_on_run: bool
    clear_terminal_before_run: bool
    cmake: dict[str, Any]


class CMakeBuildConfig(TypedDict, total=False):
    name: str
    build_dir: str
    build_type: str
    target: str
    configure_args: str
    build_args: str
    run_args: str
    parallel_jobs: int
    env: list[str] | dict[str, str]


class BuildCMakeSettings(TypedDict, total=False):
    active_config: str
    build_configs: list[CMakeBuildConfig]


class PythonRunConfig(TypedDict, total=False):
    name: str
    script_path: str
    args: str
    working_dir: str
    interpreter: str
    env: list[str] | dict[str, str]


class BuildPythonSettings(TypedDict, total=False):
    active_config: str
    run_configs: list[PythonRunConfig]


class RustRunConfig(TypedDict, total=False):
    name: str
    command_type: str  # run | test | build | custom
    package: str
    binary: str
    profile: str  # debug | release
    features: str
    args: str
    test_filter: str
    command: str
    working_dir: str
    env: list[str] | dict[str, str]


class BuildRustSettings(TypedDict, total=False):
    active_config: str
    run_configs: list[RustRunConfig]


class BuildSettings(TypedDict, total=False):
    cmake: BuildCMakeSettings
    python: BuildPythonSettings
    rust: BuildRustSettings


class InterpretersSettings(TypedDict, total=False):
    default: str
    by_directory: list[InterpreterOverride]


class IndexingSettings(TypedDict, total=False):
    exclude_dirs: list[str]
    exclude_files: list[str]
    follow_symlinks: bool


class ExplorerVisibilitySettings(TypedDict, total=False):
    exclude_dirs: list[str]
    exclude_files: list[str]
    hide_indexing_excluded: bool


class LintArgsSettings(TypedDict, total=False):
    ruff: list[str]
    pyflakes: list[str]


class LintVisualSettings(TypedDict, total=False):
    mode: str
    error_color: str
    warning_color: str
    info_color: str
    hint_color: str
    squiggle_thickness: int
    line_alpha: int


class LintSettings(TypedDict, total=False):
    enabled: bool
    respect_excludes: bool
    debounce_ms: int
    run_on_save: bool
    run_on_idle: bool
    max_problems_per_file: int
    backend: str
    fallback_backend: str
    args: LintArgsSettings
    severity_overrides: dict[str, str]
    visuals: LintVisualSettings


class IdeCompletionSettings(TypedDict, total=False):
    enabled: bool
    respect_excludes: bool
    auto_trigger: bool
    auto_trigger_after_dot: bool
    auto_trigger_min_chars: int
    debounce_ms: int
    backend: str
    max_items: int
    case_sensitive: bool
    show_signatures: bool
    show_right_label: bool
    show_doc_tooltip: bool
    doc_tooltip_delay_ms: int


class CppFallbackSettings(TypedDict, total=False):
    c_standard: str
    cpp_standard: str
    include_paths: list[str]
    defines: list[str]
    extra_flags: list[str] | str


class CppSettings(TypedDict, total=False):
    enable_cpp: bool
    clangd_path: str
    query_driver: str
    compile_commands_mode: str
    compile_commands_path: str
    log_lsp_traffic: bool
    fallback: CppFallbackSettings


class RustSettings(TypedDict, total=False):
    enable_rust: bool
    rust_analyzer_path: str
    rust_analyzer_args: list[str]
    did_change_debounce_ms: int
    log_lsp_traffic: bool
    initialization_options: dict[str, Any]


class EditorSessionEntry(TypedDict, total=False):
    key: str
    file_path: str
    modified: bool


class ProjectSettings(TypedDict, total=False):
    project_name: str
    interpreter: str  # Legacy fallback, retained for compatibility.
    interpreters: InterpretersSettings
    indexing: IndexingSettings
    explorer: ExplorerVisibilitySettings
    build: BuildSettings
    c_cpp: CppSettings
    rust: RustSettings
    open_editors: list[EditorSessionEntry]


class IdeWindowSettings(TypedDict, total=False):
    use_native_chrome: bool
    show_title_in_custom_toolbar: bool


class NewProjectDefaults(TypedDict, total=False):
    name: str
    interpreter: str


class IdeProjectHistorySettings(TypedDict, total=False):
    open_last_project: bool
    max_recent_projects: int
    recent_projects: list[str]
    last_create_in: str


class IdeAutosaveSettings(TypedDict, total=False):
    enabled: bool
    debounce_ms: int


class IdeGitHubSettings(TypedDict, total=False):
    username: str
    use_token_for_git: bool
    last_clone_destination: str
    last_clone_mode: str
    last_clone_url: str


class IdeGitSettings(TypedDict, total=False):
    enable_file_tinting: bool
    tracked_clean_color: str
    tracked_dirty_color: str
    untracked_color: str


class IdeEditorSettings(TypedDict, total=False):
    background_color: str
    background_image_path: str
    background_image_scale_mode: str
    background_image_brightness: int
    background_tint_color: str
    background_tint_strength: int


class IdeFileDialogSettings(TypedDict, total=False):
    background_image_path: str
    background_scale_mode: str
    background_brightness: int
    tint_color: str
    tint_strength: int
    starred_paths: list[str]


class IdeSettings(TypedDict, total=False):
    theme: str
    font_size: int
    font_family: str
    window: IdeWindowSettings
    run: RunSettings
    projects: IdeProjectHistorySettings
    autosave: IdeAutosaveSettings
    lint: LintSettings
    completion: IdeCompletionSettings
    ai_assist: AIAssistSettings
    github: IdeGitHubSettings
    git: IdeGitSettings
    editor: IdeEditorSettings
    file_dialog: IdeFileDialogSettings
    keybindings: dict[str, dict[str, list[str]]]
    defaults: NewProjectDefaults


@dataclass(slots=True, frozen=True)
class SettingsPaths:
    project_root: Path
    ide_app_dir: Path
    project_filename: str = ".tide/project.json"
    ide_filename: str = "ide-settings.json"
    project_file: Path = field(init=False)
    project_ide_dir: Path = field(init=False)
    ide_file: Path = field(init=False)

    def __post_init__(self) -> None:
        project_root = Path(self.project_root).expanduser().resolve()
        ide_app_dir = Path(self.ide_app_dir).expanduser().resolve()
        object.__setattr__(self, "project_root", project_root)
        object.__setattr__(self, "ide_app_dir", ide_app_dir)
        object.__setattr__(self, "project_ide_dir", project_root / ".tide")
        object.__setattr__(self, "project_file", project_root / self.project_filename)
        object.__setattr__(self, "ide_file", ide_app_dir / self.ide_filename)


def default_project_settings() -> ProjectSettings:
    default_excluded_dirs = [".git", ".venv", "__pycache__", "node_modules", ".tide"]
    default_excluded_files = ["*.lock"]
    defaults: ProjectSettings = {
        "project_name": "My Python Project",
        "interpreter": "python",
        "interpreters": {
            "default": "python",
            "by_directory": [],
        },
        "indexing": {
            "exclude_dirs": list(default_excluded_dirs),
            "exclude_files": list(default_excluded_files),
            "follow_symlinks": False,
        },
        "explorer": {
            "exclude_dirs": list(default_excluded_dirs),
            "exclude_files": list(default_excluded_files),
            "hide_indexing_excluded": True,
        },
        "build": {
            "cmake": {
                "active_config": "Debug",
                "build_configs": [
                    {
                        "name": "Debug",
                        "build_dir": "build",
                        "build_type": "Debug",
                        "target": "",
                        "configure_args": "",
                        "build_args": "",
                        "run_args": "",
                        "parallel_jobs": 0,
                        "env": [],
                    },
                    {
                        "name": "Release",
                        "build_dir": "build-release",
                        "build_type": "Release",
                        "target": "",
                        "configure_args": "",
                        "build_args": "",
                        "run_args": "",
                        "parallel_jobs": 0,
                        "env": [],
                    },
                ],
            },
            "python": {
                "active_config": "",
                "run_configs": [],
            },
            "rust": {
                "active_config": "",
                "run_configs": [],
            },
        },
        "c_cpp": {
            "enable_cpp": True,
            "clangd_path": "clangd",
            "query_driver": "",
            "compile_commands_mode": "auto",
            "compile_commands_path": "",
            "log_lsp_traffic": False,
            "fallback": {
                "c_standard": "",
                "cpp_standard": "",
                "include_paths": [],
                "defines": [],
                "extra_flags": [],
            },
        },
        "rust": {
            "enable_rust": True,
            "rust_analyzer_path": "rust-analyzer",
            "rust_analyzer_args": [],
            "did_change_debounce_ms": 260,
            "log_lsp_traffic": False,
            "initialization_options": {},
        },
        "open_editors": [],
    }
    return deepcopy(defaults)


def default_ide_settings() -> IdeSettings:
    defaults: IdeSettings = {
        "theme": "Dark",
        "font_size": 10,
        "font_family": "",
        "window": {
            "use_native_chrome": False,
            "show_title_in_custom_toolbar": True,
        },
        "completion": {
            "enabled": True,
            "respect_excludes": True,
            "auto_trigger": True,
            "auto_trigger_after_dot": True,
            "auto_trigger_min_chars": 2,
            "debounce_ms": 180,
            "backend": "jedi",
            "max_items": 500,
            "case_sensitive": False,
            "show_signatures": True,
            "show_right_label": True,
            "show_doc_tooltip": True,
            "doc_tooltip_delay_ms": 180,
        },
        "ai_assist": default_ai_settings(),
        "github": {
            "username": "",
            "use_token_for_git": True,
            "last_clone_destination": str(Path.home()),
            "last_clone_mode": "my_repos",
            "last_clone_url": "",
        },
        "git": {
            "enable_file_tinting": True,
            "tracked_clean_color": "#7fbf7f",
            "tracked_dirty_color": "#e69f6b",
            "untracked_color": "#c8c8c8",
        },
        "editor": {
            "background_color": "#252526",
            "background_image_path": "",
            "background_image_scale_mode": "stretch",
            "background_image_brightness": 100,
            "background_tint_color": "#000000",
            "background_tint_strength": 0,
        },
        "file_dialog": {
            "background_image_path": "",
            "background_scale_mode": "stretch",
            "background_brightness": 100,
            "tint_color": "#000000",
            "tint_strength": 0,
            "starred_paths": [],
        },
        "keybindings": default_keybindings(),
        "run": {
            "default_cwd": ".",
            "auto_save_before_run": True,
            "reuse_existing_output_tab": True,
            "clear_output_before_run": True,
            "focus_output_on_run": True,
            "clear_terminal_before_run": True,
            "cmake": {
                "build_dir": "build",
                "build_type": "Debug",
                "target": "",
                "configure_args": "",
                "build_args": "",
                "run_args": "",
                "parallel_jobs": 0,
            },
        },
        "projects": {
            "open_last_project": False,
            "max_recent_projects": 10,
            "recent_projects": [],
            "last_create_in": str(Path.home()),
        },
        "autosave": {
            "enabled": False,
            "debounce_ms": 1200,
        },
        "lint": {
            "enabled": True,
            "respect_excludes": True,
            "debounce_ms": 600,
            "run_on_save": True,
            "run_on_idle": True,
            "max_problems_per_file": 200,
            "backend": "ruff",
            "fallback_backend": "ast",
            "args": {
                "ruff": ["check", "--output-format", "json"],
                "pyflakes": [],
            },
            "severity_overrides": {},
            "visuals": {
                "mode": "squiggle",
                "error_color": "#E35D6A",
                "warning_color": "#D6A54A",
                "info_color": "#6AA1FF",
                "hint_color": "#8F9AA5",
                "squiggle_thickness": 2,
                "line_alpha": 64,
            },
        },
        "defaults": {
            "name": "My Python Project",
            "interpreter": "python",
        },
    }
    return deepcopy(defaults)


def typed_dict_to_plain(data: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(data)
