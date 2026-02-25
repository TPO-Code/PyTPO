"""Controller for Git/GitHub workflow dialogs and command orchestration."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import QDialog, QMessageBox

from src.git.github_auth import GitHubAuthStore
from src.git.github_release_service import GitHubReleaseService
from src.ui.dialogs.git_branches_dialog import GitBranchesDialog
from src.ui.dialogs.git_commit_dialog import GitCommitDialog
from src.ui.dialogs.git_releases_dialog import GitReleasesDialog
from src.ui.dialogs.share_to_github_dialog import ShareToGitHubDialog


class GitWorkflowController:
    def __init__(self, ide):
        self.ide = ide

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    def open_clone_repository_dialog(self) -> None:
        auth = GitHubAuthStore(self.ide_app_dir)
        if not auth.has_token():
            answer = QMessageBox.question(
                self.ide,
                "GitHub Token Required",
                "No GitHub token is configured.\n\nOpen Settings > GitHub now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self.open_settings(initial_page_id="ide-github")
            return

        from src.ui.dialogs.clone_repo_dialog import CloneRepositoryDialog

        dialog = CloneRepositoryDialog(
            manager=self.settings_manager,
            default_destination=self.project_root,
            use_native_chrome=self.use_native_chrome,
            parent=self.ide,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        cloned_path = str(dialog.cloned_path or "").strip()
        if not cloned_path:
            return

        self.ide.statusBar().showMessage(f"Repository cloned: {cloned_path}", 2200)
        if dialog.open_after_clone:
            self.open_project_path(cloned_path)

    def open_share_to_github_dialog(self) -> None:
        token = str(self.ide._github_auth_store.get() or "").strip()
        if not token:
            answer = QMessageBox.question(
                self.ide,
                "GitHub Token Required",
                "No GitHub token is configured.\n\nOpen Settings > GitHub now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self.open_settings(initial_page_id="ide-github")
            return

        indexing = self._indexing_config()
        exclude_dirs = indexing.get("exclude_dirs", []) if isinstance(indexing, dict) else []
        exclude_files = indexing.get("exclude_files", []) if isinstance(indexing, dict) else []
        dialog = ShareToGitHubDialog(
            project_root=self.project_root,
            token=token,
            share_service=self.github_share_service,
            exclude_dirs=exclude_dirs if isinstance(exclude_dirs, list) else [],
            exclude_files=exclude_files if isinstance(exclude_files, list) else [],
            exclude_path_predicate=self._is_path_filtered_in_workspace,
            use_native_chrome=self.use_native_chrome,
            parent=self.ide,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        result = dialog.result_data
        if result is None:
            return
        self.ide._git_repo_root = str(result.repo_root or "").strip() or self.ide._git_repo_root
        self.schedule_git_status_refresh(delay_ms=0, force=True)
        msg = f"Shared to GitHub: {result.repo_full_name}"
        if result.html_url:
            msg = f"{msg} ({result.html_url})"
        self.ide.statusBar().showMessage(msg, 4200)

    def open_git_commit_dialog(self, *, prefer_push_action: bool = False) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return
        release_service = GitHubReleaseService(
            git_service=self.git_service,
            github_token_provider=lambda: self.ide._github_auth_store.get(),
            canonicalize=self._canonical_path,
        )
        dialog = GitCommitDialog(
            git_service=self.git_service,
            repo_root=repo_root,
            release_service=release_service,
            exclude_untracked_predicate=self._is_path_filtered_in_workspace,
            prefer_push_action=prefer_push_action,
            use_native_chrome=self.use_native_chrome,
            parent=self.ide,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.push_error:
            self.ide.statusBar().showMessage("Commit succeeded locally, but push authentication failed.", 3200)
        elif dialog.release_error:
            self.ide.statusBar().showMessage("Commit/push succeeded, but release publishing failed.", 3600)
        elif dialog.release_url:
            self.ide.statusBar().showMessage(f"Release published: {dialog.release_url}", 4200)
        elif dialog.push_output:
            self.ide.statusBar().showMessage("Commit and push completed.", 2200)
        else:
            self.ide.statusBar().showMessage("Commit completed.", 1800)
        self.schedule_git_status_refresh(delay_ms=80, force=True)

    def open_git_releases_dialog(self) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return
        token = str(self.ide._github_auth_store.get() or "").strip()
        if not token:
            answer = QMessageBox.question(
                self.ide,
                "GitHub Token Required",
                "No GitHub token is configured.\n\nOpen Settings > GitHub now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self.open_settings(initial_page_id="ide-github")
            return

        release_service = GitHubReleaseService(
            git_service=self.git_service,
            github_token_provider=lambda: self.ide._github_auth_store.get(),
            canonicalize=self._canonical_path,
        )
        dialog = GitReleasesDialog(
            release_service=release_service,
            repo_root=repo_root,
            use_native_chrome=self.use_native_chrome,
            parent=self.ide,
        )
        dialog.exec()

    def _is_path_filtered_in_workspace(self, abs_path: str) -> bool:
        cpath = self._canonical_path(abs_path)
        project_root = self._canonical_path(self.project_root)
        try:
            if os.path.commonpath([project_root, cpath]) != project_root:
                return False
        except Exception:
            return False

        try:
            if self.is_path_excluded(cpath, for_feature="indexing"):
                return True
        except Exception:
            pass

        try:
            if self._is_tree_path_excluded(cpath, False):
                return True
        except Exception:
            pass

        parent = self._canonical_path(os.path.dirname(cpath))
        while parent and parent != cpath:
            try:
                if os.path.commonpath([project_root, parent]) != project_root:
                    break
            except Exception:
                break
            if parent == project_root:
                break
            try:
                if self._is_tree_path_excluded(parent, True):
                    return True
            except Exception:
                pass
            next_parent = self._canonical_path(os.path.dirname(parent))
            if next_parent == parent:
                break
            parent = next_parent

        return False

    def push_current_branch(self) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return

        self.ide.statusBar().showMessage("Pushing current branch...", 1400)

        def _run():
            return self.git_service.push_current_branch(repo_root)

        self._submit_git_task("push", _run)

    def fetch_remote(self) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return

        self.ide.statusBar().showMessage("Fetching remote updates...", 1400)

        def _run():
            return self.git_service.fetch(repo_root, prune=True)

        self._submit_git_task("fetch", _run)

    def pull_current_branch(self) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return

        self.ide.statusBar().showMessage("Pulling current branch...", 1400)

        def _run():
            return self.git_service.pull_current_branch(repo_root)

        self._submit_git_task("pull", _run)

    def run_push_preflight_check(self) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return

        self.ide.statusBar().showMessage("Running Git preflight check...", 1400)

        def _run():
            return self.git_service.preflight_push_check(repo_root)

        self._submit_git_task("preflight_check", _run)

    def open_git_branches_dialog(self) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return
        dialog = GitBranchesDialog(
            git_service=self.git_service,
            repo_root=repo_root,
            use_native_chrome=self.use_native_chrome,
            parent=self.ide,
        )
        dialog.exec()
        self.schedule_git_status_refresh(delay_ms=80, force=True)

    def rollback_file_changes(self, file_path: str) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return
        cpath = self._canonical_path(file_path)
        answer = QMessageBox.question(
            self.ide,
            "Rollback File",
            f"Discard local changes for this file?\n\n{cpath}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        def _run():
            self.git_service.rollback_file(repo_root, cpath)

        self._submit_git_task("rollback_file", _run, context=cpath)

    def rollback_discard_unstaged(self) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return
        answer = QMessageBox.warning(
            self.ide,
            "Discard Unstaged Changes",
            "Discard all unstaged changes in the repository?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        def _run():
            self.git_service.discard_unstaged_changes(repo_root)

        self._submit_git_task("rollback_repo", _run)

    def rollback_unstage_all(self) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return
        answer = QMessageBox.warning(
            self.ide,
            "Unstage All",
            "Unstage all staged changes in the repository?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        def _run():
            self.git_service.unstage_all(repo_root)

        self._submit_git_task("rollback_repo", _run)

    def rollback_hard_reset_head(self) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return
        answer = QMessageBox.critical(
            self.ide,
            "Hard Reset to HEAD",
            "Hard reset repository to HEAD?\n\nThis discards local changes permanently.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        def _run():
            self.git_service.hard_reset_head(repo_root)

        self._submit_git_task("rollback_repo", _run)

    def track_paths_in_git(self, paths: list[str]) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return
        rel_paths = self._to_repo_rel_paths(repo_root, paths)
        if not rel_paths:
            QMessageBox.information(self.ide, "Git Track", "No eligible file under repository root.")
            return

        def _run():
            self.git_service.stage_paths(repo_root, rel_paths)

        self._submit_git_task("track_paths", _run, context={"count": len(rel_paths)})

    def stage_paths_in_git(self, paths: list[str], label: str = "path") -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return
        rel_paths = self._to_repo_rel_paths(repo_root, paths)
        if not rel_paths:
            QMessageBox.information(self.ide, "Git Stage", "No eligible paths under repository root.")
            return

        def _run():
            self.git_service.stage_paths(repo_root, rel_paths)

        self._submit_git_task("stage_paths", _run, context={"count": len(rel_paths), "label": label})

    def unstage_paths_in_git(self, paths: list[str], label: str = "path") -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return
        rel_paths = self._to_repo_rel_paths(repo_root, paths)
        if not rel_paths:
            QMessageBox.information(self.ide, "Git Unstage", "No eligible paths under repository root.")
            return

        def _run():
            self.git_service.unstage_paths(repo_root, rel_paths)

        self._submit_git_task("unstage_paths", _run, context={"count": len(rel_paths), "label": label})

    def stage_all_changes(self) -> None:
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return

        def _run():
            self.git_service.stage_all_changes(repo_root)

        self._submit_git_task("stage_paths", _run, context={"count": -1, "label": "all"})

    def _to_repo_rel_paths(self, repo_root: str, paths: list[str]) -> list[str]:
        root = self._canonical_path(repo_root)
        rels: list[str] = []
        seen: set[str] = set()
        for path in paths:
            cpath = self._canonical_path(path)
            if not self._path_has_prefix(cpath, root):
                continue
            if cpath == root:
                rel = "."
            else:
                rel = str(Path(cpath).relative_to(Path(root))).replace("\\", "/")
            key = rel.lower()
            if key in seen:
                continue
            seen.add(key)
            rels.append(rel)
        return rels
