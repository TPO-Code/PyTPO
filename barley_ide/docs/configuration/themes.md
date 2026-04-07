# Themes (`.qss` and `.qsst`)

Barley supports two theme source formats:

- legacy QSS: `.qss`
- structured theme source: `.qsst` (TOML-based, compiled to QSS at runtime)

Both formats are supported. Existing `.qss` themes continue to work.

## Theme Location

Themes are discovered from:

- `TPOPyside/assets/themes/`

Examples in this repository:

- `TPOPyside/assets/themes/Midnight Forge.qsst`
- `TPOPyside/assets/themes/Deep Night.qsst`
- `TPOPyside/assets/themes/Crimson Core.qss`
- `TPOPyside/assets/themes/Nordic Sage.qss`

## Discovery and Priority Rules

Theme selection is based on file stem (name without extension), case-insensitive.

If both of these exist:

- `My Theme.qsst`
- `My Theme.qss`

Barley picks `.qsst` first (current extension priority is `(".qsst", ".qss")`).

Practical migration convention:

- keep the migrated structured file as `Theme Name.qsst`
- rename legacy file to `_Theme Name.qss` so it does not compete on the same stem

## Runtime Behavior

When applying a theme:

- `.qss` is read and applied directly.
- `.qsst` is parsed/compiled into QSS, then applied through the same stylesheet pipeline.

If `.qsst` compilation fails:

- a status-bar error is shown with the compile problem
- the app falls back to the configured fallback stylesheet (or empty stylesheet if not available)

## Auto-Refresh on Save

When you save the currently active theme file in the editor:

- Barley re-applies that active theme automatically
- this works for both `.qss` and `.qsst`

This allows iterative theme editing without manually reselecting the theme.

## `.qsst` Format Overview

`.qsst` is TOML plus theme-specific conventions:

- token tables at the top (for example `[colors]`, `[metrics]`, `[spacing]`, `[components.tabs]`)
- ordered `[[rules]]` blocks that map directly to QSS selectors
- token references using `${section.token}` syntax

Minimal structure:

```toml
name = "Example Theme"
format = "qsst-v1"

[colors]
text_primary = "#d4dbe6"
bg_root = "#1e1e1e"
border_subtle = "#3a3d41"

[metrics]
border_width = "1px"

[[rules]]
selector = "QWidget"
properties.color = "${colors.text_primary}"
properties.background = "${colors.bg_root}"

[[rules]]
selector = "QMenuBar"
properties.border-bottom = "${metrics.border_width} solid ${colors.border_subtle}"
```

## Rule Block Details

Each `[[rules]]` entry supports:

- `selector` (required): QSS selector text
- `comment` (optional): emitted as generated QSS comment
- `disabled` (optional): if `true`, rule is skipped
- properties:
  - preferred: `properties.<name> = <value>`
  - also supported: direct key/value entries in the same rule table (except reserved keys)

Property values can be:

- strings (most common)
- ints/floats/bools
- arrays (joined with spaces in generated QSS)

## Token Resolution

Token references use:

- `${section.token}`
- nested paths are supported (for example `${components.tabs.tab_pane_border_color}`)

Resolution behavior:

- unknown token references are compile errors
- cyclic references are compile errors
- all token values are resolved before final QSS emission

## Readability of Generated QSS

Generated QSS is intentionally readable:

- rule comments are preserved
- selector/property ordering follows the `[[rules]]` order in the source
- each rule is emitted as a normal QSS block

## Editor Support for `.qsst`

`.qsst` files integrate with existing editor infrastructure:

- syntax language id is TOML
- Rename Symbol supports `.qsst` token keys and `${...}` references

Rename in `.qsst` is token-aware and updates matching key/reference pairs within the same namespace.

## Migration Checklist (`.qss` -> `.qsst`)

1. Copy the legacy theme into a new `.qsst` file.
2. Create semantic token groups (`colors`, `metrics`, `spacing`, optional `components.*`).
3. Replace repeated literals with token references.
4. Move selector blocks into ordered `[[rules]]`.
5. Keep visuals close to the original first; normalize values only where safe.
6. Compile/test the theme in-app.
7. Rename old file to `_Theme Name.qss` to avoid stem conflicts.

## Recommended Conventions

- Use semantic names (`panel_bg`, `editor_bg`, `accent`, `border_subtle`) instead of placeholder names.
- Keep reusable values in tokens; avoid hardcoding duplicates in rules.
- Group related component values under `components.<area>`.
- Add short comments for non-obvious sections.
- Prefer minimal, explicit rule blocks over clever indirection.
