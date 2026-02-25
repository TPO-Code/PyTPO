# Rust Support (`rust-analyzer` + Cargo)

PyTPO uses `rust-analyzer` for language intelligence and Cargo for build/run workflows.

## Features

- diagnostics and quick fixes
- completion and hover
- go to definition / find references
- rename symbol
- outline symbols
- Cargo run/test/build/custom configurations

## Required tools

- `rust-analyzer`
- `cargo`
- `rustfmt` (for fallback formatting path)

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

See [Rust Formatting](../formatting/rust-formatting.md) for format behavior.

Recommended screenshot: `docs/assets/screenshots/11-rust-cargo-configuration.png`
