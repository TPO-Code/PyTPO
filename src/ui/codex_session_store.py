from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CodexSessionRecord:
    session_id: str
    cwd: str
    model: str
    first_user_message: str
    updated_at: datetime
    log_path: Path


def canonical_path_text(path_text: str) -> str:
    text = str(path_text or "").strip()
    if not text:
        return ""
    try:
        canonical = str(Path(text).expanduser().resolve(strict=False))
    except Exception:
        canonical = text
    return canonical.casefold()


def codex_sessions_dir() -> Path:
    return Path.home() / ".codex" / "sessions"


def session_preview_text(text: str, max_chars: int = 74) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max(1, max_chars - 3)].rstrip() + "..."


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text_value = (
            str(item.get("text") or "")
            or str(item.get("input_text") or "")
            or str(item.get("output_text") or "")
        )
        value = text_value.strip()
        if value:
            parts.append(value)
    return "\n".join(parts).strip()


def _extract_user_visible_text(text: str) -> str:
    source = str(text or "").strip()
    marker = "User message:\n"
    index = source.find(marker)
    if index >= 0:
        return source[index + len(marker):].strip()
    return source


def _is_non_user_facing_user_text(text: str) -> bool:
    source = str(text or "").strip()
    if not source:
        return True
    if source.startswith("# AGENTS.md instructions"):
        return True
    if source.startswith("<environment_context>"):
        return True
    if source.startswith("<collaboration_mode>"):
        return True
    return False


def read_codex_session(log_path: Path) -> CodexSessionRecord | None:
    session_id = ""
    cwd = ""
    model = ""
    first_user_message = ""
    fallback_user_message = ""
    try:
        updated_at = datetime.fromtimestamp(log_path.stat().st_mtime)
    except Exception:
        updated_at = datetime.now()
    try:
        with log_path.open("r", encoding="utf-8") as handle:
            for index, raw in enumerate(handle):
                line = str(raw or "").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                payload = data.get("payload")
                if not isinstance(payload, dict):
                    continue
                message_type = str(data.get("type") or "").strip()
                if message_type == "session_meta":
                    session_id = str(payload.get("id") or "").strip() or session_id
                    cwd = str(payload.get("cwd") or "").strip() or cwd
                elif message_type == "turn_context":
                    model = str(payload.get("model") or "").strip() or model
                    cwd = str(payload.get("cwd") or "").strip() or cwd
                elif message_type == "response_item":
                    if str(payload.get("type") or "").strip() != "message":
                        continue
                    if str(payload.get("role") or "").strip() != "user":
                        continue
                    raw_text = _extract_message_text(payload.get("content"))
                    text = _extract_user_visible_text(raw_text)
                    if text and not fallback_user_message:
                        fallback_user_message = text
                    if text and not _is_non_user_facing_user_text(text):
                        first_user_message = text
                if session_id and cwd and model and first_user_message:
                    break
                if index >= 500:
                    break
    except Exception:
        return None
    if not session_id:
        return None
    return CodexSessionRecord(
        session_id=session_id,
        cwd=cwd,
        model=model,
        first_user_message=first_user_message or fallback_user_message,
        updated_at=updated_at,
        log_path=log_path,
    )


def list_codex_sessions(
    *,
    limit: int | None = None,
    project_dir: Path | None = None,
) -> list[CodexSessionRecord]:
    sessions_dir = codex_sessions_dir()
    if not sessions_dir.is_dir():
        return []
    project_key = canonical_path_text(str(project_dir or ""))
    try:
        candidates = sorted(
            sessions_dir.rglob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return []
    found: list[CodexSessionRecord] = []
    seen_ids: set[str] = set()
    max_items = None if limit is None else max(1, int(limit))
    for log_path in candidates:
        session = read_codex_session(log_path)
        if session is None:
            continue
        session_key = session.session_id.casefold()
        if session_key in seen_ids:
            continue
        if project_key and canonical_path_text(session.cwd) != project_key:
            continue
        seen_ids.add(session_key)
        found.append(session)
        if max_items is not None and len(found) >= max_items:
            break
    return found


def find_codex_session(session_id: str) -> CodexSessionRecord | None:
    normalized = str(session_id or "").strip()
    if not normalized:
        return None
    for session in list_codex_sessions(limit=None):
        if session.session_id == normalized:
            return session
    return None


def delete_codex_sessions(log_paths: list[Path]) -> tuple[list[Path], list[str]]:
    deleted: list[Path] = []
    failures: list[str] = []
    seen: set[str] = set()
    for raw_path in log_paths:
        key = str(raw_path)
        if key in seen:
            continue
        seen.add(key)
        try:
            path = Path(raw_path)
            path.unlink()
        except FileNotFoundError:
            continue
        except Exception as exc:
            failures.append(f"{raw_path}: {exc}")
            continue
        deleted.append(Path(raw_path))
    return deleted, failures
