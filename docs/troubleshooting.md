# Troubleshooting

Use this page to quickly isolate setup and runtime issues.

## Fast Triage Checklist

1. Save the current file.
2. Confirm a project is open (not only the welcome screen).
3. Verify required tools are installed and reachable on `PATH`.
4. Open `File -> Settings...` and verify the relevant language settings.
5. Retry after restarting PyTPO.

## Tool Checks

```bash
python3 --version
ruff --version
clangd --version
clang-format --version
rust-analyzer --version
cargo --version
rustfmt --version
git --version
```

## No completion, go-to-definition, or references

Check:

- File type is supported (`.py`, `.c`, `.cpp`, `.rs`).
- Project language support is enabled:
  - `c_cpp.enable_cpp`
  - `rust.enable_rust`
- Tool paths are valid:
  - `c_cpp.clangd_path`
  - `rust.rust_analyzer_path`

See:

- [C/C++ Support](languages/cpp-support.md)
- [Rust Support](languages/rust-support.md)

## Formatting fails

Likely causes:

- formatter not installed
- invalid config file (`ruff.toml`, `.clang-format`, or rustfmt/cargo config)
- tool not reachable in current environment

See formatter guides:

- [Python Formatting](formatting/python-formatting.md)
- [C/C++ Formatting](formatting/cpp-formatting.md)
- [Rust Formatting](formatting/rust-formatting.md)

## Run/build appears to do nothing

Check:

- Active file is saved.
- `IDE -> Run -> Auto Save Before Run` is enabled, or save manually.
- Run target/config exists and points to valid script/package/build target.
- Terminal Dock is visible.

See [Run Configurations](configuration/run-configurations.md).

## C/C++ diagnostics are weak or missing

Most common causes:

- no `compile_commands.json`
- wrong `c_cpp.compile_commands_path` in manual mode
- compiler include paths missing when no compile commands are available

See [C/C++ Support](languages/cpp-support.md).

## Rust support missing or unstable

Confirm:

- file is inside a tree with `Cargo.toml`
- `rust-analyzer` is installed
- Rust project settings are enabled and valid

See [Rust Support](languages/rust-support.md).

## AI Assist not producing suggestions

Check:

- `ai_assist.enabled = true`
- `ai_assist.model` is set
- `ai_assist.base_url` and `ai_assist.api_key` are valid
- file is saved before requesting inline assist

See [AI Assist](features/ai-assist.md).

## Git actions unavailable or stale

Check:

- project folder is a Git repo
- `git` is installed
- credentials are configured for remote operations
- run `Git -> Refresh Git Status`

See [Git Integration](features/git.md).
