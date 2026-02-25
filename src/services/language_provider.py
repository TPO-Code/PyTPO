"""Language intelligence provider contracts (pure Python).

These contracts make completion/definition/signature/references backends
replaceable per language without coupling UI code to a specific engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LanguageProviderCapabilities:
    completion: bool = True
    signature: bool = True
    definition: bool = True
    references: bool = True


class LanguageProvider(Protocol):
    capabilities: LanguageProviderCapabilities

    def update_settings(self, completion_cfg: dict) -> None:
        ...

    def request_completion(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        prefix: str,
        token: int,
        reason: str = "auto",
    ) -> None:
        ...

    def request_signature(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        token: int,
    ) -> None:
        ...

    def get_definitions(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        token: int = 1,
        interpreter: str | None = None,
        project_root: str | None = None,
    ) -> None:
        ...

    def find_references(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        token: int = 1,
        interpreter: str | None = None,
        project_root: str | None = None,
    ) -> None:
        ...

    def cancel_references(self, token: int) -> None:
        ...

    def register_accepted(self, text: str) -> None:
        ...

    def shutdown(self) -> None:
        ...
