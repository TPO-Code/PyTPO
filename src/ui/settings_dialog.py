from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from TPOPyside.dialogs.color_picker_dialog import ColorPickerDialog
from src.settings_manager import SettingsManager
from src.settings_models import SettingsScope
from src.ui.custom_dialog import DialogWindow
from src.ui.dialogs.file_dialog_bridge import get_existing_directory, get_open_file_name
from src.ui.dialogs.font_selection_dialog import FontSelectionDialog
from src.ui.interpreter_utils import (
    discover_project_interpreters,
    interpreter_browse_directory_hint,
    normalize_interpreter_for_project,
)
from src.ui.settings.ai_settings_page import AIAssistSettingsPage
from src.ui.settings.build_configs_settings_page import BuildConfigsSettingsPage
from src.ui.settings.github_settings_page import GitHubSettingsPage
from src.ui.settings.git_settings_page import GitSettingsPage
from src.ui.settings.keybindings_settings_page import KeybindingsSettingsPage
from src.ui.settings.clangd_repair_settings_page import ClangdRepairSettingsPage
from src.ui.settings.project_maintenance_page import ProjectMaintenancePage
from src.ui.settings.python_run_configs_settings_page import PythonRunConfigsSettingsPage
from src.ui.settings.rust_run_configs_settings_page import RustRunConfigsSettingsPage

FieldType = Literal[
    "checkbox",
    "lineedit",
    "color",
    "path_dir",
    "path_file",
    "spin",
    "combo",
    "list_str",
    "json",
    "font_family",
    "ai_assist_editor",
    "github_editor",
    "git_editor",
    "keybindings_editor",
    "build_configs_editor",
    "python_run_configs_editor",
    "rust_run_configs_editor",
    "project_maintenance_tools",
    "clangd_repair_tools",
]


@dataclass(slots=True)
class SchemaField:
    id: str
    key: str
    label: str
    type: FieldType
    scope: SettingsScope
    description: str = ""
    default: Any = None
    options: list[str] | list[dict[str, Any]] | None = None
    min: int | None = None
    max: int | None = None


@dataclass(slots=True)
class SchemaSection:
    title: str
    fields: list[SchemaField]
    description: str = ""


@dataclass(slots=True)
class SchemaPage:
    id: str
    category: str
    title: str
    scope: SettingsScope
    sections: list[SchemaSection]
    subcategory: str | None = None
    description: str = ""
    keywords: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SettingsSchema:
    pages: list[SchemaPage]


@dataclass(slots=True)
class FieldBinding:
    key: str
    scope: SettingsScope
    widget: QWidget
    getter: Callable[[], Any]
    setter: Callable[[Any], None]
    on_change: Callable[[Callable[..., None]], None]
    validate: Callable[[], list[str]]
    persist: bool = True
    has_pending_changes: Callable[[], bool] | None = None
    apply_changes: Callable[[], list[str]] | None = None


PANEL_FIELD_TYPES: set[str] = {
    "ai_assist_editor",
    "github_editor",
    "git_editor",
    "keybindings_editor",
    "build_configs_editor",
    "python_run_configs_editor",
    "rust_run_configs_editor",
    "project_maintenance_tools",
    "clangd_repair_tools",
}

SETTINGS_TREE_NODE_KEY_ROLE = Qt.UserRole + 2
SETTINGS_TREE_EXPANDED_PATHS_KEY = "ui.settings_dialog.tree_expanded_paths"


class SettingsDialog(DialogWindow):
    """Schema-driven settings editor for both project and IDE scopes."""

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
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("Settings")
        self.resize(1040, 720)

        self.manager = manager
        self.schema = schema
        self.initial_page_id = str(initial_page_id or "").strip() or None
        self.on_applied = on_applied

        self._ignore_changes = False
        self._dirty_scopes: set[SettingsScope] = set()
        self._bindings_by_page: dict[int, list[FieldBinding]] = {}
        self._persisted_tree_expanded_paths = self._load_tree_expanded_paths_from_settings()

        self._build_ui()
        self._build_tree_and_pages()
        self._load_widgets_from_store()
        if not self._select_page_by_id(self.initial_page_id):
            self._select_first_page()
        self._refresh_dirty_state()

    def _build_ui(self) -> None:
        root_host = QWidget(self)
        self.set_content_widget(root_host)

        root = QVBoxLayout(root_host)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        header_row = QHBoxLayout()
        title = QLabel("Settings")
        title.setObjectName("SettingsTitle")
        header_row.addWidget(title)
        header_row.addStretch(1)
        root.addLayout(header_row)

        body = QHBoxLayout()
        body.setSpacing(12)

        left = QFrame()
        left.setObjectName("LeftFrame")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search settings...")
        self.search.textChanged.connect(self._on_search_changed)
        left_layout.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setRootIsDecorated(True)
        self.tree.setItemsExpandable(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setIndentation(12)
        self.tree.setAllColumnsShowFocus(False)
        # Keep selection visuals minimal while preserving native tree branch
        # markers so users can collapse/expand categories.
        self.tree.setStyleSheet(
            "QTreeView { "
            "  outline: none; "
            "  show-decoration-selected: 0; "
            "  selection-background-color: transparent; "
            "} "
            "QTreeView::item { border: none; } "
            "QTreeView::item:selected { border: none; } "
        )
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        left_layout.addWidget(self.tree, 1)
        left.setFixedWidth(320)

        body.addWidget(left)

        right = QFrame()
        right.setObjectName("RightFrame")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(8)

        self.page_title = QLabel("")
        self.page_title.setObjectName("PageTitle")
        right_layout.addWidget(self.page_title)

        self.page_scope = QLabel("")
        self.page_scope.setObjectName("PageScope")
        right_layout.addWidget(self.page_scope)

        self.page_desc = QLabel("")
        self.page_desc.setWordWrap(True)
        self.page_desc.setObjectName("PageDesc")
        right_layout.addWidget(self.page_desc)

        self.stack = QStackedWidget()
        right_layout.addWidget(self.stack, 1)

        self.status = QLabel("")
        self.status.setObjectName("SettingsStatus")
        right_layout.addWidget(self.status)

        body.addWidget(right, 1)
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)

        self.btn_restore_scope = QPushButton("Restore Defaults (Current Scope)")
        self.btn_restore_scope.clicked.connect(self._on_restore_scope_defaults)
        footer.addWidget(self.btn_restore_scope)

        footer.addStretch(1)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel)
        footer.addWidget(self.btn_cancel)

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_apply.setEnabled(False)
        footer.addWidget(self.btn_apply)

        self.btn_save = QPushButton("Save")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)
        footer.addWidget(self.btn_save)

        root.addLayout(footer)

    def _build_tree_and_pages(self) -> None:
        runtime_expanded_paths = self._collect_tree_expanded_paths() if self.tree.topLevelItemCount() > 0 else None
        self.tree.clear()
        self._bindings_by_page.clear()
        while self.stack.count():
            page = self.stack.widget(0)
            self.stack.removeWidget(page)
            page.deleteLater()

        grouped: dict[str, dict[str, dict[str, list[SchemaPage]]]] = {}
        for page in self.schema.pages:
            scope_key = str(page.scope or "").strip().lower()
            if scope_key == "ide":
                scope_label = "IDE"
            elif scope_key == "project":
                scope_label = "Project"
            else:
                scope_label = str(page.scope or "").strip().capitalize() or "Project"
            category = str(page.category or "").strip()
            if not category or category.lower() == scope_label.lower():
                category = ""
            sub = page.subcategory or ""
            grouped.setdefault(scope_label, {}).setdefault(category, {}).setdefault(sub, []).append(page)

        scope_order = {
            "IDE": 0,
            "Project": 1,
        }
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
            "ide-run": 120,
            "ide-linting": 130,
            "ide-ai-assist": 131,
            "ide-git": 140,
            "ide-github": 141,
        }

        def _scope_sort_key(name: str) -> tuple[int, str]:
            raw = str(name or "")
            return (scope_order.get(raw, 100), raw.lower())

        def _group_sort_key(scope_name: str, group_name: str) -> tuple[int, str]:
            clean_scope = str(scope_name or "")
            clean_group = str(group_name or "")
            if not clean_group:
                return (-1, "")
            if clean_scope == "Project":
                return (project_group_order.get(clean_group, 100), clean_group.lower())
            if clean_scope == "IDE":
                return (ide_group_order.get(clean_group, 100), clean_group.lower())
            return (100, clean_group.lower())

        def _page_sort_key(page_spec: SchemaPage) -> tuple[int, str]:
            page_id = str(page_spec.id or "").strip().lower()
            return (page_order_by_id.get(page_id, 100), page_spec.title.lower())

        for scope_name in sorted(grouped.keys(), key=_scope_sort_key):
            scope_item = QTreeWidgetItem([scope_name])
            scope_item.setFlags(scope_item.flags() & ~Qt.ItemIsSelectable)
            font = scope_item.font(0)
            font.setBold(True)
            scope_item.setFont(0, font)
            scope_item.setData(0, Qt.UserRole, None)
            scope_path = (scope_name,)
            scope_item.setData(0, SETTINGS_TREE_NODE_KEY_ROLE, scope_path)
            self.tree.addTopLevelItem(scope_item)

            for group_name in sorted(grouped[scope_name].keys(), key=lambda item: _group_sort_key(scope_name, item)):
                by_subcategory = grouped[scope_name][group_name]

                group_parent = scope_item
                group_path = scope_path
                if group_name:
                    group_item = QTreeWidgetItem([group_name])
                    group_item.setFlags(group_item.flags() & ~Qt.ItemIsSelectable)
                    group_item.setData(0, Qt.UserRole, None)
                    group_path = scope_path + (group_name,)
                    group_item.setData(0, SETTINGS_TREE_NODE_KEY_ROLE, group_path)
                    scope_item.addChild(group_item)
                    group_parent = group_item

                for subcategory in sorted(by_subcategory.keys(), key=str.lower):
                    page_specs = by_subcategory[subcategory]
                    parent_item = group_parent
                    if subcategory:
                        sub_item = QTreeWidgetItem([subcategory])
                        sub_item.setFlags(sub_item.flags() & ~Qt.ItemIsSelectable)
                        sub_item.setData(0, Qt.UserRole, None)
                        sub_item.setData(0, SETTINGS_TREE_NODE_KEY_ROLE, group_path + (subcategory,))
                        group_parent.addChild(sub_item)
                        parent_item = sub_item

                    for page_spec in sorted(page_specs, key=_page_sort_key):
                        page_widget, bindings = self._build_page_widget(page_spec)
                        self.stack.addWidget(page_widget)
                        page_index = self.stack.indexOf(page_widget)
                        self._bindings_by_page[page_index] = bindings

                        leaf = QTreeWidgetItem([page_spec.title])
                        leaf.setData(0, Qt.UserRole, page_index)
                        leaf.setData(0, Qt.UserRole + 1, page_spec)
                        parent_item.addChild(leaf)

                if not group_name:
                    continue

                # Remove empty group nodes defensively (should not happen).
                if group_parent.childCount() == 0 and group_parent.parent() is scope_item:
                    scope_item.removeChild(group_parent)

            # Remove empty scope nodes defensively.
            if scope_item.childCount() == 0:
                idx = self.tree.indexOfTopLevelItem(scope_item)
                if idx >= 0:
                    self.tree.takeTopLevelItem(idx)

        if runtime_expanded_paths is None:
            self._apply_tree_expanded_paths(self._persisted_tree_expanded_paths)
        else:
            self._apply_tree_expanded_paths(runtime_expanded_paths)

    @staticmethod
    def _normalize_tree_path(raw: Any) -> tuple[str, ...] | None:
        if not isinstance(raw, (list, tuple)):
            return None
        path: list[str] = []
        for part in raw:
            text = str(part or "").strip()
            if not text:
                return None
            path.append(text)
        return tuple(path) if path else None

    def _load_tree_expanded_paths_from_settings(self) -> set[tuple[str, ...]] | None:
        stored = self.manager.get(SETTINGS_TREE_EXPANDED_PATHS_KEY, scope_preference="ide", default=None)
        if stored is None:
            return None
        if not isinstance(stored, list):
            return None
        expanded_paths: set[tuple[str, ...]] = set()
        for entry in stored:
            normalized = self._normalize_tree_path(entry)
            if normalized is not None:
                expanded_paths.add(normalized)
        return expanded_paths

    def _iter_branch_items(self) -> list[QTreeWidgetItem]:
        branches: list[QTreeWidgetItem] = []
        stack: list[QTreeWidgetItem] = []
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            if top is not None:
                stack.append(top)
        while stack:
            item = stack.pop()
            if item.childCount() > 0:
                branches.append(item)
                for child_index in range(item.childCount()):
                    child = item.child(child_index)
                    if child is not None:
                        stack.append(child)
        return branches

    def _collect_tree_expanded_paths(self) -> set[tuple[str, ...]]:
        expanded_paths: set[tuple[str, ...]] = set()
        for item in self._iter_branch_items():
            if not item.isExpanded():
                continue
            normalized = self._normalize_tree_path(item.data(0, SETTINGS_TREE_NODE_KEY_ROLE))
            if normalized is not None:
                expanded_paths.add(normalized)
        return expanded_paths

    def _apply_tree_expanded_paths(self, expanded_paths: set[tuple[str, ...]] | None) -> None:
        if expanded_paths is None:
            self.tree.expandAll()
            return
        for item in self._iter_branch_items():
            item.setExpanded(False)
        for item in self._iter_branch_items():
            normalized = self._normalize_tree_path(item.data(0, SETTINGS_TREE_NODE_KEY_ROLE))
            if normalized is not None and normalized in expanded_paths:
                item.setExpanded(True)

    def _persist_tree_expanded_paths(self) -> None:
        expanded_paths = self._collect_tree_expanded_paths()
        serialized_paths = [list(path) for path in sorted(expanded_paths)]
        try:
            self.manager.set(SETTINGS_TREE_EXPANDED_PATHS_KEY, serialized_paths, "ide")
            self.manager.save_all(scopes={"ide"}, only_dirty=True)
            self._persisted_tree_expanded_paths = set(expanded_paths)
        except Exception:
            return

    def done(self, result: int) -> None:
        self._persist_tree_expanded_paths()
        super().done(result)

    def _build_page_widget(self, page_spec: SchemaPage) -> tuple[QWidget, list[FieldBinding]]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        content = QVBoxLayout(container)
        content.setContentsMargins(4, 4, 12, 4)
        content.setSpacing(14)

        page_bindings: list[FieldBinding] = []

        for section in page_spec.sections:
            group = QGroupBox(section.title)
            section_layout = QVBoxLayout(group)
            section_layout.setSpacing(8)

            if section.description:
                section_desc = QLabel(section.description)
                section_desc.setWordWrap(True)
                section_desc.setObjectName("SectionDesc")
                section_layout.addWidget(section_desc)

            form = QFormLayout()
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(10)

            for field in section.fields:
                binding = self._create_field_binding(field)
                page_bindings.append(binding)
                binding.on_change(lambda *_args, scope=field.scope: self._mark_dirty(scope))

                if field.type == "checkbox" or field.type in PANEL_FIELD_TYPES:
                    form.addRow(binding.widget)
                else:
                    label = QLabel(field.label)
                    if field.description:
                        label.setToolTip(field.description)
                        binding.widget.setToolTip(field.description)
                    form.addRow(label, binding.widget)

            section_layout.addLayout(form)
            content.addWidget(group)

        content.addStretch(1)
        scroll.setWidget(container)
        return scroll, page_bindings

    def _create_field_binding(self, field: SchemaField) -> FieldBinding:
        if field.type == "ai_assist_editor":
            page = AIAssistSettingsPage(manager=self.manager, scope=field.scope, parent=self)
            return FieldBinding(
                key=field.key,
                scope=field.scope,
                widget=page,
                getter=lambda: None,
                setter=lambda _value: None,
                on_change=lambda _cb: None,
                validate=lambda: [],
                persist=False,
                has_pending_changes=page.has_pending_settings_changes,
                apply_changes=page.apply_settings_changes,
            )

        if field.type == "github_editor":
            page = GitHubSettingsPage(manager=self.manager, parent=self)
            return FieldBinding(
                key=field.key,
                scope=field.scope,
                widget=page,
                getter=lambda: None,
                setter=lambda _value: None,
                on_change=lambda _cb: None,
                validate=lambda: [],
                persist=False,
                has_pending_changes=page.has_pending_settings_changes,
                apply_changes=page.apply_settings_changes,
            )

        if field.type == "git_editor":
            page = GitSettingsPage(manager=self.manager, scope=field.scope, parent=self)
            return FieldBinding(
                key=field.key,
                scope=field.scope,
                widget=page,
                getter=lambda: None,
                setter=lambda _value: None,
                on_change=lambda _cb: None,
                validate=lambda: [],
                persist=False,
                has_pending_changes=page.has_pending_settings_changes,
                apply_changes=page.apply_settings_changes,
            )

        if field.type == "keybindings_editor":
            page = KeybindingsSettingsPage(manager=self.manager, parent=self)
            return FieldBinding(
                key=field.key,
                scope=field.scope,
                widget=page,
                getter=lambda: None,
                setter=lambda _value: None,
                on_change=lambda _cb: None,
                validate=lambda: [],
                persist=False,
                has_pending_changes=page.has_pending_settings_changes,
                apply_changes=page.apply_settings_changes,
            )

        if field.type == "build_configs_editor":
            page = BuildConfigsSettingsPage(manager=self.manager, parent=self)
            return FieldBinding(
                key=field.key,
                scope=field.scope,
                widget=page,
                getter=lambda: None,
                setter=lambda _value: None,
                on_change=lambda _cb: None,
                validate=lambda: [],
                persist=False,
                has_pending_changes=page.has_pending_settings_changes,
                apply_changes=page.apply_settings_changes,
            )

        if field.type == "python_run_configs_editor":
            page = PythonRunConfigsSettingsPage(manager=self.manager, parent=self)
            return FieldBinding(
                key=field.key,
                scope=field.scope,
                widget=page,
                getter=lambda: None,
                setter=lambda _value: None,
                on_change=lambda _cb: None,
                validate=lambda: [],
                persist=False,
                has_pending_changes=page.has_pending_settings_changes,
                apply_changes=page.apply_settings_changes,
            )

        if field.type == "rust_run_configs_editor":
            page = RustRunConfigsSettingsPage(manager=self.manager, parent=self)
            return FieldBinding(
                key=field.key,
                scope=field.scope,
                widget=page,
                getter=lambda: None,
                setter=lambda _value: None,
                on_change=lambda _cb: None,
                validate=lambda: [],
                persist=False,
                has_pending_changes=page.has_pending_settings_changes,
                apply_changes=page.apply_settings_changes,
            )

        if field.type == "project_maintenance_tools":
            page = ProjectMaintenancePage(manager=self.manager, parent=self)
            return FieldBinding(
                key=field.key,
                scope=field.scope,
                widget=page,
                getter=lambda: None,
                setter=lambda _value: None,
                on_change=lambda _cb: None,
                validate=lambda: [],
                persist=False,
                has_pending_changes=page.has_pending_settings_changes,
                apply_changes=page.apply_settings_changes,
            )

        if field.type == "clangd_repair_tools":
            page = ClangdRepairSettingsPage(
                manager=self.manager,
                on_runtime_refresh=self.on_applied,
                on_query_driver_updated=lambda value: self._set_bound_value(
                    key="c_cpp.query_driver",
                    scope="project",
                    value=value,
                ),
                parent=self,
            )
            return FieldBinding(
                key=field.key,
                scope=field.scope,
                widget=page,
                getter=lambda: None,
                setter=lambda _value: None,
                on_change=lambda _cb: None,
                validate=lambda: [],
                persist=False,
                has_pending_changes=page.has_pending_settings_changes,
                apply_changes=page.apply_settings_changes,
            )

        if field.type == "checkbox":
            widget = QCheckBox(field.label)

            def get_value() -> bool:
                return widget.isChecked()

            def set_value(value: Any) -> None:
                if isinstance(value, bool):
                    widget.setChecked(value)
                    return
                if isinstance(value, (int, float)):
                    widget.setChecked(bool(value))
                    return
                text = str(value or "").strip().lower()
                if text in {"1", "true", "yes", "on", "y"}:
                    widget.setChecked(True)
                    return
                if text in {"0", "false", "no", "off", "n", ""}:
                    widget.setChecked(False)
                    return
                widget.setChecked(bool(value))

            def connect_change(callback: Callable[..., None]) -> None:
                widget.toggled.connect(callback)

            return FieldBinding(field.key, field.scope, widget, get_value, set_value, connect_change, lambda: [])

        if field.type == "color":
            line = QLineEdit()
            line.setPlaceholderText("#RRGGBB")

            swatch = QPushButton()
            swatch.setFixedSize(34, 20)
            swatch.setCursor(Qt.PointingHandCursor)
            swatch.setToolTip("Pick color")
            swatch.setText("")

            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(line, 1)
            row.addWidget(swatch)

            def update_swatch() -> None:
                color = QColor(str(line.text() or "").strip())
                if color.isValid():
                    swatch.setStyleSheet(
                        f"QPushButton {{ background-color: {color.name(QColor.HexRgb)}; border: 1px solid #6a6a6a; border-radius: 3px; }}"
                    )
                    swatch.setText("")
                    return
                swatch.setStyleSheet(
                    "QPushButton { background-color: #2f2f2f; border: 1px solid #6a6a6a; border-radius: 3px; }"
                )
                swatch.setText("?")

            def pick_color() -> None:
                initial = QColor(str(line.text() or "").strip())
                if not initial.isValid():
                    initial = QColor("#ffffff")
                dialog = ColorPickerDialog(initial, self)
                if dialog.exec() != int(QDialog.DialogCode.Accepted):
                    return
                picked = dialog.get_color()
                if isinstance(picked, QColor) and picked.isValid():
                    line.setText(picked.name(QColor.HexRgb))

            swatch.clicked.connect(pick_color)
            line.textChanged.connect(lambda *_args: update_swatch())
            update_swatch()

            def get_value() -> str:
                return line.text().strip()

            def set_value(value: Any) -> None:
                line.setText(str(value) if value is not None else "")
                update_swatch()

            def connect_change(callback: Callable[..., None]) -> None:
                line.textChanged.connect(callback)

            def validate() -> list[str]:
                text = str(line.text() or "").strip()
                if not text:
                    return []
                if QColor(text).isValid():
                    return []
                return [f"{field.label}: invalid color."]

            return FieldBinding(field.key, field.scope, holder, get_value, set_value, connect_change, validate)

        if field.type in {"lineedit", "path_dir", "path_file"}:
            if field.scope == "project" and field.key == "interpreters.default":
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

            line = QLineEdit()
            line.setPlaceholderText(field.description or "")

            control: QWidget = line
            connect_signal = line.textChanged.connect

            if field.type in {"path_dir", "path_file"}:
                holder = QWidget()
                row = QHBoxLayout(holder)
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(6)
                browse = QPushButton("Browse")
                browse.setFixedWidth(80)

                def on_browse() -> None:
                    start = str(line.text() or "").strip()
                    if field.type == "path_file":
                        selected, _selected_filter = get_open_file_name(
                            parent=self,
                            manager=self.manager,
                            caption="Select File",
                            directory=start,
                            file_filter=(
                                "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.svg);;"
                                "All Files (*)"
                            ),
                        )
                    else:
                        selected = get_existing_directory(
                            parent=self,
                            manager=self.manager,
                            caption="Select Directory",
                            directory=start,
                        )
                    if selected:
                        line.setText(selected)

                browse.clicked.connect(on_browse)
                row.addWidget(line)
                row.addWidget(browse)
                control = holder

            def get_value() -> str:
                return line.text().strip()

            def set_value(value: Any) -> None:
                line.setText(str(value) if value is not None else "")

            def connect_change(callback: Callable[..., None]) -> None:
                connect_signal(callback)

            return FieldBinding(field.key, field.scope, control, get_value, set_value, connect_change, lambda: [])

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

        if field.type == "spin":
            widget = QSpinBox()
            widget.setRange(field.min if field.min is not None else -2147483648, field.max if field.max is not None else 2147483647)

            def get_value() -> int:
                return int(widget.value())

            def set_value(value: Any) -> None:
                try:
                    widget.setValue(int(value))
                except Exception:
                    widget.setValue(widget.minimum())

            def connect_change(callback: Callable[..., None]) -> None:
                widget.valueChanged.connect(callback)

            return FieldBinding(field.key, field.scope, widget, get_value, set_value, connect_change, lambda: [])

        if field.type == "combo":
            widget = QComboBox()
            options = field.options or []
            for option in options:
                if isinstance(option, dict):
                    widget.addItem(str(option.get("label", option.get("value", ""))), option.get("value"))
                else:
                    widget.addItem(str(option), option)

            def get_value() -> Any:
                return widget.currentData()

            def set_value(value: Any) -> None:
                index = widget.findData(value)
                if index < 0:
                    index = 0
                if widget.count() > 0:
                    widget.setCurrentIndex(index)

            def connect_change(callback: Callable[..., None]) -> None:
                widget.currentIndexChanged.connect(callback)

            return FieldBinding(field.key, field.scope, widget, get_value, set_value, connect_change, lambda: [])

        if field.type == "list_str":
            holder = QWidget()
            layout = QVBoxLayout(holder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)

            widget = QListWidget()
            widget.setMinimumHeight(110)
            layout.addWidget(widget)

            actions = QHBoxLayout()
            add_btn = QPushButton("+")
            remove_btn = QPushButton("-")
            add_btn.setFixedWidth(30)
            remove_btn.setFixedWidth(30)
            actions.addWidget(add_btn)
            actions.addWidget(remove_btn)
            actions.addStretch(1)
            layout.addLayout(actions)

            callbacks: list[Callable[..., None]] = []

            def emit_change() -> None:
                for callback in callbacks:
                    callback()

            def on_add() -> None:
                item = QListWidgetItem("new-item")
                item.setFlags(item.flags() | Qt.ItemIsEditable)
                widget.addItem(item)
                widget.editItem(item)
                emit_change()

            def on_remove() -> None:
                row = widget.currentRow()
                if row >= 0:
                    widget.takeItem(row)
                    emit_change()

            add_btn.clicked.connect(on_add)
            remove_btn.clicked.connect(on_remove)
            widget.itemChanged.connect(lambda *_args: emit_change())

            def get_value() -> list[str]:
                return [widget.item(i).text() for i in range(widget.count())]

            def set_value(value: Any) -> None:
                widget.clear()
                if isinstance(value, list):
                    for item in value:
                        entry = QListWidgetItem(str(item))
                        entry.setFlags(entry.flags() | Qt.ItemIsEditable)
                        widget.addItem(entry)

            def connect_change(callback: Callable[..., None]) -> None:
                callbacks.append(callback)

            return FieldBinding(field.key, field.scope, holder, get_value, set_value, connect_change, lambda: [])

        if field.type == "json":
            widget = QTextEdit()
            widget.setAcceptRichText(False)
            widget.setMinimumHeight(140)

            def get_value() -> Any:
                text = widget.toPlainText().strip()
                if not text:
                    return []
                return json.loads(text)

            def set_value(value: Any) -> None:
                widget.setPlainText(json.dumps(value, indent=2))

            def connect_change(callback: Callable[..., None]) -> None:
                widget.textChanged.connect(callback)

            def validate() -> list[str]:
                try:
                    text = widget.toPlainText().strip()
                    if text:
                        json.loads(text)
                except Exception as exc:
                    return [f"{field.label}: {exc}"]
                return []

            return FieldBinding(field.key, field.scope, widget, get_value, set_value, connect_change, validate)

        fallback = QLabel(f"Unsupported field type: {field.type}")
        return FieldBinding(field.key, field.scope, fallback, lambda: None, lambda _v: None, lambda _c: None, lambda: [])

    def _load_widgets_from_store(self) -> None:
        self._ignore_changes = True
        try:
            for page_index, bindings in self._bindings_by_page.items():
                _ = page_index
                for binding in bindings:
                    if not binding.persist:
                        continue
                    value = self.manager.get(binding.key, scope_preference=binding.scope)
                    binding.setter(value)
        finally:
            self._ignore_changes = False

    def _set_bound_value(self, *, key: str, scope: SettingsScope, value: Any) -> None:
        self._ignore_changes = True
        try:
            for bindings in self._bindings_by_page.values():
                for binding in bindings:
                    if not binding.persist:
                        continue
                    if str(binding.key) != str(key):
                        continue
                    if str(binding.scope) != str(scope):
                        continue
                    try:
                        binding.setter(value)
                    except Exception:
                        continue
        finally:
            self._ignore_changes = False

    def _collect_all_widget_values(self) -> dict[SettingsScope, list[tuple[str, Any]]]:
        collected: dict[SettingsScope, list[tuple[str, Any]]] = {"project": [], "ide": []}
        for bindings in self._bindings_by_page.values():
            for binding in bindings:
                if not binding.persist:
                    continue
                collected[binding.scope].append((binding.key, binding.getter()))
        return collected

    def _current_page_spec(self) -> SchemaPage | None:
        current = self.tree.currentItem()
        if current is None:
            return None
        spec = current.data(0, Qt.UserRole + 1)
        return spec if isinstance(spec, SchemaPage) else None

    def _on_tree_selection_changed(self) -> None:
        current = self.tree.currentItem()
        if current is None:
            return
        index = current.data(0, Qt.UserRole)
        if not isinstance(index, int):
            return

        self.stack.setCurrentIndex(index)
        spec = current.data(0, Qt.UserRole + 1)
        if isinstance(spec, SchemaPage):
            self.page_title.setText(spec.title)
            self.page_desc.setText(spec.description)
            self.page_scope.setText(f"Scope: {spec.scope.capitalize()}")
        else:
            self.page_title.setText("")
            self.page_desc.setText("")
            self.page_scope.setText("")

    def _on_search_changed(self, text: str) -> None:
        query = text.strip().lower()

        def page_matches(spec: SchemaPage) -> bool:
            if not query:
                return True
            page_tokens = [
                spec.title,
                spec.category,
                spec.subcategory or "",
                spec.description,
                spec.scope,
                " ".join(spec.keywords),
            ]
            field_tokens: list[str] = []
            for section in spec.sections:
                field_tokens.append(section.title)
                field_tokens.append(section.description)
                for field in section.fields:
                    field_tokens.append(field.label)
                    field_tokens.append(field.key)
                    field_tokens.append(field.scope)
                    field_tokens.append(field.description)
            haystack = " ".join(page_tokens + field_tokens).lower()
            return query in haystack

        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            self._filter_tree_item(top, page_matches)

        current = self.tree.currentItem()
        if current is None or current.isHidden():
            self._select_first_visible_page()

    def _filter_tree_item(self, item: QTreeWidgetItem, matcher: Callable[[SchemaPage], bool]) -> bool:
        index = item.data(0, Qt.UserRole)
        if isinstance(index, int):
            spec = item.data(0, Qt.UserRole + 1)
            visible = bool(spec and matcher(spec))
            item.setHidden(not visible)
            return visible

        any_visible = False
        for child_idx in range(item.childCount()):
            child = item.child(child_idx)
            any_visible = self._filter_tree_item(child, matcher) or any_visible

        item.setHidden(not any_visible)
        return any_visible

    def _select_first_page(self) -> None:
        self._select_first_visible_page()

    def _select_page_by_id(self, page_id: str | None) -> bool:
        target = str(page_id or "").strip().lower()
        if not target:
            return False

        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            candidate = self._find_leaf_by_page_id(top, target)
            if candidate is not None:
                self.tree.setCurrentItem(candidate)
                return True
        return False

    def _find_leaf_by_page_id(self, item: QTreeWidgetItem, target: str) -> QTreeWidgetItem | None:
        index = item.data(0, Qt.UserRole)
        if isinstance(index, int):
            spec = item.data(0, Qt.UserRole + 1)
            if isinstance(spec, SchemaPage) and spec.id.lower() == target:
                return item
            return None

        for child_idx in range(item.childCount()):
            child = item.child(child_idx)
            found = self._find_leaf_by_page_id(child, target)
            if found is not None:
                return found
        return None

    def _select_first_visible_page(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            leaf = self._first_visible_leaf(top)
            if leaf is not None:
                self.tree.setCurrentItem(leaf)
                return

    def _first_visible_leaf(self, item: QTreeWidgetItem) -> QTreeWidgetItem | None:
        for i in range(item.childCount()):
            child = item.child(i)
            if child.isHidden():
                continue
            index = child.data(0, Qt.UserRole)
            if isinstance(index, int):
                return child
            nested = self._first_visible_leaf(child)
            if nested is not None:
                return nested
        return None

    def _mark_dirty(self, scope: SettingsScope) -> None:
        if self._ignore_changes:
            return
        self._dirty_scopes.add(scope)
        self._refresh_dirty_state()

    def _has_custom_pending_changes(self) -> bool:
        for bindings in self._bindings_by_page.values():
            for binding in bindings:
                pending_fn = binding.has_pending_changes
                if not callable(pending_fn):
                    continue
                try:
                    if bool(pending_fn()):
                        return True
                except Exception:
                    continue
        return False

    def _refresh_dirty_state(self) -> None:
        dirty = bool(self._dirty_scopes) or self._has_custom_pending_changes()
        self.btn_apply.setEnabled(dirty)
        if dirty:
            if self._dirty_scopes:
                scope_text = ", ".join(sorted(scope.capitalize() for scope in self._dirty_scopes))
                self.status.setText(f"Unsaved changes in scope(s): {scope_text}")
            else:
                self.status.setText("Unsaved changes.")
        else:
            self.status.setText("No unsaved changes")

    def _validate_all(self) -> list[str]:
        errors: list[str] = []
        for bindings in self._bindings_by_page.values():
            for binding in bindings:
                errors.extend(binding.validate())
        return errors

    def _apply_custom_pages(self) -> list[str]:
        errors: list[str] = []
        for bindings in self._bindings_by_page.values():
            for binding in bindings:
                apply_fn = binding.apply_changes
                if not callable(apply_fn):
                    continue
                try:
                    result = apply_fn()
                except Exception as exc:
                    errors.append(str(exc))
                    continue
                if isinstance(result, list):
                    errors.extend(str(item) for item in result if str(item).strip())
        return errors

    def _apply_internal(self) -> bool:
        errors = self._validate_all()
        if errors:
            QMessageBox.warning(self, "Invalid settings", "\n".join(errors[:5]))
            return False

        values_by_scope = self._collect_all_widget_values()
        for scope, entries in values_by_scope.items():
            for key, value in entries:
                self.manager.set(key, value, scope)

        custom_errors = self._apply_custom_pages()
        if custom_errors:
            QMessageBox.warning(self, "Invalid settings", "\n".join(custom_errors[:5]))
            return False

        for scope in ("project", "ide"):
            if self.manager.scoped_stores.store_for(scope).dirty:
                self._dirty_scopes.add(scope)

        self._dirty_scopes = {
            scope
            for scope in self._dirty_scopes
            if self.manager.scoped_stores.store_for(scope).dirty
        }
        if self._dirty_scopes:
            saved = self.manager.save_all(
                scopes=set(self._dirty_scopes),
                only_dirty=True,
                allow_project_repair=True,
            )
            self._dirty_scopes -= saved

        if self.on_applied is not None:
            self.on_applied()

        self._refresh_dirty_state()
        self.status.setText("Settings applied.")
        return True

    def _on_apply(self) -> None:
        try:
            self._apply_internal()
        except Exception as exc:
            QMessageBox.critical(self, "Apply failed", f"Could not save settings.\n\n{exc}")

    def _on_save(self) -> None:
        try:
            if self._apply_internal():
                self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", f"Could not save settings.\n\n{exc}")

    def _on_cancel(self) -> None:
        if self._dirty_scopes or self._has_custom_pending_changes():
            answer = QMessageBox.question(
                self,
                "Discard changes?",
                "Discard unsaved changes in both scopes?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        self.manager.reload_all()
        self.reject()

    def _on_restore_scope_defaults(self) -> None:
        spec = self._current_page_spec()
        if spec is None:
            return

        answer = QMessageBox.question(
            self,
            "Restore defaults",
            f"Restore defaults for {spec.scope.capitalize()} scope?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        current_page_id = spec.id
        self.manager.restore_scope_defaults(spec.scope)
        self._build_tree_and_pages()
        self._load_widgets_from_store()
        if not self._select_page_by_id(current_page_id):
            self._select_first_page()

        self._dirty_scopes.add(spec.scope)
        self._refresh_dirty_state()


def create_default_settings_schema(theme_options: list[str] | None = None) -> SettingsSchema:
    resolved_theme_options = [str(option) for option in (theme_options or []) if str(option).strip()]
    return SettingsSchema(
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
