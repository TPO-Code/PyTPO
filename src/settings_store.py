from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.settings_models import SettingsScope


class SettingsStoreError(RuntimeError):
    """Raised when a settings file cannot be loaded or saved."""


def deep_merge_defaults(data: Mapping[str, Any], defaults: Mapping[str, Any]) -> dict[str, Any]:
    """Merge defaults into data without overwriting explicitly provided values."""
    merged = deepcopy(data)
    for key, default_value in defaults.items():
        if key not in merged:
            merged[key] = deepcopy(default_value)
            continue
        current = merged[key]
        if isinstance(current, dict) and isinstance(default_value, dict):
            merged[key] = deep_merge_defaults(current, default_value)
    return merged


def dot_get(data: Mapping[str, Any], key: str, default: Any = None) -> Any:
    if not key:
        return data
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def dot_set(data: dict[str, Any], key: str, value: Any) -> None:
    if not key:
        raise ValueError("Key cannot be empty.")
    current: dict[str, Any] = data
    parts = key.split(".")
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def dot_delete(data: dict[str, Any], key: str) -> bool:
    if not key:
        return False
    current: dict[str, Any] = data
    parts = key.split(".")
    trail: list[tuple[dict[str, Any], str]] = []
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            return False
        trail.append((current, part))
        current = next_value
    leaf = parts[-1]
    if leaf not in current:
        return False
    del current[leaf]

    # Prune empty dictionaries up the chain.
    while trail:
        parent, child_key = trail.pop()
        child = parent.get(child_key)
        if isinstance(child, dict) and not child:
            del parent[child_key]
        else:
            break
    return True


class JsonSettingsStore:
    """JSON-backed mutable store with defaults and dot-key helpers."""

    def __init__(self, path: Path, defaults: Mapping[str, Any], *, persistent: bool = True) -> None:
        self.path = Path(path)
        self.defaults: dict[str, Any] = deepcopy(dict(defaults))
        self.data: dict[str, Any] = {}
        self.dirty: bool = False
        self.last_error: str | None = None
        self.persistent: bool = bool(persistent)

    def load(self) -> dict[str, Any]:
        if not self.persistent:
            self.data = deep_merge_defaults({}, self.defaults)
            self.dirty = False
            self.last_error = None
            return self.data

        previous_data = deepcopy(self.data) if isinstance(self.data, dict) else {}
        missing = not self.path.exists()
        loaded: dict[str, Any] = {}
        load_failed = False
        self.last_error = None

        if not missing:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    loaded = raw
                else:
                    load_failed = True
                    self.last_error = (
                        f"Settings root in '{self.path}' must be a JSON object, "
                        f"found {type(raw).__name__}."
                    )
            except Exception as exc:  # pragma: no cover - defensive UI path
                # Keep the app usable without mutating the invalid source file.
                load_failed = True
                self.last_error = str(exc)

        if load_failed:
            base = previous_data if previous_data else {}
            self.data = deep_merge_defaults(base, self.defaults)
            self.dirty = False
            return self.data

        self.data = deep_merge_defaults(loaded, self.defaults)
        self.dirty = missing
        return self.data

    def save(self) -> None:
        if not self.persistent:
            self.dirty = False
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
            self.dirty = False
            self.last_error = None
        except Exception as exc:  # pragma: no cover - defensive UI path
            raise SettingsStoreError(
                f"Could not write settings file '{self.path}': {exc}"
            ) from exc

    def get(self, key: str, default: Any = None) -> Any:
        return dot_get(self.data, key, default)

    def set(self, key: str, value: Any) -> bool:
        current = self.get(key)
        if current == value:
            return False
        dot_set(self.data, key, value)
        self.dirty = True
        return True

    def delete(self, key: str) -> bool:
        changed = dot_delete(self.data, key)
        if changed:
            self.dirty = True
        return changed

    def has(self, key: str) -> bool:
        marker = object()
        return self.get(key, marker) is not marker

    def restore_defaults(self) -> None:
        self.data = deepcopy(self.defaults)
        self.dirty = True

    def reload_from_disk(self) -> dict[str, Any]:
        return self.load()

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.data)


class ScopedSettingsStores:
    """Utility wrapper around both project and IDE JSON stores."""

    def __init__(self, stores: Mapping[SettingsScope, JsonSettingsStore]) -> None:
        self._stores: dict[SettingsScope, JsonSettingsStore] = dict(stores)
        missing = {"project", "ide"} - set(self._stores)
        if missing:
            missing_scopes = ", ".join(sorted(missing))
            raise ValueError(f"Missing stores for scopes: {missing_scopes}")

    def store_for(self, scope: SettingsScope) -> JsonSettingsStore:
        return self._stores[scope]

    def load_all(self) -> dict[SettingsScope, dict[str, Any]]:
        return {scope: store.load() for scope, store in self._stores.items()}

    def save_all(
        self,
        scopes: Iterable[SettingsScope] | None = None,
        *,
        only_dirty: bool = False,
    ) -> set[SettingsScope]:
        saved: set[SettingsScope] = set()
        target_scopes = tuple(scopes) if scopes is not None else tuple(self._stores)
        for scope in target_scopes:
            store = self._stores[scope]
            if only_dirty and not store.dirty:
                continue
            store.save()
            saved.add(scope)
        return saved

    def reload_scope(self, scope: SettingsScope) -> dict[str, Any]:
        return self._stores[scope].reload_from_disk()

    def restore_scope_defaults(self, scope: SettingsScope) -> None:
        self._stores[scope].restore_defaults()

    def dirty_scopes(self) -> set[SettingsScope]:
        return {scope for scope, store in self._stores.items() if store.dirty}
