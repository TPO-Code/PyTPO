"""Controller for Git/GitHub workflow dialogs and command orchestration."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from pytpo.git.github_auth import GitHubAuthStore
from pytpo.git.github_release_service import GitHubReleaseService
from pytpo.ui.dialogs.git_branches_dialog import GitBranchesDialog
from pytpo.ui.dialogs.git_commit_dialog import GitCommitDialog
from pytpo.ui.dialogs.git_releases_dialog import GitReleasesDialog
from pytpo.ui.dialogs.share_to_github_dialog import ShareToGitHubDialog


class GitWorkflowController:
    def __init__(self, ide):
        self.ide = ide

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    def _block_write_action(self, action_label: str) -> bool:
        blocker = getattr(self.ide, "_block_if_project_read_only", None)
        if callable(blocker):
            try:
                return bool(blocker(action_label))
            except Exception:
                return False
        return False

    def _workspace_repository_index(self):
        return getattr(self.ide, "workspace_repository_index", None)

    def _sync_repo_root(self, repo_root: str | None) -> str | None:
        root = str(repo_root or "").strip() or None
        self.ide._git_repo_root = root
        controller = getattr(self.ide, "version_control_controller", None)
        if controller is not None:
            try:
                controller._git_repo_root = root
                controller._sync_ide_state()
            except Exception:
                pass
        return root

    def _repo_label(self, repo_root: str) -> str:
        root = self._canonical_path(repo_root)
        project_root = self._canonical_path(self.project_root)
        if root == project_root:
            return f"{os.path.basename(project_root) or project_root} (project root)"
        try:
            rel = os.path.relpath(root, project_root)
        except Exception:
            rel = root
        return rel if rel not in (".", "") else (os.path.basename(root) or root)

    def _repo_options(self) -> list[tuple[str, str]]:
        repo_index = self._workspace_repository_index()
        if repo_index is None:
            return []
        try:
            repo_roots = list(repo_index.repo_roots())
        except Exception:
            return []
        return [(self._repo_label(root), self._canonical_path(root)) for root in repo_roots]

    def _choose_repo_root(self, repo_roots: list[str], *, title: str, label: str) -> str | None:
        ordered_roots = sorted(
            {self._canonical_path(root) for root in repo_roots if isinstance(root, str) and root.strip()},
            key=lambda value: (value != self._canonical_path(self.project_root), value.lower()),
        )
        if not ordered_roots:
            return None
        if len(ordered_roots) == 1:
            return ordered_roots[0]
        labels = [self._repo_label(root) for root in ordered_roots]
        chosen_label, accepted = QInputDialog.getItem(self.ide, title, label, labels, 0, False)
        if not accepted:
            return None
        try:
            idx = labels.index(str(chosen_label))
        except ValueError:
            return None
        return ordered_roots[idx]

    def _resolve_repo_root_from_selection(self) -> str | None:
        refresh_repo_index = getattr(self.ide, "refresh_workspace_repository_index", None)
        if callable(refresh_repo_index):
            try:
                refresh_repo_index(update_tree=True)
            except Exception:
                pass

        explorer = getattr(self.ide, "explorer_controller", None)
        context = None
        if explorer is not None:
            resolver = getattr(explorer, "current_selection_context", None)
            if callable(resolver):
                try:
                    context = resolver()
                except Exception:
                    context = None

        if context is not None and context.repo_root:
            return self._sync_repo_root(context.repo_root)

        repo_index = self._workspace_repository_index()
        repo_roots: list[str] = []
        if repo_index is not None:
            try:
                repo_roots = list(repo_index.repo_roots())
            except Exception:
                repo_roots = []
        if not repo_roots:
            return None

        chosen = self._choose_repo_root(
            repo_roots,
            title="Choose Repository",
            label="This action needs a repository target:",
        )
        return self._sync_repo_root(chosen)

    def _ensure_git_repo(self) -> str | None:
        repo_root = self._resolve_repo_root_from_selection()
        if repo_root:
            return repo_root

        controller = getattr(self.ide, "version_control_controller", None)
        if controller is not None:
            try:
                resolved = controller._ensure_git_repo()
            except Exception:
                resolved = None
            if resolved:
                return self._sync_repo_root(resolved)

        QMessageBox.information(self.ide, "Git", "Current selection does not resolve to a Git repository.")
        return None

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

        from pytpo.ui.dialogs.clone_repo_dialog import CloneRepositoryDialog

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
        if self._block_write_action("Share to GitHub"):
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

        indexing = self._indexing_config()
        exclude_dirs = indexing.get("exclude_dirs", []) if isinstance(indexing, dict) else []
        exclude_files = indexing.get("exclude_files", []) if isinstance(indexing, dict) else []
        selection_target = self.project_root
        explorer = getattr(self.ide, "explorer_controller", None)
        if explorer is not None:
            resolver = getattr(explorer, "current_selection_context", None)
            if callable(resolver):
                try:
                    context = resolver()
                except Exception:
                    context = None
                else:
                    if context is not None:
                        selection_target = context.repo_root or context.selected_path or self.project_root
        dialog = ShareToGitHubDialog(
            project_root=selection_target,
            token=token,
            share_service=self.github_share_service,
            git_service=self.git_service,
            repo_options=self._repo_options(),
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
        if self._block_write_action("Commit"):
            return
        repo_root = self._ensure_git_repo()
        if not repo_root:
            return
        initial_commit_message = ""
        initial_release_message = ""
        reader = getattr(self.ide, "read_commit_md_messages", None)
        if callable(reader):
            try:
                loaded_commit, loaded_release = reader(repo_root=repo_root, scope_kind="repo")
                initial_commit_message = str(loaded_commit or "")
                initial_release_message = str(loaded_release or "")
            except Exception:
                initial_commit_message = ""
                initial_release_message = ""
        release_service = GitHubReleaseService(
            git_service=self.git_service,
            github_token_provider=lambda: self.ide._github_auth_store.get(),
            canonicalize=self._canonical_path,
        )
        dialog = GitCommitDialog(
            git_service=self.git_service,
            repo_root=repo_root,
            release_service=release_service,
            repo_options=self._repo_options(),
            exclude_path_predicate=lambda path, active_repo_root: self._is_path_excluded_for_repo(active_repo_root, path),
            exclude_untracked_predicate=self._is_path_filtered_in_workspace,
            prefer_push_action=prefer_push_action,
            initial_commit_message=initial_commit_message,
            initial_release_message=initial_release_message,
            use_native_chrome=self.use_native_chrome,
            parent=self.ide,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        writer = getattr(self.ide, "update_commit_md_messages", None)
        if callable(writer):
            try:
                writer(
                    commit_message=dialog.commit_message_text(),
                    release_message=dialog.release_message_text(),
                    repo_root=dialog.selected_repo_root(),
                    scope_kind="repo",
                )
            except Exception:
                pass
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
            git_service=self.git_service,
            repo_root=repo_root,
            repo_options=self._repo_options(),
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

    def _is_path_owned_by_repo(self, repo_root: str, abs_path: str) -> bool:
        repo_index = self._workspace_repository_index()
        root = self._canonical_path(repo_root)
        cpath = self._canonical_path(abs_path)
        if repo_index is not None:
            try:
                return bool(repo_index.path_is_owned_by_repo(cpath, root))
            except Exception:
                return False
        return self._path_has_prefix(cpath, root)

    def _is_path_excluded_for_repo(self, repo_root: str, abs_path: str) -> bool:
        if self._is_path_filtered_in_workspace(abs_path):
            return True
        return not self._is_path_owned_by_repo(repo_root, abs_path)

    def _repo_has_child_repositories(self, repo_root: str) -> bool:
        repo_index = self._workspace_repository_index()
        if repo_index is None:
            return False
        try:
            return bool(repo_index.has_child_repositories(repo_root))
        except Exception:
            return False

    def _owned_change_rel_paths(
        self,
        repo_root: str,
        *,
        include_untracked: bool,
        require_staged: bool | None = None,
        require_unstaged: bool | None = None,
    ) -> list[str]:
        try:
            status = self.git_service.read_status(repo_root)
        except Exception:
            return []

        rel_paths: list[str] = []
        seen: set[str] = set()
        root = self._canonical_path(repo_root)
        for entry in getattr(status, "changes", []):
            rel_path = str(getattr(entry, "rel_path", "") or "").strip().replace("\\", "/")
            if not rel_path or rel_path in {".", ""}:
                continue
            if not include_untracked and str(getattr(entry, "state", "") or "").strip().lower() == "untracked":
                continue
            if require_staged is not None and bool(getattr(entry, "staged", False)) != require_staged:
                continue
            if require_unstaged is not None and bool(getattr(entry, "unstaged", False)) != require_unstaged:
                continue
            abs_path = self._canonical_path(os.path.join(root, rel_path))
            if not self._is_path_owned_by_repo(root, abs_path):
                continue
            key = rel_path.lower()
            if key in seen:
                continue
            seen.add(key)
            rel_paths.append(rel_path)
        return rel_paths

    def push_current_branch(self) -> None:
        if self._block_write_action("Push"):
            return
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
            repo_options=self._repo_options(),
            use_native_chrome=self.use_native_chrome,
            parent=self.ide,
        )
        dialog.exec()
        self.schedule_git_status_refresh(delay_ms=80, force=True)

    def rollback_file_changes(self, file_path: str) -> None:
        if self._block_write_action("Rollback"):
            return
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
        if self._block_write_action("Reset"):
            return
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

        if self._repo_has_child_repositories(repo_root):
            rel_paths = self._owned_change_rel_paths(
                repo_root,
                include_untracked=False,
                require_unstaged=True,
            )
            if not rel_paths:
                QMessageBox.information(self.ide, "Discard Unstaged Changes", "No unstaged tracked files found.")
                return

            def _run():
                self.git_service.restore_paths(repo_root, rel_paths, staged=False, worktree=True)

            self._submit_git_task("rollback_repo", _run)
            return

        def _run():
            self.git_service.discard_unstaged_changes(repo_root)

        self._submit_git_task("rollback_repo", _run)

    def rollback_unstage_all(self) -> None:
        if self._block_write_action("Reset"):
            return
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

        if self._repo_has_child_repositories(repo_root):
            rel_paths = self._owned_change_rel_paths(
                repo_root,
                include_untracked=True,
                require_staged=True,
            )
            if not rel_paths:
                QMessageBox.information(self.ide, "Unstage All", "No staged changes found for this repository.")
                return

            def _run():
                self.git_service.unstage_paths(repo_root, rel_paths)

            self._submit_git_task("rollback_repo", _run)
            return

        def _run():
            self.git_service.unstage_all(repo_root)

        self._submit_git_task("rollback_repo", _run)

    def rollback_hard_reset_head(self) -> None:
        if self._block_write_action("Reset"):
            return
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

        if self._repo_has_child_repositories(repo_root):
            rel_paths = self._owned_change_rel_paths(
                repo_root,
                include_untracked=False,
                require_staged=None,
                require_unstaged=None,
            )
            if not rel_paths:
                QMessageBox.information(self.ide, "Hard Reset to HEAD", "No tracked changes found for this repository.")
                return

            def _run():
                self.git_service.restore_paths(repo_root, rel_paths, staged=True, worktree=True)

            self._submit_git_task("rollback_repo", _run)
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

        if self._repo_has_child_repositories(repo_root):
            rel_paths = self._owned_change_rel_paths(
                repo_root,
                include_untracked=True,
                require_staged=None,
                require_unstaged=None,
            )
            if not rel_paths:
                QMessageBox.information(self.ide, "Git Stage", "No eligible changes found for this repository.")
                return

            def _run():
                self.git_service.stage_paths(repo_root, rel_paths)

            self._submit_git_task("stage_paths", _run, context={"count": len(rel_paths), "label": "all"})
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
            if not self._is_path_owned_by_repo(root, cpath):
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
