# TDOC Guide

This document describes the current TDOC feature set and how to use it.

## What TDOC Is

TDOC is a lightweight symbol-linking workflow for `.tdoc` documents.

- You write links as `[Label]`.
- `Label` can be a symbol alias (from `.tdocproject`) or a file link (`path/to/file.tdoc`).
- The app builds `index.tdoc` automatically to show symbol references across your project.

## Project Setup

At project root, create:

- `.tdocproject` (required marker/config)
- one or more `.tdoc` files

If `.tdocproject` is missing, indexing features are disabled.

## `.tdocproject` Format

The file is plain text (UTF-8).

Supported line types:

- Blank lines
- Comment lines starting with `#`
- Indexing rules:
  - `include: pattern1 | pattern2`
  - `ignore: pattern1 | pattern2`
- Section headers ending with `:`
- Symbol definitions:
  - `Canonical Symbol = Alias 1 | Alias 2`
  - `Canonical Symbol = Alias 1 | Alias 2 ; key=value ; key2=value2`
  - `Canonical Symbol` (no extra aliases)
  - `Canonical Symbol ; key=value` (metadata only)
  - `Canonical Symbol = Alias 1 | Alias 2` followed by indented metadata continuation lines:
    - `    key=value`
    - `    key2=value2`

Continuation metadata lines are any non-empty lines indented deeper than the symbol line, until the next blank line or next top-level entry.
Both metadata styles can be mixed for the same symbol.

Example:

```txt
# Example TDOC project config
include: my_book/**/*.tdoc
ignore: drafts/** | archive/**

characters:
Jane Doe = Jane | J. Doe ; role=character
    status=alive
Grandma Doe

magic objects:
The Wheel of Hope
The Gift = Dragon's Claw | The Claw ; rarity=legendary
```

### Include/Ignore Pattern Behavior

- Patterns are matched against project-relative file paths (for example `my_book/chapter_1.tdoc`).
- Matching uses glob-style rules.
- If `include:` rules are present, only matching `.tdoc` files are scanned.
- `ignore:` rules always exclude matching `.tdoc` files from scans.
- `index.tdoc` is always excluded from content scans.

## Link Syntax in `.tdoc` Files

### Symbol links

```txt
[Jane]
[J. Doe]
[The Wheel of Hope]
```

These resolve through `.tdocproject` aliases.

### Frontmatter

You can optionally add frontmatter at the top of `.tdoc` files:

```txt
---
title: Introduction
status: draft
tags: lore, worldbuilding
index: on
---
```

Rules:

- Frontmatter must start on line 1 with `---` and close with `---`.
- Lines inside use `key: value`.
- Keys are case-insensitive in processing.
- `index: off` (or `false`, `no`, `0`) excludes that file from symbol/unresolved indexing scans.

### File links

```txt
[my_book/chapter_1.tdoc]
[my_book/chapter_1.tdoc#L42]
```

`#L42` jumps directly to line 42.

## Generated `index.tdoc`

`index.tdoc` is generated at project root and uses a protected auto-generated block.

It includes:

- Symbols grouped by `.tdocproject` section
- Canonical symbol heading
- Alias list
- Symbol metadata (if defined in `.tdocproject`)
- Line-level backlinks (`[file.tdoc#Lnn]`)
- `Unresolved` section for symbols used in docs but not defined in `.tdocproject`
- `Documents` section with per-file frontmatter metadata and indexing state
- `Frontmatter Warnings` section when malformed frontmatter is detected

Index generation respects `include:` and `ignore:` rules.

### Auto Block Markers

The app manages only the content between:

```txt
<!-- TDOC:AUTO START -->
...
<!-- TDOC:AUTO END -->
```

Behavior:

- If markers already exist, only that block is replaced on re-index.
- If markers do not exist, the app appends a managed block to the end of the file.
- Content outside the managed block is preserved as manual notes.


## Navigation

- `Ctrl+Click` on a symbol link opens `index.tdoc` and jumps to that symbol.
- `Ctrl+Click` on a file link opens that file.
- `Ctrl+Click` on `[file.tdoc#Lnn]` jumps to the target line.

## Right-Click Actions on Symbol Links

In the editor, right-click a symbol link to access:

- `Rename Alias...`
  - Renames that alias in `.tdocproject`
  - Rewrites that alias across `.tdoc` files
  - Regenerates index and reloads open tabs
- `Normalize This Symbol`
  - Rewrites aliases for that symbol to canonical form only
  - Example: `[Jane]` and `[J. Doe]` become `[Jane Doe]`

## Tools Menu

- `Index Project`
  - Regenerates `index.tdoc`
- `Validate Project`
  - Runs strict checks and reports errors/warnings
- `Normalize One Symbol to Canonical...`
  - Prompts for a symbol/alias and normalizes only that symbol across docs

## Validation Checks

Current validator checks:

- Missing `.tdocproject`
- Malformed alias definition lines
- Malformed symbol metadata entries (inline `; key=value` or indented continuation metadata)
- Duplicate section headers
- Empty sections
- Duplicate canonical symbols
- Alias collisions across symbols
- Empty include/ignore rules (for example `ignore:` with no patterns)
- Malformed/unclosed frontmatter in `.tdoc` files
- Unresolved symbol usage in `.tdoc` files

## Current Behavior Notes

- `index.tdoc` is auto-regenerated on save operations and some project actions.
- `index.tdoc` manual notes are preserved outside the TDOC auto markers.
- Only `.tdoc` files are indexed as content sources.
