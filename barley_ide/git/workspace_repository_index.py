from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class WorkspaceRepositoryEntry:
    root_path: str
    parent_repo_root: str | None
    is_workspace_root: bool = False
    repo_kind: str = "repo"


class WorkspaceRepositoryIndex:
    def __init__(
        self,
        workspace_root: str,
        entries: list[WorkspaceRepositoryEntry],
        *,
        canonicalize: Callable[[str], str] | None = None,
    ) -> None:
        self._canonicalize = canonicalize or self._default_canonicalize
        self.workspace_root = self._canonicalize(workspace_root)
        ordered = sorted(
            entries,
            key=lambda entry: (len(self._canonicalize(entry.root_path)), self._canonicalize(entry.root_path).lower()),
        )
        self._entries_by_root: dict[str, WorkspaceRepositoryEntry] = {}
        for entry in ordered:
            root = self._canonicalize(entry.root_path)
            self._entries_by_root[root] = WorkspaceRepositoryEntry(
                root_path=root,
                parent_repo_root=self._canonicalize(entry.parent_repo_root) if entry.parent_repo_root else None,
                is_workspace_root=bool(entry.is_workspace_root),
                repo_kind=str(getattr(entry, "repo_kind", "repo") or "repo").strip() or "repo",
            )
        self._sorted_roots = sorted(self._entries_by_root.keys(), key=lambda value: (-len(value), value.lower()))
        self.workspace_repo_root = self.workspace_root if self.workspace_root in self._entries_by_root else None

    @classmethod
    def discover(
        cls,
        workspace_root: str,
        *,
        canonicalize: Callable[[str], str] | None = None,
    ) -> WorkspaceRepositoryIndex:
        canon = canonicalize or cls._default_canonicalize
        root = canon(workspace_root)
        entries: list[WorkspaceRepositoryEntry] = []
        workspace_kind = cls._git_marker_kind(root)
        if workspace_kind:
            entries.append(
                WorkspaceRepositoryEntry(
                    root_path=root,
                    parent_repo_root=None,
                    is_workspace_root=True,
                    repo_kind=workspace_kind,
                )
            )

        for current_root, dir_names, _file_names in os.walk(root, topdown=True, followlinks=False):
            current = canon(current_root)
            dir_names[:] = [name for name in dir_names if name != ".git"]
            marker_kind = cls._git_marker_kind(current) if current != root else None
            if marker_kind:
                parent_repo_root = cls._nearest_parent_repo(current, root, entries, canonicalize=canon)
                entries.append(
                    WorkspaceRepositoryEntry(
                        root_path=current,
                        parent_repo_root=parent_repo_root,
                        is_workspace_root=False,
                        repo_kind=marker_kind,
                    )
                )
                dir_names[:] = []

        return cls(root, entries, canonicalize=canon)

    def repo_roots(self) -> list[str]:
        return list(self._entries_by_root.keys())

    def repository_count(self) -> int:
        return len(self._entries_by_root)

    def has_repositories(self) -> bool:
        return bool(self._entries_by_root)

    def structural_key(self) -> tuple[str, tuple[tuple[str, str | None, bool, str], ...]]:
        entries = tuple(
            (
                entry.root_path,
                entry.parent_repo_root,
                bool(entry.is_workspace_root),
                str(entry.repo_kind or "repo"),
            )
            for entry in sorted(
                self._entries_by_root.values(),
                key=lambda item: (len(item.root_path), item.root_path.lower()),
            )
        )
        return self.workspace_root, entries

    def entry_for_root(self, repo_root: str) -> WorkspaceRepositoryEntry | None:
        return self._entries_by_root.get(self._canonicalize(repo_root))

    def is_repo_root(self, path: str) -> bool:
        return self._canonicalize(path) in self._entries_by_root

    def deepest_repo_for_path(self, path: str) -> str | None:
        cpath = self._canonicalize(path)
        if not self._is_within(self.workspace_root, cpath):
            return None
        for root in self._sorted_roots:
            if cpath == root or cpath.startswith(root + os.sep):
                return root
        return None

    def child_repo_roots(self, repo_root: str) -> list[str]:
        root = self._canonicalize(repo_root)
        child_roots = [
            entry.root_path
            for entry in self._entries_by_root.values()
            if entry.parent_repo_root == root and entry.root_path != root
        ]
        return sorted(child_roots, key=lambda value: (len(value), value.lower()))

    def has_child_repositories(self, repo_root: str) -> bool:
        return bool(self.child_repo_roots(repo_root))

    def path_is_owned_by_repo(self, path: str, repo_root: str) -> bool:
        cpath = self._canonicalize(path)
        root = self._canonicalize(repo_root)
        if not self._is_within(root, cpath):
            return False
        return self.deepest_repo_for_path(cpath) == root

    def repo_for_project_scope(self) -> str | None:
        if self.workspace_repo_root:
            return self.workspace_repo_root
        repo_roots = self.repo_roots()
        if len(repo_roots) == 1:
            return repo_roots[0]
        return None

    def _is_within(self, ancestor: str, path: str) -> bool:
        try:
            return os.path.commonpath([ancestor, path]) == ancestor
        except Exception:
            return False

    @staticmethod
    def _has_git_marker(path: str) -> bool:
        return WorkspaceRepositoryIndex._git_marker_kind(path) is not None

    @staticmethod
    def _git_marker_kind(path: str) -> str | None:
        git_path = os.path.join(path, ".git")
        if os.path.isdir(git_path):
            return "repo"
        if os.path.isfile(git_path):
            return "linked"
        return None

    @staticmethod
    def _nearest_parent_repo(
        path: str,
        workspace_root: str,
        entries: list[WorkspaceRepositoryEntry],
        *,
        canonicalize: Callable[[str], str],
    ) -> str | None:
        known_roots = {canonicalize(entry.root_path) for entry in entries}
        parent = canonicalize(os.path.dirname(path))
        root = canonicalize(workspace_root)
        while parent and parent != path:
            if parent in known_roots:
                return parent
            if parent == root:
                break
            next_parent = canonicalize(os.path.dirname(parent))
            if next_parent == parent:
                break
            parent = next_parent
        return root if root in known_roots else None

    @staticmethod
    def _default_canonicalize(path: str) -> str:
        try:
            return os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        except Exception:
            return os.path.abspath(os.path.expanduser(path))
