from __future__ import annotations

import hashlib
import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from src.ai.provider_base import AIProviderClient, InlineCompletionRequest, InlineCompletionResult, ModelListResult, ProviderResult


class OpenAICompatibleClient(AIProviderClient):
    _MODEL_CACHE_TTL_S = 180.0
    _model_cache: dict[str, tuple[float, list[str]]] = {}

    def fetch_models(
        self,
        *,
        base_url: str,
        api_key: str,
        org_id: str = "",
        project_id: str = "",
        timeout_s: float = 10.0,
        force_refresh: bool = False,
    ) -> ModelListResult:
        norm_base = self._normalize_base_url(base_url)
        if not norm_base:
            return ModelListResult(ok=False, status_text="Base URL is missing.", error_kind="invalid_config")

        cache_key = self._model_cache_key(norm_base, api_key)
        now = time.time()
        if not force_refresh:
            cached = self._model_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return ModelListResult(ok=True, status_text="Models loaded from cache.", models=list(cached[1]))

        result = self._request_json(
            method="GET",
            url=f"{norm_base}/models",
            payload=None,
            api_key=api_key,
            org_id=org_id,
            project_id=project_id,
            timeout_s=timeout_s,
        )
        if not result["ok"]:
            return ModelListResult(
                ok=False,
                status_text=str(result.get("status_text") or "Failed to fetch models."),
                http_status=result.get("http_status"),
                error_kind=str(result.get("error_kind") or "request_failed"),
            )

        obj = result.get("json")
        if not isinstance(obj, dict):
            return ModelListResult(ok=False, status_text="Provider returned unexpected model payload.", error_kind="parse_error")
        data = obj.get("data")
        if not isinstance(data, list):
            return ModelListResult(ok=False, status_text="Provider returned no model list.", error_kind="parse_error")

        model_ids: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if model_id:
                model_ids.append(model_id)

        model_ids = sorted(set(model_ids), key=str.lower)
        self._model_cache[cache_key] = (now + self._MODEL_CACHE_TTL_S, model_ids)
        return ModelListResult(ok=True, status_text=f"Found {len(model_ids)} model(s).", models=model_ids)

    def test_connection(
        self,
        *,
        base_url: str,
        api_key: str,
        org_id: str = "",
        project_id: str = "",
        timeout_s: float = 10.0,
    ) -> ProviderResult:
        models_res = self.fetch_models(
            base_url=base_url,
            api_key=api_key,
            org_id=org_id,
            project_id=project_id,
            timeout_s=timeout_s,
            force_refresh=True,
        )
        if models_res.ok:
            return ProviderResult(
                ok=True,
                status_text="Connection successful.",
                details={"models": list(models_res.models)},
            )
        return ProviderResult(
            ok=False,
            status_text=models_res.status_text,
            http_status=models_res.http_status,
            error_kind=models_res.error_kind,
        )

    def complete_inline(self, request: InlineCompletionRequest) -> InlineCompletionResult:
        base_url = self._normalize_base_url(request.base_url)
        if not base_url:
            return InlineCompletionResult(ok=False, status_text="Base URL is missing.", error_kind="invalid_config")
        if not str(request.model or "").strip():
            return InlineCompletionResult(ok=False, status_text="No model selected.", error_kind="invalid_config")

        payload = {
            "model": str(request.model),
            "messages": [
                {"role": "system", "content": str(request.system_prompt or "")},
                {"role": "user", "content": str(request.user_prompt or "")},
            ],
            "temperature": 0.15,
            "max_tokens": max(1, int(request.max_output_tokens or 160)),
            "stream": False,
            "n": 1,
        }

        result = self._request_json(
            method="POST",
            url=f"{base_url}/chat/completions",
            payload=payload,
            api_key=request.api_key,
            org_id=request.org_id,
            project_id=request.project_id,
            timeout_s=float(request.timeout_s or 10.0),
        )
        if not result["ok"]:
            return InlineCompletionResult(
                ok=False,
                status_text=str(result.get("status_text") or "AI request failed."),
                http_status=result.get("http_status"),
                error_kind=str(result.get("error_kind") or "request_failed"),
            )

        obj = result.get("json")
        if not isinstance(obj, dict):
            return InlineCompletionResult(ok=False, status_text="Provider returned invalid completion payload.", error_kind="parse_error")

        text = self._extract_completion_text(obj)
        if not text.strip():
            return InlineCompletionResult(ok=False, status_text="Provider returned empty completion.", error_kind="empty")

        return InlineCompletionResult(
            ok=True,
            status_text="AI completion received.",
            text=text,
            usage=obj.get("usage") if isinstance(obj.get("usage"), dict) else {},
        )

    def _request_json(
        self,
        *,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        api_key: str,
        org_id: str,
        project_id: str,
        timeout_s: float,
    ) -> dict[str, Any]:
        data_bytes = None
        if payload is not None:
            data_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "PyTPO-AI/1.0",
        }
        token = str(api_key or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        org_val = str(org_id or "").strip()
        if org_val:
            headers["OpenAI-Organization"] = org_val
        project_val = str(project_id or "").strip()
        if project_val:
            headers["OpenAI-Project"] = project_val

        req = urllib.request.Request(url=url, method=method.upper(), data=data_bytes, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=max(0.5, float(timeout_s))) as resp:
                raw = resp.read()
                text = raw.decode("utf-8", errors="replace")
                parsed = json.loads(text) if text.strip() else {}
                return {
                    "ok": True,
                    "http_status": int(getattr(resp, "status", 200) or 200),
                    "json": parsed,
                    "status_text": "OK",
                    "error_kind": "",
                }
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body_text = ""
            friendly = self._friendly_http_status_text(int(exc.code), body_text)
            return {
                "ok": False,
                "http_status": int(exc.code),
                "status_text": friendly,
                "error_kind": "http_error",
            }
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, ssl.SSLError):
                message = "TLS handshake failed. Check endpoint certificate settings."
            elif isinstance(reason, socket.timeout):
                message = "Connection timed out. Endpoint is unreachable."
            elif isinstance(reason, ConnectionRefusedError):
                message = "Connection refused by endpoint."
            else:
                message = "Could not reach AI endpoint. Check base URL and network connectivity."
            return {
                "ok": False,
                "http_status": None,
                "status_text": message,
                "error_kind": "network",
            }
        except socket.timeout:
            return {
                "ok": False,
                "http_status": None,
                "status_text": "Connection timed out. Endpoint is unreachable.",
                "error_kind": "network",
            }
        except Exception:
            return {
                "ok": False,
                "http_status": None,
                "status_text": "Unexpected provider response.",
                "error_kind": "unknown",
            }

    def _model_cache_key(self, base_url: str, api_key: str) -> str:
        digest = hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()[:16]
        return f"{base_url}|{digest}"

    def _normalize_base_url(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.endswith("/"):
            text = text[:-1]
        if not text.startswith("http://") and not text.startswith("https://"):
            text = f"https://{text}"
        if not text.endswith("/v1") and not text.endswith("/v1/"):
            if text.endswith("/"):
                text = text[:-1]
            text = f"{text}/v1"
        return text.rstrip("/")

    def _friendly_http_status_text(self, status: int, _body_text: str = "") -> str:
        if status in {401, 403}:
            return "Authentication failed (401/403). Verify API key, organization, and model access."
        if status == 404:
            return "Endpoint not found (404). Verify base URL and API path."
        if status == 429:
            return "Provider rate limited the request (429). Try again shortly."
        if 500 <= status <= 599:
            return f"Provider is unavailable ({status}). Try again later."
        return f"Provider request failed ({status})."

    def _extract_completion_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""

        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type") or "") == "text":
                        parts.append(str(item.get("text") or ""))
                return "".join(parts)

        text = first.get("text")
        if isinstance(text, str):
            return text
        return ""
