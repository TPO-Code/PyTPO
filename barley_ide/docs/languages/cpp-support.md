# C/C++ Support (clangd)

Barley uses `clangd` (LSP) for C/C++ language intelligence and supports either CMake or custom compiler commands for build/run workflows.

CUDA source and header files such as `.cu` and `.cuh` are treated as C/C++ files inside the editor.

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
- `cmake` for CMake-mode builds
- compiler toolchain (gcc/clang/msvc/nvcc depending on project)

## `compile_commands.json` behavior

`c_cpp.compile_commands_mode` supports:

- `auto`: discover common build-folder locations
- `manual`: use `c_cpp.compile_commands_path`

If compile commands are missing, fallback flags come from `c_cpp.fallback.*`.

## Run/build actions

- `Run -> Build Current File`
- `Run -> Build + Run Current File`

Build mode options:

1. `CMake`: resolve nearest `CMakeLists.txt`, configure, build, then run the selected or discovered executable
2. `Custom Command`: run the configured build command in the configured working directory, then optionally run the configured run command

Example `nvcc` setup:

- open `Project Settings -> Execution -> Build -> C/C++`
- set the active configuration `Mode` to `Custom Command`
- set `Build Command` to `nvcc -x cu main_nvidia.cpp -o mandel_nvidia -lSDL2 -lGLEW -lGL`
- set `Run Command` to `./mandel_nvidia`
- set `Working Directory` if the command should run somewhere other than the project root

See [Run Configurations](../configuration/run-configurations.md).

Recommended screenshot: `barley_ide/docs/assets/screenshots/10-cpp-build-and-run-output.png`
