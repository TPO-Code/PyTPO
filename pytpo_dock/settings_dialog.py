from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

from TPOPyside.dialogs.reusable_file_dialog import FileDialog, get_default_starred_paths_settings
from TPOPyside.dialogs.schema_settings_dialog import (
    SchemaField,
    SchemaPage,
    SchemaSection,
    SchemaSettingsDialog,
    SettingsSchema,
)

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


@dataclass(slots=True)
class DockVisualSettings:
    instance_indicator_mode: str = "dots"
    dock_padding: int = 10
    icon_size: int = 42
    background_color: str = "#1e1e1ebe"
    background_image_path: str = ""
    background_image_fit: str = "cover"
    background_tint: str = "#00000000"
    border_color: str = "#ffffff33"
    border_width: int = 1
    border_radius: int = 18
    border_style: str = "solid"

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None = None) -> "DockVisualSettings":
        raw = dict(values or {})
        defaults = cls()
        indicator_mode = str(raw.get("instance_indicator_mode", defaults.instance_indicator_mode) or "").strip().lower()
        if indicator_mode not in {"dots", "numbers"}:
            indicator_mode = defaults.instance_indicator_mode

        image_fit = str(raw.get("background_image_fit", defaults.background_image_fit) or "").strip().lower()
        if image_fit not in {"cover", "contain", "stretch", "tile", "center"}:
            image_fit = defaults.background_image_fit

        border_style = str(raw.get("border_style", defaults.border_style) or "").strip().lower()
        if border_style not in {"solid", "dashed", "dotted"}:
            border_style = defaults.border_style

        image_path = str(raw.get("background_image_path", defaults.background_image_path) or "").strip()
        if image_path:
            try:
                image_path = str(Path(image_path).expanduser())
            except Exception:
                image_path = defaults.background_image_path

        return cls(
            instance_indicator_mode=indicator_mode,
            dock_padding=_clamp_int(raw.get("dock_padding"), defaults.dock_padding, minimum=0, maximum=48),
            icon_size=_clamp_int(raw.get("icon_size"), defaults.icon_size, minimum=16, maximum=96),
            background_color=_normalize_color(raw.get("background_color"), defaults.background_color),
            background_image_path=image_path,
            background_image_fit=image_fit,
            background_tint=_normalize_color(raw.get("background_tint"), defaults.background_tint),
            border_color=_normalize_color(raw.get("border_color"), defaults.border_color),
            border_width=_clamp_int(raw.get("border_width"), defaults.border_width, minimum=0, maximum=12),
            border_radius=_clamp_int(raw.get("border_radius"), defaults.border_radius, minimum=0, maximum=48),
            border_style=border_style,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "instance_indicator_mode": self.instance_indicator_mode,
            "dock_padding": self.dock_padding,
            "icon_size": self.icon_size,
            "background_color": self.background_color,
            "background_image_path": self.background_image_path,
            "background_image_fit": self.background_image_fit,
            "background_tint": self.background_tint,
            "border_color": self.border_color,
            "border_width": self.border_width,
            "border_radius": self.border_radius,
            "border_style": self.border_style,
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


def _build_schema() -> SettingsSchema:
    defaults = DockVisualSettings()
    return SettingsSchema(
        pages=[
            SchemaPage(
                id="dock.appearance",
                category="Dock",
                title="Appearance",
                scope=DOCK_SCOPE,
                description="Customize how the dock looks and how running app instances are shown.",
                keywords=["dock", "appearance", "instances", "background", "border"],
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
            browse_providers={"dock_background_image": self._browse_background_image},
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
