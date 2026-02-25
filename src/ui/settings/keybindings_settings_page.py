"""Settings page for editing IDE keybindings."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.keybindings import (
    find_conflicts_for_sequence,
    get_action_sequence,
    keybinding_actions_for_scope,
    normalize_keybindings,
    normalize_sequence,
    reset_action_to_default,
    reset_scope_to_defaults,
    sequence_to_text,
    set_action_sequence,
)
from src.settings_models import SettingsScope
from src.ui.keybinding_capture_dialog import KeybindingCaptureDialog


class KeybindingsSettingsPage(QWidget):
    def __init__(self, *, manager: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._base_keybindings = normalize_keybindings(
            self._manager.get("keybindings", scope_preference="ide", default={})
        )
        self._working_keybindings = deepcopy(self._base_keybindings)

        self._build_ui()
        self._populate_categories()
        self._refresh_table()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)

        self.category_list = QListWidget(self)
        self.category_list.setFixedWidth(160)
        self.category_list.currentItemChanged.connect(lambda _curr, _prev: self._refresh_table())
        body.addWidget(self.category_list)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search actions...")
        self.search.textChanged.connect(lambda _text: self._refresh_table())
        top.addWidget(self.search, 1)

        self.reset_category_btn = QPushButton("Reset Category to Defaults", self)
        self.reset_category_btn.clicked.connect(self._on_reset_category)
        top.addWidget(self.reset_category_btn)

        self.reset_all_btn = QPushButton("Reset All", self)
        self.reset_all_btn.clicked.connect(self._on_reset_all)
        top.addWidget(self.reset_all_btn)

        right.addLayout(top)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Action Name", "Current Binding", "Edit", "Reset"])
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        right.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        right.addWidget(self.status)

        body.addLayout(right, 1)
        root.addLayout(body, 1)

    def _populate_categories(self) -> None:
        self.category_list.clear()
        for scope, label in (("general", "General"), ("python", "Python"), ("cpp", "C/C++")):
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, scope)
            self.category_list.addItem(item)
        if self.category_list.count() > 0:
            self.category_list.setCurrentRow(0)

    def _current_scope(self) -> str:
        item = self.category_list.currentItem()
        if item is None:
            return "general"
        scope = str(item.data(Qt.UserRole) or "").strip().lower()
        return scope or "general"

    def _refresh_table(self) -> None:
        # Fully clear old cell widgets before rebuilding rows. Qt can otherwise
        # leave stale widgets painted in the viewport after repeated repopulates.
        for row in range(self.table.rowCount()):
            for col in range(self.table.columnCount()):
                widget = self.table.cellWidget(row, col)
                if widget is None:
                    continue
                self.table.removeCellWidget(row, col)
                widget.setParent(None)
                widget.deleteLater()
        self.table.clearContents()
        self.table.setRowCount(0)

        scope = self._current_scope()
        query = str(self.search.text() or "").strip().lower()
        entries = keybinding_actions_for_scope(scope)
        if query:
            entries = [entry for entry in entries if query in entry.action_name.lower() or query in entry.action_id.lower()]

        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            action_cell = QTableWidgetItem(entry.action_name)
            action_cell.setData(Qt.UserRole, entry.action_id)
            self.table.setItem(row, 0, action_cell)

            sequence = get_action_sequence(self._working_keybindings, scope=scope, action_id=entry.action_id)
            binding_cell = QTableWidgetItem(sequence_to_text(sequence))
            binding_cell.setData(Qt.UserRole, sequence)
            self.table.setItem(row, 1, binding_cell)

            edit_btn = QPushButton("Edit")
            edit_btn.clicked.connect(
                lambda _checked=False, item_scope=scope, item_action=entry.action_id, item_name=entry.action_name: self._edit_action_binding(
                    scope=item_scope,
                    action_id=item_action,
                    action_name=item_name,
                )
            )
            self.table.setCellWidget(row, 2, edit_btn)

            reset_btn = QPushButton("Reset")
            reset_btn.clicked.connect(
                lambda _checked=False, item_scope=scope, item_action=entry.action_id: self._reset_action_binding(
                    scope=item_scope,
                    action_id=item_action,
                )
            )
            self.table.setCellWidget(row, 3, reset_btn)

        self._refresh_status()

    def _refresh_status(self) -> None:
        if self.has_pending_settings_changes():
            self.status.setText("Unsaved keybinding changes.")
        else:
            self.status.setText("No keybinding changes.")

    def _edit_action_binding(self, *, scope: str, action_id: str, action_name: str) -> None:
        current = get_action_sequence(self._working_keybindings, scope=scope, action_id=action_id)
        dialog = KeybindingCaptureDialog(
            action_name=action_name,
            initial_sequence=current,
            parent=self,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return

        candidate = normalize_sequence(dialog.sequence())
        if not candidate:
            return

        conflicts = find_conflicts_for_sequence(
            self._working_keybindings,
            scope=scope,
            action_id=action_id,
            sequence=candidate,
        )
        if conflicts:
            lines = [f"- {entry.action_name} ({entry.scope})" for entry in conflicts]
            answer = QMessageBox.question(
                self,
                "Shortcut conflict",
                "This shortcut conflicts with existing bindings:\n\n"
                + "\n".join(lines[:8])
                + "\n\nOverride anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        self._working_keybindings = set_action_sequence(
            self._working_keybindings,
            scope=scope,
            action_id=action_id,
            sequence=candidate,
        )
        self._refresh_table()
        self._notify_pending_changed()

    def _reset_action_binding(self, *, scope: str, action_id: str) -> None:
        self._working_keybindings = reset_action_to_default(
            self._working_keybindings,
            scope=scope,
            action_id=action_id,
        )
        self._refresh_table()
        self._notify_pending_changed()

    def _on_reset_category(self) -> None:
        scope = self._current_scope()
        label = scope.capitalize()
        answer = QMessageBox.question(
            self,
            "Reset Category",
            f"Reset all {label} keybindings to defaults?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._working_keybindings = reset_scope_to_defaults(self._working_keybindings, scope=scope)
        self._refresh_table()
        self._notify_pending_changed()

    def _on_reset_all(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset All",
            "Reset all keybindings to defaults?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._working_keybindings = normalize_keybindings({})
        self._refresh_table()
        self._notify_pending_changed()

    def has_pending_settings_changes(self) -> bool:
        return normalize_keybindings(self._working_keybindings) != normalize_keybindings(self._base_keybindings)

    def apply_settings_changes(self) -> list[str]:
        try:
            normalized = normalize_keybindings(self._working_keybindings)
            self._manager.set("keybindings", normalized, "ide")
            self._base_keybindings = deepcopy(normalized)
            self._working_keybindings = deepcopy(normalized)
            self._refresh_status()
            self._notify_pending_changed()
            return []
        except Exception as exc:
            return [str(exc)]

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


def create_keybindings_settings_page(
    *,
    manager: Any,
    scope: SettingsScope,
    binding_cls: Callable[..., Any],
    parent: QWidget | None = None,
) -> tuple[QWidget, list[Any]]:
    _ = scope
    _ = binding_cls
    page = KeybindingsSettingsPage(manager=manager, parent=None)
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(page)
    return scroll, []


__all__ = ["KeybindingsSettingsPage", "create_keybindings_settings_page"]
