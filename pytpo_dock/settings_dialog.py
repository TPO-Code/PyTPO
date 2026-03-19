from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QWidget

from TPOPyside.dialogs.reusable_file_dialog import FileDialog, get_default_starred_paths_settings
from TPOPyside.dialogs.schema_settings_dialog import (
    FieldBinding,
    SchemaField,
    SchemaPage,
    SchemaSection,
    SchemaSettingsDialog,
    SettingsSchema,
)

from .autostart import DockAutostartManager
from .storage_paths import dock_settings_path

DOCK_SCOPE = "dock"
DOCK_SETTINGS_TREE_EXPANDED_PATHS_KEY = "ui.dock.settings_dialog.tree_expanded_paths"
_IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.svg *.ico);;All Files (*)"


def _clamp_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def _normalize_color(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if len(text) == 9 and text.startswith("#"):
        rgb = text[1:7]
        alpha = text[7:9]
        try:
            red = int(rgb[0:2], 16)
            green = int(rgb[2:4], 16)
            blue = int(rgb[4:6], 16)
            alpha_value = int(alpha, 16)
            color = QColor(red, green, blue, alpha_value)
            if color.isValid():
                return f"#{red:02x}{green:02x}{blue:02x}{alpha_value:02x}"
        except Exception:
            return default
    color = QColor(text)
    if not color.isValid():
        return default
    if color.alpha() < 255:
        return f"#{color.red():02x}{color.green():02x}{color.blue():02x}{color.alpha():02x}"
    return color.name(QColor.HexRgb)


def _normalize_choice(value: Any, default: str, *, allowed: set[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        return default
    return normalized


def _normalize_image_path(value: Any, default: str) -> str:
    image_path = str(value or "").strip()
    if not image_path:
        return ""
    try:
        return str(Path(image_path).expanduser())
    except Exception:
        return default


@dataclass(slots=True)
class DockVisualSettings:
    instance_indicator_mode: str = "dots"
    visibility_animation_mode: str = "fade"
    dock_padding: int = 10
    icon_size: int = 42
    icon_opacity: int = 100
    hover_highlight_color: str = "#ffffff"
    hover_highlight_opacity: int = 18
    hover_highlight_radius: int = 12
    focused_window_highlight_color: str = "#f4d269"
    focused_window_highlight_opacity: int = 30
    focused_window_highlight_radius: int = 12
    background_color: str = "#1e1e1ebe"
    background_image_path: str = ""
    background_image_opacity: int = 100
    background_image_fit: str = "cover"
    background_tint: str = "#00000000"
    border_color: str = "#ffffff33"
    border_width: int = 1
    border_radius: int = 18
    border_style: str = "solid"
    preview_background_color: str = "#141414eb"
    preview_background_image_path: str = ""
    preview_background_image_opacity: int = 100
    preview_background_image_fit: str = "cover"
    preview_background_tint: str = "#00000000"
    preview_border_color: str = "#ffffff23"
    preview_border_width: int = 1
    preview_border_radius: int = 14
    preview_border_style: str = "solid"

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None = None) -> "DockVisualSettings":
        raw = dict(values or {})
        defaults = cls()
        indicator_mode = _normalize_choice(
            raw.get("instance_indicator_mode", defaults.instance_indicator_mode),
            defaults.instance_indicator_mode,
            allowed={"dots", "numbers"},
        )
        visibility_animation_mode = _normalize_choice(
            raw.get("visibility_animation_mode", defaults.visibility_animation_mode),
            defaults.visibility_animation_mode,
            allowed={"fade", "slide"},
        )
        image_fit = _normalize_choice(
            raw.get("background_image_fit", defaults.background_image_fit),
            defaults.background_image_fit,
            allowed={"cover", "contain", "stretch", "tile", "center"},
        )
        border_style = _normalize_choice(
            raw.get("border_style", defaults.border_style),
            defaults.border_style,
            allowed={"solid", "dashed", "dotted"},
        )
        preview_image_fit = _normalize_choice(
            raw.get("preview_background_image_fit", defaults.preview_background_image_fit),
            defaults.preview_background_image_fit,
            allowed={"cover", "contain", "stretch", "tile", "center"},
        )
        preview_border_style = _normalize_choice(
            raw.get("preview_border_style", defaults.preview_border_style),
            defaults.preview_border_style,
            allowed={"solid", "dashed", "dotted"},
        )
        image_path = _normalize_image_path(raw.get("background_image_path", defaults.background_image_path), defaults.background_image_path)
        preview_image_path = _normalize_image_path(
            raw.get("preview_background_image_path", defaults.preview_background_image_path),
            defaults.preview_background_image_path,
        )

        return cls(
            instance_indicator_mode=indicator_mode,
            visibility_animation_mode=visibility_animation_mode,
            dock_padding=_clamp_int(raw.get("dock_padding"), defaults.dock_padding, minimum=0, maximum=48),
            icon_size=_clamp_int(raw.get("icon_size"), defaults.icon_size, minimum=16, maximum=96),
            icon_opacity=_clamp_int(raw.get("icon_opacity"), defaults.icon_opacity, minimum=0, maximum=100),
            hover_highlight_color=_normalize_color(raw.get("hover_highlight_color"), defaults.hover_highlight_color),
            hover_highlight_opacity=_clamp_int(
                raw.get("hover_highlight_opacity"),
                defaults.hover_highlight_opacity,
                minimum=0,
                maximum=100,
            ),
            hover_highlight_radius=_clamp_int(
                raw.get("hover_highlight_radius"),
                defaults.hover_highlight_radius,
                minimum=0,
                maximum=48,
            ),
            focused_window_highlight_color=_normalize_color(
                raw.get("focused_window_highlight_color"),
                defaults.focused_window_highlight_color,
            ),
            focused_window_highlight_opacity=_clamp_int(
                raw.get("focused_window_highlight_opacity"),
                defaults.focused_window_highlight_opacity,
                minimum=0,
                maximum=100,
            ),
            focused_window_highlight_radius=_clamp_int(
                raw.get("focused_window_highlight_radius"),
                defaults.focused_window_highlight_radius,
                minimum=0,
                maximum=48,
            ),
            background_color=_normalize_color(raw.get("background_color"), defaults.background_color),
            background_image_path=image_path,
            background_image_opacity=_clamp_int(
                raw.get("background_image_opacity"),
                defaults.background_image_opacity,
                minimum=0,
                maximum=100,
            ),
            background_image_fit=image_fit,
            background_tint=_normalize_color(raw.get("background_tint"), defaults.background_tint),
            border_color=_normalize_color(raw.get("border_color"), defaults.border_color),
            border_width=_clamp_int(raw.get("border_width"), defaults.border_width, minimum=0, maximum=12),
            border_radius=_clamp_int(raw.get("border_radius"), defaults.border_radius, minimum=0, maximum=48),
            border_style=border_style,
            preview_background_color=_normalize_color(
                raw.get("preview_background_color"),
                defaults.preview_background_color,
            ),
            preview_background_image_path=preview_image_path,
            preview_background_image_opacity=_clamp_int(
                raw.get("preview_background_image_opacity"),
                defaults.preview_background_image_opacity,
                minimum=0,
                maximum=100,
            ),
            preview_background_image_fit=preview_image_fit,
            preview_background_tint=_normalize_color(
                raw.get("preview_background_tint"),
                defaults.preview_background_tint,
            ),
            preview_border_color=_normalize_color(
                raw.get("preview_border_color"),
                defaults.preview_border_color,
            ),
            preview_border_width=_clamp_int(
                raw.get("preview_border_width"),
                defaults.preview_border_width,
                minimum=0,
                maximum=12,
            ),
            preview_border_radius=_clamp_int(
                raw.get("preview_border_radius"),
                defaults.preview_border_radius,
                minimum=0,
                maximum=48,
            ),
            preview_border_style=preview_border_style,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "instance_indicator_mode": self.instance_indicator_mode,
            "visibility_animation_mode": self.visibility_animation_mode,
            "dock_padding": self.dock_padding,
            "icon_size": self.icon_size,
            "icon_opacity": self.icon_opacity,
            "hover_highlight_color": self.hover_highlight_color,
            "hover_highlight_opacity": self.hover_highlight_opacity,
            "hover_highlight_radius": self.hover_highlight_radius,
            "focused_window_highlight_color": self.focused_window_highlight_color,
            "focused_window_highlight_opacity": self.focused_window_highlight_opacity,
            "focused_window_highlight_radius": self.focused_window_highlight_radius,
            "background_color": self.background_color,
            "background_image_path": self.background_image_path,
            "background_image_opacity": self.background_image_opacity,
            "background_image_fit": self.background_image_fit,
            "background_tint": self.background_tint,
            "border_color": self.border_color,
            "border_width": self.border_width,
            "border_radius": self.border_radius,
            "border_style": self.border_style,
            "preview_background_color": self.preview_background_color,
            "preview_background_image_path": self.preview_background_image_path,
            "preview_background_image_opacity": self.preview_background_image_opacity,
            "preview_background_image_fit": self.preview_background_image_fit,
            "preview_background_tint": self.preview_background_tint,
            "preview_border_color": self.preview_border_color,
            "preview_border_width": self.preview_border_width,
            "preview_border_radius": self.preview_border_radius,
            "preview_border_style": self.preview_border_style,
        }


def load_dock_settings() -> DockVisualSettings:
    backend = DockSettingsBackend(dock_settings_path())
    values = {key: backend.get(key, scope_preference=DOCK_SCOPE, default=value) for key, value in backend.defaults.items()}
    return DockVisualSettings.from_mapping(values)


class DockSettingsBackend:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._defaults = DockVisualSettings().to_mapping()
        self._values = self._load_values()
        self._dirty_scopes: set[str] = set()

    @property
    def defaults(self) -> dict[str, Any]:
        return dict(self._defaults)

    def _load_values(self) -> dict[str, Any]:
        if not self._path.is_file():
            return dict(self._defaults)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return dict(self._defaults)
        if not isinstance(data, dict):
            return dict(self._defaults)
        merged = dict(self._defaults)
        merged.update({str(key): value for key, value in data.items()})
        return DockVisualSettings.from_mapping(merged).to_mapping()

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
        self._dirty_scopes.add(str(scope or DOCK_SCOPE))

    def save_all(
        self,
        scopes: set[str] | None = None,
        *,
        only_dirty: bool = False,
        **kwargs: Any,
    ) -> set[str]:
        _ = kwargs
        target_scopes = {str(scope) for scope in (scopes or {DOCK_SCOPE})}
        if DOCK_SCOPE not in target_scopes:
            return set()

        if only_dirty and DOCK_SCOPE not in self._dirty_scopes:
            return set()

        normalized = DockVisualSettings.from_mapping(self._values).to_mapping()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
        self._values = dict(normalized)
        self._dirty_scopes.discard(DOCK_SCOPE)
        return {DOCK_SCOPE}

    def reload_all(self) -> None:
        self._values = self._load_values()
        self._dirty_scopes.clear()

    def restore_scope_defaults(self, scope: str) -> None:
        if str(scope or "") != DOCK_SCOPE:
            return
        self._values = dict(self._defaults)
        self._dirty_scopes.add(DOCK_SCOPE)


class DockAutostartFieldController:
    def __init__(self, manager: DockAutostartManager | None = None) -> None:
        self._manager = manager or DockAutostartManager()
        self._applied_state = self._manager.is_enabled()

    def current_state(self) -> bool:
        return self._manager.is_enabled()

    def has_pending_changes(self, checked: bool) -> bool:
        return bool(checked) != self._applied_state

    def apply_checked_state(self, checked: bool) -> list[str]:
        try:
            if checked:
                self._manager.enable()
            else:
                self._manager.disable()
        except OSError as exc:
            return [f"Run on startup: {exc}"]
        self._applied_state = self._manager.is_enabled()
        return []


def _build_autostart_checkbox_binding(field: SchemaField, _dialog: SchemaSettingsDialog) -> FieldBinding:
    checkbox = QCheckBox(field.label)
    controller = DockAutostartFieldController()

    def get_value() -> bool:
        return checkbox.isChecked()

    def set_value(value: Any) -> None:
        checkbox.setChecked(bool(value))

    def connect_change(callback: Callable[..., None]) -> None:
        checkbox.toggled.connect(callback)

    set_value(controller.current_state())
    if field.description:
        checkbox.setToolTip(field.description)

    return FieldBinding(
        field.key,
        field.scope,
        checkbox,
        get_value,
        set_value,
        connect_change,
        lambda: [],
        persist=False,
        has_pending_changes=lambda: controller.has_pending_changes(checkbox.isChecked()),
        apply_changes=lambda: controller.apply_checked_state(checkbox.isChecked()),
    )


def _build_schema() -> SettingsSchema:
    defaults = DockVisualSettings()
    return SettingsSchema(
        pages=[
            SchemaPage(
                id="dock.general",
                category="Dock",
                title="General",
                scope=DOCK_SCOPE,
                description="Control startup behavior for the dock.",
                keywords=["dock", "startup", "autostart", "session"],
                order=0,
                sections=[
                    SchemaSection(
                        title="Startup",
                        description="Create or remove the user's XDG autostart entry for the dock.",
                        fields=[
                            SchemaField(
                                id="run_on_startup",
                                key="run_on_startup",
                                label="Run on startup",
                                type="dock_autostart_checkbox",
                                scope=DOCK_SCOPE,
                                description="Start the PyTPO Dock automatically when your Linux desktop session starts.",
                                default=False,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="dock.appearance",
                category="Dock",
                title="Appearance",
                scope=DOCK_SCOPE,
                description="Customize how the dock looks and how running app instances are shown.",
                keywords=["dock", "appearance", "instances", "background", "border"],
                order=1,
                sections=[
                    SchemaSection(
                        title="Instances",
                        description="Choose how running window counts are shown under dock items.",
                        fields=[
                            SchemaField(
                                id="instance_indicator_mode",
                                key="instance_indicator_mode",
                                label="Instance indicator",
                                type="combo",
                                scope=DOCK_SCOPE,
                                default=defaults.instance_indicator_mode,
                                options=[
                                    {"label": "Dots", "value": "dots"},
                                    {"label": "Numbers", "value": "numbers"},
                                ],
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Layout",
                        description="Adjust dock spacing and icon sizing.",
                        fields=[
                            SchemaField(
                                id="visibility_animation_mode",
                                key="visibility_animation_mode",
                                label="Show or hide animation",
                                type="combo",
                                scope=DOCK_SCOPE,
                                default=defaults.visibility_animation_mode,
                                options=[
                                    {"label": "Fade", "value": "fade"},
                                    {"label": "Slide from bottom", "value": "slide"},
                                ],
                            ),
                            SchemaField(
                                id="dock_padding",
                                key="dock_padding",
                                label="Padding",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.dock_padding,
                                min=0,
                                max=48,
                            ),
                            SchemaField(
                                id="icon_size",
                                key="icon_size",
                                label="Icon size",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.icon_size,
                                min=16,
                                max=96,
                            ),
                            SchemaField(
                                id="icon_opacity",
                                key="icon_opacity",
                                label="Icon opacity",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.icon_opacity,
                                min=0,
                                max=100,
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Highlights",
                        description="Control how dock items look on hover and when one of their windows is focused.",
                        fields=[
                            SchemaField(
                                id="hover_highlight_color",
                                key="hover_highlight_color",
                                label="Hover highlight color",
                                type="color",
                                scope=DOCK_SCOPE,
                                default=defaults.hover_highlight_color,
                            ),
                            SchemaField(
                                id="hover_highlight_opacity",
                                key="hover_highlight_opacity",
                                label="Hover highlight opacity",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.hover_highlight_opacity,
                                min=0,
                                max=100,
                            ),
                            SchemaField(
                                id="hover_highlight_radius",
                                key="hover_highlight_radius",
                                label="Hover highlight radius",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.hover_highlight_radius,
                                min=0,
                                max=48,
                            ),
                            SchemaField(
                                id="focused_window_highlight_color",
                                key="focused_window_highlight_color",
                                label="Focused window color",
                                type="color",
                                scope=DOCK_SCOPE,
                                default=defaults.focused_window_highlight_color,
                            ),
                            SchemaField(
                                id="focused_window_highlight_opacity",
                                key="focused_window_highlight_opacity",
                                label="Focused window opacity",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.focused_window_highlight_opacity,
                                min=0,
                                max=100,
                            ),
                            SchemaField(
                                id="focused_window_highlight_radius",
                                key="focused_window_highlight_radius",
                                label="Focused window radius",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.focused_window_highlight_radius,
                                min=0,
                                max=48,
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Background",
                        description="Style the dock background with color, image, fit mode, and tint.",
                        fields=[
                            SchemaField(
                                id="background_color",
                                key="background_color",
                                label="Background color",
                                type="color",
                                scope=DOCK_SCOPE,
                                default=defaults.background_color,
                            ),
                            SchemaField(
                                id="background_image_path",
                                key="background_image_path",
                                label="Background image",
                                type="path_file",
                                scope=DOCK_SCOPE,
                                default=defaults.background_image_path,
                                browse_provider_id="dock_background_image",
                                browse_caption="Select Dock Background Image",
                                browse_file_filter=_IMAGE_FILTER,
                                browse_button_text="Choose Image",
                            ),
                            SchemaField(
                                id="background_image_fit",
                                key="background_image_fit",
                                label="Image fit",
                                type="combo",
                                scope=DOCK_SCOPE,
                                default=defaults.background_image_fit,
                                options=[
                                    {"label": "Cover", "value": "cover"},
                                    {"label": "Contain", "value": "contain"},
                                    {"label": "Stretch", "value": "stretch"},
                                    {"label": "Tile", "value": "tile"},
                                    {"label": "Center", "value": "center"},
                                ],
                            ),
                            SchemaField(
                                id="background_image_opacity",
                                key="background_image_opacity",
                                label="Image opacity",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.background_image_opacity,
                                min=0,
                                max=100,
                            ),
                            SchemaField(
                                id="background_tint",
                                key="background_tint",
                                label="Tint",
                                type="color",
                                scope=DOCK_SCOPE,
                                default=defaults.background_tint,
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Border",
                        description="Tune the dock border color, thickness, corner radius, and line style.",
                        fields=[
                            SchemaField(
                                id="border_color",
                                key="border_color",
                                label="Border color",
                                type="color",
                                scope=DOCK_SCOPE,
                                default=defaults.border_color,
                            ),
                            SchemaField(
                                id="border_width",
                                key="border_width",
                                label="Border width",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.border_width,
                                min=0,
                                max=12,
                            ),
                            SchemaField(
                                id="border_radius",
                                key="border_radius",
                                label="Corner radius",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.border_radius,
                                min=0,
                                max=48,
                            ),
                            SchemaField(
                                id="border_style",
                                key="border_style",
                                label="Border style",
                                type="combo",
                                scope=DOCK_SCOPE,
                                default=defaults.border_style,
                                options=[
                                    {"label": "Solid", "value": "solid"},
                                    {"label": "Dashed", "value": "dashed"},
                                    {"label": "Dotted", "value": "dotted"},
                                ],
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Preview Panel Background",
                        description="Style the window preview panel with its own color, image, fit mode, and tint.",
                        fields=[
                            SchemaField(
                                id="preview_background_color",
                                key="preview_background_color",
                                label="Preview background color",
                                type="color",
                                scope=DOCK_SCOPE,
                                default=defaults.preview_background_color,
                            ),
                            SchemaField(
                                id="preview_background_image_path",
                                key="preview_background_image_path",
                                label="Preview background image",
                                type="path_file",
                                scope=DOCK_SCOPE,
                                default=defaults.preview_background_image_path,
                                browse_provider_id="dock_preview_background_image",
                                browse_caption="Select Preview Background Image",
                                browse_file_filter=_IMAGE_FILTER,
                                browse_button_text="Choose Image",
                            ),
                            SchemaField(
                                id="preview_background_image_fit",
                                key="preview_background_image_fit",
                                label="Preview image fit",
                                type="combo",
                                scope=DOCK_SCOPE,
                                default=defaults.preview_background_image_fit,
                                options=[
                                    {"label": "Cover", "value": "cover"},
                                    {"label": "Contain", "value": "contain"},
                                    {"label": "Stretch", "value": "stretch"},
                                    {"label": "Tile", "value": "tile"},
                                    {"label": "Center", "value": "center"},
                                ],
                            ),
                            SchemaField(
                                id="preview_background_image_opacity",
                                key="preview_background_image_opacity",
                                label="Preview image opacity",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.preview_background_image_opacity,
                                min=0,
                                max=100,
                            ),
                            SchemaField(
                                id="preview_background_tint",
                                key="preview_background_tint",
                                label="Preview tint",
                                type="color",
                                scope=DOCK_SCOPE,
                                default=defaults.preview_background_tint,
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Preview Panel Border",
                        description="Tune the preview panel border color, thickness, corner radius, and line style.",
                        fields=[
                            SchemaField(
                                id="preview_border_color",
                                key="preview_border_color",
                                label="Preview border color",
                                type="color",
                                scope=DOCK_SCOPE,
                                default=defaults.preview_border_color,
                            ),
                            SchemaField(
                                id="preview_border_width",
                                key="preview_border_width",
                                label="Preview border width",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.preview_border_width,
                                min=0,
                                max=12,
                            ),
                            SchemaField(
                                id="preview_border_radius",
                                key="preview_border_radius",
                                label="Preview corner radius",
                                type="spin",
                                scope=DOCK_SCOPE,
                                default=defaults.preview_border_radius,
                                min=0,
                                max=48,
                            ),
                            SchemaField(
                                id="preview_border_style",
                                key="preview_border_style",
                                label="Preview border style",
                                type="combo",
                                scope=DOCK_SCOPE,
                                default=defaults.preview_border_style,
                                options=[
                                    {"label": "Solid", "value": "solid"},
                                    {"label": "Dashed", "value": "dashed"},
                                    {"label": "Dotted", "value": "dotted"},
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


class DockSettingsDialog(SchemaSettingsDialog):
    def __init__(
        self,
        *,
        on_applied: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            backend=DockSettingsBackend(dock_settings_path()),
            schema=_build_schema(),
            on_applied=on_applied,
            parent=parent,
            object_name="DockSettingsDialog",
            window_title="Dock Settings",
            tree_expanded_paths_key=DOCK_SETTINGS_TREE_EXPANDED_PATHS_KEY,
            tree_expanded_paths_scope=DOCK_SCOPE,
            field_factories={"dock_autostart_checkbox": _build_autostart_checkbox_binding},
            browse_providers={
                "dock_background_image": self._browse_background_image,
                "dock_preview_background_image": self._browse_background_image,
            },
        )

    def _browse_background_image(self, field: SchemaField, _dialog: SchemaSettingsDialog, current_text: str) -> str | None:
        raw = str(current_text or "").strip()
        start_dir = raw
        if raw:
            try:
                path = Path(raw).expanduser()
                start_dir = str(path.parent if path.suffix else path)
            except Exception:
                start_dir = raw
        selected, _selected_filter, _starred_paths = FileDialog.getOpenFileName(
            parent=self,
            caption=str(field.browse_caption or "Select Background Image"),
            directory=start_dir,
            filter=str(field.browse_file_filter or _IMAGE_FILTER),
            starred_paths_settings=get_default_starred_paths_settings(),
        )
        selected = str(selected or "").strip()
        return selected or None
