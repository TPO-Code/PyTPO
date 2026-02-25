from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.settings_models import SettingsScope, default_project_settings
from src.ui.dialogs.file_dialog_bridge import get_existing_directory, get_open_file_name


def _default_python_configs() -> list[dict[str, Any]]:
    build_defaults = default_project_settings().get("build", {})
    python_defaults = build_defaults.get("python", {}) if isinstance(build_defaults, dict) else {}
    raw = python_defaults.get("run_configs", []) if isinstance(python_defaults, dict) else []
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                out.append(deepcopy(item))
    return out


class PythonRunConfigsSettingsPage(QWidget):
    CURRENT_FILE_LABEL = "Run Current File"

    def __init__(self, *, manager: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._loading = False
        self._base_configs: list[dict[str, Any]] = []
        self._base_active_name: str = ""
        self._working_configs: list[dict[str, Any]] = []
        self._working_active_name: str = ""
        self._last_selected_name: str = ""
        self._editor_row: int = -1

        self._build_ui()
        self._load_from_manager()
        self._refresh_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)
        top_row.addWidget(QLabel("Active Python Run Config:"))
        self.active_combo = QComboBox(self)
        self.active_combo.currentIndexChanged.connect(self._on_active_changed)
        top_row.addWidget(self.active_combo, 1)
        root.addLayout(top_row)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)

        left_group = QGroupBox("Run Configurations", self)
        left_layout = QVBoxLayout(left_group)
        left_layout.setContentsMargins(8, 10, 8, 8)
        left_layout.setSpacing(8)

        self.config_list = QListWidget(self)
        self.config_list.currentRowChanged.connect(self._on_config_selected)
        left_layout.addWidget(self.config_list, 1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.add_btn = QPushButton("Add", self)
        self.dup_btn = QPushButton("Duplicate", self)
        self.remove_btn = QPushButton("Remove", self)
        self.reset_btn = QPushButton("Reset", self)
        row.addWidget(self.add_btn)
        row.addWidget(self.dup_btn)
        row.addWidget(self.remove_btn)
        row.addWidget(self.reset_btn)
        left_layout.addLayout(row)

        self.add_btn.clicked.connect(self._add_config)
        self.dup_btn.clicked.connect(self._duplicate_config)
        self.remove_btn.clicked.connect(self._remove_config)
        self.reset_btn.clicked.connect(self._reset_defaults)

        body.addWidget(left_group, 0)

        right_group = QGroupBox("Selected Run Config", self)
        right_layout = QVBoxLayout(right_group)
        right_layout.setContentsMargins(8, 10, 8, 8)
        right_layout.setSpacing(8)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.name_edit = QLineEdit(self)

        script_holder = QWidget(self)
        script_row = QHBoxLayout(script_holder)
        script_row.setContentsMargins(0, 0, 0, 0)
        script_row.setSpacing(6)
        self.script_path_edit = QLineEdit(self)
        self.script_path_edit.setPlaceholderText("Relative to project root or absolute path")
        self.script_browse_btn = QPushButton("Browse", self)
        self.script_browse_btn.setFixedWidth(82)
        script_row.addWidget(self.script_path_edit, 1)
        script_row.addWidget(self.script_browse_btn)

        self.args_edit = QLineEdit(self)
        self.working_dir_edit = QLineEdit(self)
        self.working_dir_edit.setPlaceholderText("Optional. Defaults to policy/script folder.")
        self.interpreter_edit = QLineEdit(self)
        self.interpreter_edit.setPlaceholderText("Optional. Defaults to project policy resolution.")

        wd_holder = QWidget(self)
        wd_row = QHBoxLayout(wd_holder)
        wd_row.setContentsMargins(0, 0, 0, 0)
        wd_row.setSpacing(6)
        self.wd_browse_btn = QPushButton("Browse", self)
        self.wd_browse_btn.setFixedWidth(82)
        wd_row.addWidget(self.working_dir_edit, 1)
        wd_row.addWidget(self.wd_browse_btn)

        self.env_edit = QTextEdit(self)
        self.env_edit.setPlaceholderText("One KEY=VALUE per line")
        self.env_edit.setMinimumHeight(110)

        form.addRow("Name", self.name_edit)
        form.addRow("Script", script_holder)
        form.addRow("Arguments", self.args_edit)
        form.addRow("Working Directory", wd_holder)
        form.addRow("Interpreter", self.interpreter_edit)
        form.addRow("Environment", self.env_edit)
        right_layout.addLayout(form)
        right_layout.addStretch(1)

        body.addWidget(right_group, 1)
        root.addLayout(body, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.name_edit.textChanged.connect(self._on_editor_changed)
        self.script_path_edit.textChanged.connect(self._on_editor_changed)
        self.args_edit.textChanged.connect(self._on_editor_changed)
        self.working_dir_edit.textChanged.connect(self._on_editor_changed)
        self.interpreter_edit.textChanged.connect(self._on_editor_changed)
        self.env_edit.textChanged.connect(self._on_editor_changed)
        self.script_browse_btn.clicked.connect(self._browse_script)
        self.wd_browse_btn.clicked.connect(self._browse_working_dir)

    def _browse_script(self) -> None:
        start = str(self.script_path_edit.text() or "").strip() or str(self._manager.paths.project_root)
        selected, _flt = get_open_file_name(
            parent=self,
            manager=self._manager,
            caption="Select Python Script",
            directory=start,
            file_filter="Python Files (*.py);;All Files (*)",
        )
        if selected:
            self.script_path_edit.setText(str(selected))

    def _browse_working_dir(self) -> None:
        start = str(self.working_dir_edit.text() or "").strip() or str(self._manager.paths.project_root)
        selected = get_existing_directory(
            parent=self,
            manager=self._manager,
            caption="Select Working Directory",
            directory=start,
        )
        if selected:
            self.working_dir_edit.setText(str(selected))

    def _load_from_manager(self) -> None:
        build_cfg = self._manager.get("build", scope_preference="project", default={})
        if not isinstance(build_cfg, dict):
            build_cfg = {}
        py_cfg = build_cfg.get("python", {}) if isinstance(build_cfg, dict) else {}
        if not isinstance(py_cfg, dict):
            py_cfg = {}
        raw_configs = py_cfg.get("run_configs")
        configs: list[dict[str, Any]] = []
        if isinstance(raw_configs, list):
            for idx, item in enumerate(raw_configs):
                configs.append(self._normalize_config(item, idx))
        if not configs:
            defaults = _default_python_configs()
            for idx, item in enumerate(defaults):
                configs.append(self._normalize_config(item, idx))
        self._base_configs = configs
        self._working_configs = deepcopy(configs)

        active = str(py_cfg.get("active_config") or "").strip()
        if active and active.lower() not in {str(item.get("name") or "").strip().lower() for item in self._working_configs}:
            active = str(self._working_configs[0].get("name") or "").strip() if self._working_configs else ""
        self._base_active_name = active
        self._working_active_name = active

    @staticmethod
    def _normalize_config(raw: Any, index: int) -> dict[str, Any]:
        cfg = raw if isinstance(raw, dict) else {}
        name = str(cfg.get("name") or "").strip() or f"Run Config {index + 1}"
        env_raw = cfg.get("env")
        if isinstance(env_raw, dict):
            env = [f"{str(k).strip()}={str(v or '')}" for k, v in env_raw.items() if str(k).strip()]
        elif isinstance(env_raw, list):
            env = [str(item).strip() for item in env_raw if str(item).strip()]
        else:
            env = []
        return {
            "name": name,
            "script_path": str(cfg.get("script_path") or "").strip(),
            "args": str(cfg.get("args") or "").strip(),
            "working_dir": str(cfg.get("working_dir") or "").strip(),
            "interpreter": str(cfg.get("interpreter") or "").strip(),
            "env": env,
        }

    def _refresh_ui(self) -> None:
        self._loading = True
        try:
            self.config_list.clear()
            for cfg in self._working_configs:
                self.config_list.addItem(QListWidgetItem(str(cfg.get("name") or "Run Config")))
            self.active_combo.clear()
            self.active_combo.addItem(self.CURRENT_FILE_LABEL, "")
            for cfg in self._working_configs:
                name = str(cfg.get("name") or "").strip()
                if name:
                    self.active_combo.addItem(name, name)
            idx = self.active_combo.findData(self._working_active_name, Qt.UserRole, Qt.MatchExactly)
            self.active_combo.setCurrentIndex(idx if idx >= 0 else 0)
            row = self._row_for_name(self._last_selected_name)
            if row < 0:
                row = self._row_for_name(self._working_active_name)
            if row < 0 and self.config_list.count() > 0:
                row = 0
            self.config_list.setCurrentRow(row)
            self._load_selected_into_editor()
        finally:
            self._loading = False
        self._refresh_status()

    def _row_for_name(self, name: str) -> int:
        target = str(name or "").strip().lower()
        if not target:
            return -1
        for idx, cfg in enumerate(self._working_configs):
            if str(cfg.get("name") or "").strip().lower() == target:
                return idx
        return -1

    def _current_index(self) -> int:
        row = int(self.config_list.currentRow())
        return row if 0 <= row < len(self._working_configs) else -1

    def _sync_current_config(self) -> None:
        row = int(self._editor_row)
        if 0 <= row < len(self._working_configs):
            self._working_configs[row] = self._editor_to_config(row)

    def _editor_to_config(self, row: int) -> dict[str, Any]:
        current = self._working_configs[row] if 0 <= row < len(self._working_configs) else {}
        name = str(self.name_edit.text() or "").strip() or str(current.get("name") or f"Run Config {row + 1}").strip()
        env = [line.strip() for line in str(self.env_edit.toPlainText() or "").splitlines() if line.strip()]
        return {
            "name": name,
            "script_path": str(self.script_path_edit.text() or "").strip(),
            "args": str(self.args_edit.text() or "").strip(),
            "working_dir": str(self.working_dir_edit.text() or "").strip(),
            "interpreter": str(self.interpreter_edit.text() or "").strip(),
            "env": env,
        }

    def _on_config_selected(self, _row: int) -> None:
        if self._loading:
            return
        self._sync_current_config()
        self._load_selected_into_editor()

    def _load_selected_into_editor(self) -> None:
        self._loading = True
        try:
            row = self._current_index()
            has_item = row >= 0
            for widget in (
                self.name_edit,
                self.script_path_edit,
                self.args_edit,
                self.working_dir_edit,
                self.interpreter_edit,
                self.env_edit,
            ):
                widget.setEnabled(has_item)
            self.script_browse_btn.setEnabled(has_item)
            self.wd_browse_btn.setEnabled(has_item)
            self.dup_btn.setEnabled(has_item)
            self.remove_btn.setEnabled(has_item and len(self._working_configs) > 0)
            if not has_item:
                self._editor_row = -1
                self.name_edit.setText("")
                self.script_path_edit.setText("")
                self.args_edit.setText("")
                self.working_dir_edit.setText("")
                self.interpreter_edit.setText("")
                self.env_edit.setPlainText("")
                return
            cfg = self._working_configs[row]
            self._editor_row = row
            self._last_selected_name = str(cfg.get("name") or "").strip()
            self.name_edit.setText(str(cfg.get("name") or ""))
            self.script_path_edit.setText(str(cfg.get("script_path") or ""))
            self.args_edit.setText(str(cfg.get("args") or ""))
            self.working_dir_edit.setText(str(cfg.get("working_dir") or ""))
            self.interpreter_edit.setText(str(cfg.get("interpreter") or ""))
            env_items = cfg.get("env", [])
            if not isinstance(env_items, list):
                env_items = []
            self.env_edit.setPlainText("\n".join(str(item).strip() for item in env_items if str(item).strip()))
        finally:
            self._loading = False

    def _on_editor_changed(self, *_args) -> None:
        if self._loading:
            return
        row = self._current_index()
        edit_row = int(self._editor_row)
        if row < 0 or edit_row < 0:
            return
        old_name = str(self._working_configs[edit_row].get("name") or "").strip()
        updated = self._editor_to_config(edit_row)
        self._working_configs[edit_row] = updated
        new_name = str(updated.get("name") or "").strip()
        self._last_selected_name = new_name
        if self._working_active_name.lower() == old_name.lower():
            self._working_active_name = new_name
        self._refresh_ui()
        self._mark_dirty()

    def _on_active_changed(self, _index: int) -> None:
        if self._loading:
            return
        self._working_active_name = str(self.active_combo.currentData(Qt.UserRole) or "").strip()
        self._mark_dirty()

    def _add_config(self) -> None:
        self._sync_current_config()
        existing = {str(cfg.get("name") or "").strip().lower() for cfg in self._working_configs}
        base = "Run Config"
        name = base
        idx = 2
        while name.lower() in existing:
            name = f"{base} {idx}"
            idx += 1
        self._working_configs.append(
            self._normalize_config(
                {
                    "name": name,
                    "script_path": "",
                    "args": "",
                    "working_dir": "",
                    "interpreter": "",
                    "env": [],
                },
                len(self._working_configs),
            )
        )
        self._last_selected_name = name
        self._refresh_ui()
        self._mark_dirty()

    def _duplicate_config(self) -> None:
        row = self._current_index()
        if row < 0:
            return
        self._sync_current_config()
        source = deepcopy(self._working_configs[row])
        base = f"{str(source.get('name') or 'Run Config').strip()} Copy"
        existing = {str(cfg.get("name") or "").strip().lower() for cfg in self._working_configs}
        name = base
        idx = 2
        while name.lower() in existing:
            name = f"{base} {idx}"
            idx += 1
        source["name"] = name
        self._working_configs.insert(row + 1, source)
        self._last_selected_name = name
        self._refresh_ui()
        self._mark_dirty()

    def _remove_config(self) -> None:
        row = self._current_index()
        if row < 0:
            return
        removed_name = str(self._working_configs[row].get("name") or "").strip()
        self._working_configs.pop(row)
        if self._working_active_name.lower() == removed_name.lower():
            self._working_active_name = str(self._working_configs[max(0, row - 1)].get("name") or "").strip() if self._working_configs else ""
        self._last_selected_name = self._working_active_name
        self._refresh_ui()
        self._mark_dirty()

    def _reset_defaults(self) -> None:
        defaults = _default_python_configs()
        self._sync_current_config()
        self._working_configs = [self._normalize_config(item, idx) for idx, item in enumerate(defaults)]
        self._working_active_name = str(self._working_configs[0].get("name") or "").strip() if self._working_configs else ""
        self._last_selected_name = self._working_active_name
        self._refresh_ui()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._refresh_status()
        self._notify_pending_changed()

    def _refresh_status(self) -> None:
        self.status_label.setText("Unsaved Python run configuration changes." if self.has_pending_settings_changes() else "No Python run configuration changes.")

    def has_pending_settings_changes(self) -> bool:
        return (
            [self._normalize_config(item, idx) for idx, item in enumerate(self._working_configs)]
            != [self._normalize_config(item, idx) for idx, item in enumerate(self._base_configs)]
            or str(self._working_active_name or "").strip() != str(self._base_active_name or "").strip()
        )

    def apply_settings_changes(self) -> list[str]:
        self._sync_current_config()
        normalized: list[dict[str, Any]] = []
        names_seen: set[str] = set()
        for idx, item in enumerate(self._working_configs):
            cfg = self._normalize_config(item, idx)
            name = str(cfg.get("name") or "").strip()
            if not name:
                return [f"Run config #{idx + 1}: name is required."]
            key = name.lower()
            if key in names_seen:
                return [f"Duplicate run config name: {name}"]
            names_seen.add(key)
            normalized.append(cfg)

        active_name = str(self._working_active_name or "").strip()
        valid_names = {str(cfg.get("name") or "").strip().lower() for cfg in normalized}
        if active_name and active_name.lower() not in valid_names:
            active_name = str(normalized[0].get("name") or "").strip() if normalized else ""

        self._manager.set("build.python.run_configs", normalized, "project")
        self._manager.set("build.python.active_config", active_name, "project")

        self._base_configs = deepcopy(normalized)
        self._base_active_name = active_name
        self._working_configs = deepcopy(normalized)
        self._working_active_name = active_name
        self._refresh_ui()
        self._notify_pending_changed()
        return []

    def _notify_pending_changed(self) -> None:
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "_refresh_dirty_state"):
            parent = parent.parentWidget()
        if parent is None:
            return
        refresh = getattr(parent, "_refresh_dirty_state", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass


def create_python_run_configs_page(
    *,
    manager: Any,
    scope: SettingsScope,
    binding_cls: Callable[..., Any],
    parent: QWidget | None = None,
) -> tuple[QWidget, list[Any]]:
    _ = scope
    _ = binding_cls
    page = PythonRunConfigsSettingsPage(manager=manager, parent=None)
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(page)
    return scroll, []


__all__ = ["PythonRunConfigsSettingsPage", "create_python_run_configs_page"]
