# Codex Agent Dock

PyTPO includes a docked chat interface for `codex` CLI.  
This is separate from inline AI Assist and is designed for multi-step coding conversations.

## What it is for

Use Codex Agent Dock when you want:

- conversational coding help in a persistent transcript
- patch/diff style responses rendered in a chat-friendly view
- clickable file links directly from agent output
- quick model/reasoning/permission controls per conversation
- resumable Codex sessions from recent history

Use [AI Assist](ai-assist.md) for inline editor completion behavior.

## Prerequisites

1. `codex` CLI installed and available on `PATH`
2. an open project directory in PyTPO
3. a valid Codex login/session in your shell environment

Quick check:

```bash
codex --version
```

## Open the dock

You can open it from:

- `Tools -> Open Codex Agent Dock`
- `View -> Codex Agent Dock` (dock visibility toggle)

## Configure command template

Open:

- `File -> Settings...`
- `Code Intelligence -> Code Agents`
- `Codex Command Template`

The command supports `{project}` placeholder expansion.

Default template:

```text
codex exec --skip-git-repo-check --sandbox workspace-write -
```

## Dock layout

Top row:

- `Instructions (shown/hidden)` toggle for optional system preamble
- recent session dropdown
- `New Chat`
- `Stop`

Middle:

- transcript bubbles (`You`, `Assistant`, `Thinking`, `Tools`, `Diff`, `Meta`, `System`)

Composer:

- multiline input box
- attachment summary row (`Attached (...)`) with `Clear`
- bottom control row: `Agent Options` toggle, `+` attachment button, `Send`
- model/reasoning/permissions/rate-limits section (toggleable)

## Keyboard behavior

- `Enter`: newline
- `Ctrl+Enter`: send
- mention popup:
  - `Up/Down/PageUp/PageDown`: navigate
  - `Enter` or `Tab`: accept mention
  - `Esc`: close mention popup

## Conversation model

The dock starts a Codex CLI process per turn and streams output into bubbles.

First turn prompt shape:

```text
[optional preamble]

Project path: <project-path>

[optional staged attachment links]
User message:
<your message>
```

Follow-up turn prompt shape:

- normally just your follow-up text
- if attachments are present, an attachment block is prepended

## Agent options

The dock exposes three runtime controls:

- `Model`
- `Reasoning` (`Low`, `Medium`, `High`, `Extra High`)
- `Permissions` (`Default Permissions`, `Full Access`)

When options change, active session attachment is reset and a new chat session begins for safety/consistency.

## Session behavior

The dock can attach to recent Codex sessions:

- recent sessions are loaded from `~/.codex/sessions`
- list is filtered to current project `cwd`
- labels use first user message (friendly preview) + timestamp
- selecting an entry restores visible user/assistant transcript

Session resets happen automatically when:

- project root changes
- critical agent options change
- user starts `New Chat`

## Transcript rendering

The dock classifies stream lines into roles:

- `Assistant`: normal response text
- `Thinking`: reasoning/status style blocks when emitted by CLI
- `Tools`: command/run status lines
- `Diff`: unified patch content with colorized additions/removals/hunks
- `Meta`: model/session/token and auxiliary structured info
- `System`: dock lifecycle notices

At end of turn:

- a clickable `Changed files:` list is appended when file changes are detected
- links open files directly in the IDE

## File mentions with `@`

In composer input:

- type `@` to get file suggestions from project files
- selecting one inserts a markdown file link reference
- mention rendering collapses to filename-only for readability
- on send, collapsed mentions are serialized back to full markdown links

Notes:

- suggestion index is project-local and excludes common heavy folders like `.git`, `node_modules`, `target`, `dist`, `.venv`
- mention target paths are home-relative when possible, otherwise absolute

## Attach files with `+`

Click `+` to add one or more files using the IDE reusable file dialog integration.

Behavior:

1. selected source files are tracked in the attachment summary row
2. on send, files are copied into:
   - `.tide/codex-agent/attachments/<chat-id>/`
3. prompt includes staged links to those copied files
4. staging area is cleaned when chat resets or dock shuts down

Why staging exists:

- ensures files are available inside project workspace for sandboxed Codex runs
- avoids depending on out-of-workspace absolute paths

Current limitation:

- staged attachments are chat-lifecycle scoped and not preserved as long-term artifacts

## Rate limits

Rate limit label is read-only and shown as:

- `5h: <remaining>% | Weekly: <remaining>%`

Data source:

- latest `token_count` payload in the active session log under `~/.codex/sessions`

If unavailable, placeholder is shown.

## Command compatibility notes

The dock normalizes common Codex command variants to reduce failure modes:

- ensures `exec` invocation pattern for non-resume turns
- injects/remaps runtime flags for model/reasoning/sandbox compatibility
- uses resume-compatible flags when continuing existing sessions

If command startup fails, the dock surfaces a system error bubble and non-zero exit status bubble.

## Debug trace file

For temporary UI verification, the dock writes bubble snapshots to:

```text
.tide/codex-agent-bubble-debug.log
```

This file is conversation-scoped and reset when a new chat starts.

## Limitations and expectations

- No built-in CLI undo command is exposed by this integration.
- Undo/revert should be handled with normal editor/git workflows.
- Behavior depends on installed `codex` CLI version and provider-side capabilities.
- Permission mode affects what filesystem scope Codex can access directly.

## Recommended workflow

1. open project
2. open Codex Agent Dock
3. keep `Permissions = Default` unless a task requires broader access
4. attach files with `+` only when needed
5. use `@` mentions for precise file references
6. review `Diff` and `Changed files` bubbles after each turn
7. commit changes with your normal VCS workflow

Recommended screenshot: `docs/assets/screenshots/16-codex-agent-dock.png`
