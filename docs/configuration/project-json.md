# Project Configuration (`.tide/project.json`)

Project settings live in:

- `.tide/project.json`

Use `File -> Settings...` and edit `Project` pages for safest updates.

## What this file controls

- project metadata (`project_name`)
- interpreter resolution (`interpreter`, `interpreters`)
- indexing and explorer excludes (`indexing`, `explorer`)
- run/build configs (`build`)
- C/C++ language behavior (`c_cpp`)
- Rust language behavior (`rust`)

## Minimal example

```json
{
  "project_name": "My Project",
  "interpreters": {
    "default": "python",
    "by_directory": []
  },
  "indexing": {
    "exclude_dirs": [".git", ".venv", "node_modules", ".tide"],
    "exclude_files": ["*.lock"],
    "follow_symlinks": false
  },
  "build": {
    "python": { "active_config": "", "run_configs": [] },
    "cmake": { "active_config": "Debug", "build_configs": [] },
    "rust": { "active_config": "", "run_configs": [] }
  }
}
```

For full key-by-key details, see [Project JSON Reference](project-json-reference.md).
