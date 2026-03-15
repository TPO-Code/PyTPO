from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .storage_paths import appgrid_settings_path

APPGRID_SCOPE = "appgrid"
APPGRID_SETTINGS_TREE_EXPANDED_PATHS_KEY = "ui.appgrid.settings_dialog.tree_expanded_paths"
_IMAGE_FIT_MODES = {"cover", "contain", "stretch", "tile", "center"}
_BORDER_STYLES = {"solid", "dashed", "dotted"}


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
    if text.startswith("#") and len(text) in {7, 9}:
        return text.lower()
    return default


@dataclass(slots=True)
class PanelVisualSettings:
    background_color: str
    background_opacity: int
    background_image_path: str
    background_image_opacity: int
    background_image_fit: str
    background_tint: str
    border_color: str
    border_width: int
    border_radius: int
    border_style: str

    @classmethod
    def from_mapping(
        cls,
        values: dict[str, Any] | None,
        *,
        prefix: str,
        defaults: "PanelVisualSettings",
    ) -> "PanelVisualSettings":
        raw = dict(values or {})
        image_fit = str(raw.get(f"{prefix}background_image_fit", defaults.background_image_fit) or "").strip().lower()
        if image_fit not in _IMAGE_FIT_MODES:
            image_fit = defaults.background_image_fit

        border_style = str(raw.get(f"{prefix}border_style", defaults.border_style) or "").strip().lower()
        if border_style not in _BORDER_STYLES:
            border_style = defaults.border_style

        image_path = str(raw.get(f"{prefix}background_image_path", defaults.background_image_path) or "").strip()
        if image_path:
            try:
                image_path = str(Path(image_path).expanduser())
            except Exception:
                image_path = defaults.background_image_path

        return cls(
            background_color=_normalize_color(raw.get(f"{prefix}background_color"), defaults.background_color),
            background_opacity=_clamp_int(
                raw.get(f"{prefix}background_opacity"),
                defaults.background_opacity,
                minimum=0,
                maximum=100,
            ),
            background_image_path=image_path,
            background_image_opacity=_clamp_int(
                raw.get(f"{prefix}background_image_opacity"),
                defaults.background_image_opacity,
                minimum=0,
                maximum=100,
            ),
            background_image_fit=image_fit,
            background_tint=_normalize_color(raw.get(f"{prefix}background_tint"), defaults.background_tint),
            border_color=_normalize_color(raw.get(f"{prefix}border_color"), defaults.border_color),
            border_width=_clamp_int(raw.get(f"{prefix}border_width"), defaults.border_width, minimum=0, maximum=12),
            border_radius=_clamp_int(raw.get(f"{prefix}border_radius"), defaults.border_radius, minimum=0, maximum=48),
            border_style=border_style,
        )

    def to_mapping(self, *, prefix: str) -> dict[str, Any]:
        return {
            f"{prefix}background_color": self.background_color,
            f"{prefix}background_opacity": self.background_opacity,
            f"{prefix}background_image_path": self.background_image_path,
            f"{prefix}background_image_opacity": self.background_image_opacity,
            f"{prefix}background_image_fit": self.background_image_fit,
            f"{prefix}background_tint": self.background_tint,
            f"{prefix}border_color": self.border_color,
            f"{prefix}border_width": self.border_width,
            f"{prefix}border_radius": self.border_radius,
            f"{prefix}border_style": self.border_style,
        }


@dataclass(slots=True)
class SearchBoxSettings:
    background_color: str = "#181d24ff"
    border_color: str = "#2e3742ff"
    border_width: int = 1
    border_radius: int = 10

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None, *, defaults: "SearchBoxSettings") -> "SearchBoxSettings":
        raw = dict(values or {})
        return cls(
            background_color=_normalize_color(raw.get("search_background_color"), defaults.background_color),
            border_color=_normalize_color(raw.get("search_border_color"), defaults.border_color),
            border_width=_clamp_int(raw.get("search_border_width"), defaults.border_width, minimum=0, maximum=12),
            border_radius=_clamp_int(raw.get("search_border_radius"), defaults.border_radius, minimum=0, maximum=48),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "search_background_color": self.background_color,
            "search_border_color": self.border_color,
            "search_border_width": self.border_width,
            "search_border_radius": self.border_radius,
        }


@dataclass(slots=True)
class AppGridVisualSettings:
    font_color: str = "#f2f4f8ff"
    highlight_color: str = "#284b63ff"
    icon_size: int = 52
    tile_spacing: int = 10
    window: PanelVisualSettings = field(
        default_factory=lambda: PanelVisualSettings(
            background_color="#101317ff",
            background_opacity=100,
            background_image_path="",
            background_image_opacity=100,
            background_image_fit="cover",
            background_tint="#00000000",
            border_color="#2e3742ff",
            border_width=1,
            border_radius=22,
            border_style="solid",
        )
    )
    side_panel: PanelVisualSettings = field(
        default_factory=lambda: PanelVisualSettings(
            background_color="#161b22ff",
            background_opacity=100,
            background_image_path="",
            background_image_opacity=100,
            background_image_fit="cover",
            background_tint="#00000000",
            border_color="#2e3742ff",
            border_width=1,
            border_radius=14,
            border_style="solid",
        )
    )
    app_panel: PanelVisualSettings = field(
        default_factory=lambda: PanelVisualSettings(
            background_color="#161b22ff",
            background_opacity=100,
            background_image_path="",
            background_image_opacity=100,
            background_image_fit="cover",
            background_tint="#00000000",
            border_color="#2e3742ff",
            border_width=1,
            border_radius=14,
            border_style="solid",
        )
    )
    search_box: SearchBoxSettings = field(default_factory=SearchBoxSettings)

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None = None) -> "AppGridVisualSettings":
        raw = dict(values or {})
        defaults = cls()
        return cls(
            font_color=_normalize_color(raw.get("font_color"), defaults.font_color),
            highlight_color=_normalize_color(raw.get("highlight_color"), defaults.highlight_color),
            icon_size=_clamp_int(raw.get("icon_size"), defaults.icon_size, minimum=24, maximum=128),
            tile_spacing=_clamp_int(raw.get("tile_spacing"), defaults.tile_spacing, minimum=0, maximum=48),
            window=PanelVisualSettings.from_mapping(raw, prefix="window_", defaults=defaults.window),
            side_panel=PanelVisualSettings.from_mapping(raw, prefix="side_panel_", defaults=defaults.side_panel),
            app_panel=PanelVisualSettings.from_mapping(raw, prefix="app_panel_", defaults=defaults.app_panel),
            search_box=SearchBoxSettings.from_mapping(raw, defaults=defaults.search_box),
        )

    def to_mapping(self) -> dict[str, Any]:
        values = {
            "font_color": self.font_color,
            "highlight_color": self.highlight_color,
            "icon_size": self.icon_size,
            "tile_spacing": self.tile_spacing,
        }
        values.update(self.window.to_mapping(prefix="window_"))
        values.update(self.side_panel.to_mapping(prefix="side_panel_"))
        values.update(self.app_panel.to_mapping(prefix="app_panel_"))
        values.update(self.search_box.to_mapping())
        return values


def load_appgrid_settings() -> AppGridVisualSettings:
    backend = AppGridSettingsBackend(appgrid_settings_path())
    values = {key: backend.get(key, scope_preference=APPGRID_SCOPE, default=value) for key, value in backend.defaults.items()}
    return AppGridVisualSettings.from_mapping(values)


class AppGridSettingsBackend:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._defaults = AppGridVisualSettings().to_mapping()
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
        return AppGridVisualSettings.from_mapping(merged).to_mapping()

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
        self._dirty_scopes.add(str(scope or APPGRID_SCOPE))

    def save_all(
        self,
        scopes: set[str] | None = None,
        *,
        only_dirty: bool = False,
        **kwargs: Any,
    ) -> set[str]:
        _ = kwargs
        target_scopes = {str(scope) for scope in (scopes or {APPGRID_SCOPE})}
        if APPGRID_SCOPE not in target_scopes:
            return set()
        if only_dirty and APPGRID_SCOPE not in self._dirty_scopes:
            return set()

        normalized = AppGridVisualSettings.from_mapping(self._values).to_mapping()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
        self._values = dict(normalized)
        self._dirty_scopes.discard(APPGRID_SCOPE)
        return {APPGRID_SCOPE}

    def reload_all(self) -> None:
        self._values = self._load_values()
        self._dirty_scopes.clear()

    def restore_scope_defaults(self, scope: str) -> None:
        if str(scope or "") != APPGRID_SCOPE:
            return
        self._values = dict(self._defaults)
        self._dirty_scopes.add(APPGRID_SCOPE)
