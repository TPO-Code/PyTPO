# Interpreter Selection

For Python execution and Python-based tooling, interpreter resolution follows this order.

1. matching directory override in `interpreters.by_directory[*].python`
2. `interpreters.default`
3. legacy `interpreter`
4. fallback command: `python`

## Directory overrides

Each `interpreters.by_directory` entry can include:

- `path`: subdirectory matcher
- `python`: interpreter command/path
- `exclude_from_indexing`: optional indexing exclusion for that path

## Path behavior

- Absolute interpreter paths are used directly.
- Relative paths resolve from the project root.

See [Project JSON Reference](../configuration/project-json-reference.md).
