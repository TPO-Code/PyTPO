import os
import re
import signal
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QTabWidget

from src.ui.widgets.terminal_widget import TerminalWidget


RUN_EXIT_MARKER_RE = re.compile(r"__PYTPO_RUN_EXIT__:(-?\d+)")

@dataclass
class ConsoleTabSession:
    file_key: str
    label: str
    run_in: str = ""
    interpreter: str = "python"
    run_mode: str = "python"  # python | custom
    terminal: Optional[TerminalWidget] = None
    process_handle: Optional[int] = None
    running: bool = False
    last_command: str = ""
    last_exit_code: Optional[int] = None
    failed: bool = False
    expected_stop: bool = False
    marker_buffer: str = ""
    stop_stage: int = 0
    stop_t1_timer: Optional[QTimer] = None
    stop_t2_timer: Optional[QTimer] = None


class ConsoleRunManager(QObject):
    runStateChanged = Signal()

    def __init__(
        self,
        tab_widget: QTabWidget,
        canonicalize: Callable[[str], str],
        resolve_interpreter: Callable[[str], str],
        resolve_run_in: Callable[[str], str],
        run_config_provider: Callable[[], dict],
        terminal_styler: Optional[Callable[[TerminalWidget], None]] = None,
        active_terminal_changed: Optional[Callable[[Optional[TerminalWidget]], None]] = None,
        traceback_activated: Optional[Callable[[str, int, int], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.tab_widget = tab_widget
        self._canonicalize = canonicalize
        self._resolve_interpreter = resolve_interpreter
        self._resolve_run_in = resolve_run_in
        self._run_config_provider = run_config_provider
        self._terminal_styler = terminal_styler
        self._active_terminal_changed = active_terminal_changed
        self._traceback_activated = traceback_activated
        self._sessions: dict[str, ConsoleTabSession] = {}

        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.currentChanged.connect(self._on_current_tab_changed)

    def get_or_create_tab(self, file_key: str, label: str) -> ConsoleTabSession:
        key = self._canonicalize(file_key)
        session = self._sessions.get(key)
        if session:
            if label:
                session.label = label
                self._sync_tab_title(session)
            return session

        terminal = TerminalWidget()
        if self._terminal_styler:
            self._terminal_styler(terminal)
        terminal.setProperty("file_key", key)
        terminal.outputReceived.connect(lambda data, k=key: self._handle_terminal_output(k, data))
        terminal.shellExited.connect(lambda code, k=key: self._handle_terminal_exit(k, code))
        terminal.tracebackLinkActivated.connect(
            lambda path, line, col, k=key: self._handle_traceback_link(k, path, line, col)
        )

        session = ConsoleTabSession(
            file_key=key,
            label=label,
            terminal=terminal,
            process_handle=terminal.process_id(),
        )
        self._sessions[key] = session
        self.tab_widget.addTab(terminal, label)
        self.tab_widget.setCurrentWidget(terminal)
        self._sync_tab_title(session)
        self._on_current_tab_changed(self.tab_widget.currentIndex())
        self.runStateChanged.emit()
        return session

    def clear_tab(self, file_key: str):
        key = self._canonicalize(file_key)
        session = self._sessions.get(key)
        if not session or not session.terminal:
            return
        session.marker_buffer = ""
        session.terminal.post("clear")

    def set_running(self, file_key: str, running: bool):
        key = self._canonicalize(file_key)
        session = self._sessions.get(key)
        if not session:
            return
        session.running = bool(running)
        if running:
            session.failed = False
            session.last_exit_code = None
            session.expected_stop = False
            session.stop_stage = 0
            self._cancel_stop_timers(session)
        else:
            self._cancel_stop_timers(session)
            session.stop_stage = 0
        self._sync_tab_title(session)
        self.runStateChanged.emit()

    def running_sessions(self) -> list[ConsoleTabSession]:
        return [session for session in self._sessions.values() if session.running]

    def session_for_key(self, file_key: str) -> Optional[ConsoleTabSession]:
        key = self._canonicalize(file_key)
        return self._sessions.get(key)

    def append_output(self, file_key: str, text: str):
        key = self._canonicalize(file_key)
        session = self._sessions.get(key)
        if not session or not session.terminal:
            return
        safe = text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
        session.terminal.post(f'printf "%s\\n" "{safe}"')

    def run_file(self, file_path: str):
        key = self._canonicalize(file_path)
        label = os.path.basename(key) or key
        session = self.get_or_create_tab(key, label)

        session.run_in = self._resolve_run_in(key)
        session.interpreter = self._resolve_interpreter(key)
        session.run_mode = "python"
        session.expected_stop = False
        session.last_exit_code = None
        session.failed = False
        session.marker_buffer = ""
        session.stop_stage = 0
        self._cancel_stop_timers(session)

        run_cfg = self._run_config()
        if bool(run_cfg.get("clear_output_before_run", True)):
            self.clear_tab(key)

        command_block = self._build_run_command_block(
            file_path=key,
            run_in=session.run_in,
            interpreter=session.interpreter,
        )
        self._run_session_command(session, command_block)

    def run_custom_command(
        self,
        *,
        file_key: str,
        label: str,
        run_in: str,
        command_block: str,
    ) -> None:
        key = self._canonicalize(file_key)
        resolved_run_in = self._canonicalize(run_in) if str(run_in or "").strip() else self._resolve_run_in(key)
        resolved_label = str(label or "").strip() or (os.path.basename(key) or key)
        session = self.get_or_create_tab(key, resolved_label)

        session.run_in = resolved_run_in
        session.interpreter = "__custom__"
        session.run_mode = "custom"
        session.expected_stop = False
        session.last_exit_code = None
        session.failed = False
        session.marker_buffer = ""
        session.stop_stage = 0
        self._cancel_stop_timers(session)

        self._run_session_command(session, command_block)

    def _run_session_command(self, session: ConsoleTabSession, command_block: str) -> None:
        key = self._canonicalize(session.file_key)
        run_cfg = self._run_config()
        if bool(run_cfg.get("clear_output_before_run", True)):
            self.clear_tab(key)

        session.last_command = command_block
        self.set_running(key, True)

        if bool(run_cfg.get("focus_output_on_run", True)):
            self.tab_widget.setCurrentWidget(session.terminal)

        session.terminal.post(command_block + "\n")

    def stop_file(self, file_path: str) -> int:
        key = self._canonicalize(file_path)
        session = self._sessions.get(key)
        if not session or not session.terminal:
            return 0
        if not session.running:
            self._cancel_stop_timers(session)
            session.stop_stage = 0
            return 0

        # Stop escalation is explicit user intent:
        # click 1: interrupt, click 2: SIGTERM, click 3+: SIGKILL.
        self._cancel_stop_timers(session)
        session.expected_stop = True
        if session.stop_stage <= 0:
            session.stop_stage = 1
            session.terminal.interrupt()
            return 1
        if session.stop_stage == 1:
            session.stop_stage = 2
            self._send_signal_best_effort(session, signal.SIGTERM)
            return 2
        session.stop_stage = 3
        self._send_signal_best_effort(session, signal.SIGKILL)
        return 3

    def stop_active_tab(self) -> int:
        session = self.active_session()
        if not session:
            return 0
        return self.stop_file(session.file_key)

    def rerun_file(self, file_path: str):
        key = self._canonicalize(file_path)
        session = self._sessions.get(key)
        if session and session.running:
            self.stop_file(key)
            if session.run_mode == "custom" and session.last_command:
                QTimer.singleShot(80, lambda k=key: self._rerun_existing_or_python(k))
            else:
                QTimer.singleShot(60, lambda k=key: self.run_file(k))
            return
        if session and session.last_command and session.terminal:
            self._run_session_command(session, session.last_command)
            return
        self.run_file(key)

    def _rerun_existing_or_python(self, file_key: str) -> None:
        key = self._canonicalize(file_key)
        session = self._sessions.get(key)
        if session and session.last_command and session.terminal:
            self._run_session_command(session, session.last_command)
            return
        self.run_file(key)

    def active_session(self) -> Optional[ConsoleTabSession]:
        w = self.tab_widget.currentWidget()
        if not isinstance(w, TerminalWidget):
            return None
        key = w.property("file_key")
        if not isinstance(key, str):
            return None
        return self._sessions.get(key)

    def active_file_key(self) -> Optional[str]:
        session = self.active_session()
        return session.file_key if session else None

    def close_session(self, file_key: str) -> bool:
        key = self._canonicalize(file_key)
        session = self._sessions.get(key)
        if session is None:
            return False
        self._close_session(key, session)
        return True

    def close_session_for_terminal(self, terminal: TerminalWidget) -> bool:
        for key, session in list(self._sessions.items()):
            if session.terminal is terminal:
                self._close_session(key, session)
                return True
        return False

    def _run_config(self) -> dict:
        cfg = self._run_config_provider() or {}
        if not isinstance(cfg, dict):
            return {}
        return cfg

    def _close_session(self, file_key: str, session: ConsoleTabSession):
        self._cancel_stop_timers(session)
        session.running = False
        session.expected_stop = True
        session.stop_stage = 0

        terminal = session.terminal
        session.terminal = None
        self._sessions.pop(file_key, None)

        if terminal is not None:
            idx = self.tab_widget.indexOf(terminal)
            if idx >= 0:
                self.tab_widget.removeTab(idx)
            try:
                terminal.close()
            except Exception:
                pass
            terminal.deleteLater()

        self._on_current_tab_changed(self.tab_widget.currentIndex())
        self.runStateChanged.emit()

    def _tab_index(self, session: ConsoleTabSession) -> int:
        if not session.terminal:
            return -1
        return self.tab_widget.indexOf(session.terminal)

    def _sync_tab_title(self, session: ConsoleTabSession):
        idx = self._tab_index(session)
        if idx < 0:
            return
        if session.running:
            title = f"● {session.label}"
        elif session.failed:
            title = f"✖ {session.label}"
        else:
            title = session.label
        self.tab_widget.setTabText(idx, title)

    def _handle_terminal_output(self, file_key: str, data: bytes):
        session = self._sessions.get(file_key)
        if not session:
            return

        chunk = data.decode("utf-8", errors="replace")
        merged = session.marker_buffer + chunk
        consumed = 0
        for match in RUN_EXIT_MARKER_RE.finditer(merged):
            consumed = match.end()
            try:
                exit_code = int(match.group(1))
            except Exception:
                exit_code = 1
            self._mark_run_finished(session, exit_code)

        if consumed > 0:
            merged = merged[consumed:]
        if len(merged) > 1024:
            merged = merged[-1024:]
        session.marker_buffer = merged

    def _handle_terminal_exit(self, file_key: str, _exit_code: int):
        session = self._sessions.get(file_key)
        if not session:
            return
        self._cancel_stop_timers(session)
        session.stop_stage = 0
        session.running = False
        if session.last_exit_code is None:
            session.last_exit_code = 1
            session.failed = True
        self._sync_tab_title(session)
        self.runStateChanged.emit()

    def _mark_run_finished(self, session: ConsoleTabSession, exit_code: int):
        self._cancel_stop_timers(session)
        session.stop_stage = 0
        session.running = False
        session.last_exit_code = exit_code
        if session.expected_stop:
            session.expected_stop = False
            session.failed = False
        else:
            session.failed = exit_code != 0
        self._sync_tab_title(session)
        self.runStateChanged.emit()

    def _build_run_command_block(self, file_path: str, run_in: str, interpreter: str) -> str:
        lines = [f"cd {shlex.quote(run_in)}"]

        activate_script = self._resolve_activate_script(interpreter)
        if activate_script:
            lines.append(f"source {shlex.quote(activate_script)}")
            python_cmd = f"python {shlex.quote(file_path)}"
        else:
            runner = interpreter.strip() or "python"
            python_cmd = f"{shlex.quote(runner)} {shlex.quote(file_path)}"

        lines.append("clear")
        lines.append(python_cmd)
        lines.append("status=$?")
        lines.append("printf '\\n__PYTPO_RUN_EXIT__:%s\\n' \"$status\"")
        return "\n".join(lines)

    def _resolve_activate_script(self, interpreter: str) -> Optional[str]:
        path_text = (interpreter or "").strip()
        if not path_text:
            return None

        interp_path = Path(path_text).expanduser()
        if not interp_path.is_absolute():
            return None
        if not interp_path.name.startswith("python"):
            return None
        if interp_path.parent.name != "bin":
            return None
        activate = interp_path.parent / "activate"
        if not activate.exists():
            return None
        return str(activate)

    def _on_current_tab_changed(self, _index: int):
        if self._active_terminal_changed is None:
            return
        w = self.tab_widget.currentWidget()
        self._active_terminal_changed(w if isinstance(w, TerminalWidget) else None)

    def _cancel_stop_timers(self, session: ConsoleTabSession):
        if session.stop_t1_timer is not None:
            session.stop_t1_timer.stop()
            session.stop_t1_timer.deleteLater()
            session.stop_t1_timer = None
        if session.stop_t2_timer is not None:
            session.stop_t2_timer.stop()
            session.stop_t2_timer.deleteLater()
            session.stop_t2_timer = None

    def _send_signal_best_effort(self, session: ConsoleTabSession, sig: int):
        terminal = session.terminal
        if terminal is None:
            return

        sent = False
        pgid = terminal.foreground_process_group()
        if pgid is not None:
            sent = terminal.signal_process_group(pgid, sig)
        if not sent:
            pid = session.process_handle or terminal.process_id()
            if isinstance(pid, int) and pid > 0:
                sent = terminal.signal_process(pid, sig)
        if not sent:
            self._inject_fallback_kill(session, sig)

    def _inject_fallback_kill(self, session: ConsoleTabSession, sig: int):
        terminal = session.terminal
        if terminal is None:
            return

        sig_name = "TERM" if sig == signal.SIGTERM else "KILL"
        fallback_script = "\n".join(
            [
                f"kill -{sig_name} -- -$$ >/dev/null 2>&1 || true",
                f"kill -{sig_name} $$ >/dev/null 2>&1 || true",
                f"pkill -{sig_name} -P $$ >/dev/null 2>&1 || true",
            ]
        )
        terminal.post(fallback_script + "\n")

    def _handle_traceback_link(self, file_key: str, raw_path: str, line: int, col: int):
        if self._traceback_activated is None:
            return

        session = self._sessions.get(file_key)
        if session is None:
            return

        path_text = str(raw_path or "").strip()
        if not path_text:
            return

        resolved = self._resolve_traceback_path(session, path_text)
        if not resolved:
            return
        self._traceback_activated(resolved, max(1, int(line or 1)), max(1, int(col or 1)))

    def _resolve_traceback_path(self, session: ConsoleTabSession, raw_path: str) -> Optional[str]:
        text = str(raw_path).strip()
        if not text or text.startswith("<"):
            return None

        candidates: list[str] = []
        if os.path.isabs(text):
            candidates.append(text)
        else:
            if session.run_in:
                candidates.append(os.path.join(session.run_in, text))
            if session.file_key:
                candidates.append(os.path.join(os.path.dirname(session.file_key), text))
            candidates.append(text)

        for candidate in candidates:
            cpath = self._canonicalize(candidate)
            if os.path.exists(cpath):
                return cpath
        if candidates:
            return self._canonicalize(candidates[0])
        return None
