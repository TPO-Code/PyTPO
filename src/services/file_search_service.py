"""Disk-backed find/replace helpers used by Find in Files."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable

from . import file_io


@dataclass(frozen=True, slots=True)
class SearchMatch:
    file_path: str
    line: int
    column: int
    preview: str

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "line": self.line,
            "column": self.column,
            "preview": self.preview,
        }


@dataclass(frozen=True, slots=True)
class ReplaceResult:
    changed_files: int
    replacements_total: int
    changed_paths: list[str]
    updated_text_by_path: dict[str, str]


def iter_indexable_python_files(
    project_root: str,
    *,
    canonicalize: Callable[[str], str],
    path_has_prefix: Callable[[str, str], bool],
    is_path_excluded: Callable[[str], bool],
    follow_symlinks: bool,
) -> list[str]:
    files: list[str] = []
    root = canonicalize(project_root)
    for walk_root, dirnames, filenames in os.walk(root, topdown=True, followlinks=follow_symlinks):
        root_path = canonicalize(walk_root)
        if not path_has_prefix(root_path, root):
            dirnames[:] = []
            continue

        kept_dirs: list[str] = []
        for dirname in sorted(dirnames):
            dpath = canonicalize(os.path.join(root_path, dirname))
            if not path_has_prefix(dpath, root):
                continue
            if is_path_excluded(dpath):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            fpath = canonicalize(os.path.join(root_path, filename))
            if not path_has_prefix(fpath, root):
                continue
            if is_path_excluded(fpath):
                continue
            files.append(fpath)
    return files


def search_indexed_files(
    pattern: re.Pattern[str],
    targets: list[str],
    *,
    max_results: int = 20000,
) -> list[SearchMatch]:
    results: list[SearchMatch] = []
    for file_path in targets:
        try:
            text = file_io.read_text(file_path, encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line_number, line_text in enumerate(text.splitlines(), start=1):
            for match in pattern.finditer(line_text):
                start = int(match.start())
                end = int(match.end())
                if end <= start:
                    continue
                results.append(
                    SearchMatch(
                        file_path=file_path,
                        line=line_number,
                        column=start + 1,
                        preview=line_text.strip()[:320],
                    )
                )
                if len(results) >= max_results:
                    return results
    return results


def replace_in_indexed_files(
    pattern: re.Pattern[str],
    replace_text: str,
    targets: list[str],
) -> ReplaceResult:
    replacements_total = 0
    changed_paths: list[str] = []
    updated_text_by_path: dict[str, str] = {}
    for file_path in targets:
        try:
            text = file_io.read_text(file_path, encoding="utf-8", errors="ignore")
        except Exception:
            continue
        new_text, replace_count = pattern.subn(replace_text, text)
        if replace_count <= 0 or new_text == text:
            continue
        try:
            file_io.write_text(file_path, new_text, encoding="utf-8")
        except Exception:
            continue
        replacements_total += int(replace_count)
        changed_paths.append(file_path)
        updated_text_by_path[file_path] = new_text

    return ReplaceResult(
        changed_files=len(changed_paths),
        replacements_total=replacements_total,
        changed_paths=changed_paths,
        updated_text_by_path=updated_text_by_path,
    )
