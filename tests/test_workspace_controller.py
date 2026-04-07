from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QObject

from barley_ide.ui.controllers.workspace_controller import WorkspaceController


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def showMessage(self, text: str, _timeout: int = 0) -> None:
        self.messages.append(str(text))


class _FakeTabs:
    def __init__(self, widget: object) -> None:
        self._widget = widget
        self.refreshed: list[object] = []

    def indexOf(self, widget: object) -> int:
        return 0 if widget is self._widget else -1

    def _refresh_tab_title(self, widget: object) -> None:
        self.refreshed.append(widget)


class _FakeEditorWorkspace:
    def __init__(self, tabs: _FakeTabs) -> None:
        self._tabs = tabs

    def all_tabs(self) -> list[_FakeTabs]:
        return [self._tabs]


class _FakeViewer:
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.load_calls: list[str] = []

    def load_file(self, path: str) -> bool:
        self.load_calls.append(str(path))
        return True


class _FakeSvgTab:
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.refresh_calls = 0

    def refresh_preview_from_source(self) -> None:
        self.refresh_calls += 1

    def document(self):
        return object()


class _FakeIde(QObject):
    def __init__(self, widget: object, path: str) -> None:
        super().__init__()
        self._widget = widget
        self._path = str(Path(path).resolve())
        self._status_bar = _StatusBar()
        self._tabs = _FakeTabs(widget)
        self.editor_workspace = _FakeEditorWorkspace(self._tabs)
        self._project_config_reload_active = False
        self.theme_controller = None
        self.editor_change_highlight_service = None

    def statusBar(self) -> _StatusBar:
        return self._status_bar

    def is_project_read_only(self) -> bool:
        return False

    def _autosave_config(self) -> dict[str, bool]:
        return {"enabled": False}

    def _iter_open_document_widgets(self) -> list[object]:
        return [self._widget]

    def _document_widget_path(self, widget: object) -> str:
        return str(getattr(widget, "file_path", "") or "")

    def _canonical_path(self, path: str) -> str:
        return str(Path(path).resolve())

    def _collect_open_file_paths(self) -> set[str]:
        return {self._path}

    def _is_tdoc_related_path(self, _path: str) -> bool:
        return False

    def _schedule_tdoc_validation(self, _path: str, *, delay_ms: int = 0) -> None:
        _ = delay_ms

    def _is_project_config_path(self, _path: str) -> bool:
        return False

    def _queue_project_config_reload(self, *, source: str, honor_open_editors: bool = True) -> None:
        _ = (source, honor_open_editors)

    def _editor_from_document_widget(self, _widget: object):
        return None

    def _doc_key_for_editor(self, _editor: object) -> str:
        return "doc"

    def _refresh_editor_title(self, _editor: object) -> None:
        pass

    def _attach_editor_lint_hooks(self, _editor: object) -> None:
        pass

    def _request_lint_for_editor(self, _editor: object, *, reason: str, include_source_if_modified: bool) -> None:
        _ = (reason, include_source_if_modified)

    def refresh_subtree(self, _path: str) -> None:
        pass

    def schedule_git_status_refresh(self, *, delay_ms: int = 0) -> None:
        _ = delay_ms


class WorkspaceControllerTests(unittest.TestCase):
    def test_note_editor_saved_refreshes_documentless_viewers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "icon.svg"
            path.write_text("<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8'/>", encoding="utf-8")

            viewer = _FakeViewer(str(path))
            ide = _FakeIde(viewer, str(path))
            controller = WorkspaceController(ide)

            class _SavedEditor:
                file_path = str(path)

            controller._note_editor_saved(_SavedEditor(), source="manual save")

            self.assertEqual(viewer.load_calls, [str(path.resolve())])
            self.assertEqual(ide._tabs.refreshed, [viewer])

    def test_external_file_change_reloads_documentless_viewer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "icon.png"
            path.write_bytes(b"not-a-real-image-but-load-is-mocked")

            viewer = _FakeViewer(str(path))
            ide = _FakeIde(viewer, str(path))
            controller = WorkspaceController(ide)

            sig = controller._external_file_signature(str(path))
            self.assertIsNotNone(sig)
            controller._handle_external_file_change(str(path), sig)

            self.assertEqual(viewer.load_calls, [str(path.resolve())])
            self.assertTrue(any("Reloaded from disk" in msg for msg in ide.statusBar().messages))

    def test_note_editor_saved_refreshes_svg_wrapper_preview_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "icon.svg"
            path.write_text("<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8'/>", encoding="utf-8")

            wrapper = _FakeSvgTab(str(path))
            ide = _FakeIde(wrapper, str(path))
            controller = WorkspaceController(ide)

            class _SavedEditor:
                file_path = str(path)

            controller._note_editor_saved(_SavedEditor(), source="manual save")

            self.assertEqual(wrapper.refresh_calls, 1)


if __name__ == "__main__":
    unittest.main()
