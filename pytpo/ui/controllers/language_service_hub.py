"""Qt-aware hub that routes language intelligence requests to providers."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QObject, Signal


class LanguageServiceHub(QObject):
    completionReady = Signal(object)
    signatureReady = Signal(object)
    definitionReady = Signal(object)
    referencesProgress = Signal(object)
    referencesReady = Signal(object)
    statusMessage = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._providers_by_language: dict[str, object] = {}
        self._default_provider: object | None = None
        self._connected_provider_ids: set[int] = set()

    def register_provider(
        self,
        provider: object,
        *,
        language_ids: Iterable[str] | None = None,
        default: bool = False,
    ) -> None:
        if provider is None:
            return

        if default or self._default_provider is None:
            self._default_provider = provider

        if language_ids is not None:
            if isinstance(language_ids, str):
                language_iter = [language_ids]
            else:
                language_iter = language_ids
            for raw in language_iter:
                lang = str(raw or "").strip().lower()
                if not lang:
                    continue
                self._providers_by_language[lang] = provider

        self._connect_provider_signals(provider)

    def provider_for_language(self, language_id: str) -> object | None:
        key = str(language_id or "").strip().lower()
        if key and key in self._providers_by_language:
            return self._providers_by_language[key]
        return self._default_provider

    def has_provider_for(self, language_id: str) -> bool:
        return self.provider_for_language(language_id) is not None

    def request_completion(self, *, language_id: str, **kwargs) -> None:
        provider = self.provider_for_language(language_id)
        if provider is None:
            self._emit_empty_completion(kwargs)
            return
        fn = getattr(provider, "request_completion", None)
        if callable(fn):
            fn(**kwargs)
            return
        self._emit_empty_completion(kwargs)

    def request_signature(self, *, language_id: str, **kwargs) -> None:
        provider = self.provider_for_language(language_id)
        if provider is None:
            self._emit_empty_signature(kwargs)
            return
        fn = getattr(provider, "request_signature", None)
        if callable(fn):
            fn(**kwargs)
            return
        self._emit_empty_signature(kwargs)

    def get_definitions(self, *, language_id: str, **kwargs) -> None:
        provider = self.provider_for_language(language_id)
        if provider is None:
            self._emit_empty_definition(kwargs)
            return
        fn = getattr(provider, "get_definitions", None)
        if callable(fn):
            fn(**kwargs)
            return
        self._emit_empty_definition(kwargs)

    def find_references(self, *, language_id: str, **kwargs) -> None:
        provider = self.provider_for_language(language_id)
        if provider is None:
            self._emit_empty_references(kwargs)
            return
        fn = getattr(provider, "find_references", None)
        if callable(fn):
            fn(**kwargs)
            return
        self._emit_empty_references(kwargs)

    def cancel_references(self, token: int) -> None:
        for provider in self._iter_unique_providers():
            fn = getattr(provider, "cancel_references", None)
            if callable(fn):
                fn(token)

    def register_accepted(self, text: str) -> None:
        for provider in self._iter_unique_providers():
            fn = getattr(provider, "register_accepted", None)
            if callable(fn):
                fn(text)

    def update_settings(self, completion_cfg: dict) -> None:
        for provider in self._iter_unique_providers():
            fn = getattr(provider, "update_settings", None)
            if callable(fn):
                fn(completion_cfg)

    def shutdown(self) -> None:
        for provider in self._iter_unique_providers():
            fn = getattr(provider, "shutdown", None)
            if callable(fn):
                fn()

    def _iter_unique_providers(self):
        seen: set[int] = set()
        if self._default_provider is not None:
            pid = id(self._default_provider)
            seen.add(pid)
            yield self._default_provider
        for provider in self._providers_by_language.values():
            pid = id(provider)
            if pid in seen:
                continue
            seen.add(pid)
            yield provider

    def _connect_provider_signals(self, provider: object) -> None:
        pid = id(provider)
        if pid in self._connected_provider_ids:
            return
        self._connected_provider_ids.add(pid)

        completion_ready = getattr(provider, "completionReady", None)
        if completion_ready is not None and hasattr(completion_ready, "connect"):
            completion_ready.connect(self.completionReady.emit)

        signature_ready = getattr(provider, "signatureReady", None)
        if signature_ready is not None and hasattr(signature_ready, "connect"):
            signature_ready.connect(self.signatureReady.emit)

        definition_ready = getattr(provider, "definitionReady", None)
        if definition_ready is not None and hasattr(definition_ready, "connect"):
            definition_ready.connect(self.definitionReady.emit)

        references_progress = getattr(provider, "referencesProgress", None)
        if references_progress is not None and hasattr(references_progress, "connect"):
            references_progress.connect(self.referencesProgress.emit)

        references_ready = getattr(provider, "referencesReady", None)
        if references_ready is not None and hasattr(references_ready, "connect"):
            references_ready.connect(self.referencesReady.emit)

        status_message = getattr(provider, "statusMessage", None)
        if status_message is not None and hasattr(status_message, "connect"):
            status_message.connect(self.statusMessage.emit)

    def _emit_empty_completion(self, request: dict) -> None:
        self.completionReady.emit(
            {
                "result_type": "completion",
                "file_path": str(request.get("file_path") or ""),
                "token": int(request.get("token") or 0),
                "items": [],
                "backend": "none",
                "reason": str(request.get("reason") or "manual"),
            }
        )

    def _emit_empty_signature(self, request: dict) -> None:
        self.signatureReady.emit(
            {
                "result_type": "signature",
                "file_path": str(request.get("file_path") or ""),
                "token": int(request.get("token") or 0),
                "signature": "",
                "documentation": "",
                "source": "none",
            }
        )

    def _emit_empty_definition(self, request: dict) -> None:
        self.definitionReady.emit(
            {
                "result_type": "definition",
                "file_path": str(request.get("file_path") or ""),
                "token": int(request.get("token") or 0),
                "results": [],
                "source": "none",
            }
        )

    def _emit_empty_references(self, request: dict) -> None:
        self.referencesReady.emit(
            {
                "result_type": "references_done",
                "file_path": str(request.get("file_path") or ""),
                "token": int(request.get("token") or 0),
                "results": [],
                "processed": 0,
                "canceled": False,
                "source": "none",
            }
        )
