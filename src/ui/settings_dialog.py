from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Callable

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from TPOPyside.dialogs.schema_settings_dialog import (
    FieldBinding,
    SchemaField,
    SchemaPage,
    SchemaSection,
    SchemaSettingsDialog,
    SettingsSchema,
)
from src.settings_manager import SettingsManager
from src.ui.dialogs.file_dialog_bridge import get_existing_directory, get_open_file_name
from src.ui.dialogs.font_selection_dialog import FontSelectionDialog
from src.ui.interpreter_utils import (
    discover_project_interpreters,
    interpreter_browse_directory_hint,
    normalize_interpreter_for_project,
)
from src.ui.settings.ai_settings_page import AIAssistSettingsPage
from src.ui.settings.build_configs_settings_page import BuildConfigsSettingsPage
from src.ui.settings.clangd_repair_settings_page import ClangdRepairSettingsPage
from src.ui.settings.file_templates_settings_page import FileTemplatesSettingsPage
from src.ui.settings.github_settings_page import GitHubSettingsPage
from src.ui.settings.git_settings_page import GitSettingsPage
from src.ui.settings.keybindings_settings_page import KeybindingsSettingsPage
from src.ui.settings.project_maintenance_page import ProjectMaintenancePage
from src.ui.settings.python_run_configs_settings_page import PythonRunConfigsSettingsPage
from src.ui.settings.rust_run_configs_settings_page import RustRunConfigsSettingsPage
from src.ui.settings.syntax_highlighting_settings_page import SyntaxHighlightingSettingsPage

SETTINGS_TREE_EXPANDED_PATHS_KEY = "ui.settings_dialog.tree_expanded_paths"

PANEL_FIELD_TYPES: set[str] = {
    "ai_assist_editor",
    "github_editor",
    "git_editor",
    "keybindings_editor",
    "file_templates_editor",
    "build_configs_editor",
    "python_run_configs_editor",
    "rust_run_configs_editor",
    "syntax_highlighting_editor",
    "project_maintenance_tools",
    "clangd_repair_tools",
}


class SettingsDialog(SchemaSettingsDialog):
    """IDE-specific wrapper around the generic schema settings dialog."""

    def __init__(
        self,
        manager: SettingsManager,
        schema: SettingsSchema,
        *,
        initial_page_id: str | None = None,
        on_applied: Callable[[], None] | None = None,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        self.manager = manager
        super().__init__(
            backend=manager,
            schema=schema,
            initial_page_id=initial_page_id,
            on_applied=on_applied,
            use_native_chrome=use_native_chrome,
            parent=parent,
            object_name="SettingsDialog",
            window_title="Settings",
            scope_labeler=self._scope_labeler,
            tree_expanded_paths_key=SETTINGS_TREE_EXPANDED_PATHS_KEY,
            tree_expanded_paths_scope="ide",
            save_all_kwargs={"allow_project_repair": True},
            browse_providers={
                "path_dir": self._browse_project_directory,
                "path_file": self._browse_project_file,
                "codex_command": self._browse_codex_command_template,
            },
        )

    @staticmethod
    def _scope_labeler(scope: str) -> str:
        key = str(scope or "").strip().lower()
        if key == "ide":
            return "IDE"
        if key == "project":
            return "Project"
        return str(scope or "").strip().capitalize() or "Default"

    def _apply_application_tree_font(self) -> None:
        try:
            size = int(self.manager.get("tree_font_size", scope_preference="ide", default=10))
        except Exception:
            size = 10
        size = max(6, min(48, size))

        family = str(self.manager.get("tree_font_family", scope_preference="ide", default="") or "").strip()
        if family and family not in set(QFontDatabase.families()):
            family = ""

        try:
            base_font = QApplication.font(self.tree)
            font = self.tree.font()
            font.setPointSize(size)
            font.setFamily(family or base_font.family())
            self.tree.setFont(font)
        except Exception:
            return

    def _panel_binding(self, *, field: SchemaField, widget: QWidget) -> FieldBinding:
        return FieldBinding(
            key=field.key,
            scope=field.scope,
            widget=widget,
            getter=lambda: None,
            setter=lambda _value: None,
            on_change=lambda _cb: None,
            validate=lambda: [],
            persist=False,
            full_row=True,
            has_pending_changes=getattr(widget, "has_pending_settings_changes", None),
            apply_changes=getattr(widget, "apply_settings_changes", None),
        )

    def _browse_project_file(self, field: SchemaField, _dialog: SchemaSettingsDialog, current_text: str) -> str | None:
        selected, _selected_filter = get_open_file_name(
            parent=self,
            manager=self.manager,
            caption=str(field.browse_caption or "Select File"),
            directory=str(current_text or "").strip(),
            file_filter=str(
                field.browse_file_filter
                or ("Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.svg);;All Files (*)")
            ),
        )
        if not selected:
            return None
        return str(selected)

    def _browse_project_directory(
        self,
        field: SchemaField,
        _dialog: SchemaSettingsDialog,
        current_text: str,
    ) -> str | None:
        selected = get_existing_directory(
            parent=self,
            manager=self.manager,
            caption=str(field.browse_caption or "Select Directory"),
            directory=str(current_text or "").strip(),
        )
        if not selected:
            return None
        return str(selected)

    def _browse_codex_command_template(
        self,
        field: SchemaField,
        _dialog: SchemaSettingsDialog,
        current_text: str,
    ) -> str | None:
        raw = str(current_text or "").strip()
        start_dir = ""
        if raw:
            try:
                parsed = shlex.split(raw)
            except Exception:
                parsed = []
            if parsed:
                probe = Path(str(parsed[0] or "")).expanduser()
                if probe.is_dir():
                    start_dir = str(probe)
                else:
                    start_dir = str(probe.parent)
        selected, _selected_filter = get_open_file_name(
            parent=self,
            manager=self.manager,
            caption=str(field.browse_caption or "Select Codex Binary"),
            directory=start_dir,
            file_filter=str(field.browse_file_filter or "All Files (*)"),
        )
        if not selected:
            return None
        existing_tail: list[str] = []
        if raw:
            try:
                parsed = shlex.split(raw)
            except Exception:
                parsed = []
            if len(parsed) > 1:
                existing_tail = [str(part) for part in parsed[1:]]
        return shlex.join([str(selected), *existing_tail])

    def _create_field_binding(self, field: SchemaField) -> FieldBinding:
        if field.type in PANEL_FIELD_TYPES:
            if field.type == "ai_assist_editor":
                return self._panel_binding(
                    field=field,
                    widget=AIAssistSettingsPage(manager=self.manager, scope=field.scope, parent=self),
                )
            if field.type == "github_editor":
                return self._panel_binding(
                    field=field,
                    widget=GitHubSettingsPage(manager=self.manager, parent=self),
                )
            if field.type == "git_editor":
                return self._panel_binding(
                    field=field,
                    widget=GitSettingsPage(manager=self.manager, scope=field.scope, parent=self),
                )
            if field.type == "keybindings_editor":
                return self._panel_binding(
                    field=field,
                    widget=KeybindingsSettingsPage(manager=self.manager, parent=self),
                )
            if field.type == "file_templates_editor":
                return self._panel_binding(
                    field=field,
                    widget=FileTemplatesSettingsPage(manager=self.manager, parent=self),
                )
            if field.type == "build_configs_editor":
                return self._panel_binding(
                    field=field,
                    widget=BuildConfigsSettingsPage(manager=self.manager, parent=self),
                )
            if field.type == "python_run_configs_editor":
                return self._panel_binding(
                    field=field,
                    widget=PythonRunConfigsSettingsPage(manager=self.manager, parent=self),
                )
            if field.type == "rust_run_configs_editor":
                return self._panel_binding(
                    field=field,
                    widget=RustRunConfigsSettingsPage(manager=self.manager, parent=self),
                )
            if field.type == "syntax_highlighting_editor":
                return self._panel_binding(
                    field=field,
                    widget=SyntaxHighlightingSettingsPage(manager=self.manager, scope=field.scope, parent=self),
                )
            if field.type == "project_maintenance_tools":
                return self._panel_binding(
                    field=field,
                    widget=ProjectMaintenancePage(manager=self.manager, parent=self),
                )
            if field.type == "clangd_repair_tools":
                page = ClangdRepairSettingsPage(
                    manager=self.manager,
                    on_runtime_refresh=self.on_applied,
                    on_query_driver_updated=lambda value: self.set_bound_value(
                        key="c_cpp.query_driver",
                        scope="project",
                        value=value,
                    ),
                    parent=self,
                )
                return self._panel_binding(field=field, widget=page)

        if field.type == "lineedit" and field.scope == "project" and field.key == "interpreters.default":
            project_root = str(self.manager.paths.project_root)
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.NoInsert)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(28)

            holder = QWidget()
            root_row = QVBoxLayout(holder)
            root_row.setContentsMargins(0, 0, 0, 0)
            root_row.setSpacing(6)

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)

            refresh = QPushButton("Refresh")
            refresh.setFixedWidth(80)
            browse = QPushButton("Browse")
            browse.setFixedWidth(80)

            row.addWidget(combo, 1)
            row.addWidget(refresh)
            row.addWidget(browse)
            root_row.addLayout(row)

            detected_values: list[str] = []
            detected_row = QHBoxLayout()
            detected_row.setContentsMargins(0, 0, 0, 0)
            detected_row.setSpacing(6)
            detected_row.addWidget(QLabel("Detected in project:"))
            detected_combo = QComboBox(holder)
            detected_combo.setEditable(False)
            detected_row.addWidget(detected_combo, 1)
            use_detected = QPushButton("Use")
            use_detected.setFixedWidth(80)
            detected_row.addWidget(use_detected)
            root_row.addLayout(detected_row)

            def refresh_options(*, preferred: str | None = None) -> None:
                current = str(preferred if preferred is not None else combo.currentText()).strip()
                detected = discover_project_interpreters(project_root)
                detected_values.clear()
                detected_values.extend(detected)

                detected_combo.blockSignals(True)
                detected_combo.clear()
                if detected:
                    detected_combo.addItems(detected)
                else:
                    detected_combo.addItem("(no project interpreters found)")
                detected_combo.setEnabled(bool(detected))
                use_detected.setEnabled(bool(detected))
                detected_combo.blockSignals(False)

                options = list(detected)
                for fallback in ("python", "python3"):
                    if fallback not in options:
                        options.append(fallback)
                if current and current not in options:
                    options.insert(0, current)

                combo.blockSignals(True)
                combo.clear()
                combo.addItems(options)
                combo.setEditText(current)
                combo.blockSignals(False)

            def on_browse() -> None:
                selected, _selected_filter = get_open_file_name(
                    parent=self,
                    manager=self.manager,
                    caption="Select Python Interpreter",
                    directory=interpreter_browse_directory_hint(combo.currentText(), project_root),
                    file_filter="All Files (*)",
                )
                if not selected:
                    return
                refresh_options(preferred=normalize_interpreter_for_project(selected, project_root))

            def on_use_detected() -> None:
                idx = int(detected_combo.currentIndex())
                if idx < 0 or idx >= len(detected_values):
                    return
                chosen = str(detected_values[idx] or "").strip()
                if not chosen:
                    return
                combo.setEditText(chosen)

            refresh.clicked.connect(lambda: refresh_options())
            browse.clicked.connect(on_browse)
            use_detected.clicked.connect(on_use_detected)
            refresh_options()

            def get_value() -> str:
                return normalize_interpreter_for_project(str(combo.currentText() or "").strip(), project_root)

            def set_value(value: Any) -> None:
                text = str(value or "").strip()
                refresh_options(preferred=text)

            def connect_change(callback: Callable[..., None]) -> None:
                line_edit = combo.lineEdit()
                if line_edit is not None:
                    line_edit.textChanged.connect(callback)
                else:
                    combo.currentTextChanged.connect(callback)

            return FieldBinding(field.key, field.scope, holder, get_value, set_value, connect_change, lambda: [])

        if field.type == "font_family":
            line = QLineEdit()
            line.setPlaceholderText(field.description or "Font family")

            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)

            choose = QPushButton("Select Font...")
            choose.setFixedWidth(110)

            def on_choose() -> None:
                dialog = FontSelectionDialog(
                    initial_family=str(line.text() or "").strip(),
                    use_native_chrome=self.use_native_chrome,
                    parent=self,
                )
                if dialog.exec() != int(QDialog.DialogCode.Accepted):
                    return
                chosen = dialog.selected_family()
                if chosen:
                    line.setText(chosen)

            choose.clicked.connect(on_choose)

            row.addWidget(line, 1)
            row.addWidget(choose)

            def get_value() -> str:
                return str(line.text() or "").strip()

            def set_value(value: Any) -> None:
                line.setText(str(value or "").strip())

            def connect_change(callback: Callable[..., None]) -> None:
                line.textChanged.connect(callback)

            return FieldBinding(field.key, field.scope, holder, get_value, set_value, connect_change, lambda: [])

        return super()._create_field_binding(field)


def _apply_default_page_ordering(schema: SettingsSchema) -> None:
    scope_order = {"ide": 0, "project": 1}
    project_group_order = {
        "General": 0,
        "Languages": 1,
        "Execution": 2,
        "Maintenance": 3,
    }
    ide_group_order = {
        "General": 0,
        "Editor": 1,
        "Execution": 2,
        "Code Intelligence": 3,
        "Integrations": 4,
    }
    page_order_by_id = {
        "project-general": 0,
        "project-indexing": 1,
        "project-interpreters": 10,
        "project-cpp": 11,
        "project-rust": 12,
        "project-build-configs": 10,
        "project-rust-run-configs": 11,
        "project-run-configs": 12,
        "project-maintenance": 30,
        "ide-startup-projects": 100,
        "ide-window": 101,
        "ide-appearance": 102,
        "ide-editor-ux": 110,
        "ide-keybindings": 111,
        "ide-file-templates": 112,
        "ide-syntax-highlighting": 113,
        "ide-run": 120,
        "ide-linting": 130,
        "ide-ai-assist": 131,
        "ide-code-agents": 132,
        "ide-git": 140,
        "ide-github": 141,
    }
    for page in schema.pages:
        scope_key = str(page.scope or "").strip().lower()
        page.scope_order = scope_order.get(scope_key, 100)
        if scope_key == "project":
            page.category_order = project_group_order.get(str(page.category or "").strip(), 100)
        elif scope_key == "ide":
            page.category_order = ide_group_order.get(str(page.category or "").strip(), 100)
        else:
            page.category_order = 100
        page.order = page_order_by_id.get(str(page.id or "").strip().lower(), 100)


def create_default_settings_schema(theme_options: list[str] | None = None) -> SettingsSchema:
    resolved_theme_options = [str(option) for option in (theme_options or []) if str(option).strip()]
    schema = SettingsSchema(
        pages=[
            SchemaPage(
                id="ide-keybindings",
                category="Editor",
                title="Keybindings",
                scope="ide",
                description="Customize global and language-specific keyboard shortcuts.",
                keywords=["shortcut", "keybinding", "hotkey", "keys"],
                sections=[
                    SchemaSection(
                        title="Keyboard Shortcuts",
                        fields=[
                            SchemaField(
                                id="ide-keybindings-editor",
                                key="keybindings",
                                label="Keybindings Editor",
                                type="keybindings_editor",
                                scope="ide",
                            )
                        ],
                    )
                ],
            ),
            SchemaPage(
                id="ide-run",
                category="Execution",
                title="Run",
                scope="ide",
                description="IDE run behavior and execution defaults.",
                keywords=["run", "execution", "cwd", "ide"],
                sections=[
                    SchemaSection(
                        title="Execution",
                        fields=[
                            SchemaField(
                                id="run-default-cwd",
                                key="run.default_cwd",
                                label="Default Working Directory",
                                type="path_dir",
                                scope="ide",
                                description="Directory used when running files from this IDE instance.",
                            ),
                            SchemaField(
                                id="run-auto-save",
                                key="run.auto_save_before_run",
                                label="Auto Save Before Run",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="run-reuse-tab",
                                key="run.reuse_existing_output_tab",
                                label="Reuse Existing Output Tab",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="run-clear-output",
                                key="run.clear_output_before_run",
                                label="Clear Output Before Run",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="run-focus-output",
                                key="run.focus_output_on_run",
                                label="Focus Output On Run",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="run-clear-terminal",
                                key="run.clear_terminal_before_run",
                                label="Clear Terminal Before Run",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="run-show-terminal-toolbar",
                                key="run.show_terminal_toolbar",
                                label="Show Terminal Toolbar",
                                type="checkbox",
                                scope="ide",
                                description="Show Copy/Paste/Clear and Quick Commands in terminal tabs.",
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="C/C++ (CMake)",
                        fields=[
                            SchemaField(
                                id="run-cmake-build-dir",
                                key="run.cmake.build_dir",
                                label="Build Directory",
                                type="lineedit",
                                scope="ide",
                                description="Relative to CMake project root (or absolute path).",
                            ),
                            SchemaField(
                                id="run-cmake-build-type",
                                key="run.cmake.build_type",
                                label="Build Type",
                                type="lineedit",
                                scope="ide",
                                description="Examples: Debug, Release, RelWithDebInfo.",
                            ),
                            SchemaField(
                                id="run-cmake-target",
                                key="run.cmake.target",
                                label="Target (optional)",
                                type="lineedit",
                                scope="ide",
                                description="If empty, IDE tries to auto-detect a runnable executable.",
                            ),
                            SchemaField(
                                id="run-cmake-parallel",
                                key="run.cmake.parallel_jobs",
                                label="Parallel Build Jobs (0 = auto)",
                                type="spin",
                                scope="ide",
                                min=0,
                                max=128,
                            ),
                            SchemaField(
                                id="run-cmake-configure-args",
                                key="run.cmake.configure_args",
                                label="Extra Configure Args",
                                type="lineedit",
                                scope="ide",
                                description="Extra args passed to `cmake -S ... -B ...`.",
                            ),
                            SchemaField(
                                id="run-cmake-build-args",
                                key="run.cmake.build_args",
                                label="Extra Build Args",
                                type="lineedit",
                                scope="ide",
                                description="Extra args passed after `cmake --build ... --`.",
                            ),
                            SchemaField(
                                id="run-cmake-run-args",
                                key="run.cmake.run_args",
                                label="Program Args",
                                type="lineedit",
                                scope="ide",
                                description="Arguments passed to the compiled executable.",
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="ide-file-templates",
                category="Editor",
                title="File Templates",
                scope="ide",
                description="Configure Project Explorer New File templates and menu groupings.",
                keywords=["new file", "template", "file templates", "project explorer", "boilerplate"],
                sections=[
                    SchemaSection(
                        title="New File Templates",
                        fields=[
                            SchemaField(
                                id="ide-file-templates-editor",
                                key="file_templates",
                                label="File Templates Editor",
                                type="file_templates_editor",
                                scope="ide",
                            )
                        ],
                    )
                ],
            ),
            SchemaPage(
                id="ide-startup-projects",
                category="General",
                title="Startup / Projects",
                scope="ide",
                description="Project startup behavior, recent-project history, and file autosave.",
                keywords=["startup", "projects", "recent", "autosave", "ide"],
                sections=[
                    SchemaSection(
                        title="Projects",
                        fields=[
                            SchemaField(
                                id="ide-open-last-project",
                                key="projects.open_last_project",
                                label="Open Last Project On Startup",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-max-recent-projects",
                                key="projects.max_recent_projects",
                                label="Recent Projects To Keep",
                                type="spin",
                                scope="ide",
                                min=1,
                                max=50,
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Auto Save",
                        fields=[
                            SchemaField(
                                id="ide-autosave-enabled",
                                key="autosave.enabled",
                                label="Enable Auto Save",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-autosave-debounce",
                                key="autosave.debounce_ms",
                                label="Auto Save Debounce (ms)",
                                type="spin",
                                scope="ide",
                                min=250,
                                max=30000,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="project-general",
                category="General",
                title="Metadata",
                scope="project",
                description="Core project metadata saved in project.json.",
                keywords=["project", "name", "metadata"],
                sections=[
                    SchemaSection(
                        title="Project Metadata",
                        fields=[
                            SchemaField(
                                id="project-name",
                                key="project_name",
                                label="Project Name",
                                type="lineedit",
                                scope="project",
                                description="Display name used in explorer title and window context.",
                            ),
                            SchemaField(
                                id="project-read-only",
                                key="read_only",
                                label="Read Only",
                                type="checkbox",
                                scope="project",
                                description="Disable file edits and write operations in this project.",
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="project-interpreters",
                category="Languages",
                title="Python",
                scope="project",
                description="Interpreter selection for this workspace and Python-specific fallback behavior.",
                keywords=["python", "interpreter", "venv", "project"],
                sections=[
                    SchemaSection(
                        title="Interpreter Resolution",
                        fields=[
                            SchemaField(
                                id="interpreter-default",
                                key="interpreters.default",
                                label="Default Interpreter",
                                type="lineedit",
                                scope="project",
                                description="Command or absolute path for default Python interpreter.",
                            ),
                            SchemaField(
                                id="interpreter-by-directory",
                                key="interpreters.by_directory",
                                label="Directory Overrides (JSON)",
                                type="json",
                                scope="project",
                                description=(
                                    "List of objects: [{\"path\":\"src\",\"python\":\"...\",\"exclude_from_indexing\":false}]"
                                ),
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Advanced",
                        fields=[
                            SchemaField(
                                id="project-interpreter-legacy",
                                key="interpreter",
                                label="Legacy Interpreter Fallback",
                                type="lineedit",
                                scope="project",
                                description="Used only if no interpreter defaults/overrides resolve.",
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="project-cpp",
                category="Languages",
                title="C/C++",
                scope="project",
                description="clangd-based language intelligence settings for C/C++ files.",
                keywords=["c", "cpp", "c++", "clangd", "compile_commands", "lsp"],
                sections=[
                    SchemaSection(
                        title="clangd",
                        fields=[
                            SchemaField(
                                id="project-cpp-enabled",
                                key="c_cpp.enable_cpp",
                                label="Enable C/C++ Language Support",
                                type="checkbox",
                                scope="project",
                            ),
                            SchemaField(
                                id="project-cpp-clangd-path",
                                key="c_cpp.clangd_path",
                                label="clangd Executable",
                                type="lineedit",
                                scope="project",
                                description="Command/path used to launch clangd.",
                            ),
                            SchemaField(
                                id="project-cpp-query-driver",
                                key="c_cpp.query_driver",
                                label="query-driver (optional)",
                                type="lineedit",
                                scope="project",
                                description="Comma-separated compiler paths/globs for include extraction. Leave blank for auto; set 'off' to disable.",
                            ),
                            SchemaField(
                                id="project-cpp-compile-mode",
                                key="c_cpp.compile_commands_mode",
                                label="compile_commands Mode",
                                type="combo",
                                scope="project",
                                options=[
                                    {"label": "Auto Discover", "value": "auto"},
                                    {"label": "Manual Path", "value": "manual"},
                                ],
                            ),
                            SchemaField(
                                id="project-cpp-compile-path",
                                key="c_cpp.compile_commands_path",
                                label="Manual compile_commands Path",
                                type="lineedit",
                                scope="project",
                                description="Directory containing compile_commands.json, or the JSON file path.",
                            ),
                            SchemaField(
                                id="project-cpp-log-traffic",
                                key="c_cpp.log_lsp_traffic",
                                label="Log LSP Traffic To Status Bar",
                                type="checkbox",
                                scope="project",
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Fallback Flags (No compile_commands)",
                        fields=[
                            SchemaField(
                                id="project-cpp-c-standard",
                                key="c_cpp.fallback.c_standard",
                                label="C Standard",
                                type="lineedit",
                                scope="project",
                                description="Example: c17",
                            ),
                            SchemaField(
                                id="project-cpp-cpp-standard",
                                key="c_cpp.fallback.cpp_standard",
                                label="C++ Standard",
                                type="lineedit",
                                scope="project",
                                description="Example: c++20",
                            ),
                            SchemaField(
                                id="project-cpp-include-paths",
                                key="c_cpp.fallback.include_paths",
                                label="Include Paths",
                                type="list_str",
                                scope="project",
                                description="Each entry becomes a -I flag when compile_commands is unavailable.",
                            ),
                            SchemaField(
                                id="project-cpp-defines",
                                key="c_cpp.fallback.defines",
                                label="Preprocessor Defines",
                                type="list_str",
                                scope="project",
                                description="Entries become -D flags.",
                            ),
                            SchemaField(
                                id="project-cpp-extra-flags",
                                key="c_cpp.fallback.extra_flags",
                                label="Extra Flags",
                                type="list_str",
                                scope="project",
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Repair",
                        fields=[
                            SchemaField(
                                id="project-cpp-repair-tools",
                                key="c_cpp.repair_tools",
                                label="Clangd Repair Tools",
                                type="clangd_repair_tools",
                                scope="project",
                                description="Detect and repair missing C/C++ standard include paths for clangd.",
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="project-rust",
                category="Languages",
                title="Rust",
                scope="project",
                description="rust-analyzer language support settings for Rust files.",
                keywords=["rust", "cargo", "rust-analyzer", "lsp"],
                sections=[
                    SchemaSection(
                        title="rust-analyzer",
                        fields=[
                            SchemaField(
                                id="project-rust-enabled",
                                key="rust.enable_rust",
                                label="Enable Rust Language Support",
                                type="checkbox",
                                scope="project",
                            ),
                            SchemaField(
                                id="project-rust-analyzer-path",
                                key="rust.rust_analyzer_path",
                                label="rust-analyzer Executable",
                                type="lineedit",
                                scope="project",
                                description="Command/path used to launch rust-analyzer.",
                            ),
                            SchemaField(
                                id="project-rust-analyzer-args",
                                key="rust.rust_analyzer_args",
                                label="rust-analyzer Args",
                                type="list_str",
                                scope="project",
                            ),
                            SchemaField(
                                id="project-rust-change-debounce",
                                key="rust.did_change_debounce_ms",
                                label="didChange Debounce (ms)",
                                type="spin",
                                scope="project",
                                min=120,
                                max=3000,
                            ),
                            SchemaField(
                                id="project-rust-log-traffic",
                                key="rust.log_lsp_traffic",
                                label="Log LSP Traffic To Status Bar",
                                type="checkbox",
                                scope="project",
                            ),
                            SchemaField(
                                id="project-rust-init-options",
                                key="rust.initialization_options",
                                label="Initialization Options (JSON)",
                                type="json",
                                scope="project",
                                description="Optional rust-analyzer initialization options object.",
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="project-build-configs",
                category="Execution",
                subcategory="Build",
                title="C/C++",
                scope="project",
                description="Named CMake build/run presets for this project.",
                keywords=["build", "cmake", "preset", "configuration", "project"],
                sections=[
                    SchemaSection(
                        title="CMake Build Configurations",
                        fields=[
                            SchemaField(
                                id="project-build-configs-editor",
                                key="build.cmake",
                                label="CMake Build Configurations",
                                type="build_configs_editor",
                                scope="project",
                            )
                        ],
                    )
                ],
            ),
            SchemaPage(
                id="project-run-configs",
                category="Execution",
                subcategory="Run",
                title="Configurations",
                scope="project",
                description="Named run presets for the current project.",
                keywords=["run", "configuration", "project", "args", "env"],
                sections=[
                    SchemaSection(
                        title="Python Run Configurations",
                        fields=[
                            SchemaField(
                                id="project-run-configs-editor",
                                key="build.python",
                                label="Python Run Configurations",
                                type="python_run_configs_editor",
                                scope="project",
                            )
                        ],
                    )
                ],
            ),
            SchemaPage(
                id="project-rust-run-configs",
                category="Execution",
                subcategory="Build",
                title="Rust (Cargo)",
                scope="project",
                description="Named Cargo run/test/build/custom presets for this project.",
                keywords=["rust", "cargo", "run", "test", "build", "configuration"],
                sections=[
                    SchemaSection(
                        title="Cargo Run Configurations",
                        fields=[
                            SchemaField(
                                id="project-rust-run-configs-editor",
                                key="build.rust",
                                label="Cargo Run Configurations",
                                type="rust_run_configs_editor",
                                scope="project",
                            )
                        ],
                    )
                ],
            ),
            SchemaPage(
                id="project-indexing",
                category="General",
                title="Indexing",
                scope="project",
                description="Indexing and exclusion rules shared by this project.",
                keywords=["index", "exclude", "symlink", "project"],
                sections=[
                    SchemaSection(
                        title="Indexing Policy",
                        fields=[
                            SchemaField(
                                id="indexing-exclude-dirs",
                                key="indexing.exclude_dirs",
                                label="Excluded Directories (names/patterns)",
                                type="list_str",
                                scope="project",
                                description="One filter per item. Supports glob patterns (example: build-*).",
                            ),
                            SchemaField(
                                id="indexing-exclude-files",
                                key="indexing.exclude_files",
                                label="Excluded Files (names/patterns)",
                                type="list_str",
                                scope="project",
                                description="One filter per item. Examples: *.lock, .tide/project.json",
                            ),
                            SchemaField(
                                id="indexing-follow-symlinks",
                                key="indexing.follow_symlinks",
                                label="Follow Symbolic Links",
                                type="checkbox",
                                scope="project",
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Project Explorer Visibility",
                        fields=[
                            SchemaField(
                                id="explorer-exclude-dirs",
                                key="explorer.exclude_dirs",
                                label="Explorer Hidden Directories (names/patterns)",
                                type="list_str",
                                scope="project",
                                description="One filter per item. Supports glob patterns (example: build-*).",
                            ),
                            SchemaField(
                                id="explorer-exclude-files",
                                key="explorer.exclude_files",
                                label="Explorer Hidden Files (names/patterns)",
                                type="list_str",
                                scope="project",
                                description="One filter per item. Examples: *.lock, .tide/project.json",
                            ),
                            SchemaField(
                                id="explorer-hide-indexing-excluded",
                                key="explorer.hide_indexing_excluded",
                                label="Also Hide Indexing-Excluded Paths",
                                type="checkbox",
                                scope="project",
                                description="When enabled, indexing policy exclusions are hidden in the project explorer.",
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="project-maintenance",
                category="Maintenance",
                title="Maintenance",
                scope="project",
                description="Project-local IDE storage and cache maintenance.",
                keywords=["project", "maintenance", "cache", "ruff", "tide"],
                sections=[
                    SchemaSection(
                        title="Cache Maintenance",
                        fields=[
                            SchemaField(
                                id="project-maintenance-tools",
                                key="maintenance.tools",
                                label="Maintenance Tools",
                                type="project_maintenance_tools",
                                scope="project",
                            )
                        ],
                    )
                ],
            ),
            SchemaPage(
                id="ide-appearance",
                category="General",
                title="Appearance",
                scope="ide",
                description="Machine-local look and feel preferences.",
                keywords=["theme", "font", "ui", "ide"],
                sections=[
                    SchemaSection(
                        title="Theme",
                        fields=[
                            SchemaField(
                                id="ide-theme",
                                key="theme",
                                label="Theme",
                                type="combo",
                                scope="ide",
                                options=resolved_theme_options,
                            ),
                            SchemaField(
                                id="ide-font-size",
                                key="font_size",
                                label="Editor Font Size",
                                type="spin",
                                scope="ide",
                                min=6,
                                max=48,
                            ),
                            SchemaField(
                                id="ide-font-family",
                                key="font_family",
                                label="Editor Font Family",
                                type="font_family",
                                scope="ide",
                                description="Monospace font used by the code editor.",
                            ),
                            SchemaField(
                                id="ide-tree-font-size",
                                key="tree_font_size",
                                label="Tree Font Size",
                                type="spin",
                                scope="ide",
                                min=6,
                                max=48,
                            ),
                            SchemaField(
                                id="ide-tree-font-family",
                                key="tree_font_family",
                                label="Tree Font Family",
                                type="font_family",
                                scope="ide",
                                description="Font used by project and navigation trees.",
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Editor Background",
                        fields=[
                            SchemaField(
                                id="ide-editor-bg-color",
                                key="editor.background_color",
                                label="Editor Background Color",
                                type="color",
                                scope="ide",
                                description="Hex color like #252526.",
                            ),
                            SchemaField(
                                id="ide-editor-bg-image",
                                key="editor.background_image_path",
                                label="Editor Background Image",
                                type="path_file",
                                scope="ide",
                                description="Optional image path for editor background.",
                            ),
                            SchemaField(
                                id="ide-editor-bg-scale-mode",
                                key="editor.background_image_scale_mode",
                                label="Editor Image Scale Mode",
                                type="combo",
                                scope="ide",
                                options=[
                                    {"label": "Stretch", "value": "stretch"},
                                    {"label": "Fit Width", "value": "fit_width"},
                                    {"label": "Fit Height", "value": "fit_height"},
                                    {"label": "Tile", "value": "tile"},
                                ],
                            ),
                            SchemaField(
                                id="ide-editor-bg-brightness",
                                key="editor.background_image_brightness",
                                label="Editor Image Brightness (%)",
                                type="spin",
                                scope="ide",
                                min=0,
                                max=200,
                            ),
                            SchemaField(
                                id="ide-editor-bg-tint-color",
                                key="editor.background_tint_color",
                                label="Editor Tint Color",
                                type="color",
                                scope="ide",
                                description="Hex color like #000000.",
                            ),
                            SchemaField(
                                id="ide-editor-bg-tint-strength",
                                key="editor.background_tint_strength",
                                label="Editor Tint Strength (%)",
                                type="spin",
                                scope="ide",
                                min=0,
                                max=100,
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Editor Change Regions",
                        fields=[
                            SchemaField(
                                id="ide-editor-dirty-bg",
                                key="editor.editor_dirty_background",
                                label="Unsaved Change Background",
                                type="color",
                                scope="ide",
                                description=(
                                    "Hex color like #ffcc0030 for in-editor unsaved changes "
                                    "(disk vs live buffer)."
                                ),
                            ),
                            SchemaField(
                                id="ide-editor-uncommitted-bg",
                                key="editor.editor_uncommitted_background",
                                label="Git Uncommitted Background",
                                type="color",
                                scope="ide",
                                description=(
                                    "Hex color like #ff4d4d24 for in-editor saved-but-uncommitted "
                                    "changes (HEAD vs disk)."
                                ),
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Editor Gutter",
                        fields=[
                            SchemaField(
                                id="ide-editor-gutter-bg",
                                key="editor.gutter_background_color",
                                label="Gutter Background Color",
                                type="color",
                                scope="ide",
                                description=(
                                    "Hex color like #1d232c. Leave blank to auto-derive from editor background."
                                ),
                            ),
                            SchemaField(
                                id="ide-editor-gutter-fg",
                                key="editor.gutter_foreground_color",
                                label="Gutter Foreground Color",
                                type="color",
                                scope="ide",
                                description=(
                                    "Line-number color. Leave blank to auto-derive from gutter background."
                                ),
                            ),
                            SchemaField(
                                id="ide-editor-gutter-fg-active",
                                key="editor.gutter_active_foreground_color",
                                label="Active Line Number Color",
                                type="color",
                                scope="ide",
                                description=(
                                    "Current-line number color. Leave blank to use the default behavior."
                                ),
                            ),
                            SchemaField(
                                id="ide-editor-gutter-fold-marker",
                                key="editor.gutter_fold_marker_color",
                                label="Fold Marker Color",
                                type="color",
                                scope="ide",
                                description=(
                                    "Fold triangle color. Leave blank to auto-derive from line-number color."
                                ),
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Reusable File Dialog",
                        fields=[
                            SchemaField(
                                id="ide-file-dialog-bg-image",
                                key="file_dialog.background_image_path",
                                label="Dialog Background Image",
                                type="path_file",
                                scope="ide",
                                description="Optional image path for reusable file dialogs.",
                            ),
                            SchemaField(
                                id="ide-file-dialog-scale-mode",
                                key="file_dialog.background_scale_mode",
                                label="Dialog Image Scale Mode",
                                type="combo",
                                scope="ide",
                                options=[
                                    {"label": "Stretch", "value": "stretch"},
                                    {"label": "Fit Width", "value": "fit_width"},
                                    {"label": "Fit Height", "value": "fit_height"},
                                    {"label": "Tile", "value": "tile"},
                                ],
                            ),
                            SchemaField(
                                id="ide-file-dialog-bg-brightness",
                                key="file_dialog.background_brightness",
                                label="Dialog Image Brightness (%)",
                                type="spin",
                                scope="ide",
                                min=0,
                                max=200,
                            ),
                            SchemaField(
                                id="ide-file-dialog-tint-color",
                                key="file_dialog.tint_color",
                                label="Dialog Tint Color",
                                type="color",
                                scope="ide",
                                description="Hex color like #000000.",
                            ),
                            SchemaField(
                                id="ide-file-dialog-tint-strength",
                                key="file_dialog.tint_strength",
                                label="Dialog Tint Strength (%)",
                                type="spin",
                                scope="ide",
                                min=0,
                                max=100,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="ide-window",
                category="General",
                title="Window",
                scope="ide",
                description="Window chrome and desktop integration preferences.",
                keywords=["window", "chrome", "native", "ide"],
                sections=[
                    SchemaSection(
                        title="Window",
                        fields=[
                            SchemaField(
                                id="ide-native-chrome",
                                key="window.use_native_chrome",
                                label="Use Native Window Chrome",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-custom-toolbar-title",
                                key="window.show_title_in_custom_toolbar",
                                label="Show Title In Custom Toolbar",
                                type="checkbox",
                                scope="ide",
                            ),
                        ],
                    )
                ],
            ),
            SchemaPage(
                id="ide-linting",
                category="Code Intelligence",
                title="Linting",
                scope="ide",
                description="IDE linting preferences and backend defaults.",
                keywords=["lint", "ruff", "pyflakes", "ast", "diagnostics", "ide"],
                sections=[
                    SchemaSection(
                        title="Lint Engine",
                        fields=[
                            SchemaField(
                                id="ide-lint-enabled",
                                key="lint.enabled",
                                label="Enable Linting",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-lint-backend",
                                key="lint.backend",
                                label="Backend",
                                type="combo",
                                scope="ide",
                                options=["ruff", "pyflakes", "ast"],
                            ),
                            SchemaField(
                                id="ide-lint-fallback",
                                key="lint.fallback_backend",
                                label="Fallback Backend",
                                type="combo",
                                scope="ide",
                                options=["none", "ruff", "pyflakes", "ast"],
                            ),
                            SchemaField(
                                id="ide-lint-run-on-idle",
                                key="lint.run_on_idle",
                                label="Run On Idle",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-lint-run-on-save",
                                key="lint.run_on_save",
                                label="Run On Save",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-lint-respect-excludes",
                                key="lint.respect_excludes",
                                label="Respect Excludes",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-lint-debounce",
                                key="lint.debounce_ms",
                                label="Debounce (ms)",
                                type="spin",
                                scope="ide",
                                min=100,
                                max=5000,
                            ),
                            SchemaField(
                                id="ide-lint-max-problems",
                                key="lint.max_problems_per_file",
                                label="Max Problems Per File",
                                type="spin",
                                scope="ide",
                                min=1,
                                max=5000,
                            ),
                            SchemaField(
                                id="ide-lint-args",
                                key="lint.args",
                                label="Backend Arguments (JSON)",
                                type="json",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-lint-severity-overrides",
                                key="lint.severity_overrides",
                                label="Severity Overrides (JSON)",
                                type="json",
                                scope="ide",
                                description=(
                                    "Map rule codes/prefixes to severity. "
                                    "Example: {\"F401\":\"warning\", \"F*\":\"info\", \"E*\":\"error\"}."
                                ),
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Diagnostic Visuals",
                        fields=[
                            SchemaField(
                                id="ide-lint-visual-mode",
                                key="lint.visuals.mode",
                                label="Diagnostic Style",
                                type="combo",
                                scope="ide",
                                options=[
                                    {"label": "Squiggles", "value": "squiggle"},
                                    {"label": "Line Highlight", "value": "line"},
                                    {"label": "Both", "value": "both"},
                                ],
                            ),
                            SchemaField(
                                id="ide-lint-visual-error-color",
                                key="lint.visuals.error_color",
                                label="Error Color",
                                type="color",
                                scope="ide",
                                description="Hex color like #E35D6A.",
                            ),
                            SchemaField(
                                id="ide-lint-visual-warning-color",
                                key="lint.visuals.warning_color",
                                label="Warning Color",
                                type="color",
                                scope="ide",
                                description="Hex color like #D6A54A.",
                            ),
                            SchemaField(
                                id="ide-lint-visual-info-color",
                                key="lint.visuals.info_color",
                                label="Info Color",
                                type="color",
                                scope="ide",
                                description="Hex color like #6AA1FF.",
                            ),
                            SchemaField(
                                id="ide-lint-visual-hint-color",
                                key="lint.visuals.hint_color",
                                label="Hint Color",
                                type="color",
                                scope="ide",
                                description="Hex color like #8F9AA5.",
                            ),
                            SchemaField(
                                id="ide-lint-visual-squiggle-thickness",
                                key="lint.visuals.squiggle_thickness",
                                label="Squiggle Thickness",
                                type="spin",
                                scope="ide",
                                min=1,
                                max=6,
                                description="Best-effort thickness for diagnostic squiggles.",
                            ),
                            SchemaField(
                                id="ide-lint-visual-line-alpha",
                                key="lint.visuals.line_alpha",
                                label="Line Highlight Opacity",
                                type="spin",
                                scope="ide",
                                min=0,
                                max=255,
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="ide-editor-ux",
                category="Editor",
                title="Editor UX",
                scope="ide",
                description="Completion behavior and UI preferences for this IDE instance.",
                keywords=["completion", "tooltip", "signature", "indent", "tabs", "spaces", "ide"],
                sections=[
                    SchemaSection(
                        title="Completion Behavior",
                        fields=[
                            SchemaField(
                                id="ide-completion-enabled",
                                key="completion.enabled",
                                label="Enable Completion",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-completion-respect-excludes",
                                key="completion.respect_excludes",
                                label="Respect Excludes",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-completion-auto-trigger",
                                key="completion.auto_trigger",
                                label="Auto Trigger",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-completion-after-dot",
                                key="completion.auto_trigger_after_dot",
                                label="Auto Trigger After Dot",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-completion-min-chars",
                                key="completion.auto_trigger_min_chars",
                                label="Auto Trigger Min Chars",
                                type="spin",
                                scope="ide",
                                min=1,
                                max=10,
                            ),
                            SchemaField(
                                id="ide-completion-debounce",
                                key="completion.debounce_ms",
                                label="Debounce (ms)",
                                type="spin",
                                scope="ide",
                                min=40,
                                max=3000,
                            ),
                            SchemaField(
                                id="ide-completion-backend",
                                key="completion.backend",
                                label="Backend",
                                type="combo",
                                scope="ide",
                                options=["jedi"],
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Completion UI",
                        fields=[
                            SchemaField(
                                id="ide-completion-max-items",
                                key="completion.max_items",
                                label="Max Completion Items",
                                type="spin",
                                scope="ide",
                                min=5,
                                max=1000,
                            ),
                            SchemaField(
                                id="ide-completion-case",
                                key="completion.case_sensitive",
                                label="Case Sensitive Completion",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-completion-signatures",
                                key="completion.show_signatures",
                                label="Show Signatures",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-completion-right-label",
                                key="completion.show_right_label",
                                label="Show Right Label",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-completion-doc-tooltip",
                                key="completion.show_doc_tooltip",
                                label="Show Documentation Tooltip",
                                type="checkbox",
                                scope="ide",
                            ),
                            SchemaField(
                                id="ide-completion-doc-delay",
                                key="completion.doc_tooltip_delay_ms",
                                label="Doc Tooltip Delay (ms)",
                                type="spin",
                                scope="ide",
                                min=120,
                                max=1200,
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Indentation",
                        fields=[
                            SchemaField(
                                id="ide-editor-use-tabs",
                                key="editor.use_tabs",
                                label="Use Hard Tabs",
                                type="checkbox",
                                scope="ide",
                                description="When enabled, Tab inserts \\t; otherwise spaces are used.",
                            ),
                            SchemaField(
                                id="ide-editor-indent-width",
                                key="editor.indent_width",
                                label="Indent Width",
                                type="spin",
                                scope="ide",
                                min=1,
                                max=8,
                                description="Spaces per indent level and visual width for tab-aware indentation.",
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Spell Check",
                        fields=[
                            SchemaField(
                                id="ide-editor-spellcheck-enabled",
                                key="editor.spellcheck.enabled",
                                label="Enable Spell Check",
                                type="checkbox",
                                scope="ide",
                                description="Checks only the active editor tab.",
                            ),
                            SchemaField(
                                id="ide-editor-spellcheck-color",
                                key="editor.spellcheck.color",
                                label="Squiggle Color",
                                type="color",
                                scope="ide",
                                description="Hex color like #66C07A.",
                            ),
                            SchemaField(
                                id="ide-editor-spellcheck-debounce",
                                key="editor.spellcheck.debounce_ms",
                                label="Debounce (ms)",
                                type="spin",
                                scope="ide",
                                min=120,
                                max=2400,
                                description="Delay before re-checking while typing.",
                            ),
                            SchemaField(
                                id="ide-editor-spellcheck-identifiers",
                                key="editor.spellcheck.check_identifiers_in_code",
                                label="Check Code Identifiers",
                                type="checkbox",
                                scope="ide",
                                description="When disabled, code checks comments and string literals only.",
                            ),
                            SchemaField(
                                id="ide-editor-spellcheck-max-highlights",
                                key="editor.spellcheck.max_highlights",
                                label="Max Misspelling Squiggles",
                                type="spin",
                                scope="ide",
                                min=100,
                                max=5000,
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="File Creation",
                        fields=[
                            SchemaField(
                                id="ide-editor-open-created-files",
                                key="editor.open_created_files",
                                label="Open Newly Created Files",
                                type="checkbox",
                                scope="ide",
                                description="When enabled, files created from New File actions open in the editor automatically.",
                            ),
                        ],
                    ),
                    SchemaSection(
                        title="Occurrence Highlights",
                        fields=[
                            SchemaField(
                                id="ide-editor-max-occurrence-highlights",
                                key="editor.max_occurrence_highlights",
                                label="Max Occurrence Highlights",
                                type="spin",
                                scope="ide",
                                min=0,
                                max=20000,
                                description="Cap on in-editor occurrence highlight ranges for the symbol/selection under cursor.",
                            ),
                            SchemaField(
                                id="ide-editor-occurrence-highlight-alpha",
                                key="editor.occurrence_highlight_alpha",
                                label="Occurrence Highlight Opacity",
                                type="spin",
                                scope="ide",
                                min=0,
                                max=255,
                                description="Alpha channel for occurrence highlight fill color.",
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="ide-syntax-highlighting",
                category="Editor",
                title="Syntax Highlighting",
                scope="ide",
                description="Customize syntax token colors globally or per language.",
                keywords=["syntax", "highlighting", "colors", "theme", "tokens"],
                sections=[
                    SchemaSection(
                        title="Syntax Colors",
                        fields=[
                            SchemaField(
                                id="ide-syntax-highlighting-editor",
                                key="editor.syntax_highlighting",
                                label="Syntax Highlighting Colors",
                                type="syntax_highlighting_editor",
                                scope="ide",
                            )
                        ],
                    )
                ],
            ),
            SchemaPage(
                id="ide-ai-assist",
                category="Code Intelligence",
                title="AI Assist",
                scope="ide",
                description="Inline AI completion settings and provider connection details.",
                keywords=[
                    "ai",
                    "assist",
                    "inline",
                    "openai",
                    "models",
                    "endpoint",
                    "ghost text",
                ],
                sections=[
                    SchemaSection(
                        title="AI Assist",
                        fields=[
                            SchemaField(
                                id="ide-ai-assist-editor",
                                key="ai_assist",
                                label="AI Assist Editor",
                                type="ai_assist_editor",
                                scope="ide",
                            )
                        ],
                    )
                ],
            ),
            SchemaPage(
                id="ide-code-agents",
                category="Code Intelligence",
                title="Code Agents",
                scope="ide",
                description="Configure command settings for docked external coding agents.",
                keywords=["codex", "agent", "command", "dock", "cli"],
                sections=[
                    SchemaSection(
                        title="Codex CLI",
                        fields=[
                            SchemaField(
                                id="ide-code-agents-command-template",
                                key="codex_agent.command_template",
                                label="Codex Command Template",
                                type="lineedit",
                                scope="ide",
                                browse_provider_id="codex_command",
                                browse_caption="Select Codex Binary",
                                browse_file_filter="All Files (*)",
                                browse_button_text="Browse",
                                description=(
                                    "Command used by the Codex Agent dock. Supports {project}. "
                                    "You can enter just 'codex' or a codex binary path; the dock adds exec mode automatically."
                                ),
                            ),
                            SchemaField(
                                id="ide-code-agents-auto-skip-git-check",
                                key="codex_agent.auto_skip_git_repo_check",
                                label="Auto-add --skip-git-repo-check For Non-Git Projects",
                                type="checkbox",
                                scope="ide",
                                description=(
                                    "When enabled, the dock adds --skip-git-repo-check for projects not under source control "
                                    "and shows a one-time warning at the start of a new chat session."
                                ),
                            ),
                            SchemaField(
                                id="ide-code-agents-add-workspace-sandbox",
                                key="codex_agent.sandbox_mode",
                                label="Default Sandbox Mode",
                                type="combo",
                                scope="ide",
                                description=(
                                    "Select which --sandbox mode the Codex Agent uses for turns."
                                ),
                                options=[
                                    {"label": "read-only", "value": "read-only"},
                                    {"label": "workspace-write", "value": "workspace-write"},
                                    {"label": "danger-full-access", "value": "danger-full-access"},
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            SchemaPage(
                id="ide-github",
                category="Integrations",
                title="GitHub",
                scope="ide",
                description="GitHub token sign-in and repository cloning configuration.",
                keywords=["github", "git", "token", "clone", "repository"],
                sections=[
                    SchemaSection(
                        title="GitHub",
                        fields=[
                            SchemaField(
                                id="ide-github-editor",
                                key="github",
                                label="GitHub Settings",
                                type="github_editor",
                                scope="ide",
                            )
                        ],
                    )
                ],
            ),
            SchemaPage(
                id="ide-git",
                category="Integrations",
                title="Git",
                scope="ide",
                description="Git project explorer tinting and source control defaults.",
                keywords=["git", "scm", "status", "color", "tint"],
                sections=[
                    SchemaSection(
                        title="Git",
                        fields=[
                            SchemaField(
                                id="ide-git-editor",
                                key="git",
                                label="Git Settings",
                                type="git_editor",
                                scope="ide",
                            )
                        ],
                    )
                ],
            ),
        ]
    )
    _apply_default_page_ordering(schema)
    return schema
