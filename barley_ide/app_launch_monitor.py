from __future__ import annotations

import os
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path

MONITOR_LAUNCH_ARG = "--monitor-launch"


def extract_monitor_launch(argv: list[str]) -> tuple[str, list[str]] | None:
    if not argv or argv[0] != MONITOR_LAUNCH_ARG:
        return None
    if len(argv) < 3:
        return "", []
    return argv[1], argv[2:]


def run_monitored_process(
    *,
    python_executable: str,
    main_script: Path,
    working_dir: str,
    child_args: list[str],
    app_name: str,
) -> int:
    target_dir = str(Path(working_dir or Path.cwd()).expanduser())
    if not os.path.isdir(target_dir):
        target_dir = str(Path.cwd())

    command = [python_executable, str(main_script), *child_args]
    log_path = _make_log_path(app_name)

    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            log_file.write(f"[monitor] cwd: {target_dir}\n")
            log_file.write(f"[monitor] command: {_format_command(command)}\n\n")
            log_file.flush()
            proc = subprocess.Popen(
                command,
                cwd=target_dir,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            return_code = int(proc.wait())
    except Exception as exc:
        _show_error_dialog(
            f"{app_name} launch failed",
            f"Could not start the IDE process.\n\n{exc}",
        )
        return 1

    if return_code == 0:
        try:
            log_path.unlink()
        except Exception:
            pass
        return 0

    summary = _describe_exit(return_code)
    excerpt = _tail_log(log_path)
    body = [
        f"{app_name} exited unexpectedly.",
        "",
        f"Reason: {summary}",
        f"Working directory: {target_dir}",
        f"Log: {log_path}",
    ]
    if excerpt:
        body.extend(["", "Recent output:", excerpt])
    _show_error_dialog(f"{app_name} crash detected", "\n".join(body))
    return return_code


def _make_log_path(app_name: str) -> Path:
    safe_name = "".join(ch.lower() if ch.isalnum() else "-" for ch in app_name).strip("-") or "app"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / f"{safe_name}-crash-{stamp}-{os.getpid()}.log"


def _format_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _describe_exit(return_code: int) -> str:
    if return_code < 0:
        sig_num = -return_code
        try:
            sig_name = signal.Signals(sig_num).name
        except Exception:
            sig_name = f"signal {sig_num}"
        return f"terminated by {sig_name} ({sig_num})"
    return f"process exited with code {return_code}"


def _tail_log(log_path: Path, *, max_lines: int = 30, max_chars: int = 5000) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    if not lines:
        return ""
    excerpt = "\n".join(lines[-max_lines:]).strip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[-max_chars:].lstrip()
    return excerpt


def _show_error_dialog(title: str, message: str) -> None:
    if _show_qt_dialog(title, message):
        return
    if _show_tk_dialog(title, message):
        return
    try:
        print(f"{title}\n\n{message}", file=os.sys.stderr, flush=True)
    except Exception:
        pass


def _show_tk_dialog(title: str, message: str) -> bool:
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        messagebox.showerror(title, message, parent=root)
        root.destroy()
        return True
    except Exception:
        return False


def _show_qt_dialog(title: str, message: str) -> bool:
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import (
            QApplication,
            QDialog,
            QDialogButtonBox,
            QLabel,
            QPlainTextEdit,
            QVBoxLayout,
        )

        app = QApplication.instance()
        owns_app = app is None
        if app is None:
            app = QApplication([title])
        dialog = QDialog()
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.resize(820, 520)

        layout = QVBoxLayout(dialog)

        label = QLabel("The full error text below can be selected and copied.")
        label.setWordWrap(True)
        layout.addWidget(label)

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(message)
        editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        editor.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        layout.addWidget(editor, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        copy_button = buttons.addButton("Copy", QDialogButtonBox.ActionRole)
        copy_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(message))
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        editor.selectAll()
        dialog.exec()
        if owns_app:
            app.quit()
        return True
    except Exception:
        return False
