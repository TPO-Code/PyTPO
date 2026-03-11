from __future__ import annotations

import os
import uuid
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QWidget

from TPOPyside.widgets.terminal_widget import TerminalWidget

from .settings import TerminalSettings


def _terminal_image_mode(size_mode: str) -> str:
    mapping = {
        "tile": "tile",
        "fit width": "fit_width",
        "fit height": "fit_height",
        "fit": "fit",
        "stretch": "stretch",
        "contain": "contain",
        "center": "center",
    }
    return mapping.get(str(size_mode or "").strip().lower(), "fit")


class TerminalSessionWidget(TerminalWidget):
    activated = Signal(object)
    titleChanged = Signal(object)

    def __init__(
        self,
        *,
        title: str,
        shell_path: str,
        login_shell: bool = False,
        history_lines: int = 5000,
        show_toolbar: bool = True,
        env_overrides: dict[str, str] | None = None,
        cwd: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            shell=shell_path,
            login=bool(login_shell),
            cwd=cwd,
            env=dict(env_overrides or {}),
            parent=parent,
            show_toolbar=True,
            history_lines=int(history_lines),
        )
        self.editor_id = uuid.uuid4().hex
        self._base_title = str(title or "Terminal").strip() or "Terminal"
        self._exit_code: int | None = None
        self.shellExited.connect(self._on_shell_exited)
        toolbar = getattr(self, "_toolbar", None)
        if toolbar is not None:
            toolbar.setVisible(bool(show_toolbar))

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.activated.emit(self)
        super().focusInEvent(event)

    def _on_shell_exited(self, code: int) -> None:
        self._exit_code = int(code)
        self.titleChanged.emit(self)

    def is_running(self) -> bool:
        return self._exit_code is None

    def has_active_command(self) -> bool:
        """
        Best-effort check for an active command/process in this terminal.
        Returns False when shell is idle at prompt.
        """
        if not self.is_running():
            return False

        shell_pid = int(self.process_id())
        if shell_pid <= 0:
            return False

        shell_pgid = self._safe_getpgid(shell_pid)
        fg_pgid = self.foreground_process_group()
        if shell_pgid is not None and fg_pgid is not None and int(fg_pgid) != int(shell_pgid):
            return True

        # Linux fallback: any live child process indicates activity (foreground/background job).
        for pid in self._linux_child_pids(shell_pid):
            if pid != shell_pid and self._is_live_non_zombie_process(pid):
                return True
        return False

    @staticmethod
    def _safe_getpgid(pid: int) -> int | None:
        try:
            pgid = os.getpgid(int(pid))
        except Exception:
            return None
        return int(pgid) if pgid > 0 else None

    @staticmethod
    def _linux_child_pids(parent_pid: int) -> list[int]:
        children_file = Path(f"/proc/{int(parent_pid)}/task/{int(parent_pid)}/children")
        if not children_file.exists():
            return []
        try:
            raw = children_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []
        out: list[int] = []
        for token in raw.split():
            try:
                pid = int(token)
            except Exception:
                continue
            if pid > 0:
                out.append(pid)
        return out

    @staticmethod
    def _is_live_non_zombie_process(pid: int) -> bool:
        stat_path = Path(f"/proc/{int(pid)}/stat")
        if not stat_path.exists():
            return False
        try:
            stat_text = stat_path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return False
        parts = stat_text.split()
        if len(parts) < 3:
            return False
        return parts[2] != "Z"

    def set_session_title(self, title: str) -> None:
        cleaned = str(title or "").strip()
        if cleaned:
            self._base_title = cleaned
            self.titleChanged.emit(self)

    def display_name(self) -> str:
        if self._exit_code is None:
            return self._base_title
        return f"{self._base_title} (exited)"

    def tab_title(self) -> str:
        return self.display_name()

    def apply_settings(self, settings: TerminalSettings) -> None:
        font = QFont(self.font())
        family = str(settings.font_family or "").strip()
        if family:
            font.setFamily(family)
        font.setPointSize(int(settings.font_size))
        self.setFont(font)

        fg = QColor(str(settings.foreground_color or "").strip())
        bg = QColor(str(settings.background_color or "").strip())
        if fg.isValid() and bg.isValid():
            pal = QPalette(self.palette())
            pal.setColor(QPalette.Window, bg)
            pal.setColor(QPalette.Base, bg)
            pal.setColor(QPalette.Button, bg)
            pal.setColor(QPalette.WindowText, fg)
            pal.setColor(QPalette.Text, fg)
            pal.setColor(QPalette.ButtonText, fg)
            sel_bg = QColor(str(settings.selection_background_color or "").strip())
            sel_fg = QColor(str(settings.selection_foreground_color or "").strip())
            if sel_bg.isValid():
                pal.setColor(QPalette.Highlight, sel_bg)
            if sel_fg.isValid():
                pal.setColor(QPalette.HighlightedText, sel_fg)
            self.setPalette(pal)

            cursor = QColor(str(settings.cursor_color or "").strip())
            if not cursor.isValid():
                cursor = QColor(fg)
            self.cursorColor = cursor

            link = QColor(str(settings.link_color or "").strip())
            if not link.isValid():
                link = QColor(fg).lighter(135)
            self.linkColor = link

        tint = QColor(str(settings.background_tint_color or "").strip())
        if tint.isValid():
            self.set_background_tint(tint)

        self.set_background_tint_strength(float(int(settings.background_tint_strength)) / 100.0)
        self.set_background_image_alpha_mode(settings.background_alpha_mode)

        image_path = str(settings.background_image_path or "").strip()
        if image_path:
            loaded = self.set_background_image(
                image_path,
                mode=_terminal_image_mode(settings.background_size_mode),
            )
            if not loaded:
                self.clear_background_image()
        else:
            self.clear_background_image()

        toolbar = getattr(self, "_toolbar", None)
        if toolbar is not None:
            toolbar.setVisible(bool(settings.show_toolbar))

        try:
            self.set_commands(
                quick_commands=list(settings.quick_commands or []),
                templates=list(settings.command_templates or []),
            )
        except Exception:
            pass

        self.update()
