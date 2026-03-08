import os

from PySide6.QtCore import Qt, QRect, QSize, Signal
from PySide6.QtGui import QColor, QPainter, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QPlainTextEdit,
    QTextEdit,
    QSplitter,
    QLabel,
    QStyle,
)

from debugger_backend import ExecutionState
from debugger_controller import DebuggerController
from debugger_editor import DebugEditor


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self.editor.line_number_area_paint_event(event)

    def mousePressEvent(self, event):
        self.editor.line_number_area_mouse_press_event(event)


class DebugCodeEditor(QPlainTextEdit):
    breakpointToggled = Signal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)

        self.breakpoints = set()
        self.execution_line = -1

        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.update_extra_selections)

        self.update_line_number_area_width(0)
        self.update_extra_selections()

    def line_number_area_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 28 + self.fontMetrics().horizontalAdvance("9") * digits

    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())

        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#252526"))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_number = block_number + 1

                if line_number in self.breakpoints:
                    painter.setBrush(QColor("#e51400"))
                    painter.setPen(Qt.NoPen)
                    painter.drawEllipse(4, int(top) + 3, 10, 10)

                if line_number == self.execution_line:
                    painter.setPen(QColor("#ffd54f"))
                    painter.drawText(17, int(top), 12, self.fontMetrics().height(), Qt.AlignLeft, "▶")

                painter.setPen(QColor("#c8c8c8"))
                painter.drawText(
                    0,
                    int(top),
                    self.line_number_area.width() - 5,
                    self.fontMetrics().height(),
                    Qt.AlignRight,
                    str(line_number),
                )

            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1

    def line_number_area_mouse_press_event(self, event):
        y = event.position().y() if hasattr(event, "position") else event.y()

        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()

        while block.isValid():
            bottom = top + self.blockBoundingRect(block).height()
            if top <= y <= bottom:
                self.toggle_breakpoint(block.blockNumber() + 1)
                break
            block = block.next()
            top = bottom

    def toggle_breakpoint(self, line_number):
        if line_number in self.breakpoints:
            self.breakpoints.remove(line_number)
            self.breakpointToggled.emit(line_number, False)
        else:
            self.breakpoints.add(line_number)
            self.breakpointToggled.emit(line_number, True)
        self.line_number_area.update()

    def set_execution_line(self, line_number):
        self.execution_line = line_number
        self.update_extra_selections()
        self.line_number_area.update()

    def clear_execution_line(self):
        self.set_execution_line(-1)

    def update_extra_selections(self):
        selections = []

        if self.execution_line > 0:
            block = self.document().findBlockByLineNumber(self.execution_line - 1)
            if block.isValid():
                sel = QTextEdit.ExtraSelection()
                sel.cursor = QTextCursor(block)
                sel.format.setProperty(QTextFormat.FullWidthSelection, True)
                sel.format.setBackground(QColor(255, 230, 120, 90))
                selections.append(sel)
        else:
            sel = QTextEdit.ExtraSelection()
            sel.cursor = self.textCursor()
            sel.cursor.clearSelection()
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.format.setBackground(QColor(180, 180, 180, 40))
            selections.append(sel)

        self.setExtraSelections(selections)


class DebugCodeEditorAdapter(DebugEditor):
    def __init__(self, editor: DebugCodeEditor, file_path="", parent=None):
        super().__init__(parent)
        self.editor = editor
        self._file_path = file_path

    def file_path(self):
        return self._file_path

    def set_file_path(self, file_path):
        normalized = file_path or ""
        if self._file_path == normalized:
            return
        self._file_path = normalized
        self.filePathChanged.emit(self._file_path)

    def source_text(self):
        return self.editor.toPlainText()

    def breakpoints(self):
        return set(self.editor.breakpoints)

    def is_modified(self):
        return self.editor.document().isModified()

    def set_execution_line(self, line_number):
        self.editor.set_execution_line(line_number)

    def clear_execution_line(self):
        self.editor.clear_execution_line()


class DebuggerWidget(QWidget):
    stateChanged = Signal(str)

    def __init__(self, editor: DebugEditor, controller: DebuggerController, parent=None):
        super().__init__(parent)

        self.editor = editor
        self.controller = controller
        self._state = ExecutionState.IDLE

        self._build_ui()
        self._connect_controller()
        self._apply_state(ExecutionState.IDLE)

    def _build_ui(self):
        root = QVBoxLayout(self)

        toolbar = QHBoxLayout()

        self.btn_run = QPushButton("Run / Restart")
        self.btn_run.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.btn_run.clicked.connect(self.start_debugging)

        self.btn_step_over = QPushButton("Step Over")
        self.btn_step_over.clicked.connect(lambda: self.send_command("next"))

        self.btn_step_in = QPushButton("Step In")
        self.btn_step_in.clicked.connect(lambda: self.send_command("step"))

        self.btn_continue = QPushButton("Continue")
        self.btn_continue.clicked.connect(lambda: self.send_command("continue"))

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.btn_stop.clicked.connect(self.stop_debugging)

        self.status_label = QLabel("Idle")

        toolbar.addWidget(self.btn_run)
        toolbar.addWidget(self.btn_step_over)
        toolbar.addWidget(self.btn_step_in)
        toolbar.addWidget(self.btn_continue)
        toolbar.addWidget(self.btn_stop)
        toolbar.addStretch()
        toolbar.addWidget(self.status_label)

        splitter = QSplitter(Qt.Horizontal)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet(
            "background:#1e1e1e; color:#d4d4d4; font-family:monospace;"
        )

        self.variables_view = QTextEdit()
        self.variables_view.setReadOnly(True)
        self.variables_view.setPlaceholderText("Locals and globals will appear here...")

        splitter.addWidget(self.console)
        splitter.addWidget(self.variables_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        root.addLayout(toolbar)
        root.addWidget(splitter)

    def _connect_controller(self):
        self.controller.stateChanged.connect(self._handle_state_changed)
        self.controller.stdoutReceived.connect(self._handle_stdout_line)
        self.controller.stderrReceived.connect(self._handle_stderr_line)
        self.controller.protocolError.connect(self._handle_protocol_error)
        self.controller.started.connect(self._handle_session_started)
        self.controller.paused.connect(self._handle_session_paused)
        self.controller.exceptionRaised.connect(self._handle_exception)
        self.controller.fatalError.connect(self._handle_fatal)
        self.controller.finished.connect(self._handle_session_finished)

    def _apply_state(self, state: ExecutionState):
        self._state = state
        self.stateChanged.emit(state.value)

        self.status_label.setText(f"State: {state.value}")

        is_idle = state == ExecutionState.IDLE
        is_starting = state == ExecutionState.STARTING
        is_running = state == ExecutionState.RUNNING
        is_paused = state == ExecutionState.PAUSED
        is_stopping = state == ExecutionState.STOPPING

        self.btn_run.setEnabled(is_idle)
        self.btn_stop.setEnabled(not is_idle and not is_stopping)

        self.btn_step_over.setEnabled(is_paused)
        self.btn_step_in.setEnabled(is_paused)
        self.btn_continue.setEnabled(is_paused)

        if is_idle:
            self.editor.clear_execution_line()

    def _append_console(self, text):
        self.console.append(text)

    def _set_variables(self, stop_data):
        locals_dict = stop_data.get("locals", {})
        globals_dict = stop_data.get("globals", {})
        function_name = stop_data.get("function", "<unknown>")
        line_no = stop_data.get("line", "?")

        chunks = [f"Function: {function_name}", f"Line: {line_no}", "", "Locals:"]
        if locals_dict:
            for key in sorted(locals_dict):
                chunks.append(f"  {key} = {locals_dict[key]}")
        else:
            chunks.append("  <none>")

        chunks.append("")
        chunks.append("Globals:")
        if globals_dict:
            shown = 0
            for key in sorted(globals_dict):
                chunks.append(f"  {key} = {globals_dict[key]}")
                shown += 1
                if shown >= 25:
                    chunks.append("  ...")
                    break
        else:
            chunks.append("  <none>")

        self.variables_view.setPlainText("\n".join(chunks))

    def start_debugging(self):
        self.console.clear()
        self.variables_view.clear()

        self._append_console("<i>Preparing debugger...</i>")
        self.controller.start_debugging()

    def stop_debugging(self, clean_only=False):
        self.controller.stop_debugging(clean_only=clean_only)
        self.editor.clear_execution_line()

    def send_command(self, action, extra=None):
        self.controller.send_command(action, extra)

    def _handle_state_changed(self, state_value):
        self._apply_state(ExecutionState(state_value))

    def _handle_stdout_line(self, line):
        self._append_console(self._escape_plain(line))

    def _handle_stderr_line(self, line):
        self._append_console(f"<span style='color:#ff8080;'>{self._escape_plain(line)}</span>")

    def _handle_protocol_error(self, line):
        self._append_console(f"<span style='color:#ff8080;'>Bad protocol: {self._escape_plain(line)}</span>")

    def _handle_session_started(self, _data):
        self._append_console("<i>Debugger started</i>")

    def _handle_session_paused(self, data):
        line_no = data.get("line", -1)
        function_name = data.get("function", "<unknown>")
        file_name = os.path.basename(data.get("file", ""))

        self.editor.set_execution_line(line_no)
        self._set_variables(data)
        self._append_console(
            f"<i>Paused at {self._escape_plain(file_name)}:{line_no} in {self._escape_plain(function_name)}()</i>"
        )

    def _handle_exception(self, data):
        exc_type = data.get("type", "Exception")
        message = data.get("message", "")
        tb = data.get("traceback", "")
        self._append_console(
            f"<span style='color:#ff8080;'><b>{self._escape_plain(exc_type)}:</b> {self._escape_plain(message)}</span>"
        )
        if tb:
            self._append_console(f"<span style='color:#ffaaaa;'>{self._escape_plain(tb)}</span>")

    def _handle_fatal(self, data):
        message = data.get("message", "Fatal debugger error")
        tb = data.get("traceback", "")
        self._append_console(
            f"<span style='color:#ff4d4d;'><b>{self._escape_plain(message)}</b></span>"
        )
        if tb:
            self._append_console(f"<span style='color:#ffaaaa;'>{self._escape_plain(tb)}</span>")

    def _handle_session_finished(self):
        self._append_console("<i>Execution finished</i>")
        self.editor.clear_execution_line()

    @staticmethod
    def _escape_plain(text):
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
