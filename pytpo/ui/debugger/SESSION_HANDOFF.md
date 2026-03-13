# Debugger Session Handoff

This file exists so a new Codex session can resume debugger work quickly.

## Status summary

Completed in the real IDE:

- debugger code lives in `pytpo/ui/debugger/`
- the IDE has a dedicated `Debugger` dock separate from `Debug Output`
- Python Run/Stop flows route into debugger sessions, and Rust can now route into native debug sessions when a supported LLDB adapter is available
- debugger sessions are tabbed per file/configuration
- Python now has separate terminal `Run` and debugger `Debug` flows again instead of forcing all Python launches through the debugger
- per-file breakpoints persist in project settings and restore when editors open
- conditional, hit-count, and logpoint breakpoints are supported in the editor gutter
- watch expressions persist per project and render in the debugger session
- debugger splitter/layout state persists at the IDE level
- Python run configs support both script and module debugger launches
- imported project modules can stop on breakpoints and open/focus the matching editor
- paused sessions expose stack frames, locals/globals, watches, and structured issue data
- debugger output is colorized, linkified, and can jump to referenced file/line locations
- backend stop escalation planning and startup-failure reporting now have direct regression coverage
- `PythonDebuggerBackend` now owns backend selection and wraps the current `bdb` provider instead of exposing the harness directly as the only implementation
- debugpy is now the active Python debugger provider and talks to the adapter over DAP behind the existing debugger interface
- normal script exits now auto-close the debugpy adapter session by tracking the launched debuggee PID instead of waiting indefinitely for a DAP shutdown event that may never arrive in this launch mode, with a forced adapter teardown fallback if disconnect stalls
- manual Stop now decides whether to terminate the debuggee based on whether the launched PID is still alive, so ended sessions disconnect cleanly while long-running ones still stop in one action
- regression coverage now includes real debugpy auto-finish and long-running stop flows when loopback sockets are available
- Python debugging now exposes a project default plus per-run-config `Just My Code` option instead of hardcoding the adapter flag
- debugger output rewrites debugpy's raw `justMyCode` step-skip note into clearer IDE-facing guidance
- regression coverage now includes a small Qt event-loop breakpoint flow when loopback sockets are available
- module entry resolution now understands common `pytpo/` package layouts, and the live debugpy suite includes a package/module launch case for that project shape
- regression coverage now includes watch refresh after stepping plus explicit launch/configuration failure reporting
- Python run configs can now execute normally in the built-in terminal for both script and module targets, while explicit debug actions keep using the debugger dock
- Python `Run` and `Debug` now remember separate last-selected config/current-file targets, so the debug button no longer follows the run target state
- Rust debugging now uses a native LLDB DAP backend behind the existing debugger dock/session UI, including breakpoint, stepping, watch, and Cargo-target integration
- Rust debug actions stay disabled until a supported LLDB adapter (`lldb-dap`, `lldb-vscode`, or versioned `lldb-vscode-*`) is installed and visible on `PATH`

Still not complete:

- broader Python launch target coverage
- richer long-running process handling beyond current root/working-directory hardening, PID-aware stop decisions, the current PID-poll plus forced-adapter-shutdown fallback, and larger GUI/event-loop scenarios
- native C++ backends

## Recommended next action

Continue in `pytpo/ui/debugger/` and `pytpo/ui/controllers/execution_controller.py`, starting with deeper backend hardening and broader launch coverage.

## Integration target files

- `pytpo/ui/python_ide.py`
- `pytpo/ui/editor_workspace.py`
- `pytpo/ui/widgets/code_editor.py`
- `pytpo/ui/controllers/execution_controller.py`
- `pytpo/ui/debugger/backend.py`
- `pytpo/ui/debugger/breakpoint_store.py`
- `pytpo/ui/debugger/controller.py`
- `pytpo/ui/debugger/dock_widget.py`
- `pytpo/ui/debugger/python_backend.py`
- `pytpo/ui/debugger/session_widget.py`
- `tests/test_debugger_integration.py`

## Important architecture rule

Debugger integration belongs in `pytpo/`, not `TPOPyside`.

## Current debugger shape

- `pytpo/ui/debugger/dock_widget.py` manages session tabs, shared controls, and persisted watch ownership
- `pytpo/ui/debugger/session_widget.py` owns one debugger backend/controller pair per tab
- `pytpo/ui/debugger/python_backend.py` is now the backend-selection seam, `pytpo/ui/debugger/debugpy_backend.py` owns the active DAP-backed Python runtime path, and `pytpo/ui/debugger/lldb_dap_backend.py` owns the Rust native-debug path
- `pytpo/ui/debugger/breakpoint_store.py` owns persisted breakpoints and watches

## Main remaining risks

- backend reliability for long-running processes and package edge cases beyond the current stop/root hardening, PID-aware stop decisions, and PID-based/forced-shutdown auto-finish fallback, now centered on the new debugpy runtime path
- launch coverage still centered on the current Python config model
- LLDB adapter availability is an external runtime dependency for Rust debugging, so user reports about a disabled Rust debug button should first check `which lldb-dap`, `which lldb-vscode`, and versioned `lldb-vscode-*` binaries
- no native C++ debugger adapter yet
