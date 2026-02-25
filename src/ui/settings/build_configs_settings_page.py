from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.settings_models import SettingsScope, default_project_settings


def _default_build_configs() -> list[dict[str, Any]]:
    build_defaults = default_project_settings().get("build", {})
    cmake_defaults = build_defaults.get("cmake", {}) if isinstance(build_defaults, dict) else {}
    raw = cmake_defaults.get("build_configs", []) if isinstance(cmake_defaults, dict) else []
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                out.append(deepcopy(item))
    return out


class BuildConfigsSettingsPage(QWidget):
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
        top_row.addWidget(QLabel("Active Build Configuration:"))
        self.active_combo = QComboBox(self)
        self.active_combo.currentTextChanged.connect(self._on_active_changed)
        top_row.addWidget(self.active_combo, 1)
        root.addLayout(top_row)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)

        left_group = QGroupBox("Configurations", self)
        left_layout = QVBoxLayout(left_group)
        left_layout.setContentsMargins(8, 10, 8, 8)
        left_layout.setSpacing(8)

        self.config_list = QListWidget(self)
        self.config_list.currentRowChanged.connect(self._on_config_selected)
        left_layout.addWidget(self.config_list, 1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)
        self.add_btn = QPushButton("Add", self)
        self.dup_btn = QPushButton("Duplicate", self)
        self.remove_btn = QPushButton("Remove", self)
        self.reset_btn = QPushButton("Reset Defaults", self)
        button_row.addWidget(self.add_btn)
        button_row.addWidget(self.dup_btn)
        button_row.addWidget(self.remove_btn)
        button_row.addWidget(self.reset_btn)
        left_layout.addLayout(button_row)

        self.add_btn.clicked.connect(self._add_config)
        self.dup_btn.clicked.connect(self._duplicate_config)
        self.remove_btn.clicked.connect(self._remove_config)
        self.reset_btn.clicked.connect(self._reset_defaults)

        body.addWidget(left_group, 0)

        right_group = QGroupBox("Selected Configuration", self)
        right_layout = QVBoxLayout(right_group)
        right_layout.setContentsMargins(8, 10, 8, 8)
        right_layout.setSpacing(8)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.name_edit = QLineEdit(self)
        self.build_dir_edit = QLineEdit(self)
        self.build_type_edit = QLineEdit(self)
        self.target_edit = QLineEdit(self)
        self.parallel_spin = QSpinBox(self)
        self.parallel_spin.setRange(0, 128)
        self.configure_args_edit = QLineEdit(self)
        self.build_args_edit = QLineEdit(self)
        self.run_args_edit = QLineEdit(self)
        self.env_edit = QTextEdit(self)
        self.env_edit.setPlaceholderText("One KEY=VALUE per line")
        self.env_edit.setMinimumHeight(110)

        form.addRow("Name", self.name_edit)
        form.addRow("Build Directory", self.build_dir_edit)
        form.addRow("Build Type", self.build_type_edit)
        form.addRow("Target", self.target_edit)
        form.addRow("Parallel Jobs", self.parallel_spin)
        form.addRow("Configure Args", self.configure_args_edit)
        form.addRow("Build Args", self.build_args_edit)
        form.addRow("Program Args", self.run_args_edit)
        form.addRow("Environment", self.env_edit)
        right_layout.addLayout(form)
        right_layout.addStretch(1)

        self.name_edit.textChanged.connect(self._on_editor_changed)
        self.build_dir_edit.textChanged.connect(self._on_editor_changed)
        self.build_type_edit.textChanged.connect(self._on_editor_changed)
        self.target_edit.textChanged.connect(self._on_editor_changed)
        self.parallel_spin.valueChanged.connect(self._on_editor_changed)
        self.configure_args_edit.textChanged.connect(self._on_editor_changed)
        self.build_args_edit.textChanged.connect(self._on_editor_changed)
        self.run_args_edit.textChanged.connect(self._on_editor_changed)
        self.env_edit.textChanged.connect(self._on_editor_changed)

        body.addWidget(right_group, 1)
        root.addLayout(body, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

    def _load_from_manager(self) -> None:
        build_cfg = self._manager.get("build", scope_preference="project", default={})
        if not isinstance(build_cfg, dict):
            build_cfg = {}
        cmake_cfg = build_cfg.get("cmake", {}) if isinstance(build_cfg, dict) else {}
        if not isinstance(cmake_cfg, dict):
            cmake_cfg = {}

        raw_configs = cmake_cfg.get("build_configs")
        configs: list[dict[str, Any]] = []
        if isinstance(raw_configs, list):
            for idx, item in enumerate(raw_configs):
                configs.append(self._normalize_config(item, idx))
        if not configs:
            defaults = _default_build_configs()
            for idx, item in enumerate(defaults):
                configs.append(self._normalize_config(item, idx))
        if not configs:
            configs.append(self._normalize_config({"name": "Debug", "build_type": "Debug"}, 0))

        self._base_configs = configs
        self._working_configs = deepcopy(configs)

        active_name = str(cmake_cfg.get("active_config") or "").strip()
        if not active_name and self._working_configs:
            active_name = str(self._working_configs[0].get("name") or "").strip()
        if active_name.lower() not in {str(item.get("name") or "").strip().lower() for item in self._working_configs}:
            active_name = str(self._working_configs[0].get("name") or "").strip()
        self._base_active_name = active_name
        self._working_active_name = active_name

    @staticmethod
    def _normalize_config(raw: Any, index: int) -> dict[str, Any]:
        item = raw if isinstance(raw, dict) else {}
        name = str(item.get("name") or "").strip() or f"Config {index + 1}"
        build_dir = str(item.get("build_dir") or "build").strip() or "build"
        build_type = str(item.get("build_type") or "Debug").strip() or "Debug"
        target = str(item.get("target") or "").strip()
        configure_args = str(item.get("configure_args") or "").strip()
        build_args = str(item.get("build_args") or "").strip()
        run_args = str(item.get("run_args") or "").strip()
        try:
            parallel_jobs = max(0, min(128, int(item.get("parallel_jobs", 0))))
        except Exception:
            parallel_jobs = 0
        env_raw = item.get("env")
        env: list[str] = []
        if isinstance(env_raw, dict):
            for key, value in env_raw.items():
                key_text = str(key or "").strip()
                if not key_text:
                    continue
                env.append(f"{key_text}={str(value or '')}")
        elif isinstance(env_raw, list):
            env = [str(entry).strip() for entry in env_raw if str(entry).strip()]
        return {
            "name": name,
            "build_dir": build_dir,
            "build_type": build_type,
            "target": target,
            "configure_args": configure_args,
            "build_args": build_args,
            "run_args": run_args,
            "parallel_jobs": parallel_jobs,
            "env": env,
        }

    def _refresh_ui(self) -> None:
        self._loading = True
        try:
            self.config_list.clear()
            for cfg in self._working_configs:
                self.config_list.addItem(QListWidgetItem(str(cfg.get("name") or "Config")))

            self.active_combo.clear()
            for cfg in self._working_configs:
                name = str(cfg.get("name") or "").strip()
                if name:
                    self.active_combo.addItem(name)

            if self._working_active_name:
                idx = self.active_combo.findText(self._working_active_name, Qt.MatchExactly)
                if idx >= 0:
                    self.active_combo.setCurrentIndex(idx)
            if self.active_combo.currentIndex() < 0 and self.active_combo.count() > 0:
                self.active_combo.setCurrentIndex(0)
                self._working_active_name = str(self.active_combo.currentText() or "").strip()

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

    def _on_config_selected(self, _row: int) -> None:
        if self._loading:
            return
        self._sync_current_config()
        self._load_selected_into_editor()

    def _current_index(self) -> int:
        row = int(self.config_list.currentRow())
        if row < 0 or row >= len(self._working_configs):
            return -1
        return row

    def _sync_current_config(self) -> None:
        row = int(self._editor_row)
        if row < 0:
            return
        if row >= len(self._working_configs):
            return
        self._working_configs[row] = self._editor_to_config(row)

    def _editor_to_config(self, row: int) -> dict[str, Any]:
        current = self._working_configs[row] if 0 <= row < len(self._working_configs) else {}
        name = str(self.name_edit.text() or "").strip() or str(current.get("name") or f"Config {row + 1}").strip()
        env_lines = [line.strip() for line in str(self.env_edit.toPlainText() or "").splitlines() if line.strip()]
        return {
            "name": name,
            "build_dir": str(self.build_dir_edit.text() or "").strip() or "build",
            "build_type": str(self.build_type_edit.text() or "").strip() or "Debug",
            "target": str(self.target_edit.text() or "").strip(),
            "configure_args": str(self.configure_args_edit.text() or "").strip(),
            "build_args": str(self.build_args_edit.text() or "").strip(),
            "run_args": str(self.run_args_edit.text() or "").strip(),
            "parallel_jobs": int(self.parallel_spin.value()),
            "env": env_lines,
        }

    def _load_selected_into_editor(self) -> None:
        self._loading = True
        try:
            row = self._current_index()
            has_item = row >= 0
            for widget in (
                self.name_edit,
                self.build_dir_edit,
                self.build_type_edit,
                self.target_edit,
                self.configure_args_edit,
                self.build_args_edit,
                self.run_args_edit,
                self.env_edit,
            ):
                widget.setEnabled(has_item)
            self.parallel_spin.setEnabled(has_item)
            self.dup_btn.setEnabled(has_item)
            self.remove_btn.setEnabled(has_item and len(self._working_configs) > 1)
            if not has_item:
                self._editor_row = -1
                self.name_edit.setText("")
                self.build_dir_edit.setText("")
                self.build_type_edit.setText("")
                self.target_edit.setText("")
                self.configure_args_edit.setText("")
                self.build_args_edit.setText("")
                self.run_args_edit.setText("")
                self.parallel_spin.setValue(0)
                self.env_edit.setPlainText("")
                return

            cfg = self._working_configs[row]
            self._editor_row = row
            self._last_selected_name = str(cfg.get("name") or "").strip()
            self.name_edit.setText(str(cfg.get("name") or ""))
            self.build_dir_edit.setText(str(cfg.get("build_dir") or ""))
            self.build_type_edit.setText(str(cfg.get("build_type") or ""))
            self.target_edit.setText(str(cfg.get("target") or ""))
            self.configure_args_edit.setText(str(cfg.get("configure_args") or ""))
            self.build_args_edit.setText(str(cfg.get("build_args") or ""))
            self.run_args_edit.setText(str(cfg.get("run_args") or ""))
            self.parallel_spin.setValue(max(0, min(128, int(cfg.get("parallel_jobs", 0)))))
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
        new_name = str(updated.get("name") or "").strip()
        self._working_configs[edit_row] = updated
        self._last_selected_name = new_name
        if self._working_active_name.lower() == old_name.lower():
            self._working_active_name = new_name
        self._refresh_ui()
        self._mark_dirty()

    def _on_active_changed(self, text: str) -> None:
        if self._loading:
            return
        self._working_active_name = str(text or "").strip()
        self._mark_dirty()

    def _add_config(self) -> None:
        self._sync_current_config()
        existing = {str(cfg.get("name") or "").strip().lower() for cfg in self._working_configs}
        base = "New Config"
        name = base
        n = 2
        while name.lower() in existing:
            name = f"{base} {n}"
            n += 1
        self._working_configs.append(
            self._normalize_config(
                {
                    "name": name,
                    "build_dir": "build",
                    "build_type": "Debug",
                    "target": "",
                    "configure_args": "",
                    "build_args": "",
                    "run_args": "",
                    "parallel_jobs": 0,
                    "env": [],
                },
                len(self._working_configs),
            )
        )
        if not self._working_active_name:
            self._working_active_name = name
        self._last_selected_name = name
        self._refresh_ui()
        self._mark_dirty()

    def _duplicate_config(self) -> None:
        row = self._current_index()
        if row < 0:
            return
        self._sync_current_config()
        source = deepcopy(self._working_configs[row])
        base = f"{str(source.get('name') or 'Config').strip()} Copy"
        existing = {str(cfg.get("name") or "").strip().lower() for cfg in self._working_configs}
        name = base
        n = 2
        while name.lower() in existing:
            name = f"{base} {n}"
            n += 1
        source["name"] = name
        self._working_configs.insert(row + 1, source)
        self._last_selected_name = name
        self._refresh_ui()
        self._mark_dirty()

    def _remove_config(self) -> None:
        row = self._current_index()
        if row < 0 or len(self._working_configs) <= 1:
            return
        removed_name = str(self._working_configs[row].get("name") or "").strip()
        self._working_configs.pop(row)
        if self._working_active_name.lower() == removed_name.lower():
            self._working_active_name = str(self._working_configs[max(0, row - 1)].get("name") or "").strip()
        self._last_selected_name = self._working_active_name
        self._refresh_ui()
        self._mark_dirty()

    def _reset_defaults(self) -> None:
        defaults = _default_build_configs()
        if not defaults:
            return
        self._sync_current_config()
        self._working_configs = [self._normalize_config(item, idx) for idx, item in enumerate(defaults)]
        self._working_active_name = str(self._working_configs[0].get("name") or "").strip()
        self._last_selected_name = self._working_active_name
        self._refresh_ui()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._refresh_status()
        self._notify_pending_changed()

    def _refresh_status(self) -> None:
        if self.has_pending_settings_changes():
            self.status_label.setText("Unsaved build configuration changes.")
        else:
            self.status_label.setText("No build configuration changes.")

    def has_pending_settings_changes(self) -> bool:
        current_configs = self._normalized_for_compare(self._working_configs)
        base_configs = self._normalized_for_compare(self._base_configs)
        current_active = str(self._working_active_name or "").strip()
        base_active = str(self._base_active_name or "").strip()
        return current_configs != base_configs or current_active != base_active

    def _normalized_for_compare(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for idx, item in enumerate(raw):
            out.append(self._normalize_config(item, idx))
        return out

    def apply_settings_changes(self) -> list[str]:
        self._sync_current_config()
        if not self._working_configs:
            return ["At least one build configuration is required."]

        names_seen: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(self._working_configs):
            cfg = self._normalize_config(item, idx)
            name = str(cfg.get("name") or "").strip()
            if not name:
                return [f"Configuration #{idx + 1}: name is required."]
            key = name.lower()
            if key in names_seen:
                return [f"Duplicate configuration name: {name}"]
            names_seen.add(key)
            normalized.append(cfg)

        active_name = str(self._working_active_name or "").strip()
        if active_name.lower() not in {str(cfg.get("name") or "").strip().lower() for cfg in normalized}:
            active_name = str(normalized[0].get("name") or "").strip()

        self._manager.set("build.cmake.active_config", active_name, "project")
        self._manager.set("build.cmake.build_configs", normalized, "project")

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


def create_build_configs_page(
    *,
    manager: Any,
    scope: SettingsScope,
    binding_cls: Callable[..., Any],
    parent: QWidget | None = None,
) -> tuple[QWidget, list[Any]]:
    _ = scope
    _ = binding_cls
    page = BuildConfigsSettingsPage(manager=manager, parent=None)
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(page)
    return scroll, []


__all__ = ["BuildConfigsSettingsPage", "create_build_configs_page"]
