# Daily Development Workflow

Use this when you want a repeatable loop from opening PyTPO to shipping changes.

## 1. Open and orient

1. Start PyTPO.
2. Open your project from `File -> Open Project...`.
3. Confirm the `Project Dock`, `Problems Dock`, and `Terminal Dock` are visible from `View`.

Recommended screenshot: `docs/assets/screenshots/04-project-dock-and-editor.png`

## 2. Edit with fast feedback

1. Open your target file.
2. Use completion (`Ctrl+Space`) and navigation (`F12`, `Shift+F12`).
3. Keep the Problems panel open while editing.

Recommended screenshot: `docs/assets/screenshots/05-problems-dock-with-diagnostics.png`

## 3. Run, build, or cargo-run

- Quick run: `F5` or `Run -> Run`.
- Python config run: `Run -> Run Configuration`.
- Cargo config run: `Run -> Cargo Configuration`.
- C/C++ build-only: `Run -> Build Current File`.
- C/C++ build+run: `Run -> Build + Run Current File`.

See [Run Configurations](../configuration/run-configurations.md).

## 4. Format before commit

- `Edit -> Format File`
- `Edit -> Format Selection`

Language formatter references:

- [Python Formatting](../formatting/python-formatting.md)
- [C/C++ Formatting](../formatting/cpp-formatting.md)
- [Rust Formatting](../formatting/rust-formatting.md)

## 5. Commit and sync

From the `Git` menu:

1. `Commit...`
2. `Push...` or `Commit and Push...`
3. `Refresh Git Status` if needed

See [Git Integration](../features/git.md).
