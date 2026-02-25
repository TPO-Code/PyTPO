from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


from file_dialog import BackgroundOptions, FileDialog, SidebarLocation


STARRED_STATE_FILE = Path(__file__).resolve().with_name(".demo_starred.json")


def _load_starred_paths() -> list[str]:
    try:
        payload = json.loads(STARRED_STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    raw_paths = payload.get("starred_paths", [])
    if not isinstance(raw_paths, list):
        return []
    output: list[str] = []
    for entry in raw_paths:
        if isinstance(entry, str) and entry.strip():
            output.append(entry.strip())
    return output


def _save_starred_paths(paths: list[str]) -> None:
    payload = {"starred_paths": paths}
    STARRED_STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class DemoWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Reusable File Dialog Demo")
        self.resize(860, 520)

        self._starred_paths = _load_starred_paths()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        controls.addWidget(QLabel("Mode:"))
        self._mode_combo = QComboBox(self)
        self._mode_combo.addItems(
            [
                "Open File",
                "Open Files",
                "Save File",
                "Select Directory",
            ]
        )
        controls.addWidget(self._mode_combo)

        self._open_btn = QPushButton("Open Demo Dialog", self)
        self._open_btn.clicked.connect(self._open_dialog)
        controls.addWidget(self._open_btn)
        controls.addStretch(1)

        layout.addLayout(controls)

        self._output = QTextEdit(self)
        self._output.setReadOnly(True)
        layout.addWidget(self._output, 1)

        self._append("Demo ready.")
        self._append(f"Loaded {len(self._starred_paths)} persisted starred paths.")

    def _dialog_locations(self) -> list[SidebarLocation]:
        home = Path.home()
        candidates = [
            SidebarLocation("Home", home, "user-home"),
            SidebarLocation("Downloads", home / "Downloads", "folder-download"),
            SidebarLocation("Music", home / "Music", "folder-music"),
            SidebarLocation("Pictures", home / "Pictures", "folder-pictures"),
            SidebarLocation("Projects", home / "Work", "folder"),
        ]
        return [entry for entry in candidates if Path(entry.path).exists()]

    def _dialog_background(self) -> BackgroundOptions:
        # Uses package default.qss by default. You can replace with your own qss_path.
        return BackgroundOptions(brightness=0.92)

    def _open_dialog(self):
        mode = self._mode_combo.currentText()
        filters = [
            "Images (*.png *.jpg *.jpeg *.webp *.gif)",
            "Text (*.txt *.md *.json *.py)",
            "All Files (*)",
        ]

        dialog = FileDialog(
            parent=self,
            caption=f"Reusable Dialog Demo - {mode}",
            directory=Path.home(),
            name_filters=filters,
            sidebar_locations=self._dialog_locations(),
            starred_paths=self._starred_paths,
            background=self._dialog_background(),
        )

        if mode == "Open File":
            dialog.setFileMode(FileDialog.FileMode.ExistingFile)
            dialog.setAcceptMode(FileDialog.AcceptMode.AcceptOpen)
        elif mode == "Open Files":
            dialog.setFileMode(FileDialog.FileMode.ExistingFiles)
            dialog.setAcceptMode(FileDialog.AcceptMode.AcceptOpen)
        elif mode == "Save File":
            dialog.setFileMode(FileDialog.FileMode.AnyFile)
            dialog.setAcceptMode(FileDialog.AcceptMode.AcceptSave)
            dialog.setDefaultSuffix("txt")
        else:
            dialog.setFileMode(FileDialog.FileMode.Directory)
            dialog.setAcceptMode(FileDialog.AcceptMode.AcceptOpen)

        accepted = dialog.exec() == dialog.DialogCode.Accepted
        selected_files = dialog.selectedFiles() if accepted else []
        self._starred_paths = dialog.starredPaths()
        _save_starred_paths(self._starred_paths)

        self._append("")
        self._append(f"Mode: {mode}")
        self._append(f"Accepted: {accepted}")
        self._append(f"Selected filter: {dialog.selectedNameFilter()}")
        self._append(f"Selected files ({len(selected_files)}):")
        for path in selected_files:
            self._append(f"  - {path}")
        self._append(f"Starred paths ({len(self._starred_paths)}):")
        for path in self._starred_paths:
            self._append(f"  - {path}")

    def _append(self, line: str):
        self._output.append(line)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("FileDialogDemo")

    try:
        window = DemoWindow()
        window.show()
    except Exception as exc:  # noqa: BLE001
        QMessageBox.critical(None, "Demo Error", f"Unable to start demo:\n{exc}")
        return 1

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
