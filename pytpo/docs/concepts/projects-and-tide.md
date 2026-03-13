# Projects and `.tide`

PyTPO is project-first: opening a folder defines workspace scope for editing, indexing, and run behavior.

## Project root

The open project folder is used for:

- relative path resolution
- settings discovery
- run/build defaults
- workspace detection (for CMake/Cargo)

## `.tide` directory

PyTPO stores project metadata in `.tide/`.

Most important file:

- `.tide/project.json`: project-scoped settings and run/build configs

See [Project Configuration](../configuration/project-json.md).

## Version control guidance

Commit `.tide/project.json` only if your team wants shared project defaults.

Keep machine-local or temporary state out of version control when possible.
