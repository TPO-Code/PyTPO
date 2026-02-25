from __future__ import annotations

import concurrent.futures
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ai.openai_compatible_client import OpenAICompatibleClient
from src.ai.provider_base import ModelListResult, ProviderResult
from src.ai.settings_schema import normalize_ai_settings
from src.settings_models import SettingsScope


class AIAssistSettingsPage(QWidget):
    def __init__(self, *, manager: Any, scope: SettingsScope, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._scope = scope
        self._client = OpenAICompatibleClient()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="pytpo-ai-settings")
        self._pending: dict[concurrent.futures.Future, str] = {}
        self._base_settings: dict[str, Any] = {}

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(40)
        self._result_pump.timeout.connect(self._drain_pending)

        self._build_ui()
        self._wire_change_notifications()
        initial = self._manager.get("ai_assist", scope_preference=self._scope, default={})
        self._set_settings_value(initial)
        self._base_settings = dict(self._current_settings_value())
        self.destroyed.connect(lambda *_args: self._shutdown())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(12)

        conn_group = QGroupBox("Endpoint")
        conn_form = QFormLayout(conn_group)
        conn_form.setHorizontalSpacing(14)
        conn_form.setVerticalSpacing(8)

        self.enabled_chk = QCheckBox("Enable AI Assist")
        conn_form.addRow(self.enabled_chk)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        conn_form.addRow("Base URL", self.base_url_edit)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        key_layout.addWidget(self.api_key_edit, 1)
        self.reveal_key_btn = QToolButton()
        self.reveal_key_btn.setCheckable(True)
        self.reveal_key_btn.setText("Show")
        self.reveal_key_btn.toggled.connect(self._on_reveal_toggled)
        key_layout.addWidget(self.reveal_key_btn)
        conn_form.addRow("API Key", key_row)

        controls_row = QWidget()
        controls_layout = QHBoxLayout(controls_row)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        self.test_btn = QPushButton("Test Connection")
        self.fetch_btn = QPushButton("Fetch Models")
        controls_layout.addWidget(self.test_btn)
        controls_layout.addWidget(self.fetch_btn)
        controls_layout.addStretch(1)
        conn_form.addRow("", controls_row)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(False)
        self.model_combo.setMaxVisibleItems(24)
        conn_form.addRow("Model", self.model_combo)

        self.storage_warning = QLabel("API keys are stored in plain text in your IDE settings file.")
        self.storage_warning.setWordWrap(True)
        self.storage_warning.setObjectName("AiKeyStorageWarning")
        conn_form.addRow("", self.storage_warning)

        behavior_group = QGroupBox("Inline Completion")
        behavior_form = QFormLayout(behavior_group)
        behavior_form.setHorizontalSpacing(14)
        behavior_form.setVerticalSpacing(8)

        self.trigger_combo = QComboBox()
        self.trigger_combo.addItem("Manual only", "manual_only")
        self.trigger_combo.addItem("Hybrid (recommended)", "hybrid")
        self.trigger_combo.addItem("Passive aggressive", "passive_aggressive")
        behavior_form.addRow("Trigger Mode", self.trigger_combo)

        self.debounce_spin = QSpinBox()
        self.debounce_spin.setRange(40, 5000)
        behavior_form.addRow("Debounce (ms)", self.debounce_spin)

        self.max_context_spin = QSpinBox()
        self.max_context_spin.setRange(512, 32768)
        behavior_form.addRow("Max Context Tokens", self.max_context_spin)

        self.retrieval_spin = QSpinBox()
        self.retrieval_spin.setRange(0, 12)
        behavior_form.addRow("Retrieval Snippets", self.retrieval_spin)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1000, 30000)
        behavior_form.addRow("Inline Timeout (ms)", self.timeout_spin)

        self.output_tokens_spin = QSpinBox()
        self.output_tokens_spin.setRange(32, 512)
        behavior_form.addRow("Max Output Tokens", self.output_tokens_spin)

        self.min_prefix_spin = QSpinBox()
        self.min_prefix_spin.setRange(1, 8)
        behavior_form.addRow("Min Prefix Chars", self.min_prefix_spin)

        context_group = QGroupBox("Context Packing (Advanced)")
        context_form = QFormLayout(context_group)
        context_form.setHorizontalSpacing(14)
        context_form.setVerticalSpacing(8)

        self.context_radius_spin = QSpinBox()
        self.context_radius_spin.setRange(10, 400)
        context_form.addRow("Cursor Radius (lines)", self.context_radius_spin)

        self.enclosing_block_chars_spin = QSpinBox()
        self.enclosing_block_chars_spin.setRange(500, 40000)
        context_form.addRow("Enclosing Block Cap (chars)", self.enclosing_block_chars_spin)

        self.imports_max_spin = QSpinBox()
        self.imports_max_spin.setRange(0, 500)
        context_form.addRow("Max Imports", self.imports_max_spin)

        self.symbols_max_spin = QSpinBox()
        self.symbols_max_spin.setRange(0, 1000)
        context_form.addRow("Max Top-Level Symbols", self.symbols_max_spin)

        retrieval_group = QGroupBox("Retrieval Limits (Advanced)")
        retrieval_form = QFormLayout(retrieval_group)
        retrieval_form.setHorizontalSpacing(14)
        retrieval_form.setVerticalSpacing(8)

        self.retrieval_read_cap_spin = QSpinBox()
        self.retrieval_read_cap_spin.setRange(1000, 200000)
        retrieval_form.addRow("File Read Cap (chars)", self.retrieval_read_cap_spin)

        self.retrieval_same_dir_limit_spin = QSpinBox()
        self.retrieval_same_dir_limit_spin.setRange(0, 500)
        retrieval_form.addRow("Same-Dir File Limit", self.retrieval_same_dir_limit_spin)

        self.retrieval_recent_limit_spin = QSpinBox()
        self.retrieval_recent_limit_spin.setRange(0, 500)
        retrieval_form.addRow("Recent File Limit", self.retrieval_recent_limit_spin)

        self.retrieval_walk_limit_spin = QSpinBox()
        self.retrieval_walk_limit_spin.setRange(0, 2000)
        retrieval_form.addRow("Project Walk File Limit", self.retrieval_walk_limit_spin)

        self.retrieval_total_candidates_spin = QSpinBox()
        self.retrieval_total_candidates_spin.setRange(0, 4000)
        retrieval_form.addRow("Total Candidate Limit", self.retrieval_total_candidates_spin)

        self.retrieval_snippet_char_cap_spin = QSpinBox()
        self.retrieval_snippet_char_cap_spin.setRange(80, 8000)
        retrieval_form.addRow("Snippet Char Cap", self.retrieval_snippet_char_cap_spin)

        self.retrieval_snippet_segments_spin = QSpinBox()
        self.retrieval_snippet_segments_spin.setRange(1, 400)
        retrieval_form.addRow("Snippet Segment Limit", self.retrieval_snippet_segments_spin)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("AiSettingsStatus")

        root.addWidget(conn_group)
        root.addWidget(behavior_group)
        root.addWidget(context_group)
        root.addWidget(retrieval_group)
        root.addWidget(self.status_label)
        root.addStretch(1)

        self.test_btn.clicked.connect(self._on_test_clicked)
        self.fetch_btn.clicked.connect(self._on_fetch_clicked)

    def _wire_change_notifications(self) -> None:
        self.enabled_chk.toggled.connect(lambda *_args: self._notify_pending_changed())
        self.base_url_edit.textEdited.connect(lambda *_args: self._notify_pending_changed())
        self.api_key_edit.textEdited.connect(lambda *_args: self._notify_pending_changed())
        self.model_combo.currentTextChanged.connect(lambda *_args: self._notify_pending_changed())
        self.trigger_combo.currentIndexChanged.connect(lambda *_args: self._notify_pending_changed())
        self.debounce_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.max_context_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.retrieval_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.timeout_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.output_tokens_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.min_prefix_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.context_radius_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.enclosing_block_chars_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.imports_max_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.symbols_max_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.retrieval_read_cap_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.retrieval_same_dir_limit_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.retrieval_recent_limit_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.retrieval_walk_limit_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.retrieval_total_candidates_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.retrieval_snippet_char_cap_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())
        self.retrieval_snippet_segments_spin.valueChanged.connect(lambda *_args: self._notify_pending_changed())

    def _current_settings_value(self) -> dict[str, Any]:
        raw = {
            "enabled": bool(self.enabled_chk.isChecked()),
            "base_url": str(self.base_url_edit.text() or "").strip(),
            "api_key": str(self.api_key_edit.text() or "").strip(),
            "model": str(self.model_combo.currentText() or "").strip(),
            "trigger_mode": str(self.trigger_combo.currentData() or "hybrid"),
            "debounce_ms": int(self.debounce_spin.value()),
            "max_context_tokens": int(self.max_context_spin.value()),
            "retrieval_snippets": int(self.retrieval_spin.value()),
            "inline_timeout_ms": int(self.timeout_spin.value()),
            "min_prefix_chars": int(self.min_prefix_spin.value()),
            "max_output_tokens": int(self.output_tokens_spin.value()),
            "context_radius_lines": int(self.context_radius_spin.value()),
            "enclosing_block_max_chars": int(self.enclosing_block_chars_spin.value()),
            "imports_outline_max_imports": int(self.imports_max_spin.value()),
            "imports_outline_max_symbols": int(self.symbols_max_spin.value()),
            "retrieval_file_read_cap_chars": int(self.retrieval_read_cap_spin.value()),
            "retrieval_same_dir_file_limit": int(self.retrieval_same_dir_limit_spin.value()),
            "retrieval_recent_file_limit": int(self.retrieval_recent_limit_spin.value()),
            "retrieval_walk_file_limit": int(self.retrieval_walk_limit_spin.value()),
            "retrieval_total_candidate_limit": int(self.retrieval_total_candidates_spin.value()),
            "retrieval_snippet_char_cap": int(self.retrieval_snippet_char_cap_spin.value()),
            "retrieval_snippet_segment_limit": int(self.retrieval_snippet_segments_spin.value()),
        }
        normalized = normalize_ai_settings(raw)
        return {str(k): normalized[k] for k in normalized}

    def _set_settings_value(self, value: Any) -> None:
        normalized = normalize_ai_settings(value if isinstance(value, dict) else {})
        self.enabled_chk.setChecked(bool(normalized["enabled"]))
        self.base_url_edit.setText(str(normalized["base_url"]))
        self.api_key_edit.setText(str(normalized["api_key"]))
        self._set_model(normalized["model"])
        self._set_trigger_mode(normalized["trigger_mode"])
        self.debounce_spin.setValue(int(normalized["debounce_ms"]))
        self.max_context_spin.setValue(int(normalized["max_context_tokens"]))
        self.retrieval_spin.setValue(int(normalized["retrieval_snippets"]))
        self.timeout_spin.setValue(int(normalized["inline_timeout_ms"]))
        self.min_prefix_spin.setValue(int(normalized["min_prefix_chars"]))
        self.output_tokens_spin.setValue(int(normalized["max_output_tokens"]))
        self.context_radius_spin.setValue(int(normalized["context_radius_lines"]))
        self.enclosing_block_chars_spin.setValue(int(normalized["enclosing_block_max_chars"]))
        self.imports_max_spin.setValue(int(normalized["imports_outline_max_imports"]))
        self.symbols_max_spin.setValue(int(normalized["imports_outline_max_symbols"]))
        self.retrieval_read_cap_spin.setValue(int(normalized["retrieval_file_read_cap_chars"]))
        self.retrieval_same_dir_limit_spin.setValue(int(normalized["retrieval_same_dir_file_limit"]))
        self.retrieval_recent_limit_spin.setValue(int(normalized["retrieval_recent_file_limit"]))
        self.retrieval_walk_limit_spin.setValue(int(normalized["retrieval_walk_file_limit"]))
        self.retrieval_total_candidates_spin.setValue(int(normalized["retrieval_total_candidate_limit"]))
        self.retrieval_snippet_char_cap_spin.setValue(int(normalized["retrieval_snippet_char_cap"]))
        self.retrieval_snippet_segments_spin.setValue(int(normalized["retrieval_snippet_segment_limit"]))

    def has_pending_settings_changes(self) -> bool:
        return self._current_settings_value() != self._base_settings

    def apply_settings_changes(self) -> list[str]:
        try:
            value = self._current_settings_value()
            self._manager.set("ai_assist", value, self._scope)
            self._base_settings = dict(value)
            self._notify_pending_changed()
            return []
        except Exception as exc:
            return [str(exc)]

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

    def create_bindings(self, binding_cls: Callable[..., Any], scope: SettingsScope) -> list[Any]:
        bindings: list[Any] = []

        def _mk(
            key: str,
            widget: QWidget,
            getter: Callable[[], Any],
            setter: Callable[[Any], None],
            connector: Callable[[Callable[..., None]], None],
        ) -> None:
            bindings.append(binding_cls(key, scope, widget, getter, setter, connector, lambda: []))

        _mk(
            "ai_assist.enabled",
            self.enabled_chk,
            lambda: bool(self.enabled_chk.isChecked()),
            lambda value: self.enabled_chk.setChecked(bool(value)),
            lambda cb: self.enabled_chk.toggled.connect(cb),
        )
        _mk(
            "ai_assist.base_url",
            self.base_url_edit,
            lambda: str(self.base_url_edit.text()).strip(),
            lambda value: self.base_url_edit.setText(str(value or "")),
            lambda cb: self.base_url_edit.textEdited.connect(cb),
        )
        _mk(
            "ai_assist.api_key",
            self.api_key_edit,
            lambda: str(self.api_key_edit.text()).strip(),
            lambda value: self.api_key_edit.setText(str(value or "")),
            lambda cb: self.api_key_edit.textEdited.connect(cb),
        )
        _mk(
            "ai_assist.model",
            self.model_combo,
            lambda: str(self.model_combo.currentText()).strip(),
            self._set_model,
            lambda cb: self.model_combo.currentTextChanged.connect(cb),
        )
        _mk(
            "ai_assist.trigger_mode",
            self.trigger_combo,
            lambda: str(self.trigger_combo.currentData() or "hybrid"),
            self._set_trigger_mode,
            lambda cb: self.trigger_combo.currentIndexChanged.connect(cb),
        )
        _mk(
            "ai_assist.debounce_ms",
            self.debounce_spin,
            lambda: int(self.debounce_spin.value()),
            lambda value: self.debounce_spin.setValue(int(value or 220)),
            lambda cb: self.debounce_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.max_context_tokens",
            self.max_context_spin,
            lambda: int(self.max_context_spin.value()),
            lambda value: self.max_context_spin.setValue(int(value or 8000)),
            lambda cb: self.max_context_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.retrieval_snippets",
            self.retrieval_spin,
            lambda: int(self.retrieval_spin.value()),
            lambda value: self.retrieval_spin.setValue(int(value or 4)),
            lambda cb: self.retrieval_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.inline_timeout_ms",
            self.timeout_spin,
            lambda: int(self.timeout_spin.value()),
            lambda value: self.timeout_spin.setValue(int(value or 10000)),
            lambda cb: self.timeout_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.min_prefix_chars",
            self.min_prefix_spin,
            lambda: int(self.min_prefix_spin.value()),
            lambda value: self.min_prefix_spin.setValue(int(value or 2)),
            lambda cb: self.min_prefix_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.max_output_tokens",
            self.output_tokens_spin,
            lambda: int(self.output_tokens_spin.value()),
            lambda value: self.output_tokens_spin.setValue(int(value or 160)),
            lambda cb: self.output_tokens_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.context_radius_lines",
            self.context_radius_spin,
            lambda: int(self.context_radius_spin.value()),
            lambda value: self.context_radius_spin.setValue(int(value or 75)),
            lambda cb: self.context_radius_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.enclosing_block_max_chars",
            self.enclosing_block_chars_spin,
            lambda: int(self.enclosing_block_chars_spin.value()),
            lambda value: self.enclosing_block_chars_spin.setValue(int(value or 7000)),
            lambda cb: self.enclosing_block_chars_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.imports_outline_max_imports",
            self.imports_max_spin,
            lambda: int(self.imports_max_spin.value()),
            lambda value: self.imports_max_spin.setValue(int(value or 50)),
            lambda cb: self.imports_max_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.imports_outline_max_symbols",
            self.symbols_max_spin,
            lambda: int(self.symbols_max_spin.value()),
            lambda value: self.symbols_max_spin.setValue(int(value or 120)),
            lambda cb: self.symbols_max_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.retrieval_file_read_cap_chars",
            self.retrieval_read_cap_spin,
            lambda: int(self.retrieval_read_cap_spin.value()),
            lambda value: self.retrieval_read_cap_spin.setValue(int(value or 18000)),
            lambda cb: self.retrieval_read_cap_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.retrieval_same_dir_file_limit",
            self.retrieval_same_dir_limit_spin,
            lambda: int(self.retrieval_same_dir_limit_spin.value()),
            lambda value: self.retrieval_same_dir_limit_spin.setValue(int(value or 40)),
            lambda cb: self.retrieval_same_dir_limit_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.retrieval_recent_file_limit",
            self.retrieval_recent_limit_spin,
            lambda: int(self.retrieval_recent_limit_spin.value()),
            lambda value: self.retrieval_recent_limit_spin.setValue(int(value or 80)),
            lambda cb: self.retrieval_recent_limit_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.retrieval_walk_file_limit",
            self.retrieval_walk_limit_spin,
            lambda: int(self.retrieval_walk_limit_spin.value()),
            lambda value: self.retrieval_walk_limit_spin.setValue(int(value or 120)),
            lambda cb: self.retrieval_walk_limit_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.retrieval_total_candidate_limit",
            self.retrieval_total_candidates_spin,
            lambda: int(self.retrieval_total_candidates_spin.value()),
            lambda value: self.retrieval_total_candidates_spin.setValue(int(value or 180)),
            lambda cb: self.retrieval_total_candidates_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.retrieval_snippet_char_cap",
            self.retrieval_snippet_char_cap_spin,
            lambda: int(self.retrieval_snippet_char_cap_spin.value()),
            lambda value: self.retrieval_snippet_char_cap_spin.setValue(int(value or 420)),
            lambda cb: self.retrieval_snippet_char_cap_spin.valueChanged.connect(cb),
        )
        _mk(
            "ai_assist.retrieval_snippet_segment_limit",
            self.retrieval_snippet_segments_spin,
            lambda: int(self.retrieval_snippet_segments_spin.value()),
            lambda value: self.retrieval_snippet_segments_spin.setValue(int(value or 80)),
            lambda cb: self.retrieval_snippet_segments_spin.valueChanged.connect(cb),
        )
        return bindings

    def _on_reveal_toggled(self, checked: bool) -> None:
        self.api_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self.reveal_key_btn.setText("Hide" if checked else "Show")

    def _set_trigger_mode(self, value: Any) -> None:
        idx = self.trigger_combo.findData(str(value or "hybrid"))
        self.trigger_combo.setCurrentIndex(max(0, idx))

    def _set_model(self, value: Any) -> None:
        text = str(value or "").strip()
        idx = self.model_combo.findText(text, Qt.MatchFixedString)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
            return
        if text:
            self.model_combo.addItem(text)
            self.model_combo.setCurrentIndex(self.model_combo.count() - 1)

    def _on_test_clicked(self) -> None:
        cfg = normalize_ai_settings(
            {
                "base_url": self.base_url_edit.text(),
                "api_key": self.api_key_edit.text(),
                "inline_timeout_ms": self.timeout_spin.value(),
            }
        )
        self._set_status("Testing connection...")
        self._set_busy(True)

        def _run() -> ProviderResult:
            return self._client.test_connection(
                base_url=str(cfg["base_url"]),
                api_key=str(cfg["api_key"]),
                timeout_s=max(0.5, float(int(cfg["inline_timeout_ms"])) / 1000.0),
            )

        self._submit_task("test", _run)

    def _on_fetch_clicked(self) -> None:
        cfg = normalize_ai_settings(
            {
                "base_url": self.base_url_edit.text(),
                "api_key": self.api_key_edit.text(),
                "inline_timeout_ms": self.timeout_spin.value(),
            }
        )
        self._set_status("Fetching models...")
        self._set_busy(True)

        def _run() -> ModelListResult:
            return self._client.fetch_models(
                base_url=str(cfg["base_url"]),
                api_key=str(cfg["api_key"]),
                timeout_s=max(0.5, float(int(cfg["inline_timeout_ms"])) / 1000.0),
                force_refresh=True,
            )

        self._submit_task("fetch", _run)

    def _submit_task(self, kind: str, fn: Callable[[], Any]) -> None:
        try:
            fut = self._executor.submit(fn)
        except Exception:
            self._set_busy(False)
            self._set_status("Failed to start network task.", error=True)
            return
        self._pending[fut] = kind
        if not self._result_pump.isActive():
            self._result_pump.start()

    def _drain_pending(self) -> None:
        if not self._pending:
            self._result_pump.stop()
            return

        done: list[concurrent.futures.Future] = []
        for fut, kind in list(self._pending.items()):
            if not fut.done():
                continue
            done.append(fut)
            try:
                result = fut.result()
            except Exception:
                result = None
            self._handle_task_result(kind, result)

        for fut in done:
            self._pending.pop(fut, None)

        if not self._pending:
            self._result_pump.stop()
            self._set_busy(False)

    def _handle_task_result(self, kind: str, result: Any) -> None:
        if kind == "test":
            if isinstance(result, ProviderResult) and result.ok:
                self._set_status("Connection successful.")
            elif isinstance(result, ProviderResult):
                self._set_status(str(result.status_text or "Connection failed."), error=True)
            else:
                self._set_status("Connection failed.", error=True)
            return

        if kind == "fetch":
            if isinstance(result, ModelListResult) and result.ok:
                self._apply_model_ids(result.models)
                self._set_status(str(result.status_text or "Models fetched."))
            elif isinstance(result, ModelListResult):
                self._set_status(str(result.status_text or "Failed to fetch models."), error=True)
            else:
                self._set_status("Failed to fetch models.", error=True)

    def _apply_model_ids(self, models: list[str]) -> None:
        current = str(self.model_combo.currentText() or "").strip()
        items = sorted({str(item).strip() for item in models if str(item).strip()}, key=str.lower)
        self.model_combo.clear()
        for model_id in items:
            self.model_combo.addItem(model_id)
        if current and current in items:
            idx = self.model_combo.findText(current, Qt.MatchFixedString)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        elif items:
            self.model_combo.setCurrentIndex(0)

    def _set_busy(self, busy: bool) -> None:
        disabled = bool(busy)
        self.test_btn.setDisabled(disabled)
        self.fetch_btn.setDisabled(disabled)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = "#d46a6a" if error else "#a4bf7a"
        self.status_label.setText(f"<span style='color:{color};'>{text}</span>")

    def _shutdown(self) -> None:
        self._result_pump.stop()
        for fut in list(self._pending.keys()):
            try:
                fut.cancel()
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


def create_ai_settings_page(
    *,
    manager: Any,
    scope: SettingsScope,
    binding_cls: Callable[..., Any],
    parent: QWidget | None = None,
) -> tuple[QWidget, list[Any]]:
    page = AIAssistSettingsPage(manager=manager, scope=scope, parent=None)
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(page)
    return scroll, page.create_bindings(binding_cls, scope)
