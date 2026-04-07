# AGENTS.md

## Project Overview

This is a PySide6 IDE application for working with multi-language projects, including Python, C/C++, and Rust.

## Environment

* **Python**: >= 3.11
* **Package manager**: `uv`
* **Framework**: PySide6

## Important: Always Use `uv run`

This project uses `uv` for project management.

**Never use bare `python` or `pip` commands.**
All Python commands must be prefixed with `uv run`.

```bash
# Correct
uv run barley-ide

# Incorrect
python -m barley_ide
pip install ...
```

## About the Project

The main project is primarily an IDE that supports mixed-language projects.

The following languages are currently supported:

* Python
* C/C++
* Rust

The project also supports other file types for editing only, such as:

* Markdown
* JavaScript
* JSON
* Plain text

It also supports custom file types:

* `.todo` / `.task` / `.lst` for task and checklist files
* `.tdocproject` and `.tdoc` for a linked documentation system

## Project Structure

* `barley_ide/` contains the main IDE application code
* `TPOPyside/` contains reusable UI/widgets/dialogs intended to remain independent from this IDE
* `barley_ide/docs/` contains IDE documentation
* `barley_ide/app.py` is the IDE bootstrap module

## Architecture Boundaries

### `barley_ide/`

`barley_ide/` contains the main IDE code and application-specific behavior.

This includes:

* IDE features
* language support integration
* keybinding/configuration logic
* linting, completion, and language intelligence
* project-aware behavior
* controllers, managers, and IDE-specific widgets


### `TPOPyside/`

`TPOPyside` is intended to be reusable across multiple projects and should be treated as a separate, independent package.

Code in `TPOPyside` must:

* not depend on the IDE
* not require IDE services, settings, or controllers
* remain reusable in other editors or applications

If changes are needed in `TPOPyside`, they should be made only to:

* improve generality
* expose reusable primitives/hooks
* reduce assumptions
* support extension by outside code

Do **not** place IDE-specific logic in `TPOPyside`.

If IDE-specific behavior is needed, prefer:

* sub-classing
* composition
* controllers
* adapters
* injected callbacks/strategies

### Boundary Rule

If code would only make sense inside this IDE, it probably belongs in `barley_ide/`, not in `TPOPyside`.

## Coding Rules

* Keep changes small and focused
* Preserve behavior unless asked otherwise
* Avoid unrelated cleanup
* Prefer explicit ownership and separation of concerns
* Do not add duplicate mechanisms for the same behavior
* If multiple languages need similar behavior, prefer shared abstractions or mappings rather than copy-pasted implementations
* Prefer clear extension points over hard coded special cases
* Keep reusable code generic and application code specific
* Do not rely on transitive imports or wildcard imports to make symbols available in another module.
* Every Python module must explicitly import the names it directly uses.
* Avoid from module import * except in rare, deliberate public API re-export cases, and never use it as a dependency-sharing mechanism between implementation files.
* __init__.py may be used to re-export the package's public API, but implementation modules must not depend on names being leaked through other internal modules.
Every Python module
## Editing Strategy

* Read the surrounding code first
* Confirm that the target logic belongs in that file/package before changing it
* Prefer fixing one seam at a time
* Keep refactors incremental
* Do not broaden scope unless necessary for correctness
* If a structural issue is discovered, fix only the part needed for the current task and report the rest separately

## Prompting Rules

* Solve only the requested task
* Keep refactors incremental
* Preserve existing behavior unless explicitly told to change it
* If structure is wrong, fix only the seam needed for the task
* Report follow-up issues separately instead of silently expanding scope
* Prefer minimal coherent changes over broad rewrites

## Current Architecture Direction

Implementation of a robust debugging system

### Behavior to Preserve

syntax highlighting, code folding, color swatches etc must remain part of the base implementation.


## Change Checklist

* [ ] Is this change in the correct package/module?
* [ ] Are architectural boundaries respected?
* [ ] Is behavior preserved unless intentionally changed?
* [ ] Were unrelated edits avoided?
* [ ] Is the result easier to understand?
* [ ] Was reusable code kept generic?
* [ ] Was IDE-specific behavior kept out of `TPOPyside`?
* [ ] are the TDOC editor and the CodeEditor indistinguishable to the user (keep parity between them both whenever possible)
