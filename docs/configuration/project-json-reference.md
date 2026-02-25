# Project JSON Reference

Reference for `.tide/project.json` keys.

## Top-level keys

- `project_name`: display name for workspace UI
- `interpreter`: legacy Python fallback interpreter
- `interpreters`: modern interpreter defaults and overrides
- `indexing`: indexing exclusion policy
- `explorer`: project tree visibility policy
- `build`: Python/CMake/Rust run and build configurations
- `c_cpp`: clangd integration and fallback compile flags
- `rust`: rust-analyzer integration and options
- `open_editors`: persisted open-editor state

## `interpreters`

- `default`: default interpreter command/path
- `by_directory`: list of objects:
  - `path`
  - `python`
  - `exclude_from_indexing` (optional)

## `indexing`

- `exclude_dirs`: list of directory names/glob patterns
- `exclude_files`: list of file names/glob patterns
- `follow_symlinks`: boolean

## `explorer`

- `exclude_dirs`
- `exclude_files`
- `hide_indexing_excluded`: hide index-excluded paths in explorer

## `build.python`

- `active_config`
- `run_configs[]` entries with:
  - `name`, `script_path`, `args`, `working_dir`, `interpreter`, `env`

## `build.cmake`

- `active_config`
- `build_configs[]` entries with:
  - `name`, `build_dir`, `build_type`, `target`
  - `configure_args`, `build_args`, `run_args`, `parallel_jobs`, `env`

## `build.rust`

- `active_config`
- `run_configs[]` entries with:
  - `name`, `command_type`, `command`
  - `package`, `binary`, `profile`, `features`
  - `args`, `test_filter`, `working_dir`, `env`

## `c_cpp`

- `enable_cpp`
- `clangd_path`
- `query_driver`
- `compile_commands_mode`: `auto` or `manual`
- `compile_commands_path`
- `log_lsp_traffic`
- `fallback`:
  - `c_standard`, `cpp_standard`
  - `include_paths`, `defines`, `extra_flags`

## `rust`

- `enable_rust`
- `rust_analyzer_path`
- `rust_analyzer_args`
- `did_change_debounce_ms`
- `log_lsp_traffic`
- `initialization_options` (JSON object)
