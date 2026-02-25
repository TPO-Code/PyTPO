from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMenuBar,
    QVBoxLayout,
    QWidget,
)

from src.ui.custom_window import EDGE_WIDTH, CustomTitleBar, EdgeGrip


class DialogWindow(QDialog):
    """
    Reusable dialog shell supporting native or frameless custom chrome.

    In frameless mode it provides:
    - custom titlebar
    - optional edge grips for native resize behavior
    """

    def __init__(
        self,
        *,
        use_native_chrome: bool = True,
        resizable: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.use_native_chrome = use_native_chrome
        self.resizable = resizable
        self._window_left_controls: list[QWidget] = []
        self._window_center_controls: list[QWidget] = []
        self._window_right_controls: list[QWidget] = []
        # Backwards-compatible alias for existing callers.
        self._window_controls = self._window_left_controls
        self._grips_built = False
        self._show_custom_title_text = True

        self._configure_window_flags()
        self._build_shell_ui()

        if not self.use_native_chrome and self.resizable:
            self._build_grips()

    @staticmethod
    def _apply_default_control_style(widget: QWidget) -> None:
        if not isinstance(widget, QMenuBar):
            return
        widget.setNativeMenuBar(False)
        if widget.styleSheet().strip():
            return
        widget.setStyleSheet(
            """
            QMenuBar {
                background: transparent;
                border: none;
            }
            QMenuBar::item {
                background: transparent;
            }
            """
        )

    def setWindowTitle(self, title: str) -> None:
        super().setWindowTitle(title)
        if self.title_bar is not None:
            self.title_bar.set_title(title)

    def _configure_window_flags(self) -> None:
        if self.use_native_chrome:
            self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
            return

        flags = (
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        if self.resizable:
            flags |= Qt.WindowType.WindowMinMaxButtonsHint
        self.setWindowFlags(flags)

    def _build_shell_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.chrome_host = QWidget(self)
        self.chrome_host.setObjectName("WindowRoot")
        self._chrome_layout = QVBoxLayout(self.chrome_host)
        self._chrome_layout.setContentsMargins(0, 0, 0, 0)
        self._chrome_layout.setSpacing(0)

        self.title_bar: Optional[CustomTitleBar] = None
        if not self.use_native_chrome:
            self.title_bar = CustomTitleBar(
                self.chrome_host,
                allow_minimize=False,
                allow_maximize=False,
                allow_close=True,
            )
            self.title_bar.set_title_visible(self._show_custom_title_text)
            self._chrome_layout.addWidget(self.title_bar)

        self.top_strip = QWidget(self.chrome_host)
        self.top_strip.setObjectName("TopStrip")
        self.top_strip_layout = QHBoxLayout(self.top_strip)
        self.top_strip_layout.setContentsMargins(0, 0, 0, 0)
        self.top_strip_layout.setSpacing(0)
        self.top_strip.setVisible(self.use_native_chrome)
        self._chrome_layout.addWidget(self.top_strip)

        root.addWidget(self.chrome_host, 0)

        self.content_host = QWidget(self)
        self.content_host.setObjectName("ContentHost")
        self.content_layout = QVBoxLayout(self.content_host)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        root.addWidget(self.content_host, 1)

    def _build_grips(self) -> None:
        W, H = Qt.CursorShape.SizeHorCursor, Qt.CursorShape.SizeVerCursor
        NW, NE = Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeBDiagCursor
        SW, SE = Qt.CursorShape.SizeBDiagCursor, Qt.CursorShape.SizeFDiagCursor

        self.n_grip = EdgeGrip(self, Qt.TopEdge, H)
        self.s_grip = EdgeGrip(self, Qt.BottomEdge, H)
        self.w_grip = EdgeGrip(self, Qt.LeftEdge, W)
        self.e_grip = EdgeGrip(self, Qt.RightEdge, W)
        self.nw_grip = EdgeGrip(self, Qt.TopEdge | Qt.LeftEdge, NW)
        self.ne_grip = EdgeGrip(self, Qt.TopEdge | Qt.RightEdge, NE)
        self.sw_grip = EdgeGrip(self, Qt.BottomEdge | Qt.LeftEdge, SW)
        self.se_grip = EdgeGrip(self, Qt.BottomEdge | Qt.RightEdge, SE)

        self._grips_built = True

    def _all_grips(self):
        return (
            self.n_grip,
            self.s_grip,
            self.w_grip,
            self.e_grip,
            self.nw_grip,
            self.ne_grip,
            self.sw_grip,
            self.se_grip,
        )

    def _layout_grips(self) -> None:
        if self.use_native_chrome or not self.resizable or not self._grips_built:
            return

        if self.isMaximized() or self.isFullScreen():
            for grip in self._all_grips():
                grip.hide()
            return

        for grip in self._all_grips():
            grip.show()
            grip.raise_()

        w, h = self.width(), self.height()
        ew = EDGE_WIDTH
        self.n_grip.setGeometry(ew, 0, w - 2 * ew, ew)
        self.s_grip.setGeometry(ew, h - ew, w - 2 * ew, ew)
        self.w_grip.setGeometry(0, ew, ew, h - 2 * ew)
        self.e_grip.setGeometry(w - ew, ew, ew, h - 2 * ew)
        self.nw_grip.setGeometry(0, 0, ew, ew)
        self.ne_grip.setGeometry(w - ew, 0, ew, ew)
        self.sw_grip.setGeometry(0, h - ew, ew, ew)
        self.se_grip.setGeometry(w - ew, h - ew, ew, ew)

    def _left_control_target_layout(self):
        if self.use_native_chrome:
            return self.top_strip_layout
        if self.title_bar is not None:
            return self.title_bar.left_layout
        return self.top_strip_layout

    def _center_control_target_layout(self):
        if self.use_native_chrome:
            return self.top_strip_layout
        if self.title_bar is not None:
            return self.title_bar.center_layout
        return self.top_strip_layout

    def _right_control_target_layout(self):
        if self.use_native_chrome:
            return self.top_strip_layout
        if self.title_bar is not None:
            return self.title_bar.tools_layout
        return self.top_strip_layout

    @staticmethod
    def _control_item_alignment() -> Qt.AlignmentFlag:
        return Qt.AlignmentFlag.AlignVCenter

    def _add_control_to_layout(self, layout: QHBoxLayout, widget: QWidget) -> None:
        if self.title_bar is not None and layout is self.title_bar.center_layout:
            layout.insertWidget(max(0, layout.count() - 1), widget, 0, self._control_item_alignment())
            return
        layout.addWidget(widget, 0, self._control_item_alignment())

    def _detach_widget_from_parent_layout(self, widget: QWidget) -> None:
        parent = widget.parentWidget()
        if parent is None:
            return
        layout = parent.layout()
        if layout is None:
            return
        for idx in range(layout.count()):
            item = layout.itemAt(idx)
            if item is not None and item.widget() is widget:
                layout.takeAt(idx)
                break
        widget.setParent(None)

    def _clear_layout_items(self, layout: QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)

    def _clear_title_bar_center_controls(self) -> None:
        if self.title_bar is None:
            return
        layout = self.title_bar.center_layout
        while layout.count() > 1:
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)

    def _rebuild_window_controls(self) -> None:
        self._clear_layout_items(self.top_strip_layout)
        if self.title_bar is not None:
            self._clear_layout_items(self.title_bar.left_layout)
            self._clear_layout_items(self.title_bar.tools_layout)
            self._clear_title_bar_center_controls()

        if self.use_native_chrome:
            for widget in self._window_left_controls:
                widget.setParent(None)
                self._add_control_to_layout(self.top_strip_layout, widget)
            self.top_strip_layout.addStretch(1)
            for widget in self._window_center_controls:
                widget.setParent(None)
                self._add_control_to_layout(self.top_strip_layout, widget)
            self.top_strip_layout.addStretch(1)
            for widget in self._window_right_controls:
                widget.setParent(None)
                self._add_control_to_layout(self.top_strip_layout, widget)
            return

        left_target = self._left_control_target_layout()
        for widget in self._window_left_controls:
            widget.setParent(None)
            self._add_control_to_layout(left_target, widget)

        center_target = self._center_control_target_layout()
        for widget in self._window_center_controls:
            widget.setParent(None)
            self._add_control_to_layout(center_target, widget)

        right_target = self._right_control_target_layout()
        for widget in self._window_right_controls:
            widget.setParent(None)
            self._add_control_to_layout(right_target, widget)

    def set_chrome_mode(self, use_native_chrome: bool) -> None:
        if self.use_native_chrome == use_native_chrome:
            return

        geom = self.geometry()
        visible = self.isVisible()

        self.use_native_chrome = use_native_chrome

        if self.title_bar is not None:
            self._chrome_layout.removeWidget(self.title_bar)
            self.title_bar.deleteLater()
            self.title_bar = None

        if self._grips_built:
            for grip in self._all_grips():
                grip.deleteLater()
            self._grips_built = False

        self._configure_window_flags()

        if not self.use_native_chrome:
            self.title_bar = CustomTitleBar(
                self.chrome_host,
                allow_minimize=False,
                allow_maximize=False,
                allow_close=True,
            )
            self._chrome_layout.insertWidget(0, self.title_bar)
            self.title_bar.set_title(self.windowTitle())
            self.title_bar.set_title_visible(self._show_custom_title_text)
            if self.resizable:
                self._build_grips()

        self.top_strip.setVisible(self.use_native_chrome)
        self._rebuild_window_controls()

        if visible:
            self.hide()
        self.show()
        self.setGeometry(geom)
        self._layout_grips()

    def add_window_control(self, widget: QWidget) -> None:
        self.add_window_left_control(widget)

    def add_window_left_control(self, widget: QWidget) -> None:
        if widget not in self._window_left_controls:
            self._window_left_controls.append(widget)
        self._apply_default_control_style(widget)
        self._rebuild_window_controls()

    def add_window_center_control(self, widget: QWidget) -> None:
        if widget not in self._window_center_controls:
            self._window_center_controls.append(widget)
        self._apply_default_control_style(widget)
        self._rebuild_window_controls()

    def add_window_right_control(self, widget: QWidget) -> None:
        if widget not in self._window_right_controls:
            self._window_right_controls.append(widget)
        self._apply_default_control_style(widget)
        self._rebuild_window_controls()

    def clear_window_controls(self) -> None:
        for widget in self._window_left_controls:
            self._detach_widget_from_parent_layout(widget)
        self._window_left_controls.clear()

        for widget in self._window_center_controls:
            self._detach_widget_from_parent_layout(widget)
        self._window_center_controls.clear()

        for widget in self._window_right_controls:
            self._detach_widget_from_parent_layout(widget)
        self._window_right_controls.clear()

    def set_window_title_text(self, text: str) -> None:
        self.setWindowTitle(text)

    def set_title_text_visible(self, visible: bool) -> None:
        self._show_custom_title_text = bool(visible)
        if self.title_bar is not None:
            self.title_bar.set_title_visible(self._show_custom_title_text)

    def set_content_widget(self, widget: QWidget) -> None:
        self.clear_content()
        self.content_layout.addWidget(widget)

    def clear_content(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._layout_grips()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            if self.title_bar is not None:
                self.title_bar.set_maximized_state(self.isMaximized())
            self._layout_grips()
