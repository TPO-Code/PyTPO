"""Qt-aware controller for Git polling, debouncing, and background tasks."""

from __future__ import annotations

import concurrent.futures
import os

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from src.git.git_service import GitPreflightReport, GitRepoStatus, GitServiceError


class VersionControlController(QObject):
    statusChanged = Signal(dict, dict, str)

    def __init__(self, ide, git_service, tree, parent=None):
        super().__init__(parent or ide)
        self.ide = ide
        self.git_service = git_service
        self.tree = tree

        self._git_repo_root: str | None = None
        self._git_current_branch: str = ""
        self._git_file_states: dict[str, str] = {}
        self._git_folder_states: dict[str, str] = {}
        self._git_refresh_inflight = False
        self._git_refresh_requested = False

        self._git_status_debounce_timer = QTimer(self)
        self._git_status_debounce_timer.setSingleShot(True)
        self._git_status_debounce_timer.timeout.connect(self._request_git_status_refresh)

        self._git_poll_timer = QTimer(self)
        self._git_poll_timer.timeout.connect(self.schedule_git_status_refresh)

        self._git_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="pytpo-git")
        self._git_pending: dict[concurrent.futures.Future, tuple[str, object | None]] = {}

        self._git_result_pump = QTimer(self)
        self._git_result_pump.setInterval(40)
        self._git_result_pump.timeout.connect(self._drain_git_tasks)

        self._sync_ide_state()

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    @property
    def status_debounce_timer(self) -> QTimer:
        return self._git_status_debounce_timer

    @property
    def poll_timer(self) -> QTimer:
        return self._git_poll_timer

    @property
    def result_pump(self) -> QTimer:
        return self._git_result_pump

    def _sync_ide_state(self) -> None:
        self.ide._git_repo_root = self._git_repo_root
        self.ide._git_current_branch = self._git_current_branch
        self.ide._git_file_states = dict(self._git_file_states)
        self.ide._git_folder_states = dict(self._git_folder_states)
        self.ide._git_refresh_inflight = self._git_refresh_inflight
        self.ide._git_refresh_requested = self._git_refresh_requested
        self.ide._git_status_debounce_timer = self._git_status_debounce_timer
        self.ide._git_poll_timer = self._git_poll_timer
        self.ide._git_executor = self._git_executor
        self.ide._git_pending = self._git_pending
        self.ide._git_result_pump = self._git_result_pump

    def _git_config(self) -> dict:
        cfg = self.ide.config.get("git", {}) if isinstance(getattr(self.ide, "config", None), dict) else {}
        return cfg if isinstance(cfg, dict) else {}

    def _git_tinting_enabled(self) -> bool:
        return bool(self._git_config().get("enable_file_tinting", True))

    def _git_tint_colors(self) -> dict[str, str]:
        cfg = self._git_config()
        return {
            "clean": str(cfg.get("tracked_clean_color") or "#7fbf7f"),
            "dirty": str(cfg.get("tracked_dirty_color") or "#e69f6b"),
            "untracked": str(cfg.get("untracked_color") or "#c8c8c8"),
        }

    def _apply_git_tinting_config(self) -> None:
        try:
            self.tree.set_git_tinting(enabled=self._git_tinting_enabled(), colors=self._git_tint_colors())
            self.tree.set_git_status_maps(file_states=self._git_file_states, folder_states=self._git_folder_states)
        except Exception:
            return

    def _configure_git_poll_timer(self) -> None:
        self._git_poll_timer.setInterval(3500)
        if self._git_tinting_enabled():
            self._git_poll_timer.start()
        else:
            self._git_poll_timer.stop()

    def schedule_git_status_refresh(self, *, delay_ms: int = 320, force: bool = False) -> None:
        if not self._git_tinting_enabled() and not force:
            return
        wait = max(0, int(delay_ms))
        if wait == 0:
            self._request_git_status_refresh(force=force)
            return
        self._git_status_debounce_timer.start(wait)

    def _request_git_status_refresh(self, force: bool = False) -> None:
        if not force and not self._git_tinting_enabled():
            return
        if self._git_refresh_inflight:
            self._git_refresh_requested = True
            self._sync_ide_state()
            return
        self._git_refresh_inflight = True
        self._sync_ide_state()

        def _run() -> GitRepoStatus:
            return self.git_service.read_status(self.project_root)

        self._submit_git_task("status", _run)

    def _submit_git_task(self, kind: str, fn, context: object | None = None) -> None:
        try:
            future = self._git_executor.submit(fn)
        except Exception as exc:
            self.ide.statusBar().showMessage(f"Git task failed to start: {exc}", 2600)
            if kind == "status":
                self._git_refresh_inflight = False
                self._sync_ide_state()
            return
        self._git_pending[future] = (kind, context)
        if not self._git_result_pump.isActive():
            self._git_result_pump.start()

    def submit_task(self, kind: str, fn, context: object | None = None) -> None:
        self._submit_git_task(kind, fn, context=context)

    def _drain_git_tasks(self) -> None:
        if not self._git_pending:
            self._git_result_pump.stop()
            return

        done: list[concurrent.futures.Future] = []
        for future, payload in list(self._git_pending.items()):
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
            self._handle_git_task_result(kind, context, result, error)

        for future in done:
            self._git_pending.pop(future, None)

        if not self._git_pending:
            self._git_result_pump.stop()

    def _handle_git_task_result(self, kind: str, context: object | None, result: object, error: Exception | None) -> None:
        if kind == "status":
            self._git_refresh_inflight = False
            if error is None and isinstance(result, GitRepoStatus):
                self._git_repo_root = result.repo_root
                self._git_current_branch = str(result.current_branch or "")
                self._git_file_states = dict(result.file_states)
                self._git_folder_states = dict(result.folder_states)
                self._apply_git_tinting_config()
                self.statusChanged.emit(dict(self._git_file_states), dict(self._git_folder_states), self._git_current_branch)
            else:
                self._git_repo_root = None
                self._git_current_branch = ""
                self._git_file_states = {}
                self._git_folder_states = {}
                self._apply_git_tinting_config()
                self.statusChanged.emit({}, {}, "")
            self._sync_ide_state()
            if self._git_refresh_requested:
                self._git_refresh_requested = False
                self._sync_ide_state()
                self.schedule_git_status_refresh(delay_ms=140, force=True)
            return

        if kind == "push":
            if error is None:
                self.ide.statusBar().showMessage("Push completed.", 2200)
                self.schedule_git_status_refresh(delay_ms=80, force=True)
                return
            if isinstance(error, GitServiceError):
                QMessageBox.warning(self.ide, "Git Push", str(error))
            else:
                QMessageBox.warning(self.ide, "Git Push", "Push failed.")
            return

        if kind == "fetch":
            if error is None:
                self.ide.statusBar().showMessage("Fetch completed.", 2200)
                self.schedule_git_status_refresh(delay_ms=80, force=True)
                return
            if isinstance(error, GitServiceError):
                QMessageBox.warning(self.ide, "Git Fetch", str(error))
            else:
                QMessageBox.warning(self.ide, "Git Fetch", "Fetch failed.")
            return

        if kind == "pull":
            if error is None:
                self.ide.statusBar().showMessage("Pull completed.", 2200)
                self.ide.refresh_project_tree()
                self.schedule_git_status_refresh(delay_ms=80, force=True)
                return
            if isinstance(error, GitServiceError):
                QMessageBox.warning(self.ide, "Git Pull", str(error))
            else:
                QMessageBox.warning(self.ide, "Git Pull", "Pull failed.")
            return

        if kind == "preflight_check":
            if error is None and isinstance(result, GitPreflightReport):
                self._show_preflight_report(result)
                self.schedule_git_status_refresh(delay_ms=80, force=True)
                return
            if isinstance(error, GitServiceError):
                QMessageBox.warning(self.ide, "Git Preflight Check", str(error))
            else:
                QMessageBox.warning(self.ide, "Git Preflight Check", "Could not run preflight check.")
            return

        if kind == "track_paths":
            if error is None:
                ctx = context if isinstance(context, dict) else {}
                count = int(ctx.get("count", 0)) if isinstance(ctx.get("count"), int) else 0
                if count == 1:
                    self.ide.statusBar().showMessage("File is now tracked.", 2200)
                else:
                    self.ide.statusBar().showMessage(f"{count} files are now tracked.", 2200)
                self.schedule_git_status_refresh(delay_ms=80, force=True)
                return
            if isinstance(error, GitServiceError):
                QMessageBox.warning(self.ide, "Git Track", str(error))
            else:
                QMessageBox.warning(self.ide, "Git Track", "Could not track file.")
            return

        if kind == "stage_paths":
            if error is None:
                ctx = context if isinstance(context, dict) else {}
                count = int(ctx.get("count", 0)) if isinstance(ctx.get("count"), int) else 0
                label = str(ctx.get("label") or "path")
                if count < 0:
                    msg = "Staged all changes."
                elif count == 1:
                    msg = f"Staged 1 {label}."
                else:
                    msg = f"Staged {count} {label}s."
                self.ide.statusBar().showMessage(msg, 2200)
                self.schedule_git_status_refresh(delay_ms=80, force=True)
                return
            if isinstance(error, GitServiceError):
                QMessageBox.warning(self.ide, "Git Stage", str(error))
            else:
                QMessageBox.warning(self.ide, "Git Stage", "Stage failed.")
            return

        if kind == "unstage_paths":
            if error is None:
                ctx = context if isinstance(context, dict) else {}
                count = int(ctx.get("count", 0)) if isinstance(ctx.get("count"), int) else 0
                label = str(ctx.get("label") or "path")
                if count == 1:
                    msg = f"Unstaged 1 {label}."
                else:
                    msg = f"Unstaged {count} {label}s."
                self.ide.statusBar().showMessage(msg, 2200)
                self.schedule_git_status_refresh(delay_ms=80, force=True)
                return
            if isinstance(error, GitServiceError):
                QMessageBox.warning(self.ide, "Git Unstage", str(error))
            else:
                QMessageBox.warning(self.ide, "Git Unstage", "Unstage failed.")
            return

        if kind == "rollback_file":
            file_path = str(context or "")
            if error is None:
                self.ide.statusBar().showMessage(f"Rolled back {os.path.basename(file_path)}", 2000)
                self.ide.refresh_subtree(os.path.dirname(file_path))
                self.schedule_git_status_refresh(delay_ms=60, force=True)
                return
            if isinstance(error, GitServiceError):
                QMessageBox.warning(self.ide, "Rollback File", str(error))
            else:
                QMessageBox.warning(self.ide, "Rollback File", "Could not rollback file.")
            return

        if kind == "rollback_repo":
            if error is None:
                self.ide.statusBar().showMessage("Rollback completed.", 2200)
                self.ide.refresh_project_tree()
                self.schedule_git_status_refresh(delay_ms=60, force=True)
                return
            if isinstance(error, GitServiceError):
                QMessageBox.warning(self.ide, "Rollback", str(error))
            else:
                QMessageBox.warning(self.ide, "Rollback", "Rollback failed.")

    @staticmethod
    def _render_report_section(title: str, paths: list[str], total_count: int, sample_limit: int) -> str:
        if total_count <= 0:
            return f"{title}: 0"
        lines = [f"{title}: {total_count}"]
        for path in paths[:sample_limit]:
            lines.append(f"- {path}")
        remaining = total_count - min(len(paths), sample_limit)
        if remaining > 0:
            lines.append(f"... and {remaining} more")
        return "\n".join(lines)

    def _show_preflight_report(self, report: GitPreflightReport) -> None:
        headline_bits = [
            f"Branch: {report.current_branch or '(detached)'}",
            f"Upstream: {report.upstream_branch or '(none)'}",
        ]
        if report.upstream_branch:
            headline_bits.append(f"Ahead: {report.ahead_count}")
            headline_bits.append(f"Behind: {report.behind_count}")

        details = [
            self._render_report_section(
                "Staged changes",
                report.staged_paths,
                report.staged_count,
                report.sample_limit,
            ),
            self._render_report_section(
                "Unstaged changes",
                report.unstaged_paths,
                report.unstaged_count,
                report.sample_limit,
            ),
            self._render_report_section(
                "Untracked files",
                report.untracked_paths,
                report.untracked_count,
                report.sample_limit,
            ),
            self._render_report_section(
                "Ignored files",
                report.ignored_paths,
                report.ignored_count,
                report.sample_limit,
            ),
        ]

        has_risk = bool(report.unstaged_count or report.untracked_count)
        if has_risk:
            summary = "Preflight check found files that may not be included in your push."
            icon = QMessageBox.Warning
            self.ide.statusBar().showMessage("Preflight check: potential push omissions detected.", 3200)
        else:
            summary = "Preflight check passed: no obvious local file omissions detected."
            icon = QMessageBox.Information
            self.ide.statusBar().showMessage("Preflight check completed.", 2200)

        box = QMessageBox(self.ide)
        box.setIcon(icon)
        box.setWindowTitle("Git Preflight Check")
        box.setText(summary)
        box.setInformativeText(" | ".join(headline_bits))
        box.setDetailedText("\n\n".join(details))
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def _repo_root_for_path(self, path: str) -> str | None:
        cpath = self._canonical_path(path)
        cached = self._git_repo_root
        if cached and self._path_has_prefix(cpath, cached):
            return cached
        found = self.git_service.find_repo_root(cpath)
        if not found:
            return None
        self._git_repo_root = found
        self._sync_ide_state()
        return found

    def _ensure_git_repo(self) -> str | None:
        if not self._git_repo_root:
            self._git_repo_root = self._repo_root_for_path(self.project_root)
            if self._git_repo_root:
                self.schedule_git_status_refresh(delay_ms=0, force=True)
        self._sync_ide_state()
        if not self._git_repo_root:
            QMessageBox.information(self.ide, "Git", "Current project is not a Git repository.")
            return None
        return self._git_repo_root

    def cleanup(self) -> None:
        self._git_status_debounce_timer.stop()
        self._git_poll_timer.stop()
        self._git_result_pump.stop()
        for future in list(self._git_pending.keys()):
            try:
                future.cancel()
            except Exception:
                pass
        self._git_pending.clear()
        try:
            self._git_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            try:
                self._git_executor.shutdown(wait=False)
            except Exception:
                pass
