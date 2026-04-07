from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from TPOPyside.dialogs.custom_dialog import DialogWindow
from barley_ide.ui.widgets.spellcheck_inputs import SpellcheckLineEdit, SpellcheckPlainTextEdit

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PARAM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KIND_ROLE = int(Qt.UserRole) + 1
_PATH_ROLE = int(Qt.UserRole) + 2
_NODE_GROUP = "group"
_NODE_ENTRY = "entry"


def _normalize_label_path(raw: object) -> str:
    text = str(raw or "").replace("\\", "/")
    parts = [part.strip() for part in text.split("/") if part.strip()]
    return "/".join(parts)


def _split_label_path(path: str) -> list[str]:
    return [part for part in str(path or "").split("/") if part]


def _parse_param_list(raw: object) -> list[str]:
    if isinstance(raw, list):
        values = [str(item or "").strip() for item in raw]
    else:
        values = [part.strip() for part in str(raw or "").split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or not _PARAM_RE.match(value):
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _parse_env_lines(raw: object) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        entries = [f"{key}={value}" for key, value in raw.items()]
    else:
        text = str(raw or "")
        entries = [line.strip() for line in text.splitlines() if line.strip()]

    for entry in entries:
        if "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        key = key.strip()
        if not key or not _ENV_KEY_RE.match(key):
            continue
        out[key] = value
    return out


def _entry_sort_key(entry: dict[str, Any]) -> tuple[str, str]:
    label = _normalize_label_path(entry.get("label"))
    cmd = str(entry.get("cmd") or "")
    return (label.lower(), cmd.lower())


def _normalize_entries(raw_entries: object, *, allow_params: bool) -> list[dict[str, Any]]:
    if not isinstance(raw_entries, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        label = _normalize_label_path(raw.get("label"))
        cmd = str(raw.get("cmd") or "").strip()
        if not label or not cmd:
            continue
        entry: dict[str, Any] = {
            "label": label,
            "cmd": cmd,
            "cwd": str(raw.get("cwd") or "").strip(),
            "env": _parse_env_lines(raw.get("env")),
            "dryrun": bool(raw.get("dryrun", False)),
            "params": [],
        }
        if allow_params:
            entry["params"] = _parse_param_list(raw.get("params"))
        out.append(entry)
    out.sort(key=_entry_sort_key)
    return out


class _CommandEntryDialog(DialogWindow):
    def __init__(
        self,
        *,
        mode: str,
        initial: dict[str, Any] | None = None,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self._mode = "template" if mode == "template" else "quick"
        self._initial = deepcopy(initial) if isinstance(initial, dict) else {}

        self.setWindowTitle("Edit Template" if self._mode == "template" else "Edit Quick Command")
        self.resize(720, 500)

        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.label_edit = SpellcheckLineEdit()
        self.label_edit.setPlaceholderText("python/activate")
        self.label_help = QLabel("Use '/' in label to create menu subgroups.")
        self.label_help.setWordWrap(True)

        self.cmd_edit = SpellcheckPlainTextEdit()
        self.cmd_edit.setFixedHeight(110)
        self.cwd_edit = SpellcheckLineEdit()
        self.env_edit = SpellcheckPlainTextEdit()
        self.env_edit.setFixedHeight(90)

        self.params_edit = SpellcheckLineEdit()
        self.params_help = QLabel("Comma-separated parameter names (example: pkg, version)")
        self.params_help.setWordWrap(True)

        self.dryrun_chk = QCheckBox("Preview before running")

        label_host = QWidget()
        label_layout = QVBoxLayout(label_host)
        label_layout.setContentsMargins(0, 0, 0, 0)
        label_layout.setSpacing(4)
        label_layout.addWidget(self.label_edit)
        label_layout.addWidget(self.label_help)

        form.addRow("Label", label_host)
        form.addRow("Command", self.cmd_edit)
        if self._mode == "template":
            params_host = QWidget()
            params_layout = QVBoxLayout(params_host)
            params_layout.setContentsMargins(0, 0, 0, 0)
            params_layout.setSpacing(4)
            params_layout.addWidget(self.params_edit)
            params_layout.addWidget(self.params_help)
            form.addRow("Parameters", params_host)
        form.addRow("Working Directory", self.cwd_edit)
        form.addRow("Environment", self.env_edit)
        form.addRow("", self.dryrun_chk)

        root.addLayout(form, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=host)
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self._load_initial()

    def _load_initial(self) -> None:
        self.label_edit.setText(_normalize_label_path(self._initial.get("label")))
        self.cmd_edit.setPlainText(str(self._initial.get("cmd") or ""))
        self.cwd_edit.setText(str(self._initial.get("cwd") or ""))

        env = _parse_env_lines(self._initial.get("env"))
        if env:
            lines = [f"{key}={value}" for key, value in env.items()]
            self.env_edit.setPlainText("\n".join(lines))
        else:
            self.env_edit.setPlainText("")

        if self._mode == "template":
            params = _parse_param_list(self._initial.get("params"))
            self.params_edit.setText(", ".join(params))
        self.dryrun_chk.setChecked(bool(self._initial.get("dryrun", False)))

    def _on_accept(self) -> None:
        label = _normalize_label_path(self.label_edit.text())
        cmd = str(self.cmd_edit.toPlainText() or "").strip()
        if not label:
            self.status_label.setText("Label is required.")
            self.label_edit.setFocus()
            return
        if not cmd:
            self.status_label.setText("Command is required.")
            self.cmd_edit.setFocus()
            return

        if self._mode == "template":
            raw_params = str(self.params_edit.text() or "")
            parsed_params = _parse_param_list(raw_params)
            if raw_params.strip() and not parsed_params:
                self.status_label.setText("Parameters must be valid identifiers separated by commas.")
                self.params_edit.setFocus()
                return

        raw_env = str(self.env_edit.toPlainText() or "").strip()
        if raw_env:
            parsed_env = _parse_env_lines(raw_env)
            valid_count = len(parsed_env)
            raw_count = len([line for line in raw_env.splitlines() if line.strip()])
            if valid_count < raw_count:
                self.status_label.setText("Environment must be one KEY=VALUE pair per line.")
                self.env_edit.setFocus()
                return

        self.accept()

    def entry(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "label": _normalize_label_path(self.label_edit.text()),
            "cmd": str(self.cmd_edit.toPlainText() or "").strip(),
            "cwd": str(self.cwd_edit.text() or "").strip(),
            "env": _parse_env_lines(self.env_edit.toPlainText()),
            "dryrun": bool(self.dryrun_chk.isChecked()),
            "params": [],
        }
        if self._mode == "template":
            out["params"] = _parse_param_list(self.params_edit.text())
        return out


class TerminalCommandsDialog(DialogWindow):
    def __init__(
        self,
        *,
        initial_quick: list[dict[str, Any]] | None = None,
        initial_templates: list[dict[str, Any]] | None = None,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("Terminal Run Commands")
        self.resize(980, 640)

        self._quick_entries = _normalize_entries(initial_quick or [], allow_params=False)
        self._template_entries = _normalize_entries(initial_templates or [], allow_params=True)

        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        help_label = QLabel(
            "Manage terminal Run menu entries. Labels support '/' to define submenus. "
            "Entries are auto-sorted alphabetically by label path."
        )
        help_label.setWordWrap(True)
        root.addWidget(help_label)

        self.tabs = QTabWidget(host)
        self.quick_tree = QTreeWidget(host)
        self.template_tree = QTreeWidget(host)
        self.tabs.addTab(self._build_page("quick"), "Quick Commands")
        self.tabs.addTab(self._build_page("template"), "Templates")
        root.addWidget(self.tabs, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=host)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self._rebuild_tree("quick")
        self._rebuild_tree("template")

    def _build_page(self, mode: str) -> QWidget:
        page = QWidget(self)
        layout = QGridLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setColumnStretch(0, 1)

        tree = self.quick_tree if mode == "quick" else self.template_tree
        tree.setHeaderHidden(True)
        tree.setSelectionMode(QAbstractItemView.SingleSelection)
        tree.itemDoubleClicked.connect(lambda item, _col, m=mode: self._on_item_double_clicked(m, item))
        layout.addWidget(tree, 0, 0, 6, 1)

        add_btn = QPushButton("Add")
        edit_btn = QPushButton("Edit")
        dup_btn = QPushButton("Duplicate")
        remove_btn = QPushButton("Remove")
        expand_btn = QPushButton("Expand")
        collapse_btn = QPushButton("Collapse")

        add_btn.clicked.connect(lambda _checked=False, m=mode: self._add_entry(m))
        edit_btn.clicked.connect(lambda _checked=False, m=mode: self._edit_selected(m))
        dup_btn.clicked.connect(lambda _checked=False, m=mode: self._duplicate_selected(m))
        remove_btn.clicked.connect(lambda _checked=False, m=mode: self._remove_selected(m))
        expand_btn.clicked.connect(tree.expandAll)
        collapse_btn.clicked.connect(tree.collapseAll)

        buttons = [add_btn, edit_btn, dup_btn, remove_btn, expand_btn, collapse_btn]
        for idx, btn in enumerate(buttons):
            layout.addWidget(btn, idx, 1)
        layout.setRowStretch(6, 1)
        return page

    def _entries_for_mode(self, mode: str) -> list[dict[str, Any]]:
        return self._template_entries if mode == "template" else self._quick_entries

    def _tree_for_mode(self, mode: str) -> QTreeWidget:
        return self.template_tree if mode == "template" else self.quick_tree

    def _sort_entries(self, mode: str) -> None:
        self._entries_for_mode(mode).sort(key=_entry_sort_key)

    def _find_entry_index(self, mode: str, *, label_path: str) -> int:
        target = _normalize_label_path(label_path)
        entries = self._entries_for_mode(mode)
        for idx, entry in enumerate(entries):
            if _normalize_label_path(entry.get("label")) == target:
                return idx
        return -1

    def _has_label(self, mode: str, *, label_path: str, exclude_index: int = -1) -> bool:
        target = _normalize_label_path(label_path).lower()
        if not target:
            return False
        entries = self._entries_for_mode(mode)
        for idx, entry in enumerate(entries):
            if idx == exclude_index:
                continue
            if _normalize_label_path(entry.get("label")).lower() == target:
                return True
        return False

    def _selected_entry_path(self, mode: str) -> str:
        tree = self._tree_for_mode(mode)
        item = tree.currentItem()
        if item is None:
            return ""
        if str(item.data(0, _KIND_ROLE) or "") != _NODE_ENTRY:
            return ""
        return _normalize_label_path(item.data(0, _PATH_ROLE))

    def _selected_entry_index(self, mode: str) -> int:
        label_path = self._selected_entry_path(mode)
        if not label_path:
            return -1
        return self._find_entry_index(mode, label_path=label_path)

    def _rebuild_tree(self, mode: str, *, select_path: str | None = None) -> None:
        tree = self._tree_for_mode(mode)
        entries = self._entries_for_mode(mode)
        self._sort_entries(mode)

        selected = _normalize_label_path(select_path or "")
        tree.clear()

        group_items: dict[tuple[str, ...], QTreeWidgetItem] = {}
        for entry in entries:
            path = _normalize_label_path(entry.get("label"))
            parts = _split_label_path(path)
            if not parts:
                continue

            parent: QTreeWidget | QTreeWidgetItem = tree
            breadcrumb: list[str] = []
            for group in parts[:-1]:
                breadcrumb.append(group)
                key = tuple(breadcrumb)
                node = group_items.get(key)
                if node is None:
                    node = QTreeWidgetItem([group])
                    node.setData(0, _KIND_ROLE, _NODE_GROUP)
                    node.setData(0, _PATH_ROLE, "/".join(breadcrumb))
                    if isinstance(parent, QTreeWidget):
                        parent.addTopLevelItem(node)
                    else:
                        parent.addChild(node)
                    group_items[key] = node
                parent = node

            leaf = QTreeWidgetItem([parts[-1]])
            leaf.setData(0, _KIND_ROLE, _NODE_ENTRY)
            leaf.setData(0, _PATH_ROLE, path)
            if isinstance(parent, QTreeWidget):
                parent.addTopLevelItem(leaf)
            else:
                parent.addChild(leaf)

        tree.expandAll()
        if selected:
            self._select_entry_path(tree, selected)

    def _select_entry_path(self, tree: QTreeWidget, label_path: str) -> None:
        target = _normalize_label_path(label_path)
        if not target:
            return
        for idx in range(tree.topLevelItemCount()):
            root = tree.topLevelItem(idx)
            if root is None:
                continue
            match = self._find_item_by_path(root, target)
            if match is None:
                continue
            tree.setCurrentItem(match)
            return

    def _find_item_by_path(self, item: QTreeWidgetItem, target: str) -> QTreeWidgetItem | None:
        if str(item.data(0, _KIND_ROLE) or "") == _NODE_ENTRY:
            if _normalize_label_path(item.data(0, _PATH_ROLE)) == target:
                return item
        for idx in range(item.childCount()):
            child = item.child(idx)
            if child is None:
                continue
            found = self._find_item_by_path(child, target)
            if found is not None:
                return found
        return None

    def _on_item_double_clicked(self, mode: str, item: QTreeWidgetItem) -> None:
        if str(item.data(0, _KIND_ROLE) or "") != _NODE_ENTRY:
            return
        self._edit_selected(mode)

    def _add_entry(self, mode: str) -> None:
        dlg = _CommandEntryDialog(mode=mode, use_native_chrome=self.use_native_chrome, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        candidate = dlg.entry()
        label = _normalize_label_path(candidate.get("label"))
        if self._has_label(mode, label_path=label):
            self.status_label.setText(f"An entry with label '{label}' already exists.")
            return
        self._entries_for_mode(mode).append(candidate)
        self._rebuild_tree(mode, select_path=label)

    def _edit_selected(self, mode: str) -> None:
        idx = self._selected_entry_index(mode)
        if idx < 0:
            self.status_label.setText("Select a command entry to edit.")
            return
        entries = self._entries_for_mode(mode)
        original = entries[idx]
        dlg = _CommandEntryDialog(
            mode=mode,
            initial=original,
            use_native_chrome=self.use_native_chrome,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        candidate = dlg.entry()
        label = _normalize_label_path(candidate.get("label"))
        if self._has_label(mode, label_path=label, exclude_index=idx):
            self.status_label.setText(f"An entry with label '{label}' already exists.")
            return
        entries[idx] = candidate
        self._rebuild_tree(mode, select_path=label)

    def _duplicate_label(self, mode: str, source_label: str) -> str:
        label = _normalize_label_path(source_label)
        parts = _split_label_path(label)
        if not parts:
            base = "Command"
            group = ""
        elif len(parts) == 1:
            base = parts[0]
            group = ""
        else:
            base = parts[-1]
            group = "/".join(parts[:-1])

        candidate_leaf = f"{base} Copy"
        candidate = f"{group}/{candidate_leaf}" if group else candidate_leaf
        if not self._has_label(mode, label_path=candidate):
            return candidate

        index = 2
        while True:
            candidate_leaf = f"{base} Copy {index}"
            candidate = f"{group}/{candidate_leaf}" if group else candidate_leaf
            if not self._has_label(mode, label_path=candidate):
                return candidate
            index += 1

    def _duplicate_selected(self, mode: str) -> None:
        idx = self._selected_entry_index(mode)
        if idx < 0:
            self.status_label.setText("Select a command entry to duplicate.")
            return
        entries = self._entries_for_mode(mode)
        clone = deepcopy(entries[idx])
        clone["label"] = self._duplicate_label(mode, str(clone.get("label") or ""))
        entries.append(clone)
        self._rebuild_tree(mode, select_path=str(clone.get("label") or ""))

    def _remove_selected(self, mode: str) -> None:
        idx = self._selected_entry_index(mode)
        if idx < 0:
            self.status_label.setText("Select a command entry to remove.")
            return
        entries = self._entries_for_mode(mode)
        label = _normalize_label_path(entries[idx].get("label")) or "command"
        answer = QMessageBox.question(
            self,
            "Remove Command",
            f"Remove '{label}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        entries.pop(idx)
        self._rebuild_tree(mode)

    def quick_commands(self) -> list[dict[str, Any]]:
        return _normalize_entries(self._quick_entries, allow_params=False)

    def templates(self) -> list[dict[str, Any]]:
        return _normalize_entries(self._template_entries, allow_params=True)
