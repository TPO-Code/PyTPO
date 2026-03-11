# IDE Settings

IDE settings are machine-local preferences saved in `ide-settings.json`.

Open settings from `File -> Settings...`, then use the `IDE` section.

## What belongs here

- appearance and window behavior
- keybindings
- global run defaults
- lint defaults
- AI Assist configuration
- Code Agents configuration
- Git/GitHub integration preferences

## High-impact pages

- `IDE -> Keybindings`
- `IDE -> Run`
- `IDE -> Linting`
- `IDE -> AI Assist`
- `IDE -> Code Agents`
- `IDE -> Appearance`

## Keybindings and workspace slots

Workspace slot shortcuts are configured on the `IDE -> Keybindings` page.

- default load shortcuts: `Ctrl+F1` through `Ctrl+F12`
- default save shortcuts: `Ctrl+Shift+F1` through `Ctrl+Shift+F12`
- each slot uses an assigned key, and PyTPO keeps load/save paired as `Ctrl+key` and `Ctrl+Shift+key`
- conflict checks validate both paired shortcuts before applying changes

See [Workspace Slots](../features/workspace-slots.md) for usage details.

Recommended screenshot: `docs/assets/screenshots/03-settings-dialog-overview.png`
Recommended screenshot: `docs/assets/screenshots/15-keybindings-settings-page.png`

## Scope rule

If a value should apply to all projects on your machine, set it in IDE scope.

If it should travel with one repository, use project scope instead.

See [Project Configuration](project-json.md).
