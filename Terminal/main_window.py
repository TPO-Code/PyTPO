from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QMessageBox, QToolButton, QWidget

from TPOPyside.widgets import Window
from TPOPyside.widgets.split_tab_workspace import WorkspaceTabs
from TPOPyside.widgets.terminal_widget import TerminalWidget

from .settings import TerminalSettingsStore
from .settings_dialog import TerminalSettingsBackend, TerminalSettingsDialog
from .theme_manager import TerminalThemeManager
from .workspace import TerminalWorkspace

APP_NAME = "PyTPO Terminal"


class TerminalMainWindow(Window):
    def __init__(
        self,
        *,
        settings_store: TerminalSettingsStore | None = None,
        theme_manager: TerminalThemeManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=False, parent=parent)
        self._settings_store = settings_store or TerminalSettingsStore()
        self._theme_manager = theme_manager or TerminalThemeManager()
        self._settings = self._settings_store.load()
        self._shell_warning_shown = False
        self._startup_cwd_warning_shown = False

        self.workspace = TerminalWorkspace(self)
        self.workspace.newTabRequested.connect(self.open_new_tab_for_tabs)
        self.workspace.stateChanged.connect(self._update_window_title)
        self.set_content_widget(self.workspace)
        self._menu_button: QToolButton | None = None

        self._create_burger_menu()
        self.resize(1240, 820)

        self._apply_workspace_behavior_settings()
        self._apply_ansi_palette_from_settings()
        self._apply_theme_from_settings(show_errors=True)
        self._open_initial_tabs()
        self._apply_window_state_from_settings()
        self._update_window_title()

    def _create_burger_menu(self) -> None:
        new_tab_action = QAction("New Terminal Tab", self)
        new_tab_action.setShortcut(QKeySequence("Ctrl+T"))
        new_tab_action.triggered.connect(self.open_new_tab)

        close_tab_action = QAction("Close Terminal Tab", self)
        close_tab_action.setShortcut(QKeySequence("Ctrl+W"))
        close_tab_action.triggered.connect(self.close_current_tab)

        close_others_action = QAction("Close Other Tabs", self)
        close_others_action.setShortcut(QKeySequence("Ctrl+Alt+W"))
        close_others_action.triggered.connect(self.close_other_tabs)

        close_all_action = QAction("Close All Tabs", self)
        close_all_action.setShortcut(QKeySequence("Ctrl+Shift+W"))
        close_all_action.triggered.connect(self.close_all_tabs)

        settings_action = QAction("Settings...", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.open_settings_dialog)

        help_docs_action = QAction("Documentation...", self)
        help_docs_action.setShortcut(QKeySequence("F1"))
        help_docs_action.triggered.connect(self.open_documentation_dialog)

        next_tab_action = QAction("Next Tab", self)
        next_tab_action.setShortcut(QKeySequence("Ctrl+Tab"))
        next_tab_action.triggered.connect(self.workspace.next_tab)

        prev_tab_action = QAction("Previous Tab", self)
        prev_tab_action.setShortcut(QKeySequence("Ctrl+Shift+Tab"))
        prev_tab_action.triggered.connect(self.workspace.previous_tab)

        exit_action = QAction("Exit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)

        for action in (
            new_tab_action,
            close_tab_action,
            close_others_action,
            close_all_action,
            settings_action,
            help_docs_action,
            next_tab_action,
            prev_tab_action,
            exit_action,
        ):
            self.addAction(action)

        menu = QMenu(self)
        menu.addAction(new_tab_action)
        menu.addAction(close_tab_action)
        menu.addAction(close_others_action)
        menu.addAction(close_all_action)
        menu.addSeparator()
        menu.addAction(next_tab_action)
        menu.addAction(prev_tab_action)
        menu.addSeparator()
        menu.addAction(settings_action)
        help_menu = menu.addMenu("Help")
        help_menu.addAction(help_docs_action)
        menu.addSeparator()
        menu.addAction(exit_action)

        menu_button = QToolButton(self)
        menu_button.setObjectName("TerminalBurgerMenuButton")
        menu_button.setToolTip("Main Menu")
        menu_button.setAutoRaise(True)
        menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_button.setMenu(menu)
        menu_button.setStyleSheet(
            """
            QToolButton#TerminalBurgerMenuButton,
            QToolButton#TerminalBurgerMenuButton:hover,
            QToolButton#TerminalBurgerMenuButton:pressed {
                background: transparent;
                border: none;
                padding: 2px;
            }
            QToolButton#TerminalBurgerMenuButton::menu-indicator {
                image: none;
                width: 0px;
            }
            """
        )
        menu_button.setText("")
        self._menu_button = menu_button
        self._refresh_burger_menu_icon()
        self.add_window_control(menu_button)

    def _refresh_burger_menu_icon(self) -> None:
        if self._menu_button is None:
            return
        self._menu_button.setIcon(self._build_burger_icon(size=16))
        self._menu_button.setIconSize(QSize(16, 16))

    def _build_burger_icon(self, *, size: int) -> QIcon:
        dpr = max(1.0, float(self.devicePixelRatioF()))
        pixmap = QPixmap(int(size * dpr), int(size * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)

        color = self.palette().color(self.foregroundRole())
        if not color.isValid():
            color = self.palette().buttonText().color()
        if not color.isValid():
            color = QColor("#d0d0d0")
        color.setAlpha(255)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color)
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        for y in (4, 8, 12):
            painter.drawLine(3, y, size - 3, y)
        painter.end()
        return QIcon(pixmap)

    def _resolve_shell_path(self, preferred_mode: str, custom_shell_path: str) -> str:
        preferred = str(preferred_mode or "").strip().lower()
        if preferred == "custom":
            custom = str(custom_shell_path or "").strip()
            if custom:
                expanded = str(Path(custom).expanduser())
                if Path(expanded).is_file() and os.access(expanded, os.X_OK):
                    return expanded
        elif preferred == "auto":
            # Prefer SHELL when available, falling back to explicit modes below.
            env_shell = str(os.environ.get("SHELL") or "").strip()
            if " " in env_shell:
                env_shell = env_shell.split(" ", 1)[0].strip()
            if env_shell:
                expanded = str(Path(env_shell).expanduser())
                if Path(expanded).is_file() and os.access(expanded, os.X_OK):
                    return expanded
                env_named = shutil.which(Path(expanded).name or env_shell)
                if env_named:
                    return env_named
        elif preferred in {"bash", "zsh", "sh"}:
            candidate = shutil.which(preferred)
            if candidate:
                return candidate

        for fallback in ("bash", "zsh", "sh"):
            candidate = shutil.which(fallback)
            if candidate:
                if not self._shell_warning_shown:
                    QMessageBox.warning(
                        self,
                        "Shell Fallback",
                        f"Requested shell '{preferred or preferred_mode}' is unavailable. Using '{fallback}'.",
                    )
                    self._shell_warning_shown = True
                return candidate

        if not self._shell_warning_shown:
            QMessageBox.warning(
                self,
                "Shell Fallback",
                "No standard shell executable was found in PATH. Falling back to /bin/sh.",
            )
            self._shell_warning_shown = True
        return "/bin/sh"

    def _default_cwd_for_new_tab(self) -> str:
        configured = str(self._settings.startup_cwd or "").strip()
        if configured:
            resolved = Path(configured).expanduser()
            if resolved.is_dir():
                return str(resolved)
            if not self._startup_cwd_warning_shown:
                QMessageBox.warning(
                    self,
                    "Startup Directory",
                    f"Configured startup directory does not exist:\n{resolved}\n\nUsing home directory instead.",
                )
                self._startup_cwd_warning_shown = True
        home = Path.home()
        if home.is_dir():
            return str(home)
        return str(Path.cwd())

    def _apply_window_state_from_settings(self) -> None:
        if self._settings.start_fullscreen:
            self.showFullScreen()
            return
        if self._settings.start_maximized:
            self.showMaximized()
            return

    def _apply_workspace_behavior_settings(self) -> None:
        self.workspace.set_confirm_close_running(bool(self._settings.confirm_close_running))

    def _apply_ansi_palette_from_settings(self) -> None:
        TerminalWidget.reset_ansi_colors()
        for name, value in dict(self._settings.ansi_colors or {}).items():
            try:
                TerminalWidget.set_ansi_color(str(name), value)
            except Exception:
                continue

    def _open_initial_tabs(self) -> None:
        count = max(1, int(self._settings.startup_tabs))
        for _ in range(count):
            self.open_new_tab()

    def open_new_tab(self) -> None:
        self._open_new_tab(None)

    def open_new_tab_for_tabs(self, tabs_obj: object | None) -> None:
        target_tabs = tabs_obj if isinstance(tabs_obj, WorkspaceTabs) else None
        self._open_new_tab(target_tabs)

    def _open_new_tab(self, target_tabs: WorkspaceTabs | None) -> None:
        shell_path = self._resolve_shell_path(
            self._settings.default_shell_mode,
            self._settings.custom_shell_path,
        )
        session = self.workspace.create_session(
            shell_path=shell_path,
            login_shell=bool(self._settings.shell_login),
            history_lines=int(self._settings.history_lines),
            show_toolbar=bool(self._settings.show_toolbar),
            cwd=self._default_cwd_for_new_tab(),
            target_tabs=target_tabs,
        )
        session.apply_settings(self._settings)
        self._update_window_title()

    def close_current_tab(self) -> None:
        self.workspace.close_current_editor(self)
        self._update_window_title()

    def close_other_tabs(self) -> None:
        current = self.workspace.current_session()
        if current is None:
            return
        self.workspace.close_other_sessions(current, self)
        self._update_window_title()

    def close_all_tabs(self) -> None:
        self.workspace.request_close_all(self)
        self._update_window_title()

    def open_settings_dialog(self) -> None:
        backend = TerminalSettingsBackend(self._settings_store)
        dialog = TerminalSettingsDialog(
            backend=backend,
            theme_manager=self._theme_manager,
            on_applied=self._on_settings_applied,
            parent=self,
        )
        dialog.exec()

    def _terminal_docs_root(self) -> Path:
        return (Path(__file__).resolve().parent / "docs").resolve()

    def open_documentation_dialog(self) -> None:
        docs_root = self._terminal_docs_root()
        if not docs_root.is_dir():
            QMessageBox.information(
                self,
                "Documentation",
                f"No docs folder was found at:\n{docs_root}",
            )
            return
        try:
            from TPOPyside.widgets.doc_viewer import DocumentationViewerDialog
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Documentation",
                f"Could not open documentation viewer:\n{exc}",
            )
            return

        dialog = DocumentationViewerDialog(str(docs_root), parent=self)
        dialog.exec()

    def _on_settings_applied(self) -> None:
        self._settings = self._settings_store.load()
        self._apply_workspace_behavior_settings()
        self._apply_ansi_palette_from_settings()
        self._apply_theme_from_settings(show_errors=True)
        self._apply_window_state_from_settings()
        self._apply_settings_to_all_sessions()

    def _apply_settings_to_all_sessions(self) -> None:
        for session in self.workspace.all_sessions():
            session.apply_settings(self._settings)

    def _apply_theme_from_settings(self, *, show_errors: bool) -> None:
        result = self._theme_manager.apply_theme(self._settings.theme_name)
        self._refresh_burger_menu_icon()
        if result.applied_name != self._settings.theme_name:
            self._settings.theme_name = result.applied_name
            self._settings_store.save(self._settings)
        if show_errors and result.error:
            QMessageBox.warning(self, "Theme Apply Failed", result.error)

    def _update_window_title(self) -> None:
        session = self.workspace.current_session()
        if session is None:
            self.setWindowTitle(APP_NAME)
            return
        self.setWindowTitle(f"{session.tab_title()} - {APP_NAME}")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.workspace.request_close_all(self):
            event.accept()
            return
        event.ignore()
