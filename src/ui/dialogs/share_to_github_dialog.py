from __future__ import annotations

import concurrent.futures
import os
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.git.github_share_service import (
    GitHubShareError,
    GitHubShareRequest,
    GitHubShareResult,
    GitHubShareService,
)
from src.ui.custom_dialog import DialogWindow
from src.ui.icons.file_icon_provider import FileIconProvider


class ShareToGitHubDialog(DialogWindow):
    def __init__(
        self,
        *,
        project_root: str,
        token: str,
        share_service: GitHubShareService,
        exclude_dirs: list[str] | None = None,
        exclude_files: list[str] | None = None,
        exclude_path_predicate: Callable[[str], bool] | None = None,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("Share to GitHub")
        self.resize(820, 680)

        self._project_root = str(project_root or "").strip()
        self._token = str(token or "").strip()
        self._share_service = share_service
        self._exclude_dirs = [str(item) for item in (exclude_dirs or []) if str(item).strip()]
        self._exclude_files = [str(item) for item in (exclude_files or []) if str(item).strip()]
        self._exclude_path_predicate = exclude_path_predicate
        self._icon_provider = FileIconProvider()

        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pytpo-github-share")
        self._pending: dict[concurrent.futures.Future, str] = {}
        self._files: list[str] = []
        self._checked_by_path: dict[str, bool] = {}
        self._is_syncing_tree_checks = False

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(40)
        self._result_pump.timeout.connect(self._drain_pending)

        self.result_data: GitHubShareResult | None = None

        self._build_ui()
        self.destroyed.connect(lambda *_args: self._shutdown())
        QTimer.singleShot(0, self._load_files)

    def _build_ui(self) -> None:
        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.repo_root_label = QLabel(f"Project: {self._project_root}")
        self.repo_root_label.setWordWrap(True)
        root.addWidget(self.repo_root_label)

        self.note_label = QLabel(
            "Select files for the initial commit. Checking a folder selects all visible files under it."
        )
        self.note_label.setWordWrap(True)
        root.addWidget(self.note_label)

        row_repo = QHBoxLayout()
        row_repo.addWidget(QLabel("Repository Name"), 0)
        self.repo_name_edit = QLineEdit()
        self.repo_name_edit.setPlaceholderText("repository-name")
        self.repo_name_edit.setText(os.path.basename(self._project_root.rstrip("/")) or "my-project")
        row_repo.addWidget(self.repo_name_edit, 1)
        root.addLayout(row_repo)

        self.description_edit = QPlainTextEdit()
        self.description_edit.setPlaceholderText("Description (optional)")
        self.description_edit.setFixedHeight(82)
        root.addWidget(QLabel("Description"))
        root.addWidget(self.description_edit)

        row_visibility = QHBoxLayout()
        row_visibility.addWidget(QLabel("Visibility"), 0)
        self.visibility_combo = QComboBox()
        self.visibility_combo.addItem("Private", "private")
        self.visibility_combo.addItem("Public", "public")
        row_visibility.addWidget(self.visibility_combo, 1)
        root.addLayout(row_visibility)

        row_commit = QHBoxLayout()
        row_commit.addWidget(QLabel("Commit Message"), 0)
        self.commit_message_edit = QLineEdit("Initial commit")
        row_commit.addWidget(self.commit_message_edit, 1)
        root.addLayout(row_commit)

        tree_header = QHBoxLayout()
        tree_header.addWidget(QLabel("Initial Commit Files"), 1)
        self.refresh_btn = QPushButton("Refresh")
        tree_header.addWidget(self.refresh_btn)
        root.addLayout(tree_header)

        self.files_tree = QTreeWidget()
        self.files_tree.setColumnCount(1)
        self.files_tree.setHeaderLabel("Project Files")
        root.addWidget(self.files_tree, 1)

        selection_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_none_btn = QPushButton("Select None")
        selection_row.addWidget(self.select_all_btn)
        selection_row.addWidget(self.select_none_btn)
        selection_row.addStretch(1)
        root.addLayout(selection_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.share_btn = QPushButton("Share to GitHub")
        self.share_btn.setDefault(True)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.share_btn)
        root.addLayout(actions)

        self.refresh_btn.clicked.connect(self._load_files)
        self.select_all_btn.clicked.connect(self._select_all)
        self.select_none_btn.clicked.connect(self._select_none)
        self.files_tree.itemChanged.connect(self._on_item_changed)
        self.repo_name_edit.textChanged.connect(self._refresh_share_enabled)
        self.commit_message_edit.textChanged.connect(self._refresh_share_enabled)
        self.cancel_btn.clicked.connect(self.reject)
        self.share_btn.clicked.connect(lambda: self._start_share(replace_origin=False))

    def _load_files(self) -> None:
        self._set_busy(True)
        self._set_status("Scanning project files...")

        def _run():
            return self._share_service.list_project_files(
                self._project_root,
                exclude_dirs=self._exclude_dirs,
                exclude_files=self._exclude_files,
            )

        self._submit_task("load_files", _run)

    def _start_share(self, *, replace_origin: bool) -> None:
        repo_name = str(self.repo_name_edit.text() or "").strip()
        commit_message = str(self.commit_message_edit.text() or "").strip()
        selected = self._selected_rel_paths()
        if not repo_name:
            self._set_status("Repository name is required.", error=True)
            return
        if not commit_message:
            self._set_status("Commit message is required.", error=True)
            return
        if not selected:
            self._set_status("Select at least one file.", error=True)
            return

        self._set_busy(True)
        self._set_status("Sharing project to GitHub...")

        request = GitHubShareRequest(
            project_root=self._project_root,
            token=self._token,
            repo_name=repo_name,
            description=str(self.description_edit.toPlainText() or "").strip(),
            private=str(self.visibility_combo.currentData() or "private") == "private",
            commit_message=commit_message,
            selected_files=selected,
            replace_existing_origin=bool(replace_origin),
        )

        def _run():
            return self._share_service.share_to_github(request)

        self._submit_task("share", _run)

    def _submit_task(self, kind: str, fn: Callable[[], Any]) -> None:
        try:
            future = self._executor.submit(fn)
        except Exception:
            self._set_busy(False)
            self._set_status("Could not start background operation.", error=True)
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
        if kind == "load_files":
            if error is None and isinstance(result, list):
                raw_files = [str(item) for item in result if str(item).strip()]
                self._files = self._apply_workspace_filters(raw_files)
                self._checked_by_path = {path: bool(self._checked_by_path.get(path, True)) for path in self._files}
                self._populate_files_tree()
                if not self._files:
                    self._set_status("No files found to share.", error=True)
                else:
                    filtered_out = max(0, len(raw_files) - len(self._files))
                    if filtered_out:
                        self._set_status(
                            f"Loaded {len(self._files)} file(s) after filtering {filtered_out} hidden item(s)."
                        )
                    else:
                        self._set_status(f"Loaded {len(self._files)} file(s).")
                return
            self._set_status("Failed to load project files.", error=True)
            return

        if kind == "share":
            if error is None and isinstance(result, GitHubShareResult):
                self.result_data = result
                self._set_status("Shared to GitHub successfully.")
                self.accept()
                return

            if isinstance(error, GitHubShareError):
                if str(error.kind or "") == "origin_exists":
                    answer = QMessageBox.question(
                        self,
                        "Replace Remote Origin",
                        "Remote 'origin' already exists with a different URL.\n\nReplace it and continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if answer == QMessageBox.Yes:
                        self._start_share(replace_origin=True)
                        return
                self._set_status(str(error), error=True)
                return

            self._set_status("Share to GitHub failed.", error=True)

    def _populate_files_tree(self) -> None:
        self._is_syncing_tree_checks = True
        self.files_tree.blockSignals(True)
        self.files_tree.clear()
        style = QApplication.style()
        folder_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon) if style is not None else None
        fallback_file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon) if style is not None else None

        dir_nodes: dict[str, QTreeWidgetItem] = {}
        for rel_path in self._files:
            parent = self.files_tree.invisibleRootItem()
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
            item = QTreeWidgetItem([name])
            item.setData(0, Qt.UserRole, rel_path)
            item.setToolTip(0, rel_path)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if self._checked_by_path.get(rel_path, False) else Qt.Unchecked)
            icon = self._icon_provider.icon_for_file_name(name)
            if icon is not None:
                item.setIcon(0, icon)
            elif fallback_file_icon is not None:
                item.setIcon(0, fallback_file_icon)
            parent.addChild(item)

        self._refresh_directory_states()
        self.files_tree.expandAll()
        self.files_tree.blockSignals(False)
        self._is_syncing_tree_checks = False
        self._refresh_share_enabled()

    def _select_all(self) -> None:
        self._is_syncing_tree_checks = True
        self.files_tree.blockSignals(True)
        for path in self._files:
            self._checked_by_path[path] = True
        self._sync_item_checks()
        self.files_tree.blockSignals(False)
        self._is_syncing_tree_checks = False
        self._refresh_share_enabled()

    def _select_none(self) -> None:
        self._is_syncing_tree_checks = True
        self.files_tree.blockSignals(True)
        for path in self._files:
            self._checked_by_path[path] = False
        self._sync_item_checks()
        self.files_tree.blockSignals(False)
        self._is_syncing_tree_checks = False
        self._refresh_share_enabled()

    def _sync_item_checks(self) -> None:
        stack: list[QTreeWidgetItem] = []
        root = self.files_tree.invisibleRootItem()
        for idx in range(root.childCount()):
            stack.append(root.child(idx))
        while stack:
            item = stack.pop()
            rel_path = str(item.data(0, Qt.UserRole) or "").strip()
            if rel_path:
                item.setCheckState(0, Qt.Checked if self._checked_by_path.get(rel_path, False) else Qt.Unchecked)
            for idx in range(item.childCount()):
                stack.append(item.child(idx))
        self._refresh_directory_states()

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
        self._refresh_share_enabled()

    def _selected_rel_paths(self) -> list[str]:
        selected = [path for path, checked in self._checked_by_path.items() if checked and path in self._files]
        selected.sort(key=str.lower)
        return selected

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
            if total == 0 or checked_count == 0:
                current.setCheckState(0, Qt.Unchecked)
            elif checked_count == total:
                current.setCheckState(0, Qt.Checked)
            else:
                current.setCheckState(0, Qt.PartiallyChecked)
            current = current.parent()

    def _refresh_directory_states(self) -> None:
        root = self.files_tree.invisibleRootItem()
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

    def _apply_workspace_filters(self, rel_paths: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for rel_path in rel_paths:
            rel = str(rel_path or "").strip().replace("\\", "/")
            while rel.startswith("./"):
                rel = rel[2:]
            if not rel or rel in {".", ".."} or rel.startswith("../"):
                continue
            if "/__pycache__/" in f"/{rel}/" or rel.endswith(".pyc") or rel.endswith(".pyo"):
                continue
            abs_path = os.path.normpath(os.path.join(self._project_root, rel))
            if callable(self._exclude_path_predicate):
                try:
                    if bool(self._exclude_path_predicate(abs_path)):
                        continue
                except Exception:
                    pass
            dedupe = rel.lower()
            if dedupe in seen:
                continue
            seen.add(dedupe)
            output.append(rel)
        output.sort(key=str.lower)
        return output

    def _set_busy(self, busy: bool) -> None:
        disabled = bool(busy)
        self.repo_name_edit.setDisabled(disabled)
        self.description_edit.setDisabled(disabled)
        self.visibility_combo.setDisabled(disabled)
        self.commit_message_edit.setDisabled(disabled)
        self.refresh_btn.setDisabled(disabled)
        self.files_tree.setDisabled(disabled)
        self.select_all_btn.setDisabled(disabled)
        self.select_none_btn.setDisabled(disabled)
        self.cancel_btn.setDisabled(disabled)
        if disabled:
            self.share_btn.setDisabled(True)
        else:
            self._refresh_share_enabled()

    def _refresh_share_enabled(self) -> None:
        if self._pending:
            self.share_btn.setDisabled(True)
            return
        has_repo_name = bool(str(self.repo_name_edit.text() or "").strip())
        has_message = bool(str(self.commit_message_edit.text() or "").strip())
        has_files = bool(self._selected_rel_paths())
        self.share_btn.setEnabled(bool(has_repo_name and has_message and has_files))

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
