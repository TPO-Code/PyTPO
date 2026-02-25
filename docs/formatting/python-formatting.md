# Python Formatting (`ruff format`)

PyTPO formats Python files with Ruff over stdin/stdout.

## Config discovery order

For each file, PyTPO searches upward for:

1. `ruff.toml`
2. `.ruff.toml`
3. `pyproject.toml` containing `[tool.ruff`

Search stops at project root for in-project files; loose files search to filesystem root.

## Missing config flow

If no valid Ruff config is found, PyTPO opens `No ruff.toml found` with:

- `Minimal config`
- `Full config`
- `Create & Format`
- `Cancel`

On create, `ruff.toml` is written to project root (or file directory fallback) and formatting reruns.

Recommended screenshot: `docs/assets/screenshots/12-format-missing-ruff-config-dialog.png`

## Execution strategy

Primary command candidate:

- `<interpreter> -m ruff format --stdin-filename <file> -`

Fallback command:

- `ruff format --stdin-filename <file> -`

## Selection formatting

`Edit -> Format Selection` falls back to full-file formatting for Python.
