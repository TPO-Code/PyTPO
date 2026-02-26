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
- section headers: `Section Name:`
- symbol definitions:
  - `Canonical Symbol`
  - `Canonical Symbol = Alias 1 | Alias 2`
  - `Canonical Symbol ; key=value`
  - `Canonical Symbol = Alias 1 | Alias 2 ; key=value ; key2=value2`

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
Index
    ...
```

- Manual notes above the separator are preserved.
- If no separator exists, one is created.
- Legacy HTML auto markers are removed if present and are not used anymore.
- Indentation is 4 spaces per level.
- Generated sections include:
  - symbol groups by section
  - `Unresolved`
  - `Documents`
  - `Project Warnings` (including section-capitalization warnings)
  - `Frontmatter Warnings`
- References are grouped by file path. Each file row links to the file, and line numbers are emitted as titled links.

Example structure (with clickable link markup preserved):

```txt
    Characters:
        [Ari Vale]
            Aliases: [Ari Vale], [Ari], [A. Vale]
            References:
                [demo/chapter_01.tdoc]: [6|demo/chapter_01.tdoc#L6], [9|demo/chapter_01.tdoc#L9]
```

## Problems panel integration and quick fixes

TDOC diagnostics are reported in the same Problems panel used by other languages (`source: tdoc`), including:

- missing `.tdocproject`
- malformed/invalid `.tdocproject` entries
- unresolved symbols
- frontmatter warnings
- section capitalization warnings
- missing inline image files (`![...]` / `![caption|...]`)

TDOC quick fixes from Problems context menu:

- `Add '<symbol>' to .tdocproject`
  - appends the unresolved symbol at end of `.tdocproject`
- `Capitalize section '<section>'`
  - rewrites the warned section header in `.tdocproject`

## TDOC in-editor symbol actions

In a `.tdoc` tab, right-click a symbol link for:

- `Rename Alias...`
  - updates alias in `.tdocproject`
  - rewrites link usages across TDOC documents
  - rebuilds index and refreshes open TDOC tabs
- `Normalize This Symbol`
  - rewrites all aliases for that symbol to canonical form in TDOC docs
  - rebuilds index and refreshes diagnostics
