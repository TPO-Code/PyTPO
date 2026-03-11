from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from TPOPyside.dialogs import FieldBinding, SchemaField, SchemaPage, SchemaSection, SchemaSettingsDialog, SettingsSchema

from .settings import DEFAULT_THEME_NAME, TerminalSettings, TerminalSettingsStore
from .theme_manager import TerminalThemeManager

TERMINAL_SCOPE = "terminal"
_PROMPT_EDITOR_WIDGET_CLASS: type[QWidget] | None = None
_PROMPT_EDITOR_LOAD_ERROR: str | None = None


class TerminalSettingsBackend:
    def __init__(self, store: TerminalSettingsStore) -> None:
        self._store = store
        self._values = store.load().to_dict()
        self._defaults = TerminalSettings().to_dict()
        self._dirty_scopes: set[str] = set()

    def get(
        self,
        key: str,
        scope_preference: str | None = None,
        *,
        default: Any = None,
    ) -> Any:
        _ = scope_preference
        return self._values.get(str(key), default)

    def set(self, key: str, value: Any, scope: str) -> None:
        skey = str(key)
        if self._values.get(skey) == value:
            return
        self._values[skey] = value
        self._dirty_scopes.add(str(scope or TERMINAL_SCOPE))

    def save_all(
        self,
        scopes: set[str] | None = None,
        *,
        only_dirty: bool = False,
        **kwargs: Any,
    ) -> set[str]:
        _ = kwargs
        target_scopes = {str(scope) for scope in (scopes or {TERMINAL_SCOPE})}
        if TERMINAL_SCOPE not in target_scopes:
            return set()

        if only_dirty and TERMINAL_SCOPE not in self._dirty_scopes:
            return set()

        normalized = self._store.save_mapping(self._values).to_dict()
        self._values = dict(normalized)
        self._dirty_scopes.discard(TERMINAL_SCOPE)
        return {TERMINAL_SCOPE}

    def reload_all(self) -> None:
        self._values = self._store.load().to_dict()
        self._dirty_scopes.clear()

    def restore_scope_defaults(self, scope: str) -> None:
        if str(scope or "") != TERMINAL_SCOPE:
            return
        for key, value in self._defaults.items():
            self._values[key] = value
        self._dirty_scopes.add(TERMINAL_SCOPE)


def _build_schema() -> SettingsSchema:
    defaults = TerminalSettings()

    return SettingsSchema(
        pages=[
            SchemaPage(
                id="terminal.startup",
                category="Terminal",
                title="Startup",
                scope=TERMINAL_SCOPE,
                sections=[
                    SchemaSection(
                        title="Startup",
                        fields=[
                            SchemaField(
                                id="startup_tabs",
                                key="startup_tabs",
                                label="Open tabs on launch",
                                type="spin",
                                scope=TERMINAL_SCOPE,
                                min=1,
                                max=20,
                                default=defaults.startup_tabs,
                            ),
                            SchemaField(
                                id="startup_cwd",
                                key="startup_cwd",
                                label="Default working directory",
                                type="path_dir",
                                scope=TERMINAL_SCOPE,
                                default=defaults.startup_cwd,
                                browse_caption="Select Default Working Directory",
                            ),
                            SchemaField(
                                id="start_maximized",
                                key="start_maximized",
                                label="Start maximized",
                                type="checkbox",
                                scope=TERMINAL_SCOPE,
                                default=defaults.start_maximized,
                            ),
                            SchemaField(
                                id="start_fullscreen",
                                key="start_fullscreen",
                                label="Start full screen",
                                type="checkbox",
                                scope=TERMINAL_SCOPE,
                                default=defaults.start_fullscreen,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="terminal.shell",
                category="Terminal",
                title="Shell",
                scope=TERMINAL_SCOPE,
                sections=[
                    SchemaSection(
                        title="Default Shell",
                        fields=[
                            SchemaField(
                                id="default_shell_mode",
                                key="default_shell_mode",
                                label="Shell mode",
                                type="combo",
                                scope=TERMINAL_SCOPE,
                                default=defaults.default_shell_mode,
                                options=[
                                    {"label": "Auto (SHELL)", "value": "auto"},
                                    {"label": "bash", "value": "bash"},
                                    {"label": "zsh", "value": "zsh"},
                                    {"label": "sh", "value": "sh"},
                                    {"label": "Custom path", "value": "custom"},
                                ],
                            ),
                            SchemaField(
                                id="custom_shell_path",
                                key="custom_shell_path",
                                label="Custom shell path",
                                type="path_file",
                                scope=TERMINAL_SCOPE,
                                default=defaults.custom_shell_path,
                                browse_caption="Select Shell Executable",
                                browse_file_filter="All Files (*)",
                            ),
                            SchemaField(
                                id="shell_login",
                                key="shell_login",
                                label="Launch as login shell",
                                type="checkbox",
                                scope=TERMINAL_SCOPE,
                                default=defaults.shell_login,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="terminal.session",
                category="Terminal",
                title="Session Behavior",
                scope=TERMINAL_SCOPE,
                sections=[
                    SchemaSection(
                        title="Behavior",
                        fields=[
                            SchemaField(
                                id="history_lines",
                                key="history_lines",
                                label="Scrollback lines",
                                type="spin",
                                scope=TERMINAL_SCOPE,
                                min=200,
                                max=300000,
                                default=defaults.history_lines,
                            ),
                            SchemaField(
                                id="show_toolbar",
                                key="show_toolbar",
                                label="Show per-tab toolbar",
                                type="checkbox",
                                scope=TERMINAL_SCOPE,
                                default=defaults.show_toolbar,
                            ),
                            SchemaField(
                                id="confirm_close_running",
                                key="confirm_close_running",
                                label="Warn when closing running jobs",
                                type="checkbox",
                                scope=TERMINAL_SCOPE,
                                default=defaults.confirm_close_running,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="terminal.appearance",
                category="Terminal",
                title="Appearance",
                scope=TERMINAL_SCOPE,
                sections=[
                    SchemaSection(
                        title="Font",
                        fields=[
                            SchemaField(
                                id="font_family",
                                key="font_family",
                                label="Font family",
                                type="font_family",
                                scope=TERMINAL_SCOPE,
                                default=defaults.font_family,
                            ),
                            SchemaField(
                                id="font_size",
                                key="font_size",
                                label="Font size",
                                type="spin",
                                scope=TERMINAL_SCOPE,
                                min=6,
                                max=72,
                                default=defaults.font_size,
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Colors",
                        fields=[
                            SchemaField(
                                id="foreground_color",
                                key="foreground_color",
                                label="Foreground color",
                                type="color",
                                scope=TERMINAL_SCOPE,
                                default=defaults.foreground_color,
                            ),
                            SchemaField(
                                id="background_color",
                                key="background_color",
                                label="Background color",
                                type="color",
                                scope=TERMINAL_SCOPE,
                                default=defaults.background_color,
                            ),
                            SchemaField(
                                id="cursor_color",
                                key="cursor_color",
                                label="Cursor color",
                                type="color",
                                scope=TERMINAL_SCOPE,
                                default=defaults.cursor_color,
                            ),
                            SchemaField(
                                id="link_color",
                                key="link_color",
                                label="Traceback link color",
                                type="color",
                                scope=TERMINAL_SCOPE,
                                default=defaults.link_color,
                            ),
                            SchemaField(
                                id="selection_background_color",
                                key="selection_background_color",
                                label="Selection background",
                                type="color",
                                scope=TERMINAL_SCOPE,
                                default=defaults.selection_background_color,
                            ),
                            SchemaField(
                                id="selection_foreground_color",
                                key="selection_foreground_color",
                                label="Selection text",
                                type="color",
                                scope=TERMINAL_SCOPE,
                                default=defaults.selection_foreground_color,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="terminal.background",
                category="Terminal",
                title="Background Image",
                scope=TERMINAL_SCOPE,
                sections=[
                    SchemaSection(
                        title="Background Image",
                        fields=[
                            SchemaField(
                                id="background_image_path",
                                key="background_image_path",
                                label="Image path",
                                type="path_file",
                                scope=TERMINAL_SCOPE,
                                default=defaults.background_image_path,
                                browse_caption="Select Background Image",
                                browse_file_filter=(
                                    "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif *.svg);;All Files (*)"
                                ),
                            ),
                            SchemaField(
                                id="background_tint_color",
                                key="background_tint_color",
                                label="Tint color",
                                type="color",
                                scope=TERMINAL_SCOPE,
                                default=defaults.background_tint_color,
                            ),
                            SchemaField(
                                id="background_tint_strength",
                                key="background_tint_strength",
                                label="Tint strength (%)",
                                type="spin",
                                scope=TERMINAL_SCOPE,
                                min=0,
                                max=100,
                                default=defaults.background_tint_strength,
                            ),
                            SchemaField(
                                id="background_alpha_mode",
                                key="background_alpha_mode",
                                label="Transparency handling",
                                type="combo",
                                scope=TERMINAL_SCOPE,
                                default=defaults.background_alpha_mode,
                                options=[
                                    {"label": "Preserve image alpha", "value": "preserve"},
                                    {"label": "Flatten image alpha", "value": "flatten"},
                                ],
                            ),
                            SchemaField(
                                id="background_size_mode",
                                key="background_size_mode",
                                label="Image size mode",
                                type="combo",
                                scope=TERMINAL_SCOPE,
                                default=defaults.background_size_mode,
                                options=[
                                    {"label": "tile", "value": "tile"},
                                    {"label": "fit width", "value": "fit width"},
                                    {"label": "fit height", "value": "fit height"},
                                    {"label": "fit", "value": "fit"},
                                    {"label": "stretch", "value": "stretch"},
                                    {"label": "contain", "value": "contain"},
                                    {"label": "center", "value": "center"},
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="terminal.commands",
                category="Terminal",
                title="Quick Commands",
                scope=TERMINAL_SCOPE,
                sections=[
                    SchemaSection(
                        title="Commands",
                        fields=[
                            SchemaField(
                                id="quick_commands",
                                key="quick_commands",
                                label="Quick commands (JSON)",
                                type="json",
                                scope=TERMINAL_SCOPE,
                                description=(
                                    "List of objects: label, cmd, optional cwd/env/dryrun. "
                                    "Example: [{\"label\":\"Run tests\",\"cmd\":\"uv run pytest -q\"}]"
                                ),
                                default=defaults.quick_commands,
                            ),
                            SchemaField(
                                id="command_templates",
                                key="command_templates",
                                label="Templates (JSON)",
                                type="json",
                                scope=TERMINAL_SCOPE,
                                description=(
                                    "Like quick commands, plus optional params list. "
                                    "Example: [{\"label\":\"Install {pkg}\",\"cmd\":\"uv pip install {pkg}\",\"params\":[\"pkg\"]}]"
                                ),
                                default=defaults.command_templates,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="terminal.ansi",
                category="Terminal",
                title="ANSI Palette",
                scope=TERMINAL_SCOPE,
                sections=[
                    SchemaSection(
                        title="Overrides",
                        fields=[
                            SchemaField(
                                id="ansi_colors",
                                key="ansi_colors",
                                label="ANSI color overrides (JSON)",
                                type="json",
                                scope=TERMINAL_SCOPE,
                                description=(
                                    "Use color-name keys like black/red/brightblue and hex values. "
                                    "Example: {\"red\":\"#ff6655\",\"brightwhite\":\"#ffffff\"}"
                                ),
                                default=defaults.ansi_colors,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="terminal.prompt",
                category="Terminal",
                title="Prompt Editor",
                scope=TERMINAL_SCOPE,
                sections=[
                    SchemaSection(
                        title="Shell Prompt",
                        description="Edit shell prompt markup and apply to bash/zsh config files.",
                        fields=[
                            SchemaField(
                                id="prompt_editor_widget",
                                key="prompt_editor_widget",
                                label="Prompt editor",
                                type="prompt_editor_widget",
                                scope=TERMINAL_SCOPE,
                                description="Embedded prompt editor",
                                default=None,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="terminal.theme",
                category="Terminal",
                title="Theme",
                scope=TERMINAL_SCOPE,
                sections=[
                    SchemaSection(
                        title="Application Theme",
                        fields=[
                            SchemaField(
                                id="theme_name",
                                key="theme_name",
                                label="Theme",
                                type="combo",
                                scope=TERMINAL_SCOPE,
                                default=defaults.theme_name,
                                options_provider_id="terminal_theme_options",
                            ),
                        ],
                    ),
                ],
            )
        ]
    )


class TerminalSettingsDialog(SchemaSettingsDialog):
    def __init__(
        self,
        *,
        backend: TerminalSettingsBackend,
        theme_manager: TerminalThemeManager,
        on_applied=None,
        parent: QWidget | None = None,
    ) -> None:
        def _theme_options_provider(_field, _dialog):
            names = list(theme_manager.available_themes())
            if DEFAULT_THEME_NAME not in names:
                names.insert(0, DEFAULT_THEME_NAME)
            return [{"label": name, "value": name} for name in names]

        super().__init__(
            backend=backend,
            schema=_build_schema(),
            initial_page_id="terminal.startup",
            on_applied=on_applied,
            use_native_chrome=False,
            parent=parent,
            object_name="TerminalSettingsDialog",
            window_title="Terminal Settings",
            save_button_text="Save",
            apply_button_text="Apply",
            cancel_button_text="Cancel",
            restore_button_text="Restore Defaults",
            field_factories={
                "prompt_editor_widget": _create_prompt_editor_binding,
            },
            options_providers={
                "terminal_theme_options": _theme_options_provider,
            },
        )


def _load_prompt_editor_widget_class() -> type[QWidget] | None:
    global _PROMPT_EDITOR_WIDGET_CLASS, _PROMPT_EDITOR_LOAD_ERROR
    if _PROMPT_EDITOR_WIDGET_CLASS is not None:
        return _PROMPT_EDITOR_WIDGET_CLASS
    if _PROMPT_EDITOR_LOAD_ERROR is not None:
        return None

    module_path = (Path(__file__).resolve().parent / "prompt-editor.py").resolve()
    if not module_path.is_file():
        _PROMPT_EDITOR_LOAD_ERROR = f"Prompt editor file not found: {module_path}"
        return None

    try:
        spec = importlib.util.spec_from_file_location("terminal_prompt_editor", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not create module spec for prompt editor.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        widget_cls = getattr(module, "PromptEditorWidget", None)
        if not isinstance(widget_cls, type) or not issubclass(widget_cls, QWidget):
            raise RuntimeError("PromptEditorWidget was not found in prompt-editor.py.")
    except Exception as exc:
        _PROMPT_EDITOR_LOAD_ERROR = str(exc)
        return None

    _PROMPT_EDITOR_WIDGET_CLASS = widget_cls
    return _PROMPT_EDITOR_WIDGET_CLASS


def _create_prompt_editor_binding(field: SchemaField, _dialog: SchemaSettingsDialog) -> FieldBinding:
    widget_cls = _load_prompt_editor_widget_class()
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)

    if widget_cls is None:
        message = str(_PROMPT_EDITOR_LOAD_ERROR or "Prompt editor is unavailable.")
        label = QLabel(f"Could not load prompt editor:\n{message}")
        label.setWordWrap(True)
        layout.addWidget(label)
    else:
        layout.addWidget(widget_cls(holder))

    return FieldBinding(
        key=field.key,
        scope=field.scope,
        widget=holder,
        getter=lambda: None,
        setter=lambda _value: None,
        on_change=lambda _callback: None,
        validate=lambda: [],
        persist=False,
        full_row=True,
    )
