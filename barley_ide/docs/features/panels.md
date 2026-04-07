# Panels

Barley uses dock panels for project context and runtime feedback.

## Main panels

- `Project Dock`
- `Terminal Dock`
- `Problems Dock`
- `Usages Dock`
- `Outline Dock`
- `Debug Dock`
- `Codex Agent Dock`

Toggle visibility from `View`.

## Codex Agent Dock highlights

The Codex dock is a conversational coding panel with:

- streaming transcript bubbles (`Assistant`, `Diff`, `Meta`, `System`, etc.)
- multiline composer with `Ctrl+Enter` send
- model/reasoning/permission controls
- recent-session picker and restore
- `@` file mention suggestions
- `+` file attachments staged into `.tide` for sandbox-safe access

See [Codex Agent Dock](codex-agent.md) for full usage and configuration details.

## Typical usage flow

1. Navigate files from Project Dock.
2. Fix diagnostics from Problems Dock.
3. Inspect references in Usages Dock.
4. Monitor run/build output in Terminal Dock.
5. Use Codex Agent Dock for task-level code chat and patch review.

Recommended screenshot: `barley_ide/docs/assets/screenshots/05-problems-dock-with-diagnostics.png`
