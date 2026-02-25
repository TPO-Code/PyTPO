from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any, Mapping

from src.ai.settings_schema import normalize_ai_settings
from src.settings_models import (
    SettingsPaths,
    SettingsScope,
    default_ide_settings,
    default_project_settings,
)
from src.settings_store import JsonSettingsStore, ScopedSettingsStores, deep_merge_defaults, dot_delete, dot_get, dot_set


IDE_COMPLETION_KEYS: set[str] = {
    "enabled",
    "respect_excludes",
    "auto_trigger",
    "auto_trigger_after_dot",
    "auto_trigger_min_chars",
    "debounce_ms",
    "backend",
    "max_items",
    "case_sensitive",
    "show_signatures",
    "show_right_label",
    "show_doc_tooltip",
    "doc_tooltip_delay_ms",
}


def _normalize_query_driver_text(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.lower() in {"off", "none", "disabled"}:
        return raw.lower()
    cleaned: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,]+", raw):
        part = str(token or "").strip()
        if not part:
            continue
        if part.lower().startswith("--query-driver="):
            part = part.split("=", 1)[1].strip()
            if not part:
                continue
        if part.startswith("="):
            part = part[1:].strip()
            if not part:
                continue
        dedupe = part.lower()
        if dedupe in seen:
            continue
        seen.add(dedupe)
        cleaned.append(part)
    return ",".join(cleaned)

IDE_KEY_ALIASES: dict[str, str] = {
    "theme": "theme",
    "font_size": "font_size",
    "font_family": "font_family",
    "window": "window",
    "window.use_native_chrome": "window.use_native_chrome",
    "window.show_title_in_custom_toolbar": "window.show_title_in_custom_toolbar",
    "run": "run",
    "projects": "projects",
    "projects.last_create_in": "projects.last_create_in",
    "autosave": "autosave",
    "lint": "lint",
    "completion": "completion",
    "completion.max_items": "completion.max_items",
    "completion.case_sensitive": "completion.case_sensitive",
    "completion.show_signatures": "completion.show_signatures",
    "completion.show_right_label": "completion.show_right_label",
    "completion.show_doc_tooltip": "completion.show_doc_tooltip",
    "completion.doc_tooltip_delay_ms": "completion.doc_tooltip_delay_ms",
    "ai_assist": "ai_assist",
    "ai_assist.enabled": "ai_assist.enabled",
    "ai_assist.base_url": "ai_assist.base_url",
    "ai_assist.api_key": "ai_assist.api_key",
    "ai_assist.model": "ai_assist.model",
    "ai_assist.trigger_mode": "ai_assist.trigger_mode",
    "ai_assist.debounce_ms": "ai_assist.debounce_ms",
    "ai_assist.max_context_tokens": "ai_assist.max_context_tokens",
    "ai_assist.retrieval_snippets": "ai_assist.retrieval_snippets",
    "ai_assist.inline_timeout_ms": "ai_assist.inline_timeout_ms",
    "ai_assist.min_prefix_chars": "ai_assist.min_prefix_chars",
    "ai_assist.max_output_tokens": "ai_assist.max_output_tokens",
    "ai_assist.context_radius_lines": "ai_assist.context_radius_lines",
    "ai_assist.enclosing_block_max_chars": "ai_assist.enclosing_block_max_chars",
    "ai_assist.imports_outline_max_imports": "ai_assist.imports_outline_max_imports",
    "ai_assist.imports_outline_max_symbols": "ai_assist.imports_outline_max_symbols",
    "ai_assist.retrieval_file_read_cap_chars": "ai_assist.retrieval_file_read_cap_chars",
    "ai_assist.retrieval_same_dir_file_limit": "ai_assist.retrieval_same_dir_file_limit",
    "ai_assist.retrieval_recent_file_limit": "ai_assist.retrieval_recent_file_limit",
    "ai_assist.retrieval_walk_file_limit": "ai_assist.retrieval_walk_file_limit",
    "ai_assist.retrieval_total_candidate_limit": "ai_assist.retrieval_total_candidate_limit",
    "ai_assist.retrieval_snippet_char_cap": "ai_assist.retrieval_snippet_char_cap",
    "ai_assist.retrieval_snippet_segment_limit": "ai_assist.retrieval_snippet_segment_limit",
    "github": "github",
    "github.username": "github.username",
    "github.use_token_for_git": "github.use_token_for_git",
    "github.last_clone_destination": "github.last_clone_destination",
    "github.last_clone_mode": "github.last_clone_mode",
    "github.last_clone_url": "github.last_clone_url",
    "git": "git",
    "git.enable_file_tinting": "git.enable_file_tinting",
    "git.tracked_clean_color": "git.tracked_clean_color",
    "git.tracked_dirty_color": "git.tracked_dirty_color",
    "git.untracked_color": "git.untracked_color",
    "editor": "editor",
    "editor.background_color": "editor.background_color",
    "editor.background_image_path": "editor.background_image_path",
    "editor.background_image_scale_mode": "editor.background_image_scale_mode",
    "editor.background_image_brightness": "editor.background_image_brightness",
    "editor.background_tint_color": "editor.background_tint_color",
    "editor.background_tint_strength": "editor.background_tint_strength",
    "file_dialog": "file_dialog",
    "file_dialog.background_image_path": "file_dialog.background_image_path",
    "file_dialog.background_scale_mode": "file_dialog.background_scale_mode",
    "file_dialog.background_brightness": "file_dialog.background_brightness",
    "file_dialog.tint_color": "file_dialog.tint_color",
    "file_dialog.tint_strength": "file_dialog.tint_strength",
    "file_dialog.starred_paths": "file_dialog.starred_paths",
    "keybindings": "keybindings",
}

PROJECT_TO_IDE_KEY_MAPPINGS: tuple[tuple[str, str], ...] = (
    ("font_size", "font_size"),
    ("font_family", "font_family"),
    ("theme", "theme"),
    ("window", "window"),
    ("run", "run"),
    ("projects", "projects"),
    ("autosave", "autosave"),
    ("lint", "lint"),
    ("completion", "completion"),
    ("ai_assist", "ai_assist"),
    ("github", "github"),
    ("git", "git"),
    ("editor", "editor"),
    ("file_dialog", "file_dialog"),
    ("keybindings", "keybindings"),
)

PROJECT_KEY_PREFIXES: tuple[str, ...] = (
    "project_name",
    "interpreter",
    "interpreters",
    "indexing",
    "explorer",
    "build",
    "open_editors",
    "rust",
)

IDE_KEY_PREFIXES: tuple[str, ...] = (
    "theme",
    "font_size",
    "font_family",
    "window",
    "run",
    "projects",
    "autosave",
    "lint",
    "completion",
    "ai_assist",
    "github",
    "git",
    "editor",
    "file_dialog",
    "keybindings",
    "defaults",
)


class LegacySettingsAdapter:
    """
    Compatibility adapter for legacy callers.

    Deprecated behavior: code should no longer assume every setting is stored in
    `<project_root>/.tide/project.json`. Callers should use `SettingsManager` scope-aware
    APIs for new code.
    """

    def __init__(self, manager: SettingsManager) -> None:
        self._manager = manager

    def get(self, key: str, default: Any = None) -> Any:
        return self._manager.get(key, default=default)

    def set(self, key: str, value: Any, scope: SettingsScope | None = None) -> None:
        resolved_scope = scope
        resolved_key = key
        if resolved_scope is None:
            translated = self._manager.resolve_key_scope(key)
            if translated is not None:
                resolved_scope, resolved_key = translated
            else:
                resolved_scope = "project"
        self._manager.set(resolved_key, value, resolved_scope)


class SettingsManager:
    PROJECT_IDE_DIRNAME = ".tide"

    def __init__(
        self,
        project_root: str | Path,
        ide_app_dir: str | Path,
        *,
        project_filename: str = ".tide/project.json",
        ide_filename: str = "ide-settings.json",
        project_persistent: bool = True,
    ) -> None:
        self.paths = SettingsPaths(
            project_root=Path(project_root),
            ide_app_dir=Path(ide_app_dir),
            project_filename=project_filename,
            ide_filename=ide_filename,
        )

        self.project_store = JsonSettingsStore(
            self.paths.project_file,
            default_project_settings(),
            persistent=project_persistent,
        )
        self.ide_store = JsonSettingsStore(self.paths.ide_file, default_ide_settings())
        self.scoped_stores = ScopedSettingsStores(
            {
                "project": self.project_store,
                "ide": self.ide_store,
            }
        )
        self.compat = LegacySettingsAdapter(self)

    @property
    def project_path(self) -> Path:
        return self.paths.project_file

    @property
    def ide_path(self) -> Path:
        return self.paths.ide_file

    def load_all(self) -> None:
        self.scoped_stores.load_all()
        migrated = self.migrate_legacy_project_json()
        normalized = self._normalize_all()
        if migrated or normalized or self.scoped_stores.dirty_scopes():
            self.save_all(only_dirty=True)

    def save_all(
        self,
        scopes: set[SettingsScope] | None = None,
        *,
        only_dirty: bool = False,
        allow_project_repair: bool = False,
    ) -> set[SettingsScope]:
        target_scopes = set(scopes) if scopes is not None else {"project", "ide"}
        # Never overwrite a malformed project.json with regenerated defaults.
        if self.project_store.last_error and not allow_project_repair:
            target_scopes.discard("project")
        elif self.project_store.last_error and "project" in target_scopes and not self.project_store.dirty:
            # If explicit repair is enabled, still avoid writing untouched in-memory defaults.
            target_scopes.discard("project")
        if not target_scopes:
            return set()
        return self.scoped_stores.save_all(scopes=target_scopes, only_dirty=only_dirty)

    def load_errors(self) -> dict[SettingsScope, str]:
        errors: dict[SettingsScope, str] = {}
        if isinstance(self.project_store.last_error, str) and self.project_store.last_error.strip():
            errors["project"] = self.project_store.last_error.strip()
        if isinstance(self.ide_store.last_error, str) and self.ide_store.last_error.strip():
            errors["ide"] = self.ide_store.last_error.strip()
        return errors

    def get(
        self,
        key: str,
        scope_preference: SettingsScope | None = None,
        *,
        default: Any = None,
    ) -> Any:
        if scope_preference is not None:
            return self.scoped_stores.store_for(scope_preference).get(key, default)

        translated = self.resolve_key_scope(key)
        if translated is not None:
            scope, scope_key = translated
            return self.scoped_stores.store_for(scope).get(scope_key, default)

        project_val = self.project_store.get(key, None)
        if project_val is not None:
            return project_val
        return self.ide_store.get(key, default)

    def set(self, key: str, value: Any, scope: SettingsScope) -> None:
        self.scoped_stores.store_for(scope).set(key, value)

    def reload_all(self) -> None:
        self.load_all()

    def restore_scope_defaults(self, scope: SettingsScope) -> None:
        self.scoped_stores.restore_scope_defaults(scope)

    def migrate_legacy_project_json(self) -> bool:
        return self._extract_ide_keys_from_project()

    def resolve_key_scope(self, key: str) -> tuple[SettingsScope, str] | None:
        alias = IDE_KEY_ALIASES.get(key)
        if alias is not None:
            return "ide", alias

        for prefix in IDE_KEY_PREFIXES:
            if key == prefix or key.startswith(prefix + "."):
                return "ide", key

        for prefix in PROJECT_KEY_PREFIXES:
            if key == prefix or key.startswith(prefix + "."):
                return "project", key

        return None

    def export_legacy_config(self) -> dict[str, Any]:
        merged = self.project_store.snapshot()
        ide_data = self.ide_store.snapshot()

        for key in (
            "theme",
            "font_size",
            "font_family",
            "window",
            "run",
            "projects",
            "autosave",
            "lint",
            "completion",
            "ai_assist",
            "github",
            "git",
            "editor",
            "file_dialog",
            "keybindings",
            "defaults",
        ):
            value = ide_data.get(key)
            if value is None:
                continue
            merged[key] = deepcopy(value)
        return merged

    def apply_legacy_config(self, legacy_config: Mapping[str, Any]) -> None:
        incoming = deepcopy(dict(legacy_config))

        project_data: dict[str, Any] = {}
        ide_data: dict[str, Any] = {}

        for key, value in incoming.items():
            if key in {
                "theme",
                "font_size",
                "window",
                "run",
                "projects",
                "autosave",
                "lint",
                "completion",
                "ai_assist",
                "github",
                "git",
                "editor",
                "file_dialog",
                "defaults",
            }:
                ide_data[key] = value
                continue

            project_data[key] = value

        project_data = deep_merge_defaults(project_data, default_project_settings())
        ide_data = deep_merge_defaults(ide_data, default_ide_settings())

        project_changed = project_data != self.project_store.data
        ide_changed = ide_data != self.ide_store.data

        self.project_store.data = project_data
        self.ide_store.data = ide_data
        self.project_store.dirty = project_changed
        self.ide_store.dirty = ide_changed

        self._normalize_all()

    def _normalize_all(self) -> bool:
        changed = False
        changed |= self._normalize_project_settings()
        changed |= self._normalize_ide_settings()
        return changed

    def _normalize_project_settings(self) -> bool:
        moved = self._extract_ide_keys_from_project()
        data = self.project_store.data
        before = deepcopy(data)
        project_defaults = default_project_settings()
        key_pat = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

        def _norm_env_entries(raw_env: Any) -> list[str]:
            out: list[str] = []
            seen: set[str] = set()
            if isinstance(raw_env, dict):
                for rk, rv in raw_env.items():
                    key = str(rk or "").strip()
                    if not key or not key_pat.match(key):
                        continue
                    item = f"{key}={str(rv or '')}"
                    dedupe = item.lower()
                    if dedupe in seen:
                        continue
                    seen.add(dedupe)
                    out.append(item)
                return out
            if not isinstance(raw_env, list):
                return out
            for item in raw_env:
                text = str(item or "").strip()
                if not text or "=" not in text:
                    continue
                key, _, value = text.partition("=")
                key = key.strip()
                if not key or not key_pat.match(key):
                    continue
                entry = f"{key}={value}"
                dedupe = entry.lower()
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                out.append(entry)
            return out

        def _norm_build_cfg(raw_cfg: Any, index: int) -> dict[str, Any]:
            cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
            name = str(cfg.get("name") or "").strip() or f"Config {index + 1}"
            norm = {
                "name": name,
                "build_dir": str(cfg.get("build_dir") or "build").strip() or "build",
                "build_type": str(cfg.get("build_type") or "Debug").strip() or "Debug",
                "target": str(cfg.get("target") or "").strip(),
                "configure_args": str(cfg.get("configure_args") or "").strip(),
                "build_args": str(cfg.get("build_args") or "").strip(),
                "run_args": str(cfg.get("run_args") or "").strip(),
                "env": _norm_env_entries(cfg.get("env")),
            }
            try:
                norm["parallel_jobs"] = max(0, min(128, int(cfg.get("parallel_jobs", 0))))
            except Exception:
                norm["parallel_jobs"] = 0
            return norm

        def _norm_python_run_cfg(raw_cfg: Any, index: int) -> dict[str, Any]:
            cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
            name = str(cfg.get("name") or "").strip() or f"Run Config {index + 1}"
            norm = {
                "name": name,
                "script_path": str(cfg.get("script_path") or "").strip(),
                "args": str(cfg.get("args") or "").strip(),
                "working_dir": str(cfg.get("working_dir") or "").strip(),
                "interpreter": str(cfg.get("interpreter") or "").strip(),
                "env": _norm_env_entries(cfg.get("env")),
            }
            return norm

        def _norm_rust_run_cfg(raw_cfg: Any, index: int) -> dict[str, Any]:
            cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
            name = str(cfg.get("name") or "").strip() or f"Cargo Config {index + 1}"
            command_type = str(cfg.get("command_type") or "run").strip().lower()
            if command_type not in {"run", "test", "build", "custom"}:
                command_type = "run"
            profile = str(cfg.get("profile") or "debug").strip().lower()
            if profile not in {"debug", "release"}:
                profile = "debug"
            return {
                "name": name,
                "command_type": command_type,
                "package": str(cfg.get("package") or "").strip(),
                "binary": str(cfg.get("binary") or "").strip(),
                "profile": profile,
                "features": str(cfg.get("features") or "").strip(),
                "args": str(cfg.get("args") or "").strip(),
                "test_filter": str(cfg.get("test_filter") or "").strip(),
                "command": str(cfg.get("command") or "").strip(),
                "working_dir": str(cfg.get("working_dir") or "").strip(),
                "env": _norm_env_entries(cfg.get("env")),
            }

        if not isinstance(data.get("project_name"), str) or not str(data.get("project_name")).strip():
            data["project_name"] = "My Python Project"

        interpreter = data.get("interpreter")
        data["interpreter"] = str(interpreter).strip() if isinstance(interpreter, str) and interpreter.strip() else "python"

        interps = data.get("interpreters")
        if not isinstance(interps, dict):
            interps = {}
        interps = deep_merge_defaults(interps, project_defaults["interpreters"])
        default_interp = str(interps.get("default") or "").strip()
        interps["default"] = default_interp or data["interpreter"]

        by_directory = interps.get("by_directory")
        if not isinstance(by_directory, list):
            by_directory = []
        clean_by_directory: list[dict[str, Any]] = []
        for entry in by_directory:
            if not isinstance(entry, dict):
                continue
            raw_path = str(entry.get("path") or "").strip()
            if not raw_path:
                continue
            clean_entry: dict[str, Any] = {
                "path": raw_path,
                "exclude_from_indexing": bool(entry.get("exclude_from_indexing", False)),
            }
            if "python" in entry:
                py_val = str(entry.get("python") or "").strip()
                if py_val:
                    clean_entry["python"] = py_val
            clean_by_directory.append(clean_entry)
        interps["by_directory"] = clean_by_directory
        data["interpreters"] = interps

        indexing = data.get("indexing")
        if not isinstance(indexing, dict):
            indexing = {}
        indexing = deep_merge_defaults(indexing, project_defaults["indexing"])
        exclude_dirs = indexing.get("exclude_dirs")
        if not isinstance(exclude_dirs, list):
            exclude_dirs = []
        normalized_dirs: list[str] = []
        seen_dirs: set[str] = set()
        for item in exclude_dirs:
            text = str(item).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            normalized_dirs.append(text)
        if self.PROJECT_IDE_DIRNAME.lower() not in seen_dirs:
            normalized_dirs.append(self.PROJECT_IDE_DIRNAME)
        indexing["exclude_dirs"] = normalized_dirs

        exclude_files = indexing.get("exclude_files")
        if not isinstance(exclude_files, list):
            exclude_files = []
        indexing["exclude_files"] = [str(item) for item in exclude_files if str(item).strip()]
        indexing["follow_symlinks"] = bool(indexing.get("follow_symlinks", False))
        data["indexing"] = indexing

        explorer = data.get("explorer")
        if not isinstance(explorer, dict):
            explorer = {}
        explorer = deep_merge_defaults(explorer, project_defaults["explorer"])
        explorer_exclude_dirs = explorer.get("exclude_dirs")
        if not isinstance(explorer_exclude_dirs, list):
            explorer_exclude_dirs = []
        normalized_explorer_dirs: list[str] = []
        seen_explorer_dirs: set[str] = set()
        for item in explorer_exclude_dirs:
            text = str(item).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen_explorer_dirs:
                continue
            seen_explorer_dirs.add(key)
            normalized_explorer_dirs.append(text)
        explorer["exclude_dirs"] = normalized_explorer_dirs

        explorer_exclude_files = explorer.get("exclude_files")
        if not isinstance(explorer_exclude_files, list):
            explorer_exclude_files = []
        explorer["exclude_files"] = [str(item) for item in explorer_exclude_files if str(item).strip()]
        explorer["hide_indexing_excluded"] = bool(explorer.get("hide_indexing_excluded", True))
        data["explorer"] = explorer

        build_cfg = data.get("build")
        if not isinstance(build_cfg, dict):
            build_cfg = {}
        build_cfg = deep_merge_defaults(build_cfg, project_defaults["build"])
        cmake_build = build_cfg.get("cmake")
        if not isinstance(cmake_build, dict):
            cmake_build = {}
        cmake_build = deep_merge_defaults(cmake_build, project_defaults["build"]["cmake"])

        raw_build_cfgs = cmake_build.get("build_configs")
        build_configs: list[dict[str, Any]] = []
        if isinstance(raw_build_cfgs, list):
            for idx, item in enumerate(raw_build_cfgs):
                build_configs.append(_norm_build_cfg(item, idx))

        # One-time migration path from IDE-scoped run.cmake presets.
        if not build_configs:
            ide_run = self.ide_store.data.get("run")
            ide_cmake = ide_run.get("cmake") if isinstance(ide_run, dict) else {}
            if isinstance(ide_cmake, dict):
                migrated_cfgs = ide_cmake.get("build_configs")
                if isinstance(migrated_cfgs, list):
                    for idx, item in enumerate(migrated_cfgs):
                        build_configs.append(_norm_build_cfg(item, idx))
                    if build_configs:
                        self.project_store.dirty = True
                        if dot_delete(self.ide_store.data, "run.cmake.build_configs"):
                            self.ide_store.dirty = True

        if not build_configs:
            defaults_cfgs = project_defaults["build"]["cmake"].get("build_configs", [])
            if isinstance(defaults_cfgs, list):
                for idx, item in enumerate(defaults_cfgs):
                    build_configs.append(_norm_build_cfg(item, idx))

        deduped_cfgs: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for idx, cfg in enumerate(build_configs):
            base_name = str(cfg.get("name") or "").strip() or f"Config {idx + 1}"
            name = base_name
            counter = 2
            while name.lower() in seen_names:
                name = f"{base_name} ({counter})"
                counter += 1
            cfg["name"] = name
            seen_names.add(name.lower())
            deduped_cfgs.append(cfg)
        build_configs = deduped_cfgs
        cmake_build["build_configs"] = build_configs

        active_config = str(cmake_build.get("active_config") or "").strip()
        if not active_config and build_configs:
            active_config = str(build_configs[0].get("name") or "").strip()
        if not active_config:
            ide_run = self.ide_store.data.get("run")
            ide_cmake = ide_run.get("cmake") if isinstance(ide_run, dict) else {}
            if isinstance(ide_cmake, dict):
                active_config = str(ide_cmake.get("active_config") or "").strip()
                if active_config and dot_delete(self.ide_store.data, "run.cmake.active_config"):
                    self.ide_store.dirty = True
        valid_names = {str(cfg.get("name") or "").strip().lower() for cfg in build_configs}
        if active_config.lower() not in valid_names and build_configs:
            active_config = str(build_configs[0].get("name") or "").strip()
        cmake_build["active_config"] = active_config

        python_build = build_cfg.get("python")
        if not isinstance(python_build, dict):
            python_build = {}
        python_build = deep_merge_defaults(python_build, project_defaults["build"]["python"])
        raw_py_cfgs = python_build.get("run_configs")
        py_configs: list[dict[str, Any]] = []
        if isinstance(raw_py_cfgs, list):
            for idx, item in enumerate(raw_py_cfgs):
                py_configs.append(_norm_python_run_cfg(item, idx))

        deduped_py: list[dict[str, Any]] = []
        seen_py_names: set[str] = set()
        for idx, cfg in enumerate(py_configs):
            base_name = str(cfg.get("name") or "").strip() or f"Run Config {idx + 1}"
            name = base_name
            counter = 2
            while name.lower() in seen_py_names:
                name = f"{base_name} ({counter})"
                counter += 1
            cfg["name"] = name
            seen_py_names.add(name.lower())
            deduped_py.append(cfg)
        py_configs = deduped_py
        python_build["run_configs"] = py_configs

        active_py = str(python_build.get("active_config") or "").strip()
        valid_py_names = {str(cfg.get("name") or "").strip().lower() for cfg in py_configs}
        if active_py and active_py.lower() not in valid_py_names:
            active_py = str(py_configs[0].get("name") or "").strip() if py_configs else ""
        python_build["active_config"] = active_py

        rust_build = build_cfg.get("rust")
        if not isinstance(rust_build, dict):
            rust_build = {}
        rust_build = deep_merge_defaults(rust_build, project_defaults["build"]["rust"])
        raw_rust_cfgs = rust_build.get("run_configs")
        rust_configs: list[dict[str, Any]] = []
        if isinstance(raw_rust_cfgs, list):
            for idx, item in enumerate(raw_rust_cfgs):
                rust_configs.append(_norm_rust_run_cfg(item, idx))

        deduped_rust: list[dict[str, Any]] = []
        seen_rust_names: set[str] = set()
        for idx, cfg in enumerate(rust_configs):
            base_name = str(cfg.get("name") or "").strip() or f"Cargo Config {idx + 1}"
            name = base_name
            counter = 2
            while name.lower() in seen_rust_names:
                name = f"{base_name} ({counter})"
                counter += 1
            cfg["name"] = name
            seen_rust_names.add(name.lower())
            deduped_rust.append(cfg)
        rust_configs = deduped_rust
        rust_build["run_configs"] = rust_configs

        active_rust = str(rust_build.get("active_config") or "").strip()
        valid_rust_names = {str(cfg.get("name") or "").strip().lower() for cfg in rust_configs}
        if active_rust and active_rust.lower() not in valid_rust_names:
            active_rust = str(rust_configs[0].get("name") or "").strip() if rust_configs else ""
        rust_build["active_config"] = active_rust

        build_cfg["python"] = python_build
        build_cfg["cmake"] = cmake_build
        build_cfg["rust"] = rust_build
        data["build"] = build_cfg

        cpp_cfg = data.get("c_cpp")
        if not isinstance(cpp_cfg, dict):
            cpp_cfg = {}
        cpp_cfg = deep_merge_defaults(cpp_cfg, project_defaults["c_cpp"])
        cpp_cfg["enable_cpp"] = bool(cpp_cfg.get("enable_cpp", True))
        cpp_cfg["clangd_path"] = str(cpp_cfg.get("clangd_path") or "clangd").strip() or "clangd"
        cpp_cfg["query_driver"] = _normalize_query_driver_text(cpp_cfg.get("query_driver"))
        mode = str(cpp_cfg.get("compile_commands_mode") or "auto").strip().lower()
        if mode not in {"auto", "manual"}:
            mode = "auto"
        cpp_cfg["compile_commands_mode"] = mode
        cpp_cfg["compile_commands_path"] = str(cpp_cfg.get("compile_commands_path") or "").strip()
        cpp_cfg["log_lsp_traffic"] = bool(cpp_cfg.get("log_lsp_traffic", False))

        fallback = cpp_cfg.get("fallback")
        if not isinstance(fallback, dict):
            fallback = {}
        fallback = deep_merge_defaults(fallback, project_defaults["c_cpp"]["fallback"])
        fallback["c_standard"] = str(fallback.get("c_standard") or "").strip()
        fallback["cpp_standard"] = str(fallback.get("cpp_standard") or "").strip()

        includes_raw = fallback.get("include_paths")
        if not isinstance(includes_raw, list):
            includes_raw = []
        fallback["include_paths"] = [str(item).strip() for item in includes_raw if str(item).strip()]

        defines_raw = fallback.get("defines")
        if not isinstance(defines_raw, list):
            defines_raw = []
        fallback["defines"] = [str(item).strip() for item in defines_raw if str(item).strip()]

        extra_flags_raw = fallback.get("extra_flags")
        if isinstance(extra_flags_raw, list):
            extra_flags_clean = [str(item).strip() for item in extra_flags_raw if str(item).strip()]
        elif isinstance(extra_flags_raw, str):
            extra_flags_clean = str(extra_flags_raw).split()
        else:
            extra_flags_clean = []
        fallback["extra_flags"] = extra_flags_clean

        cpp_cfg["fallback"] = fallback
        data["c_cpp"] = cpp_cfg

        rust_cfg = data.get("rust")
        if not isinstance(rust_cfg, dict):
            rust_cfg = {}
        rust_cfg = deep_merge_defaults(rust_cfg, project_defaults["rust"])
        rust_cfg["enable_rust"] = bool(rust_cfg.get("enable_rust", True))
        rust_cfg["rust_analyzer_path"] = (
            str(rust_cfg.get("rust_analyzer_path") or "rust-analyzer").strip() or "rust-analyzer"
        )
        rust_args = rust_cfg.get("rust_analyzer_args")
        if isinstance(rust_args, list):
            rust_cfg["rust_analyzer_args"] = [str(item).strip() for item in rust_args if str(item).strip()]
        elif isinstance(rust_args, str):
            text = str(rust_args).strip()
            rust_cfg["rust_analyzer_args"] = [text] if text else []
        else:
            rust_cfg["rust_analyzer_args"] = []
        try:
            rust_cfg["did_change_debounce_ms"] = max(
                120,
                min(3000, int(rust_cfg.get("did_change_debounce_ms", 260))),
            )
        except Exception:
            rust_cfg["did_change_debounce_ms"] = 260
        rust_cfg["log_lsp_traffic"] = bool(rust_cfg.get("log_lsp_traffic", False))
        init_opts = rust_cfg.get("initialization_options")
        rust_cfg["initialization_options"] = init_opts if isinstance(init_opts, dict) else {}
        data["rust"] = rust_cfg

        open_editors = data.get("open_editors")
        if not isinstance(open_editors, list):
            open_editors = []
        clean_editors: list[dict[str, Any]] = []
        for item in open_editors:
            if not isinstance(item, dict):
                continue
            file_path = item.get("file_path")
            if not isinstance(file_path, str) or not file_path.strip():
                continue
            clean_item: dict[str, Any] = {"file_path": str(file_path).strip()}
            key = item.get("key")
            if isinstance(key, str) and key.strip():
                clean_item["key"] = key.strip()
            modified = item.get("modified")
            if isinstance(modified, bool):
                clean_item["modified"] = modified
            clean_editors.append(clean_item)
        data["open_editors"] = clean_editors

        changed = data != before
        if moved or changed:
            self.project_store.dirty = True
        return moved or changed

    def _normalize_ide_settings(self) -> bool:
        data = self.ide_store.data
        before = deepcopy(data)

        theme = data.get("theme")
        data["theme"] = str(theme).strip() if isinstance(theme, str) and theme.strip() else "Dark"

        try:
            data["font_size"] = max(6, min(48, int(data.get("font_size", 10))))
        except Exception:
            data["font_size"] = 10

        window = data.get("window")
        if not isinstance(window, dict):
            window = {}
        window = deep_merge_defaults(window, default_ide_settings()["window"])
        window["use_native_chrome"] = bool(window.get("use_native_chrome", False))
        window["show_title_in_custom_toolbar"] = bool(window.get("show_title_in_custom_toolbar", True))
        data["window"] = window

        run = data.get("run")
        if not isinstance(run, dict):
            run = {}
        run = deep_merge_defaults(run, default_ide_settings()["run"])
        run["default_cwd"] = str(run.get("default_cwd") or ".")
        for bool_key in (
            "auto_save_before_run",
            "reuse_existing_output_tab",
            "clear_output_before_run",
            "focus_output_on_run",
            "clear_terminal_before_run",
        ):
            run[bool_key] = bool(run.get(bool_key, True))

        cmake = run.get("cmake")
        if not isinstance(cmake, dict):
            cmake = {}
        cmake = deep_merge_defaults(cmake, default_ide_settings()["run"]["cmake"])
        cmake["build_dir"] = str(cmake.get("build_dir") or "build").strip() or "build"
        cmake["build_type"] = str(cmake.get("build_type") or "Debug").strip() or "Debug"
        cmake["target"] = str(cmake.get("target") or "").strip()
        cmake["configure_args"] = str(cmake.get("configure_args") or "").strip()
        cmake["build_args"] = str(cmake.get("build_args") or "").strip()
        cmake["run_args"] = str(cmake.get("run_args") or "").strip()
        try:
            cmake["parallel_jobs"] = max(0, min(128, int(cmake.get("parallel_jobs", 0))))
        except Exception:
            cmake["parallel_jobs"] = 0

        run["cmake"] = cmake
        data["run"] = run

        projects = data.get("projects")
        if not isinstance(projects, dict):
            projects = {}
        projects = deep_merge_defaults(projects, default_ide_settings()["projects"])
        projects["open_last_project"] = bool(projects.get("open_last_project", False))
        try:
            projects["max_recent_projects"] = max(1, min(50, int(projects.get("max_recent_projects", 10))))
        except Exception:
            projects["max_recent_projects"] = 10
        recent_raw = projects.get("recent_projects")
        clean_recent: list[str] = []
        seen_recent: set[str] = set()
        if isinstance(recent_raw, list):
            for item in recent_raw:
                text = str(item or "").strip()
                if not text:
                    continue
                try:
                    canonical = str(Path(text).expanduser().resolve())
                except Exception:
                    canonical = str(Path(text).expanduser())
                dedupe_key = canonical.lower()
                if dedupe_key in seen_recent:
                    continue
                seen_recent.add(dedupe_key)
                clean_recent.append(canonical)
        projects["recent_projects"] = clean_recent[: int(projects["max_recent_projects"])]
        raw_create_in = str(projects.get("last_create_in") or "").strip()
        if raw_create_in:
            try:
                projects["last_create_in"] = str(Path(raw_create_in).expanduser().resolve())
            except Exception:
                projects["last_create_in"] = str(Path(raw_create_in).expanduser())
        else:
            projects["last_create_in"] = str(Path.home())
        data["projects"] = projects

        autosave = data.get("autosave")
        if not isinstance(autosave, dict):
            autosave = {}
        autosave = deep_merge_defaults(autosave, default_ide_settings()["autosave"])
        autosave["enabled"] = bool(autosave.get("enabled", False))
        try:
            autosave["debounce_ms"] = max(250, min(30000, int(autosave.get("debounce_ms", 1200))))
        except Exception:
            autosave["debounce_ms"] = 1200
        data["autosave"] = autosave

        lint = data.get("lint")
        if not isinstance(lint, dict):
            lint = {}
        lint = deep_merge_defaults(lint, default_ide_settings()["lint"])
        lint["enabled"] = bool(lint.get("enabled", True))
        lint["respect_excludes"] = bool(lint.get("respect_excludes", True))
        lint["run_on_save"] = bool(lint.get("run_on_save", True))
        lint["run_on_idle"] = bool(lint.get("run_on_idle", True))
        lint["debounce_ms"] = max(100, min(5000, int(lint.get("debounce_ms", 600))))
        lint["max_problems_per_file"] = max(1, int(lint.get("max_problems_per_file", 200)))

        backend = str(lint.get("backend", "ruff")).strip().lower()
        if backend not in {"ruff", "pyflakes", "ast"}:
            backend = "ruff"
        lint["backend"] = backend

        fallback = str(lint.get("fallback_backend", "ast")).strip().lower()
        if fallback not in {"none", "ruff", "pyflakes", "ast"}:
            fallback = "ast"
        lint["fallback_backend"] = fallback

        args_cfg = lint.get("args")
        if not isinstance(args_cfg, dict):
            args_cfg = {}
        ruff_args = args_cfg.get("ruff")
        pyflakes_args = args_cfg.get("pyflakes")
        args_cfg["ruff"] = [str(item) for item in ruff_args] if isinstance(ruff_args, list) else ["check", "--output-format", "json"]
        args_cfg["pyflakes"] = [str(item) for item in pyflakes_args] if isinstance(pyflakes_args, list) else []
        lint["args"] = args_cfg

        severity_overrides_raw = lint.get("severity_overrides")
        severity_overrides: dict[str, str] = {}
        if isinstance(severity_overrides_raw, dict):
            for raw_key, raw_value in severity_overrides_raw.items():
                key = str(raw_key or "").strip()
                if not key:
                    continue
                value = str(raw_value or "").strip().lower()
                if value not in {"error", "warning", "info", "hint"}:
                    continue
                severity_overrides[key] = value
        lint["severity_overrides"] = severity_overrides

        visuals = lint.get("visuals")
        if not isinstance(visuals, dict):
            visuals = {}
        visuals = deep_merge_defaults(visuals, default_ide_settings()["lint"]["visuals"])
        mode = str(visuals.get("mode") or "squiggle").strip().lower()
        if mode not in {"squiggle", "line", "both"}:
            mode = "squiggle"
        visuals["mode"] = mode
        visuals["error_color"] = str(visuals.get("error_color") or "#E35D6A").strip() or "#E35D6A"
        visuals["warning_color"] = str(visuals.get("warning_color") or "#D6A54A").strip() or "#D6A54A"
        visuals["info_color"] = str(visuals.get("info_color") or "#6AA1FF").strip() or "#6AA1FF"
        visuals["hint_color"] = str(visuals.get("hint_color") or "#8F9AA5").strip() or "#8F9AA5"
        try:
            visuals["squiggle_thickness"] = max(1, min(6, int(visuals.get("squiggle_thickness", 2))))
        except Exception:
            visuals["squiggle_thickness"] = 2
        try:
            visuals["line_alpha"] = max(0, min(255, int(visuals.get("line_alpha", 64))))
        except Exception:
            visuals["line_alpha"] = 64
        lint["visuals"] = visuals
        data["lint"] = lint

        completion = data.get("completion")
        if not isinstance(completion, dict):
            completion = {}
        completion = deep_merge_defaults(completion, default_ide_settings()["completion"])
        completion["enabled"] = bool(completion.get("enabled", True))
        completion["respect_excludes"] = bool(completion.get("respect_excludes", True))
        completion["auto_trigger"] = bool(completion.get("auto_trigger", True))
        completion["auto_trigger_after_dot"] = bool(completion.get("auto_trigger_after_dot", True))
        completion["auto_trigger_min_chars"] = max(1, min(10, int(completion.get("auto_trigger_min_chars", 2))))
        completion["debounce_ms"] = max(40, min(3000, int(completion.get("debounce_ms", 180))))
        backend = str(completion.get("backend", "jedi")).strip().lower()
        completion["backend"] = "jedi" if backend != "jedi" else backend
        completion["max_items"] = max(5, min(1000, int(completion.get("max_items", 500))))
        completion["case_sensitive"] = bool(completion.get("case_sensitive", False))
        completion["show_signatures"] = bool(completion.get("show_signatures", True))
        completion["show_right_label"] = bool(completion.get("show_right_label", True))
        completion["show_doc_tooltip"] = bool(completion.get("show_doc_tooltip", True))
        completion["doc_tooltip_delay_ms"] = max(120, min(1200, int(completion.get("doc_tooltip_delay_ms", 180))))
        data["completion"] = completion

        data["ai_assist"] = normalize_ai_settings(data.get("ai_assist"))

        github = data.get("github")
        if not isinstance(github, dict):
            github = {}
        github = deep_merge_defaults(github, default_ide_settings()["github"])
        github["username"] = str(github.get("username") or "").strip()
        github["use_token_for_git"] = bool(github.get("use_token_for_git", True))
        raw_destination = str(github.get("last_clone_destination") or "").strip()
        github["last_clone_destination"] = raw_destination or str(Path.home())
        mode = str(github.get("last_clone_mode") or "").strip().lower()
        if mode not in {"my_repos", "by_url"}:
            mode = "my_repos"
        github["last_clone_mode"] = mode
        github["last_clone_url"] = str(github.get("last_clone_url") or "").strip()
        data["github"] = github

        git_cfg = data.get("git")
        if not isinstance(git_cfg, dict):
            git_cfg = {}
        git_cfg = deep_merge_defaults(git_cfg, default_ide_settings()["git"])
        git_cfg["enable_file_tinting"] = bool(git_cfg.get("enable_file_tinting", True))
        git_cfg["tracked_clean_color"] = str(git_cfg.get("tracked_clean_color") or "#7fbf7f").strip() or "#7fbf7f"
        git_cfg["tracked_dirty_color"] = str(git_cfg.get("tracked_dirty_color") or "#e69f6b").strip() or "#e69f6b"
        git_cfg["untracked_color"] = str(git_cfg.get("untracked_color") or "#c8c8c8").strip() or "#c8c8c8"
        data["git"] = git_cfg

        editor_cfg = data.get("editor")
        if not isinstance(editor_cfg, dict):
            editor_cfg = {}
        editor_cfg = deep_merge_defaults(editor_cfg, default_ide_settings()["editor"])
        editor_cfg["background_color"] = str(editor_cfg.get("background_color") or "#252526").strip() or "#252526"
        editor_cfg["background_image_path"] = str(editor_cfg.get("background_image_path") or "").strip()
        editor_scale_mode = str(editor_cfg.get("background_image_scale_mode") or "stretch").strip().lower()
        if editor_scale_mode not in {"stretch", "fit_width", "fit_height", "tile"}:
            editor_scale_mode = "stretch"
        editor_cfg["background_image_scale_mode"] = editor_scale_mode
        try:
            editor_cfg["background_image_brightness"] = max(
                0,
                min(200, int(editor_cfg.get("background_image_brightness", 100))),
            )
        except Exception:
            editor_cfg["background_image_brightness"] = 100
        editor_cfg["background_tint_color"] = str(editor_cfg.get("background_tint_color") or "#000000").strip() or "#000000"
        try:
            editor_cfg["background_tint_strength"] = max(
                0,
                min(100, int(editor_cfg.get("background_tint_strength", 0))),
            )
        except Exception:
            editor_cfg["background_tint_strength"] = 0
        data["editor"] = editor_cfg

        file_dialog_cfg = data.get("file_dialog")
        if not isinstance(file_dialog_cfg, dict):
            file_dialog_cfg = {}
        file_dialog_cfg = deep_merge_defaults(file_dialog_cfg, default_ide_settings()["file_dialog"])
        file_dialog_cfg["background_image_path"] = str(file_dialog_cfg.get("background_image_path") or "").strip()
        dialog_scale_mode = str(file_dialog_cfg.get("background_scale_mode") or "stretch").strip().lower()
        if dialog_scale_mode not in {"stretch", "fit_width", "fit_height", "tile"}:
            dialog_scale_mode = "stretch"
        file_dialog_cfg["background_scale_mode"] = dialog_scale_mode
        try:
            file_dialog_cfg["background_brightness"] = max(
                0,
                min(200, int(file_dialog_cfg.get("background_brightness", 100))),
            )
        except Exception:
            file_dialog_cfg["background_brightness"] = 100
        file_dialog_cfg["tint_color"] = str(file_dialog_cfg.get("tint_color") or "#000000").strip() or "#000000"
        try:
            file_dialog_cfg["tint_strength"] = max(
                0,
                min(100, int(file_dialog_cfg.get("tint_strength", 0))),
            )
        except Exception:
            file_dialog_cfg["tint_strength"] = 0
        starred_paths_raw = file_dialog_cfg.get("starred_paths")
        clean_starred_paths: list[str] = []
        seen_starred_paths: set[str] = set()
        if isinstance(starred_paths_raw, list):
            for item in starred_paths_raw:
                text = str(item or "").strip()
                if not text:
                    continue
                try:
                    canonical = str(Path(text).expanduser().resolve())
                except Exception:
                    canonical = str(Path(text).expanduser())
                key = canonical.lower()
                if key in seen_starred_paths:
                    continue
                seen_starred_paths.add(key)
                clean_starred_paths.append(canonical)
        file_dialog_cfg["starred_paths"] = clean_starred_paths
        data["file_dialog"] = file_dialog_cfg

        defaults = data.get("defaults")
        if not isinstance(defaults, dict):
            defaults = {}
        defaults = deep_merge_defaults(defaults, default_ide_settings()["defaults"])
        defaults["name"] = str(defaults.get("name") or "My Python Project")
        defaults["interpreter"] = str(defaults.get("interpreter") or "python")
        data["defaults"] = defaults

        changed = data != before
        if changed:
            self.ide_store.dirty = True
        return changed

    def _extract_ide_keys_from_project(self) -> bool:
        """Keep project.json project-only by migrating known IDE keys to IDE scope."""
        project = self.project_store.data
        ide = self.ide_store.data
        moved_any = False

        for project_key, ide_key in PROJECT_TO_IDE_KEY_MAPPINGS:
            marker = object()
            value = dot_get(project, project_key, marker)
            if value is marker:
                continue
            existing = dot_get(ide, ide_key, marker)
            if isinstance(existing, dict) and isinstance(value, dict):
                dot_set(ide, ide_key, deep_merge_defaults(value, existing))
            else:
                dot_set(ide, ide_key, deepcopy(value))
            if dot_delete(project, project_key):
                moved_any = True
            self.ide_store.dirty = True

        if moved_any:
            self.project_store.dirty = True
        return moved_any
