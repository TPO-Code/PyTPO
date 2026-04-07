# Linting and Problems

Diagnostics are surfaced in-editor and in the Problems Dock.

## Python linting

Configurable backends and fallback:

- `ruff`
- `pyflakes`
- `ast`

Default behavior uses `ruff`, then falls back to `pyflakes`, and only then to `ast` syntax checks.

Key IDE settings:

- `lint.enabled`
- `lint.backend`
- `lint.fallback_backend`
- `lint.run_on_save`
- `lint.run_on_idle`
- `lint.respect_excludes`
- `lint.max_problems_per_file`

## C/C++ and Rust diagnostics

Language-server diagnostics are provided by:

- `clangd`
- `rust-analyzer`

These diagnostics are routed into the same Problems surface.

## TDOC diagnostics

TDOC project/document diagnostics are also routed to the same Problems Dock (`source: tdoc`), including unresolved symbols, `.tdocproject` issues, and frontmatter warnings.

Problems context menu quick fixes include:

- add unresolved symbol to `.tdocproject`
- capitalize TDOC section headers that start lowercase

## Common fixes for missing diagnostics

- save the file first
- verify tool paths in project settings
- if undefined-name errors are missing, make sure the selected interpreter can run `ruff` or `pyflakes`
- reduce exclusion rules if files are hidden from analysis

See [Troubleshooting](../troubleshooting.md).
