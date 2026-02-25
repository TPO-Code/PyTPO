from __future__ import annotations

import concurrent.futures
from typing import Any, Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.git.github_auth import GitHubAuthError, GitHubAuthStore
from src.git.github_client import GitHubClient, GitHubClientError
from src.settings_models import SettingsScope


class GitHubSettingsPage(QWidget):
    def __init__(self, *, manager: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._auth_store = GitHubAuthStore(manager.paths.ide_app_dir)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="pytpo-github")
        self._pending: dict[concurrent.futures.Future, str] = {}
        self._initial_bridge_enabled = self._bridge_enabled_value()

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(40)
        self._result_pump.timeout.connect(self._drain_pending)

        self._build_ui()
        self._refresh_initial_state()
        self.destroyed.connect(lambda *_args: self._shutdown())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(12)

        auth_group = QGroupBox("GitHub Authentication")
        auth_layout = QVBoxLayout(auth_group)
        auth_layout.setSpacing(8)

        hint = QLabel("Use a GitHub personal access token for API and optional Git transport over HTTPS.")
        hint.setWordWrap(True)
        auth_layout.addWidget(hint)

        token_row = QWidget()
        token_layout = QHBoxLayout(token_row)
        token_layout.setContentsMargins(0, 0, 0, 0)
        token_layout.setSpacing(6)

        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("github_pat_...")
        token_layout.addWidget(self.token_edit, 1)

        self.show_btn = QToolButton()
        self.show_btn.setCheckable(True)
        self.show_btn.setText("Show")
        self.show_btn.toggled.connect(self._on_show_toggled)
        token_layout.addWidget(self.show_btn)
        auth_layout.addWidget(token_row)

        controls_row = QWidget()
        controls_layout = QHBoxLayout(controls_row)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        self.test_btn = QPushButton("Test Sign-in")
        self.sign_out_btn = QPushButton("Sign out")

        controls_layout.addWidget(self.test_btn)
        controls_layout.addWidget(self.sign_out_btn)
        controls_layout.addStretch(1)
        auth_layout.addWidget(controls_row)

        self.use_git_bridge_chk = QCheckBox("Use GitHub token for Git operations (clone/push/pull)")
        self.use_git_bridge_chk.setChecked(self._initial_bridge_enabled)
        auth_layout.addWidget(self.use_git_bridge_chk)

        self.account_label = QLabel("")
        self.account_label.setWordWrap(True)
        auth_layout.addWidget(self.account_label)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        auth_layout.addWidget(self.status_label)

        self.bridge_label = QLabel("")
        self.bridge_label.setWordWrap(True)
        auth_layout.addWidget(self.bridge_label)

        root.addWidget(auth_group)
        root.addStretch(1)

        self.test_btn.clicked.connect(self._on_test_clicked)
        self.sign_out_btn.clicked.connect(self._on_sign_out_clicked)
        self.use_git_bridge_chk.toggled.connect(self._on_bridge_toggled)
        self.token_edit.textChanged.connect(lambda _text: self._notify_pending_changed())

    def create_bindings(self, _binding_cls: Callable[..., Any], _scope: SettingsScope) -> list[Any]:
        return []

    def has_pending_settings_changes(self) -> bool:
        token_pending = bool(str(self.token_edit.text() or "").strip())
        bridge_pending = bool(self.use_git_bridge_chk.isChecked()) != bool(self._initial_bridge_enabled)
        return token_pending or bridge_pending

    def _refresh_initial_state(self) -> None:
        has_token = self._auth_store.has_token()
        cached_username = str(self._manager.get("github.username", scope_preference="ide", default="") or "").strip()
        self._set_bridge_status(bool(self.use_git_bridge_chk.isChecked()))
        if has_token and cached_username:
            self._set_account(cached_username)
            self._set_status(f"API auth: connected as {cached_username}.")
            return
        if has_token:
            self._set_account("")
            self._set_status("API auth: token saved. Click Test Sign-in to verify.")
            return
        self._set_account("")
        self._set_status("API auth: disconnected.", error=True)

    def _on_show_toggled(self, checked: bool) -> None:
        self.token_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self.show_btn.setText("Hide" if checked else "Show")

    def apply_settings_changes(self) -> list[str]:
        """
        Called by the parent Settings dialog during Apply/Save.

        Persists a token only when the input field is non-empty.
        """
        bridge_enabled = bool(self.use_git_bridge_chk.isChecked())
        try:
            self._manager.set("github.use_token_for_git", bridge_enabled, "ide")
        except Exception as exc:
            self._set_status("Could not save Git transport bridge setting.", error=True)
            return [str(exc)]
        self._initial_bridge_enabled = bridge_enabled
        self._set_bridge_status(bridge_enabled)

        token = str(self.token_edit.text() or "").strip()
        if not token:
            self._notify_pending_changed()
            return []
        try:
            self._auth_store.set(token)
        except GitHubAuthError as exc:
            self._set_status(str(exc), error=True)
            return [str(exc)]
        try:
            self._manager.set("github.username", "", "ide")
        except Exception:
            pass
        self.token_edit.clear()
        self._set_account("")
        self.show_btn.setChecked(False)
        self._set_status("API auth: token saved securely.")
        self._notify_pending_changed()
        return []

    def _on_sign_out_clicked(self) -> None:
        try:
            self._auth_store.clear()
            self._manager.set("github.username", "", "ide")
        except Exception:
            self._set_status("Failed to clear GitHub credentials.", error=True)
            return
        self.token_edit.clear()
        self.show_btn.setChecked(False)
        self._set_account("")
        self._set_status("API auth: disconnected.")
        self._notify_pending_changed()

    def _on_test_clicked(self) -> None:
        token = str(self.token_edit.text() or "").strip()
        if not token:
            token = str(self._auth_store.get() or "").strip()
        if not token:
            self._set_status("No token found. Save a token first.", error=True)
            return

        self._set_busy(True)
        self._set_status("Testing GitHub sign-in...")

        def _run() -> str:
            client = GitHubClient(token)
            return client.test_connection()

        self._submit_task("test", _run)

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
        if kind != "test":
            return

        if error is None and isinstance(result, str) and result.strip():
            username = result.strip()
            self._set_account(username)
            self._set_status(f"API auth: connected as {username}.")
            try:
                self._manager.set("github.username", username, "ide")
            except Exception:
                pass
            self._notify_pending_changed()
            return

        if isinstance(error, GitHubClientError):
            self._set_status(str(error), error=True)
            return

        self._set_status("GitHub sign-in test failed.", error=True)

    def _set_account(self, username: str) -> None:
        text = str(username or "").strip()
        if text:
            self.account_label.setText(f"Account: {text}")
        else:
            self.account_label.setText("Account: Not connected")

    def _set_bridge_status(self, enabled: bool) -> None:
        if enabled:
            self.bridge_label.setText("<span style='color:#a4bf7a;'>Git transport bridge: enabled.</span>")
        else:
            self.bridge_label.setText("<span style='color:#d8bf6a;'>Git transport bridge: disabled.</span>")

    def _set_busy(self, busy: bool) -> None:
        disabled = bool(busy)
        self.test_btn.setDisabled(disabled)
        self.sign_out_btn.setDisabled(disabled)
        self.token_edit.setDisabled(disabled)
        self.use_git_bridge_chk.setDisabled(disabled)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = "#d46a6a" if error else "#a4bf7a"
        self.status_label.setText(f"<span style='color:{color};'>{text}</span>")

    def _on_bridge_toggled(self, checked: bool) -> None:
        self._set_bridge_status(bool(checked))
        self._notify_pending_changed()

    def _notify_pending_changed(self) -> None:
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "_refresh_dirty_state"):
            parent = parent.parentWidget()
        if parent is None:
            return
        refresh = getattr(parent, "_refresh_dirty_state", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass

    def _bridge_enabled_value(self) -> bool:
        try:
            return bool(self._manager.get("github.use_token_for_git", scope_preference="ide", default=True))
        except Exception:
            return True

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


def create_github_settings_page(
    *,
    manager: Any,
    scope: SettingsScope,
    binding_cls: Callable[..., Any],
    parent: QWidget | None = None,
) -> tuple[QWidget, list[Any]]:
    _ = scope
    page = GitHubSettingsPage(manager=manager, parent=None)
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(page)
    return scroll, page.create_bindings(binding_cls, scope)
