from __future__ import annotations

import json
import os
from pathlib import Path


class GitHubAuthError(RuntimeError):
    """Raised when a token cannot be persisted or loaded."""


class GitHubAuthStore:
    """
    Token storage abstraction for GitHub auth.

    Prefers OS keyring when available. Falls back to a local file in the app
    secrets directory with restrictive permissions when keyring is unavailable.
    """

    def __init__(
        self,
        ide_app_dir: str | Path,
        *,
        service_name: str = "barley_ide.github",
        account_name: str = "access_token",
    ) -> None:
        self._storage_dir = Path(ide_app_dir).expanduser()
        self._service_name = str(service_name)
        self._account_name = str(account_name)
        self._legacy_fallback_file = self._storage_dir / "github-token.json"
        self._secrets_dir = self._storage_dir / "secrets"
        self._fallback_file = self._secrets_dir / "github-token.json"
        self._migrate_legacy_fallback_file()

    def set(self, token: str) -> None:
        text = str(token or "").strip()
        if not text:
            raise GitHubAuthError("Token is required.")

        if self._set_keyring_token(text):
            self._remove_fallback_file()
            return

        self._write_fallback_token(text)

    def get(self) -> str | None:
        token = self._get_keyring_token()
        if token:
            return token
        try:
            return self._read_fallback_token()
        except Exception:
            return None

    def clear(self) -> None:
        self._clear_keyring_token()
        self._remove_fallback_file()

    def has_token(self) -> bool:
        try:
            return bool(self.get())
        except Exception:
            return False

    def _set_keyring_token(self, token: str) -> bool:
        keyring, _errors = self._import_keyring()
        if keyring is None:
            return False
        try:
            keyring.set_password(self._service_name, self._account_name, token)
            return True
        except Exception:
            return False

    def _get_keyring_token(self) -> str | None:
        keyring, _errors = self._import_keyring()
        if keyring is None:
            return None
        try:
            token = keyring.get_password(self._service_name, self._account_name)
        except Exception:
            return None
        text = str(token or "").strip()
        return text or None

    def _clear_keyring_token(self) -> None:
        keyring, _errors = self._import_keyring()
        if keyring is None:
            return
        try:
            keyring.delete_password(self._service_name, self._account_name)
        except Exception:
            return

    def _read_fallback_token(self) -> str | None:
        self._migrate_legacy_fallback_file()
        path = self._fallback_file
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None
        text = str(payload.get("token") or "").strip()
        return text or None

    def _write_fallback_token(self, token: str) -> None:
        self._secrets_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._secrets_dir, 0o700)
        except Exception:
            pass
        fd = -1
        try:
            fd = os.open(str(self._fallback_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                json.dump({"token": token}, handle)
        except Exception as exc:
            if fd >= 0:
                try:
                    os.close(fd)
                except Exception:
                    pass
            raise GitHubAuthError("Could not store token.") from exc

    def _remove_fallback_file(self) -> None:
        try:
            self._fallback_file.unlink(missing_ok=True)
        except Exception:
            return

    def _migrate_legacy_fallback_file(self) -> None:
        if self._fallback_file.exists():
            return
        if not self._legacy_fallback_file.is_file():
            return
        try:
            self._secrets_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self._secrets_dir, 0o700)
            except Exception:
                pass
            fd = os.open(str(self._fallback_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                payload = self._legacy_fallback_file.read_text(encoding="utf-8")
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    fd = -1
                    handle.write(payload)
            finally:
                if fd >= 0:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
        except Exception:
            return

    @staticmethod
    def _import_keyring():
        try:
            import keyring  # type: ignore
            from keyring import errors as keyring_errors  # type: ignore

            return keyring, keyring_errors
        except Exception:
            return None, None
