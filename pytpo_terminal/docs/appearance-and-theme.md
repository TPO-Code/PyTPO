# Appearance and Theme Notes

## Theme Layer

`theme_name` applies shared themes from `themes/` through `TerminalThemeManager`.

## Terminal Palette Layer

Terminal-specific colors are then applied per session:

- Foreground/background
- Cursor color
- Traceback link color
- Selection foreground/background

## Background Image Layer

Background rendering order:

1. Theme/widget base color
2. Background image (if configured)
3. Optional alpha flattening
4. Tint overlay using tint color and strength

## ANSI Colors

`ansi_colors` overrides affect terminal ANSI rendering globally for all sessions.

If no overrides are set, default ANSI color mapping is used.

## Readability Recommendations

- Keep contrast high between `foreground_color` and `background_color`.
- Use moderate tint strengths (`20-45`) for image backgrounds.
- Avoid heavily saturated ANSI overrides for common colors (`red`, `green`) if you rely on compiler output parsing visually.
