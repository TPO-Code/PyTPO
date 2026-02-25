from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QPoint, QEvent
from PySide6.QtGui import QMouseEvent, QIcon, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractSpinBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    
)

from PySide6.QtWidgets import QTextEdit, QPushButton, QWidget, QPlainTextEdit, QToolButton, QSizePolicy, QVBoxLayout
EDGE_WIDTH = 8


def start_system_move(widget: QWidget, global_pos: Optional[QPoint] = None) -> None:
    move = getattr(widget, "startSystemMove", None)
    if callable(move):
        try:
            if global_pos is not None:
                move(global_pos)
            else:
                move()
            return
        except TypeError:
            move()
            return

    wh = widget.windowHandle()
    if wh is not None:
        try:
            if global_pos is not None:
                wh.startSystemMove(global_pos)
            else:
                wh.startSystemMove()
        except TypeError:
            wh.startSystemMove()


def start_system_resize(
        widget: QWidget,
        edges: Qt.Edges,
        global_pos: Optional[QPoint] = None,
) -> None:
    resize = getattr(widget, "startSystemResize", None)
    if callable(resize):
        try:
            if global_pos is not None:
                resize(edges, global_pos)
            else:
                resize(edges)
            return
        except TypeError:
            resize(edges)
            return

    wh = widget.windowHandle()
    if wh is not None:
        try:
            if global_pos is not None:
                wh.startSystemResize(edges, global_pos)
            else:
                wh.startSystemResize(edges)
        except TypeError:
            wh.startSystemResize(edges)


class EdgeGrip(QWidget):
    """Invisible grip that triggers native system resize for frameless windows."""

    def __init__(self, parent: QWidget, edges: Qt.Edges, cursor: Qt.CursorShape):
        super().__init__(parent)
        self.edges = edges
        self.setCursor(cursor)
        self.setStyleSheet("background: transparent;")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            gp = event.globalPosition().toPoint()
            start_system_resize(self.window(), self.edges, gp)
            event.accept()
            return
        event.ignore()


class CustomTitleBar(QFrame):
    """Custom title bar used only in frameless mode."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        allow_minimize: bool = True,
        allow_maximize: bool = True,
        allow_close: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(38)
        # Allow stylesheet background on the titlebar frame itself.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._allow_minimize = allow_minimize
        self._allow_maximize = allow_maximize
        self._allow_close = allow_close

        self._drag_start_global: Optional[QPoint] = None
        self._drag_started = False
        self._drag_threshold = QApplication.startDragDistance()
        self._drag_press_source: Optional[QWidget] = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.left_layout = QHBoxLayout()
        self.left_layout.setContentsMargins(8, 0, 4, 0)
        self.left_layout.setSpacing(4)

        self.center_layout = QHBoxLayout()
        self.center_layout.setContentsMargins(4, 0, 4, 0)
        self.center_layout.setSpacing(4)

        self.tools_layout = QHBoxLayout()
        self.tools_layout.setContentsMargins(4, 0, 4, 0)
        self.tools_layout.setSpacing(4)

        self.right_layout = QHBoxLayout()
        self.right_layout.setContentsMargins(4, 0, 6, 0)
        self.right_layout.setSpacing(2)

        left_wrap = QWidget(self)
        left_wrap.setLayout(self.left_layout)
        left_wrap.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        left_wrap.setObjectName("TitleBarLeftHost")
        left_wrap.setStyleSheet("background: transparent;")

        center_wrap = QWidget(self)
        center_wrap.setLayout(self.center_layout)
        center_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        center_wrap.setObjectName("TitleBarCenterHost")
        center_wrap.setStyleSheet("background: transparent;")

        tools_wrap = QWidget(self)
        tools_wrap.setLayout(self.tools_layout)
        tools_wrap.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        tools_wrap.setObjectName("TitleBarToolsHost")
        tools_wrap.setStyleSheet("background: transparent;")

        right_wrap = QWidget(self)
        right_wrap.setLayout(self.right_layout)
        right_wrap.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        right_wrap.setObjectName("TitleBarRightHost")
        right_wrap.setStyleSheet("background: transparent;")

        root.addWidget(left_wrap, 0)
        root.addWidget(center_wrap, 1)
        root.addWidget(tools_wrap, 0)
        root.addWidget(right_wrap, 0)

        # Track drag gestures across all interactive widgets in the titlebar.
        self._install_drag_event_filter(left_wrap)
        self._install_drag_event_filter(center_wrap)
        self._install_drag_event_filter(tools_wrap)
        self._install_drag_event_filter(right_wrap)

        self.title_label = QLabel("PyTPO", self)
        self.title_label.setObjectName("WindowTitleLabel")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        self.center_layout.addWidget(self.title_label)

        self.btn_min = QToolButton(self)
        self.btn_max = QToolButton(self)
        self.btn_close = QToolButton(self)

        self._setup_control_button(self.btn_min, "–", "Minimize", "min")
        self._setup_control_button(self.btn_max, "□", "Maximize", "max")
        self._setup_control_button(self.btn_close, "✕", "Close", "close")

        self.right_layout.addWidget(self.btn_min)
        self.right_layout.addWidget(self.btn_max)
        self.right_layout.addWidget(self.btn_close)

        self.btn_min.setVisible(self._allow_minimize)
        self.btn_max.setVisible(self._allow_maximize)
        self.btn_close.setVisible(self._allow_close)

        self.btn_min.clicked.connect(self._on_minimize_clicked)
        self.btn_max.clicked.connect(self._on_maximize_restore_clicked)
        self.btn_close.clicked.connect(self._on_close_clicked)

    # ---------- Public ----------
    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_maximized_state(self, is_maximized: bool) -> None:
        self.btn_max.setIcon(QIcon())
        self.btn_max.setText("❐" if is_maximized else "□")

    def add_left_widget(self, widget: QWidget) -> None:
        self.left_layout.addWidget(widget)
        self._install_drag_event_filter(widget)

    def add_center_widget(self, widget: QWidget) -> None:
        self.center_layout.insertWidget(max(0, self.center_layout.count() - 1), widget)
        self._install_drag_event_filter(widget)

    def add_right_widget(self, widget: QWidget) -> None:
        self.right_layout.insertWidget(max(0, self.right_layout.count() - 3), widget)
        self._install_drag_event_filter(widget)

    def add_tool_widget(self, widget: QWidget) -> None:
        self.tools_layout.addWidget(widget)
        self._install_drag_event_filter(widget)

    def set_title_visible(self, visible: bool) -> None:
        self.title_label.setVisible(bool(visible))

    # ---------- Internal ----------
    def _setup_control_button(self, btn: QToolButton, fallback_text: str, tooltip: str, role: str) -> None:
        btn.setObjectName("TitleBarControlButton")
        btn.setProperty("role", role)  # "min" | "max" | "close"
        btn.setFixedSize(34, 30)
        btn.setToolTip(tooltip)
        btn.setIcon(QIcon())
        btn.setText(fallback_text)

    def _on_minimize_clicked(self) -> None:
        if not self._allow_minimize:
            return
        win = self.window()
        if isinstance(win, QWidget):
            win.showMinimized()

    def _on_maximize_restore_clicked(self) -> None:
        if not self._allow_maximize:
            return
        win = self.window()
        if not isinstance(win, QWidget):
            return
        if win.isMaximized():
            win.showNormal()
            self.set_maximized_state(False)
        else:
            win.showMaximized()
            self.set_maximized_state(True)

    def _on_close_clicked(self) -> None:
        if not self._allow_close:
            return
        win = self.window()
        if isinstance(win, QWidget):
            win.close()

    def _install_drag_event_filter(self, widget: QWidget) -> None:
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def _reset_drag_tracking(self) -> None:
        self._drag_start_global = None
        self._drag_started = False
        self._drag_press_source = None

    def _begin_drag_tracking(self, global_pos: QPoint, source: Optional[QWidget]) -> None:
        self._drag_start_global = global_pos
        self._drag_started = False
        self._drag_press_source = source

    def _is_text_interaction_widget(self, widget: Optional[QWidget]) -> bool:
        current = widget
        while current is not None and current is not self:
            if isinstance(current, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                return True
            if isinstance(current, QComboBox) and current.isEditable():
                return True
            current = current.parentWidget()
        return False

    def _maybe_start_window_drag(self, event: QMouseEvent) -> bool:
        if self._drag_start_global is None:
            return False
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._reset_drag_tracking()
            return False
        if self._is_text_interaction_widget(self._drag_press_source):
            self._reset_drag_tracking()
            return False

        delta = event.globalPosition().toPoint() - self._drag_start_global
        if self._drag_started or delta.manhattanLength() < self._drag_threshold:
            return False

        self._drag_started = True
        source = self._drag_press_source
        if isinstance(source, QAbstractButton):
            source.setDown(False)
        start_system_move(self.window(), event.globalPosition().toPoint())
        return True

    def eventFilter(self, watched, event):
        if isinstance(watched, QWidget) and event.type() == QEvent.Type.ChildAdded:
            child = getattr(event, "child", lambda: None)()
            if isinstance(child, QWidget):
                self._install_drag_event_filter(child)
            return super().eventFilter(watched, event)

        if not isinstance(event, QMouseEvent) or not isinstance(watched, QWidget):
            return super().eventFilter(watched, event)

        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if watched is not self:
                if self._is_text_interaction_widget(watched):
                    self._reset_drag_tracking()
                else:
                    self._begin_drag_tracking(event.globalPosition().toPoint(), watched)
            return super().eventFilter(watched, event)

        if event_type == QEvent.Type.MouseMove and self._maybe_start_window_drag(event):
            event.accept()
            return True

        if event_type == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            started_drag = self._drag_started
            self._reset_drag_tracking()
            if started_drag and watched is not self:
                event.accept()
                return True

        return super().eventFilter(watched, event)

    def _is_in_drag_zone(self, pos_in_titlebar) -> bool:
        child = self.childAt(pos_in_titlebar)
        if child is None:
            return True
        if child is self or child is self.title_label:
            return True
        if isinstance(child, QWidget) and not isinstance(child, (QPushButton, QToolButton)):
            local = child.mapFrom(self, pos_in_titlebar)
            deep = child.childAt(local)
            if deep is None or deep is child:
                return True
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_in_drag_zone(event.position().toPoint()):
            self._begin_drag_tracking(event.globalPosition().toPoint(), self)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._maybe_start_window_drag(event):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._reset_drag_tracking()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if (
            self._allow_maximize
            and event.button() == Qt.MouseButton.LeftButton
            and self._is_in_drag_zone(event.position().toPoint())
        ):
            self._on_maximize_restore_clicked()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class Window(QMainWindow):
    """
    One reusable shell, two modes:
    - native chrome: OS titlebar + OS resize/snap
    - frameless: custom titlebar + edge grips
    """

    def __init__(self, *, use_native_chrome: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.use_native_chrome = use_native_chrome
        self._window_controls: list[QWidget] = []
        self._window_right_controls: list[QWidget] = []
        self._grips_built = False
        self._show_custom_title_text = True

        self._configure_window_flags()
        self._build_ui()

        if not self.use_native_chrome:
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
            self.setWindowFlags(Qt.WindowType.Window)
        else:
            self.setWindowFlags(
                Qt.WindowType.Window
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowSystemMenuHint
                | Qt.WindowType.WindowMinMaxButtonsHint
                | Qt.WindowType.WindowCloseButtonHint
            )

    def _build_ui(self) -> None:
        self.chrome_host = QWidget(self)
        self.chrome_host.setObjectName("WindowRoot")
        self._chrome_layout = QVBoxLayout(self.chrome_host)
        self._chrome_layout.setContentsMargins(0, 0, 0, 0)
        self._chrome_layout.setSpacing(0)

        self.title_bar: Optional[CustomTitleBar] = None
        if not self.use_native_chrome:
            self.title_bar = CustomTitleBar(self.chrome_host)
            self.title_bar.set_title_visible(self._show_custom_title_text)
            self._chrome_layout.addWidget(self.title_bar)

        self.top_strip = QWidget(self.chrome_host)
        self.top_strip.setObjectName("TopStrip")
        self.top_strip_layout = QHBoxLayout(self.top_strip)
        self.top_strip_layout.setContentsMargins(0, 0, 0, 0)
        self.top_strip_layout.setSpacing(0)
        self._chrome_layout.addWidget(self.top_strip)
        self.top_strip.setVisible(self.use_native_chrome)
        self.setMenuWidget(self.chrome_host)

        self.content_host = QWidget(self)
        self.content_host.setObjectName("ContentHost")
        self.content_layout = QVBoxLayout(self.content_host)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        self.setCentralWidget(self.content_host)

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

    def _layout_grips(self) -> None:
        if self.use_native_chrome or not self._grips_built:
            return

        if self.isMaximized() or self.isFullScreen():
            for g in self._all_grips():
                g.hide()
            return

        for g in self._all_grips():
            g.show()
            g.raise_()

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

    def _all_grips(self):
        return (
            self.n_grip, self.s_grip, self.w_grip, self.e_grip,
            self.nw_grip, self.ne_grip, self.sw_grip, self.se_grip,
        )

    def _left_control_target_layout(self):
        if self.use_native_chrome:
            return self.top_strip_layout
        if self.title_bar is not None:
            return self.title_bar.left_layout
        return self.top_strip_layout

    def _right_control_target_layout(self):
        if self.use_native_chrome:
            return self.top_strip_layout
        if self.title_bar is not None:
            return self.title_bar.tools_layout
        return self.top_strip_layout

    @staticmethod
    def _control_item_alignment() -> Qt.AlignmentFlag:
        return Qt.AlignmentFlag.AlignBottom

    def _add_control_to_layout(self, layout: QHBoxLayout, widget: QWidget) -> None:
        layout.addWidget(widget, 0, self._control_item_alignment())

    def _detach_widget_from_parent_layout(self, widget: QWidget) -> None:
        parent = widget.parentWidget()
        if parent is None:
            return
        lay = parent.layout()
        if lay is None:
            return
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item and item.widget() is widget:
                lay.takeAt(i)
                break
        widget.setParent(None)

    def _clear_layout_items(self, layout: QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _rebuild_window_controls(self) -> None:
        self._clear_layout_items(self.top_strip_layout)
        if self.title_bar is not None:
            self._clear_layout_items(self.title_bar.left_layout)
            self._clear_layout_items(self.title_bar.tools_layout)

        if self.use_native_chrome:
            for w in self._window_controls:
                w.setParent(None)
                self._add_control_to_layout(self.top_strip_layout, w)
            self.top_strip_layout.addStretch(1)
            for w in self._window_right_controls:
                w.setParent(None)
                self._add_control_to_layout(self.top_strip_layout, w)
            return

        left_target = self._left_control_target_layout()
        for w in self._window_controls:
            w.setParent(None)
            self._add_control_to_layout(left_target, w)

        right_target = self._right_control_target_layout()
        for w in self._window_right_controls:
            w.setParent(None)
            self._add_control_to_layout(right_target, w)

    def set_chrome_mode(self, use_native_chrome: bool) -> None:
        if self.use_native_chrome == use_native_chrome:
            return

        geom = self.geometry()
        was_visible = self.isVisible()
        was_max = self.isMaximized()
        was_full = self.isFullScreen()

        self.use_native_chrome = use_native_chrome

        if self.title_bar is not None:
            self._chrome_layout.removeWidget(self.title_bar)
            self.title_bar.deleteLater()
            self.title_bar = None

        if self._grips_built:
            for g in self._all_grips():
                g.deleteLater()
            self._grips_built = False

        self._configure_window_flags()
        self.setMenuWidget(self.chrome_host)

        if not self.use_native_chrome:
            self.title_bar = CustomTitleBar(self.chrome_host)
            self._chrome_layout.insertWidget(0, self.title_bar)
            self.title_bar.set_title(self.windowTitle())
            self.title_bar.set_title_visible(self._show_custom_title_text)
            self.title_bar.set_maximized_state(self.isMaximized())
            self._build_grips()

        self.top_strip.setVisible(self.use_native_chrome)
        self._rebuild_window_controls()

        if was_visible:
            self.hide()
        self.show()

        self.setGeometry(geom)
        if was_full:
            self.showFullScreen()
        elif was_max:
            self.showMaximized()
        else:
            self.showNormal()

        self._layout_grips()

    def add_window_control(self, widget: QWidget) -> None:
        self.add_window_left_control(widget)

    def add_window_left_control(self, widget: QWidget) -> None:
        if widget not in self._window_controls:
            self._window_controls.append(widget)
        self._apply_default_control_style(widget)
        self._rebuild_window_controls()

    def add_window_right_control(self, widget: QWidget) -> None:
        if widget not in self._window_right_controls:
            self._window_right_controls.append(widget)
        self._apply_default_control_style(widget)
        self._rebuild_window_controls()

    def clear_window_controls(self) -> None:
        for w in self._window_controls:
            self._detach_widget_from_parent_layout(w)
        self._window_controls.clear()
        for w in self._window_right_controls:
            self._detach_widget_from_parent_layout(w)
        self._window_right_controls.clear()

    # ---------- Public API ----------
    def set_window_title_text(self, text: str) -> None:
        self.setWindowTitle(text)

    def set_title_text_visible(self, visible: bool) -> None:
        self._show_custom_title_text = bool(visible)
        if self.title_bar is not None:
            self.title_bar.set_title_visible(self._show_custom_title_text)

    def add_to_top_strip(self, widget: QWidget, stretch: int = 0) -> None:
        self.top_strip_layout.addWidget(widget, stretch)

    def clear_top_strip(self) -> None:
        while self.top_strip_layout.count():
            item = self.top_strip_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def set_content_widget(self, widget: QWidget) -> None:
        self.clear_content()
        self.content_layout.addWidget(widget)

    def clear_content(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def enable_top_strip(self, enabled: bool) -> None:
        self.top_strip.setVisible(enabled)

    # ---------- Events ----------
    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        self._layout_grips()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            if self.title_bar is not None:
                self.title_bar.set_maximized_state(self.isMaximized())
            self._layout_grips()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    app = QApplication(sys.argv)

    # True  -> native OS titlebar
    # False -> custom frameless titlebar + grips
    win = Window(use_native_chrome=False)
    win.resize(1100, 720)
    win.setWindowTitle("Dream Editor Window")

    # Controls demo
    file_btn = QPushButton("File")
    edit_btn = QPushButton("Edit")
    run_btn = QPushButton("Run")
    badge = QPushButton("BG")
    badge.setFixedHeight(28)
    toggle = QPushButton("Toggle Chrome")
    toggle.clicked.connect(lambda: win.set_chrome_mode(not win.use_native_chrome))

    win.add_window_control(file_btn)
    win.add_window_control(edit_btn)
    win.add_window_control(run_btn)
    win.add_window_control(badge)
    win.add_window_control(toggle)

    editor = QTextEdit()
    editor.setPlaceholderText("Your editor widget goes here.")
    win.set_content_widget(editor)

    style_path = Path(__file__).resolve().parent / "styles" / "app.qss"
    try:
        app.setStyleSheet(style_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    win.show()
    sys.exit(app.exec())
