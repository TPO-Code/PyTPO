from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFontDialog,
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

from .color_picker_dialog import ColorPickerDialog
from .custom_dialog import DialogWindow
from .reusable_file_dialog import FileDialog

BuiltinFieldType = Literal[
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
    "button",
    "button_row",
]

SETTINGS_TREE_NODE_KEY_ROLE = Qt.UserRole + 2
_HEX_COLOR_WITH_ALPHA_RE = re.compile(r"^#(?P<rgb>[0-9a-fA-F]{6})(?P<alpha>[0-9a-fA-F]{2})?$")


class SettingsBackend(Protocol):
    def get(
        self,
        key: str,
        scope_preference: str | None = None,
        *,
        default: Any = None,
    ) -> Any:
        ...

    def set(self, key: str, value: Any, scope: str) -> None:
        ...

    def save_all(
        self,
        scopes: set[str] | None = None,
        *,
        only_dirty: bool = False,
        **kwargs: Any,
    ) -> set[str]:
        ...

    def reload_all(self) -> None:
        ...

    def restore_scope_defaults(self, scope: str) -> None:
        ...


@dataclass(slots=True)
class SchemaField:
    id: str
    key: str
    label: str
    type: str
    scope: str
    description: str = ""
    default: Any = None
    options: list[str] | list[dict[str, Any]] | None = None
    options_provider_id: str | None = None
    browse_provider_id: str | None = None
    browse_caption: str | None = None
    browse_file_filter: str | None = None
    browse_button_text: str | None = None
    action_id: str | None = None
    actions: list[dict[str, Any]] | None = None
    min: int | None = None
    max: int | None = None
    visible_when: list[dict[str, Any]] | None = None


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
    scope: str
    sections: list[SchemaSection]
    subcategory: str | None = None
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    scope_order: int | None = None
    category_order: int | None = None
    subcategory_order: int | None = None
    order: int | None = None


@dataclass(slots=True)
class SettingsSchema:
    pages: list[SchemaPage]


@dataclass(slots=True)
class FieldBinding:
    key: str
    scope: str
    widget: QWidget
    getter: Callable[[], Any]
    setter: Callable[[Any], None]
    on_change: Callable[[Callable[..., None]], None]
    validate: Callable[[], list[str]]
    persist: bool = True
    full_row: bool = False
    has_pending_changes: Callable[[], bool] | None = None
    apply_changes: Callable[[], list[str]] | None = None


@dataclass(slots=True)
class _FieldRowBinding:
    field: SchemaField
    binding: FieldBinding
    form: QFormLayout
    row: int
    label: QLabel | None = None


FieldFactory = Callable[[SchemaField, "SchemaSettingsDialog"], FieldBinding]
ActionHandler = Callable[[SchemaField, "SchemaSettingsDialog"], None]
OptionsProvider = Callable[[SchemaField, "SchemaSettingsDialog"], list[str] | list[dict[str, Any]]]
BrowseProvider = Callable[[SchemaField, "SchemaSettingsDialog", str], str | None]
SortKeyScope = Callable[[str], tuple[Any, ...]]
SortKeyGroup = Callable[[str, str], tuple[Any, ...]]
SortKeyPage = Callable[[SchemaPage], tuple[Any, ...]]
ScopeLabeler = Callable[[str], str]


def _parse_color_value(value: object) -> QColor:
    text = str(value or "").strip()
    if not text:
        return QColor()
    match = _HEX_COLOR_WITH_ALPHA_RE.fullmatch(text)
    if match:
        rgb = str(match.group("rgb") or "")
        alpha = str(match.group("alpha") or "")
        try:
            red = int(rgb[0:2], 16)
            green = int(rgb[2:4], 16)
            blue = int(rgb[4:6], 16)
            if alpha:
                return QColor(red, green, blue, int(alpha, 16))
            return QColor(red, green, blue)
        except Exception:
            return QColor()
    if text.startswith("#"):
        return QColor()
    return QColor(text)


class SchemaSettingsDialog(DialogWindow):
    """Generic schema-driven settings editor with pluggable field types."""

    BUILTIN_FIELD_TYPES: set[str] = {
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
        "button",
        "button_row",
    }

    def __init__(
        self,
        backend: SettingsBackend,
        schema: SettingsSchema,
        *,
        initial_page_id: str | None = None,
        on_applied: Callable[[], None] | None = None,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
        object_name: str = "SettingsDialog",
        window_title: str = "Settings",
        save_button_text: str = "Save",
        apply_button_text: str = "Apply",
        cancel_button_text: str = "Cancel",
        restore_button_text: str = "Restore Defaults (Current Scope)",
        scope_sort_key: SortKeyScope | None = None,
        group_sort_key: SortKeyGroup | None = None,
        page_sort_key: SortKeyPage | None = None,
        scope_labeler: ScopeLabeler | None = None,
        tree_expanded_paths_key: str | None = None,
        tree_expanded_paths_scope: str | None = None,
        save_all_kwargs: dict[str, Any] | None = None,
        field_factories: dict[str, FieldFactory] | None = None,
        action_handlers: dict[str, ActionHandler] | None = None,
        options_providers: dict[str, OptionsProvider] | None = None,
        browse_providers: dict[str, BrowseProvider] | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setObjectName(str(object_name or "SettingsDialog").strip() or "SettingsDialog")
        self.setWindowTitle(window_title)
        self.resize(1040, 720)

        self.backend = backend
        self.schema = schema
        self.initial_page_id = str(initial_page_id or "").strip() or None
        self.on_applied = on_applied
        self._scope_sort_key = scope_sort_key
        self._group_sort_key = group_sort_key
        self._page_sort_key = page_sort_key
        self._scope_labeler = scope_labeler
        self._tree_expanded_paths_key = str(tree_expanded_paths_key or "").strip() or None
        self._tree_expanded_paths_scope = str(tree_expanded_paths_scope or "").strip() or None
        self._save_all_kwargs: dict[str, Any] = dict(save_all_kwargs or {})
        self._field_factories: dict[str, FieldFactory] = dict(field_factories or {})
        self._action_handlers: dict[str, ActionHandler] = dict(action_handlers or {})
        self._options_providers: dict[str, OptionsProvider] = dict(options_providers or {})
        self._browse_providers: dict[str, BrowseProvider] = dict(browse_providers or {})

        self._ignore_changes = False
        self._dirty_scopes: set[str] = set()
        self._bindings_by_page: dict[int, list[FieldBinding]] = {}
        self._binding_lookup: dict[tuple[str, str], FieldBinding] = {}
        self._field_rows: list[_FieldRowBinding] = []
        self._persisted_tree_expanded_paths = self._load_tree_expanded_paths_from_backend()

        self._build_ui(
            save_button_text=save_button_text,
            apply_button_text=apply_button_text,
            cancel_button_text=cancel_button_text,
            restore_button_text=restore_button_text,
        )
        self._build_tree_and_pages()
        self._load_widgets_from_backend()
        if not self._select_page_by_id(self.initial_page_id):
            self._select_first_page()
        self._refresh_dirty_state()

    def register_field_factory(self, field_type: str, factory: FieldFactory) -> None:
        self._field_factories[str(field_type)] = factory

    def register_action_handler(self, action_id: str, handler: ActionHandler) -> None:
        self._action_handlers[str(action_id)] = handler

    def register_options_provider(self, provider_id: str, provider: OptionsProvider) -> None:
        self._options_providers[str(provider_id)] = provider

    def register_browse_provider(self, provider_id: str, provider: BrowseProvider) -> None:
        self._browse_providers[str(provider_id)] = provider

    def set_bound_value(self, *, key: str, scope: str, value: Any) -> None:
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
        self._refresh_conditional_visibility()

    def _build_ui(
        self,
        *,
        save_button_text: str,
        apply_button_text: str,
        cancel_button_text: str,
        restore_button_text: str,
    ) -> None:
        root_host = QWidget(self)
        self.set_content_widget(root_host)

        root = QVBoxLayout(root_host)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

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
        self.tree.setStyleSheet(
            "QTreeView { "
            "  outline: none; "
            "  show-decoration-selected: 0; "
            "  selection-background-color: transparent; "
            "} "
            "QTreeView::item { border: none; } "
            "QTreeView::item:selected { border: none; } "
        )
        self._apply_application_tree_font()
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

        self.btn_restore_scope = QPushButton(restore_button_text)
        self.btn_restore_scope.clicked.connect(self._on_restore_scope_defaults)
        footer.addWidget(self.btn_restore_scope)

        footer.addStretch(1)

        self.btn_cancel = QPushButton(cancel_button_text)
        self.btn_cancel.clicked.connect(self._on_cancel)
        footer.addWidget(self.btn_cancel)

        self.btn_apply = QPushButton(apply_button_text)
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_apply.setEnabled(False)
        footer.addWidget(self.btn_apply)

        self.btn_save = QPushButton(save_button_text)
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)
        footer.addWidget(self.btn_save)

        root.addLayout(footer)

    def _apply_application_tree_font(self) -> None:
        try:
            base_font = QApplication.font(self.tree)
            self.tree.setFont(base_font)
        except Exception:
            return

    def _scope_display_name(self, scope: str) -> str:
        text = str(scope or "").strip()
        if callable(self._scope_labeler):
            try:
                value = str(self._scope_labeler(text) or "").strip()
                if value:
                    return value
            except Exception:
                pass
        return text.capitalize() or "Default"

    @staticmethod
    def _normalize_order(raw: Any, default: int = 100) -> int:
        try:
            return int(raw)
        except Exception:
            return default

    def _build_tree_and_pages(self) -> None:
        runtime_expanded_paths = self._collect_tree_expanded_paths() if self.tree.topLevelItemCount() > 0 else None
        self.tree.clear()
        self._bindings_by_page.clear()
        self._binding_lookup.clear()
        self._field_rows.clear()
        while self.stack.count():
            page_widget = self.stack.widget(0)
            self.stack.removeWidget(page_widget)
            page_widget.deleteLater()

        grouped: dict[str, dict[str, dict[str, list[SchemaPage]]]] = {}
        scope_order_by_name: dict[str, int] = {}
        group_order_by_name: dict[tuple[str, str], int] = {}
        subcategory_order_by_name: dict[tuple[str, str, str], int] = {}
        for page in self.schema.pages:
            scope_label = self._scope_display_name(str(page.scope or ""))
            category = str(page.category or "").strip()
            if not category or category.lower() == scope_label.lower():
                category = ""
            subcategory = str(page.subcategory or "").strip()
            grouped.setdefault(scope_label, {}).setdefault(category, {}).setdefault(subcategory, []).append(page)

            scope_order = page.scope_order
            if scope_order is not None:
                current = scope_order_by_name.get(scope_label)
                normalized = self._normalize_order(scope_order)
                if current is None or normalized < current:
                    scope_order_by_name[scope_label] = normalized

            category_order = page.category_order
            if category and category_order is not None:
                group_key = (scope_label, category)
                current = group_order_by_name.get(group_key)
                normalized = self._normalize_order(category_order)
                if current is None or normalized < current:
                    group_order_by_name[group_key] = normalized

            subcategory_order = page.subcategory_order
            if category and subcategory and subcategory_order is not None:
                sub_key = (scope_label, category, subcategory)
                current = subcategory_order_by_name.get(sub_key)
                normalized = self._normalize_order(subcategory_order)
                if current is None or normalized < current:
                    subcategory_order_by_name[sub_key] = normalized

        def _default_scope_sort(scope_name: str) -> tuple[Any, ...]:
            return (scope_order_by_name.get(scope_name, 100), str(scope_name or "").lower())

        def _default_group_sort(scope_name: str, group_name: str) -> tuple[Any, ...]:
            text = str(group_name or "")
            if not text:
                return (-1, "")
            return (group_order_by_name.get((scope_name, text), 100), text.lower())

        def _default_page_sort(page_spec: SchemaPage) -> tuple[Any, ...]:
            return (
                self._normalize_order(page_spec.order),
                str(page_spec.title or "").lower(),
                str(page_spec.id or "").lower(),
            )

        def _default_subcategory_sort(scope_name: str, group_name: str, subcategory_name: str) -> tuple[Any, ...]:
            text = str(subcategory_name or "")
            return (
                subcategory_order_by_name.get((scope_name, group_name, text), 100),
                text.lower(),
            )

        scope_sort = self._scope_sort_key or _default_scope_sort
        group_sort = self._group_sort_key or _default_group_sort
        page_sort = self._page_sort_key or _default_page_sort

        for scope_name in sorted(grouped.keys(), key=scope_sort):
            scope_item = QTreeWidgetItem([scope_name])
            scope_item.setFlags(scope_item.flags() & ~Qt.ItemIsSelectable)
            font = scope_item.font(0)
            font.setBold(True)
            scope_item.setFont(0, font)
            scope_item.setData(0, Qt.UserRole, None)
            scope_path = (scope_name,)
            scope_item.setData(0, SETTINGS_TREE_NODE_KEY_ROLE, scope_path)
            self.tree.addTopLevelItem(scope_item)

            for group_name in sorted(grouped[scope_name].keys(), key=lambda value: group_sort(scope_name, value)):
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

                for subcategory in sorted(
                    by_subcategory.keys(),
                    key=lambda value: _default_subcategory_sort(scope_name, group_name, str(value)),
                ):
                    page_specs = by_subcategory[subcategory]
                    parent_item = group_parent
                    if subcategory:
                        sub_item = QTreeWidgetItem([subcategory])
                        sub_item.setFlags(sub_item.flags() & ~Qt.ItemIsSelectable)
                        sub_item.setData(0, Qt.UserRole, None)
                        sub_item.setData(0, SETTINGS_TREE_NODE_KEY_ROLE, group_path + (subcategory,))
                        group_parent.addChild(sub_item)
                        parent_item = sub_item

                    for page_spec in sorted(page_specs, key=page_sort):
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
                if group_parent.childCount() == 0 and group_parent.parent() is scope_item:
                    scope_item.removeChild(group_parent)

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

    def _load_tree_expanded_paths_from_backend(self) -> set[tuple[str, ...]] | None:
        if self._tree_expanded_paths_key is None or self._tree_expanded_paths_scope is None:
            return None
        stored = self.backend.get(
            self._tree_expanded_paths_key,
            scope_preference=self._tree_expanded_paths_scope,
            default=None,
        )
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
        if self._tree_expanded_paths_key is None or self._tree_expanded_paths_scope is None:
            return
        expanded_paths = self._collect_tree_expanded_paths()
        serialized_paths = [list(path) for path in sorted(expanded_paths)]
        try:
            self.backend.set(self._tree_expanded_paths_key, serialized_paths, self._tree_expanded_paths_scope)
            self._save_backend(scopes={self._tree_expanded_paths_scope}, only_dirty=True)
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

            for schema_field in section.fields:
                binding = self._create_field_binding(schema_field)
                page_bindings.append(binding)
                self._binding_lookup[(str(binding.scope), str(binding.key))] = binding
                binding.on_change(lambda *_args, scope=schema_field.scope: self._mark_dirty(scope))
                binding.on_change(lambda *_args: self._refresh_conditional_visibility())
                binding.widget.setObjectName(
                    f"SettingsField__{str(schema_field.scope or '').strip()}__{str(schema_field.id or '').strip()}"
                )

                field_type = str(schema_field.type or "")
                row_index = form.rowCount()
                if field_type == "checkbox" or bool(binding.full_row):
                    form.addRow(binding.widget)
                    self._field_rows.append(
                        _FieldRowBinding(
                            field=schema_field,
                            binding=binding,
                            form=form,
                            row=row_index,
                        )
                    )
                else:
                    label = QLabel(schema_field.label)
                    label.setObjectName(
                        f"SettingsLabel__{str(schema_field.scope or '').strip()}__{str(schema_field.id or '').strip()}"
                    )
                    if schema_field.description:
                        label.setToolTip(schema_field.description)
                        binding.widget.setToolTip(schema_field.description)
                    form.addRow(label, binding.widget)
                    self._field_rows.append(
                        _FieldRowBinding(
                            field=schema_field,
                            binding=binding,
                            form=form,
                            row=row_index,
                            label=label,
                        )
                    )

            section_layout.addLayout(form)
            content.addWidget(group)

        content.addStretch(1)
        scroll.setWidget(container)
        return scroll, page_bindings

    def _create_field_binding(self, field: SchemaField) -> FieldBinding:
        custom_factory = self._field_factories.get(str(field.type))
        if custom_factory is not None:
            return custom_factory(field, self)

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
            line.setPlaceholderText("#RRGGBB or #RRGGBBAA")

            swatch = QPushButton()
            swatch.setFixedSize(28, 24)
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
                color = _parse_color_value(line.text())
                if color.isValid():
                    rgba = f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"
                    swatch.setStyleSheet(
                        "QPushButton { "
                        f"background-color: {rgba}; "
                        "border: 1px solid #6a6a6a; "
                        "border-radius: 3px; "
                        "min-height: 0px; "
                        "min-width: 0px; "
                        "padding: 0px; "
                        "}"
                    )
                    swatch.setText("")
                    return
                swatch.setStyleSheet(
                    "QPushButton { "
                    "background-color: #2f2f2f; "
                    "border: 1px solid #6a6a6a; "
                    "border-radius: 3px; "
                    "min-height: 0px; "
                    "min-width: 0px; "
                    "padding: 0px; "
                    "}"
                )
                swatch.setText("?")

            def pick_color() -> None:
                current_text = str(line.text() or "").strip()
                initial = _parse_color_value(current_text)
                if not initial.isValid():
                    initial = QColor("#ffffff")
                dialog = ColorPickerDialog(initial, self)
                if dialog.exec() != int(QDialog.DialogCode.Accepted):
                    return
                picked = dialog.get_color()
                if isinstance(picked, QColor) and picked.isValid():
                    had_alpha = bool(_HEX_COLOR_WITH_ALPHA_RE.fullmatch(current_text)) and len(current_text) == 9
                    if had_alpha or picked.alpha() < 255:
                        line.setText(
                            f"#{picked.red():02x}{picked.green():02x}{picked.blue():02x}{picked.alpha():02x}"
                        )
                    else:
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
                if _parse_color_value(text).isValid():
                    return []
                return [f"{field.label}: invalid color."]

            return FieldBinding(field.key, field.scope, holder, get_value, set_value, connect_change, validate)

        if field.type == "button":
            widget = QPushButton(field.label or field.id or "Action")
            if field.description:
                widget.setToolTip(field.description)
            action_id = str(field.action_id or "").strip()

            def on_click() -> None:
                self._trigger_action(action_id, field)

            widget.clicked.connect(on_click)
            return FieldBinding(
                field.key,
                field.scope,
                widget,
                lambda: None,
                lambda _value: None,
                lambda _cb: None,
                lambda: [],
                persist=False,
                full_row=True,
            )

        if field.type == "button_row":
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            raw_actions = field.actions if isinstance(field.actions, list) else []
            for raw in raw_actions:
                action_spec = raw if isinstance(raw, dict) else {}
                action_id = str(action_spec.get("id") or "").strip()
                label = str(action_spec.get("label") or action_id or "Action").strip()
                if not action_id:
                    continue
                button = QPushButton(label)
                tooltip = str(action_spec.get("description") or "").strip()
                if tooltip:
                    button.setToolTip(tooltip)
                button.clicked.connect(lambda _checked=False, aid=action_id: self._trigger_action(aid, field))
                row.addWidget(button)
            row.addStretch(1)
            return FieldBinding(
                field.key,
                field.scope,
                holder,
                lambda: None,
                lambda _value: None,
                lambda _cb: None,
                lambda: [],
                persist=False,
                full_row=True,
            )

        if field.type in {"lineedit", "path_dir", "path_file"}:
            line = QLineEdit()
            line.setPlaceholderText(field.description or "")

            control: QWidget = line
            connect_signal = line.textChanged.connect
            browse_provider = self._resolve_browse_provider(field)
            if field.type in {"path_dir", "path_file"} or browse_provider is not None:
                holder = QWidget()
                row = QHBoxLayout(holder)
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(6)
                browse = QPushButton(str(field.browse_button_text or "").strip() or "Browse")
                browse.setFixedWidth(80)

                def on_browse() -> None:
                    selected = self._run_browse(field=field, current_text=str(line.text() or "").strip())
                    if selected is not None:
                        line.setText(str(selected))

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
                initial = str(line.text() or "").strip()

                initial_font = QApplication.font(self)
                if initial and initial_font.family() != initial:
                    initial_font.setFamily(initial)
                # Some style/font setups can provide a non-positive point size.
                # Ensure we pass a valid point size to avoid Qt warnings.
                if initial_font.pointSizeF() <= 0:
                    initial_font.setPointSize(10)

                result = QFontDialog.getFont(
                    initial_font,
                    self,
                    "Select Font Family",
                )
                selected_font: QFont | None = None
                accepted = False
                if isinstance(result, tuple) and len(result) >= 2:
                    first, second = result[0], result[1]
                    if isinstance(first, QFont) and isinstance(second, bool):
                        selected_font = first
                        accepted = second
                    elif isinstance(first, bool) and isinstance(second, QFont):
                        accepted = first
                        selected_font = second

                if not accepted or selected_font is None:
                    return
                family = str(selected_font.family() or "").strip()
                if family:
                    line.setText(family)
                elif initial:
                    line.setText(initial)

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
            widget.setRange(
                field.min if field.min is not None else -2147483648,
                field.max if field.max is not None else 2147483647,
            )

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
            options = self._resolve_options(field)
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
                row_index = widget.currentRow()
                if row_index >= 0:
                    widget.takeItem(row_index)
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

        fallback = QLabel(
            f"Unsupported field type: {field.type}. "
            "Register a custom field factory with register_field_factory()."
        )
        return FieldBinding(field.key, field.scope, fallback, lambda: None, lambda _v: None, lambda _c: None, lambda: [])

    def _resolve_options(self, field: SchemaField) -> list[str] | list[dict[str, Any]]:
        provider_id = str(field.options_provider_id or "").strip()
        if not provider_id:
            return field.options or []
        provider = self._options_providers.get(provider_id)
        if provider is None:
            return field.options or []
        try:
            provided = provider(field, self)
        except Exception:
            return field.options or []
        if isinstance(provided, list):
            return cast(list[str] | list[dict[str, Any]], provided)
        return field.options or []

    def _resolve_browse_provider(self, field: SchemaField) -> BrowseProvider | None:
        provider_id = str(field.browse_provider_id or "").strip()
        if not provider_id and field.type in {"path_dir", "path_file"}:
            provider_id = field.type
        if not provider_id:
            return None
        return self._browse_providers.get(provider_id)

    def _run_browse(self, *, field: SchemaField, current_text: str) -> str | None:
        provider = self._resolve_browse_provider(field)
        if provider is not None:
            try:
                result = provider(field, self, current_text)
            except Exception:
                return None
            if result is None:
                return None
            return str(result)

        if field.type == "path_file":
            selected, _selected_filter, _starred = FileDialog.getOpenFileName(
                self,
                str(field.browse_caption or "Select File"),
                current_text,
                str(field.browse_file_filter or "All Files (*)"),
            )
            return str(selected) if selected else None
        if field.type == "path_dir":
            selected, _starred = FileDialog.getExistingDirectory(
                self,
                str(field.browse_caption or "Select Directory"),
                current_text,
            )
            return str(selected) if selected else None
        return None

    def _trigger_action(self, action_id: str, field: SchemaField) -> None:
        action_key = str(action_id or "").strip()
        if not action_key:
            return
        handler = self._action_handlers.get(action_key)
        if handler is None:
            QMessageBox.warning(self, "Missing action handler", f"No action handler registered for '{action_key}'.")
            return
        try:
            handler(field, self)
        except Exception as exc:
            QMessageBox.warning(self, "Action failed", str(exc))

    def _all_schema_scopes(self) -> set[str]:
        scopes: set[str] = set()
        for page in self.schema.pages:
            page_scope = str(page.scope or "").strip()
            if page_scope:
                scopes.add(page_scope)
            for section in page.sections:
                for schema_field in section.fields:
                    field_scope = str(schema_field.scope or "").strip()
                    if field_scope:
                        scopes.add(field_scope)
        return scopes

    def _load_widgets_from_backend(self) -> None:
        self._ignore_changes = True
        try:
            for bindings in self._bindings_by_page.values():
                for binding in bindings:
                    if not binding.persist:
                        continue
                    value = self.backend.get(binding.key, scope_preference=binding.scope, default=None)
                    if value is None:
                        schema_default = self._schema_default_for(binding.key, binding.scope)
                        if schema_default is not None:
                            value = schema_default
                    binding.setter(value)
        finally:
            self._ignore_changes = False
        self._refresh_conditional_visibility()

    def _bound_value_for(self, *, key: str, scope: str, default: Any = None) -> Any:
        binding = self._binding_lookup.get((str(scope), str(key)))
        if binding is not None:
            try:
                return binding.getter()
            except Exception:
                return default
        return self.backend.get(key, scope_preference=scope, default=default)

    def _field_is_visible(self, field: SchemaField) -> bool:
        rules = field.visible_when if isinstance(field.visible_when, list) else None
        if not rules:
            return True
        for raw_rule in rules:
            rule = raw_rule if isinstance(raw_rule, dict) else {}
            key = str(rule.get("key") or "").strip()
            if not key:
                continue
            scope = str(rule.get("scope") or field.scope or "").strip()
            expected = rule.get("equals", rule.get("value"))
            current = self._bound_value_for(key=key, scope=scope)
            if current != expected:
                return False
        return True

    def _set_form_row_visible(self, row_binding: _FieldRowBinding, visible: bool) -> None:
        try:
            row_binding.form.setRowVisible(row_binding.row, visible)
            return
        except Exception:
            pass
        if row_binding.label is not None:
            row_binding.label.setVisible(visible)
        row_binding.binding.widget.setVisible(visible)

    def _refresh_conditional_visibility(self) -> None:
        for row_binding in self._field_rows:
            self._set_form_row_visible(
                row_binding,
                self._field_is_visible(row_binding.field),
            )

    def _schema_default_for(self, key: str, scope: str) -> Any:
        for page in self.schema.pages:
            for section in page.sections:
                for schema_field in section.fields:
                    if str(schema_field.key) == str(key) and str(schema_field.scope) == str(scope):
                        return schema_field.default
        return None

    def _collect_all_widget_values(self) -> dict[str, list[tuple[str, Any]]]:
        collected: dict[str, list[tuple[str, Any]]] = {}
        for bindings in self._bindings_by_page.values():
            for binding in bindings:
                if not binding.persist:
                    continue
                collected.setdefault(binding.scope, []).append((binding.key, binding.getter()))
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
            scope_name = self._scope_display_name(spec.scope)
            self.page_scope.setText(f"Scope: {scope_name}")
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
                for schema_field in section.fields:
                    field_tokens.append(schema_field.label)
                    field_tokens.append(schema_field.key)
                    field_tokens.append(schema_field.scope)
                    field_tokens.append(schema_field.description)
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

    def _mark_dirty(self, scope: str) -> None:
        if self._ignore_changes:
            return
        self._dirty_scopes.add(str(scope))
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
                scope_text = ", ".join(sorted(self._scope_display_name(scope) for scope in self._dirty_scopes))
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
        touched_scopes: set[str] = set()
        for scope, entries in values_by_scope.items():
            touched_scopes.add(scope)
            for key, value in entries:
                self.backend.set(key, value, scope)
                self._dirty_scopes.add(scope)

        custom_errors = self._apply_custom_pages()
        if custom_errors:
            QMessageBox.warning(self, "Invalid settings", "\n".join(custom_errors[:5]))
            return False

        target_scopes = set(self._dirty_scopes)
        target_scopes.update(touched_scopes)
        target_scopes.update(self._all_schema_scopes())
        if target_scopes:
            saved = self._save_backend(scopes=target_scopes, only_dirty=True)
            if isinstance(saved, set):
                saved_scopes = cast(set[str], saved)
                self._dirty_scopes -= saved_scopes
            else:
                self._dirty_scopes.clear()

        self._apply_application_tree_font()
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
                "Discard unsaved changes in modified scopes?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        self.backend.reload_all()
        self.reject()

    def _on_restore_scope_defaults(self) -> None:
        spec = self._current_page_spec()
        if spec is None:
            return

        answer = QMessageBox.question(
            self,
            "Restore defaults",
            f"Restore defaults for {self._scope_display_name(spec.scope)} scope?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        current_page_id = spec.id
        self.backend.restore_scope_defaults(spec.scope)
        self._build_tree_and_pages()
        self._load_widgets_from_backend()
        if not self._select_page_by_id(current_page_id):
            self._select_first_page()

        self._dirty_scopes.add(spec.scope)
        self._refresh_dirty_state()

    def _save_backend(self, *, scopes: set[str], only_dirty: bool) -> set[str] | Any:
        if self._save_all_kwargs:
            try:
                return self.backend.save_all(
                    scopes=scopes,
                    only_dirty=only_dirty,
                    **self._save_all_kwargs,
                )
            except TypeError:
                pass
        try:
            return self.backend.save_all(scopes=scopes, only_dirty=only_dirty)
        except TypeError:
            return self.backend.save_all(scopes=scopes)
