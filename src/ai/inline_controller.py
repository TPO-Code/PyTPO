from __future__ import annotations

import concurrent.futures
import queue
import re
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from src.ai.context_assembler import ContextAssembler
from src.ai.prompt_overrides import resolve_system_prompt
from src.ai.provider_base import AIProviderClient, InlineCompletionRequest
from src.ai.settings_schema import NormalizedAIAssistConfig, default_ai_settings


@dataclass(slots=True)
class _InlineWorkItem:
    editor_id: str
    file_path: str
    source_text: str
    line: int
    column: int
    prefix: str
    previous_char: str
    trigger: str
    token: int
    cfg: NormalizedAIAssistConfig
    recent_files: list[str]


class InlineSuggestionController(QObject):
    suggestionReady = Signal(object)  # {editor_id, token, text, trigger, metadata}
    statusMessage = Signal(str)

    def __init__(
        self,
        *,
        provider_client: AIProviderClient,
        context_assembler: ContextAssembler,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider_client
        self._assembler = context_assembler

        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="pytpo-ai-inline")
        self._active_futures: set[concurrent.futures.Future] = set()
        self._inflight_by_editor: dict[str, concurrent.futures.Future] = {}
        self._result_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(16)
        self._result_pump.timeout.connect(self._drain_results)
        self._result_pump.start()

        self._debounce_timers: dict[str, QTimer] = {}
        self._pending_by_editor: dict[str, _InlineWorkItem] = {}
        self._latest_token_by_editor: dict[str, int] = {}
        self._token_counter = 0
        self._cfg = NormalizedAIAssistConfig.from_mapping(default_ai_settings())

    def update_settings(self, ai_cfg: Any) -> None:
        self._cfg = NormalizedAIAssistConfig.from_mapping(ai_cfg)
        if not self._cfg.enabled:
            self.cancel_all(clear=True)

    def request_manual(
        self,
        *,
        editor_id: str,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        prefix: str,
        previous_char: str,
        recent_files: list[str] | None = None,
    ) -> None:
        item = self._build_item(
            editor_id=editor_id,
            file_path=file_path,
            source_text=source_text,
            line=line,
            column=column,
            prefix=prefix,
            previous_char=previous_char,
            trigger="manual",
            recent_files=recent_files,
        )
        if item is None:
            return

        self._pending_by_editor.pop(item.editor_id, None)
        self._stop_timer(item.editor_id)
        self._start_worker(item)

    def request_passive(
        self,
        *,
        editor_id: str,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        prefix: str,
        previous_char: str,
        recent_files: list[str] | None = None,
    ) -> None:
        item = self._build_item(
            editor_id=editor_id,
            file_path=file_path,
            source_text=source_text,
            line=line,
            column=column,
            prefix=prefix,
            previous_char=previous_char,
            trigger="passive",
            recent_files=recent_files,
        )
        if item is None:
            return

        if item.cfg.trigger_mode == "manual_only":
            return
        if not self._should_passive_trigger(item.prefix, item.previous_char, item.cfg.min_prefix_chars):
            return

        self._pending_by_editor[item.editor_id] = item
        timer = self._debounce_timers.get(item.editor_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda editor_key=item.editor_id: self._flush_debounced(editor_key))
            self._debounce_timers[item.editor_id] = timer
        timer.start(int(item.cfg.debounce_ms))

    def cancel_for_editor(self, editor_id: str, *, clear: bool = False) -> None:
        key = str(editor_id or "").strip()
        if not key:
            return
        self._pending_by_editor.pop(key, None)
        self._stop_timer(key)
        inflight = self._inflight_by_editor.pop(key, None)
        if inflight is not None:
            try:
                inflight.cancel()
            except Exception:
                pass
        self._latest_token_by_editor[key] = self._next_token()
        if clear:
            self.suggestionReady.emit(
                {
                    "editor_id": key,
                    "token": self._latest_token_by_editor[key],
                    "text": "",
                    "trigger": "cancel",
                    "ok": True,
                    "status_text": "",
                    "metadata": {},
                }
            )

    def cancel_all(self, *, clear: bool = False) -> None:
        for editor_id in list(set(self._latest_token_by_editor.keys()) | set(self._pending_by_editor.keys())):
            self.cancel_for_editor(editor_id, clear=clear)

    def shutdown(self) -> None:
        self.cancel_all(clear=False)
        for timer in list(self._debounce_timers.values()):
            timer.stop()
            timer.deleteLater()
        self._debounce_timers.clear()
        self._pending_by_editor.clear()

        try:
            self._result_pump.stop()
        except Exception:
            pass

        for fut in list(self._active_futures):
            try:
                fut.cancel()
            except Exception:
                pass
        self._active_futures.clear()
        self._inflight_by_editor.clear()

        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass

    def _build_item(
        self,
        *,
        editor_id: str,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        prefix: str,
        previous_char: str,
        trigger: str,
        recent_files: list[str] | None,
    ) -> _InlineWorkItem | None:
        key = str(editor_id or "").strip()
        if not key:
            return None
        if not self._cfg.enabled:
            return None

        token = self._next_token()
        self._latest_token_by_editor[key] = token

        return _InlineWorkItem(
            editor_id=key,
            file_path=str(file_path or ""),
            source_text=str(source_text or ""),
            line=max(1, int(line or 1)),
            column=max(0, int(column or 0)),
            prefix=str(prefix or ""),
            previous_char=str(previous_char or ""),
            trigger=str(trigger or "manual"),
            token=token,
            cfg=self._cfg,
            recent_files=list(recent_files or []),
        )

    def _flush_debounced(self, editor_id: str) -> None:
        item = self._pending_by_editor.pop(editor_id, None)
        if item is None:
            return
        self._start_worker(item)

    def _start_worker(self, item: _InlineWorkItem) -> None:
        existing = self._inflight_by_editor.get(item.editor_id)
        if existing is not None and not existing.done():
            try:
                existing.cancel()
            except Exception:
                pass

        try:
            fut = self._executor.submit(self._run_worker, item)
        except Exception:
            if item.trigger == "manual":
                self.statusMessage.emit("AI Assist is unavailable right now.")
            return

        self._inflight_by_editor[item.editor_id] = fut
        self._active_futures.add(fut)
        fut.add_done_callback(lambda future: self._queue_result(item.editor_id, future))

    def _queue_result(self, editor_id: str, future: concurrent.futures.Future) -> None:
        self._active_futures.discard(future)
        current = self._inflight_by_editor.get(editor_id)
        if current is future:
            self._inflight_by_editor.pop(editor_id, None)

        if future.cancelled():
            return
        try:
            payload = future.result()
        except Exception:
            payload = {
                "editor_id": editor_id,
                "token": int(self._latest_token_by_editor.get(editor_id, 0)),
                "text": "",
                "ok": False,
                "status_text": "AI request failed.",
                "trigger": "manual",
                "metadata": {},
            }
        self._result_queue.put(payload)

    def _drain_results(self) -> None:
        while True:
            try:
                payload = self._result_queue.get_nowait()
            except queue.Empty:
                return
            if not isinstance(payload, dict):
                continue
            self._handle_worker_result(payload)

    def _handle_worker_result(self, payload: dict[str, Any]) -> None:
        editor_id = str(payload.get("editor_id") or "")
        token = int(payload.get("token") or 0)
        if not editor_id or token <= 0:
            return
        if token != int(self._latest_token_by_editor.get(editor_id, 0)):
            return

        ok = bool(payload.get("ok", False))
        trigger = str(payload.get("trigger") or "manual")
        text = str(payload.get("text") or "")
        status_text = str(payload.get("status_text") or "").strip()

        if not ok and trigger == "manual" and status_text:
            self.statusMessage.emit(status_text)
        elif ok and trigger == "manual" and not text.strip():
            self.statusMessage.emit("No AI suggestion.")

        self.suggestionReady.emit(payload)

    def _run_worker(self, item: _InlineWorkItem) -> dict[str, Any]:
        cfg = item.cfg
        if not cfg.model:
            return {
                "editor_id": item.editor_id,
                "token": item.token,
                "trigger": item.trigger,
                "text": "",
                "ok": False,
                "status_text": "AI model is not configured.",
                "metadata": {},
            }

        assembled = self._assembler.assemble_inline(
            file_path=item.file_path,
            source_text=item.source_text,
            line=item.line,
            column=item.column,
            max_context_tokens=cfg.max_context_tokens,
            retrieval_snippets=cfg.retrieval_snippets,
            context_radius_lines=cfg.context_radius_lines,
            enclosing_block_max_chars=cfg.enclosing_block_max_chars,
            imports_outline_max_imports=cfg.imports_outline_max_imports,
            imports_outline_max_symbols=cfg.imports_outline_max_symbols,
            retrieval_file_read_cap_chars=cfg.retrieval_file_read_cap_chars,
            retrieval_same_dir_file_limit=cfg.retrieval_same_dir_file_limit,
            retrieval_recent_file_limit=cfg.retrieval_recent_file_limit,
            retrieval_walk_file_limit=cfg.retrieval_walk_file_limit,
            retrieval_total_candidate_limit=cfg.retrieval_total_candidate_limit,
            retrieval_snippet_char_cap=cfg.retrieval_snippet_char_cap,
            retrieval_snippet_segment_limit=cfg.retrieval_snippet_segment_limit,
            recent_files=item.recent_files,
        )
        system_prompt, prompt_meta = resolve_system_prompt(
            assembled.system_prompt,
            cfg.prompt_overrides,
            file_path=item.file_path,
            language=str(assembled.metadata.get("language") or ""),
            project_root=str(getattr(self._assembler, "project_root", "") or ""),
        )
        metadata = dict(assembled.metadata)
        if prompt_meta:
            metadata["prompt_overrides"] = prompt_meta

        request = InlineCompletionRequest(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            org_id="",
            project_id="",
            model=cfg.model,
            system_prompt=system_prompt,
            user_prompt=assembled.user_prompt,
            max_output_tokens=cfg.max_output_tokens,
            timeout_s=max(0.5, float(cfg.inline_timeout_ms) / 1000.0),
        )
        result = self._provider.complete_inline(request)
        if not result.ok:
            return {
                "editor_id": item.editor_id,
                "token": item.token,
                "trigger": item.trigger,
                "text": "",
                "ok": False,
                "status_text": str(result.status_text or "AI request failed."),
                "metadata": metadata,
            }

        text = self._sanitize_completion(str(result.text or ""), prefix=item.prefix)
        return {
            "editor_id": item.editor_id,
            "token": item.token,
            "trigger": item.trigger,
            "text": text,
            "ok": True,
            "status_text": str(result.status_text or ""),
            "metadata": metadata,
        }

    def _sanitize_completion(self, text: str, *, prefix: str) -> str:
        raw = str(text or "").replace("\r", "")
        if not raw.strip():
            return ""

        fenced = re.match(r"^\s*```[a-zA-Z0-9_\-]*\n([\s\S]*?)\n```\s*$", raw)
        if fenced:
            raw = str(fenced.group(1) or "")

        cleaned = raw
        if prefix and cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
        cleaned = cleaned.lstrip("\n")
        if len(cleaned) > 2400:
            cleaned = cleaned[:2400]
        return cleaned

    def _should_passive_trigger(self, prefix: str, previous_char: str, min_prefix_chars: int) -> bool:
        if len(str(prefix or "")) >= max(1, int(min_prefix_chars or 1)):
            return True
        return str(previous_char or "") in {".", "(", ",", "="}

    def _next_token(self) -> int:
        self._token_counter += 1
        return self._token_counter

    def _stop_timer(self, editor_id: str) -> None:
        timer = self._debounce_timers.get(editor_id)
        if timer is None:
            return
        timer.stop()
