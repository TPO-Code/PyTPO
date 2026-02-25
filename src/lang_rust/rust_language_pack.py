"""Language-provider bridge for Rust via rust-analyzer."""

from __future__ import annotations

import os
import re
from typing import Any, Callable

from PySide6.QtCore import QObject, QUrl, Signal

from src.services.document_outline_service import OutlineSymbol
from src.services.language_provider import LanguageProviderCapabilities

from .cargo_discovery import discover_workspace_root_for_file
from .rust_workspace import RustWorkspace


_RUST_KIND_BY_ID = {
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
    20: "enum_member",
    21: "constant",
    22: "struct",
    23: "event",
    24: "operator",
    25: "type_parameter",
}

RUST_FILE_EXTENSIONS = (".rs",)
RUST_LANGUAGE_IDS = ("rust",)


class RustLanguagePack(QObject):
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

    def __init__(
        self,
        project_root: str,
        canonicalize: Callable[[str], str],
        path_has_prefix: Callable[[str, str], bool],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._canonicalize = canonicalize
        self._path_has_prefix = path_has_prefix
        self._project_root = self._canonicalize(project_root)
        self._rust_settings: dict[str, Any] = {}

        self._workspace_by_root: dict[str, RustWorkspace] = {}
        self._editor_to_workspace_root: dict[str, str] = {}
        self._completion_cfg: dict[str, Any] = {"max_items": 500}
        self._references_token_to_request: dict[int, int] = {}
        self._references_token_to_workspace_root: dict[int, str] = {}
        self._references_token_to_file: dict[int, str] = {}

    def update_settings(self, completion_cfg: dict) -> None:
        if isinstance(completion_cfg, dict):
            self._completion_cfg = dict(completion_cfg)

    def update_project_settings(self, rust_cfg: dict) -> None:
        self._rust_settings = rust_cfg if isinstance(rust_cfg, dict) else {}
        for workspace in self._workspace_by_root.values():
            workspace.update_settings(self._rust_settings)

    def register_accepted(self, _text: str) -> None:
        return

    def shutdown(self) -> None:
        for workspace in self._workspace_by_root.values():
            workspace.shutdown()
        self._workspace_by_root.clear()
        self._editor_to_workspace_root.clear()
        self._references_token_to_request.clear()
        self._references_token_to_workspace_root.clear()
        self._references_token_to_file.clear()

    def supports_file(self, file_path: str) -> bool:
        suffix = os.path.splitext(str(file_path or ""))[1].lower()
        return suffix in RUST_FILE_EXTENSIONS

    def on_editor_attached(self, *, editor_id: str, file_path: str, source_text: str, language_id: str) -> None:
        cpath = self._canonicalize(file_path)
        editor_key = str(editor_id or "").strip()
        if not editor_key:
            return
        if not self.supports_file(cpath):
            self.on_editor_detached(editor_key)
            return
        workspace = self._workspace_for_file(cpath, create_if_missing=True)
        if workspace is None:
            return

        prev_root = self._editor_to_workspace_root.get(editor_key, "")
        next_root = workspace.workspace_root
        if prev_root and prev_root != next_root:
            prev = self._workspace_by_root.get(prev_root)
            if prev is not None:
                prev.detach_editor(editor_key)
        self._editor_to_workspace_root[editor_key] = next_root
        workspace.attach_editor(
            editor_id=editor_key,
            file_path=cpath,
            source_text=source_text,
            language_id=language_id,
        )

    def on_editor_detached(self, editor_id: str) -> None:
        editor_key = str(editor_id or "").strip()
        if not editor_key:
            return
        root = self._editor_to_workspace_root.pop(editor_key, "")
        workspace = self._workspace_by_root.get(root)
        if workspace is not None:
            workspace.detach_editor(editor_key)

    def on_document_changed(self, *, file_path: str, source_text: str) -> None:
        cpath = self._canonicalize(file_path)
        if not self.supports_file(cpath):
            return
        workspace = self._workspace_for_file(cpath, create_if_missing=True)
        if workspace is None:
            return
        workspace.document_changed(file_path=cpath, source_text=source_text)

    def on_document_saved(self, *, file_path: str, source_text: str | None = None) -> None:
        cpath = self._canonicalize(file_path)
        if not self.supports_file(cpath):
            return
        workspace = self._workspace_for_file(cpath, create_if_missing=True)
        if workspace is None:
            return
        workspace.document_saved(file_path=cpath, source_text=source_text)

    def clear_file_diagnostics(self, file_path: str) -> None:
        cpath = self._canonicalize(file_path)
        workspace = self._workspace_for_file(cpath, create_if_missing=False)
        if workspace is not None:
            workspace.clear_file_diagnostics(cpath)
            return
        self.diagnosticsUpdated.emit(cpath, [])

    def clear_all_diagnostics(self) -> None:
        for workspace in self._workspace_by_root.values():
            workspace.clear_all_diagnostics()

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
        workspace = self._workspace_for_file(cpath, create_if_missing=True)
        if workspace is None:
            self._emit_empty_completion(cpath, token, reason)
            return

        def _done(result_obj: object, error_obj: object) -> None:
            if error_obj is not None:
                self.statusMessage.emit(f"Rust completion failed: {error_obj}")
            max_items = max(5, int(self._completion_cfg.get("max_items", 500)))
            raw_items = _normalize_completion_items(result_obj, max_items=max(500, max_items * 3))
            items = _prioritize_completion_items(raw_items, prefix=str(prefix or ""), limit=max_items)
            self.completionReady.emit(
                {
                    "result_type": "completion",
                    "file_path": cpath,
                    "token": max(1, int(token)),
                    "items": items,
                    "backend": "rust-analyzer",
                    "reason": str(reason or "auto"),
                }
            )

        workspace.request_completion(
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
        workspace = self._workspace_for_file(cpath, create_if_missing=True)
        if workspace is None:
            self._emit_empty_signature(cpath, token)
            return

        def _done_signature(result_obj: object, error_obj: object) -> None:
            if error_obj is not None:
                self.statusMessage.emit(f"Rust signature help failed: {error_obj}")
            signature, documentation = _signature_help_to_text(result_obj)
            if signature or documentation:
                self.signatureReady.emit(
                    {
                        "result_type": "signature",
                        "file_path": cpath,
                        "token": max(1, int(token)),
                        "signature": signature,
                        "documentation": documentation,
                        "full_name": "",
                        "module_name": "",
                        "source": "rust-analyzer",
                    }
                )
                return

            def _done_hover(hover_result_obj: object, hover_error_obj: object) -> None:
                if hover_error_obj is not None:
                    self.statusMessage.emit(f"Rust hover failed: {hover_error_obj}")
                hover_sig, hover_doc = _hover_payload_to_signature(hover_result_obj)
                self.signatureReady.emit(
                    {
                        "result_type": "signature",
                        "file_path": cpath,
                        "token": max(1, int(token)),
                        "signature": hover_sig,
                        "documentation": hover_doc,
                        "full_name": "",
                        "module_name": "",
                        "source": "rust-analyzer",
                    }
                )

            workspace.request_hover(
                file_path=cpath,
                source_text=source_text,
                line=int(line),
                column=int(column),
                callback=_done_hover,
            )

        workspace.request_signature_help(
            file_path=cpath,
            source_text=source_text,
            line=int(line),
            column=int(column),
            callback=_done_signature,
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
        workspace = self._workspace_for_file(cpath, create_if_missing=True)
        if workspace is None:
            self.definitionReady.emit(
                {
                    "result_type": "definition",
                    "file_path": cpath,
                    "token": max(1, int(token)),
                    "results": [],
                    "source": "rust-analyzer",
                }
            )
            return

        def _done(result_obj: object, error_obj: object) -> None:
            if error_obj is not None:
                self.statusMessage.emit(f"Rust definition failed: {error_obj}")
            results = _locations_to_results(result_obj, fallback_file_path=cpath, source_text=source_text)
            self.definitionReady.emit(
                {
                    "result_type": "definition",
                    "file_path": cpath,
                    "token": max(1, int(token)),
                    "results": results,
                    "source": "rust-analyzer",
                }
            )

        workspace.request_definition(
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
        workspace = self._workspace_for_file(cpath, create_if_missing=True)
        if workspace is None:
            self.referencesReady.emit(
                {
                    "result_type": "references_done",
                    "file_path": cpath,
                    "token": tok,
                    "results": [],
                    "processed": 0,
                    "canceled": False,
                    "source": "rust-analyzer",
                }
            )
            return

        def _done(result_obj: object, error_obj: object) -> None:
            self._references_token_to_request.pop(tok, None)
            self._references_token_to_workspace_root.pop(tok, None)
            self._references_token_to_file.pop(tok, None)
            if error_obj is not None:
                self.statusMessage.emit(f"Rust references failed: {error_obj}")
                self.referencesReady.emit(
                    {
                        "result_type": "references_done",
                        "file_path": cpath,
                        "token": tok,
                        "results": [],
                        "processed": 0,
                        "canceled": False,
                        "source": "rust-analyzer",
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
                    "source": "rust-analyzer",
                }
            )

        request_id = workspace.request_references(
            file_path=cpath,
            source_text=source_text,
            line=int(line),
            column=int(column),
            callback=_done,
        )
        if request_id > 0:
            self._references_token_to_request[tok] = request_id
            self._references_token_to_workspace_root[tok] = workspace.workspace_root
            self._references_token_to_file[tok] = cpath

    def cancel_references(self, token: int) -> None:
        tok = max(0, int(token))
        if tok <= 0:
            return
        request_id = self._references_token_to_request.pop(tok, 0)
        root = self._references_token_to_workspace_root.pop(tok, "")
        file_path = self._references_token_to_file.pop(tok, "")
        workspace = self._workspace_by_root.get(root)
        if request_id > 0 and workspace is not None:
            workspace.cancel_request(request_id)
        self.referencesReady.emit(
            {
                "result_type": "references_done",
                "file_path": str(file_path or ""),
                "token": tok,
                "results": [],
                "processed": 0,
                "canceled": True,
                "source": "rust-analyzer",
            }
        )

    def request_rename(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        new_name: str,
        callback: Callable[[object, object], None],
    ) -> None:
        cpath = self._canonicalize(file_path)
        workspace = self._workspace_for_file(cpath, create_if_missing=True)
        if workspace is None:
            callback(None, {"code": -32000, "message": "rust_support_disabled"})
            return
        workspace.request_rename(
            file_path=cpath,
            source_text=source_text,
            line=int(line),
            column=int(column),
            new_name=str(new_name or ""),
            callback=callback,
        )

    def request_code_actions(
        self,
        *,
        file_path: str,
        source_text: str,
        line: int,
        column: int,
        diagnostics: list[dict[str, Any]],
        callback: Callable[[object, object], None],
    ) -> None:
        cpath = self._canonicalize(file_path)
        workspace = self._workspace_for_file(cpath, create_if_missing=True)
        if workspace is None:
            callback([], {"code": -32000, "message": "rust_support_disabled"})
            return
        workspace.request_code_actions(
            file_path=cpath,
            source_text=source_text,
            line=int(line),
            column=int(column),
            diagnostics=[item for item in diagnostics if isinstance(item, dict)],
            callback=callback,
        )

    def request_outline_symbols(
        self,
        *,
        file_path: str,
        source_text: str,
        callback: Callable[[list[OutlineSymbol], str], None],
    ) -> None:
        cpath = self._canonicalize(file_path)
        workspace = self._workspace_for_file(cpath, create_if_missing=True)
        if workspace is None:
            callback([], "Rust language support is disabled.")
            return

        def _done(result_obj: object, error_obj: object) -> None:
            if error_obj is not None:
                callback([], f"Rust outline failed: {error_obj}")
                return
            callback(_document_symbols_to_outline(result_obj), "")

        workspace.request_document_symbols(
            file_path=cpath,
            source_text=source_text,
            callback=_done,
        )

    def request_folding_ranges(
        self,
        *,
        file_path: str,
        source_text: str,
        callback: Callable[[list[tuple[int, int]], str], None],
    ) -> None:
        cpath = self._canonicalize(file_path)
        workspace = self._workspace_for_file(cpath, create_if_missing=True)
        if workspace is None:
            callback([], "Rust language support is disabled.")
            return

        def _done(result_obj: object, error_obj: object) -> None:
            if error_obj is not None:
                callback([], f"Rust foldingRange failed: {error_obj}")
                return
            callback(_folding_ranges_to_lines(result_obj), "")

        workspace.request_folding_ranges(
            file_path=cpath,
            source_text=source_text,
            callback=_done,
        )

    def _workspace_for_file(self, file_path: str, *, create_if_missing: bool) -> RustWorkspace | None:
        cpath = self._canonicalize(file_path)
        if not cpath:
            return None
        root = discover_workspace_root_for_file(
            file_path=cpath,
            project_root=self._project_root,
            canonicalize=self._canonicalize,
            path_has_prefix=self._path_has_prefix,
        )
        if not root and self.supports_file(cpath):
            root = self._canonicalize(os.path.dirname(cpath))
        if not root:
            return None

        workspace = self._workspace_by_root.get(root)
        if workspace is not None or not create_if_missing:
            return workspace

        workspace = RustWorkspace(root, canonicalize=self._canonicalize, parent=self)
        workspace.update_settings(self._rust_settings)
        workspace.statusMessage.connect(lambda text, r=root: self._on_workspace_status(r, text))
        workspace.diagnosticsUpdated.connect(self.diagnosticsUpdated.emit)
        workspace.lspTraffic.connect(self._on_workspace_traffic)
        self._workspace_by_root[root] = workspace
        return workspace

    def _on_workspace_status(self, root: str, text: str) -> None:
        msg = str(text or "").strip()
        if not msg:
            return
        name = os.path.basename(str(root or "").strip()) or "rust"
        self.statusMessage.emit(f"[rust:{name}] {msg}")

    def _on_workspace_traffic(self, direction: str, payload: str) -> None:
        compact = " ".join(str(payload or "").split())
        if len(compact) > 220:
            compact = compact[:217] + "..."
        self.statusMessage.emit(f"[rust-analyzer:{direction}] {compact}")

    def _emit_empty_completion(self, cpath: str, token: int, reason: str) -> None:
        self.completionReady.emit(
            {
                "result_type": "completion",
                "file_path": cpath,
                "token": max(1, int(token)),
                "items": [],
                "backend": "rust-analyzer",
                "reason": str(reason or "auto"),
            }
        )

    def _emit_empty_signature(self, cpath: str, token: int) -> None:
        self.signatureReady.emit(
            {
                "result_type": "signature",
                "file_path": cpath,
                "token": max(1, int(token)),
                "signature": "",
                "documentation": "",
                "source": "rust-analyzer",
            }
        )


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
        documentation = _stringify_hover_content(item.get("documentation"))
        out.append(
            {
                "label": label,
                "insert_text": insert_text,
                "kind": _RUST_KIND_BY_ID.get(int(item.get("kind") or 0), "name"),
                "detail": detail,
                "documentation": documentation,
                "source": "rust-analyzer",
                "source_scope": "project",
                "lsp_text_edit": text_edit if isinstance(text_edit, dict) else None,
                "lsp_insert_text_format": int(item.get("insertTextFormat") or 1),
            }
        )
        if len(out) >= max(5, int(max_items)):
            break
    return out


def _prioritize_completion_items(items: list[dict[str, Any]], *, prefix: str, limit: int) -> list[dict[str, Any]]:
    if not items:
        return []
    hard_limit = max(20, int(limit))
    pfx = str(prefix or "").strip().lower()
    if not pfx:
        return list(items[:hard_limit])

    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for idx, item in enumerate(items):
        label = str(item.get("label") or item.get("insert_text") or "").strip().lower()
        if label.startswith(pfx):
            rank = 0
        elif pfx in label:
            rank = 1
        else:
            rank = 2
        ranked.append((rank, idx, item))
    ranked.sort(key=lambda row: (row[0], row[1]))
    return [item for _, _, item in ranked[:hard_limit]]


def _signature_help_to_text(result_obj: object) -> tuple[str, str]:
    payload = result_obj if isinstance(result_obj, dict) else {}
    signatures = payload.get("signatures")
    if not isinstance(signatures, list):
        return "", ""
    if not signatures:
        return "", ""

    active_idx = int(payload.get("activeSignature", 0) or 0)
    active_idx = max(0, min(active_idx, len(signatures) - 1))
    sig = signatures[active_idx] if isinstance(signatures[active_idx], dict) else {}
    label = str(sig.get("label") or "").strip()
    doc = _stringify_hover_content(sig.get("documentation")).strip()
    return label, doc


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
                with open(file_path, "r", encoding="utf-8") as handle:
                    for idx, raw in enumerate(handle, start=1):
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
    out.sort(
        key=lambda item: (
            str(item.get("file_path") or ""),
            int(item.get("line") or 1),
            int(item.get("column") or 1),
        )
    )
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


def _document_symbols_to_outline(result_obj: object) -> list[OutlineSymbol]:
    if not isinstance(result_obj, list):
        return []
    out: list[OutlineSymbol] = []

    if result_obj and isinstance(result_obj[0], dict) and "children" in result_obj[0]:
        for item in result_obj:
            symbol = _document_symbol_item_to_outline(item)
            if symbol is not None:
                out.append(symbol)
        return out

    # SymbolInformation fallback (flat list).
    for item in result_obj:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip() or "<symbol>"
        kind = _RUST_KIND_BY_ID.get(int(item.get("kind") or 0), "symbol")
        location = item.get("location")
        if not isinstance(location, dict):
            continue
        range_obj = location.get("range")
        if not isinstance(range_obj, dict):
            continue
        start = range_obj.get("start") if isinstance(range_obj.get("start"), dict) else {}
        line = max(1, int(start.get("line", 0)) + 1)
        column = max(1, int(start.get("character", 0)) + 1)
        out.append(OutlineSymbol(name=name, kind=kind, line=line, column=column, children=[]))

    out.sort(key=lambda sym: (int(sym.line), int(sym.column), str(sym.name)))
    return out


def _document_symbol_item_to_outline(item: object) -> OutlineSymbol | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip() or "<symbol>"
    kind = _RUST_KIND_BY_ID.get(int(item.get("kind") or 0), "symbol")
    sel = item.get("selectionRange")
    if not isinstance(sel, dict):
        sel = item.get("range")
    if not isinstance(sel, dict):
        return None
    start = sel.get("start") if isinstance(sel.get("start"), dict) else {}
    line = max(1, int(start.get("line", 0)) + 1)
    column = max(1, int(start.get("character", 0)) + 1)

    children_obj = item.get("children")
    children: list[OutlineSymbol] = []
    if isinstance(children_obj, list):
        for child in children_obj:
            symbol = _document_symbol_item_to_outline(child)
            if symbol is not None:
                children.append(symbol)
    return OutlineSymbol(name=name, kind=kind, line=line, column=column, children=children)


def _folding_ranges_to_lines(result_obj: object) -> list[tuple[int, int]]:
    ranges = result_obj if isinstance(result_obj, list) else []
    out: list[tuple[int, int]] = []
    for item in ranges:
        if not isinstance(item, dict):
            continue
        try:
            start = max(1, int(item.get("startLine", 0)) + 1)
            end = max(start, int(item.get("endLine", 0)) + 1)
        except Exception:
            continue
        if end > start:
            out.append((start, end))
    out.sort(key=lambda row: (row[0], row[1]))
    return out

