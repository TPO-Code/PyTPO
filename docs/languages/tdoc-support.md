# TDOC Support (`.tdoc` + `.tdocproject`)

PyTPO includes built-in TDOC document support with symbol-aware links, project indexing, and Problems panel diagnostics.

## File types and tabs

- `.tdoc` files open in the custom TDOC document widget (`TDocDocumentWidget`), not the standard code editor.
- `.tdocproject` opens in the standard editor as plain text.
- `index.tdoc` is treated as a TDOC file and opens in the TDOC widget.
- TDOC tabs use the same editor background configuration as code-editor tabs.

## TDOC project root resolution

TDOC root is resolved in this order:

1. nearest ancestor folder named `.tdocprojects`
2. nearest ancestor containing a `.tdocprojects/` folder
3. nearest ancestor containing `.tdocproject`
4. project root `.tdocprojects/`
5. project root `.tdocproject`
6. file directory fallback

Relative TDOC file links resolve from that TDOC root. This means `.tdocprojects` is treated as the TDOC root when present.
Relative file links in general resolve from that same TDOC root.

## `.tdocproject` format

Supported lines:

- comments: lines starting with `#`
- include/ignore rules: `include: pattern1 | pattern2`, `ignore: pattern1 | pattern2`
- frontmatter schema rule: `frontmatter_schema: frontmatter.schema.json`
- section headers: `Section Name:`
- symbol definitions:
  - `Canonical Symbol`
  - `Canonical Symbol = Alias 1 | Alias 2`
  - `Canonical Symbol ; key=value`
  - `Canonical Symbol = Alias 1 | Alias 2 ; key=value ; key2=value2`
  - continuation metadata lines (indented deeper than the symbol line), for example:
    - `Canonical Symbol = Alias 1 | Alias 2`
    - `    key=value`
    - `    key2=value2`

You can mix both metadata styles in one symbol entry. Continuation metadata lines also accept optional trailing `;` and optional `;`-separated multiple `key=value` pairs on the same line.

Example:

```txt
Characters:
    Ari Vale = Ari | A. Vale ; role=protagonist
        faction=Wardens of Ash

    Mira Stone = Mira
        role=mentor
        is_alive=yes
```

Validation includes duplicate symbols, alias collisions, malformed metadata, empty rules, empty sections, unresolved symbols, and more.

Section headers should start with a capital letter (for example `Locations:`). Non-capitalized headers produce a warning.

## Link syntax in `.tdoc`

- symbol links: `[Alias Or Canonical]`
- file links: `[path/to/file.ext]`
- file+line links: `[path/to/file.ext#L42]`
- titled links (custom display text): `[Shown Text|target]`
  - example: `[2|Characters/Main/Molly.tdoc#L2]`, `[Build Script|../../scripts/build.sh]`
  - `target` can be a symbol label/alias or a file/file+line target
  - existing non-titled links remain supported
  - file targets are path-like values (for example `docs/guide.md`, `src/main.cpp`, `../notes.txt`)
- inline images:
  - `![images/map.png]`
  - `![Map Overview|images/map.png]`
  - images render inline in TDOC editor when the target resolves and loads

Path scope:

- File targets are resolved relative to TDOC root.
- Paths can still escape via `..` segments (for example `../../outside.txt`) if intentionally authored.
- Absolute-style targets (`/abs/path`, `C:\abs\path`, `~`) are not treated as file links.
- Image targets follow the same root/relative rules.

Optional frontmatter is supported at file top:

```txt
---
title: Chapter 1
status: draft
index: on
---
```

`index: off` excludes a document from symbol-reference indexing.

Frontmatter editor support:

- Frontmatter is folded by default when opening a `.tdoc` document.
- Right-click inside a `.tdoc` document and use `Fold Frontmatter` / `Unfold Frontmatter` to toggle frontmatter visibility while editing.
- TDOC completion inside frontmatter supports key/value suggestions.
- If `.tdocproject` defines `frontmatter_schema`, completion keys/enum values are driven by that schema.

Folding behavior:

- TDOC uses gutter fold markers for foldable regions.
- Frontmatter, markdown heading sections, fenced code blocks, and list blocks are foldable.
- Clicking a fold marker toggles that region.

### Frontmatter schema via `.tdocproject`

You can define frontmatter rules by adding this line to `.tdocproject`:

```txt
frontmatter_schema: frontmatter.schema.json
```

You can create this schema file from Project Explorer:

- `New File` -> `TDOC` -> `Frontmatter Schema`
  - creates `frontmatter.schema.json` in the selected folder

Schema path should be relative to TDOC root. JSON schema supports:

- `properties` (key definitions)
- per-key `enum` (value suggestions/validation)
- per-key `const` (single allowed value)
- `required` (required frontmatter keys)
- `additionalProperties` (set to `false` to warn on unknown keys)

Minimal example:

```json
{
  "properties": {
    "title": { "type": "string" },
    "status": { "enum": ["draft", "review", "final"] },
    "index": { "enum": ["on", "off"] }
  },
  "required": ["title", "status"],
  "additionalProperties": false
}
```

## Markdown headings in `.tdoc`

TDOC documents support markdown-style ATX headings:

- `# Heading 1`
- `## Heading 2`
- `### Heading 3`

Behavior:

- Heading markers (`#`, `##`, `###`) are shown while editing the heading line.
- When the caret is outside the heading line, the markers are hidden and the line is rendered as a heading.
- Rendered heading levels use different font sizes (H1 > H2 > H3).
- Saving preserves the original raw heading markup.
- Heading text can include TDOC links (for example `# [SourDough Discard Crumpets]`), and those links remain clickable while rendered as a heading.

Notes:

- Heading parsing is line-based and expects heading markers at the start of the line (optionally with up to 3 leading spaces).
- A space is required after the heading markers (for example `## Title`).

## Markdown lists in `.tdoc`

TDOC documents support markdown-style lists:

- unordered list markers: `*` and `-`
- ordered/numbered list markers: `1.`, `2.`, `3.` ...

Unordered list examples:

- `* Item`
- `- Item`
- `    * Nested Item`
- `    - Nested Item`

Ordered list example:

- `1. First`
- `2. Second`
- `3. Third`

Behavior:

- Unordered list markers are shown while editing the line.
- When the caret is outside an unordered list line, the marker is rendered as a bullet glyph (`•`).
- Ordered list markers remain visible and are validated for sequence continuity.
- List text can include TDOC links, and those links remain clickable.
- Saving preserves the original raw list markup.

Notes:

- Unordered list parsing is line-based and expects `*` or `-` followed by at least one space.
- Numbered-list diagnostics warn when sequence values are skipped and include a `Renumber numbered list` quick fix.

## Navigation

- `Ctrl+Click` a file link to open that file in the IDE (and line for `#Lnn` links where applicable).
- `Ctrl+Click` a symbol link to open `index.tdoc` and jump to that symbol.
- Images are inline visual content, not click-navigation links.

If needed, symbol navigation creates/refreshes `index.tdoc` first.

## Building the TDOC index

- Use the titlebar toolbar `Index` button (`Build TDOC Index`).
- The button is shown when a project is loaded and at least one TDOC-related file is open.
- Index build is manual by design. Saving TDOC files runs validation, but does not automatically rebuild `index.tdoc`.

Index rebuild also runs after TDOC symbol actions that rewrite aliases/links (rename alias, normalize symbol).

## Generated `index.tdoc` format

Generated content is placed below a dashed separator:

```txt
--------------------
# Index
## ...
```

- Manual notes above the separator are preserved.
- If no separator exists, one is created.
- Legacy HTML auto markers are removed if present and are not used anymore.
- Generated headers use markdown heading markup (`#`, `##`) so they render as headings in the TDOC editor.
  - top-level index title: `# Index`
  - generated sections: `## <Section Name>`
- Detail rows under each heading are indented with 4 spaces.
- Generated sections include:
  - symbol groups by section
  - `Unresolved`
  - `Documents`
  - `Project Warnings` (including section-capitalization warnings)
  - `Frontmatter Warnings`
- References are grouped by file path. Each file row links to the file, and line numbers are emitted as titled links.

Example structure (with clickable link markup preserved):

```txt
## Characters
    [Ari Vale]
        Aliases: [Ari Vale], [Ari], [A. Vale]
        References:
            [demo/chapter_01.tdoc]: [6|demo/chapter_01.tdoc#L6], [9|demo/chapter_01.tdoc#L9]
```

## Problems panel integration and quick fixes

TDOC diagnostics are reported in the same Problems panel used by other languages (`source: tdoc`), including:

- missing `.tdocproject`
- malformed/invalid `.tdocproject` entries
- invalid/missing `frontmatter_schema` configuration or schema JSON load errors
- unresolved symbols
- frontmatter warnings
- frontmatter schema warnings (missing required keys, unknown keys when disallowed, invalid enum values)
- section capitalization warnings
- missing inline image files (`![...]` / `![caption|...]`)
- numbered-list sequence gaps (for example `1.`, `3.` with missing `2.`)

TDOC quick fixes from Problems context menu:

- `Add '<symbol>' to .tdocproject`
  - appends the unresolved symbol at end of `.tdocproject`
- `Capitalize section '<section>'`
  - rewrites the warned section header in `.tdocproject`
- `Renumber numbered list`
  - rewrites the affected numbered list block in the `.tdoc` document to a continuous sequence

## TDOC in-editor symbol actions

In a `.tdoc` tab, right-click a symbol link for:

- `Rename Alias...`
  - updates alias in `.tdocproject`
  - rewrites link usages across TDOC documents
  - rebuilds index and refreshes open TDOC tabs
- `Normalize This Symbol`
  - rewrites all aliases for that symbol to canonical form in TDOC docs
  - rebuilds index and refreshes diagnostics
