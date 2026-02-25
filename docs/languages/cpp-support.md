# C/C++ Support (clangd)

PyTPO uses `clangd` (LSP) for C/C++ language intelligence and CMake for build/run workflows.

## Features

- diagnostics
- completion
- go to definition
- find references
- hover
- build current file
- build and run current file

## Required tools

- `clangd`
- `cmake`
- compiler toolchain (gcc/clang/msvc depending on platform)

## `compile_commands.json` behavior

`c_cpp.compile_commands_mode` supports:

- `auto`: discover common build-folder locations
- `manual`: use `c_cpp.compile_commands_path`

If compile commands are missing, fallback flags come from `c_cpp.fallback.*`.

## Run/build actions

- `Run -> Build Current File`
- `Run -> Build + Run Current File`

Build + run pipeline:

1. resolve nearest CMake project root
2. run configure
3. run build
4. run executable

See [Run Configurations](../configuration/run-configurations.md).

Recommended screenshot: `docs/assets/screenshots/10-cpp-build-and-run-output.png`
