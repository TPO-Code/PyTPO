from __future__ import annotations

from copy import deepcopy
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class FileTemplatesSettingsPage(QWidget):
    def __init__(self, *, manager: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._entries: list[dict[str, str]] = []
        self._base_entries: list[dict[str, str]] = []
        self._syncing = False
        self._build_ui()
        self._load_from_settings()

    @staticmethod
    def _normalize_extension(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        normalized = "." + text.lstrip(".")
        return "" if normalized == "." else normalized

    @staticmethod
    def _normalize_path(value: object) -> str:
        text = str(value or "").strip().replace("\\", "/")
        parts = [part.strip() for part in text.split("/") if str(part).strip()]
        return "/".join(parts)

    @staticmethod
    def _path_segments(path: str) -> list[str]:
        return [part for part in str(path or "").split("/") if part]

    @classmethod
    def _flatten_templates(cls, templates: object) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []

        def _walk(raw_nodes: object, breadcrumb: list[str]) -> None:
            if not isinstance(raw_nodes, list):
                return
            for raw_node in raw_nodes:
                if not isinstance(raw_node, dict):
                    continue
                label = str(raw_node.get("label") or "").strip()
                if not label:
                    continue
                children = raw_node.get("children")
                if isinstance(children, list):
                    _walk(children, [*breadcrumb, label])
                    continue
                mode = str(raw_node.get("mode") or "prompt").strip().lower()
                if mode not in {"prompt", "fixed"}:
                    mode = "prompt"
                path = cls._normalize_path("/".join([*breadcrumb, label]))
                if not path:
                    continue
                result.append(
                    {
                        "path": path,
                        "mode": mode,
                        "fixed_name": str(raw_node.get("fixed_name") or "").strip(),
                        "default_extension": cls._normalize_extension(raw_node.get("default_extension")),
                        "content": str(raw_node.get("content") or ""),
                    }
                )

        _walk(templates, [])
        return sorted(result, key=lambda entry: str(entry.get("path") or "").lower())

    @classmethod
    def _build_templates_from_entries(cls, entries: list[dict[str, str]]) -> list[dict[str, Any]]:
        node = {"groups": {}, "templates": []}
        for entry in sorted(entries, key=lambda item: str(item.get("path") or "").lower()):
            path = cls._normalize_path(entry.get("path"))
            parts = cls._path_segments(path)
            if not parts:
                continue
            cursor = node
            for group in parts[:-1]:
                groups = cursor["groups"]
                if group not in groups:
                    groups[group] = {"groups": {}, "templates": []}
                cursor = groups[group]
            cursor["templates"].append(
                {
                    "label": parts[-1],
                    "mode": str(entry.get("mode") or "prompt").strip().lower() or "prompt",
                    "fixed_name": str(entry.get("fixed_name") or "").strip(),
                    "default_extension": cls._normalize_extension(entry.get("default_extension")),
                    "content": str(entry.get("content") or ""),
                }
            )

        def _emit(cursor: dict[str, Any]) -> list[dict[str, Any]]:
            combined: list[dict[str, Any]] = []
            groups = cursor.get("groups", {})
            templates = cursor.get("templates", [])
            if isinstance(groups, dict):
                for label, group_node in groups.items():
                    combined.append(
                        {
                            "kind": "group",
                            "label": str(label),
                            "children": _emit(group_node if isinstance(group_node, dict) else {"groups": {}, "templates": []}),
                        }
                    )
            if isinstance(templates, list):
                for template in templates:
                    if not isinstance(template, dict):
                        continue
                    combined.append(
                        {
                            "kind": "template",
                            "label": str(template.get("label") or "").strip(),
                            "mode": str(template.get("mode") or "prompt").strip().lower(),
                            "fixed_name": str(template.get("fixed_name") or "").strip(),
                            "default_extension": cls._normalize_extension(template.get("default_extension")),
                            "content": str(template.get("content") or ""),
                        }
                    )

            combined.sort(key=lambda item: str(item.get("label") or "").lower())
            output: list[dict[str, Any]] = []
            for item in combined:
                if str(item.get("kind")) == "group":
                    label = str(item.get("label") or "").strip()
                    children = item.get("children")
                    if not label or not isinstance(children, list) or not children:
                        continue
                    output.append({"label": label, "children": children})
                    continue
                label = str(item.get("label") or "").strip()
                if not label:
                    continue
                mode = str(item.get("mode") or "prompt").strip().lower()
                if mode not in {"prompt", "fixed"}:
                    mode = "prompt"
                output.append(
                    {
                        "label": label,
                        "mode": mode,
                        "fixed_name": str(item.get("fixed_name") or "").strip(),
                        "default_extension": cls._normalize_extension(item.get("default_extension")),
                        "content": str(item.get("content") or ""),
                    }
                )
            return output

        return _emit(node)

    @staticmethod
    def _snapshot(entries: list[dict[str, str]]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            normalized.append(
                {
                    "path": str(entry.get("path") or "").strip(),
                    "mode": str(entry.get("mode") or "").strip().lower(),
                    "fixed_name": str(entry.get("fixed_name") or "").strip(),
                    "default_extension": str(entry.get("default_extension") or "").strip(),
                    "content": str(entry.get("content") or ""),
                }
            )
        return sorted(normalized, key=lambda item: str(item.get("path") or "").lower())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        info = QLabel(
            "Template Path uses '/' to describe the menu location.\n"
            "Example: Python/Class -> New File -> Python -> Class"
        )
        info.setWordWrap(True)
        root.addWidget(info)

        splitter = QSplitter(Qt.Horizontal, self)
        root.addWidget(splitter, 1)

        left_host = QWidget(splitter)
        left_layout = QVBoxLayout(left_host)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.template_tree = QTreeWidget(left_host)
        self.template_tree.setHeaderHidden(True)
        self.template_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.template_tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        left_layout.addWidget(self.template_tree, 1)

        left_actions = QHBoxLayout()
        left_actions.setContentsMargins(0, 0, 0, 0)
        self.add_btn = QPushButton("Add Template", left_host)
        self.remove_btn = QPushButton("Remove", left_host)
        left_actions.addWidget(self.add_btn)
        left_actions.addWidget(self.remove_btn)
        left_actions.addStretch(1)
        left_layout.addLayout(left_actions)

        self.add_btn.clicked.connect(self._add_template)
        self.remove_btn.clicked.connect(self._remove_selected_template)

        right_host = QWidget(splitter)
        right_layout = QVBoxLayout(right_host)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.path_edit = QLineEdit(right_host)
        self.path_edit.setPlaceholderText("Python/Class")

        self.mode_combo = QComboBox(right_host)
        self.mode_combo.addItem("Prompt For Name", "prompt")
        self.mode_combo.addItem("Fixed Name", "fixed")

        self.fixed_name_edit = QLineEdit(right_host)
        self.fixed_name_edit.setPlaceholderText("__init__.py")

        self.extension_edit = QLineEdit(right_host)
        self.extension_edit.setPlaceholderText(".py")

        self.content_edit = QTextEdit(right_host)
        self.content_edit.setAcceptRichText(False)
        self.content_edit.setMinimumHeight(180)

        form.addRow("Template Path", self.path_edit)
        form.addRow("Mode", self.mode_combo)
        form.addRow("Fixed Name", self.fixed_name_edit)
        form.addRow("Default Extension", self.extension_edit)
        form.addRow("Content", self.content_edit)
        right_layout.addLayout(form)

        self.status_label = QLabel("", right_host)
        self.status_label.setWordWrap(True)
        right_layout.addWidget(self.status_label)
        right_layout.addStretch(1)

        self.path_edit.textChanged.connect(self._on_editor_changed)
        self.mode_combo.currentIndexChanged.connect(self._on_editor_changed)
        self.fixed_name_edit.textChanged.connect(self._on_editor_changed)
        self.extension_edit.textChanged.connect(self._on_editor_changed)
        self.content_edit.textChanged.connect(self._on_editor_changed)

        splitter.addWidget(left_host)
        splitter.addWidget(right_host)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 5)

    def _load_from_settings(self) -> None:
        raw = self._manager.get("file_templates", scope_preference="ide", default=[])
        self._entries = self._flatten_templates(raw)
        self._base_entries = deepcopy(self._entries)
        selected = self._entries[0]["path"] if self._entries else None
        self._rebuild_tree(select_path=selected)
        self._refresh_editor_enabled_state()

    def _entry_index_for_path(self, path: str) -> int:
        target = str(path or "").strip()
        for idx, entry in enumerate(self._entries):
            if str(entry.get("path") or "").strip() == target:
                return idx
        return -1

    def _path_can_update_tree(self, raw_value: object, *, exclude_index: int = -1) -> tuple[bool, str]:
        text = str(raw_value or "").replace("\\", "/")
        stripped = text.strip()
        if not stripped:
            return False, ""
        if stripped.startswith("/") or stripped.endswith("/"):
            return False, ""
        if "//" in stripped:
            return False, ""
        parts = [part.strip() for part in stripped.split("/")]
        if any(not part for part in parts):
            return False, ""
        if any(part in {".", ".."} for part in parts):
            return False, ""
        normalized = "/".join(parts)
        dedupe = normalized.lower()
        for idx, entry in enumerate(self._entries):
            if idx == exclude_index:
                continue
            if str(entry.get("path") or "").strip().lower() == dedupe:
                return False, ""
        return True, normalized

    def _selected_template_path(self) -> str:
        item = self.template_tree.currentItem()
        if item is None:
            return ""
        if str(item.data(0, Qt.UserRole + 1) or "") != "template":
            return ""
        return str(item.data(0, Qt.UserRole) or "").strip()

    def _selected_group_path(self) -> str:
        item = self.template_tree.currentItem()
        if item is None:
            return ""
        kind = str(item.data(0, Qt.UserRole + 1) or "")
        if kind == "group":
            return str(item.data(0, Qt.UserRole + 2) or "").strip()
        if kind == "template":
            template_path = str(item.data(0, Qt.UserRole) or "").strip()
            parts = self._path_segments(template_path)
            if len(parts) <= 1:
                return ""
            return "/".join(parts[:-1])
        return ""

    def _rebuild_tree(self, *, select_path: str | None = None) -> None:
        self._syncing = True
        try:
            self.template_tree.clear()
            group_items: dict[tuple[str, ...], QTreeWidgetItem] = {}
            for entry in sorted(self._entries, key=lambda item: str(item.get("path") or "").lower()):
                path = self._normalize_path(entry.get("path"))
                parts = self._path_segments(path)
                if not parts:
                    continue
                parent: QTreeWidgetItem | None = None
                group_parts: list[str] = []
                for group in parts[:-1]:
                    group_parts.append(group)
                    key = tuple(group_parts)
                    existing = group_items.get(key)
                    if existing is not None:
                        parent = existing
                        continue
                    group_item = QTreeWidgetItem([group])
                    group_item.setData(0, Qt.UserRole + 1, "group")
                    group_item.setData(0, Qt.UserRole + 2, "/".join(group_parts))
                    if parent is None:
                        self.template_tree.addTopLevelItem(group_item)
                    else:
                        parent.addChild(group_item)
                    group_items[key] = group_item
                    parent = group_item

                leaf = QTreeWidgetItem([parts[-1]])
                leaf.setData(0, Qt.UserRole, path)
                leaf.setData(0, Qt.UserRole + 1, "template")
                if parent is None:
                    self.template_tree.addTopLevelItem(leaf)
                else:
                    parent.addChild(leaf)

            self.template_tree.sortItems(0, Qt.AscendingOrder)
            for idx in range(self.template_tree.topLevelItemCount()):
                top = self.template_tree.topLevelItem(idx)
                if top is not None:
                    self._sort_tree_recursive(top)

            chosen = str(select_path or "").strip()
            target_item = self._find_template_item(chosen) if chosen else None
            if target_item is None:
                target_item = self._first_template_item()
            if target_item is not None:
                self.template_tree.setCurrentItem(target_item)
        finally:
            self._syncing = False
        self._on_tree_selection_changed()
        self._refresh_editor_enabled_state()

    def _find_template_item(self, path: str) -> QTreeWidgetItem | None:
        if not path:
            return None
        stack: list[QTreeWidgetItem] = []
        for idx in range(self.template_tree.topLevelItemCount()):
            top = self.template_tree.topLevelItem(idx)
            if top is not None:
                stack.append(top)
        while stack:
            item = stack.pop()
            if str(item.data(0, Qt.UserRole + 1) or "") == "template" and str(item.data(0, Qt.UserRole) or "").strip() == path:
                return item
            for child_idx in range(item.childCount()):
                child = item.child(child_idx)
                if child is not None:
                    stack.append(child)
        return None

    def _sort_tree_recursive(self, item: QTreeWidgetItem) -> None:
        item.sortChildren(0, Qt.AscendingOrder)
        for idx in range(item.childCount()):
            child = item.child(idx)
            if child is not None:
                self._sort_tree_recursive(child)

    def _first_template_item(self) -> QTreeWidgetItem | None:
        stack: list[QTreeWidgetItem] = []
        for idx in range(self.template_tree.topLevelItemCount()):
            top = self.template_tree.topLevelItem(idx)
            if top is not None:
                stack.append(top)
        while stack:
            item = stack.pop(0)
            if str(item.data(0, Qt.UserRole + 1) or "") == "template":
                return item
            for child_idx in range(item.childCount()):
                child = item.child(child_idx)
                if child is not None:
                    stack.append(child)
        return None

    def _refresh_editor_enabled_state(self) -> None:
        has_leaf = bool(self._selected_template_path())
        self.path_edit.setEnabled(has_leaf)
        self.mode_combo.setEnabled(has_leaf)
        self.fixed_name_edit.setEnabled(has_leaf and self.mode_combo.currentData() == "fixed")
        self.extension_edit.setEnabled(has_leaf)
        self.content_edit.setEnabled(has_leaf)
        self.remove_btn.setEnabled(has_leaf)

    def _on_tree_selection_changed(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            template_path = self._selected_template_path()
            idx = self._entry_index_for_path(template_path) if template_path else -1
            if idx < 0:
                self.path_edit.setText("")
                self.mode_combo.setCurrentIndex(0)
                self.fixed_name_edit.setText("")
                self.extension_edit.setText("")
                self.content_edit.setPlainText("")
            else:
                entry = self._entries[idx]
                self.path_edit.setText(str(entry.get("path") or "").strip())
                mode = str(entry.get("mode") or "prompt").strip().lower()
                mode_idx = self.mode_combo.findData(mode)
                self.mode_combo.setCurrentIndex(mode_idx if mode_idx >= 0 else 0)
                self.fixed_name_edit.setText(str(entry.get("fixed_name") or "").strip())
                self.extension_edit.setText(str(entry.get("default_extension") or "").strip())
                self.content_edit.setPlainText(str(entry.get("content") or ""))
        finally:
            self._syncing = False
        self._refresh_editor_enabled_state()

    def _unique_path(self, desired_path: str, *, exclude_index: int = -1) -> str:
        target = self._normalize_path(desired_path)
        if not target:
            return ""
        existing = {
            str(entry.get("path") or "").strip().lower()
            for idx, entry in enumerate(self._entries)
            if idx != exclude_index
        }
        if target.lower() not in existing:
            return target
        parts = self._path_segments(target)
        if not parts:
            return target
        base = parts[-1]
        prefix = parts[:-1]
        counter = 2
        while True:
            candidate_leaf = f"{base} ({counter})"
            candidate = "/".join([*prefix, candidate_leaf]) if prefix else candidate_leaf
            if candidate.lower() not in existing:
                return candidate
            counter += 1

    def _add_template(self) -> None:
        group_path = self._selected_group_path()
        base_path = "/".join([group_path, "New Template"]) if group_path else "New Template"
        unique_path = self._unique_path(base_path)
        if not unique_path:
            return
        self._entries.append(
            {
                "path": unique_path,
                "mode": "prompt",
                "fixed_name": "",
                "default_extension": "",
                "content": "",
            }
        )
        self._rebuild_tree(select_path=unique_path)
        self.path_edit.setFocus()
        self.path_edit.selectAll()
        self._notify_pending_changed()

    def _remove_selected_template(self) -> None:
        template_path = self._selected_template_path()
        idx = self._entry_index_for_path(template_path)
        if idx < 0:
            return
        del self._entries[idx]
        self._rebuild_tree()
        self._notify_pending_changed()

    def _on_editor_changed(self) -> None:
        if self._syncing:
            return
        template_path = self._selected_template_path()
        idx = self._entry_index_for_path(template_path)
        if idx < 0:
            self._refresh_editor_enabled_state()
            return

        previous_path = str(self._entries[idx].get("path") or "").strip()
        mode = str(self.mode_combo.currentData() or "prompt").strip().lower()
        if mode not in {"prompt", "fixed"}:
            mode = "prompt"
        fixed_name = str(self.fixed_name_edit.text() or "").strip()
        extension = self._normalize_extension(self.extension_edit.text())
        content = str(self.content_edit.toPlainText() or "")

        can_update_tree, committed_path = self._path_can_update_tree(self.path_edit.text(), exclude_index=idx)
        next_path = committed_path if can_update_tree else previous_path
        self._entries[idx] = {
            "path": next_path,
            "mode": mode,
            "fixed_name": fixed_name,
            "default_extension": extension,
            "content": content,
        }

        self._syncing = True
        try:
            if str(self.extension_edit.text() or "").strip() != extension:
                self.extension_edit.setText(extension)
        finally:
            self._syncing = False
        self._refresh_editor_enabled_state()
        if can_update_tree and next_path != previous_path:
            self._rebuild_tree(select_path=next_path)
        self._notify_pending_changed()

    def has_pending_settings_changes(self) -> bool:
        return self._snapshot(self._entries) != self._snapshot(self._base_entries)

    def _validate_entries(self) -> tuple[list[dict[str, str]], list[str]]:
        normalized: list[dict[str, str]] = []
        errors: list[str] = []
        seen_paths: set[str] = set()
        for idx, entry in enumerate(self._entries):
            row = idx + 1
            path = self._normalize_path(entry.get("path"))
            parts = self._path_segments(path)
            if not parts:
                errors.append(f"Template {row}: path is required.")
                continue
            if any(part in {".", ".."} for part in parts):
                errors.append(f"Template {row}: path contains invalid segments.")
                continue
            dedupe = path.lower()
            if dedupe in seen_paths:
                errors.append(f"Template {row}: duplicate path '{path}'.")
                continue
            seen_paths.add(dedupe)

            mode = str(entry.get("mode") or "prompt").strip().lower()
            if mode not in {"prompt", "fixed"}:
                mode = "prompt"
            fixed_name = str(entry.get("fixed_name") or "").strip()
            if mode == "fixed" and not fixed_name:
                errors.append(f"Template {row} ({path}): fixed_name is required for fixed mode.")
                continue

            normalized.append(
                {
                    "path": path,
                    "mode": mode,
                    "fixed_name": fixed_name,
                    "default_extension": self._normalize_extension(entry.get("default_extension")),
                    "content": str(entry.get("content") or ""),
                }
            )

        return normalized, errors

    def apply_settings_changes(self) -> list[str]:
        normalized_entries, errors = self._validate_entries()
        if errors:
            self._set_status("\n".join(errors[:4]), error=True)
            return errors
        templates = self._build_templates_from_entries(normalized_entries)
        try:
            self._manager.set("file_templates", templates, "ide")
        except Exception as exc:
            err = str(exc)
            self._set_status(err, error=True)
            return [err]

        self._entries = deepcopy(normalized_entries)
        self._base_entries = deepcopy(normalized_entries)
        selected = self._selected_template_path()
        self._rebuild_tree(select_path=selected)
        self._set_status("File template changes saved.", error=False)
        self._notify_pending_changed()
        return []

    def _set_status(self, text: str, *, error: bool) -> None:
        color = "#d46a6a" if error else "#a4bf7a"
        self.status_label.setText(f"<span style='color:{color};'>{text}</span>")

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
