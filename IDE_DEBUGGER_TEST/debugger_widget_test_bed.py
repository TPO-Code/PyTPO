import os
import shlex
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from debugger_controller import DebuggerController
from debugger_session_context import DebugSessionContext, SavePolicy
from debugger_session import PythonDebuggerBackend
from debugger_widget import DebugCodeEditor, DebugCodeEditorAdapter, DebuggerWidget


SAMPLE_CODE = '''\
def calculate(a, b):
    result = a + b
    return result

def greet(name):
    message = f"Hello, {name}"
    print(message)
    return message

print("Starting calculation")
x = 10
y = 20
z = calculate(x, y)
print(f"Result is {z}")

for i in range(3):
    print(f"Loop {i}")

greet("John")
print("Done")
'''


class DebuggerTestBedWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Debugger Prototype Host")
        self.resize(1100, 750)
        self.current_file_path = ""

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        controls = QHBoxLayout()
        self.path_label = QLabel("File: Untitled")
        self.btn_open = QPushButton("Open File")
        self.btn_open.clicked.connect(self.open_file)
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self.save_file)
        self.btn_save_as = QPushButton("Save As")
        self.btn_save_as.clicked.connect(self.save_file_as)
        self.cwd_input = QLineEdit()
        self.cwd_input.setPlaceholderText("Working directory")
        self.cwd_input.editingFinished.connect(self._sync_context_from_inputs)
        self.args_input = QLineEdit()
        self.args_input.setPlaceholderText("Arguments")
        self.args_input.editingFinished.connect(self._sync_context_from_inputs)
        self.save_policy = QComboBox()
        self.save_policy.addItem("Debug Buffer", SavePolicy.DEBUG_BUFFER)
        self.save_policy.addItem("Require Save", SavePolicy.REQUIRE_SAVE)
        self.save_policy.currentIndexChanged.connect(self._sync_context_from_inputs)

        controls.addWidget(self.btn_open)
        controls.addWidget(self.btn_save)
        controls.addWidget(self.btn_save_as)
        controls.addWidget(self.cwd_input)
        controls.addWidget(self.args_input)
        controls.addWidget(self.save_policy)
        controls.addStretch()
        controls.addWidget(self.path_label)

        self.editor = DebugCodeEditor()
        self.editor.setPlainText(SAMPLE_CODE)
        self.editor_adapter = DebugCodeEditorAdapter(self.editor, parent=self)
        self.editor_adapter.filePathChanged.connect(self._update_path_label)
        self.context = DebugSessionContext(self)
        self.context.filePathChanged.connect(self._update_path_label)
        self.backend = PythonDebuggerBackend(self)
        self.controller = DebuggerController(self.editor_adapter, self.backend, self.context, self)

        self.debugger = DebuggerWidget(self.editor_adapter, controller=self.controller)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.editor)
        splitter.addWidget(self.debugger)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout.addLayout(controls)
        layout.addWidget(splitter)
        self._sync_context_from_inputs()
        self._update_path_label(self.editor_adapter.file_path())

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Source File",
            self.current_file_path or os.getcwd(),
            "Source Files (*.py *.rs *.c *.cpp *.h *.hpp *.txt);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            QMessageBox.critical(self, "Open Failed", str(exc))
            return

        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        self.current_file_path = path
        self.editor_adapter.set_file_path(path)
        self.context.set_file_path(path)
        if not self.cwd_input.text().strip():
            self.context.set_working_directory(os.path.dirname(path))

    def save_file(self):
        if not self.current_file_path:
            return self.save_file_as()
        return self._write_file(self.current_file_path)

    def save_file_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Source File",
            self.current_file_path or os.getcwd(),
            "Python Files (*.py);;Rust Files (*.rs);;C/C++ Files (*.c *.cpp *.h *.hpp);;All Files (*)",
        )
        if not path:
            return False

        self.current_file_path = path
        self.editor_adapter.set_file_path(path)
        self.context.set_file_path(path)
        return self._write_file(path)

    def _write_file(self, path):
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.editor.toPlainText())
        except OSError as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))
            return False

        self.editor.document().setModified(False)
        self.current_file_path = path
        self.editor_adapter.set_file_path(path)
        self.context.set_file_path(path)
        if not self.cwd_input.text().strip():
            self.context.set_working_directory(os.path.dirname(path))
        return True

    def _update_path_label(self, path):
        shown = path or "Untitled"
        self.path_label.setText(f"File: {shown}")

    def _sync_context_from_inputs(self):
        self.context.set_file_path(self.editor_adapter.file_path())
        self.context.set_working_directory(self.cwd_input.text().strip())
        args_text = self.args_input.text().strip()
        self.context.set_arguments(shlex.split(args_text) if args_text else ())
        self.context.set_save_policy(self.save_policy.currentData())


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DebuggerTestBedWindow()
    window.show()
    sys.exit(app.exec())
