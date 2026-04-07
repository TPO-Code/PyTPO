# Debugger Integration Plan

This file tracks the current debugger state inside the real IDE.

## Goal

Record what is already integrated, what remains open, and where follow-up work
should continue.

## Current state

Completed in the real IDE:

- IDE-owned debugger code under `barley_ide/ui/debugger/`
- a dedicated `Debugger` dock separate from `Debug Output`
- Python launches from the main Run/Stop controls routed into debugger sessions
- Rust launches from current-file and named Cargo targets can now route into native debug sessions when a supported LLDB adapter is available
- per-session debugger tabs similar to the terminal workflow
- Python now has separate terminal `Run` and debugger `Debug` actions again instead of routing all launches through the debugger
- per-file breakpoint persistence and restore
- conditional, hit-count, and logpoint breakpoint support in the IDE editor gutter
- Python run-config debugging for scripts and modules
- imported-module breakpoint stops and multi-file pause mapping
- persisted watch expressions and debugger splitter/layout state
- stack frames plus structured locals/globals, watches, and issue presentation
- more defensive debugger-dock sizing, colored/linkified output, and clickable source references
- backend stop escalation planning plus startup-failure reporting coverage
- Python debugger backend selection now runs through an IDE-owned provider seam instead of hard-wiring the inline harness directly into the session widget
- debugpy is now the active Python debugger provider, speaking to the adapter over DAP behind the existing IDE debugger interface
- normal script exits now auto-finish reliably by tracking the launched debuggee PID and closing the adapter session without requiring a manual Stop press, with a forced adapter teardown fallback if disconnect stalls
- manual Stop now terminates the debuggee only when its launched PID is still alive, so completed sessions disconnect cleanly while long-running ones still stop promptly
- real debugpy integration coverage now includes both simple-script auto-finish and long-running stop flows when loopback sockets are available
- Python debugging now exposes a project default plus per-run-config `Just My Code` option instead of hardcoding the adapter flag
- debugger output now rewrites debugpy's raw `justMyCode` step-skip note into clearer IDE-facing guidance
- real debugpy integration coverage now includes a small Qt event-loop breakpoint flow when loopback sockets are available
- module entry resolution now understands common `barley_ide/` package layouts, and live debugpy coverage includes a package/module launch case for that shape
- regression coverage now includes watch refresh after stepping and explicit launch/configuration failure reporting
- Python script and module run configs now also support normal built-in-terminal execution, with separate explicit debug actions for debugger launches
- Python `Run` and `Debug` keep separate remembered target selections, so choosing `Debug Current File` no longer leaves the debug button bound to the run-config selection
- targeted regression coverage for breakpoint persistence and imported-module stops
- Rust debugging now uses an LLDB DAP backend behind the existing debugger dock/session UI and enables `.rs` gutter breakpoints plus current-file/Cargo-target debug actions
- Rust debug actions intentionally stay disabled until a supported LLDB adapter (`lldb-dap`, `lldb-vscode`, or versioned `lldb-vscode-*`) is available on `PATH`

Still incomplete:

- broader regression coverage for real debugpy runtime flows, especially around larger PySide GUI flows and more complex longer-lived processes
- broader Python debug target coverage beyond the current script/module config model
- richer package/long-running process handling beyond the current working-directory/root hardening
- native C/C++ debugger backends

## Current module layout

- `barley_ide/ui/debugger/backend.py`
- `barley_ide/ui/debugger/breakpoint_store.py`
- `barley_ide/ui/debugger/controller.py`
- `barley_ide/ui/debugger/dock_widget.py`
- `barley_ide/ui/debugger/editor_adapter.py`
- `barley_ide/ui/debugger/lldb_dap_backend.py`
- `barley_ide/ui/debugger/python_backend.py`
- `barley_ide/ui/debugger/session_context.py`
- `barley_ide/ui/debugger/session_widget.py`

## Main IDE seams already in use

Main window / dock:

- `barley_ide/ui/python_ide.py`

Editor workspace:

- `barley_ide/ui/editor_workspace.py`

IDE editor subclass:

- `barley_ide/ui/widgets/code_editor.py`

Run/config resolution:

- `barley_ide/ui/controllers/execution_controller.py`

Tests:

- `tests/test_debugger_integration.py`

## Recommended next session focus

1. Harden the real debugpy/DAP-backed Python provider with more runtime coverage and edge-case testing.
2. Expand Python debug target coverage beyond the current script/module flow.
3. Add more regression coverage around issue reporting, process-start failures, and live watch refresh behavior.

## Later
4. Add live Rust debug coverage once an environment with a supported LLDB adapter is available, then design the C/C++ native adapter without leaking IDE logic into `TPOPyside`.
