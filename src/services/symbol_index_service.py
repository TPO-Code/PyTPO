"""Project symbol export index service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from src.services.ast_query import is_valid_python_identifier


@dataclass(frozen=True)
class SymbolFileExport:
    module_name: str
    names: set[str]
    mtime_ns: int
    size: int


class SymbolIndexService:
    def __init__(self) -> None:
        self._file_exports: dict[str, SymbolFileExport] = {}
        self._modules_by_name: dict[str, list[str]] = {}

    def refresh(
        self,
        file_paths: list[str],
        *,
        module_name_for_file: Callable[[str], str],
        exported_names_for_file: Callable[[str], set[str]],
    ) -> None:
        next_file_exports: dict[str, SymbolFileExport] = {}
        previous = self._file_exports

        for file_path in file_paths:
            module_name = module_name_for_file(file_path)
            if not module_name:
                continue

            try:
                stat = os.stat(file_path)
            except Exception:
                continue
            mtime_ns = int(getattr(stat, "st_mtime_ns", 0) or 0)
            size = int(getattr(stat, "st_size", 0) or 0)

            cached = previous.get(file_path)
            if (
                cached
                and cached.module_name == module_name
                and cached.mtime_ns == mtime_ns
                and cached.size == size
            ):
                next_file_exports[file_path] = cached
                continue

            names = exported_names_for_file(file_path)
            next_file_exports[file_path] = SymbolFileExport(
                module_name=module_name,
                names=names,
                mtime_ns=mtime_ns,
                size=size,
            )

        modules_by_name: dict[str, list[str]] = {}
        for export in next_file_exports.values():
            for name in export.names:
                key = name.lower()
                entries = modules_by_name.setdefault(key, [])
                if export.module_name not in entries:
                    entries.append(export.module_name)
        for entries in modules_by_name.values():
            entries.sort()

        self._file_exports = next_file_exports
        self._modules_by_name = modules_by_name

    def modules_for_symbol(self, symbol: str, *, current_module: str = "") -> list[str]:
        target = str(symbol or "").strip()
        if not is_valid_python_identifier(target):
            return []

        modules = list(self._modules_by_name.get(target.lower(), []))
        if not modules:
            return []

        if current_module:
            modules = [name for name in modules if name != current_module]
        return modules

    def invalidate_file(self, file_path: str) -> None:
        self._file_exports.pop(file_path, None)

    def clear(self) -> None:
        self._file_exports.clear()
        self._modules_by_name.clear()

    def file_exports_snapshot(self) -> dict[str, SymbolFileExport]:
        return dict(self._file_exports)
