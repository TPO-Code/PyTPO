from __future__ import annotations

import concurrent.futures
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from barley_ide.git.git_service import (
    GitBranchInfo,
    GitRepoDisplayState,
    GitService,
    GitServiceError,
    format_git_branch_label,
    format_git_remote_label,
)
from TPOPyside.dialogs.custom_dialog import DialogWindow


class GitBranchesDialog(DialogWindow):
    def __init__(
        self,
        *,
        git_service: GitService,
        repo_root: str,
        repo_options: list[tuple[str, str]] | None = None,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("Branches")
        self.resize(620, 520)

        self._git_service = git_service
        self._repo_root = str(repo_root)
        self._repo_options = [(str(label), str(root)) for label, root in (repo_options or []) if str(root).strip()]
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pytpo-git-branch")
        self._pending: dict[concurrent.futures.Future, str] = {}

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(40)
        self._result_pump.timeout.connect(self._drain_pending)

        self._build_ui()
        self.destroyed.connect(lambda *_args: self._shutdown())
        QTimer.singleShot(0, self._load_branches)

    def _build_ui(self) -> None:
        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.repo_label = QLabel(f"Repository: {self._repo_root}")
        self.repo_label.setWordWrap(True)
        root.addWidget(self.repo_label)
        self.repo_state_label = QLabel("Git state: (loading...)")
        self.repo_state_label.setWordWrap(True)
        root.addWidget(self.repo_state_label)

        self.repo_combo: QComboBox | None = None
        if len(self._repo_options) > 1:
            repo_row = QHBoxLayout()
            repo_row.addWidget(QLabel("Target Repository"), 0)
            combo = QComboBox()
            for label, repo_path in self._repo_options:
                combo.addItem(label, repo_path)
            current_index = max(0, combo.findData(self._repo_root))
            combo.setCurrentIndex(current_index)
            combo.currentIndexChanged.connect(self._on_repo_changed)
            repo_row.addWidget(combo, 1)
            root.addLayout(repo_row)
            self.repo_combo = combo

        top = QHBoxLayout()
        self.current_label = QLabel("Current: ")
        top.addWidget(self.current_label, 1)
        self.show_remote_chk = QCheckBox("Show remote branches")
        self.show_remote_chk.setChecked(False)
        top.addWidget(self.show_remote_chk)
        self.refresh_btn = QPushButton("Refresh")
        top.addWidget(self.refresh_btn)
        root.addLayout(top)

        self.branches_list = QListWidget()
        root.addWidget(self.branches_list, 1)

        checkout_row = QHBoxLayout()
        self.checkout_btn = QPushButton("Checkout Selected")
        checkout_row.addStretch(1)
        checkout_row.addWidget(self.checkout_btn)
        root.addLayout(checkout_row)

        create_row = QHBoxLayout()
        self.new_branch_edit = QLineEdit()
        self.new_branch_edit.setPlaceholderText("new-branch-name")
        self.auto_checkout_chk = QCheckBox("Checkout after create")
        self.auto_checkout_chk.setChecked(True)
        self.create_btn = QPushButton("Create Branch")
        create_row.addWidget(self.new_branch_edit, 1)
        create_row.addWidget(self.auto_checkout_chk)
        create_row.addWidget(self.create_btn)
        root.addLayout(create_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.close_btn = QPushButton("Close")
        footer.addWidget(self.close_btn)
        root.addLayout(footer)

        self.refresh_btn.clicked.connect(self._load_branches)
        self.checkout_btn.clicked.connect(self._checkout_selected)
        self.create_btn.clicked.connect(self._create_branch)
        self.close_btn.clicked.connect(self.accept)
        self.show_remote_chk.toggled.connect(lambda _checked=False: self._load_branches())
        self.new_branch_edit.textChanged.connect(self._refresh_action_state)
        self.branches_list.itemSelectionChanged.connect(self._refresh_action_state)

    def _load_branches(self) -> None:
        self._set_busy(True)
        self._set_status("Loading branches...")
        include_remote = bool(self.show_remote_chk.isChecked())

        def _run():
            if include_remote:
                # Refresh remote refs so newly created server branches appear.
                self._git_service.fetch(self._repo_root, prune=True)
            info = self._git_service.list_branches(self._repo_root, include_remote=include_remote)
            state = None
            describe = getattr(self._git_service, "describe_repo_state", None)
            if callable(describe):
                try:
                    state = describe(self._repo_root)
                except Exception:
                    state = None
            return info, state

        self._submit_task("load", _run)

    def _checkout_selected(self) -> None:
        item = self.branches_list.currentItem()
        if item is None:
            self._set_status("Select a branch.", error=True)
            return
        branch = str(item.data(Qt.UserRole + 1) or item.text() or "").strip()
        if not branch:
            self._set_status("Select a branch.", error=True)
            return
        item_kind = str(item.data(Qt.UserRole) or "local")

        self._set_busy(True)
        if item_kind == "remote":
            self._set_status(f"Checking out tracking branch for {branch}...")
        else:
            self._set_status(f"Checking out {branch}...")

        def _run():
            if item_kind == "remote":
                local_name = self._git_service.checkout_remote_branch(self._repo_root, branch)
                return f"{local_name} (tracking {branch})"
            self._git_service.checkout_branch(self._repo_root, branch)
            return branch

        self._submit_task("checkout", _run)

    def _create_branch(self) -> None:
        name = str(self.new_branch_edit.text() or "").strip()
        if not name:
            self._set_status("Enter a branch name.", error=True)
            return
        checkout = bool(self.auto_checkout_chk.isChecked())

        self._set_busy(True)
        self._set_status(f"Creating branch {name}...")

        def _run():
            self._git_service.create_branch(self._repo_root, name, checkout=checkout)
            return name

        self._submit_task("create", _run)

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
        if error is not None:
            if isinstance(error, GitServiceError):
                self._set_status(str(error), error=True)
            else:
                self._set_status("Git operation failed.", error=True)
            return

        info = result
        repo_state = None
        if isinstance(result, tuple) and result:
            info = result[0]
            if len(result) > 1 and isinstance(result[1], GitRepoDisplayState):
                repo_state = result[1]
        if kind == "load" and isinstance(info, GitBranchInfo):
            branch_text = format_git_branch_label(repo_state) or str(info.current or "").strip() or "HEAD (detached)"
            remote_text = format_git_remote_label(repo_state)
            self.current_label.setText(f"Current: {branch_text}")
            self.repo_state_label.setText(
                f"Git state: {branch_text}" + (f" | {remote_text}" if remote_text else "")
            )
            self.branches_list.clear()
            for branch in info.branches:
                item = QListWidgetItem(branch)
                item.setData(Qt.UserRole, "local")
                item.setData(Qt.UserRole + 1, branch)
                self.branches_list.addItem(item)
            remote_count = 0
            for branch in info.remote_branches:
                item = QListWidgetItem(f"{branch} [remote]")
                item.setData(Qt.UserRole, "remote")
                item.setData(Qt.UserRole + 1, branch)
                self.branches_list.addItem(item)
                remote_count += 1
            if remote_count:
                self._set_status(f"Loaded {len(info.branches)} local and {remote_count} remote branch(es).")
            else:
                self._set_status(f"Loaded {len(info.branches)} branch(es).")
            self._refresh_action_state()
            return

        if kind == "checkout":
            self._set_status(f"Checked out {str(result or '').strip()}.")
            self._load_branches()
            return

        if kind == "create":
            self.new_branch_edit.clear()
            self._set_status(f"Created branch {str(result or '').strip()}.")
            self._load_branches()

    def _refresh_action_state(self) -> None:
        busy = bool(self._pending)
        if self.repo_combo is not None:
            self.repo_combo.setDisabled(busy)
        if busy:
            self.checkout_btn.setEnabled(False)
            self.create_btn.setEnabled(False)
            return
        current = self.branches_list.currentItem()
        can_checkout = current is not None
        self.checkout_btn.setEnabled(can_checkout)
        self.create_btn.setEnabled(bool(str(self.new_branch_edit.text() or "").strip()))

    def _set_busy(self, busy: bool) -> None:
        disabled = bool(busy)
        self.refresh_btn.setDisabled(disabled)
        self.branches_list.setDisabled(disabled)
        self.show_remote_chk.setDisabled(disabled)
        self.new_branch_edit.setDisabled(disabled)
        self.auto_checkout_chk.setDisabled(disabled)
        self.close_btn.setDisabled(disabled)
        self._refresh_action_state()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = "#d46a6a" if error else "#a4bf7a"
        self.status_label.setText(f"<span style='color:{color};'>{text}</span>")

    def _on_repo_changed(self, index: int) -> None:
        if self.repo_combo is None:
            return
        repo_root = str(self.repo_combo.itemData(index) or "").strip()
        if not repo_root or repo_root == self._repo_root:
            return
        self._repo_root = repo_root
        self.repo_label.setText(f"Repository: {self._repo_root}")
        self.repo_state_label.setText("Git state: (loading...)")
        self.branches_list.clear()
        self.current_label.setText("Current: ")
        self._load_branches()

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
