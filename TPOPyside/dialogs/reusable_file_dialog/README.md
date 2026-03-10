# Reusable File Dialog

A self-contained `PySide6` file dialog package that can be copied into another project.

## Included features

- Top navigation/search strip with breadcrumb-style path pills (`_PathPillBar`) and back/forward/up actions.
- Navigation bar supports manual location entry/edit:
  - click the path pills area to switch to text mode
  - press `Enter` to navigate
  - press `Esc` to cancel
  - press `Ctrl+L` to focus location editing
- Quick access menu includes:
  - zoom in/out/reset
  - copy
  - paste
  - new file / new folder
- File-view context menu includes the same `zoom`, `copy`, `paste`, and `new` options.
- Configurable sidebar locations (Home, Downloads, Music, etc., or app-defined list).
- Configurable name filters (`"Images (*.png *.jpg)"`, etc.).
- Starred paths input/output:
  - pass an initial list via constructor or static helpers
  - retrieve current list at close via `starredPaths()` or `resultPayload()`
- Background support with brightness and stylesheet overrides.

## Directory contents

- `reusable_file_dialog/__init__.py`
- `reusable_file_dialog/config.py`
- `reusable_file_dialog/dialog.py`
- `reusable_file_dialog/styles/default.qss`
- `reusable_file_dialog/README.md`

## Public API

### Types

- `SidebarLocation(label: str, path: str | Path, icon_name: str = "folder")`
- `BackgroundOptions(image_path=None, brightness=1.0, qss_path=None, extra_qss="")`
- `FileDialogResult(accepted, selected_files, selected_name_filter, starred_paths)`

### Dialog class

`ReusableFileDialog` supports core `QFileDialog`-like methods:

- `setFileMode(...)`, `fileMode()`
- `setAcceptMode(...)`, `acceptMode()`
- `setDirectory(...)`, `directory()`
- `setNameFilter(...)`, `setNameFilters(...)`, `nameFilters()`
- `selectNameFilter(...)`, `selectedNameFilter()`
- `setDefaultSuffix(...)`, `defaultSuffix()`
- `selectFile(...)`, `selectedFiles()`
- `setOptions(...)`, `options()`

Reusable-specific methods:

- `setSidebarLocations(locations)`
- `sidebarLocations()`
- `setStarredPaths(paths)`
- `starredPaths()`
- `setBackground(BackgroundOptions(...))`
- `resultPayload()`

### Static helpers

All static helpers return starred paths as part of the response:

- `getOpenFileName(...) -> (filename, selected_filter, starred_paths)`
- `getOpenFileNames(...) -> (filenames, selected_filter, starred_paths)`
- `getSaveFileName(...) -> (filename, selected_filter, starred_paths)`
- `getExistingDirectory(...) -> (directory, starred_paths)`

## Example usage

```python
from pathlib import Path

from reusable_file_dialog import (
    BackgroundOptions,
    ReusableFileDialog,
    SidebarLocation,
)

locations = [
    SidebarLocation("Workspace", Path.home() / "Work", "folder"),
    SidebarLocation("Downloads", Path.home() / "Downloads", "folder-download"),
]

dialog = ReusableFileDialog(
    caption="Import Media",
    directory=Path.home(),
    name_filters=["Images (*.png *.jpg *.jpeg)", "All Files (*)"],
    sidebar_locations=locations,
    starred_paths=[str(Path.home() / "Downloads")],
    background=BackgroundOptions(
        image_path="/path/to/background.jpg",
        brightness=0.85,
    ),
)

dialog.setFileMode(ReusableFileDialog.FileMode.ExistingFiles)
if dialog.exec():
    selected_files = dialog.selectedFiles()
    starred = dialog.starredPaths()
```

## Demo app

Run the included demo from the repository root:

```bash
python3 -m reusable_file_dialog.demo
```

Alternative:

```bash
python3 reusable_file_dialog/demo.py
```

The demo shows:

- passing an initial starred list into the dialog
- reading starred paths back after close
- configurable sidebar locations
- name filters
- background brightness configuration
- quick menu and context menu actions (`zoom`, `copy`, `paste`, `new`)

## Styling contract

The default theme is loaded from `reusable_file_dialog/styles/default.qss`.

### Object names available for overrides

- `#ReusableFileDialogRoot`
- `#DialogTopBar`
- `#BackButton`
- `#ForwardButton`
- `#UpButton`
- `#QuickAccessButton`
- `#PathPillBar`
- `#PathPillButton`
- `#PathPillSeparator`
- `#SearchLineEdit`
- `#LocationSidebar`
- `#FileTableView`
- `#FileGridView`
- `#DialogEntryRow`
- `#FilenameEdit`
- `#FilterCombo`

### Background and brightness notes

- `BackgroundOptions.image_path` paints a scaled image behind dialog content.
- `BackgroundOptions.brightness` is clamped to `0.0..2.0`:
  - `< 1.0` darkens
  - `> 1.0` lightens
- `BackgroundOptions.qss_path` lets you replace the default stylesheet entirely.
- `BackgroundOptions.extra_qss` appends additional QSS rules after the loaded stylesheet.

## Exporting to another project

Copy the entire `reusable_file_dialog/` directory and ensure the target project has `PySide6` installed.
