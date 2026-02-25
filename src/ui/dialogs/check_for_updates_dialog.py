from __future__ import annotations

import concurrent.futures
from typing import Any, Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.services.update_service import (
    UpdateApplyResult,
    UpdateCheckResult,
    UpdateService,
    UpdateServiceError,
)
from src.ui.custom_dialog import DialogWindow


class CheckForUpdatesDialog(DialogWindow):
    def __init__(
        self,
        *,
        update_service: UpdateService,
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("Check for Updates")
        self.resize(740, 560)

        self._update_service = update_service
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pytpo-updater")
        self._pending: dict[concurrent.futures.Future, str] = {}
        self._latest_check: UpdateCheckResult | None = None

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(40)
        self._result_pump.timeout.connect(self._drain_pending)

        self._build_ui()
        self.destroyed.connect(lambda *_args: self._shutdown())
        QTimer.singleShot(0, self._check_now)

    def _build_ui(self) -> None:
        host = QWidget(self)
        self.set_content_widget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.repo_label = QLabel("Repository: -")
        self.repo_label.setWordWrap(True)
        root.addWidget(self.repo_label)

        self.current_label = QLabel("Current Version: -")
        self.current_label.setWordWrap(True)
        root.addWidget(self.current_label)

        self.latest_label = QLabel("Latest Version: -")
        self.latest_label.setWordWrap(True)
        root.addWidget(self.latest_label)

        self.release_label = QLabel("Release: -")
        self.release_label.setWordWrap(True)
        root.addWidget(self.release_label)

        self.details_edit = QPlainTextEdit()
        self.details_edit.setReadOnly(True)
        self.details_edit.setPlaceholderText("Update details will appear here.")
        root.addWidget(self.details_edit, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        actions = QHBoxLayout()
        self.check_btn = QPushButton("Check Again")
        self.update_btn = QPushButton("Update and Sync")
        self.close_btn = QPushButton("Close")
        self.close_btn.setDefault(True)
        actions.addWidget(self.check_btn)
        actions.addStretch(1)
        actions.addWidget(self.update_btn)
        actions.addWidget(self.close_btn)
        root.addLayout(actions)

        self.check_btn.clicked.connect(self._check_now)
        self.update_btn.clicked.connect(self._apply_update)
        self.close_btn.clicked.connect(self.accept)

        self._refresh_actions()

    def _check_now(self) -> None:
        self._latest_check = None
        self._set_busy(True)
        self._set_status("Checking GitHub for updates...")

        def _run():
            return self._update_service.check_for_updates()

        self._submit_task("check", _run)

    def _apply_update(self) -> None:
        info = self._latest_check
        if info is None:
            self._set_status("Check for updates first.", error=True)
            return
        if not info.update_available:
            self._set_status("Already up to date.", error=False)
            return

        self._set_busy(True)
        self._set_status("Applying update and syncing dependencies...")

        def _run():
            return self._update_service.apply_update()

        self._submit_task("apply", _run)

    def _submit_task(self, kind: str, fn: Callable[[], Any]) -> None:
        try:
            future = self._executor.submit(fn)
        except Exception:
            self._set_busy(False)
            self._set_status("Could not start update operation.", error=True)
            return
        self._pending[future] = kind
        if not self._result_pump.isActive():
            self._result_pump.start()
        self._refresh_actions()

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
        self._refresh_actions()

    def _handle_result(self, kind: str, result: Any, error: Exception | None) -> None:
        if kind == "check":
            if error is None and isinstance(result, UpdateCheckResult):
                self._latest_check = result
                self._render_check(result)
                return
            self._handle_error(error, fallback="Failed to check for updates.")
            return

        if kind == "apply":
            if error is None and isinstance(result, UpdateApplyResult):
                self._render_apply(result)
                return
            self._handle_error(error, fallback="Failed to apply update.")
            return

    def _render_check(self, result: UpdateCheckResult) -> None:
        self.repo_label.setText(f"Repository: {result.repo_slug}")
        self.current_label.setText(
            f"Current Version: {result.current_version} (build {int(result.current_build)})"
        )
        self.latest_label.setText(
            f"Latest Version: {result.latest_version} (build {int(result.latest_build)}, tag: {result.latest_tag})"
        )

        release_parts: list[str] = []
        if result.release_title:
            release_parts.append(result.release_title)
        if result.published_at:
            release_parts.append(result.published_at)
        if result.release_url:
            release_parts.append(result.release_url)
        self.release_label.setText(f"Release: {' | '.join(release_parts) if release_parts else '-'}")

        details_lines: list[str] = []
        details_lines.append(f"Repository: {result.repo_slug}")
        details_lines.append(f"Current: {result.current_version} (build {int(result.current_build)})")
        details_lines.append(f"Latest: {result.latest_version} (build {int(result.latest_build)})")
        details_lines.append(f"Latest tag: {result.latest_tag}")
        if result.release_url:
            details_lines.append(f"Release URL: {result.release_url}")
        if result.published_at:
            details_lines.append(f"Published: {result.published_at}")
        if result.release_notes:
            details_lines.append("")
            details_lines.append("Release Notes:")
            details_lines.append(result.release_notes.strip())
        self.details_edit.setPlainText("\n".join(details_lines).strip())

        if result.update_available:
            self._set_status("Update available. Click 'Update and Sync' to apply it.")
        else:
            self._set_status("You are up to date.")
        self._refresh_actions()

    def _render_apply(self, result: UpdateApplyResult) -> None:
        headline = "Update applied successfully." if result.updated else "Already up to date."
        self._set_status(
            f"{headline} Dependencies were synced. Please reload the UI to apply changes."
        )
        details_lines = [
            f"Repository Root: {result.repo_root}",
            f"Branch: {result.branch}",
            "",
            "git pull --ff-only output:",
            result.pull_output or "(no output)",
            "",
            "uv sync output:",
            result.uv_sync_output or "(no output)",
            "",
            "Action Required: Close and reopen all PyTPO windows.",
        ]
        self.details_edit.setPlainText("\n".join(details_lines))
        self._refresh_actions()

    def _handle_error(self, error: Exception | None, *, fallback: str) -> None:
        if isinstance(error, UpdateServiceError):
            self._set_status(str(error), error=True)
            self.details_edit.setPlainText(str(error))
            return
        self._set_status(fallback, error=True)
        self.details_edit.setPlainText(fallback)

    def _set_busy(self, busy: bool) -> None:
        disabled = bool(busy)
        self.check_btn.setDisabled(disabled)
        self.update_btn.setDisabled(disabled)
        self.close_btn.setDisabled(disabled)

    def _refresh_actions(self) -> None:
        if self._pending:
            self.check_btn.setEnabled(False)
            self.update_btn.setEnabled(False)
            return
        self.check_btn.setEnabled(True)
        self.update_btn.setEnabled(bool(self._latest_check is not None and self._latest_check.update_available))
        self.close_btn.setEnabled(True)

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
