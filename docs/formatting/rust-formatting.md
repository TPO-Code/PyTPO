# Rust Formatting (`cargo fmt` / `rustfmt`)

PyTPO formats Rust files with a workspace-aware strategy.

## Format order

1. `cargo fmt` when the file is inside a Cargo workspace and editor buffer matches disk
2. fallback to `rustfmt --emit stdout --stdin-filepath <file>`

## Why fallback exists

Fallback keeps format available for loose files or contexts where cargo execution is not suitable.

## Selection formatting

`Edit -> Format Selection` currently falls back to full-file formatting for Rust.

## Config behavior

Rust formatter config is discovered by Cargo/rustfmt using normal filesystem rules (for example `rustfmt.toml`).

See [Rust Support](../languages/rust-support.md).
