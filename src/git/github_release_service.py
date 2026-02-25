from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.git.git_service import GitService, GitServiceError
from src.git.github_client import GitHubClient, GitHubClientError


@dataclass(slots=True)
class GitHubReleaseRequest:
    repo_root: str
    version: str
    tag_name: str
    title: str
    notes: str
    prerelease: bool = False
    draft: bool = False


@dataclass(slots=True)
class GitHubReleaseResult:
    repo_root: str
    tag_name: str
    title: str
    html_url: str


class GitHubReleaseError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "release_failed") -> None:
        super().__init__(message)
        self.kind = kind


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_PROJECT_HEADER_RE = re.compile(r"^\s*\[project\]\s*$", re.IGNORECASE)
_CARGO_PACKAGE_HEADER_RE = re.compile(r"^\s*\[package\]\s*$", re.IGNORECASE)
_CARGO_WORKSPACE_PACKAGE_HEADER_RE = re.compile(r"^\s*\[workspace\.package\]\s*$", re.IGNORECASE)
_TABLE_HEADER_RE = re.compile(r"^\s*\[[^\]]+\]\s*$")
_VERSION_LINE_RE = re.compile(r"^(\s*version\s*=\s*)(['\"])([^'\"]*)(['\"])(\s*(#.*)?)$")


class GitHubReleaseService:
    def __init__(
        self,
        *,
        git_service: GitService,
        github_token_provider: Callable[[], str | None],
        canonicalize: Callable[[str], str] | None = None,
    ) -> None:
        self._git = git_service
        self._github_token_provider = github_token_provider
        self._canonicalize = canonicalize

    def update_pyproject_version(self, repo_root: str, version: str) -> bool:
        target_version = self._normalize_version(version)
        root = self._canonical(repo_root)
        pyproject_path = os.path.join(root, "pyproject.toml")
        if not os.path.isfile(pyproject_path):
            raise GitHubReleaseError("pyproject.toml was not found in repository root.", kind="pyproject_missing")

        changed = self._update_version_line_in_toml(
            file_path=pyproject_path,
            header_pattern=_PROJECT_HEADER_RE,
            target_version=target_version,
        )
        if changed is None:
            raise GitHubReleaseError(
                "Could not locate [project].version in pyproject.toml.",
                kind="pyproject_missing_version",
            )
        return changed

    def update_cargo_version(self, repo_root: str, version: str) -> bool:
        target_version = self._normalize_version(version)
        root = self._canonical(repo_root)
        cargo_path = os.path.join(root, "Cargo.toml")
        if not os.path.isfile(cargo_path):
            raise GitHubReleaseError("Cargo.toml was not found in repository root.", kind="cargo_missing")

        changed = self._update_version_line_in_toml(
            file_path=cargo_path,
            header_pattern=_CARGO_PACKAGE_HEADER_RE,
            target_version=target_version,
        )
        if changed is None:
            changed = self._update_version_line_in_toml(
                file_path=cargo_path,
                header_pattern=_CARGO_WORKSPACE_PACKAGE_HEADER_RE,
                target_version=target_version,
            )
        if changed is None:
            raise GitHubReleaseError(
                "Could not locate [package].version or [workspace.package].version in Cargo.toml.",
                kind="cargo_missing_version",
            )
        return changed

    def _update_version_line_in_toml(
        self,
        *,
        file_path: str,
        header_pattern: re.Pattern[str],
        target_version: str,
    ) -> bool | None:
        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except Exception as exc:
            raise GitHubReleaseError(f"Could not read {os.path.basename(file_path)}: {exc}", kind="toml_io") from exc

        lines = text.splitlines(keepends=True)
        in_target = False
        changed = False
        found = False

        for idx, line in enumerate(lines):
            raw = line.rstrip("\r\n")
            if header_pattern.match(raw):
                in_target = True
                continue
            if _TABLE_HEADER_RE.match(raw):
                in_target = False
            if not in_target:
                continue
            match = _VERSION_LINE_RE.match(raw)
            if match is None:
                continue
            found = True
            current = str(match.group(3) or "").strip()
            if current == target_version:
                break
            prefix = str(match.group(1) or "")
            quote = str(match.group(2) or '"')
            suffix = str(match.group(5) or "")
            newline = "\n"
            if line.endswith("\r\n"):
                newline = "\r\n"
            lines[idx] = f"{prefix}{quote}{target_version}{quote}{suffix}{newline}"
            changed = True
            break

        if not found:
            return None
        if not changed:
            return False

        try:
            Path(file_path).write_text("".join(lines), encoding="utf-8")
        except Exception as exc:
            raise GitHubReleaseError(f"Could not write {os.path.basename(file_path)}: {exc}", kind="toml_io") from exc
        return True

    def create_release(self, req: GitHubReleaseRequest) -> GitHubReleaseResult:
        root = self._canonical(req.repo_root)
        version = self._normalize_version(req.version)
        tag_name = self._normalize_tag(req.tag_name, version)
        title = str(req.title or "").strip() or tag_name

        token = ""
        try:
            token = str(self._github_token_provider() or "").strip()
        except Exception:
            token = ""
        if not token:
            raise GitHubReleaseError("No GitHub token is configured.", kind="no_token")

        try:
            exists = self._git.tag_exists(root, tag_name)
            if not exists:
                self._git.create_annotated_tag(root, tag_name, message=title)
            self._git.push_tag(root, tag_name, remote_name="origin")
        except GitServiceError as exc:
            raise GitHubReleaseError(str(exc), kind=str(exc.kind or "git_error")) from None

        try:
            remote_url = self._git.get_remote_url(root, "origin")
        except GitServiceError as exc:
            raise GitHubReleaseError(str(exc), kind=str(exc.kind or "git_error")) from None
        slug = self._parse_repo_slug(remote_url or "")
        if slug is None:
            raise GitHubReleaseError("Could not resolve owner/repo from remote 'origin'.", kind="repo_resolution")
        owner, repo = slug

        client = GitHubClient(token)
        try:
            created = client.create_release(
                owner=owner,
                repo=repo,
                tag_name=tag_name,
                name=title,
                body=str(req.notes or "").strip(),
                draft=bool(req.draft),
                prerelease=bool(req.prerelease),
            )
        except GitHubClientError as exc:
            if str(exc.kind or "") == "already_exists":
                raise GitHubReleaseError(
                    "Release or tag already exists on GitHub.",
                    kind="already_exists",
                ) from None
            raise GitHubReleaseError(str(exc), kind=str(exc.kind or "github_error")) from None

        return GitHubReleaseResult(
            repo_root=root,
            tag_name=created.tag_name,
            title=created.name or title,
            html_url=created.html_url,
        )

    @staticmethod
    def _normalize_version(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise GitHubReleaseError("Version is required.", kind="validation")
        if not _SEMVER_RE.fullmatch(text):
            raise GitHubReleaseError("Version must be semver-like (for example: 1.2.3).", kind="validation")
        return text

    @staticmethod
    def _normalize_tag(tag_name: str, version: str) -> str:
        tag = str(tag_name or "").strip()
        if not tag:
            tag = f"v{version}"
        if " " in tag:
            raise GitHubReleaseError("Tag name cannot contain spaces.", kind="validation")
        return tag

    @staticmethod
    def _parse_repo_slug(raw: str) -> tuple[str, str] | None:
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
        owner = str(parts[0]).strip()
        repo = str(parts[1]).strip()
        if not owner or not repo:
            return None
        return owner, repo

    def _canonical(self, path: str) -> str:
        if self._canonicalize is not None:
            try:
                return self._canonicalize(path)
            except Exception:
                pass
        try:
            return str(Path(path).expanduser().resolve())
        except Exception:
            return os.path.abspath(os.path.expanduser(path))
