from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QWidget

from TPOPyside.dialogs.reusable_file_dialog import FileDialog, get_default_starred_paths_settings
from TPOPyside.dialogs.schema_settings_dialog import (
    SchemaField,
    SchemaPage,
    SchemaSection,
    SchemaSettingsDialog,
    SettingsSchema,
)

from .settings import (
    APPGRID_SCOPE,
    APPGRID_SETTINGS_TREE_EXPANDED_PATHS_KEY,
    AppGridSettingsBackend,
    AppGridVisualSettings,
)
from .storage_paths import appgrid_settings_path
from .theme import load_default_stylesheet
from .main_window import build_window

_IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.svg *.ico);;All Files (*)"


def _panel_background_fields(prefix: str, defaults, *, scope: str) -> list[SchemaField]:
    return [
        SchemaField(
            id=f"{prefix}background_color",
            key=f"{prefix}background_color",
            label="Background color",
            type="color",
            scope=scope,
            default=defaults.background_color,
        ),
        SchemaField(
            id=f"{prefix}background_opacity",
            key=f"{prefix}background_opacity",
            label="Background opacity",
            type="spin",
            scope=scope,
            default=defaults.background_opacity,
            min=0,
            max=100,
        ),
        SchemaField(
            id=f"{prefix}background_image_path",
            key=f"{prefix}background_image_path",
            label="Background image",
            type="path_file",
            scope=scope,
            default=defaults.background_image_path,
            browse_provider_id="appgrid_background_image",
            browse_caption="Select Background Image",
            browse_file_filter=_IMAGE_FILTER,
            browse_button_text="Choose Image",
        ),
        SchemaField(
            id=f"{prefix}background_image_fit",
            key=f"{prefix}background_image_fit",
            label="Image fit",
            type="combo",
            scope=scope,
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
            id=f"{prefix}background_image_opacity",
            key=f"{prefix}background_image_opacity",
            label="Image opacity",
            type="spin",
            scope=scope,
            default=defaults.background_image_opacity,
            min=0,
            max=100,
        ),
        SchemaField(
            id=f"{prefix}background_tint",
            key=f"{prefix}background_tint",
            label="Tint",
            type="color",
            scope=scope,
            default=defaults.background_tint,
        ),
    ]


def _panel_border_fields(prefix: str, defaults, *, scope: str) -> list[SchemaField]:
    return [
        SchemaField(
            id=f"{prefix}border_color",
            key=f"{prefix}border_color",
            label="Border color",
            type="color",
            scope=scope,
            default=defaults.border_color,
        ),
        SchemaField(
            id=f"{prefix}border_width",
            key=f"{prefix}border_width",
            label="Border width",
            type="spin",
            scope=scope,
            default=defaults.border_width,
            min=0,
            max=12,
        ),
        SchemaField(
            id=f"{prefix}border_radius",
            key=f"{prefix}border_radius",
            label="Corner radius",
            type="spin",
            scope=scope,
            default=defaults.border_radius,
            min=0,
            max=48,
        ),
        SchemaField(
            id=f"{prefix}border_style",
            key=f"{prefix}border_style",
            label="Border style",
            type="combo",
            scope=scope,
            default=defaults.border_style,
            options=[
                {"label": "Solid", "value": "solid"},
                {"label": "Dashed", "value": "dashed"},
                {"label": "Dotted", "value": "dotted"},
            ],
        ),
    ]


def _build_schema() -> SettingsSchema:
    defaults = AppGridVisualSettings()
    return SettingsSchema(
        pages=[
            SchemaPage(
                id="appgrid.general",
                category="App Grid",
                title="General",
                scope=APPGRID_SCOPE,
                description="Core visual settings for text, selection, and tile sizing.",
                keywords=["font", "highlight", "icon", "spacing"],
                sections=[
                    SchemaSection(
                        title="Colors",
                        description="Set the main text color and selection highlight color used across the launcher.",
                        fields=[
                            SchemaField(
                                id="font_color",
                                key="font_color",
                                label="Font color",
                                type="color",
                                scope=APPGRID_SCOPE,
                                default=defaults.font_color,
                            ),
                            SchemaField(
                                id="highlight_color",
                                key="highlight_color",
                                label="Highlight color",
                                type="color",
                                scope=APPGRID_SCOPE,
                                default=defaults.highlight_color,
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Tiles",
                        description="Tune icon size and spacing between app tiles without changing the layout structure.",
                        fields=[
                            SchemaField(
                                id="icon_size",
                                key="icon_size",
                                label="Icon size",
                                type="spin",
                                scope=APPGRID_SCOPE,
                                default=defaults.icon_size,
                                min=24,
                                max=128,
                            ),
                            SchemaField(
                                id="tile_spacing",
                                key="tile_spacing",
                                label="Tile spacing",
                                type="spin",
                                scope=APPGRID_SCOPE,
                                default=defaults.tile_spacing,
                                min=0,
                                max=48,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="appgrid.window",
                category="App Grid",
                title="Window",
                scope=APPGRID_SCOPE,
                description="Style the outer launcher background and border.",
                keywords=["window", "background", "border"],
                sections=[
                    SchemaSection(
                        title="Background",
                        description="Set the main window background color, image, tint, and transparency.",
                        fields=_panel_background_fields("window_", defaults.window, scope=APPGRID_SCOPE),
                    ),
                    SchemaSection(
                        title="Border",
                        description="Adjust the outer window border style and rounded corners.",
                        fields=_panel_border_fields("window_", defaults.window, scope=APPGRID_SCOPE),
                    ),
                ],
            ),
            SchemaPage(
                id="appgrid.side_panel",
                category="Panels",
                title="Side Panel",
                scope=APPGRID_SCOPE,
                description="Customize the category panel on the left.",
                keywords=["side", "categories", "panel"],
                sections=[
                    SchemaSection(
                        title="Background",
                        description="Set the side panel background image, tint, scale mode, and transparency.",
                        fields=_panel_background_fields("side_panel_", defaults.side_panel, scope=APPGRID_SCOPE),
                    ),
                    SchemaSection(
                        title="Border",
                        description="Control the side panel border width, radius, style, and color.",
                        fields=_panel_border_fields("side_panel_", defaults.side_panel, scope=APPGRID_SCOPE),
                    ),
                ],
            ),
            SchemaPage(
                id="appgrid.app_panel",
                category="Panels",
                title="App Panel",
                scope=APPGRID_SCOPE,
                description="Customize the application panel on the right.",
                keywords=["apps", "grid", "panel"],
                sections=[
                    SchemaSection(
                        title="Background",
                        description="Set the app panel background image, tint, scale mode, and transparency.",
                        fields=_panel_background_fields("app_panel_", defaults.app_panel, scope=APPGRID_SCOPE),
                    ),
                    SchemaSection(
                        title="Border",
                        description="Control the app panel border width, radius, style, and color.",
                        fields=_panel_border_fields("app_panel_", defaults.app_panel, scope=APPGRID_SCOPE),
                    ),
                ],
            ),
            SchemaPage(
                id="appgrid.search",
                category="App Grid",
                title="Search Box",
                scope=APPGRID_SCOPE,
                description="Tune the search field background and border styling.",
                keywords=["search", "input", "border"],
                sections=[
                    SchemaSection(
                        title="Search Field",
                        description="Set the search box background color and border styling.",
                        fields=[
                            SchemaField(
                                id="search_background_color",
                                key="search_background_color",
                                label="Background color",
                                type="color",
                                scope=APPGRID_SCOPE,
                                default=defaults.search_box.background_color,
                            ),
                            SchemaField(
                                id="search_border_color",
                                key="search_border_color",
                                label="Border color",
                                type="color",
                                scope=APPGRID_SCOPE,
                                default=defaults.search_box.border_color,
                            ),
                            SchemaField(
                                id="search_border_width",
                                key="search_border_width",
                                label="Border width",
                                type="spin",
                                scope=APPGRID_SCOPE,
                                default=defaults.search_box.border_width,
                                min=0,
                                max=12,
                            ),
                            SchemaField(
                                id="search_border_radius",
                                key="search_border_radius",
                                label="Corner radius",
                                type="spin",
                                scope=APPGRID_SCOPE,
                                default=defaults.search_box.border_radius,
                                min=0,
                                max=48,
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


class AppGridSettingsDialog(SchemaSettingsDialog):
    def __init__(
        self,
        *,
        on_applied=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            backend=AppGridSettingsBackend(appgrid_settings_path()),
            schema=_build_schema(),
            on_applied=on_applied,
            parent=parent,
            object_name="AppGridSettingsDialog",
            window_title="App Grid Settings",
            tree_expanded_paths_key=APPGRID_SETTINGS_TREE_EXPANDED_PATHS_KEY,
            tree_expanded_paths_scope=APPGRID_SCOPE,
            browse_providers={"appgrid_background_image": self._browse_background_image},
        )
        stylesheet = load_default_stylesheet()
        if stylesheet.strip():
            self.setStyleSheet(stylesheet)
        self._preview_refresh_timer = QTimer(self)
        self._preview_refresh_timer.setSingleShot(True)
        self._preview_refresh_timer.timeout.connect(self._refresh_preview_window)
        self._preview_window = build_window(
            visual_settings=self._current_visual_settings(),
            close_on_focus_loss=False,
            quit_on_close=False,
        )
        self._preview_window.setWindowTitle("App Grid Preview")
        self._preview_window.show()
        self._connect_live_preview_bindings()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_preview_window()

    def done(self, result: int) -> None:
        if self._preview_window is not None:
            self._preview_window.close()
            self._preview_window.deleteLater()
            self._preview_window = None
        super().done(result)

    def _connect_live_preview_bindings(self) -> None:
        for bindings in self._bindings_by_page.values():
            for binding in bindings:
                if not binding.persist:
                    continue
                binding.on_change(lambda *_args: self._schedule_preview_refresh())

    def _schedule_preview_refresh(self) -> None:
        self._preview_refresh_timer.start(30)

    def _current_visual_settings(self) -> AppGridVisualSettings:
        values = self.backend.defaults
        for bindings in self._bindings_by_page.values():
            for binding in bindings:
                if not binding.persist:
                    continue
                try:
                    values[str(binding.key)] = binding.getter()
                except Exception:
                    continue
        return AppGridVisualSettings.from_mapping(values)

    def _refresh_preview_window(self) -> None:
        if self._preview_window is None:
            return
        self._preview_window.update_visual_settings(self._current_visual_settings())

    def _position_preview_window(self) -> None:
        if self._preview_window is None:
            return
        screen = self.screen()
        if screen is None:
            self._preview_window.move(self.x() + self.width() + 16, self.y())
            return
        available = screen.availableGeometry()
        target = QPoint(self.frameGeometry().right() + 16, self.frameGeometry().top())
        if target.x() + self._preview_window.width() > available.right():
            target.setX(max(available.left(), self.frameGeometry().left() - self._preview_window.width() - 16))
        target.setY(max(available.top(), min(target.y(), available.bottom() - self._preview_window.height())))
        self._preview_window.move(target)

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
