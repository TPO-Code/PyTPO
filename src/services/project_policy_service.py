"""Pure project policy logic for interpreters, indexing exclusions, and explorer visibility."""

from __future__ import annotations

import fnmatch
import os


class ProjectPolicyService:
    def __init__(
        self,
        *,
        project_root: str,
        canonicalize,
        rel_to_project,
        path_has_prefix,
        resolve_path_from_project,
        resolve_path_from_project_no_symlink_resolve,
        normalize_rel,
    ) -> None:
        self.project_root = str(project_root)
        self._canonicalize = canonicalize
        self._rel_to_project = rel_to_project
        self._path_has_prefix = path_has_prefix
        self._resolve_path_from_project = resolve_path_from_project
        self._resolve_path_from_project_no_symlink_resolve = resolve_path_from_project_no_symlink_resolve
        self._normalize_rel = normalize_rel

    def _indexing_config(self, config: dict) -> dict:
        cfg = config.get("indexing", {}) if isinstance(config, dict) else {}
        return cfg if isinstance(cfg, dict) else {}

    def _explorer_config(self, config: dict) -> dict:
        cfg = config.get("explorer", {}) if isinstance(config, dict) else {}
        return cfg if isinstance(cfg, dict) else {}

    def _iter_directory_overrides(self, config: dict) -> list[dict]:
        interps = config.get("interpreters", {}) if isinstance(config, dict) else {}
        if not isinstance(interps, dict):
            return []
        entries = interps.get("by_directory", [])
        if not isinstance(entries, list):
            return []
        return [e for e in entries if isinstance(e, dict)]

    def _directory_entry_abs_path(self, entry: dict) -> str:
        return self._resolve_path_from_project(str(entry.get("path") or ""))

    def _resolve_interpreter_value(self, value) -> str:
        if not isinstance(value, str):
            return ""
        text = value.strip()
        if not text:
            return ""

        looks_like_path = (
            text.startswith(".")
            or text.startswith("/")
            or text.startswith("~")
            or os.sep in text
            or (os.altsep and os.altsep in text)
        )
        if looks_like_path:
            return self._resolve_path_from_project_no_symlink_resolve(text)
        return text

    def resolve_folder_policy(self, config: dict, path: str) -> dict:
        target = self._canonicalize(path)
        if not os.path.isdir(target):
            target = self._canonicalize(os.path.dirname(target))

        best_entry = None
        best_len = -1
        best_abs = ""

        for entry in self._iter_directory_overrides(config):
            entry_path = entry.get("path")
            if not isinstance(entry_path, str) or not entry_path.strip():
                continue
            abs_path = self._directory_entry_abs_path(entry)
            if not self._path_has_prefix(target, abs_path):
                continue
            if len(abs_path) > best_len:
                best_len = len(abs_path)
                best_entry = entry
                best_abs = abs_path

        policy = {
            "path": target,
            "relative_path": self._rel_to_project(target),
            "python": "",
            "exclude_from_indexing": False,
            "matched": False,
            "match_path": None,
        }

        if best_entry:
            policy["matched"] = True
            policy["match_path"] = best_abs
            policy["exclude_from_indexing"] = bool(best_entry.get("exclude_from_indexing", False))
            python_value = self._resolve_interpreter_value(best_entry.get("python"))
            if python_value:
                policy["python"] = python_value

        return policy

    def resolve_interpreter(self, config: dict, file_path: str) -> str:
        file_key = self._canonicalize(file_path)

        folder_policy = self.resolve_folder_policy(config, file_key)
        policy_interpreter = str(folder_policy.get("python") or "").strip()
        if policy_interpreter:
            return policy_interpreter

        interps = config.get("interpreters", {}) if isinstance(config, dict) else {}
        if not isinstance(interps, dict):
            interps = {}

        default_interp = self._resolve_interpreter_value(interps.get("default"))
        if default_interp:
            return default_interp

        legacy_interp = self._resolve_interpreter_value(config.get("interpreter") if isinstance(config, dict) else None)
        if legacy_interp:
            return legacy_interp

        return "python"

    def resolve_run_in(self, config: dict, file_path: str) -> str:
        run_cfg = config.get("run", {}) if isinstance(config, dict) else {}
        if not isinstance(run_cfg, dict):
            run_cfg = {}
        default_cwd = run_cfg.get("default_cwd", ".")
        run_in = self._resolve_path_from_project(str(default_cwd or "."))

        if os.path.isfile(run_in):
            run_in = self._canonicalize(os.path.dirname(run_in))

        if os.path.isdir(run_in):
            return run_in

        file_dir = self._canonicalize(os.path.dirname(self._canonicalize(file_path)))
        if os.path.isdir(file_dir):
            return file_dir

        return self._canonicalize(self.project_root)

    @staticmethod
    def pattern_has_glob(pattern: str) -> bool:
        return any(ch in str(pattern or "") for ch in ("*", "?", "[", "]"))

    def _matches_dir_patterns(self, rel_path: str, patterns: list[str]) -> bool:
        rel_norm = self._normalize_rel(rel_path)
        if rel_norm in ("", "."):
            return False
        segments = rel_norm.split("/")

        for raw_pattern in patterns:
            if not isinstance(raw_pattern, str):
                continue
            pattern = self._normalize_rel(raw_pattern)
            if not pattern:
                continue
            has_glob = self.pattern_has_glob(pattern)
            if "/" in pattern:
                if has_glob:
                    if fnmatch.fnmatchcase(rel_norm, pattern):
                        return True
                else:
                    if rel_norm == pattern or rel_norm.startswith(pattern + "/"):
                        return True
            else:
                if has_glob:
                    if any(fnmatch.fnmatchcase(segment, pattern) for segment in segments):
                        return True
                else:
                    if pattern in segments:
                        return True
        return False

    def _matches_file_patterns(self, rel_path: str, patterns: list[str]) -> bool:
        rel_norm = self._normalize_rel(rel_path)
        file_name = os.path.basename(rel_norm)
        for raw in patterns:
            if not isinstance(raw, str):
                continue
            pattern = self._normalize_rel(raw)
            if not pattern:
                continue
            has_glob = self.pattern_has_glob(pattern)
            if "/" in pattern:
                if has_glob:
                    if fnmatch.fnmatchcase(rel_norm, pattern):
                        return True
                else:
                    if rel_norm == pattern:
                        return True
                continue
            if has_glob:
                if fnmatch.fnmatchcase(file_name, pattern):
                    return True
                continue
            if rel_norm == pattern or file_name == pattern:
                return True
        return False

    def _matches_excluded_dir(self, config: dict, rel_path: str) -> bool:
        patterns = self._indexing_config(config).get("exclude_dirs", [])
        if not isinstance(patterns, list):
            return False
        return self._matches_dir_patterns(rel_path, patterns)

    def is_file_explicitly_excluded(self, config: dict, file_path: str) -> bool:
        rel = self._rel_to_project(file_path)
        if rel == file_path:
            return False
        rel_norm = self._normalize_rel(rel)
        items = self._indexing_config(config).get("exclude_files", [])
        if not isinstance(items, list):
            return False
        file_name = os.path.basename(rel_norm)
        for raw in items:
            if not isinstance(raw, str):
                continue
            pattern = self._normalize_rel(raw)
            if not pattern or self.pattern_has_glob(pattern):
                continue
            if "/" in pattern:
                if rel_norm == pattern:
                    return True
                continue
            if rel_norm == pattern or file_name == pattern:
                return True
        return False

    def _matches_excluded_file_pattern(self, config: dict, file_path: str) -> bool:
        rel = self._rel_to_project(file_path)
        if rel == file_path:
            return False
        items = self._indexing_config(config).get("exclude_files", [])
        if not isinstance(items, list):
            return False
        return self._matches_file_patterns(rel, items)

    def is_path_excluded(self, config: dict, path: str, *, for_feature: str = "indexing") -> bool:
        feature = str(for_feature or "indexing").strip().lower()
        if feature == "lint":
            lint_cfg = config.get("lint", {}) if isinstance(config, dict) else {}
            if isinstance(lint_cfg, dict) and not bool(lint_cfg.get("respect_excludes", True)):
                return False
        if feature == "completion":
            comp_cfg = config.get("completion", {}) if isinstance(config, dict) else {}
            if isinstance(comp_cfg, dict) and not bool(comp_cfg.get("respect_excludes", True)):
                return False

        cpath = self._canonicalize(path)
        if not self._path_has_prefix(cpath, self.project_root):
            return False

        folder_policy = self.resolve_folder_policy(config, cpath)
        if bool(folder_policy.get("exclude_from_indexing", False)):
            return True

        rel = self._rel_to_project(cpath)
        if rel == cpath:
            return False

        rel_norm = self._normalize_rel(rel)
        if self._matches_excluded_dir(config, rel_norm):
            return True

        if not os.path.isdir(cpath) and self._matches_excluded_file_pattern(config, cpath):
            return True

        return False

    def is_tree_path_excluded(self, config: dict, path: str, is_dir: bool, *, no_project_mode: bool) -> bool:
        cpath = self._canonicalize(path)
        if cpath == self.project_root:
            return False
        if no_project_mode:
            return True
        rel = self._rel_to_project(cpath)
        if rel == cpath:
            return False

        explorer_cfg = self._explorer_config(config)
        explorer_exclude_dirs = explorer_cfg.get("exclude_dirs", [])
        explorer_exclude_files = explorer_cfg.get("exclude_files", [])
        if not isinstance(explorer_exclude_dirs, list):
            explorer_exclude_dirs = []
        if not isinstance(explorer_exclude_files, list):
            explorer_exclude_files = []

        if is_dir:
            if self._matches_dir_patterns(rel, explorer_exclude_dirs):
                return True
        else:
            if self._matches_file_patterns(rel, explorer_exclude_files):
                return True

        if not bool(explorer_cfg.get("hide_indexing_excluded", True)):
            return False

        if is_dir:
            return self._matches_excluded_dir(config, rel)
        return self._matches_excluded_file_pattern(config, cpath)

    def normalize_folder_store_path(self, folder_path: str) -> str:
        rel = self._rel_to_project(folder_path)
        if rel != folder_path:
            return "." if rel in ("", ".") else self._normalize_rel(rel)
        return self._canonicalize(folder_path)

    def find_folder_override(self, config: dict, folder_path: str) -> tuple[int, dict | None]:
        target = self._canonicalize(folder_path)
        entries = self._iter_directory_overrides(config)
        for idx, entry in enumerate(entries):
            if self._canonicalize(self._directory_entry_abs_path(entry)) == target:
                return idx, entry
        return -1, None

    def directory_entries_ref(self, config: dict) -> list:
        interps = config.setdefault("interpreters", {})
        if not isinstance(interps, dict):
            interps = {}
            config["interpreters"] = interps
        entries = interps.setdefault("by_directory", [])
        if not isinstance(entries, list):
            entries = []
            interps["by_directory"] = entries
        return entries

    def set_folder_interpreter(self, config: dict, folder_path: str, python_path: str) -> bool:
        folder = self._canonicalize(folder_path)
        py_raw = str(python_path).strip()
        if not py_raw:
            return False

        entries = self.directory_entries_ref(config)
        idx = -1
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            if self._canonicalize(self._directory_entry_abs_path(entry)) == folder:
                idx = i
                break

        if idx >= 0:
            entry = entries[idx]
            entry["python"] = py_raw
        else:
            entries.append(
                {
                    "path": self.normalize_folder_store_path(folder),
                    "python": py_raw,
                    "exclude_from_indexing": False,
                }
            )
        return True

    def clear_folder_interpreter(self, config: dict, folder_path: str) -> bool:
        folder = self._canonicalize(folder_path)
        entries = self.directory_entries_ref(config)

        for i, entry in enumerate(list(entries)):
            if not isinstance(entry, dict):
                continue
            if self._canonicalize(self._directory_entry_abs_path(entry)) != folder:
                continue
            entry.pop("python", None)
            if not bool(entry.get("exclude_from_indexing", False)):
                entries.pop(i)
            return True
        return False

    def set_folder_excluded(self, config: dict, folder_path: str, excluded: bool) -> bool:
        folder = self._canonicalize(folder_path)
        entries = self.directory_entries_ref(config)

        for i, entry in enumerate(list(entries)):
            if not isinstance(entry, dict):
                continue
            if self._canonicalize(self._directory_entry_abs_path(entry)) != folder:
                continue

            if excluded:
                entry["exclude_from_indexing"] = True
            else:
                entry["exclude_from_indexing"] = False
                if not str(entry.get("python") or "").strip():
                    entries.pop(i)
            return True

        if excluded:
            entries.append(
                {
                    "path": self.normalize_folder_store_path(folder),
                    "exclude_from_indexing": True,
                }
            )
            return True
        return False

    def set_file_excluded(self, config: dict, file_path: str, excluded: bool) -> bool:
        cpath = self._canonicalize(file_path)
        rel = self._rel_to_project(cpath)
        if rel == cpath:
            raise ValueError("Only project files can be toggled.")

        rel_norm = self._normalize_rel(rel)
        indexing = config.setdefault("indexing", {})
        if not isinstance(indexing, dict):
            indexing = {}
            config["indexing"] = indexing
        entries = indexing.setdefault("exclude_files", [])
        if not isinstance(entries, list):
            entries = []
            indexing["exclude_files"] = entries

        existing = {self._normalize_rel(v) for v in entries if isinstance(v, str)}
        if excluded:
            existing.add(rel_norm)
        else:
            existing.discard(rel_norm)

        indexing["exclude_files"] = sorted(existing)
        return True
