# Settings Reference

This page documents all terminal settings persisted in `.terminal/settings.json`.

## Startup

- `startup_tabs` (int, 1-20)
  - Number of tabs opened when the app launches.
- `startup_cwd` (string path)
  - Default working directory for new tabs. If invalid or empty, launch/request cwd is used when available, otherwise home.
- `start_maximized` (bool)
  - Start the app maximized.
- `start_fullscreen` (bool)
  - Start the app full screen.

## Shell

- `default_shell_mode` (enum)
  - `auto`, `bash`, `zsh`, `sh`, `custom`
- `custom_shell_path` (string path)
  - Executable path used when `default_shell_mode` is `custom`.
- `shell_login` (bool)
  - Launch shell as login shell (`-l`) while still interactive (`-i`).

## Session Behavior

- `history_lines` (int, 200-300000)
  - Scrollback history line limit for new sessions.
- `show_toolbar` (bool)
  - Show/hide per-tab toolbar (Copy/Paste/Clear/Quick Commands).
- `confirm_close_running` (bool)
  - Prompt before closing tabs that appear to have active jobs.

## Appearance

- `font_family` (string)
- `font_size` (int, 6-72)
- `foreground_color` (hex)
- `background_color` (hex)
- `cursor_color` (hex)
- `link_color` (hex)
  - Used for traceback/diagnostic link highlighting.
- `selection_background_color` (hex)
- `selection_foreground_color` (hex)

## Background Image

- `background_image_path` (string path)
- `background_tint_color` (hex)
- `background_tint_strength` (int, 0-100)
- `background_alpha_mode` (enum)
  - `preserve`, `flatten`
- `background_size_mode` (enum)
  - `tile`, `fit width`, `fit height`, `fit`, `stretch`, `contain`, `center`

## Quick Commands

- `quick_commands` (array of objects)
  - Fast one-click commands from toolbar menu.

## Command Templates

- `command_templates` (array of objects)
  - Command entries with optional parameter prompts.

## ANSI Palette Overrides

- `ansi_colors` (object)
  - Optional overrides for ANSI color names:
  - `black`, `red`, `green`, `brown`, `blue`, `magenta`, `cyan`, `white`
  - `brightblack`, `brightred`, `brightgreen`, `brightbrown`, `brightblue`, `brightmagenta`, `brightcyan`, `brightwhite`

Example:

```json
{
  "ansi_colors": {
    "red": "#ff6b6b",
    "brightblue": "#8ec5ff"
  }
}
```

## Theme

- `theme_name` (string)
  - Any available theme name discovered from `src/themes`.

## System Integration

- `default_terminal_launcher_path` (string path)
  - Installed launcher command location (Linux, best effort). This is not the repository path.
- `default_terminal_desktop_file` (string path)
  - Installed desktop entry used for integration.
- Note for Pop!_OS / Ubuntu:
  - True system default terminal uses `update-alternatives` (`x-terminal-emulator`) and requires an elevated step.
  - Settings install/uninstall actions can offer to open the required `sudo` commands in an interactive terminal dialog.

## Prompt Editor Page

- Custom settings page embeds `Terminal/prompt-editor.py` as a widget.
- Prompt apply operations are executed from inside that widget and write shell rc managed blocks.
