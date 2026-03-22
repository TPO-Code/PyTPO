from __future__ import annotations

import concurrent.futures
import os
import re
import tomllib
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pytpo.git.git_service import (
    GitRepoDisplayState,
    GitService,
    GitServiceError,
    format_git_branch_label,
    format_git_remote_label,
)
from pytpo.git.github_release_service import (
    GitHubReleaseError,
    GitHubReleaseRequest,
    GitHubReleaseService,
)
from TPOPyside.dialogs.custom_dialog import DialogWindow
from pytpo.ui.icons.file_icon_provider import FileIconProvider
from pytpo.ui.widgets.spellcheck_inputs import SpellcheckPlainTextEdit

_BUILD_TAG_RE = re.compile(
    r"^v?(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)\+build\.(?P<build>[0-9]+)$"
)
_BRANCH_FORBIDDEN_CHARS = set("~^:?*[\\")


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
      - optional release_service for tag/release publishing
    """

    def __init__(
        self,
        *,
        git_service: GitService,
        repo_root: str,
        release_service: GitHubReleaseService | None = None,
        repo_options: list[tuple[str, str]] | None = None,
        exclude_path_predicate: Callable[..., bool] | None = None,
        exclude_untracked_predicate: Callable[[str], bool] | None = None,
        prefer_push_action: bool = False,
        initial_commit_message: str = "",
        initial_release_message: str = "",
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("Commit Changes")
        self.resize(980, 760)

        self._git_service = git_service
        self._repo_root = str(repo_root)
        self._repo_options = [(str(label), str(root)) for label, root in (repo_options or []) if str(root).strip()]
        self._release_service = release_service
        self._exclude_path_predicate = exclude_path_predicate
        self._exclude_untracked_predicate = exclude_untracked_predicate
        self._repo_has_pyproject = os.path.isfile(os.path.join(self._repo_root, "pyproject.toml"))
        self._repo_has_cargo_toml = os.path.isfile(os.path.join(self._repo_root, "Cargo.toml"))

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
        self._release_build_user_modified = False
        self._updating_release_build_value = False
        self._initial_commit_message = str(initial_commit_message or "")
        self._initial_release_message = str(initial_release_message or "")
        self._current_branch_name: str = ""

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(40)
        self._result_pump.timeout.connect(self._drain_pending)

        self.commit_output: str = ""
        self.push_output: str = ""
        self.push_error: str = ""
        self.push_error_kind: str = ""
        self.release_url: str = ""
        self.release_error: str = ""

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
            "Select files to commit. Checking a folder selects all visible files under it."
        )
        self.note_label.setWordWrap(True)
        root.addWidget(self.note_label)

        top_row = QHBoxLayout()
        self.branch_label = QLabel("Current branch: (loading...)")
        top_row.addWidget(self.branch_label, 1)

        self.refresh_btn = QPushButton("Refresh")
        top_row.addWidget(self.refresh_btn)
        root.addLayout(top_row)

        content_row = QHBoxLayout()
        content_row.setSpacing(12)

        trees_column = QVBoxLayout()
        trees_column.setSpacing(10)

        tree_header = QLabel("Files")
        trees_column.addWidget(tree_header)
        self.files_tree = QTreeWidget()
        self.files_tree.setColumnCount(1)
        self.files_tree.setHeaderLabel("Changed / Unversioned")
        trees_column.addWidget(self.files_tree, 1)
        content_row.addLayout(trees_column, 1)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        content_row.addWidget(separator)

        details_column = QVBoxLayout()
        details_column.setSpacing(8)

        self.message_edit = SpellcheckPlainTextEdit()
        self.message_edit.setPlaceholderText("Commit message")
        self.message_edit.setMinimumHeight(180)
        details_column.addWidget(self.message_edit, 2)

        self.release_chk = QCheckBox("Create GitHub release after push")
        details_column.addWidget(self.release_chk)

        self.release_form = QWidget()
        release_layout = QVBoxLayout(self.release_form)
        release_layout.setContentsMargins(0, 0, 0, 0)
        release_layout.setSpacing(8)

        version_row = QHBoxLayout()
        version_row.addWidget(QLabel("Version"), 0)
        self.release_version_edit = QLineEdit()
        self.release_version_edit.setPlaceholderText("1.2.3")
        version_row.addWidget(self.release_version_edit, 1)
        version_row.addWidget(QLabel("Build"), 0)
        self.release_build_spin = QSpinBox()
        self.release_build_spin.setRange(0, 1_000_000_000)
        self.release_build_spin.setValue(0)
        self.release_build_spin.setToolTip("Build number used when publishing multiple releases for the same version.")
        version_row.addWidget(self.release_build_spin, 0)
        version_row.addWidget(QLabel("Tag"), 0)
        self.release_tag_edit = QLineEdit()
        self.release_tag_edit.setPlaceholderText("v1.2.3+build.1")
        version_row.addWidget(self.release_tag_edit, 1)
        release_layout.addLayout(version_row)

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("Title"), 0)
        self.release_title_edit = QLineEdit()
        self.release_title_edit.setPlaceholderText("v1.2.3 (build 1)")
        title_row.addWidget(self.release_title_edit, 1)
        release_layout.addLayout(title_row)

        self.release_notes_edit = SpellcheckPlainTextEdit()
        self.release_notes_edit.setPlaceholderText("Release notes (optional)")
        self.release_notes_edit.setMinimumHeight(120)
        release_layout.addWidget(self.release_notes_edit, 1)

        self.release_prerelease_chk = QCheckBox("Pre-release")
        release_layout.addWidget(self.release_prerelease_chk)

        self.bump_pyproject_chk = QCheckBox("Update pyproject.toml version")
        self.bump_pyproject_chk.setChecked(True)
        release_layout.addWidget(self.bump_pyproject_chk)

        self.bump_cargo_chk = QCheckBox("Update Cargo.toml version")
        self.bump_cargo_chk.setChecked(True)
        release_layout.addWidget(self.bump_cargo_chk)

        details_column.addWidget(self.release_form, 1)
        self.release_form.setVisible(False)

        self.branch_form = QWidget()
        branch_layout = QVBoxLayout(self.branch_form)
        branch_layout.setContentsMargins(0, 0, 0, 0)
        branch_layout.setSpacing(6)
        self.current_branch_detail_label = QLabel("Current branch: (loading...)")
        branch_layout.addWidget(self.current_branch_detail_label)
        self.target_branch_label = QLabel("Commit target: (loading...)")
        branch_layout.addWidget(self.target_branch_label)
        self.commit_new_branch_chk = QCheckBox("Commit to new branch")
        branch_layout.addWidget(self.commit_new_branch_chk)
        self.new_branch_name_row = QWidget(self.branch_form)
        new_branch_row_layout = QHBoxLayout(self.new_branch_name_row)
        new_branch_row_layout.setContentsMargins(0, 0, 0, 0)
        new_branch_row_layout.setSpacing(6)
        new_branch_row_layout.addWidget(QLabel("New branch name"), 0)
        self.new_branch_name_edit = QLineEdit(self.new_branch_name_row)
        self.new_branch_name_edit.setPlaceholderText("feature/my-change")
        new_branch_row_layout.addWidget(self.new_branch_name_edit, 1)
        branch_layout.addWidget(self.new_branch_name_row)
        self.new_branch_name_row.setVisible(False)
        details_column.addWidget(self.branch_form)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        details_column.addWidget(self.status_label)

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
        details_column.addLayout(actions)

        content_row.addLayout(details_column, 2)
        root.addLayout(content_row, 1)

        # signals
        self.refresh_btn.clicked.connect(self._load_changes)
        self.cancel_btn.clicked.connect(self.reject)

        self.commit_btn.clicked.connect(lambda: self._commit_clicked(push_after=False))
        self.commit_push_btn.clicked.connect(lambda: self._commit_clicked(push_after=True))

        self.message_edit.textChanged.connect(self._refresh_commit_enabled)
        self.release_chk.toggled.connect(self._on_release_toggle)
        self.release_version_edit.textChanged.connect(self._on_release_version_changed)
        self.release_build_spin.valueChanged.connect(self._on_release_build_changed)
        self.release_tag_edit.textChanged.connect(self._refresh_commit_enabled)
        self.release_tag_edit.textEdited.connect(lambda _text: self.release_tag_edit.setModified(True))
        self.release_title_edit.textEdited.connect(lambda _text: self.release_title_edit.setModified(True))
        self.commit_new_branch_chk.toggled.connect(self._on_commit_new_branch_toggled)
        self.new_branch_name_edit.textChanged.connect(self._on_new_branch_name_changed)
        self.files_tree.itemChanged.connect(self._on_item_changed)

        self._refresh_branch_target_state()
        self._refresh_repo_capabilities()
        self._seed_release_version()
        if self._initial_commit_message:
            self.message_edit.setPlainText(self._initial_commit_message)
        if self._initial_release_message:
            self.release_notes_edit.setPlainText(self._initial_release_message)

    def commit_message_text(self) -> str:
        return str(self.message_edit.toPlainText() or "")

    def release_message_text(self) -> str:
        return str(self.release_notes_edit.toPlainText() or "")

    def selected_repo_root(self) -> str:
        return str(self._repo_root or "").strip()

    def _load_changes(self) -> None:
        self._set_busy(True)
        self._set_status("Loading files...")

        def _run():
            # Backward-compatible call path:
            try:
                status = self._git_service.read_status(self._repo_root, include_untracked=True)
            except TypeError:
                # Old GitService signature: read_status(repo_root)
                status = self._git_service.read_status(self._repo_root)
            state = None
            describe = getattr(self._git_service, "describe_repo_state", None)
            if callable(describe):
                try:
                    state = describe(self._repo_root)
                except Exception:
                    state = None
            return status, state

        self._submit_task("load", _run)

    def _seed_release_version(self) -> None:
        version = ""
        if self._repo_has_pyproject:
            version = self._read_pyproject_version()
        if not version and self._repo_has_cargo_toml:
            version = self._read_cargo_version()
        self._release_build_user_modified = False
        if version:
            self.release_version_edit.setText(version)
        else:
            self.release_version_edit.setText("")
        self._set_release_build_value(self._next_build_for_version(version) if version else 0)
        self._sync_release_identity_fields()
        self.release_tag_edit.setModified(False)
        self.release_title_edit.setModified(False)

    def _refresh_repo_capabilities(self) -> None:
        self._repo_has_pyproject = os.path.isfile(os.path.join(self._repo_root, "pyproject.toml"))
        self._repo_has_cargo_toml = os.path.isfile(os.path.join(self._repo_root, "Cargo.toml"))
        if self.bump_pyproject_chk is not None:
            self.bump_pyproject_chk.setVisible(self._repo_has_pyproject)
            self.bump_pyproject_chk.setChecked(self._repo_has_pyproject)
        if self.bump_cargo_chk is not None:
            self.bump_cargo_chk.setVisible(self._repo_has_cargo_toml)
            self.bump_cargo_chk.setChecked(self._repo_has_cargo_toml)

    def _on_repo_changed(self, index: int) -> None:
        if self.repo_combo is None:
            return
        repo_root = str(self.repo_combo.itemData(index) or "").strip()
        if not repo_root or repo_root == self._repo_root:
            return
        self._repo_root = repo_root
        self.repo_label.setText(f"Repository: {self._repo_root}")
        self.repo_state_label.setText("Git state: (loading...)")
        self._current_branch_name = ""
        self.branch_label.setText("Current branch: (loading...)")
        self.current_branch_detail_label.setText("Current branch: (loading...)")
        self.files_tree.clear()
        self._file_states.clear()
        self._checked_by_path.clear()
        self._refresh_repo_capabilities()
        self._seed_release_version()
        self._load_changes()

    def _set_release_build_value(self, value: int) -> None:
        self._updating_release_build_value = True
        try:
            self.release_build_spin.setValue(max(0, int(value)))
        except Exception:
            self.release_build_spin.setValue(0)
        finally:
            self._updating_release_build_value = False

    def _next_build_for_version(self, version: str) -> int:
        text = str(version or "").strip()
        if not text:
            return 0

        tags: list[str] = []
        list_tags_fn = getattr(self._git_service, "list_tags", None)
        if callable(list_tags_fn):
            try:
                tags = list_tags_fn(self._repo_root, pattern=f"v{text}*")
            except Exception:
                tags = []

        max_seen = -1
        bare_tag = f"v{text}"
        for raw_tag in tags:
            tag = str(raw_tag or "").strip()
            if not tag:
                continue
            if tag == bare_tag or tag == text:
                max_seen = max(max_seen, 0)
                continue
            match = _BUILD_TAG_RE.fullmatch(tag)
            if match is None:
                continue
            if str(match.group("version") or "").strip() != text:
                continue
            try:
                build = int(match.group("build") or "0")
            except Exception:
                continue
            max_seen = max(max_seen, build)

        if max_seen < 0:
            return 0
        return max_seen + 1

    @staticmethod
    def _compose_release_tag(version: str, build_number: int) -> str:
        text = str(version or "").strip()
        if not text:
            return ""
        if int(build_number) <= 0:
            return f"v{text}"
        return f"v{text}+build.{int(build_number)}"

    @staticmethod
    def _compose_release_title(version: str, build_number: int) -> str:
        text = str(version or "").strip()
        if not text:
            return ""
        if int(build_number) <= 0:
            return f"v{text}"
        return f"v{text} (build {int(build_number)})"

    def _sync_release_identity_fields(self) -> None:
        version = str(self.release_version_edit.text() or "").strip()
        build = int(self.release_build_spin.value())
        if not self.release_tag_edit.isModified():
            self.release_tag_edit.setText(self._compose_release_tag(version, build))
        if not self.release_title_edit.isModified():
            self.release_title_edit.setText(self._compose_release_title(version, build))

    def _read_pyproject_version(self) -> str:
        pyproject_path = os.path.join(self._repo_root, "pyproject.toml")
        if not os.path.isfile(pyproject_path):
            return ""
        try:
            payload = tomllib.loads(Path(pyproject_path).read_text(encoding="utf-8"))
        except Exception:
            return ""
        if not isinstance(payload, dict):
            return ""
        project = payload.get("project")
        if not isinstance(project, dict):
            return ""
        return str(project.get("version") or "").strip()

    def _read_cargo_version(self) -> str:
        cargo_path = os.path.join(self._repo_root, "Cargo.toml")
        if not os.path.isfile(cargo_path):
            return ""
        try:
            payload = tomllib.loads(Path(cargo_path).read_text(encoding="utf-8"))
        except Exception:
            return ""
        if not isinstance(payload, dict):
            return ""
        package_cfg = payload.get("package")
        if isinstance(package_cfg, dict):
            version = str(package_cfg.get("version") or "").strip()
            if version:
                return version
        workspace_cfg = payload.get("workspace")
        if isinstance(workspace_cfg, dict):
            workspace_pkg = workspace_cfg.get("package")
            if isinstance(workspace_pkg, dict):
                return str(workspace_pkg.get("version") or "").strip()
        return ""

    def _on_release_toggle(self, checked: bool) -> None:
        enabled = bool(checked)
        self.release_form.setVisible(enabled)
        self.release_form.setEnabled(enabled and not bool(self._pending))
        self._refresh_commit_enabled()

    def _on_release_version_changed(self, text: str) -> None:
        version = str(text or "").strip()
        if not self._release_build_user_modified:
            self._set_release_build_value(self._next_build_for_version(version) if version else 0)
        self._sync_release_identity_fields()
        self._refresh_commit_enabled()

    def _on_release_build_changed(self, _value: int) -> None:
        if not self._updating_release_build_value:
            self._release_build_user_modified = True
        self._sync_release_identity_fields()
        self._refresh_commit_enabled()

    def _on_commit_new_branch_toggled(self, checked: bool) -> None:
        self.new_branch_name_row.setVisible(bool(checked))
        if checked and not self._pending:
            self.new_branch_name_edit.setFocus()
            self.new_branch_name_edit.selectAll()
        self._refresh_branch_target_state()
        self._refresh_commit_enabled()

    def _on_new_branch_name_changed(self, _text: str) -> None:
        self._refresh_branch_target_state()
        self._refresh_commit_enabled()

    def _refresh_branch_target_state(self) -> None:
        current = str(self._current_branch_name or "").strip() or "HEAD (detached)"
        self.branch_label.setText(f"Current branch: {current}")
        self.current_branch_detail_label.setText(f"Current branch: {current}")

        if bool(self.commit_new_branch_chk.isChecked()):
            target = str(self.new_branch_name_edit.text() or "").strip()
            if target:
                self.target_branch_label.setText(f"Commit target: {target} (new branch)")
            else:
                self.target_branch_label.setText("Commit target: (new branch name required)")
        else:
            self.target_branch_label.setText(f"Commit target: {current}")

    @staticmethod
    def _validate_branch_name(branch_name: str) -> str | None:
        name = str(branch_name or "").strip()
        if not name:
            return "New branch name is required."
        if name == "@" or name.upper() == "HEAD":
            return "Invalid branch name."
        if name.startswith("-"):
            return "Branch name cannot start with '-'."
        if (
            name.startswith("/")
            or name.endswith("/")
            or name.startswith(".")
            or name.endswith(".")
            or name.endswith(".lock")
            or ".." in name
            or "//" in name
            or "@{" in name
        ):
            return "Invalid branch name."
        if any(ch.isspace() for ch in name):
            return "Branch name cannot contain whitespace."
        if any(ch in _BRANCH_FORBIDDEN_CHARS for ch in name):
            return "Branch name contains invalid characters."
        for part in name.split("/"):
            if not part or part in {".", ".."} or part.endswith(".lock"):
                return "Invalid branch name."
        return None

    def _local_branch_exists(self, branch_name: str) -> bool:
        target = str(branch_name or "").strip().lower()
        if not target:
            return False
        try:
            info = self._git_service.list_branches(self._repo_root, include_remote=False)
        except Exception:
            return False
        branches = getattr(info, "branches", [])
        if not isinstance(branches, list):
            return False
        return any(str(item or "").strip().lower() == target for item in branches)

    def _commit_clicked(self, *, push_after: bool) -> None:
        message = str(self.message_edit.toPlainText() or "").strip()
        if not message:
            self._set_status("Commit message is required.", error=True)
            return

        selected = self._selected_rel_paths()
        if not selected:
            self._set_status("Select at least one file.", error=True)
            return

        create_new_branch = bool(self.commit_new_branch_chk.isChecked())
        new_branch_name = str(self.new_branch_name_edit.text() or "").strip()
        if create_new_branch:
            branch_error = self._validate_branch_name(new_branch_name)
            if branch_error:
                self._set_status(branch_error, error=True)
                return
            if self._local_branch_exists(new_branch_name):
                self._set_status("Branch already exists.", error=True)
                return

        create_release = bool(self.release_chk.isChecked())
        push_required = bool(push_after or create_release)
        release_req: GitHubReleaseRequest | None = None
        pyproject_should_bump = False
        cargo_should_bump = False
        if create_release:
            if self._release_service is None:
                self._set_status("Release publishing is not available in this build.", error=True)
                return
            version = str(self.release_version_edit.text() or "").strip()
            build_number = int(self.release_build_spin.value())
            tag_name = str(self.release_tag_edit.text() or "").strip()
            title = str(self.release_title_edit.text() or "").strip()
            notes = str(self.release_notes_edit.toPlainText() or "").strip()
            prerelease = bool(self.release_prerelease_chk.isChecked())
            pyproject_should_bump = bool(self.bump_pyproject_chk is not None and self.bump_pyproject_chk.isChecked())
            cargo_should_bump = bool(self.bump_cargo_chk is not None and self.bump_cargo_chk.isChecked())

            if not version:
                self._set_status("Release version is required.", error=True)
                return
            if not tag_name:
                self._set_status("Release tag is required.", error=True)
                return
            if pyproject_should_bump and "pyproject.toml" not in selected:
                selected = [*selected, "pyproject.toml"]
            if cargo_should_bump and "Cargo.toml" not in selected:
                selected = [*selected, "Cargo.toml"]

            release_req = GitHubReleaseRequest(
                repo_root=self._repo_root,
                version=version,
                tag_name=tag_name,
                title=title or tag_name,
                notes=notes,
                build_number=build_number,
                prerelease=prerelease,
            )

        to_add = [p for p in selected if self._file_states.get(p) == "untracked"]

        self._set_busy(True)
        self._commit_with_push = push_required
        self.release_error = ""
        self.release_url = ""
        self.push_error_kind = ""
        if create_release:
            self._set_status("Committing, pushing, and publishing release...")
        elif create_new_branch:
            if push_required:
                self._set_status(f"Creating branch '{new_branch_name}', committing, and pushing...")
            else:
                self._set_status(f"Creating branch '{new_branch_name}' and committing changes...")
        else:
            self._set_status("Committing and pushing..." if push_required else "Committing changes...")

        def _run():
            release_url = ""
            release_error = ""
            push_error_kind = ""

            if create_new_branch:
                self._git_service.create_branch(self._repo_root, new_branch_name, checkout=True)

            # Add selected untracked files first so they become tracked in this commit.
            if to_add:
                add_fn = getattr(self._git_service, "add_files", None)
                if callable(add_fn):
                    add_fn(self._repo_root, to_add)
                else:
                    stage_fn = getattr(self._git_service, "stage_paths", None)
                    if callable(stage_fn):
                        stage_fn(self._repo_root, to_add)

            if create_release and pyproject_should_bump and self._release_service is not None and release_req is not None:
                self._release_service.update_pyproject_version(self._repo_root, release_req.version)
            if create_release and cargo_should_bump and self._release_service is not None and release_req is not None:
                self._release_service.update_cargo_version(self._repo_root, release_req.version)

            commit_output = self._git_service.commit_files(self._repo_root, selected, message)

            push_output = ""
            push_error = ""
            if push_required:
                try:
                    if create_new_branch:
                        push_output = self._git_service.push_head_to_origin(
                            self._repo_root,
                            set_upstream=True,
                        )
                    else:
                        push_output = self._git_service.push_current_branch(self._repo_root)
                except GitServiceError as exc:
                    push_error = str(exc)
                    push_error_kind = str(getattr(exc, "kind", "") or "")

            if create_release and not push_error and self._release_service is not None and release_req is not None:
                try:
                    published = self._release_service.create_release(release_req)
                    release_url = str(published.html_url or "").strip()
                except GitHubReleaseError as exc:
                    release_error = str(exc)

            return {
                "commit_output": str(commit_output or "").strip(),
                "push_output": str(push_output or "").strip(),
                "push_error": str(push_error or "").strip(),
                "push_error_kind": str(push_error_kind or "").strip(),
                "release_url": release_url,
                "release_error": release_error,
                "target_branch": new_branch_name if create_new_branch else str(self._current_branch_name or "").strip(),
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
            status_result = result
            repo_state = None
            if isinstance(result, tuple) and result:
                status_result = result[0]
                if len(result) > 1 and isinstance(result[1], GitRepoDisplayState):
                    repo_state = result[1]
            if error is None and hasattr(status_result, "file_states"):
                branch = str(getattr(status_result, "current_branch", "") or "")
                self._current_branch_name = branch
                self._apply_repo_state(repo_state, branch_fallback=branch)
                self._refresh_branch_target_state()

                self._file_states = self._extract_file_states(status_result)
                self._populate_files_tree()
                tracked_count = sum(1 for v in self._file_states.values() if v == "tracked")
                untracked_count = sum(1 for v in self._file_states.values() if v == "untracked")

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
                self.push_error_kind = str(payload.get("push_error_kind") or "").strip()
                self.release_url = str(payload.get("release_url") or "").strip()
                self.release_error = str(payload.get("release_error") or "").strip()
                target_branch = str(payload.get("target_branch") or "").strip()
                if target_branch:
                    self._current_branch_name = target_branch
                    self._refresh_branch_target_state()

                if self.push_error:
                    self._set_status("Commit succeeded locally, but push failed.", error=True)
                    push_title = "Commit and Push"
                    if self.push_error_kind == "auth_failed":
                        push_body = f"Commit succeeded locally, but push authentication failed.\n\n{self.push_error}"
                    else:
                        push_body = f"Commit succeeded locally, but push failed.\n\n{self.push_error}"
                    QMessageBox.warning(
                        self,
                        push_title,
                        push_body,
                    )
                elif self.release_error:
                    self._set_status("Commit/push succeeded, but release publishing failed.", error=True)
                    QMessageBox.warning(
                        self,
                        "Release Publishing",
                        f"Commit/push succeeded, but release publishing failed.\n\n{self.release_error}",
                    )
                elif self.release_url:
                    self._set_status("Commit, push, and release completed.")
                else:
                    self._set_status("Commit and push completed." if self._commit_with_push else "Commit completed.")

                self.accept()
                return

            if isinstance(error, GitServiceError):
                self._set_status(str(error), error=True)
                return
            if isinstance(error, GitHubReleaseError):
                self._set_status(str(error), error=True)
                return

            self._set_status("Commit failed.", error=True)

    def _apply_repo_state(self, state: GitRepoDisplayState | None, *, branch_fallback: str = "") -> None:
        branch_text = format_git_branch_label(state) or str(branch_fallback or "").strip() or "HEAD (detached)"
        remote_text = format_git_remote_label(state)
        detail = f"Git state: {branch_text}"
        if remote_text:
            detail = f"{detail} | {remote_text}"
        self.repo_state_label.setText(detail)

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
            if callable(self._exclude_path_predicate):
                try:
                    if bool(self._exclude_path_predicate(cpath, self._repo_root)):
                        continue
                except TypeError:
                    try:
                        if bool(self._exclude_path_predicate(cpath)):
                            continue
                    except Exception:
                        pass
                except Exception:
                    pass
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
        self.files_tree.blockSignals(True)
        self.files_tree.clear()

        style = QApplication.style()
        folder_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon) if style is not None else None
        fallback_file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon) if style is not None else None

        tracked_count = sum(1 for kind in self._file_states.values() if kind == "tracked")
        untracked_count = sum(1 for kind in self._file_states.values() if kind == "untracked")

        tracked_root = QTreeWidgetItem([f"Changed ({tracked_count})"])
        tracked_root.setData(0, Qt.UserRole, "")
        tracked_root.setFlags(tracked_root.flags() | Qt.ItemIsUserCheckable)
        tracked_root.setCheckState(0, Qt.Unchecked)
        if folder_icon is not None:
            tracked_root.setIcon(0, folder_icon)
        self.files_tree.addTopLevelItem(tracked_root)

        untracked_root = QTreeWidgetItem([f"Unversioned Files ({untracked_count})"])
        untracked_root.setData(0, Qt.UserRole, "")
        untracked_root.setFlags(untracked_root.flags() | Qt.ItemIsUserCheckable)
        untracked_root.setCheckState(0, Qt.Unchecked)
        if folder_icon is not None:
            untracked_root.setIcon(0, folder_icon)
        self.files_tree.addTopLevelItem(untracked_root)

        self._populate_tree_for_kind(
            root_item=tracked_root,
            kind="tracked",
            folder_icon=folder_icon,
            fallback_file_icon=fallback_file_icon,
        )
        self._populate_tree_for_kind(
            root_item=untracked_root,
            kind="untracked",
            folder_icon=folder_icon,
            fallback_file_icon=fallback_file_icon,
        )

        self._refresh_directory_states(self.files_tree)

        self.files_tree.expandAll()
        self.files_tree.blockSignals(False)
        self._is_syncing_tree_checks = False
        self._refresh_commit_enabled()

    def _populate_tree_for_kind(
        self,
        *,
        root_item: QTreeWidgetItem,
        kind: str,
        folder_icon,
        fallback_file_icon,
    ) -> None:
        dir_nodes: dict[str, QTreeWidgetItem] = {}

        for rel_path, entry_kind in self._file_states.items():
            if entry_kind != kind:
                continue
            parent = root_item
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
        self._sync_tree_file_item_checks(self.files_tree)
        self._refresh_directory_states(self.files_tree)

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

        has_message = bool(str(self.message_edit.toPlainText() or "").strip())
        has_files = bool(self._selected_rel_paths())
        release_valid = True
        if self.release_chk.isChecked():
            release_valid = bool(
                str(self.release_version_edit.text() or "").strip()
                and str(self.release_tag_edit.text() or "").strip()
            )
        branch_valid = True
        if self.commit_new_branch_chk.isChecked():
            branch_valid = bool(str(self.new_branch_name_edit.text() or "").strip())
        enabled = bool(has_message and has_files and release_valid and branch_valid)
        self.commit_btn.setEnabled(enabled)
        self.commit_push_btn.setEnabled(enabled)

    def _set_busy(self, busy: bool) -> None:
        disabled = bool(busy)
        if self.repo_combo is not None:
            self.repo_combo.setDisabled(disabled)
        self.refresh_btn.setDisabled(disabled)
        self.files_tree.setDisabled(disabled)
        self.message_edit.setDisabled(disabled)
        self.release_chk.setDisabled(disabled)
        self.release_form.setDisabled(disabled or not self.release_chk.isChecked())
        self.commit_new_branch_chk.setDisabled(disabled)
        self.new_branch_name_edit.setDisabled(disabled or not self.commit_new_branch_chk.isChecked())
        self.cancel_btn.setDisabled(disabled)
        self._refresh_branch_target_state()

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
