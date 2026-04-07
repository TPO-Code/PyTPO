# Project Templates (`.barley_ide/templates/*.json`)

Barley New Project templates are loaded from IDE-scope JSON files under:

`<ide_app_dir>/templates/`

By default this resolves to:

`.barley_ide/templates/`

On first use, Barley bootstraps:

`default_templates.json`

## File format

Use either form:

1. Object with `templates` list
2. Top-level list

Each template entry supports:

- `id` (required, unique key)
- `label` (required, shown in New Project template dropdown)
- `description` (optional, shown below template selector)
- `files` (required object: `relative/path` -> file content)

JSON object form:

```json
{
  "version": 1,
  "templates": [
    {
      "id": "example-template",
      "label": "Example Template",
      "description": "README + starter source",
      "files": {
        "README.md": "# {{project_title}}\n",
        "src/main.py": "print('Hello')\n"
      }
    }
  ]
}
```

Top-level list form:

```json
[
  {
    "id": "example-template",
    "label": "Example Template",
    "files": {
      "README.md": "# {{project_title}}\n"
    }
  }
]
```

## Template variables

The following placeholders are supported in both file paths and file contents:

- `{{project_name}}`
- `{{project_title}}`
- `{{folder_name}}`
- `{{folder_slug}}`
- `{{module_name}}`
- `{{cargo_name}}`
- `{{cmake_name}}`

Unknown placeholders are left unchanged.

## Merge and override behavior

- All `.json` files in `.barley_ide/templates/` are loaded.
- Files are loaded in lexical filename order.
- If multiple files define the same template `id`, the later file overrides the earlier template.
- Invalid files or invalid template entries are skipped; New Project still works using remaining valid templates.
- If no valid templates are loaded, Barley falls back to built-in defaults.

## Safety rules for generated files

Template file keys in `files` are treated as relative paths.

- Absolute paths are not written.
- Parent escapes (`..`) are blocked.
- Files are written only inside the newly created project directory.

## Example: add a custom TDOC template pack

Create `.barley_ide/templates/tdoc-pack.json`:

```json
{
  "templates": [
    {
      "id": "tdoc-notes",
      "label": "TDOC Notes Pack",
      "description": "Minimal TDOC docs starter",
      "files": {
        ".tdocproject": "Notes:\n    Daily Note\n",
        "overview.tdoc": "---\ntitle: Overview\nindex: on\n---\n\n# Overview\n\nStart here.\n",
        "notes/daily-note.tdoc": "# Daily Note\n\nEntry for {{project_title}}.\n"
      }
    }
  ]
}
```

Restart Barley (or reopen New Project dialog) and select `TDOC Notes Pack`.

## Current limitations

- New Project templates are JSON-only.
- No script hooks are supported.
- Post-create CMake configure is still tied to built-in `cpp-cmake` template id.
