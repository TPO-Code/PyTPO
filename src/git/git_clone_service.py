from __future__ import annotations

import os
import re
import shutil
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.git.auth_bridge import GitAuthBridge, GitCommandRunner, GitRunError, sanitize_git_text
from src.git.github_auth import GitHubAuthStore


class GitCloneError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "clone_failed") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(slots=True)
class ParsedRepoUrl:
    normalized_url: str
    folder_name: str


_SCP_LIKE_RE = re.compile(r"^(?:(?P<user>[^@\s/:]+)@)?(?P<host>[^:\s/]+):(?P<path>[^:\s].+)$")


def sanitize_repo_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    if "://" in text:
        try:
            parsed = urllib.parse.urlsplit(text)
        except Exception:
            return text
        host = parsed.netloc.rsplit("@", 1)[-1]
        path = parsed.path.rstrip("/")
        return urllib.parse.urlunsplit((parsed.scheme, host, path, "", ""))
    match = _SCP_LIKE_RE.match(text)
    if match is None:
        return text
    user = match.group("user")
    host = str(match.group("host") or "").strip()
    path = str(match.group("path") or "").strip()
    if user:
        return f"{user}@{host}:{path}"
    return f"{host}:{path}"


def parse_repo_url(url: str) -> ParsedRepoUrl:
    text = str(url or "").strip()
    if not text:
        raise GitCloneError("Invalid repository URL.", kind="invalid_url")

    if "://" in text:
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme not in {"https", "ssh", "git", "http"}:
            raise GitCloneError("Invalid repository URL.", kind="invalid_url")
        if not parsed.netloc:
            raise GitCloneError("Invalid repository URL.", kind="invalid_url")
        clean_path = parsed.path.rstrip("/")
        folder = _derive_folder_name_from_repo_path(clean_path)
        normalized = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, clean_path, "", ""))
        return ParsedRepoUrl(normalized_url=normalized, folder_name=folder)

    match = _SCP_LIKE_RE.match(text)
    if match is None:
        raise GitCloneError("Invalid repository URL.", kind="invalid_url")
    raw_path = str(match.group("path") or "").strip()
    folder = _derive_folder_name_from_repo_path(raw_path)
    user = str(match.group("user") or "").strip()
    host = str(match.group("host") or "").strip()
    normalized = f"{user + '@' if user else ''}{host}:{raw_path}"
    return ParsedRepoUrl(normalized_url=normalized, folder_name=folder)


def _derive_folder_name_from_repo_path(path_text: str) -> str:
    path = str(path_text or "").strip().strip("/")
    if not path:
        raise GitCloneError("Invalid repository URL.", kind="invalid_url")
    segment = path.split("/")[-1].strip()
    if not segment:
        raise GitCloneError("Invalid repository URL.", kind="invalid_url")
    if segment.lower().endswith(".git"):
        segment = segment[:-4]
    if not segment or segment in {".", ".."}:
        raise GitCloneError("Invalid repository URL.", kind="invalid_url")
    if any(ch in segment for ch in "\\/:*?\"<>|"):
        raise GitCloneError("Invalid repository URL.", kind="invalid_url")
    return segment


class GitCloneService:
    def __init__(
        self,
        *,
        ide_app_dir: str | Path | None = None,
        github_token_provider: Callable[[], str | None] | None = None,
        use_token_for_git_provider: Callable[[], bool] | None = None,
        command_timeout_seconds: int = 900,
    ) -> None:
        self._auth_store = GitHubAuthStore(ide_app_dir) if ide_app_dir is not None else None
        self._github_token_provider = github_token_provider
        self._use_token_for_git_provider = use_token_for_git_provider
        self._default_timeout_seconds = max(30, int(command_timeout_seconds))

    def clone(
        self,
        *,
        clone_url: str,
        destination_dir: str | Path,
        repo_name: str,
        token: str | None = None,
        default_branch: str | None = None,
    ) -> str:
        git_bin = shutil.which("git")
        if not git_bin:
            raise GitCloneError("Git is not installed or not in PATH.", kind="git_not_installed")

        url = str(clone_url or "").strip()
        if not url:
            raise GitCloneError("Repository clone URL is missing.", kind="invalid_request")

        name = str(repo_name or "").strip()
        if not name:
            raise GitCloneError("Repository name is missing.", kind="invalid_request")

        base_dir = Path(destination_dir).expanduser()
        if base_dir.exists() and not base_dir.is_dir():
            raise GitCloneError("Destination path is not a folder.", kind="invalid_destination")
        base_dir.mkdir(parents=True, exist_ok=True)

        target_path = (base_dir / name).resolve()
        if target_path.exists():
            raise GitCloneError(
                f"Destination already exists: {target_path}",
                kind="destination_exists",
            )

        command = [git_bin, "clone", "--progress"]
        branch = str(default_branch or "").strip()
        if branch:
            command.extend(["--branch", branch, "--single-branch"])
        command.extend([url, name])

        bridge = GitAuthBridge(
            token_provider=lambda: self._github_token(override=token),
            enabled_provider=self._use_token_for_git,
        )
        runner = GitCommandRunner(
            auth_bridge=bridge,
            default_timeout_seconds=self._default_timeout_seconds,
        )

        try:
            proc = runner.run(
                git_bin=git_bin,
                cwd=str(base_dir),
                args=command[1:],
                timeout_seconds=self._default_timeout_seconds,
                auth_url_hint=url,
            )
        except GitRunError as exc:
            if exc.kind == "timeout":
                raise GitCloneError("Git clone timed out.", kind="network_error") from exc
            if exc.kind == "git_not_installed":
                raise GitCloneError("Failed to launch git clone.", kind="git_not_installed") from exc
            raise GitCloneError(str(exc), kind="clone_failed") from exc

        if proc.returncode == 0:
            return str(target_path)

        err_detail = self._pick_error_line(proc.stderr, proc.stdout)
        raise GitCloneError(self._map_clone_failure(err_detail), kind=self._map_failure_kind(err_detail))

    def _github_token(self, *, override: str | None = None) -> str | None:
        text = str(override or "").strip()
        if text:
            return text
        if self._github_token_provider is not None:
            try:
                token = self._github_token_provider()
                value = str(token or "").strip()
                return value or None
            except Exception:
                return None
        if self._auth_store is None:
            return None
        try:
            token = self._auth_store.get()
        except Exception:
            return None
        value = str(token or "").strip()
        return value or None

    def _use_token_for_git(self) -> bool:
        if self._use_token_for_git_provider is not None:
            try:
                return bool(self._use_token_for_git_provider())
            except Exception:
                return False
        return True

    @staticmethod
    def _pick_error_line(stderr: str, stdout: str) -> str:
        merged = sanitize_git_text("\n".join([str(stderr or ""), str(stdout or "")]).strip())
        if not merged:
            return "git clone failed."
        lines = [str(line).strip() for line in merged.splitlines() if str(line).strip()]
        for line in lines:
            low = line.lower()
            if "fatal:" in low or "error:" in low or "failed" in low:
                return line
        for line in lines:
            if "not found" in line.lower() or "unable to access" in line.lower():
                return line
        if lines:
            return lines[-1]
        return "git clone failed."

    @staticmethod
    def _map_failure_kind(detail: str) -> str:
        text = str(detail or "").lower()
        if "already exists" in text and "not an empty directory" in text:
            return "destination_exists"
        if "authentication failed" in text:
            return "auth_failed"
        if "could not read username for" in text:
            return "auth_failed"
        if "permission denied (publickey)" in text:
            return "auth_failed"
        if "repository not found" in text:
            return "repo_not_found"
        if "access denied" in text:
            return "repo_not_found"
        if "not found" in text and "repository" in text:
            return "repo_not_found"
        if "terminal prompts disabled" in text:
            return "auth_failed"
        if "could not resolve host" in text:
            return "network_error"
        if "couldn't connect to server" in text:
            return "network_error"
        if "connection timed out" in text:
            return "network_error"
        if "unable to access" in text and "403" in text:
            return "auth_failed"
        if "unable to access" in text and "401" in text:
            return "auth_failed"
        return "clone_failed"

    def _map_clone_failure(self, detail: str) -> str:
        kind = self._map_failure_kind(detail)
        if kind == "destination_exists":
            return "Destination already exists and is not empty."
        if kind == "auth_failed":
            return "Clone authentication failed. Verify token permissions and Git transport bridge setting."
        if kind == "repo_not_found":
            return "Repository not found or access denied."
        if kind == "network_error":
            return "Network error while cloning repository."
        return "Failed to clone repository."
