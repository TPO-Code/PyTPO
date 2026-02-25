# tpops/widgets/tpo_terminal_widget.py
import os
import sys
import pty
import fcntl
import termios
import struct
import signal
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

import pyte
from pyte.screens import HistoryScreen
from pyte import modes

# ============================ Colors & Utilities ============================

class AnsiColor(Enum):
    BLACK = "black"
    RED = "red"
    GREEN = "green"
    BROWN = "brown"
    BLUE = "blue"
    MAGENTA = "magenta"
    CYAN = "cyan"
    WHITE = "white"
    BRIGHT_BLACK = "brightblack"
    BRIGHT_RED = "brightred"
    BRIGHT_GREEN = "brightgreen"
    BRIGHT_BROWN = "brightbrown"
    BRIGHT_BLUE = "brightblue"
    BRIGHT_MAGENTA = "brightmagenta"
    BRIGHT_CYAN = "brightcyan"
    BRIGHT_WHITE = "brightwhite"


ANSI16_DEFAULTS = {
    AnsiColor.BLACK: (0, 0, 0),
    AnsiColor.RED: (205, 0, 0),
    AnsiColor.GREEN: (0, 205, 0),
    AnsiColor.BROWN: (205, 205, 0),
    AnsiColor.BLUE: (0, 0, 238),
    AnsiColor.MAGENTA: (205, 0, 205),
    AnsiColor.CYAN: (0, 205, 205),
    AnsiColor.WHITE: (229, 229, 229),
    AnsiColor.BRIGHT_BLACK: (127, 127, 127),
    AnsiColor.BRIGHT_RED: (255, 0, 0),
    AnsiColor.BRIGHT_GREEN: (0, 255, 0),
    AnsiColor.BRIGHT_BROWN: (255, 255, 0),
    AnsiColor.BRIGHT_BLUE: (92, 92, 255),
    AnsiColor.BRIGHT_MAGENTA: (255, 0, 255),
    AnsiColor.BRIGHT_CYAN: (0, 255, 255),
    AnsiColor.BRIGHT_WHITE: (255, 255, 255),
}

ANSI16: Dict[str, tuple[int, int, int]] = {color.value: rgb for color, rgb in ANSI16_DEFAULTS.items()}
_ANSI16_ORDER = [color.value for color in AnsiColor]
_ANSI16_INDICES = {name: idx for idx, name in enumerate(_ANSI16_ORDER)}


def qrgb(t): return QtGui.QColor(*t)

def build_xterm256() -> List[QtGui.QColor]:
    pals = [QtGui.QColor(0, 0, 0) for _ in range(16)]
    steps = [0, 95, 135, 175, 215, 255]
    for r in steps:
        for g in steps:
            for b in steps:
                pals.append(QtGui.QColor(r, g, b))
    for i in range(24):
        v = 8 + i * 10
        pals.append(QtGui.QColor(v, v, v))
    for i, name in enumerate(_ANSI16_ORDER):
        pals[i] = qrgb(ANSI16[name])
    return pals

XTERM256 = build_xterm256()


def _normalize_ansi_name(color: AnsiColor | str) -> str:
    if isinstance(color, AnsiColor):
        return color.value
    if isinstance(color, str):
        key = color.strip().lower()
        if key in ANSI16:
            return key
    raise KeyError(f"Unknown ANSI color: {color!r}")


def _coerce_rgb(value) -> tuple[int, int, int]:
    if isinstance(value, QtGui.QColor):
        color = value
    elif isinstance(value, str):
        color = QtGui.QColor(value)
        if not color.isValid():
            raise ValueError(f"Invalid color string: {value!r}")
    elif isinstance(value, (tuple, list)):
        if len(value) != 3:
            raise ValueError("RGB sequences must have exactly three components")
        try:
            r, g, b = (int(v) for v in value)
        except (TypeError, ValueError) as exc:
            raise ValueError("RGB components must be integers") from exc
        for channel in (r, g, b):
            if not 0 <= channel <= 255:
                raise ValueError("RGB components must be in the range 0-255")
        return r, g, b
    else:
        raise TypeError("Color value must be a QColor, hex string, or an RGB tuple/list")
    return color.red(), color.green(), color.blue()


def _apply_ansi_rgb(name: str, rgb: tuple[int, int, int]) -> None:
    ANSI16[name] = rgb
    idx = _ANSI16_INDICES.get(name)
    if idx is not None:
        XTERM256[idx] = QtGui.QColor(*rgb)


def override_ansi_color(color: AnsiColor | str, value) -> None:
    """Override a global ANSI color entry used by all terminal widgets."""
    name = _normalize_ansi_name(color)
    rgb = _coerce_rgb(value)
    _apply_ansi_rgb(name, rgb)


def reset_ansi_palette() -> None:
    """Restore the ANSI color mapping to its default values."""
    ANSI16.clear()
    ANSI16.update({color.value: rgb for color, rgb in ANSI16_DEFAULTS.items()})
    refreshed = build_xterm256()
    XTERM256[:] = refreshed


def qcolor_from_pyte(color, default: QtGui.QColor) -> QtGui.QColor:
    if color is None:
        return default
    if isinstance(color, int):
        return XTERM256[color] if 0 <= color < 256 else default
    if isinstance(color, str):
        if color.startswith("#") and (len(color) in (7, 4)):
            c = QtGui.QColor(color)
            return c if c.isValid() else default
        rgb = ANSI16.get(color)
        if rgb:
            return qrgb(rgb)
    return default


# ============================ Command Specs ============================

@dataclass
class CommandSpec:
    label: str
    cmd: str
    params: List[str] = field(default_factory=list)   # optional parameter names for a prompt
    cwd: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    dryrun: bool = False


# ============================ Buffer Dialog ============================

class _BufferDialog(QtWidgets.QDialog):
    def __init__(self, parent, init_cols: int, init_rows: int):
        super().__init__(parent)
        self.setWindowTitle("Virtual Buffer Dimensions")
        self.setModal(True)

        lab_cols = QtWidgets.QLabel("Columns:")
        lab_rows = QtWidgets.QLabel("Rows:")

        self.spin_cols = QtWidgets.QSpinBox()
        self.spin_cols.setRange(40, 1000)
        self.spin_cols.setValue(int(init_cols))
        self.spin_cols.setAccelerated(True)

        self.spin_rows = QtWidgets.QSpinBox()
        self.spin_rows.setRange(50, 5000)
        self.spin_rows.setValue(int(init_rows))
        self.spin_rows.setAccelerated(True)

        form = QtWidgets.QFormLayout()
        form.addRow(lab_cols, self.spin_cols)
        form.addRow(lab_rows, self.spin_rows)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addLayout(form)
        lay.addWidget(btns)

    def values(self) -> tuple[int, int]:
        return int(self.spin_cols.value()), int(self.spin_rows.value())


# ============================ Terminal Widget ============================

class TerminalWidget(QtWidgets.QWidget):
    """
    Terminal widget with a fixed-size VT emulator, soft-wrapped viewport, QSS theming,
    and a one-click **Run** menu for quick commands and parameterized templates.
    """
    bell = QtCore.Signal()
    outputReceived = QtCore.Signal(bytes)
    shellExited = QtCore.Signal(int)
    tracebackLinkActivated = QtCore.Signal(str, int, int)  # file_path, line, column

    # Expose ANSI palette handles
    AnsiColor = AnsiColor

    # Track xterm mouse + bracketed paste toggles
    _RE_MOUSE_ENABLE = re.compile(rb"\x1b\[\?(?P<mode>1000|1002|1003|1006)h")
    _RE_MOUSE_DISABLE = re.compile(rb"\x1b\[\?(?P<mode>1000|1002|1003|1006)l")
    _RE_BRACKET_PASTE_ENABLE = re.compile(rb"\x1b\[\?2004h")
    _RE_BRACKET_PASTE_DISABLE = re.compile(rb"\x1b\[\?2004l")
    _RE_PY_TRACEBACK = re.compile(r'File "([^"]+)", line (\d+)(?:, in .*)?$')
    _RE_CXX_DIAG = re.compile(
        r"^\s*(?P<path>[^:\n][^:\n]*):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?:fatal\s+error|error)\b",
        re.IGNORECASE,
    )

    @staticmethod
    def set_ansi_color(color: AnsiColor | str, value) -> None:
        """Override a single ANSI color entry for all terminal widgets."""
        override_ansi_color(color, value)

    @staticmethod
    def reset_ansi_colors() -> None:
        """Reset the ANSI palette overrides back to the library defaults."""
        reset_ansi_palette()

    # ---- QSS property: cursorColor ----
    cursorColorChanged = QtCore.Signal()
    linkColorChanged = QtCore.Signal()
    @QtCore.Property(QtGui.QColor, notify=cursorColorChanged)
    def cursorColor(self) -> QtGui.QColor:
        return self._cursor_color
    @cursorColor.setter
    def cursorColor(self, c: QtGui.QColor):
        if isinstance(c, QtGui.QColor) and c.isValid():
            self._cursor_color = QtGui.QColor(c)
            self.cursorColorChanged.emit()
            self.update()

    @QtCore.Property(QtGui.QColor, notify=linkColorChanged)
    def linkColor(self) -> QtGui.QColor:
        return self._link_color

    @linkColor.setter
    def linkColor(self, c: QtGui.QColor):
        if isinstance(c, QtGui.QColor) and c.isValid():
            self._link_color = QtGui.QColor(c)
            self.linkColorChanged.emit()
            self.update()

    # -------- Init --------
    def __init__(
            self,
            shell="/bin/bash",
            cwd=None,
            env=None,
            parent=None,
            history_lines=5000,
            quick_commands: Optional[List[Dict]] = None,
            templates: Optional[List[Dict]] = None,
    ):
        super().__init__(parent)
        self.setObjectName("TerminalWidget")
        self.setProperty("surface", "terminal")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_StyledBackground, True)  # allow QSS to paint background

        # QSS/Palette-driven visuals
        self._cursor_color: QtGui.QColor = None  # set by _install_palette_defaults
        self._link_color: QtGui.QColor = None    # set by _install_palette_defaults
        self._install_palette_defaults()

        # Font metrics from current widget font (QSS friendly)
        self._cell_w = self._cell_h = self._baseline = 0
        self._recompute_metrics()

        # App state
        self._closing = False
        self._shell_exit_emitted = False
        self._history_limit = int(history_lines)

        # Fixed emulator geometry (virtual)
        self._virt_cols = 200
        self._virt_rows = 200

        # PTY process
        pid, fd = pty.fork()
        if pid == 0:
            if cwd:
                os.chdir(cwd)
            env2 = os.environ.copy()
            env2["TERM"] = "xterm-256color"
            if env:
                env2.update(env)
            os.execvpe(shell, [shell, "-l"], env2)
        self._pid, self._fd = pid, fd

        # Nonblocking reads
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

        # Emulator + stream
        self._screen = HistoryScreen(self._virt_cols, self._virt_rows, history=self._history_limit)
        self._screen.set_mode(modes.DECAWM)  # wraparound while printing
        self._stream = pyte.ByteStream(self._screen)

        # Inform child once (we don't chase real window resizes)
        self._set_winsize(self._virt_rows, self._virt_cols)

        # Async notifier
        self._notifier = QtCore.QSocketNotifier(fd, QtCore.QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._read_ready)

        # Viewport scrollback offset (0 = follow bottom)
        self._view_offset = 0

        # Cursor blink
        self._cursor_visible = True
        self._blink = QtCore.QTimer(self)
        self._blink.timeout.connect(self._toggle_cursor)
        self._blink.start(600)

        # Shortcuts
        self._paste_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+V"), self)
        self._paste_shortcut.activated.connect(self._paste_bracketed)

        # Mouse / bracketed paste modes
        self.setMouseTracking(True)
        self._mouse_btns = 0
        self._mouse_mode_btn = False   # 1000/1002/1003 (button/motion)
        self._mouse_mode_any = False   # 1003 any-motion
        self._mouse_mode_sgr = False   # 1006 SGR encoding
        self._bracket_paste_enabled = False  # xterm 2004 mode

        # Selection
        self._sel_start = None  # (col, row) viewport coords
        self._sel_end = None
        self._selecting = False

        # Commands model (quick + templates)
        self._quick_cmds: List[CommandSpec] = self._coerce_command_specs(quick_commands or [])
        self._templates: List[CommandSpec] = self._coerce_command_specs(templates or [])
        self._last_used: Optional[CommandSpec] = None
        self._edit_commands_callback: Optional[Callable[[], None]] = None

        # UI: toolbar + context menu + Run button
        self._build_toolbar()
        self._build_run_button()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    # -------- Palette & Metrics (QSS-friendly) --------
    def _install_palette_defaults(self):
        pal = self.palette()
        self._bg_default = pal.color(QtGui.QPalette.Window)
        self._fg_default = pal.color(QtGui.QPalette.WindowText)
        self._sel_bg = pal.color(QtGui.QPalette.Highlight)
        self._sel_fg = pal.color(QtGui.QPalette.HighlightedText)
        if self._cursor_color is None:
            self._cursor_color = pal.color(QtGui.QPalette.Text)
        if self._link_color is None:
            self._link_color = QtGui.QColor("#2f6fff")

    def _recompute_metrics(self):
        fm = QtGui.QFontMetrics(self.font())
        self._cell_w = max(1, fm.horizontalAdvance("M"))
        self._cell_h = max(1, fm.height())
        self._baseline = fm.ascent()

    def changeEvent(self, ev: QtCore.QEvent):
        et = ev.type()
        if et in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._recompute_metrics()
            self.update()
        if et == QtCore.QEvent.PaletteChange:
            self._install_palette_defaults()
            self.update()
        super().changeEvent(ev)

    def focusNextPrevChild(self, next: bool) -> bool:
        # Keep focus so Tab reaches the shell instead of moving to other widgets.
        return False

    # -------- UI: Toolbar, Run Button, Context Menu --------
    def _build_toolbar(self):
        self._toolbar = QtWidgets.QToolBar(self)
        self._toolbar.setObjectName("TerminalToolbar")
        self._toolbar.setProperty("surface", "terminal-toolbar")
        self._toolbar.setIconSize(QtCore.QSize(16, 16))
        self._toolbar.setMovable(False)
        self._toolbar.setFloatable(False)

        act_copy = self._toolbar.addAction("Copy")
        act_copy.triggered.connect(self.copySelection)

        act_paste = self._toolbar.addAction("Paste")
        act_paste.triggered.connect(self._paste_bracketed)

        act_clear = self._toolbar.addAction("Clear")
        def _do_clear():
            self._send(b"clear\r")
            self._view_offset = 0
            self.update()
        act_clear.triggered.connect(_do_clear)

        self._toolbar.addSeparator()

        act_buf = self._toolbar.addAction("Buffer…")
        act_buf.setToolTip("Change virtual buffer (cols × rows)")
        act_buf.triggered.connect(self._open_buffer_dialog)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._toolbar)
        lay.addStretch(1)

    def _build_run_button(self):
        self._toolbar.addSeparator()
        self._btn_run = QtWidgets.QToolButton(self)
        self._btn_run.setObjectName("TerminalRunButton")
        self._btn_run.setProperty("surface", "terminal-run-button")
        self._btn_run.setText("Run")
        self._btn_run.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        self._run_menu = QtWidgets.QMenu(self._btn_run)
        self._run_menu.setObjectName("TerminalRunMenu")
        self._run_menu.setProperty("surface", "terminal-run-menu")
        self._btn_run.setMenu(self._run_menu)
        self._btn_run.clicked.connect(self._run_last_or_first)
        self._toolbar.addWidget(self._btn_run)
        self._rebuild_run_menu()

    def _rebuild_run_menu(self):
        self._run_menu.clear()
        if self._quick_cmds:
            m_quick = self._run_menu.addMenu("Quick Commands")
            for spec in self._quick_cmds:
                act = m_quick.addAction(spec.label)
                act.triggered.connect(lambda _, s=spec: self._execute_spec(s))
        if self._templates:
            if self._quick_cmds:
                self._run_menu.addSeparator()
            m_tpl = self._run_menu.addMenu("Templates")
            for spec in self._templates:
                act = m_tpl.addAction(spec.label)
                act.triggered.connect(lambda _, s=spec: self._execute_spec(s))
        if self._quick_cmds or self._templates:
            self._run_menu.addSeparator()
        self._run_menu.addAction("Edit…").triggered.connect(self.open_commands_editor)

    def _coerce_command_specs(self, entries) -> List[CommandSpec]:
        specs: List[CommandSpec] = []
        if not entries:
            return specs
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            filtered: Dict[str, object] = {}
            for key in ("label", "cmd", "params", "cwd", "env", "dryrun"):
                if key in entry:
                    filtered[key] = entry[key]
            label = filtered.get("label")
            cmd = filtered.get("cmd")
            if not isinstance(label, str) or not isinstance(cmd, str):
                continue
            params = filtered.get("params")
            if not isinstance(params, list):
                filtered["params"] = []
            env = filtered.get("env")
            if not isinstance(env, dict):
                filtered["env"] = {}
            try:
                specs.append(CommandSpec(**filtered))
            except TypeError:
                continue
        return specs

    def open_commands_editor(self):
        self._invoke_edit_commands()

    def _invoke_edit_commands(self):
        if self._edit_commands_callback is not None:
            self._edit_commands_callback()
        else:
            QtWidgets.QMessageBox.information(
                self,
                "Commands",
                "To edit commands programmatically, call add_quick_command()/add_template().\n"
                "You can also implement a JSON-backed editor here."
            )

    def _run_last_or_first(self):
        spec = self._last_used or (self._quick_cmds[0] if self._quick_cmds else (self._templates[0] if self._templates else None))
        if spec:
            self._execute_spec(spec)

    def add_quick_command(self, label: str, cmd: str, *, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None):
        self._quick_cmds.append(CommandSpec(label, cmd, [], cwd, env or {}))
        self._rebuild_run_menu()

    def add_template(self, label: str, cmd: str, *, params: Optional[List[str]] = None,
                     cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None, dryrun: bool = False):
        self._templates.append(CommandSpec(label, cmd, params or [], cwd, env or {}, dryrun))
        self._rebuild_run_menu()

    def set_commands(
            self,
            *,
            quick_commands: Optional[List[Dict[str, object]]] = None,
            templates: Optional[List[Dict[str, object]]] = None,
    ) -> None:
        updated = False
        if quick_commands is not None:
            self._quick_cmds = self._coerce_command_specs(quick_commands)
            updated = True
        if templates is not None:
            self._templates = self._coerce_command_specs(templates)
            updated = True
        if updated:
            self._last_used = None
            self._rebuild_run_menu()

    def set_commands_editor(self, callback: Optional[Callable[[], None]]) -> None:
        self._edit_commands_callback = callback

    def _show_context_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        has_sel = (
                self._sel_start is not None
                and self._sel_end is not None
                and self._sel_start != self._sel_end
        )
        copy_act = menu.addAction("Copy")
        copy_act.setEnabled(bool(has_sel))
        copy_act.triggered.connect(self.copySelection)

        paste_act = menu.addAction("Paste")
        paste_act.triggered.connect(self._paste_bracketed)

        menu.exec(self.mapToGlobal(pos))

    # -------- Emulator/PTy Helpers --------
    def _set_winsize(self, rows, cols):
        TIOCSWINSZ = getattr(termios, "TIOCSWINSZ", 0x5414)
        buf = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self._fd, TIOCSWINSZ, buf)
            os.kill(self._pid, signal.SIGWINCH)
        except Exception:
            pass

    def _snapshot_all_lines(self) -> list[str]:
        """Oldest→newest: history strings + live buffer rows (trimmed)."""
        out = []
        hist_sequences = self._history_sequences()
        for entry in self._iter_history_entries(hist_sequences):
            out.append(self._history_entry_to_text(entry))
        cols = int(self._screen.columns)
        rows = int(self._screen.lines)
        for r in range(rows):
            row = self._screen.buffer.get(r, {})
            last = -1
            chars = []
            for c in range(cols):
                cell = row.get(c)
                ch = cell.data if cell and cell.data else " "
                chars.append(ch)
                if ch != " ":
                    last = c
            out.append("".join(chars[:last + 1]) if last >= 0 else "")
        return out

    def _history_sequences(self) -> tuple[Sequence, ...]:
        """Return sequences representing stored history rows."""
        screen = getattr(self, "_screen", None)
        if screen is None:
            return ()
        hist = getattr(screen, "history", None)
        if hist is None:
            return ()
        sequences: List[Sequence] = []
        top = getattr(hist, "top", None)
        bottom = getattr(hist, "bottom", None)
        if top is not None and hasattr(top, "__len__"):
            sequences.append(top)
        if bottom is not None and hasattr(bottom, "__len__"):
            sequences.append(bottom)
        if not sequences:
            if isinstance(hist, (list, tuple)):
                sequences.append(hist)
        return tuple(seq for seq in sequences if seq is not None)

    def _history_length(self, sequences: Optional[Sequence[Sequence]] = None) -> int:
        seqs = self._history_sequences() if sequences is None else sequences
        total = 0
        for seq in seqs:
            try:
                total += len(seq)
            except TypeError:
                continue
        return total

    def _history_entry_at(self, idx: int, sequences: Optional[Sequence[Sequence]] = None):
        if idx < 0:
            return None
        seqs = self._history_sequences() if sequences is None else sequences
        for seq in seqs:
            seq_len = len(seq)
            if idx < seq_len:
                try:
                    return seq[idx]
                except Exception:
                    return None
            idx -= seq_len
        return None

    def _iter_history_entries(self, sequences: Optional[Sequence[Sequence]] = None):
        seqs = self._history_sequences() if sequences is None else sequences
        for seq in seqs:
            for entry in seq:
                yield entry

    def _history_entry_to_text(self, entry) -> str:
        data = entry
        if isinstance(data, tuple) and data:
            data = data[0]
        if isinstance(data, (bytes, bytearray)):
            try:
                return data.decode("utf-8", errors="replace")
            except Exception:
                return ""
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            try:
                cols = int(getattr(self._screen, "columns", 0))
            except Exception:
                cols = 0
            cols = max(0, cols)
            chars: list[str] = []
            for col in range(cols):
                cell = data.get(col)
                ch = getattr(cell, "data", None)
                chars.append(ch if isinstance(ch, str) and ch else " ")
            return "".join(chars).rstrip()
        if isinstance(data, list):
            chars: list[str] = []
            for item in data:
                if isinstance(item, str):
                    chars.append(item)
                else:
                    ch = getattr(item, "data", None)
                    chars.append(ch if isinstance(ch, str) and ch else " ")
            return "".join(chars).rstrip()
        if data is None:
            return ""
        if isinstance(data, (int, float, bool)):
            return ""
        return ""

    def _rebuild_with_lines(self, cols: int, rows: int, lines: list[str]) -> None:
        """Recreate HistoryScreen with (cols, rows) and replay lines safely."""
        self._notifier.setEnabled(False)
        try:
            self._screen = HistoryScreen(cols, rows, history=self._history_limit)
            self._screen.set_mode(modes.DECAWM)
            self._stream = pyte.ByteStream(self._screen)
            if lines:
                payload = ("\r\n".join(lines)).encode("utf-8", errors="replace")
                try:
                    self._stream.feed(payload)
                except Exception:
                    pass
            # Move cursor to col 1, last row
            try:
                self._stream.feed(b"\x1b[%d;%dH" % (self._screen.lines, 1))
            except Exception:
                pass
        finally:
            self._notifier.setEnabled(True)

    # -------- Geometry & Viewport --------
    def _top_offset(self) -> int:
        return self._toolbar.height() if hasattr(self, "_toolbar") else 0

    def _visible_cols(self) -> int:
        return max(1, self.width() // self._cell_w)

    def _visible_rows(self) -> int:
        h = max(0, self.height() - self._top_offset())
        return max(1, h // self._cell_h)

    def _row_effective_len(self, row_dict: dict, columns: int) -> int:
        if not row_dict or columns <= 0:
            return 0
        for c in range(columns - 1, -1, -1):
            cell = row_dict.get(c)
            if not cell:
                continue
            data = getattr(cell, "data", None)
            if data and data != " ":
                return c + 1
            if getattr(cell, "reverse", False):
                return c + 1
        return 0

    def _live_rows(self) -> int:
        screen = getattr(self, "_screen", None)
        if screen is None:
            return 0
        columns = int(getattr(screen, "columns", 0))
        buffer = getattr(screen, "buffer", {})
        cursor = getattr(screen, "cursor", None)
        cursor_row = getattr(cursor, "y", 0) if cursor is not None else 0
        for row_idx in range(int(getattr(screen, "lines", 0)) - 1, -1, -1):
            row = buffer.get(row_idx, {})
            if self._row_effective_len(row, columns) > 0 or row_idx == cursor_row:
                return row_idx + 1
        return max(0, cursor_row + 1)

    def _max_view_offset(self, vis_rows: Optional[int] = None) -> int:
        if vis_rows is None:
            vis_rows = self._visible_rows()
        screen = getattr(self, "_screen", None)
        if screen is None:
            return 0
        hist_len = self._history_length()
        live_rows = max(1, self._live_rows())
        total_rows = hist_len + live_rows
        return max(0, total_rows - vis_rows)

    # -------- Async I/O --------
    def _read_ready(self):
        try:
            data = os.read(self._fd, 65536)
            if not data:
                self._notifier.setEnabled(False)
                self._emit_shell_exited_once(0)
                return

            # Track xterm mouse modes
            for m in self._RE_MOUSE_ENABLE.finditer(data):
                mode = m.group("mode")
                if mode in (b"1000", b"1002", b"1003"):
                    self._mouse_mode_btn = True
                if mode == b"1003":
                    self._mouse_mode_any = True
                if mode == b"1006":
                    self._mouse_mode_sgr = True
            for m in self._RE_MOUSE_DISABLE.finditer(data):
                mode = m.group("mode")
                if mode in (b"1000", b"1002", b"1003"):
                    self._mouse_mode_btn = False
                    self._mouse_mode_any = False
                if mode == b"1006":
                    self._mouse_mode_sgr = False

            # Track bracketed paste mode 2004
            if self._RE_BRACKET_PASTE_ENABLE.search(data):
                self._bracket_paste_enabled = True
            if self._RE_BRACKET_PASTE_DISABLE.search(data):
                self._bracket_paste_enabled = False

            self.outputReceived.emit(bytes(data))
            self._stream.feed(data)

            if self._view_offset == 0:
                self._ensure_bottom()
            self.update()
        except BlockingIOError:
            pass
        except OSError:
            self._notifier.setEnabled(False)
            self._emit_shell_exited_once(1)

    def _emit_shell_exited_once(self, code: int):
        if self._shell_exit_emitted:
            return
        self._shell_exit_emitted = True
        self.shellExited.emit(int(code))

    def _send(self, b: bytes):
        try:
            os.write(self._fd, b)
        except OSError:
            pass

    def post(self, data: str | bytes):
        """
        Send a high-level command to the PTY, ensuring it is newline-terminated.
        """
        if isinstance(data, str):
            payload = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray, memoryview)):
            payload = bytes(data)
        else:
            raise TypeError("TerminalWidget.post expects str or bytes-like data")
        if not payload.endswith(b"\r"):
            payload += b"\r"
        self._send(payload)

    def interrupt(self):
        self._send(b"\x03")

    def process_id(self) -> int:
        return int(self._pid)

    def foreground_process_group(self) -> int | None:
        try:
            pgid = os.tcgetpgrp(self._fd)
            return int(pgid) if pgid > 0 else None
        except Exception:
            return None

    def signal_process_group(self, pgid: int, sig: int) -> bool:
        if not isinstance(pgid, int) or pgid <= 0:
            return False
        try:
            os.killpg(pgid, sig)
            return True
        except Exception:
            return False

    def signal_process(self, pid: int, sig: int) -> bool:
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, sig)
            return True
        except Exception:
            return False

    # -------- Paint --------
    def paintEvent(self, e: QtGui.QPaintEvent):
        p = QtGui.QPainter(self)
        try:
            opt = QtWidgets.QStyleOption()
            opt.initFrom(self)
            self.style().drawPrimitive(QtWidgets.QStyle.PE_Widget, opt, p, self)

            p.setFont(self.font())
            top_off = self._top_offset()
            p.translate(0, top_off)

            emu_rows, emu_cols = self._screen.lines, self._screen.columns
            vis_cols = self._visible_cols()
            vis_rows = self._visible_rows()

            hist_sequences = self._history_sequences()
            hist_len = self._history_length(hist_sequences)
            live_rows = max(1, self._live_rows())
            total_rows = hist_len + live_rows
            view_top = max(0, total_rows - vis_rows - self._view_offset)
            traceback_row_flags = self._visible_traceback_row_flags()

            show_cursor = self._cursor_visible and self._view_offset == 0
            cursor = getattr(self._screen, "cursor", None)
            cursor_target_row = cursor.y if (show_cursor and cursor is not None) else None
            cursor_target_col = cursor.x if (show_cursor and cursor is not None) else None
            cursor_vis_row = None
            cursor_vis_col = None

            def row_effective_len(row_dict: dict) -> int:
                return self._row_effective_len(row_dict, emu_cols)

            sel_active = self._sel_start is not None and self._sel_end is not None
            if sel_active:
                (sel_c1, sel_r1), (sel_c2, sel_r2) = self._sel_start, self._sel_end
                if (sel_r1, sel_c1) > (sel_r2, sel_c2):
                    sel_c1, sel_r1, sel_c2, sel_r2 = sel_c2, sel_r2, sel_c1, sel_r1
            else:
                sel_c1 = sel_r1 = sel_c2 = sel_r2 = 0

            def selection_bounds_for_row(row_idx: int):
                if not sel_active or vis_cols <= 0:
                    return None
                if row_idx < sel_r1 or row_idx > sel_r2:
                    return None
                start_col = sel_c1 if row_idx == sel_r1 else 0
                end_col = sel_c2 if row_idx == sel_r2 else vis_cols - 1
                if end_col < start_col:
                    return None
                start_col = max(0, min(start_col, vis_cols - 1))
                end_col = max(start_col, min(end_col, vis_cols - 1))
                if end_col < start_col:
                    return None
                return start_col, end_col

            def paint_selection_row(row_idx: int, y_base: int):
                bounds = selection_bounds_for_row(row_idx)
                if not bounds:
                    return
                start_col, end_col = bounds
                width = (end_col - start_col + 1) * self._cell_w
                p.fillRect(start_col * self._cell_w, y_base, width, self._cell_h, self._sel_bg)

            def is_traceback_row(row_idx: int) -> bool:
                return 0 <= row_idx < len(traceback_row_flags) and bool(traceback_row_flags[row_idx])

            y = 0
            display_row = 0

            for row_i in range(emu_rows):
                if y >= vis_rows * self._cell_h:
                    break

                global_i = view_top + row_i
                if global_i < hist_len:
                    entry = self._history_entry_at(global_i, hist_sequences)
                    text = self._history_entry_to_text(entry)
                    if not text:
                        paint_selection_row(display_row, y)
                        p.setPen(self._link_color if is_traceback_row(display_row) else self._fg_default)
                        p.drawText(0, y + self._baseline, "")
                        y += self._cell_h
                        display_row += 1
                        continue

                    idx = 0
                    text_len = len(text)
                    while True:
                        chunk = text[idx:idx + vis_cols] if vis_cols > 0 else text[idx:]
                        paint_selection_row(display_row, y)
                        p.setPen(self._link_color if is_traceback_row(display_row) else self._fg_default)
                        p.drawText(0, y + self._baseline, chunk)
                        y += self._cell_h
                        display_row += 1
                        if vis_cols <= 0 or idx + vis_cols >= text_len:
                            break
                        idx += vis_cols
                    continue

                buf_row = global_i - hist_len
                row = self._screen.buffer.get(buf_row, {})
                eff_len = row_effective_len(row)
                row_display_base = display_row

                if eff_len == 0:
                    paint_selection_row(row_display_base, y)
                    p.setPen(self._link_color if is_traceback_row(row_display_base) else self._fg_default)
                    p.drawText(0, y + self._baseline, "")
                    y += self._cell_h
                    display_row += 1
                    continue

                max_vis_cols = max(1, vis_cols)
                cursor_col_for_row = None
                if cursor_target_row is not None and cursor_target_col is not None and buf_row == cursor_target_row:
                    cursor_col_for_row = max(0, cursor_target_col)
                span_len = eff_len
                if cursor_col_for_row is not None:
                    span_len = max(span_len, cursor_col_for_row + 1)
                wrapped_rows = max(1, (span_len + max_vis_cols - 1) // max_vis_cols)

                for col_idx in range(eff_len):
                    cell = row.get(col_idx)
                    if not cell:
                        continue
                    bg = qcolor_from_pyte(cell.bg, self._bg_default)
                    if getattr(cell, "reverse", False):
                        fg_tmp = qcolor_from_pyte(cell.fg, self._fg_default)
                        bg = fg_tmp
                    if bg != self._bg_default:
                        wrap_row_offset = col_idx // max_vis_cols
                        wrap_col = col_idx % max_vis_cols
                        y_base = y + wrap_row_offset * self._cell_h
                        p.fillRect(wrap_col * self._cell_w, y_base, self._cell_h, self._cell_h, bg)

                for wrap_row_offset in range(wrapped_rows):
                    paint_selection_row(row_display_base + wrap_row_offset, y + wrap_row_offset * self._cell_h)

                for col_idx in range(eff_len):
                    cell = row.get(col_idx)
                    ch = (cell.data if cell and cell.data else " ")
                    if ch == " " and not cell:
                        continue

                    fg = qcolor_from_pyte(getattr(cell, "fg", None), self._fg_default)
                    bg = qcolor_from_pyte(getattr(cell, "bg", None), self._bg_default)
                    if getattr(cell, "reverse", False):
                        fg, bg = bg, fg

                    if getattr(cell, "bold", False):
                        f = QtGui.QFont(self.font())
                        f.setBold(True)
                        p.setFont(f)
                    else:
                        p.setFont(self.font())

                    wrap_row_offset = col_idx // max_vis_cols
                    wrap_col = col_idx % max_vis_cols
                    y_base = y + wrap_row_offset * self._cell_h
                    display_idx = row_display_base + wrap_row_offset
                    sel_bounds = selection_bounds_for_row(display_idx)
                    if sel_bounds and sel_bounds[0] <= wrap_col <= sel_bounds[1]:
                        p.setPen(self._sel_fg)
                    else:
                        p.setPen(self._link_color if is_traceback_row(display_idx) else fg)
                    p.drawText(wrap_col * self._cell_w, y_base + self._baseline, ch)

                y += wrapped_rows * self._cell_h
                display_row = row_display_base + wrapped_rows

                if cursor_col_for_row is not None and cursor_vis_row is None:
                    wrap_row_offset = cursor_col_for_row // max_vis_cols
                    wrap_col = cursor_col_for_row % max_vis_cols
                    cursor_vis_row = row_display_base + wrap_row_offset
                    cursor_vis_col = wrap_col

            if show_cursor and cursor_vis_row is not None and cursor_vis_col is not None:
                vis_y = cursor_vis_row * self._cell_h
                if 0 <= cursor_vis_row < vis_rows and 0 <= vis_y < (self.height() - top_off):
                    r = QtCore.QRect(cursor_vis_col * self._cell_w, vis_y, self._cell_w, self._cell_h)
                    c = QtGui.QColor(self._cursor_color)
                    c.setAlpha(180)
                    p.fillRect(r, c)
        finally:
            p.end()

    def _toggle_cursor(self):
        self._cursor_visible = not self._cursor_visible
        if self._view_offset == 0:
            self.update()

    # -------- Input: Wheel/Keys --------
    def wheelEvent(self, e: QtGui.QWheelEvent):
        # Terminal-directed scroll (xterm mouse mode) with Shift
        if self._mouse_mode_btn and (e.modifiers() & QtCore.Qt.ShiftModifier):
            col, row = self._cell_pos_from_event(e)
            col1, row1 = col + 1, row + 1
            delta = e.angleDelta().y()
            btn = 64 if delta > 0 else 65
            if self._mouse_mode_sgr:
                seq = f"\x1b[<%d;%d;%dM" % (btn, col1, row1)
                self._send(seq.encode("ascii"))
            else:
                Cb, Cx, Cy = 32 + btn, 32 + col1, 32 + row1
                self._send(b"\x1b[M" + bytes([Cb, Cx, Cy]))
            return

        # Viewport scrollback
        steps = e.angleDelta().y() / 120.0
        if steps != 0:
            vis_rows = self._visible_rows()
            max_offset = self._max_view_offset(vis_rows)
            new_offset = self._view_offset + steps * 3
            new_offset = max(0.0, min(new_offset, max_offset))
            self._view_offset = int(new_offset)
            self.update()

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        key, mods = e.key(), e.modifiers()
        vis_rows = self._visible_rows()
        max_offset = self._max_view_offset(vis_rows)
        step = max(1, vis_rows // 2)

        # Scrollback keys
        if key == QtCore.Qt.Key_PageUp:
            self._view_offset = min(self._view_offset + step, max_offset)
            self.update(); return
        if key == QtCore.Qt.Key_PageDown:
            self._view_offset = max(0, self._view_offset - step)
            self.update(); return
        if key == QtCore.Qt.Key_Home and (mods & QtCore.Qt.ControlModifier):
            self._view_offset = max_offset; self.update(); return
        if key == QtCore.Qt.Key_End and (mods & QtCore.Qt.ControlModifier):
            self._view_offset = 0; self.update(); return

        # Paste convenience (Ctrl+V without Shift)
        if (mods & QtCore.Qt.ControlModifier) and key == QtCore.Qt.Key_V and not (mods & QtCore.Qt.ShiftModifier):
            self._paste_bracketed(); return

        # Copy selection (Ctrl+Shift+C)
        if (mods & QtCore.Qt.ControlModifier) and (mods & QtCore.Qt.ShiftModifier) and key == QtCore.Qt.Key_C:
            self.copySelection(); return

        # Terminal input mapping
        seq = None
        if key == QtCore.Qt.Key_Backspace: seq = b"\x7f"
        elif key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter): seq = b"\r"
        elif key == QtCore.Qt.Key_Tab: seq = b"\t"
        elif key == QtCore.Qt.Key_Escape: seq = b"\x1b"
        elif key == QtCore.Qt.Key_Up: seq = b"\x1b[A"
        elif key == QtCore.Qt.Key_Down: seq = b"\x1b[B"
        elif key == QtCore.Qt.Key_Right: seq = b"\x1b[C"
        elif key == QtCore.Qt.Key_Left: seq = b"\x1b[D"
        elif key == QtCore.Qt.Key_Home: seq = b"\x1b[H"
        elif key == QtCore.Qt.Key_End: seq = b"\x1b[F"
        elif key == QtCore.Qt.Key_PageUp: seq = b"\x1b[5~"
        elif key == QtCore.Qt.Key_PageDown: seq = b"\x1b[6~"
        elif key == QtCore.Qt.Key_Insert: seq = b"\x1b[2~"
        elif key == QtCore.Qt.Key_Delete: seq = b"\x1b[3~"
        elif (mods & QtCore.Qt.ControlModifier) and QtCore.Qt.Key_A <= key <= QtCore.Qt.Key_Z:
            seq = bytes([key - QtCore.Qt.Key_A + 1])
        if seq is not None:
            self._send(seq)
            self._ensure_bottom()
            return

        text = e.text()
        if text:
            self._send(text.encode("utf-8"))
            self._ensure_bottom()

    # -------- Mouse & Selection --------
    def _cell_pos_from_event(self, e: QtGui.QMouseEvent):
        """Map mouse event to cell coords in viewport space, accounting for toolbar."""
        x = max(0, min(self.width() - 1, int(e.position().x())))
        y = max(0, int(e.position().y()) - self._top_offset())
        y = min(self.height() - 1 - self._top_offset(), y)
        col = x // self._cell_w
        row = max(0, y // self._cell_h)
        return col, row

    def _visible_row_records(self) -> list[dict]:
        vis_cols = self._visible_cols()
        vis_rows = self._visible_rows()
        if vis_cols <= 0 or vis_rows <= 0:
            return []

        emu_rows = int(self._screen.lines)
        hist_sequences = self._history_sequences()
        hist_len = self._history_length(hist_sequences)
        live_rows = max(1, self._live_rows())
        total_rows = hist_len + live_rows
        view_top = max(0, total_rows - vis_rows - self._view_offset)

        out: list[dict] = []
        for row_i in range(emu_rows):
            if len(out) >= vis_rows:
                break
            global_i = view_top + row_i
            if global_i < hist_len:
                full_text = self._history_entry_to_text(self._history_entry_at(global_i, hist_sequences))
            else:
                buf_row = global_i - hist_len
                row = self._screen.buffer.get(buf_row, {})
                eff_len = self._row_effective_len(row, int(self._screen.columns))
                chars: list[str] = []
                for col_idx in range(eff_len):
                    cell = row.get(col_idx)
                    chars.append(cell.data if cell and cell.data else " ")
                full_text = "".join(chars)

            if not full_text:
                out.append({"display_text": "", "source_line": ""})
                continue

            idx = 0
            text_len = len(full_text)
            while True:
                out.append(
                    {
                        "display_text": full_text[idx:idx + vis_cols],
                        "source_line": full_text,
                    }
                )
                if len(out) >= vis_rows:
                    break
                if idx + vis_cols >= text_len:
                    break
                idx += vis_cols
        return out

    def _traceback_target_from_row(self, row: int) -> Optional[tuple[str, int, int]]:
        rows = self._visible_row_records()
        if row < 0 or row >= len(rows):
            return None

        row_data = rows[row]
        source_line = str(row_data.get("source_line") or "").strip()
        return self._source_line_target(source_line)

    def _visible_traceback_row_flags(self) -> list[bool]:
        rows = self._visible_row_records()
        flags: list[bool] = []
        for row_data in rows:
            source_line = str(row_data.get("source_line") or "").strip()
            flags.append(bool(self._source_line_target(source_line)))
        return flags

    def _source_line_target(self, source_line: str) -> Optional[tuple[str, int, int]]:
        text = str(source_line or "").strip()
        if not text:
            return None

        py_match = self._RE_PY_TRACEBACK.search(text)
        if py_match:
            raw_path = str(py_match.group(1) or "").strip()
            if not raw_path or raw_path.startswith("<"):
                return None
            try:
                line = int(py_match.group(2))
            except Exception:
                line = 1
            return raw_path, max(1, line), 1

        cxx_match = self._RE_CXX_DIAG.match(text)
        if not cxx_match:
            return None
        raw_path = str(cxx_match.group("path") or "").strip()
        if not raw_path or raw_path.startswith("<"):
            return None
        try:
            line = int(cxx_match.group("line"))
        except Exception:
            line = 1
        try:
            col = int(cxx_match.group("col") or 1)
        except Exception:
            col = 1
        return raw_path, max(1, line), max(1, col)

    def _update_traceback_hover_cursor(self, row: int):
        if self._traceback_target_from_row(row):
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, e: QtGui.QMouseEvent):
        self._mouse_btns |= e.buttons().value
        if self._mouse_mode_btn:
            self._report_mouse(e, pressed=True)
        else:
            if e.button() == Qt.MouseButton.LeftButton:
                pos = self._cell_pos_from_event(e)
                self._sel_start = pos
                self._sel_end = pos
                self._selecting = True
                self.update()

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent):
        self._mouse_btns &= ~e.button().value
        if self._mouse_mode_btn:
            self._report_mouse(e, pressed=False)
        else:
            if e.button() == Qt.MouseButton.LeftButton and self._selecting:
                pos = self._cell_pos_from_event(e)
                clicked_without_drag = self._sel_start == pos
                self._sel_end = pos
                self._selecting = False
                if clicked_without_drag:
                    target = self._traceback_target_from_row(pos[1])
                    if target:
                        self._sel_start = None
                        self._sel_end = None
                        self.tracebackLinkActivated.emit(target[0], target[1], target[2])
                self.update()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent):
        if (self._mouse_mode_any or (self._mouse_mode_btn and self._mouse_btns != Qt.MouseButton.NoButton)) and self._mouse_mode_btn:
            self._report_mouse(e, motion=True)
        else:
            if self._selecting:
                pos = self._cell_pos_from_event(e)
                self._sel_end = pos
                self.update()
            else:
                _, row = self._cell_pos_from_event(e)
                self._update_traceback_hover_cursor(row)

    def leaveEvent(self, e: QtCore.QEvent):
        self.unsetCursor()
        super().leaveEvent(e)

    def _report_mouse(self, e: QtGui.QMouseEvent, pressed=None, motion=False):
        col, row = self._cell_pos_from_event(e)
        col1, row1 = col + 1, row + 1
        btn_code = 0
        if motion:
            btn_code = 32
            if self._mouse_btns & Qt.MouseButton.LeftButton:   btn_code |= 0
            elif self._mouse_btns & Qt.MouseButton.MiddleButton: btn_code |= 1
            elif self._mouse_btns & Qt.MouseButton.RightButton:  btn_code |= 2
        else:
            if pressed is True:
                if e.button() == Qt.MouseButton.LeftButton: btn_code = 0
                elif e.button() == Qt.MouseButton.MiddleButton: btn_code = 1
                elif e.button() == Qt.MouseButton.RightButton:  btn_code = 2
            elif pressed is False:
                btn_code = 3
        if self._mouse_mode_sgr:
            final = "M" if (pressed or motion) else "m"
            seq = f"\x1b[<%d;%d;%d%s" % (btn_code, col1, row1, final)
            self._send(seq.encode("ascii"))
        else:
            Cb = 32 + btn_code
            Cx = 32 + col1
            Cy = 32 + row1
            self._send(b"\x1b[M" + bytes([Cb, Cx, Cy]))

    # -------- Clipboard & Paste --------
    def _paste_bracketed(self):
        text = QtGui.QGuiApplication.clipboard().text()
        if not text:
            return
        payload = text.encode("utf-8")
        if self._bracket_paste_enabled:
            # xterm bracketed paste: ESC [ 200~ ... ESC [ 201~
            self._send(b"\x1b[200~" + payload + b"\x1b[201~")
        else:
            self._send(payload)
        self._ensure_bottom()

    def copySelection(self):
        if not self._sel_start or not self._sel_end:
            return
        (c1, r1), (c2, r2) = self._sel_start, self._sel_end
        if (r1, c1) > (r2, c2):
            c1, r1, c2, r2 = c2, r2, c1, r1

        vis_cols = self._visible_cols()
        hist_sequences = self._history_sequences()
        hist_len = self._history_length(hist_sequences)
        vis_rows = self._visible_rows()
        live_rows = max(1, self._live_rows())
        total_rows = hist_len + live_rows
        view_top = max(0, total_rows - vis_rows - self._view_offset)

        lines = []
        for vr in range(r1, r2 + 1):
            global_i = view_top + vr
            if global_i < hist_len:
                entry = self._history_entry_at(global_i, hist_sequences)
                s = self._history_entry_to_text(entry)
                part = s[c1:(c2+1)] if r1 == r2 else (s[c1:] if vr == r1 else (s[:c2+1] if vr == r2 else s))
                lines.append(part)
            else:
                buf_row = global_i - hist_len
                row = self._screen.buffer.get(buf_row, {})
                chars = []
                for col_idx in range(self._screen.columns):
                    cell = row.get(col_idx)
                    ch = cell.data if cell and cell.data else " "
                    chars.append(ch)
                s = "".join(chars).rstrip()
                part = s[c1:(c2+1)] if r1 == r2 else (s[c1:] if vr == r1 else (s[:c2+1] if vr == r2 else s))
                lines.append(part)
        QtGui.QGuiApplication.clipboard().setText("\n".join(lines))

    # -------- Scrolling & Resize --------
    def _ensure_bottom(self):
        if self._view_offset != 0:
            self._view_offset = 0
        self.update()

    def resizeEvent(self, e: QtGui.QResizeEvent):
        # Paint-only; emulator size remains fixed.
        self._view_offset = min(self._view_offset, self._max_view_offset())
        self.update()

    # -------- Cleanup --------
    def closeEvent(self, e: QtGui.QCloseEvent):
        self._closing = True
        try:
            self._notifier.setEnabled(False)
        except Exception:
            pass
        try:
            os.kill(self._pid, signal.SIGHUP)
        except Exception:
            pass
        self._emit_shell_exited_once(0)
        try:
            os.close(self._fd)
        except Exception:
            pass
        super().closeEvent(e)

    # -------- Buffer Dialog --------
    def _open_buffer_dialog(self):
        dlg = _BufferDialog(self, self._virt_cols, self._virt_rows)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        cols, rows = dlg.values()
        if cols == self._virt_cols and rows == self._virt_rows:
            return

        lines = self._snapshot_all_lines()
        self._virt_cols = int(cols)
        self._virt_rows = int(rows)
        self._rebuild_with_lines(self._virt_cols, self._virt_rows, lines)
        self._set_winsize(self._virt_rows, self._virt_cols)
        self._view_offset = 0
        self.update()

    # ======================= Run Button Helpers =======================

    class _SafeDict(dict):
        def __missing__(self, k):
            return "{" + k + "}"

    def _context(self) -> dict:
        # Extend these with app hooks as needed
        ctx = {
            "cwd": os.getcwd(),
            "project": getattr(self, "_project_dir", os.getcwd()),
            "user": os.environ.get("USER") or os.environ.get("USERNAME") or "",
            "python": sys.executable or "python3",
        }
        return ctx

    def _expand(self, spec: CommandSpec, user_params: Optional[dict] = None) -> str:
        ctx = TerminalWidget._SafeDict(self._context())
        if user_params:
            ctx.update(user_params)
        try:
            return spec.cmd.format_map(ctx)
        except Exception:
            return spec.cmd

    def _prompt_params(self, spec: CommandSpec) -> Optional[dict]:
        if not spec.params:
            return {}
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(spec.label)
        form = QtWidgets.QFormLayout(dlg)
        edits = {}
        for name in spec.params:
            e = QtWidgets.QLineEdit(dlg)
            edits[name] = e
            form.addRow(name + ":", e)
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, parent=dlg)
        form.addRow(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return {k: v.text() for k, v in edits.items()}

    def _execute_spec(self, spec: CommandSpec):
        # 1) gather params
        params = self._prompt_params(spec)
        if params is None:
            return
        # 2) expand
        line = self._expand(spec, params)
        # 3) build prologue (cwd/env)
        prologue = []
        if spec.cwd:
            prologue.append(f"cd {self._bash_quote(spec.cwd)}")
        for k, v in (spec.env or {}).items():
            prologue.append(f"export {k}={self._bash_quote(v)}")
        full = " && ".join(prologue + [line]) if prologue else line

        if spec.dryrun:
            res = QtWidgets.QMessageBox.question(self, "Command Preview", f"Run this?\n\n{full}",
                                                 QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if res != QtWidgets.QMessageBox.Yes:
                return
        # 4) send to PTY via bash -lc
        sent = f"bash -lc {self._bash_exec_arg(full)}\r"
        self._send(sent.encode("utf-8"))
        self._ensure_bottom()
        self._last_used = spec
        self._btn_run.setText(spec.label)

    @staticmethod
    def _bash_quote(s: str) -> str:
        # Safe single-quote for bash
        return "'" + s.replace("'", "'\"'\"'") + "'"

    @staticmethod
    def _bash_exec_arg(s: str) -> str:
        # argument for bash -lc '…'
        return TerminalWidget._bash_quote(s)


# --------------------------- Standalone demo ---------------------------

if __name__ == "__main__":
    import sys as _sys
    app = QtWidgets.QApplication(_sys.argv)
    win = QtWidgets.QMainWindow()

    # Example commands
    quick = [
        {"label": "uv sync", "cmd": "uv sync"},
        {"label": "Install reqs", "cmd": "uv pip install -r requirements.txt"},
        {"label": "Run tests", "cmd": "pytest -q"},
    ]
    templates = [
        {"label": "pip install {pkg}", "cmd": "uv pip install {pkg}", "params": ["pkg"]},
        {"label": "grep \"{text}\" in {project}", "cmd": "grep -R \"{text}\" {project}", "params": ["text"]},
        {"label": "pytest -k \"{expr}\"", "cmd": "pytest -k \"{expr}\" -q", "params": ["expr"]},
    ]

    term = TerminalWidget(shell="/bin/bash", cwd=os.path.expanduser("~"),
                          quick_commands=quick, templates=templates)
    win.setCentralWidget(term)
    win.resize(1100, 700)
    win.show()
    _sys.exit(app.exec())
