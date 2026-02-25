"""Language-provider bridge for C/C++ via clangd."""

from __future__ import annotations

import os
import re
from typing import Any

from PySide6.QtCore import QObject, QUrl, Signal

from src.services.language_provider import LanguageProviderCapabilities

from .cpp_workspace import CppWorkspace

_CPP_KIND_BY_ID = {
    1: "text",
    2: "method",
    3: "function",
    4: "constructor",
    5: "field",
    6: "variable",
    7: "class",
    8: "interface",
    9: "module",
    10: "property",
    11: "unit",
    12: "value",
    13: "enum",
    14: "keyword",
    15: "snippet",
    16: "color",
    17: "file",
    18: "reference",
    19: "folder",
    20: "enum",
    21: "constant",
    22: "struct",
    23: "event",
    24: "operator",
    25: "type",
}

CPP_FILE_EXTENSIONS = (".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hh", ".hxx")
CPP_LANGUAGE_IDS = ("c", "cpp")


class CppLanguagePack(QObject):
    completionReady = Signal(object)
    signatureReady = Signal(object)
    definitionReady = Signal(object)
    referencesProgress = Signal(object)
    referencesReady = Signal(object)
    statusMessage = Signal(str)
    diagnosticsUpdated = Signal(str, object)

    capabilities = LanguageProviderCapabilities(
        completion=True,
        signature=True,
        definition=True,
        references=True,
    )

    def __init__(self, project_root: str, canonicalize, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._canonicalize = canonicalize
        self._project_root = self._canonicalize(project_root)
        self._workspace = CppWorkspace(project_root=project_root, canonicalize=canonicalize, parent=self)
        self._workspace.statusMessage.connect(self.statusMessage.emit)
        self._workspace.diagnosticsUpdated.connect(self.diagnosticsUpdated.emit)
        self._workspace.lspTraffic.connect(self._on_lsp_traffic)

        self._completion_cfg: dict[str, Any] = {"max_items": 500}
        self._references_token_to_request: dict[int, int] = {}
        self._references_token_to_file: dict[int, str] = {}

    def update_settings(self, completion_cfg: dict) -> None:
        if isinstance(completion_cfg, dict):
            self._completion_cfg = dict(completion_cfg)

    def update_project_settings(self, cpp_cfg: dict) -> None:
        self._workspace.update_settings(cpp_cfg if isinstance(cpp_cfg, dict) else {})

    def register_accepted(self, _text: str) -> None:
        # No recency ranking yet for clangd results.
        return

    def shutdown(self) -> None:
        self._workspace.shutdown()

    def supports_file(self, file_path: str) -> bool:
        return self._workspace.supports_file(file_path)

    def on_editor_attached(self, *, editor_id: str, file_path: str, source_text: str, language_id: str) -> None:
        self._workspace.attach_editor(
            editor_id=editor_id,
            file_path=file_path,
            source_text=source_text,
            language_id=language_id,
        )

    def on_editor_detached(self, editor_id: str) -> None:
        self._workspace.detach_editor(editor_id)

    def on_document_changed(self, *, file_path: str, source_text: str) -> None:
        self._workspace.document_changed(file_path=file_path, source_text=source_text)

    def on_document_saved(self, *, file_path: str, source_text: str | None = None) -> None:
        self._workspace.document_saved(file_path=file_path, source_text=source_text)

    def clear_file_diagnostics(self, file_path: str) -> None:
        self._workspace.clear_file_diagnostics(file_path)

    def clear_all_diagnostics(self) -> None:
        self._workspace.clear_all_diagnostics()

    def request_completion(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        prefix: str,
        token: int,
        reason: str = "auto",
    ) -> None:
        cpath = self._canonicalize(file_path)
        if not self.supports_file(cpath):
            self.completionReady.emit(
                {
                    "result_type": "completion",
                    "file_path": cpath,
                    "token": max(1, int(token)),
                    "items": [],
                    "backend": "clangd",
                    "reason": str(reason or "auto"),
                }
            )
            return

        def _emit_items(items: list[dict[str, Any]]) -> None:
            self.completionReady.emit(
                {
                    "result_type": "completion",
                    "file_path": cpath,
                    "token": max(1, int(token)),
                    "items": items,
                    "backend": "clangd",
                    "reason": str(reason or "auto"),
                }
            )

        def _done(result_obj: object, error_obj: object) -> None:
            if error_obj is not None:
                self.statusMessage.emit(f"C/C++ completion failed: {error_obj}")
            max_items = max(5, int(self._completion_cfg.get("max_items", 500)))
            include_mode = _include_completion_mode(
                source_text=source_text,
                line=int(line),
                column=int(column),
            )
            raw_items = _normalize_completion_items(result_obj, max_items=max(500, max_items * 3))
            items = _prioritize_completion_items(
                raw_items,
                prefix=str(prefix or ""),
                limit=max_items,
                include_mode=include_mode,
                project_root=self._project_root,
                current_file_path=cpath,
            )
            if items or not _should_retry_scoped_completion(
                source_text=source_text,
                line=int(line),
                column=int(column),
                prefix=str(prefix or ""),
            ):
                _emit_items(items)
                return

            retry_col = _retry_completion_column(column=int(column), prefix=str(prefix or ""))
            if retry_col == int(column):
                _emit_items(items)
                return

            def _done_retry(retry_result_obj: object, retry_error_obj: object) -> None:
                if retry_error_obj is not None:
                    _emit_items(items)
                    return
                retry_raw_items = _normalize_completion_items(
                    retry_result_obj,
                    max_items=max(500, max_items * 3),
                )
                retry_items = _prioritize_completion_items(
                    retry_raw_items,
                    prefix=str(prefix or ""),
                    limit=max_items,
                    include_mode=include_mode,
                    project_root=self._project_root,
                    current_file_path=cpath,
                )
                _emit_items(retry_items or items)

            self._workspace.request_completion(
                file_path=cpath,
                source_text=source_text,
                line=int(line),
                column=int(retry_col),
                callback=_done_retry,
            )

        self._workspace.request_completion(
            file_path=cpath,
            source_text=source_text,
            line=int(line),
            column=int(column),
            callback=_done,
        )

    def request_signature(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        token: int,
    ) -> None:
        cpath = self._canonicalize(file_path)
        if not self.supports_file(cpath):
            self.signatureReady.emit(
                {
                    "result_type": "signature",
                    "file_path": cpath,
                    "token": max(1, int(token)),
                    "signature": "",
                    "documentation": "",
                    "source": "clangd",
                }
            )
            return

        def _done(result_obj: object, error_obj: object) -> None:
            if error_obj is not None:
                self.statusMessage.emit(f"C/C++ hover failed: {error_obj}")
            signature, documentation = _hover_payload_to_signature(result_obj)
            self.signatureReady.emit(
                {
                    "result_type": "signature",
                    "file_path": cpath,
                    "token": max(1, int(token)),
                    "signature": signature,
                    "documentation": documentation,
                    "full_name": "",
                    "module_name": "",
                    "source": "clangd",
                }
            )

        self._workspace.request_hover(
            file_path=cpath,
            source_text=source_text,
            line=int(line),
            column=int(column),
            callback=_done,
        )

    def get_definitions(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        token: int = 1,
        interpreter: str | None = None,
        project_root: str | None = None,
    ) -> None:
        _ = interpreter, project_root
        cpath = self._canonicalize(file_path)
        if not self.supports_file(cpath):
            self.definitionReady.emit(
                {
                    "result_type": "definition",
                    "file_path": cpath,
                    "token": max(1, int(token)),
                    "results": [],
                    "source": "clangd",
                }
            )
            return

        def _done(result_obj: object, error_obj: object) -> None:
            if error_obj is not None:
                self.statusMessage.emit(f"C/C++ definition failed: {error_obj}")
            results = _locations_to_results(result_obj, fallback_file_path=cpath, source_text=source_text)
            self.definitionReady.emit(
                {
                    "result_type": "definition",
                    "file_path": cpath,
                    "token": max(1, int(token)),
                    "results": results,
                    "source": "clangd",
                }
            )

        self._workspace.request_definition(
            file_path=cpath,
            source_text=source_text,
            line=int(line),
            column=int(column),
            callback=_done,
        )

    def find_references(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        token: int = 1,
        interpreter: str | None = None,
        project_root: str | None = None,
    ) -> None:
        _ = interpreter, project_root
        cpath = self._canonicalize(file_path)
        tok = max(1, int(token))
        if not self.supports_file(cpath):
            self.referencesReady.emit(
                {
                    "result_type": "references_done",
                    "file_path": cpath,
                    "token": tok,
                    "results": [],
                    "processed": 0,
                    "canceled": False,
                    "source": "clangd",
                }
            )
            return

        def _done(result_obj: object, error_obj: object) -> None:
            self._references_token_to_request.pop(tok, None)
            self._references_token_to_file.pop(tok, None)
            if error_obj is not None:
                self.statusMessage.emit(f"C/C++ references failed: {error_obj}")
                self.referencesReady.emit(
                    {
                        "result_type": "references_done",
                        "file_path": cpath,
                        "token": tok,
                        "results": [],
                        "processed": 0,
                        "canceled": False,
                        "source": "clangd",
                        "error": str(error_obj),
                    }
                )
                return
            hits = _locations_to_results(result_obj, fallback_file_path=cpath, source_text=source_text)
            self.referencesReady.emit(
                {
                    "result_type": "references_done",
                    "file_path": cpath,
                    "token": tok,
                    "results": hits,
                    "processed": len(hits),
                    "canceled": False,
                    "source": "clangd",
                }
            )

        request_id = self._workspace.request_references(
            file_path=cpath,
            source_text=source_text,
            line=int(line),
            column=int(column),
            callback=_done,
        )
        if request_id > 0:
            self._references_token_to_request[tok] = request_id
            self._references_token_to_file[tok] = cpath

    def cancel_references(self, token: int) -> None:
        tok = max(0, int(token))
        if tok <= 0:
            return
        request_id = self._references_token_to_request.pop(tok, 0)
        file_path = self._references_token_to_file.pop(tok, "")
        if request_id > 0:
            self._workspace.cancel_request(request_id)
        self.referencesReady.emit(
            {
                "result_type": "references_done",
                "file_path": str(file_path or ""),
                "token": tok,
                "results": [],
                "processed": 0,
                "canceled": True,
                "source": "clangd",
            }
        )

    def _on_lsp_traffic(self, direction: str, payload: str) -> None:
        compact = " ".join(str(payload or "").split())
        if len(compact) > 240:
            compact = compact[:237] + "..."
        self.statusMessage.emit(f"[clangd:{direction}] {compact}")


def _normalize_completion_items(result_obj: object, *, max_items: int) -> list[dict[str, Any]]:
    raw_items: list[dict[str, Any]] = []
    if isinstance(result_obj, list):
        raw_items = [item for item in result_obj if isinstance(item, dict)]
    elif isinstance(result_obj, dict):
        nested = result_obj.get("items")
        if isinstance(nested, list):
            raw_items = [item for item in nested if isinstance(item, dict)]

    out: list[dict[str, Any]] = []
    for item in raw_items:
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        insert_text = str(item.get("insertText") or label)
        text_edit = item.get("textEdit")
        detail = str(item.get("detail") or "")
        doc = _stringify_hover_content(item.get("documentation"))
        out.append(
            {
                "label": label,
                "insert_text": insert_text,
                "kind": _CPP_KIND_BY_ID.get(int(item.get("kind") or 0), "name"),
                "detail": detail,
                "documentation": doc,
                "source": "clangd",
                "source_scope": "project",
                "lsp_text_edit": text_edit if isinstance(text_edit, dict) else None,
                "lsp_insert_text_format": int(item.get("insertTextFormat") or 1),
            }
        )
        if len(out) >= max(5, int(max_items)):
            break
    return out


def _prioritize_completion_items(
    items: list[dict[str, Any]],
    *,
    prefix: str,
    limit: int,
    include_mode: str = "",
    project_root: str = "",
    current_file_path: str = "",
) -> list[dict[str, Any]]:
    if not items:
        return []
    hard_limit = max(20, int(limit))
    pfx = str(prefix or "").strip().lower()
    mode = str(include_mode or "").strip().lower()
    if not pfx and mode != "quote":
        return list(items[:hard_limit])

    root = os.path.abspath(str(project_root or "").strip()) if project_root else ""
    current_dir = os.path.dirname(os.path.abspath(current_file_path)) if current_file_path else ""
    header_cache: dict[str, bool] = {}

    ranked: list[tuple[int, int, int, dict[str, Any]]] = []
    for idx, item in enumerate(items):
        label = str(item.get("label") or item.get("insert_text") or "").strip().lower()
        if not pfx:
            pfx_rank = 0
        elif label.startswith(pfx):
            pfx_rank = 0
        elif pfx in label:
            pfx_rank = 1
        else:
            pfx_rank = 2

        project_rank = 1
        if mode == "quote":
            project_rank = 0 if _is_project_header_completion_item(
                item,
                project_root=root,
                current_dir=current_dir,
                cache=header_cache,
            ) else 1

        ranked.append((pfx_rank, project_rank, idx, item))
    ranked.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return [item for _, _, _, item in ranked[:hard_limit]]


def _include_completion_mode(*, source_text: str, line: int, column: int) -> str:
    line_text = _line_text(source_text, int(line))
    if not line_text:
        return ""
    col = max(0, min(int(column), len(line_text)))
    before_cursor = line_text[:col]
    m = re.match(r'^\s*#\s*include\s*([<"])[^>"]*$', before_cursor)
    if not m:
        return ""
    opener = str(m.group(1) or "")
    if opener == '"':
        return "quote"
    if opener == "<":
        return "angle"
    return ""


def _is_project_header_completion_item(
    item: dict[str, Any],
    *,
    project_root: str,
    current_dir: str,
    cache: dict[str, bool],
) -> bool:
    if not project_root:
        return False
    for candidate in _header_candidates_from_completion_item(item):
        cached = cache.get(candidate)
        if cached is not None:
            if cached:
                return True
            continue
        found = _candidate_path_exists_in_project(
            candidate,
            project_root=project_root,
            current_dir=current_dir,
        )
        cache[candidate] = found
        if found:
            return True
    return False


def _header_candidates_from_completion_item(item: dict[str, Any]) -> list[str]:
    raw_values = [
        item.get("label"),
        item.get("insert_text"),
    ]
    text_edit = item.get("lsp_text_edit")
    if isinstance(text_edit, dict):
        raw_values.append(text_edit.get("newText"))

    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        candidate = _normalize_header_candidate(raw)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def _normalize_header_candidate(raw_value: object) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    text = text.replace("\\", "/")
    for token in ('"', "'", "<"):
        if text.startswith(token):
            text = text[1:]
    for token in ('"', "'", ">"):
        if text.endswith(token):
            text = text[:-1]
    text = text.strip()
    if not text or any(ch in text for ch in "{}()"):
        return ""
    if text.startswith("./"):
        text = text[2:]
    while text.startswith("../"):
        text = text[3:]
    return text.strip("/")


def _candidate_path_exists_in_project(candidate: str, *, project_root: str, current_dir: str) -> bool:
    probe = str(candidate or "").strip()
    if not probe:
        return False
    if os.path.isabs(probe):
        abspath = os.path.abspath(probe)
        return _is_subpath(project_root, abspath) and os.path.isfile(abspath)

    checks: list[str] = []
    if current_dir:
        checks.append(os.path.abspath(os.path.join(current_dir, probe)))
    checks.append(os.path.abspath(os.path.join(project_root, probe)))
    for path in checks:
        if not _is_subpath(project_root, path):
            continue
        if os.path.isfile(path):
            return True
    return False


def _is_subpath(root: str, path: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(root), os.path.abspath(path)]) == os.path.abspath(root)
    except Exception:
        return False


def _line_text(source_text: str, line: int) -> str:
    lines = str(source_text or "").splitlines()
    idx = max(0, int(line) - 1)
    if idx >= len(lines):
        return ""
    return str(lines[idx] or "")


def _should_retry_scoped_completion(*, source_text: str, line: int, column: int, prefix: str) -> bool:
    pfx = str(prefix or "")
    if not pfx:
        return False
    line_text = _line_text(source_text, int(line))
    if not line_text:
        return False
    col = max(0, int(column))
    start_col = max(0, col - len(pfx))
    if start_col <= 0:
        return False
    left = line_text[:start_col]
    return left.endswith("::") or left.endswith("->") or left.endswith(".")


def _retry_completion_column(*, column: int, prefix: str) -> int:
    pfx_len = len(str(prefix or ""))
    if pfx_len <= 0:
        return max(0, int(column))
    return max(0, int(column) - pfx_len)


def _hover_payload_to_signature(result_obj: object) -> tuple[str, str]:
    if not isinstance(result_obj, dict):
        return "", ""
    text = _stringify_hover_content(result_obj.get("contents")).strip()
    if not text:
        return "", ""

    signature = ""
    documentation = text

    code_blocks = re.findall(r"```(?:[^\n]*)\n(.*?)```", text, flags=re.DOTALL)
    if code_blocks:
        first_block = code_blocks[0].strip()
        if first_block:
            first_line = first_block.splitlines()[0].strip()
            signature = first_line

    if not signature:
        for line in text.splitlines():
            probe = line.strip()
            if probe:
                signature = probe
                break

    if signature and documentation.startswith(signature):
        documentation = documentation[len(signature):].strip()
    return signature, documentation


def _stringify_hover_content(content_obj: object) -> str:
    if isinstance(content_obj, str):
        return content_obj
    if isinstance(content_obj, dict):
        if "value" in content_obj:
            return str(content_obj.get("value") or "")
        language = str(content_obj.get("language") or "").strip()
        value = str(content_obj.get("value") or "").strip()
        if language and value:
            return f"```{language}\n{value}\n```"
        return value
    if isinstance(content_obj, list):
        parts = [_stringify_hover_content(item).strip() for item in content_obj]
        return "\n\n".join(part for part in parts if part)
    return ""


def _locations_to_results(
    result_obj: object,
    *,
    fallback_file_path: str,
    source_text: str,
) -> list[dict[str, Any]]:
    raw_locations: list[dict[str, Any]] = []
    if isinstance(result_obj, list):
        raw_locations = [item for item in result_obj if isinstance(item, dict)]
    elif isinstance(result_obj, dict):
        raw_locations = [result_obj]

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    fallback_lines = source_text.splitlines()

    for loc in raw_locations:
        file_path, line, col = _location_to_position(loc, fallback_file_path=fallback_file_path)
        key = (file_path, line, col)
        if key in seen:
            continue
        seen.add(key)

        preview = ""
        if file_path == fallback_file_path and 1 <= line <= len(fallback_lines):
            preview = str(fallback_lines[line - 1]).strip()
        elif os.path.isfile(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as fh:
                    for idx, raw in enumerate(fh, start=1):
                        if idx == line:
                            preview = str(raw).strip()
                            break
            except Exception:
                preview = ""

        out.append(
            {
                "name": "",
                "type": "",
                "file_path": file_path,
                "line": line,
                "column": col,
                "module_name": "",
                "full_name": "",
                "description": "",
                "preview": preview,
            }
        )
    out.sort(key=lambda item: (str(item.get("file_path") or ""), int(item.get("line") or 1), int(item.get("column") or 1)))
    return out


def _location_to_position(location: dict[str, Any], *, fallback_file_path: str) -> tuple[str, int, int]:
    if "targetUri" in location:
        uri = str(location.get("targetUri") or "")
        range_obj = location.get("targetSelectionRange") or location.get("targetRange") or {}
    else:
        uri = str(location.get("uri") or "")
        range_obj = location.get("range") or {}

    start = range_obj.get("start") if isinstance(range_obj, dict) else {}
    line = max(1, int(start.get("line", 0)) + 1) if isinstance(start, dict) else 1
    col = max(1, int(start.get("character", 0)) + 1) if isinstance(start, dict) else 1

    if uri:
        url = QUrl(uri)
        path = str(url.toLocalFile()) if url.isLocalFile() else str(uri)
    else:
        path = str(fallback_file_path or "")
    if path and not path.startswith("file://"):
        path = os.path.abspath(path)
    return path, line, col
