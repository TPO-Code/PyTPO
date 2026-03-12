# Getting Started

## Launch

From repo root:

```bash
uv run python terminal_app_main.py
```

The app opens `Terminal/main_window.py` and loads settings from `.terminal/settings.json`.

You can override startup/new-tab working directory from CLI:

```bash
uv run python terminal_app_main.py --cwd /path/to/project
```

## Basic Navigation

- `Ctrl+T`: New terminal tab
- `Ctrl+W`: Close current tab
- `Ctrl+Alt+W`: Close other tabs
- `Ctrl+Shift+W`: Close all tabs
- `Ctrl+Tab`: Next tab
- `Ctrl+Shift+Tab`: Previous tab
- `Ctrl+,`: Open Settings
- `F1`: Open Documentation

## Main Menu (Burger Menu)

- Tab actions
- Settings
- Help -> Documentation
- Exit

## Session Behavior

Each tab launches a shell based on your configured shell settings and startup directory.

By default the app can warn before closing a tab that appears to have an active running job.

## First Recommended Setup

1. Open **Settings**.
2. Configure shell mode (Auto, bash/zsh/sh, or custom path).
3. Set startup directory and startup tab count.
4. Set your preferred font and colors.
5. Add quick commands/templates for your normal workflows.
6. Optional: open **Settings -> System Integration** to install/remove desktop default-terminal integration.
