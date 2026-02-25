from __future__ import annotations

import concurrent.futures
import os
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.git.git_service import GitService, GitServiceError
from src.ui.custom_dialog import DialogWindow
from src.ui.icons.file_icon_provider import FileIconProvider


class GitCommitDialog(DialogWindow):
    """
    Commit dialog with tracked/untracked panes and hierarchical selection.

    Expected GitService support:
      - read_status(repo_root: str, include_untracked: bool = True)
          returns object with:
            - current_branch: str
            - file_states: dict[abs_path, state]
              where state includes at least "dirty" and (optionally) "untracked"
      - add_files(repo_root: str, rel_paths: list[str])   # optional
      - stage_paths(repo_root: str, rel_paths: list[str]) # optional
      - commit_files(repo_root: str, rel_paths: list[str], message: str)
      - push_current_branch(repo_root: str)
    """

    def __init__(
        self,
        *,
        git_service: GitService,
        repo_root: str,
        exclude_untracked_predicate: Callable[[str], bool] | None = None,
        prefer_push_action: bool = False,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("Commit Changes")
        self.resize(760, 620)

        self._git_service = git_service
        self._repo_root = str(repo_root)
        self._exclude_untracked_predicate = exclude_untracked_predicate

        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="pytpo-git-commit"
        )
        self._pending: dict[concurrent.futures.Future, str] = {}

        # rel_path -> "tracked" | "untracked"
        self._file_states: dict[str, str] = {}

        # rel_path -> checked bool
        self._checked_by_path: dict[str, bool] = {}

        self._commit_with_push = False
        self._prefer_push_action = bool(prefer_push_action)
        self._icon_provider = FileIconProvider()
        self._is_syncing_tree_checks = False

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(40)
        self._result_pump.timeout.connect(self._drain_pending)

        self.commit_output: str = ""
        self.push_output: str = ""
        self.push_error: str = ""

        self._build_ui()
        self.destroyed.connect(lambda *_args: self._shutdown())
        QTimer.singleShot(0, self._load_changes)

    def _build_ui(self) -> None:
        host = QWidget(self)
        self.set_content_widget(host)

        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.repo_label = QLabel(f"Repository: {self._repo_root}")
        self.repo_label.setWordWrap(True)
        root.addWidget(self.repo_label)

        self.note_label = QLabel(
            "Select files to commit. Checking a folder selects all visible files under it."
        )
        self.note_label.setWordWrap(True)
        root.addWidget(self.note_label)

        top_row = QHBoxLayout()
        self.branch_label = QLabel("Branch: ")
        top_row.addWidget(self.branch_label, 1)

        self.refresh_btn = QPushButton("Refresh")
        top_row.addWidget(self.refresh_btn)
        root.addLayout(top_row)

        trees_row = QHBoxLayout()
        trees_row.setSpacing(10)

        tracked_host = QVBoxLayout()
        self.tracked_label = QLabel("Tracked Changes")
        tracked_host.addWidget(self.tracked_label)
        tracked_actions = QHBoxLayout()
        self.tracked_select_all_btn = QPushButton("All")
        self.tracked_select_none_btn = QPushButton("None")
        tracked_actions.addWidget(self.tracked_select_all_btn)
        tracked_actions.addWidget(self.tracked_select_none_btn)
        tracked_actions.addStretch(1)
        tracked_host.addLayout(tracked_actions)
        self.tracked_tree = QTreeWidget()
        self.tracked_tree.setColumnCount(1)
        self.tracked_tree.setHeaderLabel("Tracked")
        tracked_host.addWidget(self.tracked_tree, 1)
        trees_row.addLayout(tracked_host, 1)

        untracked_host = QVBoxLayout()
        self.untracked_label = QLabel("Untracked Files")
        untracked_host.addWidget(self.untracked_label)
        untracked_actions = QHBoxLayout()
        self.untracked_select_all_btn = QPushButton("All")
        self.untracked_select_none_btn = QPushButton("None")
        untracked_actions.addWidget(self.untracked_select_all_btn)
        untracked_actions.addWidget(self.untracked_select_none_btn)
        untracked_actions.addStretch(1)
        untracked_host.addLayout(untracked_actions)
        self.untracked_tree = QTreeWidget()
        self.untracked_tree.setColumnCount(1)
        self.untracked_tree.setHeaderLabel("Untracked")
        untracked_host.addWidget(self.untracked_tree, 1)
        trees_row.addLayout(untracked_host, 1)

        root.addLayout(trees_row, 1)

        selection_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_none_btn = QPushButton("Select None")
        selection_row.addWidget(self.select_all_btn)
        selection_row.addWidget(self.select_none_btn)
        selection_row.addStretch(1)
        root.addLayout(selection_row)

        self.message_edit = QPlainTextEdit()
        self.message_edit.setPlaceholderText("Commit message")
        self.message_edit.setFixedHeight(120)
        root.addWidget(self.message_edit)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.commit_btn = QPushButton("Commit")
        self.commit_push_btn = QPushButton("Commit and Push")
        if self._prefer_push_action:
            self.commit_push_btn.setDefault(True)
        else:
            self.commit_btn.setDefault(True)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.commit_btn)
        actions.addWidget(self.commit_push_btn)
        root.addLayout(actions)

        # signals
        self.refresh_btn.clicked.connect(self._load_changes)

        self.select_all_btn.clicked.connect(self._select_all)
        self.select_none_btn.clicked.connect(self._select_none)
        self.tracked_select_all_btn.clicked.connect(lambda: self._select_kind("tracked", True))
        self.tracked_select_none_btn.clicked.connect(lambda: self._select_kind("tracked", False))
        self.untracked_select_all_btn.clicked.connect(lambda: self._select_kind("untracked", True))
        self.untracked_select_none_btn.clicked.connect(lambda: self._select_kind("untracked", False))
        self.cancel_btn.clicked.connect(self.reject)

        self.commit_btn.clicked.connect(lambda: self._commit_clicked(push_after=False))
        self.commit_push_btn.clicked.connect(lambda: self._commit_clicked(push_after=True))

        self.message_edit.textChanged.connect(self._refresh_commit_enabled)
        self.tracked_tree.itemChanged.connect(self._on_item_changed)
        self.untracked_tree.itemChanged.connect(self._on_item_changed)

    def _load_changes(self) -> None:
        self._set_busy(True)
        self._set_status("Loading files...")

        def _run():
            # Backward-compatible call path:
            try:
                return self._git_service.read_status(self._repo_root, include_untracked=True)
            except TypeError:
                # Old GitService signature: read_status(repo_root)
                return self._git_service.read_status(self._repo_root)

        self._submit_task("load", _run)

    def _select_all(self) -> None:
        self._is_syncing_tree_checks = True
        self.tracked_tree.blockSignals(True)
        self.untracked_tree.blockSignals(True)
        for rel_path in self._file_states.keys():
            self._checked_by_path[rel_path] = True
        self._sync_file_item_checks()
        self.tracked_tree.blockSignals(False)
        self.untracked_tree.blockSignals(False)
        self._is_syncing_tree_checks = False
        self._refresh_commit_enabled()

    def _select_none(self) -> None:
        self._is_syncing_tree_checks = True
        self.tracked_tree.blockSignals(True)
        self.untracked_tree.blockSignals(True)
        for rel_path in self._file_states.keys():
            self._checked_by_path[rel_path] = False
        self._sync_file_item_checks()
        self.tracked_tree.blockSignals(False)
        self.untracked_tree.blockSignals(False)
        self._is_syncing_tree_checks = False
        self._refresh_commit_enabled()

    def _select_kind(self, kind: str, checked: bool) -> None:
        self._is_syncing_tree_checks = True
        self.tracked_tree.blockSignals(True)
        self.untracked_tree.blockSignals(True)
        for rel_path, path_kind in self._file_states.items():
            if path_kind != kind:
                continue
            self._checked_by_path[rel_path] = bool(checked)
        self._sync_file_item_checks()
        self.tracked_tree.blockSignals(False)
        self.untracked_tree.blockSignals(False)
        self._is_syncing_tree_checks = False
        self._refresh_commit_enabled()

    def _commit_clicked(self, *, push_after: bool) -> None:
        message = str(self.message_edit.toPlainText() or "").strip()
        if not message:
            self._set_status("Commit message is required.", error=True)
            return

        selected = self._selected_rel_paths()
        if not selected:
            self._set_status("Select at least one file.", error=True)
            return

        to_add = [p for p in selected if self._file_states.get(p) == "untracked"]

        self._set_busy(True)
        self._commit_with_push = bool(push_after)
        self._set_status("Committing and pushing..." if push_after else "Committing changes...")

        def _run():
            # Add selected untracked files first so they become tracked in this commit.
            if to_add:
                add_fn = getattr(self._git_service, "add_files", None)
                if callable(add_fn):
                    add_fn(self._repo_root, to_add)
                else:
                    stage_fn = getattr(self._git_service, "stage_paths", None)
                    if callable(stage_fn):
                        stage_fn(self._repo_root, to_add)

            commit_output = self._git_service.commit_files(self._repo_root, selected, message)

            push_output = ""
            push_error = ""
            if push_after:
                try:
                    push_output = self._git_service.push_current_branch(self._repo_root)
                except GitServiceError as exc:
                    push_error = str(exc)

            return {
                "commit_output": str(commit_output or "").strip(),
                "push_output": str(push_output or "").strip(),
                "push_error": str(push_error or "").strip(),
            }

        self._submit_task("commit", _run)

    def _submit_task(self, kind: str, fn: Callable[[], Any]) -> None:
        try:
            future = self._executor.submit(fn)
        except Exception:
            self._set_busy(False)
            self._set_status("Failed to start git operation.", error=True)
            return

        self._pending[future] = kind
        if not self._result_pump.isActive():
            self._result_pump.start()

    def _drain_pending(self) -> None:
        if not self._pending:
            self._result_pump.stop()
            return

        done: list[concurrent.futures.Future] = []
        for future, kind in list(self._pending.items()):
            if not future.done():
                continue

            done.append(future)
            try:
                result = future.result()
                error = None
            except Exception as exc:
                result = None
                error = exc

            self._handle_result(kind, result, error)

        for future in done:
            self._pending.pop(future, None)

        if not self._pending:
            self._result_pump.stop()
            self._set_busy(False)

    def _handle_result(self, kind: str, result: Any, error: Exception | None) -> None:
        if kind == "load":
            if error is None and hasattr(result, "file_states"):
                branch = str(getattr(result, "current_branch", "") or "")
                self.branch_label.setText(f"Branch: {branch or '(detached)'}")

                self._file_states = self._extract_file_states(result)
                self._populate_files_tree()
                tracked_count = sum(1 for v in self._file_states.values() if v == "tracked")
                untracked_count = sum(1 for v in self._file_states.values() if v == "untracked")
                self.tracked_label.setText(f"Tracked Changes ({tracked_count})")
                self.untracked_label.setText(f"Untracked Files ({untracked_count})")

                if not self._file_states:
                    self._set_status("No files to commit.")
                else:
                    if untracked_count:
                        self._set_status(
                            f"Loaded {tracked_count} tracked + {untracked_count} untracked file(s)."
                        )
                    else:
                        self._set_status(f"Loaded {tracked_count} tracked changed file(s).")
                return

            if isinstance(error, GitServiceError):
                self._set_status(str(error), error=True)
                return

            self._set_status("Failed to load files.", error=True)
            return

        if kind == "commit":
            if error is None:
                payload = result if isinstance(result, dict) else {}
                self.commit_output = str(payload.get("commit_output") or "").strip()
                self.push_output = str(payload.get("push_output") or "").strip()
                self.push_error = str(payload.get("push_error") or "").strip()

                if self.push_error:
                    self._set_status("Commit succeeded locally, but push authentication failed.", error=True)
                    QMessageBox.warning(
                        self,
                        "Commit and Push",
                        f"Commit succeeded locally, but push authentication failed.\n\n{self.push_error}",
                    )
                else:
                    self._set_status("Commit and push completed." if self._commit_with_push else "Commit completed.")

                self.accept()
                return

            if isinstance(error, GitServiceError):
                self._set_status(str(error), error=True)
                return

            self._set_status("Commit failed.", error=True)

    def _extract_file_states(self, status: Any) -> dict[str, str]:
        """
        Convert service status.file_states into:
          rel_path -> "tracked" | "untracked"

        Accepted incoming state values:
          - "dirty"      => tracked
          - "untracked"  => untracked
        """
        states: dict[str, str] = {}
        raw = getattr(status, "file_states", {})
        if not isinstance(raw, dict):
            return states

        for abs_path, state in raw.items():
            cpath = str(abs_path or "").strip()
            if not cpath:
                continue

            state_text = str(state or "").strip().lower()
            if state_text not in {"dirty", "untracked"}:
                continue
            if state_text == "untracked" and callable(self._exclude_untracked_predicate):
                try:
                    if bool(self._exclude_untracked_predicate(cpath)):
                        continue
                except Exception:
                    pass

            try:
                rel = os.path.relpath(cpath, self._repo_root).replace("\\", "/")
            except Exception:
                continue

            if rel in (".", "") or rel.startswith("../"):
                continue

            # Keep noise out regardless of service behavior
            if "/__pycache__/" in f"/{rel}/" or rel.endswith(".pyc") or rel.endswith(".pyo"):
                continue

            states[rel] = "tracked" if state_text == "dirty" else "untracked"

        return dict(sorted(states.items(), key=lambda item: item[0].lower()))

    def _populate_files_tree(self) -> None:
        # preserve checked state when refreshing
        next_checked: dict[str, bool] = {}
        for rel_path in self._file_states.keys():
            existing = self._checked_by_path.get(rel_path)
            if existing is None:
                kind = self._file_states.get(rel_path, "tracked")
                existing = (kind == "tracked")  # tracked checked, untracked unchecked
            next_checked[rel_path] = bool(existing)
        self._checked_by_path = next_checked

        self._is_syncing_tree_checks = True
        self.tracked_tree.blockSignals(True)
        self.untracked_tree.blockSignals(True)
        self.tracked_tree.clear()
        self.untracked_tree.clear()

        style = QApplication.style()
        folder_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon) if style is not None else None
        fallback_file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon) if style is not None else None

        self._populate_tree_for_kind(
            tree=self.tracked_tree,
            kind="tracked",
            folder_icon=folder_icon,
            fallback_file_icon=fallback_file_icon,
        )
        self._populate_tree_for_kind(
            tree=self.untracked_tree,
            kind="untracked",
            folder_icon=folder_icon,
            fallback_file_icon=fallback_file_icon,
        )

        self._refresh_directory_states(self.tracked_tree)
        self._refresh_directory_states(self.untracked_tree)

        self.tracked_tree.expandAll()
        self.untracked_tree.expandAll()
        self.tracked_tree.blockSignals(False)
        self.untracked_tree.blockSignals(False)
        self._is_syncing_tree_checks = False
        self._refresh_commit_enabled()

    def _populate_tree_for_kind(
        self,
        *,
        tree: QTreeWidget,
        kind: str,
        folder_icon,
        fallback_file_icon,
    ) -> None:
        dir_nodes: dict[str, QTreeWidgetItem] = {}

        for rel_path, entry_kind in self._file_states.items():
            if entry_kind != kind:
                continue
            parent = tree.invisibleRootItem()
            parts = [part for part in rel_path.split("/") if part]
            if not parts:
                continue

            current = ""
            for part in parts[:-1]:
                current = part if not current else f"{current}/{part}"
                node = dir_nodes.get(current)
                if node is None:
                    node = QTreeWidgetItem([part])
                    node.setData(0, Qt.UserRole, "")
                    node.setFlags(node.flags() | Qt.ItemIsUserCheckable)
                    if folder_icon is not None:
                        node.setIcon(0, folder_icon)
                    node.setCheckState(0, Qt.Unchecked)
                    parent.addChild(node)
                    dir_nodes[current] = node
                parent = node

            name = parts[-1]
            file_item = QTreeWidgetItem([name])
            file_item.setData(0, Qt.UserRole, rel_path)
            file_item.setToolTip(0, f"{rel_path}\n[{entry_kind}]")
            file_item.setFlags(file_item.flags() | Qt.ItemIsUserCheckable)
            file_item.setCheckState(0, Qt.Checked if self._checked_by_path.get(rel_path, False) else Qt.Unchecked)

            icon = self._icon_provider.icon_for_file_name(name)
            if icon is not None:
                file_item.setIcon(0, icon)
            elif fallback_file_icon is not None:
                file_item.setIcon(0, fallback_file_icon)

            if entry_kind == "untracked":
                file_item.setForeground(0, QBrush(QColor("#8a8f98")))

            parent.addChild(file_item)

    def _sync_file_item_checks(self) -> None:
        self._sync_tree_file_item_checks(self.tracked_tree)
        self._sync_tree_file_item_checks(self.untracked_tree)
        self._refresh_directory_states(self.tracked_tree)
        self._refresh_directory_states(self.untracked_tree)

    def _sync_tree_file_item_checks(self, tree: QTreeWidget) -> None:
        stack: list[QTreeWidgetItem] = []
        root = tree.invisibleRootItem()
        for idx in range(root.childCount()):
            stack.append(root.child(idx))

        while stack:
            item = stack.pop()
            rel_path = str(item.data(0, Qt.UserRole) or "").strip()
            if rel_path:
                checked = self._checked_by_path.get(rel_path, False)
                item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
            for idx in range(item.childCount()):
                stack.append(item.child(idx))

    def _selected_rel_paths(self) -> list[str]:
        selected = [p for p, checked in self._checked_by_path.items() if checked and p in self._file_states]
        return sorted(selected, key=str.lower)

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._is_syncing_tree_checks:
            return

        rel_path = str(item.data(0, Qt.UserRole) or "").strip()
        if rel_path:
            self._checked_by_path[rel_path] = item.checkState(0) == Qt.Checked
            self._is_syncing_tree_checks = True
            try:
                self._update_ancestor_states(item.parent())
            finally:
                self._is_syncing_tree_checks = False
        else:
            checked = item.checkState(0) == Qt.Checked
            self._is_syncing_tree_checks = True
            try:
                self._set_descendant_file_checks(item, checked=checked)
                self._update_ancestor_states(item.parent())
            finally:
                self._is_syncing_tree_checks = False
        self._refresh_commit_enabled()

    def _set_descendant_file_checks(self, item: QTreeWidgetItem, *, checked: bool) -> None:
        for idx in range(item.childCount()):
            child = item.child(idx)
            rel_path = str(child.data(0, Qt.UserRole) or "").strip()
            if rel_path:
                self._checked_by_path[rel_path] = checked
                child.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
                continue
            child.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
            self._set_descendant_file_checks(child, checked=checked)

    def _update_ancestor_states(self, item: QTreeWidgetItem | None) -> None:
        current = item
        while current is not None:
            total = 0
            checked_count = 0
            stack = [current]
            while stack:
                node = stack.pop()
                for idx in range(node.childCount()):
                    child = node.child(idx)
                    rel_path = str(child.data(0, Qt.UserRole) or "").strip()
                    if rel_path:
                        total += 1
                        if self._checked_by_path.get(rel_path, False):
                            checked_count += 1
                    else:
                        stack.append(child)
            if total == 0:
                current.setCheckState(0, Qt.Unchecked)
            elif checked_count == 0:
                current.setCheckState(0, Qt.Unchecked)
            elif checked_count == total:
                current.setCheckState(0, Qt.Checked)
            else:
                current.setCheckState(0, Qt.PartiallyChecked)
            current = current.parent()

    def _refresh_directory_states(self, tree: QTreeWidget) -> None:
        root = tree.invisibleRootItem()
        for idx in range(root.childCount()):
            self._refresh_directory_states_recursive(root.child(idx))

    def _refresh_directory_states_recursive(self, item: QTreeWidgetItem) -> tuple[int, int]:
        rel_path = str(item.data(0, Qt.UserRole) or "").strip()
        if rel_path:
            checked = 1 if self._checked_by_path.get(rel_path, False) else 0
            return (checked, 1)

        checked_total = 0
        item_total = 0
        for idx in range(item.childCount()):
            child_checked, child_total = self._refresh_directory_states_recursive(item.child(idx))
            checked_total += child_checked
            item_total += child_total

        if item_total == 0 or checked_total == 0:
            item.setCheckState(0, Qt.Unchecked)
        elif checked_total == item_total:
            item.setCheckState(0, Qt.Checked)
        else:
            item.setCheckState(0, Qt.PartiallyChecked)
        return (checked_total, item_total)

    def _refresh_commit_enabled(self) -> None:
        if self._pending:
            self.commit_btn.setEnabled(False)
            self.commit_push_btn.setEnabled(False)
            return

        enabled = bool(str(self.message_edit.toPlainText() or "").strip() and self._selected_rel_paths())
        self.commit_btn.setEnabled(enabled)
        self.commit_push_btn.setEnabled(enabled)

    def _set_busy(self, busy: bool) -> None:
        disabled = bool(busy)
        self.refresh_btn.setDisabled(disabled)
        self.tracked_tree.setDisabled(disabled)
        self.untracked_tree.setDisabled(disabled)
        self.select_all_btn.setDisabled(disabled)
        self.select_none_btn.setDisabled(disabled)
        self.tracked_select_all_btn.setDisabled(disabled)
        self.tracked_select_none_btn.setDisabled(disabled)
        self.untracked_select_all_btn.setDisabled(disabled)
        self.untracked_select_none_btn.setDisabled(disabled)
        self.message_edit.setDisabled(disabled)
        self.cancel_btn.setDisabled(disabled)

        if disabled:
            self.commit_btn.setDisabled(True)
            self.commit_push_btn.setDisabled(True)
        else:
            self._refresh_commit_enabled()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = "#d46a6a" if error else "#a4bf7a"
        self.status_label.setText(f"<span style='color:{color};'>{text}</span>")

    def _shutdown(self) -> None:
        self._result_pump.stop()

        for future in list(self._pending.keys()):
            try:
                future.cancel()
            except Exception:
                pass
        self._pending.clear()

        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass

    def reject(self) -> None:
        if self._pending:
            answer = QMessageBox.question(
                self,
                "Cancel",
                "A git operation is running. Close anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        super().reject()
