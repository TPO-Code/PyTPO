# AGENTS.md

## Scope

This package contains IDE-owned debugger integration code.

It is the live debugger implementation inside the IDE and is intentionally narrow:

- Python and Rust debugger integration
- owned by `barley_ide/`, not `TPOPyside`
- integrated into the existing bottom-dock workflow beside `Debug Output` and `Terminal`
- reuses the IDE `CodeEditor` and `EditorWorkspace`

## Rules

- Keep debugger UI, backend process control, and editor/workspace adapters separated.
- Do not move IDE debugger logic into `TPOPyside`.
- Reuse existing IDE save/run/interpreter helpers instead of inventing parallel config systems.
- Preserve the existing `dock_debug` wiring so `python_ide.py` can still append status/debug lines into the dock output pane.
- Extend the IDE `CodeEditor` through subclass hooks instead of calling `setExtraSelections()` externally.
- Keep debugger persistence scoped to project/editor data that the IDE already owns.

## Near-term direction

- Improve Python backend reliability beyond the current inline `bdb` harness.
- Keep `PythonDebuggerBackend` as the provider seam and swap providers behind it instead of coupling session widgets to one implementation.
- Keep the debugpy-backed provider and the IDE-facing backend contract aligned while expanding runtime parity and reliability.
- Keep the Rust LLDB-adapter path aligned with the same dock/session contract, and document clearly that Rust debugging depends on a supported LLDB adapter (`lldb-dap`, `lldb-vscode`, or versioned `lldb-vscode-*`) being on `PATH`.
- Expand launch coverage from Python script/module configs and current Rust/Cargo flows to broader project-defined debug targets.

## Later
- Add native debugger adapters for C/C++.

## Deferred concerns

These are not current blockers, but they are the main Python-debugger risks to revisit if users start reporting instability:

- Larger GUI applications may stress pause/continue/step behavior because they spend much more time inside Qt callbacks, timers, signals, and framework code.
- Longer-lived sessions may expose stale frame state, watch-refresh drift, duplicate events, or shutdown edge cases that do not show up in short scripts.
- Bigger projects tend to cross user-code and library/framework boundaries more often, which can surface rough edges in `Just My Code`, stack rendering, and step-in behavior.
- Complex runtime structures such as background tasks, helper processes, package entrypoints, and richer import layouts are more likely to expose launch-resolution and stop/finish gaps.
- If these become real problems later, prefer adding live regression coverage first so backend fixes stay targeted and verifiable.

# **IMPORTANT NOTES**
KEEP THIS DOCUMENT, INTEGRATION_PLAN.md and SESSION_HANDOFF.md updated
