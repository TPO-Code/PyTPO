from __future__ import annotations

import os
import re
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(slots=True)
class GitRunResult:
    returncode: int
    stdout: str
    stderr: str


class GitRunError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "git_error",
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.stdout = sanitize_git_text(stdout)
        self.stderr = sanitize_git_text(stderr)


class GitAuthBridge:
    def __init__(
        self,
        *,
        token_provider: Callable[[], str | None] | None = None,
        enabled_provider: Callable[[], bool] | None = None,
    ) -> None:
        self._token_provider = token_provider or (lambda: None)
        self._enabled_provider = enabled_provider or (lambda: True)

    def prepare_env(self, remote_url: str | None) -> tuple[dict[str, str], Callable[[], None]]:
        if not bool(self._enabled_provider()):
            return {}, _noop_cleanup

        token = str(self._token_provider() or "").strip()
        if not token:
            return {}, _noop_cleanup

        if not _is_github_https_url(remote_url):
            return {}, _noop_cleanup

        script_path = _write_askpass_script()
        env = {
            "GIT_ASKPASS": script_path,
            "PYTPO_GIT_PAT": token,
        }

        def _cleanup() -> None:
            try:
                os.unlink(script_path)
            except Exception:
                pass

        return env, _cleanup


class GitCommandRunner:
    def __init__(
        self,
        *,
        auth_bridge: GitAuthBridge | None = None,
        default_timeout_seconds: int = 120,
    ) -> None:
        self._auth_bridge = auth_bridge
        self._default_timeout_seconds = max(10, int(default_timeout_seconds))

    def run(
        self,
        *,
        git_bin: str,
        cwd: str,
        args: list[str],
        timeout_seconds: int | None = None,
        auth_url_hint: str | None = None,
    ) -> GitRunResult:
        command = [str(git_bin), *[str(arg) for arg in args]]
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        auth_url = str(auth_url_hint or "").strip() or self._infer_auth_url(
            git_bin=str(git_bin),
            cwd=str(cwd),
            args=args,
        )

        cleanup = _noop_cleanup
        if self._auth_bridge is not None:
            bridge_env, cleanup = self._auth_bridge.prepare_env(auth_url)
            env.update(bridge_env)

        timeout = max(10, int(timeout_seconds or self._default_timeout_seconds))
        try:
            proc = subprocess.run(
                command,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitRunError(
                "Git command timed out.",
                kind="timeout",
                stdout=_to_text(exc.stdout),
                stderr=_to_text(exc.stderr),
            ) from exc
        except OSError as exc:
            raise GitRunError("Could not start git.", kind="git_not_installed") from exc
        finally:
            cleanup()

        return GitRunResult(
            returncode=int(proc.returncode),
            stdout=sanitize_git_text(proc.stdout),
            stderr=sanitize_git_text(proc.stderr),
        )

    def _infer_auth_url(self, *, git_bin: str, cwd: str, args: list[str]) -> str:
        if not args:
            return ""

        cmd = str(args[0] or "").strip().lower()
        rest = [str(item or "").strip() for item in args[1:]]

        if cmd == "clone":
            for token in rest:
                if not token or token.startswith("-"):
                    continue
                return token
            return ""

        if cmd == "ls-remote":
            for token in rest:
                if not token or token.startswith("-"):
                    continue
                return token
            return ""

        if cmd not in {"push", "pull", "fetch"}:
            return ""

        remote = _extract_remote_name(rest)
        if _looks_like_url(remote):
            return remote
        if not remote:
            remote = "origin"
        return self._read_remote_url(git_bin=git_bin, cwd=cwd, remote_name=remote)

    @staticmethod
    def _read_remote_url(*, git_bin: str, cwd: str, remote_name: str) -> str:
        remote = str(remote_name or "").strip()
        if not remote:
            return ""
        try:
            proc = subprocess.run(
                [git_bin, "config", "--get", f"remote.{remote}.url"],
                cwd=str(cwd),
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except Exception:
            return ""
        if proc.returncode != 0:
            return ""
        return sanitize_git_text(str(proc.stdout or "").strip())


def _extract_remote_name(rest: list[str]) -> str:
    if not rest:
        return ""
    idx = 0
    while idx < len(rest):
        token = rest[idx]
        idx += 1
        if not token:
            continue
        if token.startswith("-"):
            if token in {"-C", "--upload-pack"} and idx < len(rest):
                idx += 1
            continue
        return token
    return ""


def _is_github_https_url(url: str | None) -> bool:
    text = str(url or "").strip()
    if not text:
        return False
    try:
        parsed = urllib.parse.urlsplit(text)
    except Exception:
        return False
    scheme = str(parsed.scheme or "").strip().lower()
    host = str(parsed.hostname or "").strip().lower()
    if scheme not in {"https", "http"}:
        return False
    return host == "github.com"


def _looks_like_url(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return "://" in text or "@" in text and ":" in text


def _write_askpass_script() -> str:
    fd, path = tempfile.mkstemp(prefix="pytpo-git-askpass-", suffix=".sh")
    try:
        os.fchmod(fd, 0o700)
        script = """#!/bin/sh
prompt="$1"
lp="$(printf '%s' "$prompt" | tr '[:upper:]' '[:lower:]')"
case "$lp" in
  *username*)
    printf '%s\\n' 'x-access-token'
    ;;
  *password*|*token*)
    printf '%s\\n' "${PYTPO_GIT_PAT:-}"
    ;;
  *)
    printf '\\n'
    ;;
esac
"""
        os.write(fd, script.encode("utf-8"))
    finally:
        os.close(fd)
    return str(Path(path))


def _noop_cleanup() -> None:
    return


_URL_CRED_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<cred>[^/\s@]+)@(?P<rest>[^\s]+)", re.IGNORECASE)
_PAT_RE = re.compile(r"github_pat_[A-Za-z0-9_]+")


def sanitize_git_text(text: str | bytes | None) -> str:
    raw = _to_text(text)
    if not raw:
        return ""
    clean = _URL_CRED_RE.sub(lambda m: f"{m.group('scheme')}***@{m.group('rest')}", raw)
    clean = _PAT_RE.sub("***", clean)
    return clean


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
