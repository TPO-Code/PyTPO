# PyTPO Terminal Documentation

Welcome to the standalone **PyTPO Terminal** app documentation.

This docs set is local to the terminal application package and is intended to be opened from the app via **Menu -> Help -> Documentation**.

## Contents

- [Getting Started](./getting-started.md)
- [Settings Reference](./settings-reference.md)
- [Quick Commands and Templates](./quick-commands.md)
- [Appearance and Theme Notes](./appearance-and-theme.md)
- [Prompt Editor](./prompt-editor.md)
- [Troubleshooting](./troubleshooting.md)

## What This App Is

PyTPO Terminal is a standalone, multi-tab terminal application built with PySide6 and the shared `TPOPyside` terminal widget stack.

Core capabilities:

- Multi-tab terminal sessions
- Persistent terminal settings stored in `.terminal/settings.json`
- Theme integration with shared project themes (`pytpo/themes`)
- Background image/tint controls
- Quick command and template runner
- Embedded prompt editor page in terminal settings
- ANSI palette overrides
- Optional close confirmation for active jobs
- Optional Linux desktop/default-terminal integration installer

## State and Config Files

- Settings file: `.terminal/settings.json`
- App-local docs: `pytpo_terminal/docs/`

All settings are normalized on save/load to keep the config valid.
