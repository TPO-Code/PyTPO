from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path

from src.git.git_service import GitService, GitServiceError
from src.git.github_client import GitHubClient, GitHubClientError


@dataclass(slots=True)
class GitHubShareRequest:
    project_root: str
    token: str
    repo_name: str
    description: str
    private: bool
    commit_message: str
    selected_files: list[str]
    replace_existing_origin: bool = False


@dataclass(slots=True)
class GitHubShareResult:
    repo_root: str
    repo_full_name: str
    html_url: str
    clone_url: str
    remote_action: str
    commit_output: str
    push_output: str


class GitHubShareError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "share_failed") -> None:
        super().__init__(message)
        self.kind = kind


_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class GitHubShareService:
    def __init__(self, *, git_service: GitService | None = None) -> None:
        self._git = git_service or GitService()

    def list_project_files(
        self,
        project_root: str,
        *,
        exclude_dirs: list[str] | None = None,
        exclude_files: list[str] | None = None,
    ) -> list[str]:
        root = self._canonical_dir(project_root)
        blocked_dirs = [".git", *self._normalize_name_list(exclude_dirs)]
        blocked_files = self._normalize_name_list(exclude_files)

        output: list[str] = []
        for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
            rel_base = os.path.relpath(base, root).replace("\\", "/")
            rel_base = "" if rel_base in {"", "."} else rel_base

            kept_dirs: list[str] = []
            for name in dirs:
                rel_dir = name if not rel_base else f"{rel_base}/{name}"
                if self._matches_dir_pattern(rel_dir, blocked_dirs):
                    continue
                kept_dirs.append(name)
            dirs[:] = kept_dirs

            for name in files:
                rel_file = name if not rel_base else f"{rel_base}/{name}"
                if self._matches_file_pattern(rel_file, blocked_files):
                    continue
                abs_path = self._canonical(os.path.join(base, name))
                if not self._is_within(root, abs_path):
                    continue
                rel = os.path.relpath(abs_path, root).replace("\\", "/")
                if rel in {"", "."}:
                    continue
                output.append(rel)
        output.sort(key=str.lower)
        return output

    def share_to_github(self, req: GitHubShareRequest) -> GitHubShareResult:
        token = str(req.token or "").strip()
        if not token:
            raise GitHubShareError("No GitHub token is configured.", kind="no_token")

        project_root = self._canonical_dir(req.project_root)
        repo_name = str(req.repo_name or "").strip()
        if not _REPO_NAME_RE.fullmatch(repo_name):
            raise GitHubShareError(
                "Repository name is invalid. Use letters, numbers, '.', '_' or '-'.",
                kind="validation",
            )

        commit_message = str(req.commit_message or "").strip()
        if not commit_message:
            raise GitHubShareError("Commit message is required.", kind="validation")

        selected = self._normalize_selected_files(project_root, req.selected_files)
        if not selected:
            raise GitHubShareError("Select at least one file to include.", kind="no_files")

        try:
            repo_root = self._git.ensure_repo_initialized(project_root)
        except GitServiceError as exc:
            raise GitHubShareError(str(exc), kind=str(exc.kind or "git_error")) from None

        client = self._build_client(token)
        owner = self._resolve_username(client)
        expected_origin = f"https://github.com/{owner}/{repo_name}.git"
        self._validate_origin_state(
            repo_root=repo_root,
            expected_origin=expected_origin,
            replace_existing=bool(req.replace_existing_origin),
        )

        created = self._create_remote_repo(
            client=client,
            repo_name=repo_name,
            description=str(req.description or "").strip(),
            private=bool(req.private),
        )

        try:
            remote_cfg = self._git.configure_remote(
                repo_root,
                remote_name="origin",
                remote_url=created.clone_url,
                replace_existing=bool(req.replace_existing_origin),
            )
        except GitServiceError as exc:
            raise GitHubShareError(str(exc), kind=str(exc.kind or "git_error")) from None

        rel_for_repo = self._to_repo_relative_paths(
            project_root=project_root,
            repo_root=repo_root,
            selected=selected,
        )
        if not rel_for_repo:
            raise GitHubShareError("No valid files selected for commit.", kind="no_files")

        try:
            commit_output = self._git.commit_files(repo_root, rel_for_repo, commit_message)
        except GitServiceError as exc:
            raise GitHubShareError(str(exc), kind=str(exc.kind or "git_error")) from None

        try:
            push_output = self._git.push_head_to_origin(repo_root, remote_name="origin", set_upstream=True)
        except GitServiceError as exc:
            raise GitHubShareError(
                "Repository created and local commit succeeded, but push failed. "
                "Remote 'origin' is configured. Fix auth/network and retry Git -> Push.",
                kind="push_failed_after_create",
            ) from exc

        return GitHubShareResult(
            repo_root=repo_root,
            repo_full_name=created.full_name,
            html_url=created.html_url,
            clone_url=created.clone_url,
            remote_action=remote_cfg.action,
            commit_output=str(commit_output or "").strip(),
            push_output=str(push_output or "").strip(),
        )

    def _build_client(self, token: str) -> GitHubClient:
        try:
            return GitHubClient(token)
        except GitHubClientError as exc:
            raise GitHubShareError(str(exc), kind=str(exc.kind or "github_error")) from None

    @staticmethod
    def _resolve_username(client: GitHubClient) -> str:
        try:
            return client.test_connection()
        except GitHubClientError as exc:
            raise GitHubShareError(str(exc), kind=str(exc.kind or "github_error")) from None

    @staticmethod
    def _create_remote_repo(
        *,
        client: GitHubClient,
        repo_name: str,
        description: str,
        private: bool,
    ):
        try:
            return client.create_repo(name=repo_name, description=description, private=private)
        except GitHubClientError as exc:
            if str(exc.kind or "") == "already_exists":
                raise GitHubShareError(
                    "A repository with that name already exists on your GitHub account.",
                    kind="repo_exists",
                ) from None
            raise GitHubShareError(str(exc), kind=str(exc.kind or "github_error")) from None

    def _validate_origin_state(self, *, repo_root: str, expected_origin: str, replace_existing: bool) -> None:
        try:
            current = self._git.get_remote_url(repo_root, "origin")
        except GitServiceError as exc:
            raise GitHubShareError(str(exc), kind=str(exc.kind or "git_error")) from None
        if not current:
            return
        if self._normalize_remote_url(current) == self._normalize_remote_url(expected_origin):
            return
        if replace_existing:
            return
        raise GitHubShareError(
            "Remote 'origin' already exists with a different URL.",
            kind="origin_exists",
        )

    def _normalize_selected_files(self, project_root: str, values: list[str]) -> list[str]:
        root = self._canonical(project_root)
        output: list[str] = []
        seen: set[str] = set()
        for raw in values:
            text = str(raw or "").strip().replace("\\", "/")
            if not text:
                continue
            if os.path.isabs(text):
                abs_path = self._canonical(text)
                if not self._is_within(root, abs_path):
                    continue
                rel = os.path.relpath(abs_path, root).replace("\\", "/")
            else:
                rel = os.path.normpath(text).replace("\\", "/").lstrip("./")
            if not rel or rel in {".", ".."} or rel.startswith("../"):
                continue
            abs_path = self._canonical(os.path.join(root, rel))
            if not self._is_within(root, abs_path):
                continue
            if not os.path.isfile(abs_path):
                continue
            dedupe = rel.lower()
            if dedupe in seen:
                continue
            seen.add(dedupe)
            output.append(rel)
        output.sort(key=str.lower)
        return output

    def _to_repo_relative_paths(self, *, project_root: str, repo_root: str, selected: list[str]) -> list[str]:
        project = self._canonical(project_root)
        repo = self._canonical(repo_root)
        output: list[str] = []
        seen: set[str] = set()
        for rel in selected:
            abs_path = self._canonical(os.path.join(project, rel))
            if not self._is_within(repo, abs_path):
                continue
            repo_rel = os.path.relpath(abs_path, repo).replace("\\", "/")
            if repo_rel in {"", "."} or repo_rel.startswith("../"):
                continue
            dedupe = repo_rel.lower()
            if dedupe in seen:
                continue
            seen.add(dedupe)
            output.append(repo_rel)
        output.sort(key=str.lower)
        return output

    @staticmethod
    def _normalize_name_list(values: list[str] | None) -> list[str]:
        if not isinstance(values, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in values:
            text = str(item or "").strip().replace("\\", "/")
            if text:
                key = text.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(text)
        return out

    @staticmethod
    def _has_glob(pattern: str) -> bool:
        return any(ch in str(pattern or "") for ch in ("*", "?", "[", "]"))

    def _matches_dir_pattern(self, rel_dir: str, patterns: list[str]) -> bool:
        rel_norm = rel_dir.strip("/").replace("\\", "/")
        if not rel_norm:
            return False
        segments = rel_norm.split("/")
        for raw in patterns:
            pattern = str(raw or "").strip().strip("/").replace("\\", "/")
            if not pattern:
                continue
            has_glob = self._has_glob(pattern)
            if "/" in pattern:
                if has_glob:
                    if fnmatch.fnmatchcase(rel_norm, pattern):
                        return True
                elif rel_norm == pattern or rel_norm.startswith(pattern + "/"):
                    return True
                continue
            if has_glob:
                if any(fnmatch.fnmatchcase(segment, pattern) for segment in segments):
                    return True
                continue
            if pattern in segments:
                return True
        return False

    def _matches_file_pattern(self, rel_file: str, patterns: list[str]) -> bool:
        rel_norm = rel_file.strip("/").replace("\\", "/")
        if not rel_norm:
            return False
        name = os.path.basename(rel_norm)
        for raw in patterns:
            pattern = str(raw or "").strip().replace("\\", "/")
            if not pattern:
                continue
            has_glob = self._has_glob(pattern)
            if "/" in pattern:
                if has_glob:
                    if fnmatch.fnmatchcase(rel_norm, pattern):
                        return True
                elif rel_norm == pattern:
                    return True
                continue
            if has_glob:
                if fnmatch.fnmatchcase(name, pattern):
                    return True
                continue
            if rel_norm == pattern or name == pattern:
                return True
        return False

    @staticmethod
    def _normalize_remote_url(url: str) -> str:
        text = str(url or "").strip().rstrip("/")
        if text.lower().endswith(".git"):
            text = text[:-4]
        return text.lower()

    def _canonical_dir(self, path: str) -> str:
        root = self._canonical(path)
        if not root or not os.path.isdir(root):
            raise GitHubShareError("Project folder is not available.", kind="invalid_project")
        return root

    @staticmethod
    def _canonical(path: str) -> str:
        try:
            return str(Path(path).expanduser().resolve())
        except Exception:
            return os.path.abspath(os.path.expanduser(path))

    def _is_within(self, root: str, target: str) -> bool:
        try:
            return os.path.commonpath([self._canonical(root), self._canonical(target)]) == self._canonical(root)
        except Exception:
            return False
