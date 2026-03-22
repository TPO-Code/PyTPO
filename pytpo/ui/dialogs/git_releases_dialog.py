from __future__ import annotations

import concurrent.futures
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pytpo.git.git_service import GitService, format_git_branch_label, format_git_remote_label
from pytpo.git.github_release_service import (
    GitHubReleaseDeleteResult,
    GitHubReleaseError,
    GitHubReleaseService,
    GitHubReleaseSummary,
)
from TPOPyside.dialogs.custom_dialog import DialogWindow


class GitReleasesDialog(DialogWindow):
    def __init__(
        self,
        *,
        release_service: GitHubReleaseService,
        git_service: GitService | None = None,
        repo_root: str,
        repo_options: list[tuple[str, str]] | None = None,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("GitHub Releases")
        self.resize(900, 640)

        self._release_service = release_service
        self._git_service = git_service
        self._repo_root = str(repo_root or "").strip()
        self._repo_options = [(str(label), str(root)) for label, root in (repo_options or []) if str(root).strip()]
        self._releases_by_id: dict[int, GitHubReleaseSummary] = {}
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pytpo-github-releases")
        self._pending: dict[concurrent.futures.Future, tuple[str, dict[str, Any]]] = {}

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(40)
        self._result_pump.timeout.connect(self._drain_pending)

        self._build_ui()
        self.destroyed.connect(lambda *_args: self._shutdown())
        QTimer.singleShot(0, self._load_releases)

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

        self.note_label = QLabel(
            "Manage GitHub releases. Deleting a release can optionally delete its remote tag."
        )
        self.note_label.setWordWrap(True)
        root.addWidget(self.note_label)

        top = QHBoxLayout()
        self.count_label = QLabel("Releases: 0")
        top.addWidget(self.count_label, 1)
        self.refresh_btn = QPushButton("Refresh")
        top.addWidget(self.refresh_btn)
        root.addLayout(top)

        self.releases_tree = QTreeWidget()
        self.releases_tree.setColumnCount(4)
        self.releases_tree.setHeaderLabels(["Tag", "Title", "Published", "Type"])
        self.releases_tree.setRootIsDecorated(False)
        root.addWidget(self.releases_tree, 1)

        self.details_edit = QPlainTextEdit()
        self.details_edit.setReadOnly(True)
        self.details_edit.setPlaceholderText("Select a release to view details and notes.")
        self.details_edit.setFixedHeight(170)
        root.addWidget(self.details_edit)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        actions = QHBoxLayout()
        self.open_btn = QPushButton("Open on GitHub")
        self.delete_btn = QPushButton("Delete Release")
        self.delete_with_tag_btn = QPushButton("Delete Release + Tag")
        self.close_btn = QPushButton("Close")
        self.close_btn.setDefault(True)
        actions.addWidget(self.open_btn)
        actions.addWidget(self.delete_btn)
        actions.addWidget(self.delete_with_tag_btn)
        actions.addStretch(1)
        actions.addWidget(self.close_btn)
        root.addLayout(actions)

        self.refresh_btn.clicked.connect(self._load_releases)
        self.releases_tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.open_btn.clicked.connect(self._open_selected_release)
        self.delete_btn.clicked.connect(lambda: self._delete_selected(delete_tag=False))
        self.delete_with_tag_btn.clicked.connect(lambda: self._delete_selected(delete_tag=True))
        self.close_btn.clicked.connect(self.accept)

        self._refresh_actions()

    def _load_releases(self) -> None:
        self._set_busy(True)
        self._set_status("Loading releases from GitHub...")

        def _run():
            releases = self._release_service.list_releases(self._repo_root)
            state = None
            if self._git_service is not None:
                try:
                    state = self._git_service.describe_repo_state(self._repo_root)
                except Exception:
                    state = None
            return releases, state

        self._submit_task("load", _run)

    def _selected_release(self) -> GitHubReleaseSummary | None:
        item = self.releases_tree.currentItem()
        if item is None:
            return None
        rid = int(item.data(0, Qt.UserRole) or 0)
        if rid <= 0:
            return None
        return self._releases_by_id.get(rid)

    def _open_selected_release(self) -> None:
        release = self._selected_release()
        if release is None:
            self._set_status("Select a release first.", error=True)
            return
        url_text = str(release.html_url or "").strip()
        if not url_text:
            self._set_status("Selected release has no GitHub URL.", error=True)
            return
        ok = QDesktopServices.openUrl(QUrl(url_text))
        if ok:
            self._set_status(f"Opened {release.tag_name} on GitHub.")
        else:
            self._set_status("Could not open release URL.", error=True)

    def _delete_selected(self, *, delete_tag: bool) -> None:
        release = self._selected_release()
        if release is None:
            self._set_status("Select a release first.", error=True)
            return

        tag = str(release.tag_name or "").strip()
        if delete_tag:
            prompt = (
                f"Delete release '{tag}' and delete remote tag '{tag}' on GitHub?\n\n"
                "This cannot be undone."
            )
        else:
            prompt = (
                f"Delete release '{tag}' on GitHub?\n\n"
                "The tag will remain unless you choose 'Delete Release + Tag'."
            )

        answer = QMessageBox.warning(
            self,
            "Delete GitHub Release",
            prompt,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        self._set_busy(True)
        self._set_status("Deleting release..." if not delete_tag else "Deleting release and remote tag...")

        def _run():
            return self._release_service.delete_release(
                repo_root=self._repo_root,
                release_id=release.id,
                tag_name=release.tag_name,
                delete_tag=delete_tag,
            )

        self._submit_task("delete", _run, context={"delete_tag": bool(delete_tag)})

    def _submit_task(self, kind: str, fn: Callable[[], Any], *, context: dict[str, Any] | None = None) -> None:
        try:
            future = self._executor.submit(fn)
        except Exception:
            self._set_busy(False)
            self._set_status("Could not start GitHub release operation.", error=True)
            return
        self._pending[future] = (kind, dict(context or {}))
        if not self._result_pump.isActive():
            self._result_pump.start()
        self._refresh_actions()

    def _drain_pending(self) -> None:
        if not self._pending:
            self._result_pump.stop()
            return

        done: list[concurrent.futures.Future] = []
        for future, payload in list(self._pending.items()):
            if not future.done():
                continue
            done.append(future)
            kind, context = payload
            try:
                result = future.result()
                error = None
            except Exception as exc:
                result = None
                error = exc
            self._handle_result(kind, context, result, error)

        for future in done:
            self._pending.pop(future, None)

        if not self._pending:
            self._result_pump.stop()
            self._set_busy(False)
        self._refresh_actions()

    def _handle_result(self, kind: str, context: dict[str, Any], result: Any, error: Exception | None) -> None:
        if error is not None:
            if isinstance(error, GitHubReleaseError):
                self._set_status(str(error), error=True)
                self.details_edit.setPlainText(str(error))
            else:
                self._set_status("GitHub release operation failed.", error=True)
            return

        if kind == "load":
            releases = result
            repo_state = None
            if isinstance(result, tuple):
                releases = result[0] if result else []
                if len(result) > 1:
                    repo_state = result[1]
            branch_text = format_git_branch_label(repo_state)
            remote_text = format_git_remote_label(repo_state)
            if branch_text:
                self.repo_state_label.setText(
                    f"Git state: {branch_text}" + (f" | {remote_text}" if remote_text else "")
                )
            elif remote_text:
                self.repo_state_label.setText(f"Git state: {remote_text}")
            else:
                self.repo_state_label.setText("Git state: unavailable")
            releases = releases if isinstance(releases, list) else []
            self._populate_releases([item for item in releases if isinstance(item, GitHubReleaseSummary)])
            return

        if kind == "delete" and isinstance(result, GitHubReleaseDeleteResult):
            if bool(context.get("delete_tag")) and result.tag_name:
                if result.remote_tag_deleted:
                    self._set_status(f"Deleted release {result.tag_name} and remote tag.")
                else:
                    self._set_status(
                        f"Deleted release {result.tag_name}. Remote tag was already missing."
                    )
            else:
                self._set_status(f"Deleted release {result.tag_name or result.release_id}.")
            self._load_releases()

    def _populate_releases(self, releases: list[GitHubReleaseSummary]) -> None:
        self._releases_by_id = {int(item.id): item for item in releases if int(item.id) > 0}
        self.releases_tree.clear()

        for release in releases:
            release_type = "Draft" if release.draft else ("Pre-release" if release.prerelease else "Release")
            published = str(release.published_at or release.created_at or "").strip() or "-"
            title = str(release.title or "").strip() or str(release.tag_name or "").strip()
            item = QTreeWidgetItem(
                [
                    str(release.tag_name or "").strip() or "-",
                    title,
                    published,
                    release_type,
                ]
            )
            item.setData(0, Qt.UserRole, int(release.id))
            item.setToolTip(0, str(release.html_url or "").strip())
            self.releases_tree.addTopLevelItem(item)

        self.releases_tree.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        self.count_label.setText(f"Releases: {len(releases)}")
        if releases:
            self._set_status(f"Loaded {len(releases)} release(s).")
            self.releases_tree.setCurrentItem(self.releases_tree.topLevelItem(0))
        else:
            self.details_edit.clear()
            self._set_status("No releases found.")
        self._refresh_actions()

    def _on_selection_changed(self) -> None:
        release = self._selected_release()
        if release is None:
            self.details_edit.clear()
            self._refresh_actions()
            return

        lines: list[str] = []
        lines.append(f"Tag: {release.tag_name}")
        lines.append(f"Title: {release.title or release.tag_name}")
        lines.append(f"Type: {'Draft' if release.draft else ('Pre-release' if release.prerelease else 'Release')}")
        lines.append(f"Published: {release.published_at or '-'}")
        lines.append(f"Created: {release.created_at or '-'}")
        lines.append(f"Target: {release.target_commitish or '-'}")
        lines.append(f"URL: {release.html_url or '-'}")
        notes = str(release.notes or "").strip()
        if notes:
            lines.append("")
            lines.append("Notes:")
            lines.append(notes)
        self.details_edit.setPlainText("\n".join(lines))
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        busy = bool(self._pending)
        selected = self._selected_release()
        if self.repo_combo is not None:
            self.repo_combo.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy)
        self.releases_tree.setEnabled(not busy)
        self.close_btn.setEnabled(not busy)
        can_act = (selected is not None) and (not busy)
        self.open_btn.setEnabled(can_act and bool(str(selected.html_url if selected else "").strip()))
        self.delete_btn.setEnabled(can_act)
        self.delete_with_tag_btn.setEnabled(can_act and bool(str(selected.tag_name if selected else "").strip()))

    def _set_busy(self, busy: bool) -> None:
        disabled = bool(busy)
        self.refresh_btn.setDisabled(disabled)
        self.releases_tree.setDisabled(disabled)
        self.close_btn.setDisabled(disabled)
        if disabled:
            self.open_btn.setDisabled(True)
            self.delete_btn.setDisabled(True)
            self.delete_with_tag_btn.setDisabled(True)
        else:
            self._refresh_actions()

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
        self._releases_by_id.clear()
        self.releases_tree.clear()
        self.details_edit.clear()
        self.count_label.setText("Releases: 0")
        self._load_releases()

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
                "A release operation is running. Close anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        super().reject()
