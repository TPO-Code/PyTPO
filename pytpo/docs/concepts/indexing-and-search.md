# Indexing and Search

Indexing controls where PyTPO searches, lints, and surfaces navigation results.

## Core settings

- `indexing.exclude_dirs`
- `indexing.exclude_files`
- `indexing.follow_symlinks`
- `explorer.exclude_dirs`
- `explorer.exclude_files`
- `explorer.hide_indexing_excluded`

## Practical defaults

Typical high-value excludes:

- `.git`
- `.venv`
- `node_modules`
- build output folders
- generated code trees

## Symptom mapping

- Missing files in search results: check indexing excludes.
- Missing files in Project Dock only: check explorer excludes.
- Slow indexing/search: add excludes for generated/heavy folders.

See [Troubleshooting](../troubleshooting.md).
