# Editor Basics

The editor supports language-aware editing for Python, C/C++, and Rust.

## Core capabilities

- syntax highlighting by file type
- completion suggestions
- go to definition and find usages
- rename symbol and refactor actions
- inline AI assist (when enabled)
- native viewers for non-text assets such as images and supported audio files

## Useful defaults

- completion: `Ctrl+Space`
- go to definition: `F12`
- find usages: `Shift+F12`
- rename symbol: `F2`
- trigger AI inline assist: `Alt+\\`

## Format actions

- `Edit -> Format File`
- `Edit -> Format Selection`

See [Formatting](../formatting/README.md).

## Asset viewers

When a file is not opened as text, Barley can route it to a built-in viewer:

- images open in the image viewer
- supported audio files open in a read-only player with playback controls, seeking, and a cached overview strip

Audio support depends on the available Qt multimedia backend, but common formats such as `.mp3`, `.wav`, `.ogg`, `.flac`, and `.m4a` are recognized when supported by the local system.

Recommended screenshot: `barley_ide/docs/assets/screenshots/04-project-dock-and-editor.png`
