# Run Configurations

Barley supports named run/build configurations for Python, Rust/Cargo, and C/C++.

Rust debug note:

- Rust Cargo targets can also be used from the debugger UI, but Rust debugging requires a supported LLDB adapter on `PATH`
- supported adapter names include `lldb-dap`, `lldb-vscode`, and distro-versioned `lldb-vscode-*`
- if no supported adapter is found, Rust debug actions remain disabled even though Rust run configurations still work

## Where to manage them

From the run menu:

- `Run -> Run Configuration`
- `Run -> Cargo Configuration`
- `Run -> Build Configuration`

From settings:

- `Project -> Run -> Configurations`
- `Project -> Build -> Rust (Cargo)`
- `Project -> Build -> C/C++`

Recommended screenshot: `barley_ide/docs/assets/screenshots/06-run-menu-configurations.png`

## Python run configurations

Stored at:

- `build.python.run_configs`
- `build.python.active_config`

Common fields:

- `name`, `script_path`, `args`, `working_dir`, `interpreter`, `env`

## Cargo configurations

Stored at:

- `build.rust.run_configs`
- `build.rust.active_config`

Command types:

- `run`, `test`, `build`, `custom`

Common fields:

- `name`, `command_type`, `package`, `binary`, `profile`, `features`, `args`, `working_dir`, `env`

Debug behavior:

- current Rust file debugging uses the nearest Cargo project context
- named Cargo targets can be launched from the debug menu using the same stored configuration data
- custom Cargo commands are run-only; they are not valid Rust debug targets

## C/C++ build configurations

Stored at:

- `build.cmake.build_configs`
- `build.cmake.active_config`

Common fields:

- `name`, `mode`, `working_dir`, `env`
- CMake mode: `build_dir`, `build_type`, `target`, `configure_args`, `build_args`, `run_args`, `parallel_jobs`
- Custom Command mode: `build_command`, `run_command`

Custom Command mode is intended for direct toolchain commands such as `nvcc`, `make`, or shell scripts.

## Fallback behavior

If no active Python or Cargo config is selected:

- Python: current file
- Rust: cargo run in discovered workspace context

See [Run and Terminal Model](../concepts/run-and-terminal-model.md).
