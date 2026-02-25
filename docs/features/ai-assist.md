# AI Assist

PyTPO supports inline AI-assisted suggestions in the editor.

## Required configuration

In `File -> Settings...`, open `IDE -> AI Assist` and configure:

- `ai_assist.enabled = true`
- `ai_assist.model`
- `ai_assist.base_url`
- `ai_assist.api_key`

## Trigger modes

Supported modes:

- `manual_only`
- `hybrid`
- `passive_aggressive`

Manual action is available from `Tools -> AI Inline Assist`.

## Behavior details

- file should be saved for best results
- suggestions use local context and retrieval snippets
- if completion popup is active, suggestion rendering may appear there

## Quick failure checks

1. verify model and API credentials
2. verify provider endpoint reachability
3. try manual trigger in a saved file
4. check status messages for provider errors

Recommended screenshot: `docs/assets/screenshots/14-ai-assist-settings-page.png`
