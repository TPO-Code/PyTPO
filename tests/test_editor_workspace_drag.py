from __future__ import annotations

import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, QMimeData
from PySide6.QtWidgets import QApplication

from pytpo.ui.editor_workspace import (
    MIME_EDITOR_TAB,
    DropZone,
    EditorWorkspace,
    _encode_editor_drag_payload,
)
from pytpo.ui.widgets.image_viewer import ImageViewerWidget


def _app() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


class _FakeDragEvent:
    def __init__(self, event_type: QEvent.Type, pos: QPoint, mime_data: QMimeData) -> None:
        self._event_type = event_type
        self._pos = QPointF(pos)
        self._mime_data = mime_data
        self.accepted = False

    def type(self) -> QEvent.Type:
        return self._event_type

    def position(self) -> QPointF:
        return QPointF(self._pos)

    def mimeData(self) -> QMimeData:
        return self._mime_data

    def accept(self) -> None:
        self.accepted = True

    def acceptProposedAction(self) -> None:
        self.accepted = True


class EditorWorkspaceDragTests(unittest.TestCase):
    def test_image_viewer_child_drag_forwards_to_split_overlay(self) -> None:
        app = _app()
        workspace = EditorWorkspace()
        self.addCleanup(workspace.close)
        workspace.resize(900, 600)
        workspace.show()

        tabs = workspace._ensure_one_main_tabs()
        viewer = ImageViewerWidget(parent=workspace)
        viewer.set_file_path("/tmp/example.png")
        tabs.add_editor(viewer)
        app.processEvents()

        viewport = viewer._view.viewport()
        self.assertGreater(viewport.width(), 0)
        self.assertGreater(viewport.height(), 0)

        mime = QMimeData()
        mime.setData(
            MIME_EDITOR_TAB,
            _encode_editor_drag_payload(str(viewer.editor_id), viewer.file_path),
        )
        event = _FakeDragEvent(
            QEvent.Type.DragMove,
            QPoint(max(1, viewport.width() - 2), max(1, viewport.height() // 2)),
            mime,
        )

        handled = tabs.eventFilter(viewport, event)

        self.assertTrue(handled)
        self.assertTrue(event.accepted)
        self.assertEqual(tabs._overlay.zone(), DropZone.RIGHT)


if __name__ == "__main__":
    unittest.main()
