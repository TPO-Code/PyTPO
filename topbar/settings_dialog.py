from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QWidget

from TPOPyside.dialogs.reusable_file_dialog import FileDialog
from TPOPyside.dialogs.schema_settings_dialog import (
    SchemaField,
    SchemaPage,
    SchemaSection,
    SchemaSettingsDialog,
    SettingsSchema,
)

from .settings import (
    MEDIA_SCOPE,
    MENU_SCOPE,
    TOPBAR_SCOPE,
    TOPBAR_SETTINGS_TREE_EXPANDED_PATHS_KEY,
    TopBarBehaviorSettings,
    TopBarSettingsBackend,
)

_IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.svg *.ico);;All Files (*)"


def _create_topbar_settings_schema() -> SettingsSchema:
    defaults = TopBarBehaviorSettings()
    auto_hide_visible = [{"key": "auto_hide", "equals": True}]
    expand_visible = [
        {"key": "auto_hide", "equals": True},
        {"key": "auto_hide_effect_expand_width", "equals": True},
    ]
    background_gradient_visible = [{"key": "appearance_background_type", "equals": "gradient"}]
    background_image_visible = [{"key": "appearance_background_type", "equals": "image"}]
    border_visible = [{"key": "appearance_show_border", "equals": True}]
    shadow_visible = [{"key": "appearance_show_shadow", "equals": True}]
    clock_visible = [{"key": "appearance_show_clock", "equals": True}]
    menu_background_gradient_visible = [{"key": "menu_appearance_background_type", "equals": "gradient"}]
    menu_background_image_visible = [{"key": "menu_appearance_background_type", "equals": "image"}]
    menu_border_visible = [{"key": "menu_appearance_show_border", "equals": True}]
    menu_shadow_visible = [{"key": "menu_appearance_show_shadow", "equals": True}]
    full_media_controls_visible = [{"key": "media_controls_interaction_mode", "equals": "full_media_controls"}]
    media_cards_background_gradient_visible = [{"key": "media_cards_background_type", "equals": "gradient"}]
    media_cards_background_image_visible = [{"key": "media_cards_background_type", "equals": "image"}]
    media_cards_border_visible = [{"key": "media_cards_show_border", "equals": True}]
    media_cards_shadow_visible = [{"key": "media_cards_show_shadow", "equals": True}]

    pages = [
        SchemaPage(
            id="topbar-behavior-visibility",
            category="Behavior",
            title="Visibility",
            scope=TOPBAR_SCOPE,
            description="Control when the topbar stays visible and how auto-hide reveals it.",
            scope_order=0,
            category_order=0,
            order=0,
            sections=[
                SchemaSection(
                    title="Topbar Behavior",
                    description="Auto-hide is off by default, so the topbar remains visible.",
                    fields=[
                        SchemaField(
                            id="auto_hide",
                            key="auto_hide",
                            label="Enable auto-hide",
                            type="checkbox",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide,
                            description="Hide the topbar until the pointer reaches the reveal area.",
                        ),
                        SchemaField(
                            id="auto_hide_effect_slide",
                            key="auto_hide_effect_slide",
                            label="Slide",
                            type="checkbox",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide_effect_slide,
                            description="Animate the topbar moving into and out of view.",
                            visible_when=auto_hide_visible,
                        ),
                        SchemaField(
                            id="auto_hide_effect_fade",
                            key="auto_hide_effect_fade",
                            label="Fade",
                            type="checkbox",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide_effect_fade,
                            description="Animate the topbar opacity when showing and hiding.",
                            visible_when=auto_hide_visible,
                        ),
                        SchemaField(
                            id="auto_hide_effect_expand_width",
                            key="auto_hide_effect_expand_width",
                            label="Expand width",
                            type="checkbox",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide_effect_expand_width,
                            description="Animate the topbar from a smaller width to full width.",
                            visible_when=auto_hide_visible,
                        ),
                        SchemaField(
                            id="auto_hide_reveal_distance_px",
                            key="auto_hide_reveal_distance_px",
                            label="Reveal distance (px)",
                            type="spin",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide_reveal_distance_px,
                            min=1,
                            max=64,
                            description="How close the pointer must be to the top edge to reveal the topbar.",
                            visible_when=auto_hide_visible,
                        ),
                        SchemaField(
                            id="auto_hide_reveal_delay_ms",
                            key="auto_hide_reveal_delay_ms",
                            label="Reveal delay (ms)",
                            type="spin",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide_reveal_delay_ms,
                            min=0,
                            max=5000,
                            description="Delay before the hidden topbar is shown.",
                            visible_when=auto_hide_visible,
                        ),
                        SchemaField(
                            id="auto_hide_show_easing",
                            key="auto_hide_show_easing",
                            label="Show easing",
                            type="combo",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide_show_easing,
                            description="How the topbar accelerates when showing.",
                            options=[
                                {"value": "ease_out", "label": "Ease out"},
                                {"value": "ease_in_out", "label": "Ease in/out"},
                                {"value": "ease_in", "label": "Ease in"},
                                {"value": "linear", "label": "Linear"},
                            ],
                            visible_when=auto_hide_visible,
                        ),
                        SchemaField(
                            id="auto_hide_hide_delay_ms",
                            key="auto_hide_hide_delay_ms",
                            label="Hide delay (ms)",
                            type="spin",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide_hide_delay_ms,
                            min=0,
                            max=5000,
                            description="Delay before the topbar hides after the pointer leaves.",
                            visible_when=auto_hide_visible,
                        ),
                        SchemaField(
                            id="auto_hide_hide_easing",
                            key="auto_hide_hide_easing",
                            label="Hide easing",
                            type="combo",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide_hide_easing,
                            description="How the topbar accelerates when hiding.",
                            options=[
                                {"value": "ease_in", "label": "Ease in"},
                                {"value": "ease_in_out", "label": "Ease in/out"},
                                {"value": "ease_out", "label": "Ease out"},
                                {"value": "linear", "label": "Linear"},
                            ],
                            visible_when=auto_hide_visible,
                        ),
                        SchemaField(
                            id="auto_hide_animation_duration_ms",
                            key="auto_hide_animation_duration_ms",
                            label="Animation duration (ms)",
                            type="spin",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide_animation_duration_ms,
                            min=0,
                            max=5000,
                            description="How long auto-hide animations take.",
                            visible_when=auto_hide_visible,
                        ),
                        SchemaField(
                            id="auto_hide_expand_initial_width_percent",
                            key="auto_hide_expand_initial_width_percent",
                            label="Initial width (%)",
                            type="spin",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide_expand_initial_width_percent,
                            min=10,
                            max=100,
                            description="Starting width for width expansion before the topbar reaches full width.",
                            visible_when=expand_visible,
                        ),
                        SchemaField(
                            id="auto_hide_expand_origin",
                            key="auto_hide_expand_origin",
                            label="Expand origin",
                            type="combo",
                            scope=TOPBAR_SCOPE,
                            default=defaults.auto_hide_expand_origin,
                            description="Where width expansion begins.",
                            options=[
                                {"value": "center", "label": "Center"},
                                {"value": "left", "label": "Left"},
                                {"value": "right", "label": "Right"},
                            ],
                            visible_when=expand_visible,
                        ),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="topbar-appearance-background",
            category="Appearance",
            title="Background",
            scope=TOPBAR_SCOPE,
            description="Background fill, image, tint, and opacity settings for the topbar panel.",
            scope_order=0,
            category_order=1,
            order=0,
            sections=[
                SchemaSection(
                    title="Background",
                    fields=[
                        SchemaField(
                            id="appearance_background_type",
                            key="appearance_background_type",
                            label="Background type",
                            type="combo",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_background_type,
                            options=[
                                {"value": "solid", "label": "Solid color"},
                                {"value": "gradient", "label": "Gradient"},
                                {"value": "image", "label": "Image"},
                            ],
                            description="Choose the main background treatment for the topbar panel.",
                        ),
                        SchemaField(
                            id="appearance_background_color",
                            key="appearance_background_color",
                            label="Background color",
                            type="color",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_background_color,
                            description="Base color used for a solid background.",
                        ),
                        SchemaField(
                            id="appearance_gradient_start_color",
                            key="appearance_gradient_start_color",
                            label="Gradient start color",
                            type="color",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_gradient_start_color,
                            description="First color in the background gradient.",
                            visible_when=background_gradient_visible,
                        ),
                        SchemaField(
                            id="appearance_gradient_end_color",
                            key="appearance_gradient_end_color",
                            label="Gradient end color",
                            type="color",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_gradient_end_color,
                            description="Second color in the background gradient.",
                            visible_when=background_gradient_visible,
                        ),
                        SchemaField(
                            id="appearance_gradient_direction",
                            key="appearance_gradient_direction",
                            label="Gradient direction",
                            type="combo",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_gradient_direction,
                            description="Direction of the gradient blend.",
                            options=[
                                {"value": "horizontal", "label": "Horizontal"},
                                {"value": "vertical", "label": "Vertical"},
                                {"value": "diagonal_down", "label": "Diagonal down"},
                                {"value": "diagonal_up", "label": "Diagonal up"},
                            ],
                            visible_when=background_gradient_visible,
                        ),
                        SchemaField(
                            id="appearance_background_opacity",
                            key="appearance_background_opacity",
                            label="Background opacity",
                            type="spin",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_background_opacity,
                            min=0,
                            max=100,
                            description="Overall opacity of the topbar panel background.",
                        ),
                        SchemaField(
                            id="appearance_background_blur",
                            key="appearance_background_blur",
                            label="Background blur",
                            type="spin",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_background_blur,
                            min=0,
                            max=40,
                            description="Softens the background image when image mode is active.",
                        ),
                        SchemaField(
                            id="appearance_background_image_path",
                            key="appearance_background_image_path",
                            label="Background image",
                            type="path_file",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_background_image_path,
                            description="Choose an image to draw behind the topbar content.",
                            browse_provider_id="topbar_background_image",
                            browse_button_text="Browse",
                            visible_when=background_image_visible,
                        ),
                        SchemaField(
                            id="appearance_image_fit_mode",
                            key="appearance_image_fit_mode",
                            label="Image fit mode",
                            type="combo",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_image_fit_mode,
                            description="How the background image should fit inside the panel.",
                            options=[
                                {"value": "fill", "label": "Fill"},
                                {"value": "contain", "label": "Contain"},
                                {"value": "cover", "label": "Cover"},
                                {"value": "stretch", "label": "Stretch"},
                                {"value": "tile", "label": "Tile"},
                                {"value": "center", "label": "Center"},
                            ],
                            visible_when=background_image_visible,
                        ),
                        SchemaField(
                            id="appearance_image_alignment",
                            key="appearance_image_alignment",
                            label="Image alignment",
                            type="combo",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_image_alignment,
                            description="Where the image is anchored inside the panel.",
                            options=[
                                {"value": "center", "label": "Center"},
                                {"value": "top", "label": "Top"},
                                {"value": "bottom", "label": "Bottom"},
                                {"value": "left", "label": "Left"},
                                {"value": "right", "label": "Right"},
                            ],
                            visible_when=background_image_visible,
                        ),
                        SchemaField(
                            id="appearance_image_opacity",
                            key="appearance_image_opacity",
                            label="Image opacity",
                            type="spin",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_image_opacity,
                            min=0,
                            max=100,
                            description="Opacity of the background image itself.",
                            visible_when=background_image_visible,
                        ),
                        SchemaField(
                            id="appearance_overlay_tint",
                            key="appearance_overlay_tint",
                            label="Overlay tint",
                            type="color",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_overlay_tint,
                            description="Color drawn over the background to shift its tone.",
                        ),
                        SchemaField(
                            id="appearance_overlay_tint_opacity",
                            key="appearance_overlay_tint_opacity",
                            label="Overlay tint opacity",
                            type="spin",
                            scope=TOPBAR_SCOPE,
                            default=defaults.appearance_overlay_tint_opacity,
                            min=0,
                            max=100,
                            description="Strength of the overlay tint.",
                        ),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="topbar-appearance-shape",
            category="Appearance",
            title="Shape",
            scope=TOPBAR_SCOPE,
            description="Panel dimensions, margins, and corner shaping.",
            scope_order=0,
            category_order=1,
            order=1,
            sections=[
                SchemaSection(
                    title="Shape",
                    fields=[
                        SchemaField("appearance_height", "appearance_height", "Height", "spin", TOPBAR_SCOPE, "Panel height.", defaults.appearance_height, min=24, max=96),
                        SchemaField("appearance_corner_radius", "appearance_corner_radius", "Corner radius", "spin", TOPBAR_SCOPE, "Corner rounding for the panel.", defaults.appearance_corner_radius, min=0, max=48),
                        SchemaField("appearance_top_margin", "appearance_top_margin", "Top margin", "spin", TOPBAR_SCOPE, "Gap between the top edge and the panel.", defaults.appearance_top_margin, min=0, max=48),
                        SchemaField("appearance_left_margin", "appearance_left_margin", "Left margin", "spin", TOPBAR_SCOPE, "Inset from the left screen edge.", defaults.appearance_left_margin, min=0, max=96),
                        SchemaField("appearance_right_margin", "appearance_right_margin", "Right margin", "spin", TOPBAR_SCOPE, "Inset from the right screen edge.", defaults.appearance_right_margin, min=0, max=96),
                        SchemaField("appearance_internal_padding", "appearance_internal_padding", "Internal padding", "spin", TOPBAR_SCOPE, "Horizontal padding inside the panel.", defaults.appearance_internal_padding, min=0, max=48),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="topbar-appearance-border",
            category="Appearance",
            title="Border",
            scope=TOPBAR_SCOPE,
            description="Panel border visibility and tone.",
            scope_order=0,
            category_order=1,
            order=2,
            sections=[
                SchemaSection(
                    title="Border",
                    fields=[
                        SchemaField("appearance_show_border", "appearance_show_border", "Show border", "checkbox", TOPBAR_SCOPE, "Draw a border around the panel.", defaults.appearance_show_border),
                        SchemaField("appearance_border_width", "appearance_border_width", "Border width", "spin", TOPBAR_SCOPE, "Thickness of the border line.", defaults.appearance_border_width, min=0, max=12, visible_when=border_visible),
                        SchemaField("appearance_border_color", "appearance_border_color", "Border color", "color", TOPBAR_SCOPE, "Color used for the border.", defaults.appearance_border_color, visible_when=border_visible),
                        SchemaField("appearance_border_opacity", "appearance_border_opacity", "Border opacity", "spin", TOPBAR_SCOPE, "Opacity of the border color.", defaults.appearance_border_opacity, min=0, max=100, visible_when=border_visible),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="topbar-appearance-shadow",
            category="Appearance",
            title="Shadow",
            scope=TOPBAR_SCOPE,
            description="Panel shadow depth and offset.",
            scope_order=0,
            category_order=1,
            order=3,
            sections=[
                SchemaSection(
                    title="Shadow",
                    fields=[
                        SchemaField("appearance_show_shadow", "appearance_show_shadow", "Show shadow", "checkbox", TOPBAR_SCOPE, "Draw a drop shadow behind the panel.", defaults.appearance_show_shadow),
                        SchemaField("appearance_shadow_blur", "appearance_shadow_blur", "Shadow blur", "spin", TOPBAR_SCOPE, "Blur radius for the shadow.", defaults.appearance_shadow_blur, min=0, max=64, visible_when=shadow_visible),
                        SchemaField("appearance_shadow_offset_x", "appearance_shadow_offset_x", "Shadow offset X", "spin", TOPBAR_SCOPE, "Horizontal offset of the shadow.", defaults.appearance_shadow_offset_x, min=-64, max=64, visible_when=shadow_visible),
                        SchemaField("appearance_shadow_offset_y", "appearance_shadow_offset_y", "Shadow offset Y", "spin", TOPBAR_SCOPE, "Vertical offset of the shadow.", defaults.appearance_shadow_offset_y, min=-64, max=64, visible_when=shadow_visible),
                        SchemaField("appearance_shadow_opacity", "appearance_shadow_opacity", "Shadow opacity", "spin", TOPBAR_SCOPE, "Opacity of the shadow.", defaults.appearance_shadow_opacity, min=0, max=100, visible_when=shadow_visible),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="topbar-appearance-content-layout",
            category="Appearance",
            title="Content Layout",
            scope=TOPBAR_SCOPE,
            description="Spacing between topbar sections and widgets.",
            scope_order=0,
            category_order=1,
            order=4,
            sections=[
                SchemaSection(
                    title="Content Layout",
                    fields=[
                        SchemaField("appearance_section_spacing", "appearance_section_spacing", "Section spacing", "spin", TOPBAR_SCOPE, "Spacing between left, center, and right sections.", defaults.appearance_section_spacing, min=0, max=96),
                        SchemaField("appearance_widget_spacing", "appearance_widget_spacing", "Widget spacing", "spin", TOPBAR_SCOPE, "Fallback spacing used inside content rows.", defaults.appearance_widget_spacing, min=0, max=48),
                        SchemaField("appearance_left_section_spacing", "appearance_left_section_spacing", "Left section spacing", "spin", TOPBAR_SCOPE, "Spacing between widgets in the left section.", defaults.appearance_left_section_spacing, min=0, max=48),
                        SchemaField("appearance_center_section_spacing", "appearance_center_section_spacing", "Center section spacing", "spin", TOPBAR_SCOPE, "Spacing between widgets in the center section.", defaults.appearance_center_section_spacing, min=0, max=48),
                        SchemaField("appearance_right_section_spacing", "appearance_right_section_spacing", "Right section spacing", "spin", TOPBAR_SCOPE, "Spacing between widgets in the right section.", defaults.appearance_right_section_spacing, min=0, max=48),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="topbar-appearance-buttons",
            category="Appearance",
            title="Buttons",
            scope=TOPBAR_SCOPE,
            description="Shared button size, padding, icon scale, and interaction styling.",
            scope_order=0,
            category_order=1,
            order=5,
            sections=[
                SchemaSection(
                    title="Buttons",
                    fields=[
                        SchemaField("appearance_button_size", "appearance_button_size", "Button size", "spin", TOPBAR_SCOPE, "Target button height for topbar controls.", defaults.appearance_button_size, min=20, max=72),
                        SchemaField("appearance_button_padding", "appearance_button_padding", "Button padding", "spin", TOPBAR_SCOPE, "Horizontal padding inside topbar buttons.", defaults.appearance_button_padding, min=0, max=24),
                        SchemaField("appearance_button_corner_radius", "appearance_button_corner_radius", "Button corner radius", "spin", TOPBAR_SCOPE, "Corner rounding for buttons.", defaults.appearance_button_corner_radius, min=0, max=24),
                        SchemaField(
                            "appearance_button_background_style",
                            "appearance_button_background_style",
                            "Button background style",
                            "combo",
                            TOPBAR_SCOPE,
                            "Base background style for topbar buttons.",
                            defaults.appearance_button_background_style,
                            options=[
                                {"value": "transparent", "label": "Transparent"},
                                {"value": "subtle", "label": "Subtle"},
                                {"value": "filled", "label": "Filled"},
                            ],
                        ),
                        SchemaField(
                            "appearance_button_border_style",
                            "appearance_button_border_style",
                            "Button border style",
                            "combo",
                            TOPBAR_SCOPE,
                            "Border treatment for topbar buttons.",
                            defaults.appearance_button_border_style,
                            options=[
                                {"value": "none", "label": "None"},
                                {"value": "soft", "label": "Soft"},
                                {"value": "outline", "label": "Outline"},
                            ],
                        ),
                        SchemaField(
                            "appearance_button_hover_style",
                            "appearance_button_hover_style",
                            "Hover style",
                            "combo",
                            TOPBAR_SCOPE,
                            "How buttons react on hover.",
                            defaults.appearance_button_hover_style,
                            options=[
                                {"value": "none", "label": "None"},
                                {"value": "highlight", "label": "Highlight"},
                                {"value": "filled", "label": "Filled"},
                                {"value": "inset", "label": "Inset"},
                            ],
                        ),
                        SchemaField(
                            "appearance_button_pressed_style",
                            "appearance_button_pressed_style",
                            "Pressed style",
                            "combo",
                            TOPBAR_SCOPE,
                            "How buttons react when pressed.",
                            defaults.appearance_button_pressed_style,
                            options=[
                                {"value": "none", "label": "None"},
                                {"value": "highlight", "label": "Highlight"},
                                {"value": "filled", "label": "Filled"},
                                {"value": "inset", "label": "Inset"},
                            ],
                        ),
                        SchemaField("appearance_button_icon_size", "appearance_button_icon_size", "Icon size", "spin", TOPBAR_SCOPE, "Icon size used for topbar buttons.", defaults.appearance_button_icon_size, min=12, max=48),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="topbar-appearance-labels",
            category="Appearance",
            title="Labels",
            scope=TOPBAR_SCOPE,
            description="Font and text styling for topbar labels.",
            scope_order=0,
            category_order=1,
            order=6,
            sections=[
                SchemaSection(
                    title="Labels",
                    fields=[
                        SchemaField("appearance_label_font_family", "appearance_label_font_family", "Font family", "font_family", TOPBAR_SCOPE, "Font family used for general topbar labels.", defaults.appearance_label_font_family),
                        SchemaField("appearance_label_font_size", "appearance_label_font_size", "Font size", "spin", TOPBAR_SCOPE, "Font size for general labels.", defaults.appearance_label_font_size, min=8, max=32),
                        SchemaField("appearance_label_font_weight", "appearance_label_font_weight", "Font weight", "spin", TOPBAR_SCOPE, "Font weight for general labels.", defaults.appearance_label_font_weight, min=100, max=900),
                        SchemaField("appearance_label_text_color", "appearance_label_text_color", "Text color", "color", TOPBAR_SCOPE, "Text color for general labels.", defaults.appearance_label_text_color),
                        SchemaField("appearance_label_text_shadow", "appearance_label_text_shadow", "Text shadow", "checkbox", TOPBAR_SCOPE, "Enable a subtle text shadow on labels.", defaults.appearance_label_text_shadow),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="topbar-appearance-clock",
            category="Appearance",
            title="Clock",
            scope=TOPBAR_SCOPE,
            description="Clock visibility and formatting.",
            scope_order=0,
            category_order=1,
            order=7,
            sections=[
                SchemaSection(
                    title="Clock",
                    fields=[
                        SchemaField("appearance_show_clock", "appearance_show_clock", "Show clock", "checkbox", TOPBAR_SCOPE, "Show the clock in the topbar.", defaults.appearance_show_clock),
                        SchemaField("appearance_time_format", "appearance_time_format", "Time format", "lineedit", TOPBAR_SCOPE, "Qt date/time format string for the clock.", defaults.appearance_time_format, visible_when=clock_visible),
                        SchemaField("appearance_date_format", "appearance_date_format", "Date format", "lineedit", TOPBAR_SCOPE, "Qt date format string for the clock tooltip.", defaults.appearance_date_format, visible_when=clock_visible),
                        SchemaField("appearance_clock_font_family", "appearance_clock_font_family", "Clock font", "font_family", TOPBAR_SCOPE, "Font family used for the clock.", defaults.appearance_clock_font_family, visible_when=clock_visible),
                        SchemaField("appearance_clock_size", "appearance_clock_size", "Clock size", "spin", TOPBAR_SCOPE, "Font size used for the clock.", defaults.appearance_clock_size, min=8, max=32, visible_when=clock_visible),
                        SchemaField("appearance_clock_color", "appearance_clock_color", "Clock color", "color", TOPBAR_SCOPE, "Text color used for the clock.", defaults.appearance_clock_color, visible_when=clock_visible),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="topbar-appearance-tray",
            category="Appearance",
            title="Tray / Status Area",
            scope=TOPBAR_SCOPE,
            description="Sizing and styling for tray buttons.",
            scope_order=0,
            category_order=1,
            order=8,
            sections=[
                SchemaSection(
                    title="Tray / Status Area",
                    fields=[
                        SchemaField("appearance_tray_icon_size", "appearance_tray_icon_size", "Tray icon size", "spin", TOPBAR_SCOPE, "Icon size used for tray items.", defaults.appearance_tray_icon_size, min=12, max=48),
                        SchemaField("appearance_tray_icon_spacing", "appearance_tray_icon_spacing", "Tray icon spacing", "spin", TOPBAR_SCOPE, "Spacing between tray buttons.", defaults.appearance_tray_icon_spacing, min=0, max=24),
                        SchemaField(
                            "appearance_tray_button_style",
                            "appearance_tray_button_style",
                            "Tray button style",
                            "combo",
                            TOPBAR_SCOPE,
                            "How tray buttons are styled relative to the rest of the topbar.",
                            defaults.appearance_tray_button_style,
                            options=[
                                {"value": "match_buttons", "label": "Match buttons"},
                                {"value": "transparent", "label": "Transparent"},
                                {"value": "filled", "label": "Filled"},
                            ],
                        ),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="menu-appearance-background",
            category="Appearance",
            title="Background",
            scope=MENU_SCOPE,
            description="Background fill, image, tint, and opacity settings for the system menu panel.",
            scope_order=1,
            category_order=0,
            order=0,
            sections=[
                SchemaSection(
                    title="Background",
                    fields=[
                        SchemaField("menu_appearance_background_type", "menu_appearance_background_type", "Background type", "combo", MENU_SCOPE, "Choose the main background treatment for the menu panel.", defaults.menu_appearance_background_type, options=[{"value": "solid", "label": "Solid color"}, {"value": "gradient", "label": "Gradient"}, {"value": "image", "label": "Image"}]),
                        SchemaField("menu_appearance_background_color", "menu_appearance_background_color", "Background color", "color", MENU_SCOPE, "Base color used for a solid menu background.", defaults.menu_appearance_background_color),
                        SchemaField("menu_appearance_gradient_start_color", "menu_appearance_gradient_start_color", "Gradient start color", "color", MENU_SCOPE, "First color in the menu gradient.", defaults.menu_appearance_gradient_start_color, visible_when=menu_background_gradient_visible),
                        SchemaField("menu_appearance_gradient_end_color", "menu_appearance_gradient_end_color", "Gradient end color", "color", MENU_SCOPE, "Second color in the menu gradient.", defaults.menu_appearance_gradient_end_color, visible_when=menu_background_gradient_visible),
                        SchemaField("menu_appearance_gradient_direction", "menu_appearance_gradient_direction", "Gradient direction", "combo", MENU_SCOPE, "Direction of the menu gradient blend.", defaults.menu_appearance_gradient_direction, options=[{"value": "horizontal", "label": "Horizontal"}, {"value": "vertical", "label": "Vertical"}, {"value": "diagonal_down", "label": "Diagonal down"}, {"value": "diagonal_up", "label": "Diagonal up"}], visible_when=menu_background_gradient_visible),
                        SchemaField("menu_appearance_background_opacity", "menu_appearance_background_opacity", "Background opacity", "spin", MENU_SCOPE, "Overall opacity of the menu panel background.", defaults.menu_appearance_background_opacity, min=0, max=100),
                        SchemaField("menu_appearance_background_blur", "menu_appearance_background_blur", "Background blur", "spin", MENU_SCOPE, "Softens the menu background image when image mode is active.", defaults.menu_appearance_background_blur, min=0, max=40),
                        SchemaField("menu_appearance_background_image_path", "menu_appearance_background_image_path", "Background image", "path_file", MENU_SCOPE, "Choose an image to draw behind the menu content.", defaults.menu_appearance_background_image_path, browse_provider_id="menu_background_image", browse_button_text="Browse", visible_when=menu_background_image_visible),
                        SchemaField("menu_appearance_image_fit_mode", "menu_appearance_image_fit_mode", "Image fit mode", "combo", MENU_SCOPE, "How the menu background image should fit inside the panel.", defaults.menu_appearance_image_fit_mode, options=[{"value": "fill", "label": "Fill"}, {"value": "contain", "label": "Contain"}, {"value": "cover", "label": "Cover"}, {"value": "stretch", "label": "Stretch"}, {"value": "tile", "label": "Tile"}, {"value": "center", "label": "Center"}], visible_when=menu_background_image_visible),
                        SchemaField("menu_appearance_image_alignment", "menu_appearance_image_alignment", "Image alignment", "combo", MENU_SCOPE, "Where the background image is anchored inside the menu panel.", defaults.menu_appearance_image_alignment, options=[{"value": "center", "label": "Center"}, {"value": "top", "label": "Top"}, {"value": "bottom", "label": "Bottom"}, {"value": "left", "label": "Left"}, {"value": "right", "label": "Right"}], visible_when=menu_background_image_visible),
                        SchemaField("menu_appearance_image_opacity", "menu_appearance_image_opacity", "Image opacity", "spin", MENU_SCOPE, "Opacity of the menu background image itself.", defaults.menu_appearance_image_opacity, min=0, max=100, visible_when=menu_background_image_visible),
                        SchemaField("menu_appearance_overlay_tint", "menu_appearance_overlay_tint", "Overlay tint", "color", MENU_SCOPE, "Color drawn over the menu background to shift its tone.", defaults.menu_appearance_overlay_tint),
                        SchemaField("menu_appearance_overlay_tint_opacity", "menu_appearance_overlay_tint_opacity", "Overlay tint opacity", "spin", MENU_SCOPE, "Strength of the menu overlay tint.", defaults.menu_appearance_overlay_tint_opacity, min=0, max=100),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="menu-appearance-shape",
            category="Appearance",
            title="Shape",
            scope=MENU_SCOPE,
            description="Panel sizing, margins, and padding for the system menu.",
            scope_order=1,
            category_order=0,
            order=1,
            sections=[
                SchemaSection(
                    title="Shape",
                    fields=[
                        SchemaField("menu_appearance_corner_radius", "menu_appearance_corner_radius", "Corner radius", "spin", MENU_SCOPE, "Corner rounding for the menu panel.", defaults.menu_appearance_corner_radius, min=0, max=48),
                        SchemaField("menu_appearance_panel_width", "menu_appearance_panel_width", "Panel width", "spin", MENU_SCOPE, "Width of the system menu panel.", defaults.menu_appearance_panel_width, min=280, max=960),
                        SchemaField("menu_appearance_panel_max_height", "menu_appearance_panel_max_height", "Panel max height", "spin", MENU_SCOPE, "Maximum height before the whole menu starts scrolling.", defaults.menu_appearance_panel_max_height, min=240, max=1600),
                        SchemaField("menu_appearance_outer_margin", "menu_appearance_outer_margin", "Outer margin", "spin", MENU_SCOPE, "Gap between the menu panel and the screen edge.", defaults.menu_appearance_outer_margin, min=0, max=64),
                        SchemaField("menu_appearance_internal_padding", "menu_appearance_internal_padding", "Internal padding", "spin", MENU_SCOPE, "Padding inside the menu panel.", defaults.menu_appearance_internal_padding, min=0, max=48),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="menu-appearance-border",
            category="Appearance",
            title="Border",
            scope=MENU_SCOPE,
            description="Menu border visibility and tone.",
            scope_order=1,
            category_order=0,
            order=2,
            sections=[
                SchemaSection(
                    title="Border",
                    fields=[
                        SchemaField("menu_appearance_show_border", "menu_appearance_show_border", "Show border", "checkbox", MENU_SCOPE, "Draw a border around the menu panel.", defaults.menu_appearance_show_border),
                        SchemaField("menu_appearance_border_width", "menu_appearance_border_width", "Border width", "spin", MENU_SCOPE, "Thickness of the menu border line.", defaults.menu_appearance_border_width, min=0, max=12, visible_when=menu_border_visible),
                        SchemaField("menu_appearance_border_color", "menu_appearance_border_color", "Border color", "color", MENU_SCOPE, "Color used for the menu border.", defaults.menu_appearance_border_color, visible_when=menu_border_visible),
                        SchemaField("menu_appearance_border_opacity", "menu_appearance_border_opacity", "Border opacity", "spin", MENU_SCOPE, "Opacity of the menu border color.", defaults.menu_appearance_border_opacity, min=0, max=100, visible_when=menu_border_visible),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="menu-appearance-shadow",
            category="Appearance",
            title="Shadow",
            scope=MENU_SCOPE,
            description="Menu shadow depth and offset.",
            scope_order=1,
            category_order=0,
            order=3,
            sections=[
                SchemaSection(
                    title="Shadow",
                    fields=[
                        SchemaField("menu_appearance_show_shadow", "menu_appearance_show_shadow", "Show shadow", "checkbox", MENU_SCOPE, "Draw a drop shadow behind the menu panel.", defaults.menu_appearance_show_shadow),
                        SchemaField("menu_appearance_shadow_blur", "menu_appearance_shadow_blur", "Shadow blur", "spin", MENU_SCOPE, "Blur radius for the menu shadow.", defaults.menu_appearance_shadow_blur, min=0, max=64, visible_when=menu_shadow_visible),
                        SchemaField("menu_appearance_shadow_offset_x", "menu_appearance_shadow_offset_x", "Shadow offset X", "spin", MENU_SCOPE, "Horizontal offset of the menu shadow.", defaults.menu_appearance_shadow_offset_x, min=-64, max=64, visible_when=menu_shadow_visible),
                        SchemaField("menu_appearance_shadow_offset_y", "menu_appearance_shadow_offset_y", "Shadow offset Y", "spin", MENU_SCOPE, "Vertical offset of the menu shadow.", defaults.menu_appearance_shadow_offset_y, min=-64, max=64, visible_when=menu_shadow_visible),
                        SchemaField("menu_appearance_shadow_opacity", "menu_appearance_shadow_opacity", "Shadow opacity", "spin", MENU_SCOPE, "Opacity of the menu shadow.", defaults.menu_appearance_shadow_opacity, min=0, max=100, visible_when=menu_shadow_visible),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="menu-appearance-sections",
            category="Appearance",
            title="Sections",
            scope=MENU_SCOPE,
            description="Spacing and title styling for menu sections.",
            scope_order=1,
            category_order=0,
            order=4,
            sections=[
                SchemaSection(
                    title="Sections",
                    fields=[
                        SchemaField("menu_appearance_section_spacing", "menu_appearance_section_spacing", "Section spacing", "spin", MENU_SCOPE, "Vertical spacing between major menu sections.", defaults.menu_appearance_section_spacing, min=0, max=48),
                        SchemaField("menu_appearance_section_header_font_family", "menu_appearance_section_header_font_family", "Section header font", "font_family", MENU_SCOPE, "Font family used for menu section headers.", defaults.menu_appearance_section_header_font_family),
                        SchemaField("menu_appearance_section_header_font_size", "menu_appearance_section_header_font_size", "Section header size", "spin", MENU_SCOPE, "Font size used for menu section headers.", defaults.menu_appearance_section_header_font_size, min=8, max=32),
                        SchemaField("menu_appearance_section_header_color", "menu_appearance_section_header_color", "Section header color", "color", MENU_SCOPE, "Text color used for menu section headers.", defaults.menu_appearance_section_header_color),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="menu-appearance-items",
            category="Appearance",
            title="Menu Items",
            scope=MENU_SCOPE,
            description="Sizing, spacing, and text styling for menu buttons and action rows.",
            scope_order=1,
            category_order=0,
            order=5,
            sections=[
                SchemaSection(
                    title="Menu Items",
                    fields=[
                        SchemaField("menu_appearance_item_height", "menu_appearance_item_height", "Item height", "spin", MENU_SCOPE, "Target height for interactive rows and buttons.", defaults.menu_appearance_item_height, min=22, max=72),
                        SchemaField("menu_appearance_item_padding", "menu_appearance_item_padding", "Item padding", "spin", MENU_SCOPE, "Horizontal padding inside menu buttons.", defaults.menu_appearance_item_padding, min=0, max=24),
                        SchemaField("menu_appearance_item_spacing", "menu_appearance_item_spacing", "Item spacing", "spin", MENU_SCOPE, "Spacing between controls inside menu rows.", defaults.menu_appearance_item_spacing, min=0, max=24),
                        SchemaField("menu_appearance_item_corner_radius", "menu_appearance_item_corner_radius", "Item corner radius", "spin", MENU_SCOPE, "Corner rounding for interactive menu items.", defaults.menu_appearance_item_corner_radius, min=0, max=24),
                        SchemaField("menu_appearance_item_background", "menu_appearance_item_background", "Item background", "color", MENU_SCOPE, "Base background color used for menu items.", defaults.menu_appearance_item_background),
                        SchemaField("menu_appearance_item_hover_background", "menu_appearance_item_hover_background", "Hover background", "color", MENU_SCOPE, "Background color used while hovering menu items.", defaults.menu_appearance_item_hover_background),
                        SchemaField("menu_appearance_item_active_background", "menu_appearance_item_active_background", "Active background", "color", MENU_SCOPE, "Background color used while pressing menu items.", defaults.menu_appearance_item_active_background),
                        SchemaField("menu_appearance_item_text_color", "menu_appearance_item_text_color", "Text color", "color", MENU_SCOPE, "Primary text color used for menu items.", defaults.menu_appearance_item_text_color),
                        SchemaField("menu_appearance_item_secondary_text_color", "menu_appearance_item_secondary_text_color", "Secondary text color", "color", MENU_SCOPE, "Secondary text color used for muted labels and metadata.", defaults.menu_appearance_item_secondary_text_color),
                        SchemaField("menu_appearance_item_icon_size", "menu_appearance_item_icon_size", "Icon size", "spin", MENU_SCOPE, "Icon size used for menu buttons with icons.", defaults.menu_appearance_item_icon_size, min=12, max=48),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="menu-appearance-scrolling",
            category="Appearance",
            title="Scrolling",
            scope=MENU_SCOPE,
            description="Scrollbar behavior for rare overflow cases.",
            scope_order=1,
            category_order=0,
            order=6,
            sections=[
                SchemaSection(
                    title="Scrolling",
                    fields=[
                        SchemaField("menu_appearance_scrollbar_visibility", "menu_appearance_scrollbar_visibility", "Scrollbar visibility", "combo", MENU_SCOPE, "When the menu scrollbar should appear.", defaults.menu_appearance_scrollbar_visibility, options=[{"value": "auto", "label": "Automatic"}, {"value": "always", "label": "Always show"}, {"value": "hidden", "label": "Hidden"}]),
                        SchemaField("menu_appearance_scrollbar_width", "menu_appearance_scrollbar_width", "Scrollbar width", "spin", MENU_SCOPE, "Width of the menu scrollbar thumb.", defaults.menu_appearance_scrollbar_width, min=4, max=24),
                        SchemaField("menu_appearance_scrollbar_corner_radius", "menu_appearance_scrollbar_corner_radius", "Scrollbar corner radius", "spin", MENU_SCOPE, "Corner rounding for the menu scrollbar thumb.", defaults.menu_appearance_scrollbar_corner_radius, min=0, max=24),
                        SchemaField("menu_appearance_scrollbar_color", "menu_appearance_scrollbar_color", "Scrollbar color", "color", MENU_SCOPE, "Color used for the menu scrollbar thumb.", defaults.menu_appearance_scrollbar_color),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="media-controls-visibility",
            category="Controls",
            title="Visibility",
            scope=MEDIA_SCOPE,
            description="Choose which media-related sections and controls are shown in the menu.",
            scope_order=2,
            category_order=0,
            order=0,
            sections=[
                SchemaSection(
                    title="Sections",
                    fields=[
                        SchemaField("media_controls_show_media_players", "media_controls_show_media_players", "Show media players", "checkbox", MEDIA_SCOPE, "Show the media player card section.", defaults.media_controls_show_media_players),
                        SchemaField("media_controls_show_application_volumes", "media_controls_show_application_volumes", "Show application volumes", "checkbox", MEDIA_SCOPE, "Show the per-application volume card section.", defaults.media_controls_show_application_volumes),
                        SchemaField("media_controls_prefer_active_player_first", "media_controls_prefer_active_player_first", "Prefer active player first", "checkbox", MEDIA_SCOPE, "Sort the currently playing player ahead of paused or idle players.", defaults.media_controls_prefer_active_player_first),
                    ],
                ),
                SchemaSection(
                    title="Playback Controls",
                    fields=[
                        SchemaField("media_controls_interaction_mode", "media_controls_interaction_mode", "Media interaction mode", "combo", MEDIA_SCOPE, "Choose whether media cards expose full transport controls or only volume control.", defaults.media_controls_interaction_mode, options=[{"value": "full_media_controls", "label": "Full media controls"}, {"value": "application_volume_only", "label": "Application volume only"}]),
                        SchemaField("media_controls_show_player_name", "media_controls_show_player_name", "Show player name", "checkbox", MEDIA_SCOPE, "Show the player name at the top of each media card.", defaults.media_controls_show_player_name),
                        SchemaField("media_controls_show_play_pause", "media_controls_show_play_pause", "Show play/pause", "checkbox", MEDIA_SCOPE, "Show the play and pause control when available.", defaults.media_controls_show_play_pause, visible_when=full_media_controls_visible),
                        SchemaField("media_controls_show_stop", "media_controls_show_stop", "Show stop", "checkbox", MEDIA_SCOPE, "Show the stop control when available.", defaults.media_controls_show_stop, visible_when=full_media_controls_visible),
                        SchemaField("media_controls_show_previous_next", "media_controls_show_previous_next", "Show previous / next", "checkbox", MEDIA_SCOPE, "Show previous and next track controls when available.", defaults.media_controls_show_previous_next, visible_when=full_media_controls_visible),
                        SchemaField("media_controls_show_seek_controls", "media_controls_show_seek_controls", "Show seek controls", "checkbox", MEDIA_SCOPE, "Show seek-back and seek-forward buttons when seeking is available.", defaults.media_controls_show_seek_controls, visible_when=full_media_controls_visible),
                        SchemaField("media_controls_show_position_scrubbing", "media_controls_show_position_scrubbing", "Show position scrubbing", "checkbox", MEDIA_SCOPE, "Show the playback position slider when seeking is available.", defaults.media_controls_show_position_scrubbing, visible_when=full_media_controls_visible),
                        SchemaField("media_controls_show_shuffle", "media_controls_show_shuffle", "Show shuffle", "checkbox", MEDIA_SCOPE, "Show the shuffle toggle when supported.", defaults.media_controls_show_shuffle, visible_when=full_media_controls_visible),
                        SchemaField("media_controls_show_loop", "media_controls_show_loop", "Show loop", "checkbox", MEDIA_SCOPE, "Show the loop mode control when supported.", defaults.media_controls_show_loop, visible_when=full_media_controls_visible),
                        SchemaField("media_controls_show_volume_slider", "media_controls_show_volume_slider", "Show volume slider", "checkbox", MEDIA_SCOPE, "Show the player volume slider when available.", defaults.media_controls_show_volume_slider),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="media-cards-layout",
            category="Cards",
            title="Layout",
            scope=MEDIA_SCOPE,
            description="Spacing and control sizing for media and application cards.",
            scope_order=2,
            category_order=1,
            order=0,
            sections=[
                SchemaSection(
                    title="Layout",
                    fields=[
                        SchemaField("media_cards_spacing", "media_cards_spacing", "Card spacing", "spin", MEDIA_SCOPE, "Spacing between adjacent media cards.", defaults.media_cards_spacing, min=0, max=32),
                        SchemaField("media_cards_internal_padding", "media_cards_internal_padding", "Internal padding", "spin", MEDIA_SCOPE, "Padding inside each media card.", defaults.media_cards_internal_padding, min=0, max=32),
                        SchemaField("media_cards_button_size", "media_cards_button_size", "Button size", "spin", MEDIA_SCOPE, "Target size for media control buttons.", defaults.media_cards_button_size, min=18, max=56),
                        SchemaField("media_cards_seek_bar_thickness", "media_cards_seek_bar_thickness", "Seek bar thickness", "spin", MEDIA_SCOPE, "Thickness of seek and volume slider grooves.", defaults.media_cards_seek_bar_thickness, min=2, max=18),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="media-cards-background",
            category="Cards",
            title="Background",
            scope=MEDIA_SCOPE,
            description="Background fill, image, tint, and opacity settings for media and application cards.",
            scope_order=2,
            category_order=1,
            order=1,
            sections=[
                SchemaSection(
                    title="Background",
                    fields=[
                        SchemaField("media_cards_background_type", "media_cards_background_type", "Background type", "combo", MEDIA_SCOPE, "Choose the main background treatment for media cards.", defaults.media_cards_background_type, options=[{"value": "solid", "label": "Solid color"}, {"value": "gradient", "label": "Gradient"}, {"value": "image", "label": "Image"}]),
                        SchemaField("media_cards_background_color", "media_cards_background_color", "Background color", "color", MEDIA_SCOPE, "Base color used for a solid card background.", defaults.media_cards_background_color),
                        SchemaField("media_cards_gradient_start_color", "media_cards_gradient_start_color", "Gradient start color", "color", MEDIA_SCOPE, "First color in the card gradient.", defaults.media_cards_gradient_start_color, visible_when=media_cards_background_gradient_visible),
                        SchemaField("media_cards_gradient_end_color", "media_cards_gradient_end_color", "Gradient end color", "color", MEDIA_SCOPE, "Second color in the card gradient.", defaults.media_cards_gradient_end_color, visible_when=media_cards_background_gradient_visible),
                        SchemaField("media_cards_gradient_direction", "media_cards_gradient_direction", "Gradient direction", "combo", MEDIA_SCOPE, "Direction of the card gradient blend.", defaults.media_cards_gradient_direction, options=[{"value": "horizontal", "label": "Horizontal"}, {"value": "vertical", "label": "Vertical"}, {"value": "diagonal_down", "label": "Diagonal down"}, {"value": "diagonal_up", "label": "Diagonal up"}], visible_when=media_cards_background_gradient_visible),
                        SchemaField("media_cards_background_opacity", "media_cards_background_opacity", "Background opacity", "spin", MEDIA_SCOPE, "Overall opacity of the card background.", defaults.media_cards_background_opacity, min=0, max=100),
                        SchemaField("media_cards_background_blur", "media_cards_background_blur", "Blur", "spin", MEDIA_SCOPE, "Softens the card background image when image mode is active.", defaults.media_cards_background_blur, min=0, max=40),
                        SchemaField("media_cards_background_image_path", "media_cards_background_image_path", "Background image", "path_file", MEDIA_SCOPE, "Choose an image to draw behind media cards.", defaults.media_cards_background_image_path, browse_provider_id="media_cards_background_image", browse_button_text="Browse", visible_when=media_cards_background_image_visible),
                        SchemaField("media_cards_image_fit_mode", "media_cards_image_fit_mode", "Image fit mode", "combo", MEDIA_SCOPE, "How the card background image should fit inside the card.", defaults.media_cards_image_fit_mode, options=[{"value": "fill", "label": "Fill"}, {"value": "contain", "label": "Contain"}, {"value": "cover", "label": "Cover"}, {"value": "stretch", "label": "Stretch"}, {"value": "tile", "label": "Tile"}, {"value": "center", "label": "Center"}], visible_when=media_cards_background_image_visible),
                        SchemaField("media_cards_image_alignment", "media_cards_image_alignment", "Image alignment", "combo", MEDIA_SCOPE, "Where the background image is anchored inside each card.", defaults.media_cards_image_alignment, options=[{"value": "center", "label": "Center"}, {"value": "top", "label": "Top"}, {"value": "bottom", "label": "Bottom"}, {"value": "left", "label": "Left"}, {"value": "right", "label": "Right"}], visible_when=media_cards_background_image_visible),
                        SchemaField("media_cards_image_opacity", "media_cards_image_opacity", "Image opacity", "spin", MEDIA_SCOPE, "Opacity of the card background image itself.", defaults.media_cards_image_opacity, min=0, max=100, visible_when=media_cards_background_image_visible),
                        SchemaField("media_cards_overlay_tint", "media_cards_overlay_tint", "Overlay tint", "color", MEDIA_SCOPE, "Color drawn over the card background to shift its tone.", defaults.media_cards_overlay_tint),
                        SchemaField("media_cards_overlay_tint_opacity", "media_cards_overlay_tint_opacity", "Overlay tint opacity", "spin", MEDIA_SCOPE, "Strength of the card overlay tint.", defaults.media_cards_overlay_tint_opacity, min=0, max=100),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="media-cards-shape",
            category="Cards",
            title="Shape",
            scope=MEDIA_SCOPE,
            description="Corner shaping for media and application cards.",
            scope_order=2,
            category_order=1,
            order=2,
            sections=[
                SchemaSection(
                    title="Shape",
                    fields=[
                        SchemaField("media_cards_corner_radius", "media_cards_corner_radius", "Corner radius", "spin", MEDIA_SCOPE, "Corner rounding for media and application cards.", defaults.media_cards_corner_radius, min=0, max=48),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="media-cards-border",
            category="Cards",
            title="Border",
            scope=MEDIA_SCOPE,
            description="Card border visibility and tone.",
            scope_order=2,
            category_order=1,
            order=3,
            sections=[
                SchemaSection(
                    title="Border",
                    fields=[
                        SchemaField("media_cards_show_border", "media_cards_show_border", "Show border", "checkbox", MEDIA_SCOPE, "Draw a border around media and application cards.", defaults.media_cards_show_border),
                        SchemaField("media_cards_border_width", "media_cards_border_width", "Border width", "spin", MEDIA_SCOPE, "Thickness of the card border line.", defaults.media_cards_border_width, min=0, max=12, visible_when=media_cards_border_visible),
                        SchemaField("media_cards_border_color", "media_cards_border_color", "Border color", "color", MEDIA_SCOPE, "Color used for the card border.", defaults.media_cards_border_color, visible_when=media_cards_border_visible),
                        SchemaField("media_cards_border_opacity", "media_cards_border_opacity", "Border opacity", "spin", MEDIA_SCOPE, "Opacity of the card border color.", defaults.media_cards_border_opacity, min=0, max=100, visible_when=media_cards_border_visible),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="media-cards-shadow",
            category="Cards",
            title="Shadow",
            scope=MEDIA_SCOPE,
            description="Card shadow depth and offset.",
            scope_order=2,
            category_order=1,
            order=4,
            sections=[
                SchemaSection(
                    title="Shadow",
                    fields=[
                        SchemaField("media_cards_show_shadow", "media_cards_show_shadow", "Show shadow", "checkbox", MEDIA_SCOPE, "Draw a drop shadow behind media and application cards.", defaults.media_cards_show_shadow),
                        SchemaField("media_cards_shadow_blur", "media_cards_shadow_blur", "Shadow blur", "spin", MEDIA_SCOPE, "Blur radius for the card shadow.", defaults.media_cards_shadow_blur, min=0, max=64, visible_when=media_cards_shadow_visible),
                        SchemaField("media_cards_shadow_offset_x", "media_cards_shadow_offset_x", "Shadow offset X", "spin", MEDIA_SCOPE, "Horizontal offset of the card shadow.", defaults.media_cards_shadow_offset_x, min=-64, max=64, visible_when=media_cards_shadow_visible),
                        SchemaField("media_cards_shadow_offset_y", "media_cards_shadow_offset_y", "Shadow offset Y", "spin", MEDIA_SCOPE, "Vertical offset of the card shadow.", defaults.media_cards_shadow_offset_y, min=-64, max=64, visible_when=media_cards_shadow_visible),
                        SchemaField("media_cards_shadow_opacity", "media_cards_shadow_opacity", "Shadow opacity", "spin", MEDIA_SCOPE, "Opacity of the card shadow.", defaults.media_cards_shadow_opacity, min=0, max=100, visible_when=media_cards_shadow_visible),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="media-cards-text",
            category="Cards",
            title="Text",
            scope=MEDIA_SCOPE,
            description="Font and text styling for media cards.",
            scope_order=2,
            category_order=1,
            order=5,
            sections=[
                SchemaSection(
                    title="Text",
                    fields=[
                        SchemaField("media_cards_title_font_family", "media_cards_title_font_family", "Title font", "font_family", MEDIA_SCOPE, "Font family used for media card titles.", defaults.media_cards_title_font_family),
                        SchemaField("media_cards_title_size", "media_cards_title_size", "Title size", "spin", MEDIA_SCOPE, "Font size used for media card titles.", defaults.media_cards_title_size, min=8, max=32),
                        SchemaField("media_cards_title_color", "media_cards_title_color", "Title color", "color", MEDIA_SCOPE, "Text color used for media card titles.", defaults.media_cards_title_color),
                        SchemaField("media_cards_subtitle_font_family", "media_cards_subtitle_font_family", "Subtitle font", "font_family", MEDIA_SCOPE, "Font family used for media card subtitles.", defaults.media_cards_subtitle_font_family),
                        SchemaField("media_cards_subtitle_size", "media_cards_subtitle_size", "Subtitle size", "spin", MEDIA_SCOPE, "Font size used for media card subtitles.", defaults.media_cards_subtitle_size, min=8, max=28),
                        SchemaField("media_cards_subtitle_color", "media_cards_subtitle_color", "Subtitle color", "color", MEDIA_SCOPE, "Text color used for media card subtitles.", defaults.media_cards_subtitle_color),
                        SchemaField("media_cards_show_secondary_text", "media_cards_show_secondary_text", "Show secondary text", "checkbox", MEDIA_SCOPE, "Show artist, album, and secondary details on cards.", defaults.media_cards_show_secondary_text),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="media-cards-controls",
            category="Cards",
            title="Controls Appearance",
            scope=MEDIA_SCOPE,
            description="Visual styling for media control buttons.",
            scope_order=2,
            category_order=1,
            order=6,
            sections=[
                SchemaSection(
                    title="Controls Appearance",
                    fields=[
                        SchemaField("media_cards_control_icon_size", "media_cards_control_icon_size", "Control icon size", "spin", MEDIA_SCOPE, "Icon size used for transport and card action buttons.", defaults.media_cards_control_icon_size, min=12, max=40),
                        SchemaField("media_cards_control_spacing", "media_cards_control_spacing", "Control spacing", "spin", MEDIA_SCOPE, "Spacing between control buttons.", defaults.media_cards_control_spacing, min=0, max=24),
                        SchemaField("media_cards_controls_button_corner_radius", "media_cards_controls_button_corner_radius", "Button corner radius", "spin", MEDIA_SCOPE, "Corner rounding for media control buttons.", defaults.media_cards_controls_button_corner_radius, min=0, max=24),
                        SchemaField("media_cards_controls_button_background", "media_cards_controls_button_background", "Button background", "color", MEDIA_SCOPE, "Base background color used for media control buttons.", defaults.media_cards_controls_button_background),
                        SchemaField("media_cards_controls_button_hover_background", "media_cards_controls_button_hover_background", "Hover style", "color", MEDIA_SCOPE, "Background color used while hovering media control buttons.", defaults.media_cards_controls_button_hover_background),
                        SchemaField("media_cards_controls_button_active_background", "media_cards_controls_button_active_background", "Active style", "color", MEDIA_SCOPE, "Background color used while pressing media control buttons.", defaults.media_cards_controls_button_active_background),
                        SchemaField("media_cards_controls_button_disabled_opacity", "media_cards_controls_button_disabled_opacity", "Disabled style", "spin", MEDIA_SCOPE, "Opacity used for disabled media control buttons.", defaults.media_cards_controls_button_disabled_opacity, min=0, max=100),
                    ],
                ),
            ],
        ),
        SchemaPage(
            id="media-cards-progress",
            category="Cards",
            title="Progress / Sliders",
            scope=MEDIA_SCOPE,
            description="Progress and slider color styling for media cards.",
            scope_order=2,
            category_order=1,
            order=7,
            sections=[
                SchemaSection(
                    title="Progress / Sliders",
                    fields=[
                        SchemaField("media_cards_progress_color", "media_cards_progress_color", "Progress color", "color", MEDIA_SCOPE, "Color used for active progress and slider fill.", defaults.media_cards_progress_color),
                        SchemaField("media_cards_progress_background_color", "media_cards_progress_background_color", "Progress background color", "color", MEDIA_SCOPE, "Color used for inactive slider grooves.", defaults.media_cards_progress_background_color),
                        SchemaField("media_cards_slider_thickness", "media_cards_slider_thickness", "Slider thickness", "spin", MEDIA_SCOPE, "Thickness of the media slider grooves.", defaults.media_cards_slider_thickness, min=2, max=18),
                    ],
                ),
            ],
        ),
    ]
    return SettingsSchema(pages=pages)


class TopBarSettingsDialog(SchemaSettingsDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        on_applied: Callable[[], None] | None = None,
        backend: TopBarSettingsBackend | None = None,
    ) -> None:
        super().__init__(
            backend=backend or TopBarSettingsBackend(),
            schema=_create_topbar_settings_schema(),
            on_applied=on_applied,
            use_native_chrome=True,
            parent=parent,
            object_name="TopBarSettingsDialog",
            window_title="Topbar Settings",
            tree_expanded_paths_key=TOPBAR_SETTINGS_TREE_EXPANDED_PATHS_KEY,
            tree_expanded_paths_scope=TOPBAR_SCOPE,
            scope_labeler=self._scope_label,
        )
        self.register_browse_provider("topbar_background_image", self._browse_background_image)
        self.register_browse_provider("menu_background_image", self._browse_background_image)
        self.register_browse_provider("media_cards_background_image", self._browse_background_image)
        self.setModal(False)
        self.resize(880, 640)

    def _scope_label(self, scope: str) -> str:
        normalized = str(scope or "").strip().lower()
        if normalized == MENU_SCOPE:
            return "Menu"
        if normalized == MEDIA_SCOPE:
            return "Media"
        return "Topbar"

    def _browse_background_image(self, field: SchemaField, _dialog: SchemaSettingsDialog, current_text: str) -> str | None:
        selected, _selected_filter, _starred = FileDialog.getOpenFileName(
            self,
            str(field.browse_caption or "Select Background Image"),
            current_text,
            _IMAGE_FILTER,
        )
        return str(selected) if selected else None
