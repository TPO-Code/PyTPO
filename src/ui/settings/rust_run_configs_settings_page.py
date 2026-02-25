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


def _default_rust_configs() -> list[dict[str, Any]]:
    build_defaults = default_project_settings().get("build", {})
    rust_defaults = build_defaults.get("rust", {}) if isinstance(build_defaults, dict) else {}
    raw = rust_defaults.get("run_configs", []) if isinstance(rust_defaults, dict) else []
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                out.append(deepcopy(item))
    return out


class RustRunConfigsSettingsPage(QWidget):
    DEFAULT_LABEL = "Use Context Default (cargo run)"

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
        top_row.addWidget(QLabel("Active Cargo Config:"))
        self.active_combo = QComboBox(self)
        self.active_combo.currentIndexChanged.connect(self._on_active_changed)
        top_row.addWidget(self.active_combo, 1)
        root.addLayout(top_row)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)

        left_group = QGroupBox("Cargo Configurations", self)
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

        right_group = QGroupBox("Selected Cargo Config", self)
        right_layout = QVBoxLayout(right_group)
        right_layout.setContentsMargins(8, 10, 8, 8)
        right_layout.setSpacing(8)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.name_edit = QLineEdit(self)
        self.command_type_combo = QComboBox(self)
        self.command_type_combo.addItem("Cargo Run", "run")
        self.command_type_combo.addItem("Cargo Test", "test")
        self.command_type_combo.addItem("Cargo Build", "build")
        self.command_type_combo.addItem("Custom Cargo Command", "custom")
        self.package_edit = QLineEdit(self)
        self.binary_edit = QLineEdit(self)
        self.profile_combo = QComboBox(self)
        self.profile_combo.addItem("Debug", "debug")
        self.profile_combo.addItem("Release", "release")
        self.features_edit = QLineEdit(self)
        self.args_edit = QLineEdit(self)
        self.test_filter_edit = QLineEdit(self)
        self.command_edit = QLineEdit(self)
        self.working_dir_edit = QLineEdit(self)
        self.env_edit = QTextEdit(self)
        self.env_edit.setPlaceholderText("One KEY=VALUE per line")
        self.env_edit.setMinimumHeight(100)

        form.addRow("Name", self.name_edit)
        form.addRow("Type", self.command_type_combo)
        form.addRow("Package (optional)", self.package_edit)
        form.addRow("Binary (optional)", self.binary_edit)
        form.addRow("Profile", self.profile_combo)
        form.addRow("Features (optional)", self.features_edit)
        form.addRow("Program Args", self.args_edit)
        form.addRow("Test Filter Args", self.test_filter_edit)
        form.addRow("Custom Cargo Command", self.command_edit)
        form.addRow("Working Directory", self.working_dir_edit)
        form.addRow("Environment", self.env_edit)
        right_layout.addLayout(form)
        right_layout.addStretch(1)

        body.addWidget(right_group, 1)
        root.addLayout(body, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.name_edit.textChanged.connect(self._on_editor_changed)
        self.command_type_combo.currentIndexChanged.connect(self._on_editor_changed)
        self.package_edit.textChanged.connect(self._on_editor_changed)
        self.binary_edit.textChanged.connect(self._on_editor_changed)
        self.profile_combo.currentIndexChanged.connect(self._on_editor_changed)
        self.features_edit.textChanged.connect(self._on_editor_changed)
        self.args_edit.textChanged.connect(self._on_editor_changed)
        self.test_filter_edit.textChanged.connect(self._on_editor_changed)
        self.command_edit.textChanged.connect(self._on_editor_changed)
        self.working_dir_edit.textChanged.connect(self._on_editor_changed)
        self.env_edit.textChanged.connect(self._on_editor_changed)

    def _load_from_manager(self) -> None:
        build_cfg = self._manager.get("build", scope_preference="project", default={})
        if not isinstance(build_cfg, dict):
            build_cfg = {}
        rust_cfg = build_cfg.get("rust", {}) if isinstance(build_cfg, dict) else {}
        if not isinstance(rust_cfg, dict):
            rust_cfg = {}
        raw_configs = rust_cfg.get("run_configs")
        configs: list[dict[str, Any]] = []
        if isinstance(raw_configs, list):
            for idx, item in enumerate(raw_configs):
                configs.append(self._normalize_config(item, idx))
        if not configs:
            defaults = _default_rust_configs()
            for idx, item in enumerate(defaults):
                configs.append(self._normalize_config(item, idx))
        self._base_configs = configs
        self._working_configs = deepcopy(configs)

        active = str(rust_cfg.get("active_config") or "").strip()
        if active and active.lower() not in {str(item.get("name") or "").strip().lower() for item in self._working_configs}:
            active = str(self._working_configs[0].get("name") or "").strip() if self._working_configs else ""
        self._base_active_name = active
        self._working_active_name = active

    @staticmethod
    def _normalize_config(raw: Any, index: int) -> dict[str, Any]:
        cfg = raw if isinstance(raw, dict) else {}
        name = str(cfg.get("name") or "").strip() or f"Cargo Config {index + 1}"
        command_type = str(cfg.get("command_type") or "run").strip().lower()
        if command_type not in {"run", "test", "build", "custom"}:
            command_type = "run"
        profile = str(cfg.get("profile") or "debug").strip().lower()
        if profile not in {"debug", "release"}:
            profile = "debug"
        env_raw = cfg.get("env")
        if isinstance(env_raw, dict):
            env = [f"{str(k).strip()}={str(v or '')}" for k, v in env_raw.items() if str(k).strip()]
        elif isinstance(env_raw, list):
            env = [str(item).strip() for item in env_raw if str(item).strip()]
        else:
            env = []
        return {
            "name": name,
            "command_type": command_type,
            "package": str(cfg.get("package") or "").strip(),
            "binary": str(cfg.get("binary") or "").strip(),
            "profile": profile,
            "features": str(cfg.get("features") or "").strip(),
            "args": str(cfg.get("args") or "").strip(),
            "test_filter": str(cfg.get("test_filter") or "").strip(),
            "command": str(cfg.get("command") or "").strip(),
            "working_dir": str(cfg.get("working_dir") or "").strip(),
            "env": env,
        }

    def _refresh_ui(self) -> None:
        self._loading = True
        try:
            self.config_list.clear()
            for cfg in self._working_configs:
                self.config_list.addItem(QListWidgetItem(str(cfg.get("name") or "Cargo Config")))
            self.active_combo.clear()
            self.active_combo.addItem(self.DEFAULT_LABEL, "")
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
        name = str(self.name_edit.text() or "").strip() or str(current.get("name") or f"Cargo Config {row + 1}")
        env = [line.strip() for line in str(self.env_edit.toPlainText() or "").splitlines() if line.strip()]
        command_type = str(self.command_type_combo.currentData(Qt.UserRole) or "run").strip().lower()
        profile = str(self.profile_combo.currentData(Qt.UserRole) or "debug").strip().lower()
        return {
            "name": name,
            "command_type": command_type if command_type in {"run", "test", "build", "custom"} else "run",
            "package": str(self.package_edit.text() or "").strip(),
            "binary": str(self.binary_edit.text() or "").strip(),
            "profile": profile if profile in {"debug", "release"} else "debug",
            "features": str(self.features_edit.text() or "").strip(),
            "args": str(self.args_edit.text() or "").strip(),
            "test_filter": str(self.test_filter_edit.text() or "").strip(),
            "command": str(self.command_edit.text() or "").strip(),
            "working_dir": str(self.working_dir_edit.text() or "").strip(),
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
                self.command_type_combo,
                self.package_edit,
                self.binary_edit,
                self.profile_combo,
                self.features_edit,
                self.args_edit,
                self.test_filter_edit,
                self.command_edit,
                self.working_dir_edit,
                self.env_edit,
            ):
                widget.setEnabled(has_item)
            self.dup_btn.setEnabled(has_item)
            self.remove_btn.setEnabled(has_item and len(self._working_configs) > 0)
            if not has_item:
                self._editor_row = -1
                self.name_edit.setText("")
                self.package_edit.setText("")
                self.binary_edit.setText("")
                self.features_edit.setText("")
                self.args_edit.setText("")
                self.test_filter_edit.setText("")
                self.command_edit.setText("")
                self.working_dir_edit.setText("")
                self.env_edit.setPlainText("")
                return
            cfg = self._working_configs[row]
            self._editor_row = row
            self._last_selected_name = str(cfg.get("name") or "").strip()
            self.name_edit.setText(str(cfg.get("name") or ""))
            self.package_edit.setText(str(cfg.get("package") or ""))
            self.binary_edit.setText(str(cfg.get("binary") or ""))
            self.features_edit.setText(str(cfg.get("features") or ""))
            self.args_edit.setText(str(cfg.get("args") or ""))
            self.test_filter_edit.setText(str(cfg.get("test_filter") or ""))
            self.command_edit.setText(str(cfg.get("command") or ""))
            self.working_dir_edit.setText(str(cfg.get("working_dir") or ""))
            env_items = cfg.get("env", [])
            if not isinstance(env_items, list):
                env_items = []
            self.env_edit.setPlainText("\n".join(str(item).strip() for item in env_items if str(item).strip()))

            cmd_idx = self.command_type_combo.findData(str(cfg.get("command_type") or "run"), Qt.UserRole, Qt.MatchExactly)
            self.command_type_combo.setCurrentIndex(cmd_idx if cmd_idx >= 0 else 0)
            profile_idx = self.profile_combo.findData(str(cfg.get("profile") or "debug"), Qt.UserRole, Qt.MatchExactly)
            self.profile_combo.setCurrentIndex(profile_idx if profile_idx >= 0 else 0)
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
        base = "Cargo Config"
        name = base
        idx = 2
        while name.lower() in existing:
            name = f"{base} {idx}"
            idx += 1
        self._working_configs.append(self._normalize_config({"name": name}, len(self._working_configs)))
        self._last_selected_name = name
        self._refresh_ui()
        self._mark_dirty()

    def _duplicate_config(self) -> None:
        row = self._current_index()
        if row < 0:
            return
        self._sync_current_config()
        source = deepcopy(self._working_configs[row])
        base = str(source.get("name") or "Cargo Config").strip() + " Copy"
        existing = {str(cfg.get("name") or "").strip().lower() for cfg in self._working_configs}
        name = base
        idx = 2
        while name.lower() in existing:
            name = f"{base} {idx}"
            idx += 1
        source["name"] = name
        self._working_configs.insert(row + 1, self._normalize_config(source, row + 1))
        self._last_selected_name = name
        self._refresh_ui()
        self._mark_dirty()

    def _remove_config(self) -> None:
        row = self._current_index()
        if row < 0:
            return
        removed = self._working_configs.pop(row)
        removed_name = str(removed.get("name") or "").strip()
        if self._working_active_name.lower() == removed_name.lower():
            self._working_active_name = ""
        self._last_selected_name = ""
        self._refresh_ui()
        self._mark_dirty()

    def _reset_defaults(self) -> None:
        self._sync_current_config()
        defaults = _default_rust_configs()
        self._working_configs = [self._normalize_config(item, idx) for idx, item in enumerate(defaults)]
        self._working_active_name = ""
        self._last_selected_name = ""
        self._refresh_ui()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._refresh_status()
        self._notify_pending_changed()

    def _refresh_status(self) -> None:
        if self.has_pending_settings_changes():
            self.status_label.setText("Unsaved Cargo configuration changes.")
        else:
            self.status_label.setText("No Cargo configuration changes.")

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
                return [f"Cargo config #{idx + 1}: name is required."]
            key = name.lower()
            if key in names_seen:
                return [f"Duplicate cargo config name: {name}"]
            names_seen.add(key)
            normalized.append(cfg)

        active_name = str(self._working_active_name or "").strip()
        valid_names = {str(cfg.get("name") or "").strip().lower() for cfg in normalized}
        if active_name and active_name.lower() not in valid_names:
            active_name = str(normalized[0].get("name") or "").strip() if normalized else ""

        self._manager.set("build.rust.run_configs", normalized, "project")
        self._manager.set("build.rust.active_config", active_name, "project")

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


def create_rust_run_configs_page(
    *,
    manager: Any,
    scope: SettingsScope,
    binding_cls: Callable[..., Any],
    parent: QWidget | None = None,
) -> tuple[QWidget, list[Any]]:
    _ = scope
    _ = binding_cls
    page = RustRunConfigsSettingsPage(manager=manager, parent=None)
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(page)
    return scroll, []


__all__ = ["RustRunConfigsSettingsPage", "create_rust_run_configs_page"]

