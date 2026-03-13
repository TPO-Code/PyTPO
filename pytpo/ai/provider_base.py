from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProviderResult:
    ok: bool
    status_text: str
    http_status: int | None = None
    error_kind: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelListResult(ProviderResult):
    models: list[str] = field(default_factory=list)


@dataclass(slots=True)
class InlineCompletionRequest:
    base_url: str
    api_key: str
    org_id: str
    project_id: str
    model: str
    system_prompt: str
    user_prompt: str
    max_output_tokens: int
    timeout_s: float


@dataclass(slots=True)
class InlineCompletionResult(ProviderResult):
    text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class AIProviderClient(ABC):
    @abstractmethod
    def fetch_models(
        self,
        *,
        base_url: str,
        api_key: str,
        org_id: str = "",
        project_id: str = "",
        timeout_s: float = 10.0,
        force_refresh: bool = False,
    ) -> ModelListResult:
        raise NotImplementedError

    @abstractmethod
    def test_connection(
        self,
        *,
        base_url: str,
        api_key: str,
        org_id: str = "",
        project_id: str = "",
        timeout_s: float = 10.0,
    ) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def complete_inline(self, request: InlineCompletionRequest) -> InlineCompletionResult:
        raise NotImplementedError
