# Troubleshooting

## Documentation Viewer Says Docs Folder Is Missing

Expected docs location for terminal app help:

- `Terminal/docs/`

If missing, restore this directory and restart.

## Shell Fallback Warning Appears

The requested shell was unavailable. The app falls back to `bash`, `zsh`, `sh`, then `/bin/sh`.

Check:

- `default_shell_mode`
- `custom_shell_path` (if using custom mode)
- executable permissions on custom shell path

## Startup Directory Warning Appears

`startup_cwd` does not exist or is inaccessible.

Fix by updating Startup settings to a valid directory.

## Command Menu Is Empty

`quick_commands` and `command_templates` are both empty or invalid.

Check JSON in settings and ensure entries include `label` and `cmd`.

## Background Image Not Showing

Check:

- valid `background_image_path`
- image format support
- tint strength not overly opaque

## Settings File Recovery

If settings become malformed, the app normalizes values on next load.

Config file:

- `.terminal/settings.json`
