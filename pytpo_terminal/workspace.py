from __future__ import annotations

from typing import cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMenu, QMessageBox, QToolButton, QWidget

from TPOPyside.widgets.split_tab_workspace import SplitterTabWorkspace, WorkspaceTabs

from .session import TerminalSessionWidget


class TerminalWorkspaceTabs(WorkspaceTabs):
    def __init__(self, workspace: "TerminalWorkspace", parent: QWidget | None = None) -> None:
        super().__init__(workspace, parent)
        self._workspace = workspace

        tab_bar = self.tabBar()
        tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._show_tab_context_menu)

        self._new_tab_button = QToolButton(self)
        self._new_tab_button.setText("+")
        self._new_tab_button.setToolTip("New Terminal Tab")
        self._new_tab_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_tab_button.clicked.connect(lambda _checked=False: self._workspace.request_new_tab(self))
        self.setCornerWidget(self._new_tab_button, Qt.Corner.TopRightCorner)

    def _show_tab_context_menu(self, position) -> None:
        tab_bar = self.tabBar()
        index = tab_bar.tabAt(position)
        if index < 0:
            return
        editor = self.widget(index)
        if not isinstance(editor, TerminalSessionWidget):
            return

        menu = QMenu(self)
        close_action = menu.addAction("Close")
        close_others_action = menu.addAction("Close Others")
        close_all_action = menu.addAction("Close All")

        sessions = self._workspace.all_sessions()
        close_others_action.setEnabled(len(sessions) > 1)
        close_all_action.setEnabled(len(sessions) > 0)

        chosen = menu.exec(tab_bar.mapToGlobal(position))
        if chosen is None:
            return

        parent_widget = cast(QWidget | None, self.window())
        if chosen is close_action:
            self._workspace.close_editor(editor, parent_widget)
            return
        if chosen is close_others_action:
            self._workspace.close_other_sessions(editor, parent_widget)
            return
        if chosen is close_all_action:
            self._workspace.request_close_all(parent_widget)


class TerminalWorkspace(SplitterTabWorkspace):
    newTabRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session_counter = 0
        self._confirm_close_running = True

    def create_tabs(self, parent: QWidget | None = None) -> WorkspaceTabs:
        return TerminalWorkspaceTabs(self, parent)

    def is_editor_widget(self, widget: object) -> bool:
        return isinstance(widget, TerminalSessionWidget)

    def request_new_tab(self, target_tabs: WorkspaceTabs | None = None) -> None:
        self.newTabRequested.emit(target_tabs)

    def all_sessions(self) -> list[TerminalSessionWidget]:
        return [session for session in super().all_editors() if isinstance(session, TerminalSessionWidget)]

    def current_session(self) -> TerminalSessionWidget | None:
        session = super().current_editor()
        return session if isinstance(session, TerminalSessionWidget) else None

    def create_session(
        self,
        *,
        shell_path: str,
        login_shell: bool = False,
        history_lines: int = 5000,
        show_toolbar: bool = True,
        cwd: str | None = None,
        title: str | None = None,
        target_tabs: WorkspaceTabs | None = None,
    ) -> TerminalSessionWidget:
        self._session_counter += 1
        resolved_title = str(title or "").strip() or f"Terminal {self._session_counter}"
        session = TerminalSessionWidget(
            title=resolved_title,
            shell_path=shell_path,
            login_shell=bool(login_shell),
            history_lines=int(history_lines),
            show_toolbar=bool(show_toolbar),
            cwd=cwd,
        )
        self.add_editor(session, tabs=target_tabs)
        return session

    def set_confirm_close_running(self, enabled: bool) -> None:
        self._confirm_close_running = bool(enabled)

    def confirm_close_editor(self, editor: QWidget, parent: QWidget | None = None) -> bool:
        if not isinstance(editor, TerminalSessionWidget):
            return True
        if not self._confirm_close_running:
            return True
        if not editor.has_active_command():
            return True

        response = QMessageBox.question(
            parent or self,
            "Close Terminal",
            f"A command appears to be running in '{editor.display_name()}'. Close it anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return response == QMessageBox.StandardButton.Yes

    def next_tab(self) -> None:
        tabs = self._current_tabs()
        count = tabs.count()
        if count <= 1:
            return
        tabs.setCurrentIndex((tabs.currentIndex() + 1) % count)

    def previous_tab(self) -> None:
        tabs = self._current_tabs()
        count = tabs.count()
        if count <= 1:
            return
        tabs.setCurrentIndex((tabs.currentIndex() - 1) % count)

    def close_other_sessions(self, keep: TerminalSessionWidget, parent: QWidget | None = None) -> bool:
        for session in list(self.all_sessions()):
            if session is keep:
                continue
            if not self.close_editor(session, parent):
                return False
        tabs = self._tabs_for_editor(keep)
        if tabs is not None:
            tabs.setCurrentWidget(keep)
            self.set_active_editor(keep)
            keep.setFocus()
        return True
