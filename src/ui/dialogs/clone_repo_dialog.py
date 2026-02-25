from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.git.git_clone_service import (
    GitCloneError,
    GitCloneService,
    ParsedRepoUrl,
    parse_repo_url,
    sanitize_repo_url,
)
from src.git.github_auth import GitHubAuthStore
from src.git.github_client import GitHubClient, GitHubClientError, GitHubRepo
from src.settings_manager import SettingsManager
from src.ui.custom_dialog import DialogWindow
from src.ui.dialogs.file_dialog_bridge import get_existing_directory


class CloneRepositoryDialog(DialogWindow):
    def __init__(
        self,
        *,
        manager: SettingsManager,
        default_destination: str,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("Clone Repository")
        self.resize(760, 600)

        self._manager = manager
        self._auth_store = GitHubAuthStore(manager.paths.ide_app_dir)
        self._clone_service = GitCloneService(
            ide_app_dir=manager.paths.ide_app_dir,
            github_token_provider=lambda: self._auth_store.get(),
            use_token_for_git_provider=lambda: bool(
                self._manager.get("github.use_token_for_git", scope_preference="ide", default=True)
            ),
        )
        self._default_destination = str(default_destination)
        self._url_folder_name_touched = False
        self._repos_loaded_once = False

        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="pytpo-git")
        self._pending: dict[concurrent.futures.Future, str] = {}
        self._repos: list[GitHubRepo] = []

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(40)
        self._result_pump.timeout.connect(self._drain_pending)

        self.cloned_path: str | None = None
        self.open_after_clone: bool = True

        self._build_ui()
        self._load_initial_state()
        self._refresh_clone_action_state()
        self.destroyed.connect(lambda *_args: self._shutdown())

        QTimer.singleShot(0, self._on_mode_changed)

    def _build_ui(self) -> None:
        host = QWidget(self)
        self.set_content_widget(host)

        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        mode_row = QHBoxLayout()
        mode_label = QLabel("Mode")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("My Repos", "my_repos")
        self.mode_combo.addItem("By URL", "by_url")
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.mode_combo, 1)
        root.addLayout(mode_row)

        self.mode_stack = QStackedWidget()
        root.addWidget(self.mode_stack, 1)

        repos_page = QWidget()
        repos_layout = QVBoxLayout(repos_page)
        repos_layout.setContentsMargins(0, 0, 0, 0)
        repos_layout.setSpacing(8)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search repositories...")
        self.refresh_btn = QPushButton("Refresh")
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.refresh_btn)
        repos_layout.addLayout(search_row)

        self.repo_list = QListWidget()
        self.repo_list.setSelectionMode(QListWidget.SingleSelection)
        repos_layout.addWidget(self.repo_list, 1)
        self.mode_stack.addWidget(repos_page)

        by_url_page = QWidget()
        by_url_form = QFormLayout(by_url_page)
        by_url_form.setHorizontalSpacing(12)
        by_url_form.setVerticalSpacing(8)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://host/user/repo.git or git@host:user/repo.git")
        by_url_form.addRow("Repository URL", self.url_edit)

        self.folder_name_edit = QLineEdit()
        self.folder_name_edit.setPlaceholderText("repo")
        by_url_form.addRow("Target Folder Name", self.folder_name_edit)
        self.mode_stack.addWidget(by_url_page)

        dest_row = QHBoxLayout()
        self.destination_edit = QLineEdit()
        self.destination_edit.setPlaceholderText("Destination folder")
        self.browse_btn = QPushButton("Browse")
        dest_row.addWidget(self.destination_edit, 1)
        dest_row.addWidget(self.browse_btn)
        root.addLayout(dest_row)

        self.open_after_chk = QCheckBox("Open project after clone")
        self.open_after_chk.setChecked(True)
        root.addWidget(self.open_after_chk)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.clone_btn = QPushButton("Clone")
        self.clone_btn.setDefault(True)
        button_row.addWidget(self.cancel_btn)
        button_row.addWidget(self.clone_btn)
        root.addLayout(button_row)

        self.mode_combo.currentIndexChanged.connect(lambda _idx: self._on_mode_changed())
        self.search_edit.textChanged.connect(self._apply_filter)
        self.refresh_btn.clicked.connect(self._fetch_repositories)
        self.url_edit.textChanged.connect(self._on_url_text_changed)
        self.folder_name_edit.textEdited.connect(self._on_folder_name_edited)
        self.folder_name_edit.textChanged.connect(lambda _text: self._refresh_clone_action_state())
        self.browse_btn.clicked.connect(self._browse_destination)
        self.clone_btn.clicked.connect(self._clone_requested)
        self.cancel_btn.clicked.connect(self.reject)
        self.repo_list.itemSelectionChanged.connect(self._refresh_clone_action_state)
        self.destination_edit.textChanged.connect(self._refresh_clone_action_state)

    def _load_initial_state(self) -> None:
        fallback_destination = self._default_destination or str(Path.home())
        saved_destination = str(
            self._manager.get(
                "github.last_clone_destination",
                scope_preference="ide",
                default=fallback_destination,
            )
            or ""
        ).strip()
        destination = saved_destination if saved_destination else fallback_destination
        if not os.path.isdir(destination):
            destination = fallback_destination
        self.destination_edit.setText(destination)

        saved_mode = str(
            self._manager.get("github.last_clone_mode", scope_preference="ide", default="my_repos") or "my_repos"
        ).strip().lower()
        if saved_mode not in {"my_repos", "by_url"}:
            saved_mode = "my_repos"
        idx = self.mode_combo.findData(saved_mode)
        self.mode_combo.setCurrentIndex(max(0, idx))

        saved_url = str(
            self._manager.get("github.last_clone_url", scope_preference="ide", default="") or ""
        ).strip()
        self.url_edit.setText(saved_url)
        self._url_folder_name_touched = False
        self._maybe_autofill_folder_name()

    def _mode(self) -> str:
        mode = str(self.mode_combo.currentData() or "my_repos").strip().lower()
        return mode if mode in {"my_repos", "by_url"} else "my_repos"

    def _on_mode_changed(self) -> None:
        mode = self._mode()
        self.mode_stack.setCurrentIndex(0 if mode == "my_repos" else 1)
        if mode == "my_repos":
            if not self._auth_store.has_token():
                self._set_status("No GitHub token found. Open Settings > GitHub first.", error=True)
            elif not self._repos_loaded_once:
                self._fetch_repositories()
        else:
            if not self._pending:
                self._set_status("Paste a repository URL to clone.")
        self._refresh_clone_action_state()

    def _browse_destination(self) -> None:
        selected = get_existing_directory(
            parent=self,
            manager=self._manager,
            caption="Select Clone Destination",
            directory=self.destination_edit.text().strip(),
        )
        if selected:
            self.destination_edit.setText(selected)

    def _fetch_repositories(self) -> None:
        if self._mode() != "my_repos":
            return
        if not self._auth_store.has_token():
            self._set_status("No GitHub token found. Open Settings > GitHub first.", error=True)
            return
        token = str(self._auth_store.get() or "").strip()
        if not token:
            self._set_status("No GitHub token found. Open Settings > GitHub first.", error=True)
            return
        self._set_busy(True)
        self._set_status("Loading repositories...")

        def _run() -> list[GitHubRepo]:
            client = GitHubClient(token)
            return client.list_repos()

        self._submit_task("fetch", _run)

    def _on_url_text_changed(self, _text: str) -> None:
        self._maybe_autofill_folder_name()
        self._refresh_clone_action_state()

    def _on_folder_name_edited(self, _text: str) -> None:
        self._url_folder_name_touched = True

    def _maybe_autofill_folder_name(self) -> None:
        if self._url_folder_name_touched:
            return
        parsed = self._parsed_url_or_none()
        if parsed is None:
            self.folder_name_edit.setText("")
            return
        self.folder_name_edit.setText(parsed.folder_name)

    def _clone_requested(self) -> None:
        mode = self._mode()
        if mode == "my_repos":
            self._clone_from_selected_repo()
            return
        self._clone_from_url()

    def _clone_from_selected_repo(self) -> None:
        repo = self._selected_repo()
        if repo is None:
            self._set_status("Select a repository to clone.", error=True)
            return

        destination = self._validated_destination()
        if destination is None:
            return

        token = str(self._auth_store.get() or "").strip()
        if not token:
            self._set_status("No GitHub token found. Open Settings > GitHub first.", error=True)
            return

        target_path = (Path(destination).expanduser() / repo.name).resolve()
        if target_path.exists():
            self._set_status("Target folder already exists.", error=True)
            return

        self._set_busy(True)
        self._set_status(f"Cloning {repo.full_name}...")
        self._save_dialog_state()

        def _run() -> str:
            return self._clone_service.clone(
                clone_url=repo.clone_url,
                destination_dir=destination,
                repo_name=repo.name,
                default_branch=repo.default_branch,
            )

        self._submit_task("clone", _run)

    def _clone_from_url(self) -> None:
        parsed = self._parsed_url_or_none()
        if parsed is None:
            self._set_status("Invalid repository URL.", error=True)
            return

        destination = self._validated_destination()
        if destination is None:
            return

        folder_name = str(self.folder_name_edit.text() or "").strip()
        if not self._is_valid_folder_name(folder_name):
            self._set_status("Invalid target folder name.", error=True)
            return

        target_path = (Path(destination).expanduser() / folder_name).resolve()
        if target_path.exists():
            self._set_status("Target folder already exists.", error=True)
            return

        self._set_busy(True)
        self._set_status("Cloning repository...")
        self._save_dialog_state()

        def _run() -> str:
            return self._clone_service.clone(
                clone_url=parsed.normalized_url,
                destination_dir=destination,
                repo_name=folder_name,
                default_branch=None,
            )

        self._submit_task("clone", _run)

    def _validated_destination(self) -> str | None:
        destination = str(self.destination_edit.text() or "").strip()
        if not destination:
            self._set_status("Select a destination folder.", error=True)
            return None
        try:
            Path(destination).expanduser().mkdir(parents=True, exist_ok=True)
        except Exception:
            self._set_status("Could not create destination folder.", error=True)
            return None
        return destination

    def _is_valid_folder_name(self, name: str) -> bool:
        text = str(name or "").strip()
        if not text:
            return False
        if text in {".", ".."}:
            return False
        if "/" in text or "\\" in text:
            return False
        if any(ch in text for ch in ":*?\"<>|"):
            return False
        return True

    def _parsed_url_or_none(self) -> ParsedRepoUrl | None:
        raw = str(self.url_edit.text() or "").strip()
        if not raw:
            return None
        try:
            return parse_repo_url(raw)
        except GitCloneError:
            return None

    def _submit_task(self, kind: str, fn: Callable[[], Any]) -> None:
        try:
            future = self._executor.submit(fn)
        except Exception:
            self._set_busy(False)
            self._set_status("Failed to start background task.", error=True)
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
        if kind == "fetch":
            if error is None and isinstance(result, list):
                self._repos = [repo for repo in result if isinstance(repo, GitHubRepo)]
                self._repos_loaded_once = True
                self._apply_filter()
                count = len(self._repos)
                if count == 0:
                    self._set_status(
                        "No repositories returned. Check token permissions or selected repositories.",
                        error=True,
                    )
                    return
                if count <= 10:
                    self._set_status(
                        f"Loaded {count} repositories. If this seems low, your token may be limited to selected repositories.",
                        error=False,
                    )
                    return
                self._set_status(f"Loaded {count} repositories.")
                return
            if isinstance(error, GitHubClientError):
                self._set_status(str(error), error=True)
                return
            self._set_status("Failed to load repositories.", error=True)
            return

        if kind == "clone":
            if error is None and isinstance(result, str) and result.strip():
                self.cloned_path = result.strip()
                self.open_after_clone = bool(self.open_after_chk.isChecked())
                self._save_dialog_state()
                self.accept()
                return
            if isinstance(error, GitCloneError):
                self._set_status(self._friendly_clone_error(error), error=True)
                return
            self._set_status("Failed to clone repository.", error=True)

    def _friendly_clone_error(self, error: GitCloneError) -> str:
        kind = str(getattr(error, "kind", "") or "").strip()
        if kind == "invalid_url":
            return "Invalid repository URL."
        if kind == "git_not_installed":
            return "Git is not installed or not in PATH."
        if kind == "auth_failed":
            return "Authentication failed while cloning. Check token permissions and Git transport bridge setting."
        if kind == "repo_not_found":
            return "Repository not found or access denied."
        if kind == "network_error":
            return "Network error while cloning repository."
        if kind == "destination_exists":
            return "Target folder already exists."
        return str(error) or "Failed to clone repository."

    def _save_dialog_state(self) -> None:
        mode = self._mode()
        destination = str(self.destination_edit.text() or "").strip()
        raw_url = str(self.url_edit.text() or "").strip()
        safe_url = sanitize_repo_url(raw_url)
        try:
            self._manager.set("github.last_clone_mode", mode, "ide")
            if destination:
                self._manager.set("github.last_clone_destination", destination, "ide")
            self._manager.set("github.last_clone_url", safe_url, "ide")
            self._manager.save_all(scopes={"ide"}, only_dirty=True)
        except Exception:
            pass

    def _apply_filter(self) -> None:
        query = str(self.search_edit.text() or "").strip().lower()
        self.repo_list.clear()
        for repo in self._repos:
            full_name = repo.full_name
            if query and query not in full_name.lower() and query not in repo.name.lower():
                continue
            suffix = " [private]" if repo.private else ""
            item = QListWidgetItem(f"{full_name}{suffix}")
            item.setData(Qt.UserRole, repo)
            self.repo_list.addItem(item)
        self._refresh_clone_action_state()

    def _selected_repo(self) -> GitHubRepo | None:
        item = self.repo_list.currentItem()
        if item is None:
            return None
        repo = item.data(Qt.UserRole)
        return repo if isinstance(repo, GitHubRepo) else None

    def _refresh_clone_action_state(self) -> None:
        if self._pending:
            self.clone_btn.setDisabled(True)
            return

        destination = str(self.destination_edit.text() or "").strip()
        if not destination:
            self.clone_btn.setDisabled(True)
            return

        mode = self._mode()
        if mode == "my_repos":
            has_repo = self._selected_repo() is not None
            enabled = bool(has_repo and self._auth_store.has_token())
            self.clone_btn.setEnabled(enabled)
            return

        parsed = self._parsed_url_or_none()
        folder_name = str(self.folder_name_edit.text() or "").strip()
        enabled = bool(parsed is not None and self._is_valid_folder_name(folder_name))
        self.clone_btn.setEnabled(enabled)

    def _set_busy(self, busy: bool) -> None:
        disabled = bool(busy)
        self.mode_combo.setDisabled(disabled)
        self.search_edit.setDisabled(disabled)
        self.repo_list.setDisabled(disabled)
        self.refresh_btn.setDisabled(disabled)
        self.url_edit.setDisabled(disabled)
        self.folder_name_edit.setDisabled(disabled)
        self.destination_edit.setDisabled(disabled)
        self.browse_btn.setDisabled(disabled)
        self.open_after_chk.setDisabled(disabled)
        if disabled:
            self.clone_btn.setDisabled(True)
        else:
            self._refresh_clone_action_state()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = "#d46a6a" if error else "#a4bf7a"
        self.status_label.setText(f"<span style='color:{color};'>{text}</span>")

    def reject(self) -> None:
        if self._pending:
            answer = QMessageBox.question(
                self,
                "Cancel Clone",
                "Background work is still running. Close this dialog anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self._save_dialog_state()
        super().reject()

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
