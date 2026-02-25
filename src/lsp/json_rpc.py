"""Minimal JSON-RPC framing helpers for LSP transport over stdio."""

from __future__ import annotations

import json
from typing import Any


def encode_lsp_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


class LspMessageParser:
    """Incremental parser for `Content-Length` framed LSP messages."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._expected_length: int | None = None

    def reset(self) -> None:
        self._buffer.clear()
        self._expected_length = None

    def feed(self, data: bytes | bytearray) -> list[dict[str, Any]]:
        if data:
            self._buffer.extend(data)

        messages: list[dict[str, Any]] = []
        while True:
            if self._expected_length is None:
                header_end = self._buffer.find(b"\r\n\r\n")
                if header_end < 0:
                    break

                header_blob = bytes(self._buffer[:header_end])
                del self._buffer[: header_end + 4]
                self._expected_length = self._parse_content_length(header_blob)
                if self._expected_length is None:
                    # Malformed header: skip and continue scanning for a valid frame.
                    continue

            if len(self._buffer) < self._expected_length:
                break

            body = bytes(self._buffer[: self._expected_length])
            del self._buffer[: self._expected_length]
            self._expected_length = None

            try:
                decoded = json.loads(body.decode("utf-8"))
            except Exception:
                continue
            if isinstance(decoded, dict):
                messages.append(decoded)
        return messages

    @staticmethod
    def _parse_content_length(header_blob: bytes) -> int | None:
        try:
            header_text = header_blob.decode("ascii", errors="ignore")
        except Exception:
            return None

        content_length: int | None = None
        for raw_line in header_text.split("\r\n"):
            if ":" not in raw_line:
                continue
            key, value = raw_line.split(":", 1)
            if key.strip().lower() != "content-length":
                continue
            try:
                content_length = int(value.strip())
            except Exception:
                return None
            break

        if content_length is None or content_length < 0:
            return None
        return content_length

