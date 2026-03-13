# Prompt Editor

The terminal settings now include a **Prompt Editor** custom page that embeds `pytpo_terminal/prompt-editor.py`.

Open it via:

- `Settings -> Prompt Editor`

## What It Does

- Lets you compose prompt segments (tokens, literals, style, reset, newline)
- Supports bash and zsh modes
- Shows rendered preview states (normal, venv, git, longpath, failed)
- Supports custom preset save/delete
- Supports right-click builder insertion (`Add Before` / `Add After`) from the palette set
- Supports interactive live-shell prompt preview
- Can apply prompt markup into shell rc files

## Files It Touches

- bash: `~/.bashrc`
- zsh: `~/.zshrc`

It writes a managed block:

- `# >>> prompt-editor managed >>>`
- `# <<< prompt-editor managed <<<`

## Safety Notes

- Prompt apply is an explicit action from inside the prompt editor widget.
- The settings dialog itself does not auto-apply prompt changes.
- If prompt markup cannot be round-tripped, widget enters custom/raw mode and shows warnings.

## Persistence

Editor state is persisted to:

- `.terminal/prompt-editor-state.json`

This includes shell-specific builder segments, raw markup text, selected shell, preview state, custom presets, and builder selection position.
