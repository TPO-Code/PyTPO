import os
import shlex
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QInputDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from debugger_controller import DebuggerController
from debugger_editor_manager import DebugEditorManager
from debugger_session_context import DebugSessionContext, LaunchTargetKind, NamedLaunchTarget, SavePolicy
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
        self.editors_by_widget = {}

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        controls = QHBoxLayout()
        self.path_label = QLabel("File: Untitled")
        self.btn_new = QPushButton("New Tab")
        self.btn_new.clicked.connect(self.new_tab)
        self.btn_open = QPushButton("Open File")
        self.btn_open.clicked.connect(self.open_file)
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self.save_file)
        self.btn_save_as = QPushButton("Save As")
        self.btn_save_as.clicked.connect(self.save_file_as)
        self.cwd_input = QLineEdit()
        self.cwd_input.setPlaceholderText("Working directory")
        self.cwd_input.editingFinished.connect(self._sync_argument_inputs)
        self.args_input = QLineEdit()
        self.args_input.setPlaceholderText("Arguments")
        self.args_input.editingFinished.connect(self._sync_argument_inputs)
        self.launch_kind = QComboBox()
        self.launch_kind.addItem("Active File", LaunchTargetKind.ACTIVE_FILE)
        self.launch_kind.addItem("Python Module", LaunchTargetKind.MODULE)
        self.launch_kind.addItem("Named Target", LaunchTargetKind.NAMED_TARGET)
        self.launch_kind.currentIndexChanged.connect(self._sync_launch_target_inputs)
        self.module_input = QLineEdit()
        self.module_input.setPlaceholderText("Module name")
        self.module_input.editingFinished.connect(self._sync_launch_target_inputs)
        self.named_target_combo = QComboBox()
        self.named_target_combo.currentIndexChanged.connect(self._sync_launch_target_inputs)
        self.btn_save_target = QPushButton("Save Target")
        self.btn_save_target.clicked.connect(self.save_named_target)
        self.btn_delete_target = QPushButton("Delete Target")
        self.btn_delete_target.clicked.connect(self.delete_named_target)
        self.env_input = QPlainTextEdit()
        self.env_input.setPlaceholderText("Environment, one KEY=VALUE per line")
        self.env_input.setFixedHeight(72)
        self.env_input.textChanged.connect(self._sync_environment_input)
        self.save_policy = QComboBox()
        self.save_policy.addItem("Debug Buffer", SavePolicy.DEBUG_BUFFER)
        self.save_policy.addItem("Require Save", SavePolicy.REQUIRE_SAVE)
        self.save_policy.currentIndexChanged.connect(self._sync_argument_inputs)

        controls.addWidget(self.btn_new)
        controls.addWidget(self.btn_open)
        controls.addWidget(self.btn_save)
        controls.addWidget(self.btn_save_as)
        controls.addWidget(self.launch_kind)
        controls.addWidget(self.module_input)
        controls.addWidget(self.named_target_combo)
        controls.addWidget(self.btn_save_target)
        controls.addWidget(self.btn_delete_target)
        controls.addWidget(self.cwd_input)
        controls.addWidget(self.args_input)
        controls.addWidget(self.save_policy)
        controls.addStretch()
        controls.addWidget(self.path_label)

        self.context = DebugSessionContext(self)
        self.context.filePathChanged.connect(self._update_path_label)
        self.backend = PythonDebuggerBackend(self)
        self.editor_manager = DebugEditorManager(self)
        self.tabs = QTabWidget()
        self.editor, self.editor_adapter = self._create_editor_tab("Untitled", SAMPLE_CODE)
        self.controller = DebuggerController(
            self.editor_adapter,
            self.backend,
            self.context,
            editor_manager=self.editor_manager,
            parent=self,
        )
        self.controller.activeEditorChanged.connect(self._handle_active_editor_changed)
        self.tabs.currentChanged.connect(self._handle_tab_changed)

        self.debugger = DebuggerWidget(self.editor_adapter, controller=self.controller)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.debugger)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout.addLayout(controls)
        layout.addWidget(self.env_input)
        layout.addWidget(splitter)
        self._refresh_named_targets()
        self._sync_argument_inputs()
        self._sync_environment_input()
        self._sync_launch_target_inputs()
        self._update_path_label(self.editor_adapter.file_path())

    def new_tab(self):
        self.editor, self.editor_adapter = self._create_editor_tab("Untitled", SAMPLE_CODE)
        self.controller.set_active_editor(self.editor_adapter)
        self._sync_argument_inputs()
        self._sync_launch_target_inputs()

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

        existing = self.editor_manager.editor_for_path(path)
        if existing is not None:
            self._activate_editor(existing)
            return

        self.editor, self.editor_adapter = self._create_editor_tab(os.path.basename(path), text, path)
        self.editor.document().setModified(False)
        self.current_file_path = path
        self.context.set_file_path(path)
        if not self.cwd_input.text().strip():
            self.context.set_working_directory(os.path.dirname(path))
        self._sync_launch_target_inputs()

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
        self._rename_current_tab()
        if not self.cwd_input.text().strip():
            self.context.set_working_directory(os.path.dirname(path))
        self._sync_launch_target_inputs()
        return True

    def _update_path_label(self, path):
        shown = path or "Untitled"
        self.path_label.setText(f"File: {shown}")

    def _sync_argument_inputs(self):
        self.context.set_file_path(self.editor_adapter.file_path())
        self.context.set_working_directory(self.cwd_input.text().strip())
        args_text = self.args_input.text().strip()
        try:
            arguments = shlex.split(args_text) if args_text else ()
        except ValueError as exc:
            self.path_label.setText(f"Invalid arguments: {exc}")
            return
        self.context.set_arguments(arguments)
        self.context.set_save_policy(self.save_policy.currentData())
        self._update_path_label(self.editor_adapter.file_path())
        self._sync_launch_target_inputs()

    def _sync_environment_input(self):
        environment = {}
        for line_number, raw_line in enumerate(self.env_input.toPlainText().splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                self.path_label.setText(f"Invalid env on line {line_number}: expected KEY=VALUE")
                return
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                self.path_label.setText(f"Invalid env on line {line_number}: missing variable name")
                return
            environment[key] = value
        self.context.set_environment(environment)
        self._update_path_label(self.editor_adapter.file_path())

    def _sync_launch_target_inputs(self):
        launch_kind = self.launch_kind.currentData()
        self.context.set_launch_target_kind(launch_kind)
        self.context.set_module_name(self.module_input.text().strip())
        self.context.set_selected_target_name(self.named_target_combo.currentData() or "")
        self.module_input.setVisible(launch_kind == LaunchTargetKind.MODULE)
        using_named_target = launch_kind == LaunchTargetKind.NAMED_TARGET
        self.named_target_combo.setVisible(using_named_target)
        self.btn_delete_target.setVisible(using_named_target)
        self._update_path_label(self.editor_adapter.file_path())

    def _refresh_named_targets(self):
        current_name = self.context.selected_target_name()
        self.named_target_combo.blockSignals(True)
        self.named_target_combo.clear()
        self.named_target_combo.addItem("Select target", "")
        for name, target in sorted(self.context.named_targets().items()):
            label = f"{name} ({target.kind.value})"
            self.named_target_combo.addItem(label, name)
        index = self.named_target_combo.findData(current_name)
        self.named_target_combo.setCurrentIndex(index if index >= 0 else 0)
        self.named_target_combo.blockSignals(False)

    def save_named_target(self):
        name, ok = QInputDialog.getText(self, "Save Launch Target", "Target name:")
        name = name.strip()
        if not ok or not name:
            return

        kind = self.launch_kind.currentData()
        if kind == LaunchTargetKind.NAMED_TARGET:
            kind = LaunchTargetKind.MODULE if self.module_input.text().strip() else LaunchTargetKind.ACTIVE_FILE

        target = NamedLaunchTarget(
            name=name,
            kind=kind,
            file_path=self.editor_adapter.file_path(),
            module_name=self.module_input.text().strip(),
            working_directory=self.cwd_input.text().strip(),
            arguments=self.context.arguments(),
            environment=self.context.environment(),
        )
        self.context.set_named_target(target)
        self.context.set_selected_target_name(name)
        self._refresh_named_targets()
        self.launch_kind.setCurrentIndex(self.launch_kind.findData(LaunchTargetKind.NAMED_TARGET))
        self._sync_launch_target_inputs()

    def delete_named_target(self):
        target_name = self.named_target_combo.currentData()
        if not target_name:
            return
        self.context.remove_named_target(target_name)
        self._refresh_named_targets()
        self._sync_launch_target_inputs()

    def _create_editor_tab(self, title, text, file_path=""):
        editor = DebugCodeEditor()
        editor.setPlainText(text)
        adapter = DebugCodeEditorAdapter(editor, file_path=file_path, parent=self)
        adapter.filePathChanged.connect(self._update_path_label)
        self.editor_manager.add_editor(adapter)
        self.editors_by_widget[editor] = adapter
        index = self.tabs.addTab(editor, title)
        self.tabs.setCurrentIndex(index)
        return editor, adapter

    def _handle_tab_changed(self, index):
        widget = self.tabs.widget(index)
        adapter = self.editors_by_widget.get(widget)
        if adapter is None:
            return
        self.editor = widget
        self.editor_adapter = adapter
        self.current_file_path = adapter.file_path()
        self.controller.set_active_editor(adapter)
        self._update_path_label(adapter.file_path())
        self._sync_argument_inputs()
        self._sync_launch_target_inputs()

    def _handle_active_editor_changed(self, editor):
        if editor is None or editor is self.editor_adapter:
            return
        self._activate_editor(editor)

    def _activate_editor(self, adapter):
        for widget, candidate in self.editors_by_widget.items():
            if candidate is adapter:
                self.tabs.setCurrentWidget(widget)
                return

    def _rename_current_tab(self):
        index = self.tabs.currentIndex()
        if index < 0:
            return
        title = os.path.basename(self.editor_adapter.file_path()) or "Untitled"
        self.tabs.setTabText(index, title)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DebuggerTestBedWindow()
    window.show()
    sys.exit(app.exec())
