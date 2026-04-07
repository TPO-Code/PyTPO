# IDE Debugger Integration

This package contains the live IDE debugger implementation. The old
`IDE_DEBUGGER_TEST/` prototype has been retired.

Current scope:

- dedicated `Debugger` dock integrated beside `Debug Output` and `Terminal`
- Python debugging from current-file and run-configuration launches
- Rust debugging from current-file and named Cargo targets when a supported LLDB adapter is available on `PATH`
- per-session debugger tabs with pause, continue, step, and stop controls
- multi-file breakpoints, including conditional, hit-count, and logpoint support
- persisted project breakpoints and watch expressions
- persisted debugger splitter/layout state
- paused stack frames, variables, watches, and structured issue reporting
- colorized/linkified debugger output with clickable source references
- stop-escalation planning and startup-failure handling covered by regression tests
- Python debugger backend selection is now separated from the active provider implementation
- debugpy is now the active Python debugger provider behind the existing debugger UI/controller contract
- a supported LLDB adapter (`lldb-dap`, `lldb-vscode`, or versioned `lldb-vscode-*`) is required for Rust debugging; if none is found, Rust debug actions stay disabled

Still incomplete:

- stronger runtime coverage and edge-case handling for the debugpy-backed provider
- broader Python debug target coverage beyond the current script/module model
- richer long-running/package handling beyond the current working-directory/root hardening
- C/C++ native debugger backends

Active handoff docs:

- `barley_ide/ui/debugger/INTEGRATION_PLAN.md`
- `barley_ide/ui/debugger/SESSION_HANDOFF.md`

Primary integration seams:

- `barley_ide/ui/python_ide.py`
- `barley_ide/ui/widgets/code_editor.py`
- `barley_ide/ui/editor_workspace.py`
- `barley_ide/ui/controllers/execution_controller.py`
