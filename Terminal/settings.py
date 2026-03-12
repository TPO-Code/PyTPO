from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtGui import QColor

from .paths import terminal_settings_path, terminal_state_dir

DEFAULT_THEME_NAME = "Default"
DEFAULT_SHELL_MODE = "bash"
DEFAULT_CUSTOM_SHELL_PATH = ""
DEFAULT_SHELL_LOGIN = False
DEFAULT_STARTUP_CWD = ""
DEFAULT_STARTUP_TABS = 1
DEFAULT_START_MAXIMIZED = False
DEFAULT_START_FULLSCREEN = False
DEFAULT_SHOW_TOOLBAR = True
DEFAULT_CONFIRM_CLOSE_RUNNING = True
DEFAULT_HISTORY_LINES = 5000
DEFAULT_FONT_SIZE = 11
DEFAULT_FONT_FAMILY = ""
DEFAULT_FOREGROUND_COLOR = "#d4dbe6"
DEFAULT_BACKGROUND_COLOR = "#000000"
DEFAULT_CURSOR_COLOR = DEFAULT_FOREGROUND_COLOR
DEFAULT_LINK_COLOR = "#7cb2ff"
DEFAULT_SELECTION_BACKGROUND_COLOR = "#2d4c78"
DEFAULT_SELECTION_FOREGROUND_COLOR = "#ffffff"
DEFAULT_BACKGROUND_IMAGE_PATH = ""
DEFAULT_BACKGROUND_TINT_COLOR = "#000000"
DEFAULT_BACKGROUND_TINT_STRENGTH = 35
DEFAULT_BACKGROUND_ALPHA_MODE = "preserve"
DEFAULT_BACKGROUND_SIZE_MODE = "fit"
DEFAULT_DEFAULT_TERMINAL_LAUNCHER_PATH = str(Path.home() / ".local/bin/pytpo-terminal")
DEFAULT_DEFAULT_TERMINAL_DESKTOP_FILE = str(Path.home() / ".local/share/applications/pytpo-terminal.desktop")

_ALLOWED_SHELL_MODES = {"auto", "bash", "zsh", "sh", "custom"}
_ALLOWED_BACKGROUND_ALPHA_MODES = {"preserve", "flatten"}
_ALLOWED_BACKGROUND_SIZE_MODES = {"tile", "fit width", "fit height", "fit", "stretch", "contain", "center"}
_ALLOWED_ANSI_COLOR_NAMES = {
    "black",
    "red",
    "green",
    "brown",
    "blue",
    "magenta",
    "cyan",
    "white",
    "brightblack",
    "brightred",
    "brightgreen",
    "brightbrown",
    "brightblue",
    "brightmagenta",
    "brightcyan",
    "brightwhite",
}


@dataclass(slots=True)
class TerminalSettings:
    theme_name: str = DEFAULT_THEME_NAME
    default_shell_mode: str = DEFAULT_SHELL_MODE
    custom_shell_path: str = DEFAULT_CUSTOM_SHELL_PATH
    shell_login: bool = DEFAULT_SHELL_LOGIN
    startup_cwd: str = DEFAULT_STARTUP_CWD
    startup_tabs: int = DEFAULT_STARTUP_TABS
    start_maximized: bool = DEFAULT_START_MAXIMIZED
    start_fullscreen: bool = DEFAULT_START_FULLSCREEN
    show_toolbar: bool = DEFAULT_SHOW_TOOLBAR
    confirm_close_running: bool = DEFAULT_CONFIRM_CLOSE_RUNNING
    history_lines: int = DEFAULT_HISTORY_LINES
    font_family: str = DEFAULT_FONT_FAMILY
    font_size: int = DEFAULT_FONT_SIZE
    foreground_color: str = DEFAULT_FOREGROUND_COLOR
    background_color: str = DEFAULT_BACKGROUND_COLOR
    cursor_color: str = DEFAULT_CURSOR_COLOR
    link_color: str = DEFAULT_LINK_COLOR
    selection_background_color: str = DEFAULT_SELECTION_BACKGROUND_COLOR
    selection_foreground_color: str = DEFAULT_SELECTION_FOREGROUND_COLOR
    background_image_path: str = DEFAULT_BACKGROUND_IMAGE_PATH
    background_tint_color: str = DEFAULT_BACKGROUND_TINT_COLOR
    background_tint_strength: int = DEFAULT_BACKGROUND_TINT_STRENGTH
    background_alpha_mode: str = DEFAULT_BACKGROUND_ALPHA_MODE
    background_size_mode: str = DEFAULT_BACKGROUND_SIZE_MODE
    default_terminal_launcher_path: str = DEFAULT_DEFAULT_TERMINAL_LAUNCHER_PATH
    default_terminal_desktop_file: str = DEFAULT_DEFAULT_TERMINAL_DESKTOP_FILE
    ansi_colors: dict[str, str] = field(default_factory=dict)
    quick_commands: list[dict[str, Any]] = field(default_factory=list)
    command_templates: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "TerminalSettings":
        data = dict(raw or {})
        return cls(
            theme_name=_normalize_theme_name(data.get("theme_name")),
            default_shell_mode=_normalize_shell_mode(data.get("default_shell_mode")),
            custom_shell_path=_normalize_shell_path(data.get("custom_shell_path")),
            shell_login=_normalize_bool(data.get("shell_login"), DEFAULT_SHELL_LOGIN),
            startup_cwd=_normalize_path_string(data.get("startup_cwd")),
            startup_tabs=_normalize_startup_tabs(data.get("startup_tabs")),
            start_maximized=_normalize_bool(data.get("start_maximized"), DEFAULT_START_MAXIMIZED),
            start_fullscreen=_normalize_bool(data.get("start_fullscreen"), DEFAULT_START_FULLSCREEN),
            show_toolbar=_normalize_bool(data.get("show_toolbar"), DEFAULT_SHOW_TOOLBAR),
            confirm_close_running=_normalize_bool(data.get("confirm_close_running"), DEFAULT_CONFIRM_CLOSE_RUNNING),
            history_lines=_normalize_history_lines(data.get("history_lines")),
            font_family=_normalize_font_family(data.get("font_family")),
            font_size=_normalize_font_size(data.get("font_size")),
            foreground_color=_normalize_color(data.get("foreground_color"), DEFAULT_FOREGROUND_COLOR),
            background_color=_normalize_color(data.get("background_color"), DEFAULT_BACKGROUND_COLOR),
            cursor_color=_normalize_color(data.get("cursor_color"), DEFAULT_CURSOR_COLOR),
            link_color=_normalize_color(data.get("link_color"), DEFAULT_LINK_COLOR),
            selection_background_color=_normalize_color(
                data.get("selection_background_color"),
                DEFAULT_SELECTION_BACKGROUND_COLOR,
            ),
            selection_foreground_color=_normalize_color(
                data.get("selection_foreground_color"),
                DEFAULT_SELECTION_FOREGROUND_COLOR,
            ),
            background_image_path=_normalize_path_string(data.get("background_image_path")),
            background_tint_color=_normalize_color(data.get("background_tint_color"), DEFAULT_BACKGROUND_TINT_COLOR),
            background_tint_strength=_normalize_tint_strength(data.get("background_tint_strength")),
            background_alpha_mode=_normalize_alpha_mode(data.get("background_alpha_mode")),
            background_size_mode=_normalize_background_size_mode(data.get("background_size_mode")),
            default_terminal_launcher_path=(
                _normalize_path_string(data.get("default_terminal_launcher_path"))
                or DEFAULT_DEFAULT_TERMINAL_LAUNCHER_PATH
            ),
            default_terminal_desktop_file=(
                _normalize_path_string(data.get("default_terminal_desktop_file"))
                or DEFAULT_DEFAULT_TERMINAL_DESKTOP_FILE
            ),
            ansi_colors=_normalize_ansi_colors(data.get("ansi_colors")),
            quick_commands=_normalize_command_list(data.get("quick_commands")),
            command_templates=_normalize_command_list(data.get("command_templates")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme_name": _normalize_theme_name(self.theme_name),
            "default_shell_mode": _normalize_shell_mode(self.default_shell_mode),
            "custom_shell_path": _normalize_shell_path(self.custom_shell_path),
            "shell_login": _normalize_bool(self.shell_login, DEFAULT_SHELL_LOGIN),
            "startup_cwd": _normalize_path_string(self.startup_cwd),
            "startup_tabs": _normalize_startup_tabs(self.startup_tabs),
            "start_maximized": _normalize_bool(self.start_maximized, DEFAULT_START_MAXIMIZED),
            "start_fullscreen": _normalize_bool(self.start_fullscreen, DEFAULT_START_FULLSCREEN),
            "show_toolbar": _normalize_bool(self.show_toolbar, DEFAULT_SHOW_TOOLBAR),
            "confirm_close_running": _normalize_bool(self.confirm_close_running, DEFAULT_CONFIRM_CLOSE_RUNNING),
            "history_lines": _normalize_history_lines(self.history_lines),
            "font_family": _normalize_font_family(self.font_family),
            "font_size": _normalize_font_size(self.font_size),
            "foreground_color": _normalize_color(self.foreground_color, DEFAULT_FOREGROUND_COLOR),
            "background_color": _normalize_color(self.background_color, DEFAULT_BACKGROUND_COLOR),
            "cursor_color": _normalize_color(self.cursor_color, DEFAULT_CURSOR_COLOR),
            "link_color": _normalize_color(self.link_color, DEFAULT_LINK_COLOR),
            "selection_background_color": _normalize_color(
                self.selection_background_color,
                DEFAULT_SELECTION_BACKGROUND_COLOR,
            ),
            "selection_foreground_color": _normalize_color(
                self.selection_foreground_color,
                DEFAULT_SELECTION_FOREGROUND_COLOR,
            ),
            "background_image_path": _normalize_path_string(self.background_image_path),
            "background_tint_color": _normalize_color(self.background_tint_color, DEFAULT_BACKGROUND_TINT_COLOR),
            "background_tint_strength": _normalize_tint_strength(self.background_tint_strength),
            "background_alpha_mode": _normalize_alpha_mode(self.background_alpha_mode),
            "background_size_mode": _normalize_background_size_mode(self.background_size_mode),
            "default_terminal_launcher_path": _normalize_path_string(self.default_terminal_launcher_path),
            "default_terminal_desktop_file": _normalize_path_string(self.default_terminal_desktop_file),
            "ansi_colors": _normalize_ansi_colors(self.ansi_colors),
            "quick_commands": _normalize_command_list(self.quick_commands),
            "command_templates": _normalize_command_list(self.command_templates),
        }


def _normalize_theme_name(value: object) -> str:
    text = str(value or "").strip()
    return text or DEFAULT_THEME_NAME


def _normalize_shell_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    if mode in _ALLOWED_SHELL_MODES:
        return mode
    return DEFAULT_SHELL_MODE


def _normalize_shell_path(value: object) -> str:
    return _normalize_path_string(value)


def _normalize_font_family(value: object) -> str:
    return str(value or "").strip()


def _normalize_font_size(value: object) -> int:
    try:
        size = int(value)
    except Exception:
        size = DEFAULT_FONT_SIZE
    return max(6, min(72, size))


def _normalize_tint_strength(value: object) -> int:
    try:
        strength = int(value)
    except Exception:
        strength = DEFAULT_BACKGROUND_TINT_STRENGTH
    return max(0, min(100, strength))


def _normalize_startup_tabs(value: object) -> int:
    try:
        count = int(value)
    except Exception:
        count = DEFAULT_STARTUP_TABS
    return max(1, min(20, count))


def _normalize_history_lines(value: object) -> int:
    try:
        count = int(value)
    except Exception:
        count = DEFAULT_HISTORY_LINES
    return max(200, min(300_000, count))


def _normalize_bool(value: object, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(fallback)


def _normalize_alpha_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    if mode in _ALLOWED_BACKGROUND_ALPHA_MODES:
        return mode
    return DEFAULT_BACKGROUND_ALPHA_MODE


def _normalize_background_size_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    if mode in _ALLOWED_BACKGROUND_SIZE_MODES:
        return mode
    aliases = {
        "fit_width": "fit width",
        "fit_height": "fit height",
        "cover": "fit",
        "centered": "center",
    }
    mapped = aliases.get(mode, "")
    if mapped in _ALLOWED_BACKGROUND_SIZE_MODES:
        return mapped
    return DEFAULT_BACKGROUND_SIZE_MODE


def _normalize_path_string(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(Path(text).expanduser())


def _normalize_color(value: object, fallback: str) -> str:
    color = QColor(str(value or "").strip())
    if not color.isValid():
        color = QColor(str(fallback or "").strip())
    if not color.isValid():
        color = QColor("#000000")
    if color.alpha() < 255:
        return color.name(QColor.HexArgb)
    return color.name(QColor.HexRgb)


def _normalize_ansi_colors(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for raw_name, raw_color in value.items():
        name = str(raw_name or "").strip().lower()
        if name not in _ALLOWED_ANSI_COLOR_NAMES:
            continue
        normalized[name] = _normalize_color(raw_color, "#000000")
    return normalized


def _normalize_command_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("label") or "").strip()
        cmd = str(item.get("cmd") or "").strip()
        if not label or not cmd:
            continue
        entry: dict[str, Any] = {
            "label": label,
            "cmd": cmd,
            "params": _normalize_command_params(item.get("params")),
            "cwd": _normalize_path_string(item.get("cwd")),
            "env": _normalize_command_env(item.get("env")),
            "dryrun": _normalize_bool(item.get("dryrun"), False),
        }
        normalized.append(entry)
    return normalized


def _normalize_command_params(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for raw in value:
        text = str(raw or "").strip()
        if not text:
            continue
        if text in out:
            continue
        out.append(text)
    return out


def _normalize_command_env(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    env: dict[str, str] = {}
    for raw_key, raw_val in value.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        env[key] = str(raw_val or "")
    return env


class TerminalSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else terminal_settings_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> TerminalSettings:
        if not self._path.is_file():
            defaults = TerminalSettings()
            self.save(defaults)
            return defaults

        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        settings = TerminalSettings.from_mapping(payload)
        self.save(settings)
        return settings

    def save(self, settings: TerminalSettings) -> None:
        terminal_state_dir().mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(settings.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def save_mapping(self, values: Mapping[str, Any]) -> TerminalSettings:
        normalized = TerminalSettings.from_mapping(values)
        self.save(normalized)
        return normalized
