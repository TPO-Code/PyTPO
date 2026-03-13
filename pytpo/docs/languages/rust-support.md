# Rust Support (`rust-analyzer` + Cargo + LLDB DAP Adapter)

PyTPO uses `rust-analyzer` for language intelligence, Cargo for build/run workflows, and an LLDB DAP adapter for Rust debugging.

## Features

- diagnostics and quick fixes
- completion and hover
- go to definition / find references
- rename symbol
- outline symbols
- Cargo run/test/build/custom configurations
- Rust debugging for current-file and named Cargo targets when an LLDB adapter is available on `PATH`

## Required tools

- `rust-analyzer`
- `cargo`
- `rustfmt` (for fallback formatting path)
- one supported LLDB debug adapter:
  - `lldb-dap`
  - `lldb-vscode`
  - distro-versioned binaries such as `lldb-vscode-14`

## Workspace discovery

For each file, PyTPO walks upward for `Cargo.toml`:

- nearest manifest is baseline context
- if a parent manifest defines `[workspace]`, that workspace root is used

One rust-analyzer process is managed per discovered workspace root.

## Cargo configurations

Manage from:

- `Run -> Cargo Configuration`
- `File -> Settings... -> Project -> Build -> Rust (Cargo)`

Default behavior with no active config:

- run `cargo run` in discovered workspace context

## Rust debugging

Rust debugging is enabled only when all of these are true:

- the active file is a `.rs` file inside a discovered Cargo project
- a supported LLDB debug adapter is installed
- that adapter is visible on `PATH`

Quick check:

```bash
which lldb-dap
which lldb-vscode
compgen -c | grep '^lldb-vscode-'
```

On Ubuntu/Pop-style LLVM packages, `lldb` may install `lldb-vscode-14` or a similar versioned binary instead of `lldb-dap`.

If none of those commands find an adapter, Rust debug actions stay disabled until one is installed and the IDE is restarted or reloaded.

See [Rust Formatting](../formatting/rust-formatting.md) for format behavior.

Recommended screenshot: `pytpo/docs/assets/screenshots/11-rust-cargo-configuration.png`
