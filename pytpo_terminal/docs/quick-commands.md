# Quick Commands and Templates

The terminal includes a command runner in the per-tab toolbar.

## Data Model

Both `quick_commands` and `command_templates` are arrays of objects with this shape:

```json
{
  "label": "Run tests",
  "cmd": "uv run pytest -q",
  "params": [],
  "cwd": "",
  "env": {},
  "dryrun": false
}
```

Fields:

- `label` (required): menu label (supports slash grouping like `Python/Tests/Unit`)
- `cmd` (required): command text sent to shell
- `params` (optional): list of prompt variable names (for templates)
- `cwd` (optional): shell preamble `cd` path
- `env` (optional): map of env vars exported before command
- `dryrun` (optional): ask confirmation before execution

## Template Variable Expansion

Template commands can reference placeholders:

```json
{
  "label": "pip install {pkg}",
  "cmd": "uv pip install {pkg}",
  "params": ["pkg"]
}
```

When launched, the app prompts for `pkg`.

## Practical Examples

### Quick Commands

```json
[
  {"label": "Python/Run tests", "cmd": "uv run pytest -q"},
  {"label": "Rust/Cargo check", "cmd": "cargo check"}
]
```

### Templates

```json
[
  {
    "label": "Git/Checkout branch",
    "cmd": "git checkout {branch}",
    "params": ["branch"],
    "dryrun": true
  },
  {
    "label": "Python/Pip install",
    "cmd": "uv pip install {package}",
    "params": ["package"]
  }
]
```

## Notes

- Commands execute in the active interactive shell so shell state changes persist.
- Invalid entries are ignored by settings normalization.
