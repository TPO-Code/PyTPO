# Workspace Slots

Workspace slots are project-specific layout presets. They let you jump between named working contexts with shortcuts.

## What a slot restores

Each slot restores a full workspace layout, including:

- open files
- which editor area each file is in (split/main/side/bottom)
- active tab per tab group
- dock and panel placement
- dock and panel visibility
- splitter sizes

Where available, editor view state is also restored (cursor/selection and scroll position).

## Default shortcuts

- load slot: `Ctrl+F1` through `Ctrl+F12`
- save slot: `Ctrl+Shift+F1` through `Ctrl+Shift+F12`

Loading uses the slot currently assigned to that key. Saving overwrites the assigned slot.

## Save a slot (with name)

1. Press `Ctrl+Shift+F#` for the target slot, or use `View -> Workspace Slots -> Save Slot ...`.
2. Enter a slot name in the dialog.
3. Press `Enter` (or `Save`) to confirm.

The name field is pre-populated with the current slot name (or a suggested name) and selected so you can replace it immediately.

After saving, the status bar shows a short confirmation.

If you cancel the dialog, the slot is not changed.

## Load a slot

1. Press `Ctrl+F#` for the slot, or use `View -> Workspace Slots -> Load Slot ...`.
2. The IDE restores the saved layout and updates the status bar with a short confirmation.
3. If you have unsaved changes, PyTPO asks you to confirm before replacing the current layout.

If files from the saved snapshot no longer exist, PyTPO skips only those files and restores everything else.  
You still get a successful load message with the number of skipped files.

## View menu entries

Workspace slot actions are available at `View -> Workspace Slots`.

- load entries show the current slot name (`Load Slot 2: API Debug`)
- empty slots are marked (`(empty)`)
- save entries also show the current slot name (`Save Slot 2: API Debug`)

## Keybinding customization

Workspace slot shortcuts are customizable in `File -> Settings... -> IDE -> Keybindings`.

Search for `Workspace Slot` actions and edit either action for a slot.

- each slot is based on an assigned key (`F2`, `2`, `Q`, etc.)
- load is always `Ctrl+<assigned key>`
- save is always `Ctrl+Shift+<assigned key>`

When you change a slot binding, PyTPO validates conflicts for both shortcuts as a pair and warns before applying overrides.

## Scope and storage

Workspace slots are saved per project (not globally).  
They are stored in the project configuration under `workspace_presets`.
