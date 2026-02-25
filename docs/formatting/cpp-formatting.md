# C/C++ Formatting (`clang-format`)

PyTPO formats C/C++ via `clang-format -style=file`.

## Style file discovery

PyTPO searches upward for `.clang-format` from the file directory:

- in-project file: stop at project root
- loose file: stop at filesystem root

Unreadable or invalid configs are treated as missing.

## Missing config flow

If missing, PyTPO opens `No .clang-format found` with:

- style presets: `LLVM`, `Google`, `Chromium`, `Mozilla`, `WebKit`
- mode: `Minimal config` or `Full config`
- actions: `Create & Format`, `Cancel`

On create, `.clang-format` is written to project root (or file directory fallback) and formatting reruns.

Recommended screenshot: `docs/assets/screenshots/13-format-missing-clang-format-dialog.png`

## Selection formatting

`Edit -> Format Selection` uses line-range formatting via clang-format `-lines=<start>:<end>`.
