from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Callable


@dataclass(slots=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    latest_tag: str
    update_available: bool
    repo_slug: str
    release_url: str
    release_title: str
    release_notes: str
    published_at: str


@dataclass(slots=True)
class UpdateApplyResult:
    repo_root: str
    branch: str
    updated: bool
    pull_output: str
    uv_sync_output: str


class UpdateServiceError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "update_error") -> None:
        super().__init__(message)
        self.kind = kind


_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_TAG_VERSION_RE = re.compile(r"v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")


class UpdateService:
    def __init__(
        self,
        *,
        app_root: str | Path,
        github_token_provider: Callable[[], str | None] | None = None,
        github_api_base: str = "https://api.github.com",
        timeout_s: float = 15.0,
        command_timeout_s: int = 1800,
    ) -> None:
        self._app_root = self._canonical(app_root)
        self._github_token_provider = github_token_provider
        self._github_api_base = str(github_api_base or "https://api.github.com").rstrip("/")
        self._timeout_s = max(2.0, float(timeout_s))
        self._command_timeout_s = max(20, int(command_timeout_s))

    @property
    def app_root(self) -> str:
        return self._app_root

    def current_version(self) -> str:
        try:
            value = importlib_metadata.version("pytpo")
            text = str(value or "").strip()
            if text:
                return self._normalize_version_text(text)
        except Exception:
            pass

        pyproject_path = Path(self._app_root) / "pyproject.toml"
        try:
            payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            project = payload.get("project")
            if isinstance(project, dict):
                raw = str(project.get("version") or "").strip()
                if raw:
                    return self._normalize_version_text(raw)
        return "0.0.0"

    def check_for_updates(self) -> UpdateCheckResult:
        repo_slug = self._resolve_repo_slug()
        current = self.current_version()

        latest_tag = ""
        latest_version = ""
        release_url = ""
        release_title = ""
        release_notes = ""
        published_at = ""

        release_path = f"/repos/{repo_slug}/releases/latest"
        try:
            payload = self._request_json(release_path)
        except UpdateServiceError as exc:
            if exc.kind != "not_found":
                raise
            payload = None

        if isinstance(payload, dict):
            latest_tag = str(payload.get("tag_name") or "").strip()
            release_title = str(payload.get("name") or "").strip()
            release_url = str(payload.get("html_url") or "").strip()
            release_notes = str(payload.get("body") or "").strip()
            published_at = str(payload.get("published_at") or payload.get("created_at") or "").strip()

        if not latest_tag:
            tags_payload = self._request_json(f"/repos/{repo_slug}/tags?per_page=1")
            if not isinstance(tags_payload, list) or not tags_payload:
                raise UpdateServiceError("No tags were found on the GitHub repository.", kind="no_tags")
            first = tags_payload[0] if isinstance(tags_payload[0], dict) else {}
            latest_tag = str(first.get("name") or "").strip()
            if not latest_tag:
                raise UpdateServiceError("No usable tag was returned by GitHub.", kind="no_tags")

        latest_version = self._normalize_version_text(latest_tag)
        is_update = self._is_update_available(current, latest_version)

        if not release_title:
            release_title = latest_tag

        return UpdateCheckResult(
            current_version=current,
            latest_version=latest_version,
            latest_tag=latest_tag,
            update_available=is_update,
            repo_slug=repo_slug,
            release_url=release_url,
            release_title=release_title,
            release_notes=release_notes,
            published_at=published_at,
        )

    def apply_update(self) -> UpdateApplyResult:
        root = self._require_repo_root()
        self._ensure_clean_worktree(root)
        branch = self._current_branch(root)
        if not branch:
            raise UpdateServiceError(
                "Cannot update from a detached HEAD. Checkout a branch first.",
                kind="detached_head",
            )
        self._ensure_origin_remote(root)

        pull_output = self._run_command(
            ["git", "-C", root, "pull", "--ff-only"],
            kind="pull_failed",
            timeout_s=max(60, self._command_timeout_s // 4),
        )
        uv_sync_output = self._run_command(
            ["uv", "sync"],
            cwd=root,
            kind="uv_sync_failed",
            timeout_s=self._command_timeout_s,
        )
        updated = "already up to date" not in pull_output.lower()
        return UpdateApplyResult(
            repo_root=root,
            branch=branch,
            updated=updated,
            pull_output=pull_output,
            uv_sync_output=uv_sync_output,
        )

    def _resolve_repo_slug(self) -> str:
        from_env = str(os.environ.get("PYTPO_UPDATE_REPO") or "").strip()
        if from_env:
            parsed_env = self._parse_repo_slug(from_env)
            if parsed_env:
                return parsed_env

        root = self._require_repo_root()
        remote_url = self._run_command(
            ["git", "-C", root, "remote", "get-url", "origin"],
            kind="origin_missing",
            timeout_s=20,
        )
        parsed = self._parse_repo_slug(remote_url)
        if parsed:
            return parsed
        raise UpdateServiceError(
            "Could not determine GitHub repository from remote 'origin'.",
            kind="repo_resolution_failed",
        )

    def _require_repo_root(self) -> str:
        root = self._canonical(self._app_root)
        if not os.path.isdir(root):
            raise UpdateServiceError("Application root is not available.", kind="invalid_root")
        marker = os.path.join(root, ".git")
        if os.path.isdir(marker):
            return root
        try:
            out = self._run_command(["git", "-C", root, "rev-parse", "--show-toplevel"], kind="not_git_repo", timeout_s=20)
        except UpdateServiceError:
            raise UpdateServiceError("Updater requires a Git checkout of PyTPO.", kind="not_git_repo") from None
        resolved = self._canonical(out.strip())
        if not os.path.isdir(resolved):
            raise UpdateServiceError("Updater could not resolve repository root.", kind="not_git_repo")
        return resolved

    def _ensure_clean_worktree(self, repo_root: str) -> None:
        status = self._run_command(
            ["git", "-C", repo_root, "status", "--porcelain", "--untracked-files=no"],
            kind="status_failed",
            timeout_s=20,
        )
        lines = [line for line in status.splitlines() if line.strip()]
        if not lines:
            return
        preview = "\n".join(lines[:12])
        if len(lines) > 12:
            preview += f"\n...and {len(lines) - 12} more"
        raise UpdateServiceError(
            "Tracked changes are present. Commit/stash tracked changes before updating.\n\n" + preview,
            kind="dirty_worktree",
        )

    def _ensure_origin_remote(self, repo_root: str) -> None:
        self._run_command(
            ["git", "-C", repo_root, "remote", "get-url", "origin"],
            kind="origin_missing",
            timeout_s=20,
        )

    def _current_branch(self, repo_root: str) -> str:
        out = self._run_command(
            ["git", "-C", repo_root, "branch", "--show-current"],
            kind="branch_read_failed",
            timeout_s=20,
        )
        return str(out or "").strip()

    def _request_json(self, path: str) -> object:
        url = f"{self._github_api_base}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PyTPO-Updater",
        }
        token = ""
        if callable(self._github_token_provider):
            try:
                token = str(self._github_token_provider() or "").strip()
            except Exception:
                token = ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url=url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            if status == 404:
                raise UpdateServiceError("Update endpoint not found.", kind="not_found") from None
            if status in {401, 403}:
                raise UpdateServiceError(
                    "GitHub request was rejected (auth/rate limit).",
                    kind="auth_or_rate_limit",
                ) from None
            raise UpdateServiceError(f"GitHub request failed with HTTP {status}.", kind="github_http") from None
        except urllib.error.URLError as exc:
            raise UpdateServiceError("Network error while checking updates.", kind="network") from exc
        except TimeoutError as exc:
            raise UpdateServiceError("Timed out while checking updates.", kind="network") from exc

        try:
            return json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise UpdateServiceError("Invalid response from GitHub.", kind="invalid_response") from exc

    def _run_command(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        kind: str,
        timeout_s: int,
    ) -> str:
        try:
            completed = subprocess.run(
                args,
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=max(2, int(timeout_s)),
                check=False,
            )
        except FileNotFoundError as exc:
            cmd = str(args[0] if args else "command")
            raise UpdateServiceError(f"Required command is not installed: {cmd}", kind=kind) from exc
        except subprocess.TimeoutExpired as exc:
            raise UpdateServiceError("Command timed out while applying update.", kind=kind) from exc
        except Exception as exc:
            raise UpdateServiceError("Failed to run update command.", kind=kind) from exc

        output = str(completed.stdout or "").strip()
        if completed.returncode == 0:
            return output

        tail = self._trim_output(output)
        message = f"Command failed: {' '.join(args)}"
        if tail:
            message = f"{message}\n\n{tail}"
        raise UpdateServiceError(message, kind=kind)

    @staticmethod
    def _trim_output(text: str, *, max_chars: int = 5000) -> str:
        value = str(text or "").strip()
        if len(value) <= max_chars:
            return value
        return value[-max_chars:]

    @staticmethod
    def _canonical(path: str | Path) -> str:
        try:
            return str(Path(path).expanduser().resolve())
        except Exception:
            return str(Path(path).expanduser())

    @classmethod
    def _normalize_version_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "0.0.0"
        match = _TAG_VERSION_RE.search(text)
        if match is None:
            return text.lstrip("vV")
        normalized = match.group(0).strip()
        return normalized.lstrip("vV")

    @staticmethod
    def _parse_repo_slug(raw: str) -> str | None:
        text = str(raw or "").strip()
        if not text:
            return None
        if text.startswith("git@github.com:"):
            text = text.split(":", 1)[1]
        elif "github.com/" in text:
            text = text.split("github.com/", 1)[1]
        text = text.strip().strip("/")
        if text.endswith(".git"):
            text = text[:-4]
        parts = [part for part in text.split("/") if part]
        if len(parts) < 2:
            return None
        owner = parts[0].strip()
        repo = parts[1].strip()
        if not owner or not repo:
            return None
        return f"{owner}/{repo}"

    @classmethod
    def _semver_tuple(cls, text: str) -> tuple[int, int, int] | None:
        match = _VERSION_RE.fullmatch(str(text or "").strip())
        if match is None:
            return None
        try:
            return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except Exception:
            return None

    @classmethod
    def _is_update_available(cls, current: str, latest: str) -> bool:
        if current == latest:
            return False
        current_tuple = cls._semver_tuple(current)
        latest_tuple = cls._semver_tuple(latest)
        if current_tuple is not None and latest_tuple is not None:
            return latest_tuple > current_tuple
        return bool(str(latest or "").strip()) and str(current or "").strip() != str(latest or "").strip()
