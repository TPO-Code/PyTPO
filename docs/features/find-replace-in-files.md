# Find and Replace in Files

Use `File -> Find in Files...` for cross-project search and replace.

## Capabilities

- multi-file text search
- regex search support
- preview and navigate results by file and line
- replace operations across matches

## Scope behavior

Results are constrained by project exclusions and visibility rules.

If expected files are missing, review:

- `indexing.exclude_dirs` / `indexing.exclude_files`
- `explorer.exclude_dirs` / `explorer.exclude_files`

See [Indexing and Search](../concepts/indexing-and-search.md).

Recommended screenshot: `docs/assets/screenshots/08-find-in-files-results.png`
