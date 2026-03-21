from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PySide6.QtGui import QColor

from .storage_paths import topbar_settings_path

TOPBAR_SCOPE = "topbar"
MENU_SCOPE = "menu"
MEDIA_SCOPE = "media"
_SUPPORTED_SCOPES = {TOPBAR_SCOPE, MENU_SCOPE, MEDIA_SCOPE}
TOPBAR_SETTINGS_TREE_EXPANDED_PATHS_KEY = "ui.topbar.settings_dialog.tree_expanded_paths"
_VALID_EXPAND_ORIGINS = {"center", "left", "right"}
_VALID_EASING_VALUES = {"ease_in", "ease_out", "ease_in_out", "linear"}
_VALID_BACKGROUND_TYPES = {"solid", "gradient", "image"}
_VALID_GRADIENT_DIRECTIONS = {"horizontal", "vertical", "diagonal_down", "diagonal_up"}
_VALID_IMAGE_FITS = {"fill", "contain", "cover", "stretch", "tile", "center"}
_VALID_IMAGE_ALIGNMENTS = {"center", "top", "bottom", "left", "right"}
_VALID_BUTTON_BACKGROUND_STYLES = {"transparent", "subtle", "filled"}
_VALID_BUTTON_BORDER_STYLES = {"none", "soft", "outline"}
_VALID_BUTTON_INTERACTION_STYLES = {"none", "highlight", "filled", "inset"}
_VALID_TRAY_BUTTON_STYLES = {"match_buttons", "transparent", "filled"}
_VALID_SCROLLBAR_VISIBILITY = {"auto", "always", "hidden"}
_VALID_MEDIA_INTERACTION_MODES = {"full_media_controls", "application_volume_only"}


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _clamp_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def _normalize_expand_origin(value: Any, default: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _VALID_EXPAND_ORIGINS:
        return default
    return normalized


def _normalize_easing(value: Any, default: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _VALID_EASING_VALUES:
        return default
    return normalized


def _normalize_choice(value: Any, default: str, *, allowed: set[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        return default
    return normalized


def _normalize_color(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if len(text) == 9 and text.startswith("#"):
        try:
            color = QColor(
                int(text[1:3], 16),
                int(text[3:5], 16),
                int(text[5:7], 16),
                int(text[7:9], 16),
            )
        except Exception:
            return default
        if color.isValid():
            return f"#{color.red():02x}{color.green():02x}{color.blue():02x}{color.alpha():02x}"
        return default
    color = QColor(text)
    if not color.isValid():
        return default
    if color.alpha() < 255:
        return f"#{color.red():02x}{color.green():02x}{color.blue():02x}{color.alpha():02x}"
    return color.name(QColor.HexRgb)


def _normalize_image_path(value: Any, default: str) -> str:
    image_path = str(value or "").strip()
    if not image_path:
        return ""
    try:
        return str(Path(image_path).expanduser())
    except Exception:
        return default


def _scope_field_names() -> dict[str, set[str]]:
    all_fields = set(TopBarBehaviorSettings.__dataclass_fields__.keys())
    menu_fields = {name for name in all_fields if name.startswith("menu_appearance_")}
    media_fields = {name for name in all_fields if name.startswith("media_")}
    topbar_fields = all_fields - menu_fields - media_fields
    return {
        TOPBAR_SCOPE: topbar_fields,
        MENU_SCOPE: menu_fields,
        MEDIA_SCOPE: media_fields,
    }


@dataclass(slots=True)
class TopBarBehaviorSettings:
    auto_hide: bool = False
    auto_hide_effect_slide: bool = False
    auto_hide_effect_fade: bool = False
    auto_hide_effect_expand_width: bool = False
    auto_hide_reveal_delay_ms: int = 150
    auto_hide_hide_delay_ms: int = 400
    auto_hide_animation_duration_ms: int = 180
    auto_hide_reveal_distance_px: int = 2
    auto_hide_expand_origin: str = "center"
    auto_hide_expand_initial_width_percent: int = 84
    auto_hide_show_easing: str = "ease_out"
    auto_hide_hide_easing: str = "ease_in"
    reserve_screen_space: bool = True
    appearance_background_type: str = "solid"
    appearance_background_color: str = "#5b5b5b"
    appearance_gradient_start_color: str = "#5b5b5b"
    appearance_gradient_end_color: str = "#3f3f3f"
    appearance_gradient_direction: str = "horizontal"
    appearance_gradient_opacity: int = 100
    appearance_background_opacity: int = 100
    appearance_background_blur: int = 0
    appearance_background_image_path: str = ""
    appearance_image_fit_mode: str = "cover"
    appearance_image_alignment: str = "center"
    appearance_image_opacity: int = 100
    appearance_overlay_tint: str = "#000000"
    appearance_overlay_tint_opacity: int = 0
    appearance_height: int = 35
    appearance_corner_radius: int = 0
    appearance_top_margin: int = 0
    appearance_left_margin: int = 0
    appearance_right_margin: int = 0
    appearance_internal_padding: int = 15
    appearance_show_border: bool = False
    appearance_border_width: int = 1
    appearance_border_color: str = "#ffffff"
    appearance_border_opacity: int = 18
    appearance_show_shadow: bool = False
    appearance_shadow_blur: int = 20
    appearance_shadow_offset_x: int = 0
    appearance_shadow_offset_y: int = 4
    appearance_shadow_opacity: int = 30
    appearance_section_spacing: int = 24
    appearance_widget_spacing: int = 8
    appearance_left_section_spacing: int = 8
    appearance_center_section_spacing: int = 8
    appearance_right_section_spacing: int = 8
    appearance_button_size: int = 28
    appearance_button_padding: int = 8
    appearance_button_corner_radius: int = 8
    appearance_button_background_style: str = "subtle"
    appearance_button_border_style: str = "soft"
    appearance_button_hover_style: str = "highlight"
    appearance_button_pressed_style: str = "inset"
    appearance_button_icon_size: int = 18
    appearance_label_font_family: str = ""
    appearance_label_font_size: int = 11
    appearance_label_font_weight: int = 600
    appearance_label_text_color: str = "#f1f1f1"
    appearance_label_text_shadow: bool = False
    appearance_show_clock: bool = True
    appearance_time_format: str = "h:mm:ss AP"
    appearance_date_format: str = "dd/MM/yyyy"
    appearance_clock_font_family: str = ""
    appearance_clock_size: int = 11
    appearance_clock_color: str = "#f1f1f1"
    appearance_tray_icon_size: int = 20
    appearance_tray_icon_spacing: int = 4
    appearance_tray_button_style: str = "match_buttons"
    menu_appearance_background_type: str = "solid"
    menu_appearance_background_color: str = "#2d3236"
    menu_appearance_gradient_start_color: str = "#353c41"
    menu_appearance_gradient_end_color: str = "#24282b"
    menu_appearance_gradient_direction: str = "vertical"
    menu_appearance_gradient_opacity: int = 100
    menu_appearance_background_opacity: int = 100
    menu_appearance_background_blur: int = 0
    menu_appearance_background_image_path: str = ""
    menu_appearance_image_fit_mode: str = "cover"
    menu_appearance_image_alignment: str = "center"
    menu_appearance_image_opacity: int = 100
    menu_appearance_overlay_tint: str = "#000000"
    menu_appearance_overlay_tint_opacity: int = 0
    menu_appearance_corner_radius: int = 18
    menu_appearance_panel_width: int = 400
    menu_appearance_panel_max_height: int = 720
    menu_appearance_outer_margin: int = 12
    menu_appearance_internal_padding: int = 12
    menu_appearance_show_border: bool = False
    menu_appearance_border_width: int = 1
    menu_appearance_border_color: str = "#ffffff"
    menu_appearance_border_opacity: int = 14
    menu_appearance_show_shadow: bool = False
    menu_appearance_shadow_blur: int = 24
    menu_appearance_shadow_offset_x: int = 0
    menu_appearance_shadow_offset_y: int = 8
    menu_appearance_shadow_opacity: int = 30
    menu_appearance_section_spacing: int = 12
    menu_appearance_section_header_font_family: str = ""
    menu_appearance_section_header_font_size: int = 11
    menu_appearance_section_header_color: str = "#f2f4f5"
    menu_appearance_item_height: int = 30
    menu_appearance_item_padding: int = 10
    menu_appearance_item_spacing: int = 8
    menu_appearance_item_corner_radius: int = 10
    menu_appearance_item_background: str = "#ffffff14"
    menu_appearance_item_hover_background: str = "#ffffff22"
    menu_appearance_item_active_background: str = "#ffffff30"
    menu_appearance_item_text_color: str = "#f2f4f5"
    menu_appearance_item_secondary_text_color: str = "#c5ccd0"
    menu_appearance_item_icon_size: int = 16
    menu_appearance_scrollbar_visibility: str = "auto"
    menu_appearance_scrollbar_width: int = 8
    menu_appearance_scrollbar_corner_radius: int = 4
    menu_appearance_scrollbar_color: str = "#ffffff40"
    media_controls_show_media_players: bool = True
    media_controls_show_application_volumes: bool = True
    media_controls_interaction_mode: str = "full_media_controls"
    media_controls_show_seek_controls: bool = True
    media_controls_show_play_pause: bool = True
    media_controls_show_stop: bool = False
    media_controls_show_previous_next: bool = True
    media_controls_show_position_scrubbing: bool = True
    media_controls_show_shuffle: bool = True
    media_controls_show_loop: bool = True
    media_controls_show_volume_slider: bool = True
    media_controls_show_player_name: bool = True
    media_controls_prefer_active_player_first: bool = False
    media_cards_spacing: int = 6
    media_cards_internal_padding: int = 8
    media_cards_button_size: int = 24
    media_cards_seek_bar_thickness: int = 6
    media_cards_background_type: str = "solid"
    media_cards_background_color: str = "#ffffff12"
    media_cards_gradient_start_color: str = "#ffffff18"
    media_cards_gradient_end_color: str = "#ffffff08"
    media_cards_gradient_direction: str = "vertical"
    media_cards_gradient_opacity: int = 100
    media_cards_background_opacity: int = 100
    media_cards_background_blur: int = 0
    media_cards_background_image_path: str = ""
    media_cards_image_fit_mode: str = "cover"
    media_cards_image_alignment: str = "center"
    media_cards_image_opacity: int = 100
    media_cards_overlay_tint: str = "#000000"
    media_cards_overlay_tint_opacity: int = 0
    media_cards_corner_radius: int = 14
    media_cards_show_border: bool = False
    media_cards_border_width: int = 1
    media_cards_border_color: str = "#ffffff"
    media_cards_border_opacity: int = 14
    media_cards_show_shadow: bool = False
    media_cards_shadow_blur: int = 18
    media_cards_shadow_offset_x: int = 0
    media_cards_shadow_offset_y: int = 6
    media_cards_shadow_opacity: int = 20
    media_cards_title_font_family: str = ""
    media_cards_title_size: int = 11
    media_cards_title_color: str = "#f6f7f8"
    media_cards_subtitle_font_family: str = ""
    media_cards_subtitle_size: int = 10
    media_cards_subtitle_color: str = "#c8d0d4"
    media_cards_show_secondary_text: bool = True
    media_cards_control_icon_size: int = 16
    media_cards_control_spacing: int = 4
    media_cards_controls_button_corner_radius: int = 8
    media_cards_controls_button_background: str = "#ffffff12"
    media_cards_controls_button_hover_background: str = "#ffffff1f"
    media_cards_controls_button_active_background: str = "#ffffff2c"
    media_cards_controls_button_disabled_opacity: int = 40
    media_cards_progress_color: str = "#70c0ff"
    media_cards_progress_background_color: str = "#ffffff24"
    media_cards_slider_thickness: int = 6

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None = None) -> "TopBarBehaviorSettings":
        raw = dict(values or {})
        defaults = cls()
        return cls(
            auto_hide=_coerce_bool(raw.get("auto_hide"), defaults.auto_hide),
            auto_hide_effect_slide=_coerce_bool(
                raw.get("auto_hide_effect_slide"),
                defaults.auto_hide_effect_slide,
            ),
            auto_hide_effect_fade=_coerce_bool(
                raw.get("auto_hide_effect_fade"),
                defaults.auto_hide_effect_fade,
            ),
            auto_hide_effect_expand_width=_coerce_bool(
                raw.get("auto_hide_effect_expand_width"),
                defaults.auto_hide_effect_expand_width,
            ),
            auto_hide_reveal_delay_ms=_clamp_int(
                raw.get("auto_hide_reveal_delay_ms"),
                defaults.auto_hide_reveal_delay_ms,
                minimum=0,
                maximum=5000,
            ),
            auto_hide_hide_delay_ms=_clamp_int(
                raw.get("auto_hide_hide_delay_ms"),
                defaults.auto_hide_hide_delay_ms,
                minimum=0,
                maximum=5000,
            ),
            auto_hide_animation_duration_ms=_clamp_int(
                raw.get("auto_hide_animation_duration_ms"),
                defaults.auto_hide_animation_duration_ms,
                minimum=0,
                maximum=5000,
            ),
            auto_hide_reveal_distance_px=_clamp_int(
                raw.get("auto_hide_reveal_distance_px"),
                defaults.auto_hide_reveal_distance_px,
                minimum=1,
                maximum=64,
            ),
            auto_hide_expand_origin=_normalize_expand_origin(
                raw.get("auto_hide_expand_origin"),
                defaults.auto_hide_expand_origin,
            ),
            auto_hide_expand_initial_width_percent=_clamp_int(
                raw.get("auto_hide_expand_initial_width_percent"),
                defaults.auto_hide_expand_initial_width_percent,
                minimum=10,
                maximum=100,
            ),
            auto_hide_show_easing=_normalize_easing(
                raw.get("auto_hide_show_easing"),
                defaults.auto_hide_show_easing,
            ),
            auto_hide_hide_easing=_normalize_easing(
                raw.get("auto_hide_hide_easing"),
                defaults.auto_hide_hide_easing,
            ),
            reserve_screen_space=_coerce_bool(
                raw.get("reserve_screen_space"),
                defaults.reserve_screen_space,
            ),
            appearance_background_type=_normalize_choice(
                raw.get("appearance_background_type"),
                defaults.appearance_background_type,
                allowed=_VALID_BACKGROUND_TYPES,
            ),
            appearance_background_color=_normalize_color(
                raw.get("appearance_background_color"),
                defaults.appearance_background_color,
            ),
            appearance_gradient_start_color=_normalize_color(
                raw.get("appearance_gradient_start_color"),
                defaults.appearance_gradient_start_color,
            ),
            appearance_gradient_end_color=_normalize_color(
                raw.get("appearance_gradient_end_color"),
                defaults.appearance_gradient_end_color,
            ),
            appearance_gradient_direction=_normalize_choice(
                raw.get("appearance_gradient_direction"),
                defaults.appearance_gradient_direction,
                allowed=_VALID_GRADIENT_DIRECTIONS,
            ),
            appearance_gradient_opacity=_clamp_int(
                raw.get("appearance_gradient_opacity"),
                defaults.appearance_gradient_opacity,
                minimum=0,
                maximum=100,
            ),
            appearance_background_opacity=_clamp_int(
                raw.get("appearance_background_opacity"),
                defaults.appearance_background_opacity,
                minimum=0,
                maximum=100,
            ),
            appearance_background_blur=_clamp_int(
                raw.get("appearance_background_blur"),
                defaults.appearance_background_blur,
                minimum=0,
                maximum=40,
            ),
            appearance_background_image_path=_normalize_image_path(
                raw.get("appearance_background_image_path"),
                defaults.appearance_background_image_path,
            ),
            appearance_image_fit_mode=_normalize_choice(
                raw.get("appearance_image_fit_mode"),
                defaults.appearance_image_fit_mode,
                allowed=_VALID_IMAGE_FITS,
            ),
            appearance_image_alignment=_normalize_choice(
                raw.get("appearance_image_alignment"),
                defaults.appearance_image_alignment,
                allowed=_VALID_IMAGE_ALIGNMENTS,
            ),
            appearance_image_opacity=_clamp_int(
                raw.get("appearance_image_opacity"),
                defaults.appearance_image_opacity,
                minimum=0,
                maximum=100,
            ),
            appearance_overlay_tint=_normalize_color(
                raw.get("appearance_overlay_tint"),
                defaults.appearance_overlay_tint,
            ),
            appearance_overlay_tint_opacity=_clamp_int(
                raw.get("appearance_overlay_tint_opacity"),
                defaults.appearance_overlay_tint_opacity,
                minimum=0,
                maximum=100,
            ),
            appearance_height=_clamp_int(
                raw.get("appearance_height"),
                defaults.appearance_height,
                minimum=24,
                maximum=96,
            ),
            appearance_corner_radius=_clamp_int(
                raw.get("appearance_corner_radius"),
                defaults.appearance_corner_radius,
                minimum=0,
                maximum=48,
            ),
            appearance_top_margin=_clamp_int(
                raw.get("appearance_top_margin"),
                defaults.appearance_top_margin,
                minimum=0,
                maximum=48,
            ),
            appearance_left_margin=_clamp_int(
                raw.get("appearance_left_margin"),
                defaults.appearance_left_margin,
                minimum=0,
                maximum=96,
            ),
            appearance_right_margin=_clamp_int(
                raw.get("appearance_right_margin"),
                defaults.appearance_right_margin,
                minimum=0,
                maximum=96,
            ),
            appearance_internal_padding=_clamp_int(
                raw.get("appearance_internal_padding"),
                defaults.appearance_internal_padding,
                minimum=0,
                maximum=48,
            ),
            appearance_show_border=_coerce_bool(
                raw.get("appearance_show_border"),
                defaults.appearance_show_border,
            ),
            appearance_border_width=_clamp_int(
                raw.get("appearance_border_width"),
                defaults.appearance_border_width,
                minimum=0,
                maximum=12,
            ),
            appearance_border_color=_normalize_color(
                raw.get("appearance_border_color"),
                defaults.appearance_border_color,
            ),
            appearance_border_opacity=_clamp_int(
                raw.get("appearance_border_opacity"),
                defaults.appearance_border_opacity,
                minimum=0,
                maximum=100,
            ),
            appearance_show_shadow=_coerce_bool(
                raw.get("appearance_show_shadow"),
                defaults.appearance_show_shadow,
            ),
            appearance_shadow_blur=_clamp_int(
                raw.get("appearance_shadow_blur"),
                defaults.appearance_shadow_blur,
                minimum=0,
                maximum=64,
            ),
            appearance_shadow_offset_x=_clamp_int(
                raw.get("appearance_shadow_offset_x"),
                defaults.appearance_shadow_offset_x,
                minimum=-64,
                maximum=64,
            ),
            appearance_shadow_offset_y=_clamp_int(
                raw.get("appearance_shadow_offset_y"),
                defaults.appearance_shadow_offset_y,
                minimum=-64,
                maximum=64,
            ),
            appearance_shadow_opacity=_clamp_int(
                raw.get("appearance_shadow_opacity"),
                defaults.appearance_shadow_opacity,
                minimum=0,
                maximum=100,
            ),
            appearance_section_spacing=_clamp_int(
                raw.get("appearance_section_spacing"),
                defaults.appearance_section_spacing,
                minimum=0,
                maximum=96,
            ),
            appearance_widget_spacing=_clamp_int(
                raw.get("appearance_widget_spacing"),
                defaults.appearance_widget_spacing,
                minimum=0,
                maximum=48,
            ),
            appearance_left_section_spacing=_clamp_int(
                raw.get("appearance_left_section_spacing"),
                defaults.appearance_left_section_spacing,
                minimum=0,
                maximum=48,
            ),
            appearance_center_section_spacing=_clamp_int(
                raw.get("appearance_center_section_spacing"),
                defaults.appearance_center_section_spacing,
                minimum=0,
                maximum=48,
            ),
            appearance_right_section_spacing=_clamp_int(
                raw.get("appearance_right_section_spacing"),
                defaults.appearance_right_section_spacing,
                minimum=0,
                maximum=48,
            ),
            appearance_button_size=_clamp_int(
                raw.get("appearance_button_size"),
                defaults.appearance_button_size,
                minimum=20,
                maximum=72,
            ),
            appearance_button_padding=_clamp_int(
                raw.get("appearance_button_padding"),
                defaults.appearance_button_padding,
                minimum=0,
                maximum=24,
            ),
            appearance_button_corner_radius=_clamp_int(
                raw.get("appearance_button_corner_radius"),
                defaults.appearance_button_corner_radius,
                minimum=0,
                maximum=24,
            ),
            appearance_button_background_style=_normalize_choice(
                raw.get("appearance_button_background_style"),
                defaults.appearance_button_background_style,
                allowed=_VALID_BUTTON_BACKGROUND_STYLES,
            ),
            appearance_button_border_style=_normalize_choice(
                raw.get("appearance_button_border_style"),
                defaults.appearance_button_border_style,
                allowed=_VALID_BUTTON_BORDER_STYLES,
            ),
            appearance_button_hover_style=_normalize_choice(
                raw.get("appearance_button_hover_style"),
                defaults.appearance_button_hover_style,
                allowed=_VALID_BUTTON_INTERACTION_STYLES,
            ),
            appearance_button_pressed_style=_normalize_choice(
                raw.get("appearance_button_pressed_style"),
                defaults.appearance_button_pressed_style,
                allowed=_VALID_BUTTON_INTERACTION_STYLES,
            ),
            appearance_button_icon_size=_clamp_int(
                raw.get("appearance_button_icon_size"),
                defaults.appearance_button_icon_size,
                minimum=12,
                maximum=48,
            ),
            appearance_label_font_family=str(raw.get("appearance_label_font_family", defaults.appearance_label_font_family) or "").strip(),
            appearance_label_font_size=_clamp_int(
                raw.get("appearance_label_font_size"),
                defaults.appearance_label_font_size,
                minimum=8,
                maximum=32,
            ),
            appearance_label_font_weight=_clamp_int(
                raw.get("appearance_label_font_weight"),
                defaults.appearance_label_font_weight,
                minimum=100,
                maximum=900,
            ),
            appearance_label_text_color=_normalize_color(
                raw.get("appearance_label_text_color"),
                defaults.appearance_label_text_color,
            ),
            appearance_label_text_shadow=_coerce_bool(
                raw.get("appearance_label_text_shadow"),
                defaults.appearance_label_text_shadow,
            ),
            appearance_show_clock=_coerce_bool(
                raw.get("appearance_show_clock"),
                defaults.appearance_show_clock,
            ),
            appearance_time_format=str(raw.get("appearance_time_format", defaults.appearance_time_format) or defaults.appearance_time_format),
            appearance_date_format=str(raw.get("appearance_date_format", defaults.appearance_date_format) or defaults.appearance_date_format),
            appearance_clock_font_family=str(raw.get("appearance_clock_font_family", defaults.appearance_clock_font_family) or "").strip(),
            appearance_clock_size=_clamp_int(
                raw.get("appearance_clock_size"),
                defaults.appearance_clock_size,
                minimum=8,
                maximum=32,
            ),
            appearance_clock_color=_normalize_color(
                raw.get("appearance_clock_color"),
                defaults.appearance_clock_color,
            ),
            appearance_tray_icon_size=_clamp_int(
                raw.get("appearance_tray_icon_size"),
                defaults.appearance_tray_icon_size,
                minimum=12,
                maximum=48,
            ),
            appearance_tray_icon_spacing=_clamp_int(
                raw.get("appearance_tray_icon_spacing"),
                defaults.appearance_tray_icon_spacing,
                minimum=0,
                maximum=24,
            ),
            appearance_tray_button_style=_normalize_choice(
                raw.get("appearance_tray_button_style"),
                defaults.appearance_tray_button_style,
                allowed=_VALID_TRAY_BUTTON_STYLES,
            ),
            menu_appearance_background_type=_normalize_choice(
                raw.get("menu_appearance_background_type"),
                defaults.menu_appearance_background_type,
                allowed=_VALID_BACKGROUND_TYPES,
            ),
            menu_appearance_background_color=_normalize_color(
                raw.get("menu_appearance_background_color"),
                defaults.menu_appearance_background_color,
            ),
            menu_appearance_gradient_start_color=_normalize_color(
                raw.get("menu_appearance_gradient_start_color"),
                defaults.menu_appearance_gradient_start_color,
            ),
            menu_appearance_gradient_end_color=_normalize_color(
                raw.get("menu_appearance_gradient_end_color"),
                defaults.menu_appearance_gradient_end_color,
            ),
            menu_appearance_gradient_direction=_normalize_choice(
                raw.get("menu_appearance_gradient_direction"),
                defaults.menu_appearance_gradient_direction,
                allowed=_VALID_GRADIENT_DIRECTIONS,
            ),
            menu_appearance_gradient_opacity=_clamp_int(
                raw.get("menu_appearance_gradient_opacity"),
                defaults.menu_appearance_gradient_opacity,
                minimum=0,
                maximum=100,
            ),
            menu_appearance_background_opacity=_clamp_int(
                raw.get("menu_appearance_background_opacity"),
                defaults.menu_appearance_background_opacity,
                minimum=0,
                maximum=100,
            ),
            menu_appearance_background_blur=_clamp_int(
                raw.get("menu_appearance_background_blur"),
                defaults.menu_appearance_background_blur,
                minimum=0,
                maximum=40,
            ),
            menu_appearance_background_image_path=_normalize_image_path(
                raw.get("menu_appearance_background_image_path"),
                defaults.menu_appearance_background_image_path,
            ),
            menu_appearance_image_fit_mode=_normalize_choice(
                raw.get("menu_appearance_image_fit_mode"),
                defaults.menu_appearance_image_fit_mode,
                allowed=_VALID_IMAGE_FITS,
            ),
            menu_appearance_image_alignment=_normalize_choice(
                raw.get("menu_appearance_image_alignment"),
                defaults.menu_appearance_image_alignment,
                allowed=_VALID_IMAGE_ALIGNMENTS,
            ),
            menu_appearance_image_opacity=_clamp_int(
                raw.get("menu_appearance_image_opacity"),
                defaults.menu_appearance_image_opacity,
                minimum=0,
                maximum=100,
            ),
            menu_appearance_overlay_tint=_normalize_color(
                raw.get("menu_appearance_overlay_tint"),
                defaults.menu_appearance_overlay_tint,
            ),
            menu_appearance_overlay_tint_opacity=_clamp_int(
                raw.get("menu_appearance_overlay_tint_opacity"),
                defaults.menu_appearance_overlay_tint_opacity,
                minimum=0,
                maximum=100,
            ),
            menu_appearance_corner_radius=_clamp_int(
                raw.get("menu_appearance_corner_radius"),
                defaults.menu_appearance_corner_radius,
                minimum=0,
                maximum=48,
            ),
            menu_appearance_panel_width=_clamp_int(
                raw.get("menu_appearance_panel_width"),
                defaults.menu_appearance_panel_width,
                minimum=280,
                maximum=960,
            ),
            menu_appearance_panel_max_height=_clamp_int(
                raw.get("menu_appearance_panel_max_height"),
                defaults.menu_appearance_panel_max_height,
                minimum=240,
                maximum=1600,
            ),
            menu_appearance_outer_margin=_clamp_int(
                raw.get("menu_appearance_outer_margin"),
                defaults.menu_appearance_outer_margin,
                minimum=0,
                maximum=64,
            ),
            menu_appearance_internal_padding=_clamp_int(
                raw.get("menu_appearance_internal_padding"),
                defaults.menu_appearance_internal_padding,
                minimum=0,
                maximum=48,
            ),
            menu_appearance_show_border=_coerce_bool(
                raw.get("menu_appearance_show_border"),
                defaults.menu_appearance_show_border,
            ),
            menu_appearance_border_width=_clamp_int(
                raw.get("menu_appearance_border_width"),
                defaults.menu_appearance_border_width,
                minimum=0,
                maximum=12,
            ),
            menu_appearance_border_color=_normalize_color(
                raw.get("menu_appearance_border_color"),
                defaults.menu_appearance_border_color,
            ),
            menu_appearance_border_opacity=_clamp_int(
                raw.get("menu_appearance_border_opacity"),
                defaults.menu_appearance_border_opacity,
                minimum=0,
                maximum=100,
            ),
            menu_appearance_show_shadow=_coerce_bool(
                raw.get("menu_appearance_show_shadow"),
                defaults.menu_appearance_show_shadow,
            ),
            menu_appearance_shadow_blur=_clamp_int(
                raw.get("menu_appearance_shadow_blur"),
                defaults.menu_appearance_shadow_blur,
                minimum=0,
                maximum=64,
            ),
            menu_appearance_shadow_offset_x=_clamp_int(
                raw.get("menu_appearance_shadow_offset_x"),
                defaults.menu_appearance_shadow_offset_x,
                minimum=-64,
                maximum=64,
            ),
            menu_appearance_shadow_offset_y=_clamp_int(
                raw.get("menu_appearance_shadow_offset_y"),
                defaults.menu_appearance_shadow_offset_y,
                minimum=-64,
                maximum=64,
            ),
            menu_appearance_shadow_opacity=_clamp_int(
                raw.get("menu_appearance_shadow_opacity"),
                defaults.menu_appearance_shadow_opacity,
                minimum=0,
                maximum=100,
            ),
            menu_appearance_section_spacing=_clamp_int(
                raw.get("menu_appearance_section_spacing"),
                defaults.menu_appearance_section_spacing,
                minimum=0,
                maximum=48,
            ),
            menu_appearance_section_header_font_family=str(
                raw.get(
                    "menu_appearance_section_header_font_family",
                    defaults.menu_appearance_section_header_font_family,
                )
                or ""
            ).strip(),
            menu_appearance_section_header_font_size=_clamp_int(
                raw.get("menu_appearance_section_header_font_size"),
                defaults.menu_appearance_section_header_font_size,
                minimum=8,
                maximum=32,
            ),
            menu_appearance_section_header_color=_normalize_color(
                raw.get("menu_appearance_section_header_color"),
                defaults.menu_appearance_section_header_color,
            ),
            menu_appearance_item_height=_clamp_int(
                raw.get("menu_appearance_item_height"),
                defaults.menu_appearance_item_height,
                minimum=22,
                maximum=72,
            ),
            menu_appearance_item_padding=_clamp_int(
                raw.get("menu_appearance_item_padding"),
                defaults.menu_appearance_item_padding,
                minimum=0,
                maximum=24,
            ),
            menu_appearance_item_spacing=_clamp_int(
                raw.get("menu_appearance_item_spacing"),
                defaults.menu_appearance_item_spacing,
                minimum=0,
                maximum=24,
            ),
            menu_appearance_item_corner_radius=_clamp_int(
                raw.get("menu_appearance_item_corner_radius"),
                defaults.menu_appearance_item_corner_radius,
                minimum=0,
                maximum=24,
            ),
            menu_appearance_item_background=_normalize_color(
                raw.get("menu_appearance_item_background"),
                defaults.menu_appearance_item_background,
            ),
            menu_appearance_item_hover_background=_normalize_color(
                raw.get("menu_appearance_item_hover_background"),
                defaults.menu_appearance_item_hover_background,
            ),
            menu_appearance_item_active_background=_normalize_color(
                raw.get("menu_appearance_item_active_background"),
                defaults.menu_appearance_item_active_background,
            ),
            menu_appearance_item_text_color=_normalize_color(
                raw.get("menu_appearance_item_text_color"),
                defaults.menu_appearance_item_text_color,
            ),
            menu_appearance_item_secondary_text_color=_normalize_color(
                raw.get("menu_appearance_item_secondary_text_color"),
                defaults.menu_appearance_item_secondary_text_color,
            ),
            menu_appearance_item_icon_size=_clamp_int(
                raw.get("menu_appearance_item_icon_size"),
                defaults.menu_appearance_item_icon_size,
                minimum=12,
                maximum=48,
            ),
            menu_appearance_scrollbar_visibility=_normalize_choice(
                raw.get("menu_appearance_scrollbar_visibility"),
                defaults.menu_appearance_scrollbar_visibility,
                allowed=_VALID_SCROLLBAR_VISIBILITY,
            ),
            menu_appearance_scrollbar_width=_clamp_int(
                raw.get("menu_appearance_scrollbar_width"),
                defaults.menu_appearance_scrollbar_width,
                minimum=4,
                maximum=24,
            ),
            menu_appearance_scrollbar_corner_radius=_clamp_int(
                raw.get("menu_appearance_scrollbar_corner_radius"),
                defaults.menu_appearance_scrollbar_corner_radius,
                minimum=0,
                maximum=24,
            ),
            menu_appearance_scrollbar_color=_normalize_color(
                raw.get("menu_appearance_scrollbar_color"),
                defaults.menu_appearance_scrollbar_color,
            ),
            media_controls_show_media_players=_coerce_bool(
                raw.get("media_controls_show_media_players"),
                defaults.media_controls_show_media_players,
            ),
            media_controls_show_application_volumes=_coerce_bool(
                raw.get("media_controls_show_application_volumes"),
                defaults.media_controls_show_application_volumes,
            ),
            media_controls_interaction_mode=_normalize_choice(
                raw.get("media_controls_interaction_mode"),
                defaults.media_controls_interaction_mode,
                allowed=_VALID_MEDIA_INTERACTION_MODES,
            ),
            media_controls_show_seek_controls=_coerce_bool(
                raw.get("media_controls_show_seek_controls"),
                defaults.media_controls_show_seek_controls,
            ),
            media_controls_show_play_pause=_coerce_bool(
                raw.get("media_controls_show_play_pause"),
                defaults.media_controls_show_play_pause,
            ),
            media_controls_show_stop=_coerce_bool(
                raw.get("media_controls_show_stop"),
                defaults.media_controls_show_stop,
            ),
            media_controls_show_previous_next=_coerce_bool(
                raw.get("media_controls_show_previous_next"),
                defaults.media_controls_show_previous_next,
            ),
            media_controls_show_position_scrubbing=_coerce_bool(
                raw.get("media_controls_show_position_scrubbing"),
                defaults.media_controls_show_position_scrubbing,
            ),
            media_controls_show_shuffle=_coerce_bool(
                raw.get("media_controls_show_shuffle"),
                defaults.media_controls_show_shuffle,
            ),
            media_controls_show_loop=_coerce_bool(
                raw.get("media_controls_show_loop"),
                defaults.media_controls_show_loop,
            ),
            media_controls_show_volume_slider=_coerce_bool(
                raw.get("media_controls_show_volume_slider"),
                defaults.media_controls_show_volume_slider,
            ),
            media_controls_show_player_name=_coerce_bool(
                raw.get("media_controls_show_player_name"),
                defaults.media_controls_show_player_name,
            ),
            media_controls_prefer_active_player_first=_coerce_bool(
                raw.get("media_controls_prefer_active_player_first"),
                defaults.media_controls_prefer_active_player_first,
            ),
            media_cards_spacing=_clamp_int(
                raw.get("media_cards_spacing"),
                defaults.media_cards_spacing,
                minimum=0,
                maximum=32,
            ),
            media_cards_internal_padding=_clamp_int(
                raw.get("media_cards_internal_padding"),
                defaults.media_cards_internal_padding,
                minimum=0,
                maximum=32,
            ),
            media_cards_button_size=_clamp_int(
                raw.get("media_cards_button_size"),
                defaults.media_cards_button_size,
                minimum=18,
                maximum=56,
            ),
            media_cards_seek_bar_thickness=_clamp_int(
                raw.get("media_cards_seek_bar_thickness"),
                defaults.media_cards_seek_bar_thickness,
                minimum=2,
                maximum=18,
            ),
            media_cards_background_type=_normalize_choice(
                raw.get("media_cards_background_type"),
                defaults.media_cards_background_type,
                allowed=_VALID_BACKGROUND_TYPES,
            ),
            media_cards_background_color=_normalize_color(
                raw.get("media_cards_background_color"),
                defaults.media_cards_background_color,
            ),
            media_cards_gradient_start_color=_normalize_color(
                raw.get("media_cards_gradient_start_color"),
                defaults.media_cards_gradient_start_color,
            ),
            media_cards_gradient_end_color=_normalize_color(
                raw.get("media_cards_gradient_end_color"),
                defaults.media_cards_gradient_end_color,
            ),
            media_cards_gradient_direction=_normalize_choice(
                raw.get("media_cards_gradient_direction"),
                defaults.media_cards_gradient_direction,
                allowed=_VALID_GRADIENT_DIRECTIONS,
            ),
            media_cards_gradient_opacity=_clamp_int(
                raw.get("media_cards_gradient_opacity"),
                defaults.media_cards_gradient_opacity,
                minimum=0,
                maximum=100,
            ),
            media_cards_background_opacity=_clamp_int(
                raw.get("media_cards_background_opacity"),
                defaults.media_cards_background_opacity,
                minimum=0,
                maximum=100,
            ),
            media_cards_background_blur=_clamp_int(
                raw.get("media_cards_background_blur"),
                defaults.media_cards_background_blur,
                minimum=0,
                maximum=40,
            ),
            media_cards_background_image_path=_normalize_image_path(
                raw.get("media_cards_background_image_path"),
                defaults.media_cards_background_image_path,
            ),
            media_cards_image_fit_mode=_normalize_choice(
                raw.get("media_cards_image_fit_mode"),
                defaults.media_cards_image_fit_mode,
                allowed=_VALID_IMAGE_FITS,
            ),
            media_cards_image_alignment=_normalize_choice(
                raw.get("media_cards_image_alignment"),
                defaults.media_cards_image_alignment,
                allowed=_VALID_IMAGE_ALIGNMENTS,
            ),
            media_cards_image_opacity=_clamp_int(
                raw.get("media_cards_image_opacity"),
                defaults.media_cards_image_opacity,
                minimum=0,
                maximum=100,
            ),
            media_cards_overlay_tint=_normalize_color(
                raw.get("media_cards_overlay_tint"),
                defaults.media_cards_overlay_tint,
            ),
            media_cards_overlay_tint_opacity=_clamp_int(
                raw.get("media_cards_overlay_tint_opacity"),
                defaults.media_cards_overlay_tint_opacity,
                minimum=0,
                maximum=100,
            ),
            media_cards_corner_radius=_clamp_int(
                raw.get("media_cards_corner_radius"),
                defaults.media_cards_corner_radius,
                minimum=0,
                maximum=48,
            ),
            media_cards_show_border=_coerce_bool(
                raw.get("media_cards_show_border"),
                defaults.media_cards_show_border,
            ),
            media_cards_border_width=_clamp_int(
                raw.get("media_cards_border_width"),
                defaults.media_cards_border_width,
                minimum=0,
                maximum=12,
            ),
            media_cards_border_color=_normalize_color(
                raw.get("media_cards_border_color"),
                defaults.media_cards_border_color,
            ),
            media_cards_border_opacity=_clamp_int(
                raw.get("media_cards_border_opacity"),
                defaults.media_cards_border_opacity,
                minimum=0,
                maximum=100,
            ),
            media_cards_show_shadow=_coerce_bool(
                raw.get("media_cards_show_shadow"),
                defaults.media_cards_show_shadow,
            ),
            media_cards_shadow_blur=_clamp_int(
                raw.get("media_cards_shadow_blur"),
                defaults.media_cards_shadow_blur,
                minimum=0,
                maximum=64,
            ),
            media_cards_shadow_offset_x=_clamp_int(
                raw.get("media_cards_shadow_offset_x"),
                defaults.media_cards_shadow_offset_x,
                minimum=-64,
                maximum=64,
            ),
            media_cards_shadow_offset_y=_clamp_int(
                raw.get("media_cards_shadow_offset_y"),
                defaults.media_cards_shadow_offset_y,
                minimum=-64,
                maximum=64,
            ),
            media_cards_shadow_opacity=_clamp_int(
                raw.get("media_cards_shadow_opacity"),
                defaults.media_cards_shadow_opacity,
                minimum=0,
                maximum=100,
            ),
            media_cards_title_font_family=str(
                raw.get("media_cards_title_font_family", defaults.media_cards_title_font_family) or ""
            ).strip(),
            media_cards_title_size=_clamp_int(
                raw.get("media_cards_title_size"),
                defaults.media_cards_title_size,
                minimum=8,
                maximum=32,
            ),
            media_cards_title_color=_normalize_color(
                raw.get("media_cards_title_color"),
                defaults.media_cards_title_color,
            ),
            media_cards_subtitle_font_family=str(
                raw.get("media_cards_subtitle_font_family", defaults.media_cards_subtitle_font_family) or ""
            ).strip(),
            media_cards_subtitle_size=_clamp_int(
                raw.get("media_cards_subtitle_size"),
                defaults.media_cards_subtitle_size,
                minimum=8,
                maximum=28,
            ),
            media_cards_subtitle_color=_normalize_color(
                raw.get("media_cards_subtitle_color"),
                defaults.media_cards_subtitle_color,
            ),
            media_cards_show_secondary_text=_coerce_bool(
                raw.get("media_cards_show_secondary_text"),
                defaults.media_cards_show_secondary_text,
            ),
            media_cards_control_icon_size=_clamp_int(
                raw.get("media_cards_control_icon_size"),
                defaults.media_cards_control_icon_size,
                minimum=12,
                maximum=40,
            ),
            media_cards_control_spacing=_clamp_int(
                raw.get("media_cards_control_spacing"),
                defaults.media_cards_control_spacing,
                minimum=0,
                maximum=24,
            ),
            media_cards_controls_button_corner_radius=_clamp_int(
                raw.get("media_cards_controls_button_corner_radius"),
                defaults.media_cards_controls_button_corner_radius,
                minimum=0,
                maximum=24,
            ),
            media_cards_controls_button_background=_normalize_color(
                raw.get("media_cards_controls_button_background"),
                defaults.media_cards_controls_button_background,
            ),
            media_cards_controls_button_hover_background=_normalize_color(
                raw.get("media_cards_controls_button_hover_background"),
                defaults.media_cards_controls_button_hover_background,
            ),
            media_cards_controls_button_active_background=_normalize_color(
                raw.get("media_cards_controls_button_active_background"),
                defaults.media_cards_controls_button_active_background,
            ),
            media_cards_controls_button_disabled_opacity=_clamp_int(
                raw.get("media_cards_controls_button_disabled_opacity"),
                defaults.media_cards_controls_button_disabled_opacity,
                minimum=0,
                maximum=100,
            ),
            media_cards_progress_color=_normalize_color(
                raw.get("media_cards_progress_color"),
                defaults.media_cards_progress_color,
            ),
            media_cards_progress_background_color=_normalize_color(
                raw.get("media_cards_progress_background_color"),
                defaults.media_cards_progress_background_color,
            ),
            media_cards_slider_thickness=_clamp_int(
                raw.get("media_cards_slider_thickness"),
                defaults.media_cards_slider_thickness,
                minimum=2,
                maximum=18,
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


def load_topbar_behavior_settings(path: Path | None = None) -> TopBarBehaviorSettings:
    settings_path = Path(path) if path is not None else topbar_settings_path()
    raw = _read_topbar_settings_mapping(settings_path)
    return TopBarBehaviorSettings.from_mapping(raw)


def save_topbar_behavior_settings(
    settings: TopBarBehaviorSettings,
    path: Path | None = None,
) -> None:
    settings_path = Path(path) if path is not None else topbar_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings.to_mapping(), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _read_topbar_settings_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


class TopBarSettingsBackend:
    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else topbar_settings_path()
        self._settings = TopBarBehaviorSettings()
        self._extra_values: dict[str, Any] = {}
        self.reload_all()

    def get(
        self,
        key: str,
        scope_preference: str | None = None,
        *,
        default: Any = None,
    ) -> Any:
        if str(scope_preference or TOPBAR_SCOPE) not in _SUPPORTED_SCOPES:
            return default
        if hasattr(self._settings, key):
            return getattr(self._settings, key)
        if key in self._extra_values:
            return self._extra_values[key]
        return default

    def set(self, key: str, value: Any, scope: str) -> None:
        if str(scope or TOPBAR_SCOPE) not in _SUPPORTED_SCOPES:
            return
        current = self._settings.to_mapping()
        key_text = str(key or "")
        if key_text in current:
            current[key_text] = value
            self._settings = TopBarBehaviorSettings.from_mapping(current)
            return
        self._extra_values[key_text] = value

    def save_all(
        self,
        scopes: set[str] | None = None,
        *,
        only_dirty: bool = False,
        **kwargs: Any,
    ) -> set[str]:
        del only_dirty, kwargs
        normalized_scopes = {str(scope) for scope in scopes} if scopes is not None else None
        if normalized_scopes is not None and not (_SUPPORTED_SCOPES & normalized_scopes):
            return set()
        payload = self._settings.to_mapping()
        payload.update(self._extra_values)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return set(_SUPPORTED_SCOPES if normalized_scopes is None else (_SUPPORTED_SCOPES & normalized_scopes))

    def reload_all(self) -> None:
        raw = _read_topbar_settings_mapping(self._path)
        self._settings = TopBarBehaviorSettings.from_mapping(raw)
        settings_keys = set(self._settings.to_mapping().keys())
        self._extra_values = {
            str(key): value
            for key, value in raw.items()
            if str(key) not in settings_keys
        }

    def restore_scope_defaults(self, scope: str) -> None:
        normalized_scope = str(scope or TOPBAR_SCOPE)
        if normalized_scope not in _SUPPORTED_SCOPES:
            return
        defaults = TopBarBehaviorSettings()
        current = self._settings.to_mapping()
        for field_name in _scope_field_names().get(normalized_scope, set()):
            current[field_name] = getattr(defaults, field_name)
        self._settings = TopBarBehaviorSettings.from_mapping(current)

    def snapshot(self) -> TopBarBehaviorSettings:
        return TopBarBehaviorSettings.from_mapping(self._settings.to_mapping())
