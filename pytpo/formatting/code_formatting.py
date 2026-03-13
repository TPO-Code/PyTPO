"""Code formatting provider contracts and registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class FormatRequest:
    file_path: str
    source_text: str
    project_root: str = ""
    interpreter: str = ""
    parent_widget: object | None = None


@dataclass(slots=True)
class FormatResult:
    status: str  # ok | canceled | error
    formatted_text: str = ""
    message: str = ""
    stderr: str = ""
    debug_lines: list[str] = field(default_factory=list)
    created_config_path: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class CodeFormattingProvider(Protocol):
    def can_format(self, language_id: str, *, file_path: str = "") -> bool:
        ...

    def format_document(self, request: FormatRequest) -> FormatResult:
        ...

    def format_selection(
        self,
        request: FormatRequest,
        *,
        start_line: int,
        end_line: int,
    ) -> FormatResult:
        ...


class CodeFormattingRegistry:
    """Maps language ids and file extensions to formatting providers."""

    def __init__(self) -> None:
        self._by_language: dict[str, CodeFormattingProvider] = {}
        self._by_extension: dict[str, CodeFormattingProvider] = {}

    def register_provider(
        self,
        provider: CodeFormattingProvider,
        *,
        language_ids: set[str] | tuple[str, ...] | list[str] | None = None,
        extensions: set[str] | tuple[str, ...] | list[str] | None = None,
    ) -> None:
        if provider is None:
            return
        for raw in list(language_ids or []):
            key = str(raw or "").strip().lower()
            if key:
                self._by_language[key] = provider
        for raw in list(extensions or []):
            ext = str(raw or "").strip().lower()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = f".{ext}"
            self._by_extension[ext] = provider

    def provider_for(self, *, language_id: str, file_path: str = "") -> CodeFormattingProvider | None:
        key = str(language_id or "").strip().lower()
        if key and key in self._by_language:
            return self._by_language[key]
        ext = str(Path(str(file_path or "")).suffix or "").strip().lower()
        if ext and ext in self._by_extension:
            return self._by_extension[ext]
        return None

    def can_format(self, *, language_id: str, file_path: str = "") -> bool:
        provider = self.provider_for(language_id=language_id, file_path=file_path)
        if provider is None:
            return False
        try:
            return bool(provider.can_format(language_id, file_path=file_path))
        except Exception:
            return False
