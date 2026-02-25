"""Controller for completion, navigation, references, and AI inline orchestration."""

from __future__ import annotations

import os
import re

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QInputDialog, QMenu

from src.lsp.workspace_edit import apply_workspace_edits_to_text, collect_workspace_text_edits
from src.services.language_id import language_id_for_path
from src.services.refactor_engine import (
    extract_cpp_method,
    extract_cpp_variable,
    extract_python_method,
    extract_python_variable,
)
from src.ui.editor_workspace import EditorWidget

try:
    from shiboken6 import isValid as _is_qobject_valid
except Exception:
    def _is_qobject_valid(_obj) -> bool:
        return True

_IDENTIFIER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CPP_HEADER_SUFFIXES = {".h", ".hpp", ".hh", ".hxx", ".ipp", ".tpp", ".inl"}


class LanguageIntelligenceController:
    def __init__(self, ide):
        self.ide = ide

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    def _editor_lookup_id(self, ed: EditorWidget) -> str:
        return str(getattr(ed, "editor_id", "") or id(ed))

    def _next_completion_token(self) -> int:
        self.ide._completion_next_token += 1
        return self.ide._completion_next_token

    def _next_signature_token(self) -> int:
        self.ide._signature_next_token += 1
        return self.ide._signature_next_token

    def _next_definition_token(self) -> int:
        self.ide._definition_next_token += 1
        return self.ide._definition_next_token

    def _next_usages_token(self) -> int:
        self.ide._usages_next_token += 1
        return self.ide._usages_next_token

    def _completion_target_path(self, ed: EditorWidget) -> str:
        if ed.file_path:
            return self._canonical_path(ed.file_path)
        return self._canonical_path(self.project_root)

    def _editor_language_id(self, ed: EditorWidget) -> str:
        file_path = getattr(ed, "file_path", None)
        if isinstance(file_path, str) and file_path.strip():
            language_id = language_id_for_path(file_path, default="plaintext")
            suffix = os.path.splitext(file_path)[1].lower()
            if language_id == "c" and suffix in _CPP_HEADER_SUFFIXES:
                return "cpp"
            return language_id
        # Avoid forcing unsaved/unknown files through Python fallback providers.
        try:
            from_editor = str(ed.language_id() or "").strip().lower()
        except Exception:
            from_editor = ""
        if from_editor:
            return from_editor
        return language_id_for_path(None, default="plaintext")

    @staticmethod
    def _line_text_at(source_text: str, line_one_based: int) -> str:
        lines = str(source_text or "").splitlines()
        idx = max(0, int(line_one_based) - 1)
        if idx >= len(lines):
            return ""
        return str(lines[idx] or "")

    def _cpp_auto_completion_trigger(self, ed: EditorWidget, ctx: dict) -> bool:
        lang = str(self._editor_language_id(ed) or "").strip().lower()
        if lang not in {"c", "cpp"}:
            return False
        source_text = ed.toPlainText()
        line = int(ctx.get("line") or 1)
        col = max(0, int(ctx.get("column") or 0))
        line_text = self._line_text_at(source_text, line)
        if not line_text:
            return False
        col = min(col, len(line_text))
        prev_char = line_text[col - 1] if col > 0 else ""
        prev_two = line_text[col - 2:col] if col >= 2 else ""
        if prev_two in {"::", "->"}:
            return True
        if prev_char in {"<", '"', "/"}:
            before_cursor = line_text[:col]
            if re.match(r'^\s*#\s*include\s*[<"][^>"]*$', before_cursor):
                return True
        return False

    def _request_completion_for_editor(self, ed: EditorWidget, reason: str = "auto"):
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        completion_cfg = self._completion_config()
        if not bool(completion_cfg.get("enabled", True)):
            ed.hide_completion_popup()
            return

        if reason == "auto":
            if ed is not self.current_editor():
                return
            if not bool(completion_cfg.get("auto_trigger", True)):
                return

        ctx = ed.completion_context()
        prefix = str(ctx.get("prefix") or "")
        prev_char = str(ctx.get("previous_char") or "")
        min_chars = max(1, int(completion_cfg.get("auto_trigger_min_chars", 2)))
        trigger_after_dot = bool(completion_cfg.get("auto_trigger_after_dot", True))

        if reason == "auto":
            dot_trigger = trigger_after_dot and prev_char == "."
            cpp_trigger = self._cpp_auto_completion_trigger(ed, ctx)
            prefix_trigger = len(prefix) >= min_chars
            if not (dot_trigger or cpp_trigger or prefix_trigger):
                ed.hide_completion_popup()
                return

        token = self._next_completion_token()
        editor_id = self._editor_lookup_id(ed)
        self.ide._completion_latest_by_editor[editor_id] = token
        self.ide._completion_request_meta[token] = {
            "editor_id": editor_id,
            "doc_revision": int(ed.document().revision()),
        }

        self.language_service_hub.request_completion(
            language_id=self._editor_language_id(ed),
            file_path=self._completion_target_path(ed),
            source_text=ed.toPlainText(),
            line=int(ctx.get("line") or 1),
            column=int(ctx.get("column") or 0),
            prefix=prefix,
            token=token,
            reason=reason,
        )

    def _record_ai_recent_file(self, file_path: str | None) -> None:
        if not isinstance(file_path, str) or not file_path.strip():
            return
        cpath = self._canonical_path(file_path)
        self.ide._ai_recent_files = [item for item in self.ide._ai_recent_files if item != cpath]
        self.ide._ai_recent_files.insert(0, cpath)
        if len(self.ide._ai_recent_files) > 48:
            self.ide._ai_recent_files = self.ide._ai_recent_files[:48]

    def _request_ai_inline_for_editor(self, ed: EditorWidget, reason: str = "manual") -> None:
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return

        if not ed.file_path:
            ed.clear_inline_suggestion()
            if reason != "passive":
                self.statusBar().showMessage("AI Assist requires a saved file.", 1800)
            return

        ai_cfg = self._ai_assist_config()
        if not bool(ai_cfg.get("enabled", False)):
            ed.clear_inline_suggestion()
            self.inline_suggestion_controller.cancel_for_editor(self._editor_lookup_id(ed), clear=True)
            if reason != "passive":
                self.statusBar().showMessage("AI Assist is disabled.", 1800)
            return

        if ed is not self.current_editor():
            if reason == "passive":
                return
            self._focus_editor(ed)

        ctx = ed.completion_context()
        cpath = self._canonical_path(ed.file_path)
        payload = {
            "editor_id": self._editor_lookup_id(ed),
            "file_path": cpath,
            "source_text": ed.toPlainText(),
            "line": int(ctx.get("line") or 1),
            "column": int(ctx.get("column") or 0),
            "prefix": str(ctx.get("prefix") or ""),
            "previous_char": str(ctx.get("previous_char") or ""),
            "recent_files": list(self.ide._ai_recent_files),
        }
        if reason == "passive":
            self.inline_suggestion_controller.request_passive(**payload)
            return
        self.inline_suggestion_controller.request_manual(**payload)

    def _on_ai_inline_suggestion_ready(self, payload_obj: object) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        editor_id = str(payload.get("editor_id") or "")
        if not editor_id:
            return
        ed = self._editor_by_id(editor_id)
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if ed is not self.current_editor():
            return
        text = str(payload.get("text") or "")
        if ed.is_completion_popup_visible():
            if text.strip():
                ed.set_completion_ai_suggestion(text)
            else:
                ed.clear_completion_ai_suggestion()
            return
        ed.clear_completion_ai_suggestion()
        if text.strip():
            ed.set_inline_suggestion(text)
        else:
            ed.clear_inline_suggestion()

    def _on_editor_signature_requested(self, ed_ref, payload: object):
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if ed is not self.current_editor():
            return
        if not isinstance(payload, dict):
            return

        try:
            line = max(1, int(payload.get("line") or 1))
            column = max(0, int(payload.get("column") or 0))
            request_id = int(payload.get("request_id") or 0)
            request_revision = int(payload.get("revision") or 0)
        except Exception:
            return

        label = str(payload.get("label") or "").strip()
        if not label:
            return
        if request_revision and int(ed.document().revision()) != request_revision:
            return

        token = self._next_signature_token()
        editor_id = self._editor_lookup_id(ed)
        self.ide._signature_latest_by_editor[editor_id] = token
        self.ide._signature_request_meta[token] = {
            "editor_id": editor_id,
            "request_id": request_id,
            "request_revision": request_revision,
            "label": label,
        }

        self.language_service_hub.request_signature(
            language_id=self._editor_language_id(ed),
            file_path=self._completion_target_path(ed),
            source_text=ed.toPlainText(),
            line=line,
            column=column,
            token=token,
        )

    def _on_editor_definition_requested(self, ed_ref, payload: object):
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if not isinstance(payload, dict):
            self.statusBar().showMessage("No symbol under cursor.", 1600)
            return
        self._request_definition_for_editor(ed, payload)

    def _on_editor_usages_requested(self, ed_ref, payload: object):
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if not isinstance(payload, dict):
            self.statusBar().showMessage("No symbol under cursor.", 1600)
            return
        self._request_usages_for_editor(ed, payload)

    def _request_definition_for_editor(self, ed: EditorWidget, payload: dict):
        try:
            line = max(1, int(payload.get("line") or 1))
            column = max(0, int(payload.get("column") or 0))
        except Exception:
            self.statusBar().showMessage("No symbol under cursor.", 1600)
            return
        symbol = str(payload.get("symbol") or "").strip()
        if not symbol:
            self.statusBar().showMessage("No symbol under cursor.", 1600)
            return

        token = self._next_definition_token()
        editor_id = self._editor_lookup_id(ed)
        self.ide._definition_latest_by_editor[editor_id] = token
        self.ide._definition_request_meta[token] = {
            "editor_id": editor_id,
            "doc_revision": int(ed.document().revision()),
            "symbol": symbol,
        }

        self.language_service_hub.get_definitions(
            language_id=self._editor_language_id(ed),
            file_path=self._completion_target_path(ed),
            source_text=ed.toPlainText(),
            line=line,
            column=column,
            token=token,
            interpreter=self.resolve_interpreter(self._completion_target_path(ed)),
            project_root=self.project_root,
        )

    def _request_usages_for_editor(self, ed: EditorWidget, payload: dict):
        try:
            line = max(1, int(payload.get("line") or 1))
            column = max(0, int(payload.get("column") or 0))
        except Exception:
            self.statusBar().showMessage("No symbol under cursor.", 1600)
            return
        symbol = str(payload.get("symbol") or "").strip()
        if not symbol:
            self.statusBar().showMessage("No symbol under cursor.", 1600)
            return

        if self.ide._active_usages_token > 0:
            self.language_service_hub.cancel_references(self.ide._active_usages_token)

        token = self._next_usages_token()
        self.ide._active_usages_token = token
        self.ide._usages_request_meta[token] = {
            "editor_id": self._editor_lookup_id(ed),
            "doc_revision": int(ed.document().revision()),
            "symbol": symbol,
        }
        if self.usages_panel is not None:
            self.usages_panel.start_search(symbol, token)
        if self.dock_usages is not None:
            self.dock_usages.show()
            self.dock_usages.raise_()

        self.language_service_hub.find_references(
            language_id=self._editor_language_id(ed),
            file_path=self._completion_target_path(ed),
            source_text=ed.toPlainText(),
            line=line,
            column=column,
            token=token,
            interpreter=self.resolve_interpreter(self._completion_target_path(ed)),
            project_root=self.project_root,
        )
        self.statusBar().showMessage(f"Finding usages for '{symbol}'...", 1600)

    def _on_editor_rename_requested(self, ed_ref, payload: object) -> None:
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if not isinstance(payload, dict):
            self.statusBar().showMessage("No symbol under cursor.", 1600)
            return
        self._start_rename_from_payload(ed, payload)

    def rename_symbol_for_current_editor(self) -> None:
        ed = self.current_editor()
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            self.statusBar().showMessage("No active editor.", 1500)
            return

        payload_fn = getattr(ed, "_symbol_payload_from_cursor", None)
        payload = payload_fn() if callable(payload_fn) else None
        if not isinstance(payload, dict):
            self.statusBar().showMessage("No symbol under cursor.", 1600)
            return
        self._start_rename_from_payload(ed, payload)

    def _start_rename_from_payload(self, ed: EditorWidget, payload: dict) -> None:
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            self.statusBar().showMessage("No active editor.", 1500)
            return
        if not ed.file_path:
            self.statusBar().showMessage("Save the file before renaming symbols.", 2200)
            return

        symbol = str(payload.get("symbol") or "").strip()
        if not symbol:
            self.statusBar().showMessage("No symbol under cursor.", 1600)
            return

        language_id = str(self._editor_language_id(ed) or "").strip().lower()
        if language_id not in {"python", "c", "cpp", "rust"}:
            self.statusBar().showMessage("Rename is currently available for Python, C/C++, and Rust files.", 2600)
            return

        new_symbol, ok = QInputDialog.getText(
            self.ide,
            "Rename Symbol",
            f"Rename '{symbol}' to:",
            text=symbol,
        )
        if not ok:
            return
        replacement = str(new_symbol or "").strip()
        if not replacement:
            self.statusBar().showMessage("Rename canceled: new name is empty.", 2000)
            return
        if replacement == symbol:
            self.statusBar().showMessage("Rename canceled: new name matches current symbol.", 2000)
            return
        if not _IDENTIFIER_NAME_RE.match(replacement):
            self.statusBar().showMessage("Rename canceled: use a valid identifier name.", 2400)
            return

        self._request_rename_for_editor(
            ed,
            payload=payload,
            old_symbol=symbol,
            new_symbol=replacement,
        )

    def _on_editor_extract_variable_requested(self, ed_ref, payload: object) -> None:
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if not isinstance(payload, dict):
            self.statusBar().showMessage("Select text to extract a variable.", 2200)
            return
        self._start_extract_variable_from_payload(ed, payload)

    def _on_editor_extract_method_requested(self, ed_ref, payload: object) -> None:
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if not isinstance(payload, dict):
            self.statusBar().showMessage("Select lines to extract a method.", 2200)
            return
        self._start_extract_method_from_payload(ed, payload)

    def extract_variable_for_current_editor(self) -> None:
        ed = self.current_editor()
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            self.statusBar().showMessage("No active editor.", 1500)
            return
        payload = self._selection_payload_from_editor(ed)
        if not isinstance(payload, dict):
            self.statusBar().showMessage("Select text to extract a variable.", 2200)
            return
        self._start_extract_variable_from_payload(ed, payload)

    def extract_method_for_current_editor(self) -> None:
        ed = self.current_editor()
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            self.statusBar().showMessage("No active editor.", 1500)
            return
        payload = self._selection_payload_from_editor(ed)
        if not isinstance(payload, dict):
            self.statusBar().showMessage("Select lines to extract a method.", 2200)
            return
        self._start_extract_method_from_payload(ed, payload)

    def _start_extract_variable_from_payload(self, ed: EditorWidget, payload: dict) -> None:
        selection = self._normalize_selection_payload(payload)
        if selection is None:
            self.statusBar().showMessage("Select text to extract a variable.", 2200)
            return
        language = self._effective_refactor_language(ed)
        if language not in {"python", "cpp", "c"}:
            self.statusBar().showMessage("Extract Variable is available for Python and C/C++ files.", 2400)
            return

        default_name = "extracted_value" if language == "python" else "extractedValue"
        new_name, ok = QInputDialog.getText(
            self.ide,
            "Extract Variable",
            "Variable name:",
            text=default_name,
        )
        if not ok:
            return
        variable_name = str(new_name or "").strip()
        if not variable_name:
            self.statusBar().showMessage("Extraction canceled: variable name is empty.", 2200)
            return
        if not _IDENTIFIER_NAME_RE.match(variable_name):
            self.statusBar().showMessage("Extraction canceled: use a valid identifier name.", 2400)
            return

        source_text = ed.toPlainText()
        if language == "python":
            result = extract_python_variable(
                source_text,
                selection_start=selection["start"],
                selection_end=selection["end"],
                variable_name=variable_name,
            )
        else:
            result = extract_cpp_variable(
                source_text,
                selection_start=selection["start"],
                selection_end=selection["end"],
                variable_name=variable_name,
                use_auto_type=(language == "cpp"),
            )
        self._apply_extract_result(ed, result)

    def _start_extract_method_from_payload(self, ed: EditorWidget, payload: dict) -> None:
        selection = self._normalize_selection_payload(payload)
        if selection is None:
            self.statusBar().showMessage("Select lines to extract a method.", 2200)
            return
        language = self._effective_refactor_language(ed)
        if language not in {"python", "cpp"}:
            self.statusBar().showMessage("Extract Method is available for Python and C++ files.", 2400)
            return

        default_name = "extracted_method" if language == "python" else "extractedMethod"
        new_name, ok = QInputDialog.getText(
            self.ide,
            "Extract Method",
            "Method name:",
            text=default_name,
        )
        if not ok:
            return
        method_name = str(new_name or "").strip()
        if not method_name:
            self.statusBar().showMessage("Extraction canceled: method name is empty.", 2200)
            return
        if not _IDENTIFIER_NAME_RE.match(method_name):
            self.statusBar().showMessage("Extraction canceled: use a valid identifier name.", 2400)
            return

        source_text = ed.toPlainText()
        if language == "python":
            result = extract_python_method(
                source_text,
                selection_start=selection["start"],
                selection_end=selection["end"],
                method_name=method_name,
            )
        else:
            result = extract_cpp_method(
                source_text,
                selection_start=selection["start"],
                selection_end=selection["end"],
                method_name=method_name,
            )
        self._apply_extract_result(ed, result)

    def _selection_payload_from_editor(self, ed: EditorWidget) -> dict | None:
        payload_fn = getattr(ed, "_selection_payload", None)
        payload = payload_fn() if callable(payload_fn) else None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _normalize_selection_payload(payload: dict) -> dict | None:
        try:
            start = max(0, int(payload.get("selection_start") or 0))
            end = max(0, int(payload.get("selection_end") or 0))
        except Exception:
            return None
        if end < start:
            start, end = end, start
        if end <= start:
            return None
        return {"start": start, "end": end}

    def _effective_refactor_language(self, ed: EditorWidget) -> str:
        language = str(self._editor_language_id(ed) or "").strip().lower()
        file_path = str(getattr(ed, "file_path", "") or "").strip().lower()
        suffix = os.path.splitext(file_path)[1]
        if language == "c" and suffix in {".h", ".hpp", ".hh", ".hxx", ".cpp", ".cc", ".cxx"}:
            return "cpp"
        return language

    def _apply_extract_result(self, ed: EditorWidget, result_obj: object) -> None:
        result = result_obj if hasattr(result_obj, "status") and hasattr(result_obj, "source_text") else None
        if result is None:
            self.statusBar().showMessage("Extraction failed.", 2400)
            return
        status = str(getattr(result, "status", "") or "").strip().lower()
        message = str(getattr(result, "message", "") or "").strip()
        if status == "updated":
            self._replace_editor_text_preserve_cursor(ed, str(getattr(result, "source_text", "") or ""))
            self.statusBar().showMessage(message or "Extraction applied.", 2600)
            return
        if status == "already":
            self.statusBar().showMessage(message or "No changes made.", 2200)
            return
        self.statusBar().showMessage(message or "Extraction failed.", 2600)

    def _on_editor_quick_fix_requested(self, ed_ref, payload_obj: object) -> None:
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        self._request_quick_fixes_for_editor(ed, payload)

    def _request_quick_fixes_for_editor(self, ed: EditorWidget, payload: dict) -> None:
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if not ed.file_path:
            self.statusBar().showMessage("Save the file before requesting quick fixes.", 2200)
            return

        language_id = str(self._editor_language_id(ed) or "").strip().lower()
        if language_id != "rust":
            self.statusBar().showMessage("Quick fixes are currently available for Rust files.", 2200)
            return

        rust_pack = getattr(self.ide, "rust_language_pack", None)
        request_actions = getattr(rust_pack, "request_code_actions", None)
        if not callable(request_actions):
            self.statusBar().showMessage("Rust quick fixes are unavailable.", 2200)
            return

        try:
            line = max(1, int(payload.get("line") or (ed.textCursor().blockNumber() + 1)))
            column = max(1, int(payload.get("column") or (ed.textCursor().positionInBlock() + 1)))
        except Exception:
            line = max(1, int(ed.textCursor().blockNumber()) + 1)
            column = max(1, int(ed.textCursor().positionInBlock()) + 1)

        diagnostics = self._diagnostics_covering_position(ed, line=line, column=column)
        global_pos_obj = payload.get("global_pos")
        if hasattr(global_pos_obj, "x") and hasattr(global_pos_obj, "y"):
            global_pos = global_pos_obj
        else:
            try:
                rect = ed.cursorRect()
                global_pos = ed.mapToGlobal(rect.bottomRight())
            except Exception:
                global_pos = None

        def _done(result_obj: object, error_obj: object) -> None:
            if error_obj is not None:
                self.statusBar().showMessage(f"Quick fix failed: {error_obj}", 3200)
                return
            raw_actions = result_obj if isinstance(result_obj, list) else []
            actions = [item for item in raw_actions if isinstance(item, dict)]
            applicable: list[dict] = []
            for item in actions:
                if isinstance(item.get("edit"), dict):
                    applicable.append(item)
                    continue
                # v1: ignore command-only actions (executeCommand not wired in client path).
            if not applicable:
                self.statusBar().showMessage("No quick fixes available at cursor.", 2200)
                return
            self._show_quick_fix_menu(ed, applicable, global_pos=global_pos)

        request_actions(
            file_path=self._completion_target_path(ed),
            source_text=ed.toPlainText(),
            line=int(line),
            column=max(0, int(column) - 1),
            diagnostics=diagnostics,
            callback=_done,
        )

    def _diagnostics_covering_position(self, ed: EditorWidget, *, line: int, column: int) -> list[dict]:
        if not isinstance(ed, EditorWidget) or not ed.file_path:
            return []
        key = self._canonical_path(ed.file_path)
        rows = self.ide._diagnostics_by_file.get(key, [])
        out: list[dict] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            try:
                dline = int(item.get("line") or 0)
                dcol = max(1, int(item.get("column") or 1))
                dend_line = max(dline, int(item.get("end_line") or dline))
                dend_col = max(1, int(item.get("end_column") or dcol))
            except Exception:
                continue
            if int(line) < dline or int(line) > dend_line:
                continue
            if dline == dend_line and not (dcol <= int(column) <= max(dcol, dend_col)):
                continue
            out.append(item)
        return out

    def _show_quick_fix_menu(self, ed: EditorWidget, actions: list[dict], *, global_pos: object) -> None:
        if not isinstance(ed, EditorWidget):
            return
        menu = QMenu(ed)
        mapped: dict[object, dict] = {}
        for item in actions:
            title = str(item.get("title") or "").strip() or "Quick Fix"
            action = menu.addAction(title)
            mapped[action] = item
        chosen = menu.exec(global_pos) if global_pos is not None else menu.exec()
        selected = mapped.get(chosen)
        if not isinstance(selected, dict):
            return
        edit_obj = selected.get("edit")
        changed_files, changed_occurrences, details = self._apply_workspace_edit_payload(
            edit_obj,
            operation_label="Quick fix",
        )
        if changed_files <= 0:
            detail = "; ".join(details[:2]) if details else "No edits were applied."
            self.statusBar().showMessage(f"Quick fix made no changes. {detail}", 2600)
            return
        self.statusBar().showMessage(
            f"Applied quick fix: {changed_occurrences} edit(s) across {changed_files} file(s).",
            3000,
        )

    def _request_rename_for_editor(
        self,
        ed: EditorWidget,
        *,
        payload: dict,
        old_symbol: str,
        new_symbol: str,
    ) -> None:
        try:
            line = max(1, int(payload.get("line") or 1))
            column = max(0, int(payload.get("column") or 0))
        except Exception:
            self.statusBar().showMessage("No symbol under cursor.", 1600)
            return

        language_id = str(self._editor_language_id(ed) or "").strip().lower()
        if language_id == "rust":
            self._request_lsp_rename_for_editor(
                ed,
                line=line,
                column=column,
                old_symbol=old_symbol,
                new_symbol=new_symbol,
            )
            return

        self._cancel_active_rename(silent=True)

        token = self._next_usages_token()
        self.ide._active_rename_token = token
        self.ide._rename_request_meta[token] = {
            "editor_id": self._editor_lookup_id(ed),
            "doc_revision": int(ed.document().revision()),
            "old_symbol": str(old_symbol),
            "new_symbol": str(new_symbol),
            "hits": [],
        }

        self.language_service_hub.find_references(
            language_id=self._editor_language_id(ed),
            file_path=self._completion_target_path(ed),
            source_text=ed.toPlainText(),
            line=line,
            column=column,
            token=token,
            interpreter=self.resolve_interpreter(self._completion_target_path(ed)),
            project_root=self.project_root,
        )
        self.statusBar().showMessage(
            f"Renaming '{old_symbol}' to '{new_symbol}'...",
            2200,
        )

    def _request_lsp_rename_for_editor(
        self,
        ed: EditorWidget,
        *,
        line: int,
        column: int,
        old_symbol: str,
        new_symbol: str,
    ) -> None:
        rust_pack = getattr(self.ide, "rust_language_pack", None)
        request_rename = getattr(rust_pack, "request_rename", None)
        if not callable(request_rename):
            self.statusBar().showMessage("Rust rename is unavailable.", 2200)
            return

        self._cancel_active_rename(silent=True)
        token = self._next_usages_token()
        self.ide._active_rename_token = token
        self.ide._rename_request_meta[token] = {
            "editor_id": self._editor_lookup_id(ed),
            "doc_revision": int(ed.document().revision()),
            "old_symbol": str(old_symbol),
            "new_symbol": str(new_symbol),
            "mode": "lsp_workspace_edit",
        }

        def _done(workspace_edit_obj: object, error_obj: object) -> None:
            self._on_lsp_rename_done(token, workspace_edit_obj, error_obj)

        request_rename(
            file_path=self._completion_target_path(ed),
            source_text=ed.toPlainText(),
            line=int(line),
            column=int(column),
            new_name=str(new_symbol),
            callback=_done,
        )
        self.statusBar().showMessage(
            f"Renaming '{old_symbol}' to '{new_symbol}'...",
            2200,
        )

    def _on_lsp_rename_done(self, token: int, workspace_edit_obj: object, error_obj: object) -> None:
        meta = self.ide._rename_request_meta.pop(token, None)
        if not isinstance(meta, dict):
            return
        if int(self.ide._active_rename_token or 0) == int(token):
            self.ide._active_rename_token = 0

        if error_obj is not None:
            self.statusBar().showMessage(f"Rename failed: {error_obj}", 3200)
            return
        changed_files, changed_occurrences, details = self._apply_workspace_edit_payload(
            workspace_edit_obj,
            operation_label="Rename",
        )
        if changed_files <= 0:
            detail = "; ".join(details[:2]) if details else "No editable occurrences."
            self.statusBar().showMessage(f"Rename found no updates. {detail}", 2600)
            return
        old_symbol = str(meta.get("old_symbol") or "").strip()
        new_symbol = str(meta.get("new_symbol") or "").strip()
        self.statusBar().showMessage(
            f"Renamed '{old_symbol}' to '{new_symbol}' in {changed_occurrences} edit(s) across {changed_files} file(s).",
            4200,
        )

    def _apply_workspace_edit_payload(
        self,
        workspace_edit_obj: object,
        *,
        operation_label: str,
    ) -> tuple[int, int, list[str]]:
        edits_by_path = collect_workspace_text_edits(workspace_edit_obj)
        if not edits_by_path:
            return 0, 0, [f"{operation_label}: no text edits in workspace edit payload."]

        changed_files = 0
        changed_occurrences = 0
        details: list[str] = []
        refresh_dirs: set[str] = set()

        for raw_path, edits in edits_by_path.items():
            cpath = self._canonical_path(raw_path)
            if not cpath:
                continue
            if not isinstance(edits, list) or not edits:
                continue
            editor = self._find_open_editor_for_path(cpath)
            if isinstance(editor, EditorWidget) and editor.document().isModified():
                details.append(f"Skipped modified file: {cpath}")
                continue

            source_text = ""
            if isinstance(editor, EditorWidget):
                source_text = editor.toPlainText()
            else:
                try:
                    with open(cpath, "r", encoding="utf-8") as handle:
                        source_text = handle.read()
                except Exception:
                    details.append(f"Could not read: {cpath}")
                    continue

            try:
                updated_text = apply_workspace_edits_to_text(source_text, edits)
            except Exception as exc:
                details.append(f"Could not apply edits for {cpath}: {exc}")
                continue

            if updated_text == source_text:
                continue

            try:
                with open(cpath, "w", encoding="utf-8") as handle:
                    handle.write(updated_text)
            except Exception:
                details.append(f"Could not write: {cpath}")
                continue

            changed_files += 1
            changed_occurrences += len(edits)
            refresh_dirs.add(self._canonical_path(os.path.dirname(cpath)))
            self._apply_replaced_text_to_open_editor(cpath, updated_text)

        if changed_files > 0:
            for folder in sorted(refresh_dirs):
                self.refresh_subtree(folder)
            self.schedule_git_status_refresh(delay_ms=90)
        return changed_files, changed_occurrences, details

    def _cancel_active_rename(self, *, silent: bool = False) -> None:
        token = int(self.ide._active_rename_token or 0)
        if token <= 0:
            return
        self.ide._active_rename_token = 0
        self.ide._rename_request_meta.pop(token, None)
        try:
            self.language_service_hub.cancel_references(token)
        except Exception:
            pass
        if not silent:
            self.statusBar().showMessage("Canceled rename request.", 1600)

    def _on_rename_references_done(self, token: int, result_obj: dict) -> None:
        meta = self.ide._rename_request_meta.pop(token, None)
        if not isinstance(meta, dict):
            return
        if int(self.ide._active_rename_token or 0) == token:
            self.ide._active_rename_token = 0

        if bool(result_obj.get("canceled", False)):
            self.statusBar().showMessage("Rename canceled.", 1800)
            return
        error_text = str(result_obj.get("error") or "").strip()
        if error_text:
            self.statusBar().showMessage(f"Rename failed: {error_text}", 3000)
            return

        old_symbol = str(meta.get("old_symbol") or "").strip()
        new_symbol = str(meta.get("new_symbol") or "").strip()
        if not old_symbol or not new_symbol:
            self.statusBar().showMessage("Rename failed: invalid symbol payload.", 2600)
            return

        raw_hits: list[dict] = []
        stored_hits = meta.get("hits")
        if isinstance(stored_hits, list):
            raw_hits.extend([item for item in stored_hits if isinstance(item, dict)])
        done_hits = result_obj.get("results")
        if isinstance(done_hits, list):
            raw_hits.extend([item for item in done_hits if isinstance(item, dict)])
        if not raw_hits:
            self.statusBar().showMessage("Rename found no usages to update.", 2200)
            return

        updates, matched_refs, skipped_modified, read_errors = self._collect_rename_updates(
            old_symbol=old_symbol,
            new_symbol=new_symbol,
            hits=raw_hits,
        )
        if not updates:
            if skipped_modified:
                self.statusBar().showMessage(
                    "Rename skipped: target file(s) have unsaved edits.",
                    3200,
                )
                return
            self.statusBar().showMessage("Rename found no editable occurrences.", 2200)
            return

        changed_files = 0
        changed_paths: list[str] = []
        failed_writes: list[str] = []
        for file_path, text in updates.items():
            try:
                with open(file_path, "w", encoding="utf-8") as fh:
                    fh.write(text)
            except Exception:
                failed_writes.append(file_path)
                continue
            changed_files += 1
            changed_paths.append(file_path)
            self._apply_replaced_text_to_open_editor(file_path, text)

        if changed_files <= 0:
            self.statusBar().showMessage("Rename failed: no files were updated.", 2600)
            return

        self.schedule_git_status_refresh(delay_ms=90)
        summary = f"Renamed '{old_symbol}' to '{new_symbol}' in {matched_refs} occurrence(s) across {changed_files} file(s)."
        self.statusBar().showMessage(summary, 4200)
        debug_lines = [f"[Rename] {summary}"]
        if skipped_modified:
            debug_lines.append("[Rename] Skipped modified files:")
            debug_lines.extend(f"  - {path}" for path in sorted(set(skipped_modified)))
        if read_errors:
            debug_lines.append("[Rename] Could not read files:")
            debug_lines.extend(f"  - {path}" for path in sorted(set(read_errors)))
        if failed_writes:
            debug_lines.append("[Rename] Could not write files:")
            debug_lines.extend(f"  - {path}" for path in sorted(set(failed_writes)))
        self._append_debug_output_lines(debug_lines, reveal=False)

    def _collect_rename_updates(
        self,
        *,
        old_symbol: str,
        new_symbol: str,
        hits: list[dict],
    ) -> tuple[dict[str, str], int, list[str], list[str]]:
        grouped_hits: dict[str, list[tuple[int, int]]] = {}
        for hit in hits:
            file_path = str(hit.get("file_path") or "").strip()
            if not file_path:
                continue
            cpath = self._canonical_path(file_path)
            try:
                line = max(1, int(hit.get("line") or 1))
                column = max(1, int(hit.get("column") or 1))
            except Exception:
                continue
            grouped_hits.setdefault(cpath, []).append((line, column))

        updates: dict[str, str] = {}
        matched_refs = 0
        skipped_modified: list[str] = []
        read_errors: list[str] = []
        symbol_len = len(old_symbol)

        for file_path, positions in grouped_hits.items():
            editor = self._find_open_editor_for_path(file_path)
            if isinstance(editor, EditorWidget) and editor.document().isModified():
                skipped_modified.append(file_path)
                continue

            source_text, readable = self._read_text_for_rename_path(file_path, editor)
            if not readable:
                read_errors.append(file_path)
                continue
            if not source_text:
                continue

            line_starts = [0]
            lines = source_text.splitlines(keepends=True)
            for raw in lines:
                line_starts.append(line_starts[-1] + len(raw))

            offsets: set[int] = set()
            for line, column in positions:
                start = self._offset_from_line_column(source_text, line_starts, lines, line, column)
                if start < 0:
                    continue
                end = start + symbol_len
                if not self._is_symbol_match_at(source_text, old_symbol, start, end):
                    continue
                offsets.add(start)

            if not offsets:
                continue

            updated = source_text
            for start in sorted(offsets, reverse=True):
                end = start + symbol_len
                updated = f"{updated[:start]}{new_symbol}{updated[end:]}"
            if updated == source_text:
                continue

            updates[file_path] = updated
            matched_refs += len(offsets)

        return updates, matched_refs, skipped_modified, read_errors

    @staticmethod
    def _read_text_for_rename_path(file_path: str, editor: EditorWidget | None) -> tuple[str, bool]:
        if isinstance(editor, EditorWidget):
            try:
                return editor.toPlainText(), True
            except Exception:
                return "", False
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                return fh.read(), True
        except Exception:
            return "", False

    @staticmethod
    def _offset_from_line_column(
        source_text: str,
        line_starts: list[int],
        lines: list[str],
        line: int,
        column: int,
    ) -> int:
        line_idx = int(line) - 1
        if line_idx < 0 or line_idx >= len(lines):
            return -1
        col0 = max(0, int(column) - 1)
        line_text = str(lines[line_idx] or "")
        body_len = len(line_text.rstrip("\r\n"))
        if col0 > body_len:
            return -1
        start = int(line_starts[line_idx])
        offset = start + col0
        if offset < 0 or offset > len(source_text):
            return -1
        return offset

    @staticmethod
    def _is_identifier_char(ch: str) -> bool:
        return bool(ch) and (ch.isalnum() or ch == "_")

    def _is_symbol_match_at(self, source_text: str, symbol: str, start: int, end: int) -> bool:
        if start < 0 or end < start or end > len(source_text):
            return False
        if source_text[start:end] != symbol:
            return False
        if start > 0 and self._is_identifier_char(source_text[start - 1]):
            return False
        if end < len(source_text) and self._is_identifier_char(source_text[end]):
            return False
        return True

    def _editor_by_id(self, editor_id: str) -> EditorWidget | None:
        for ed in self.editor_workspace.all_editors():
            if self._editor_lookup_id(ed) == editor_id:
                return ed
        return None

    def _on_completion_result_ready(self, result_obj: object):
        if not isinstance(result_obj, dict):
            return
        token = int(result_obj.get("token") or 0)
        if token <= 0:
            return

        meta = self.ide._completion_request_meta.pop(token, None)
        if not isinstance(meta, dict):
            return
        editor_id = str(meta.get("editor_id") or "")
        if not editor_id:
            return
        if self.ide._completion_latest_by_editor.get(editor_id) != token:
            return

        ed = self._editor_by_id(editor_id)
        if ed is None:
            return

        req_revision = int(meta.get("doc_revision") or 0)
        if int(ed.document().revision()) != req_revision:
            return

        items = result_obj.get("items")
        if not isinstance(items, list):
            items = []
        if not items:
            ed.hide_completion_popup()
            return
        if ed is not self.current_editor():
            return
        ed.show_completion_popup(
            items,
            file_path=str(result_obj.get("file_path") or self._completion_target_path(ed)),
            token=token,
        )

    def _on_signature_result_ready(self, result_obj: object):
        if not isinstance(result_obj, dict):
            return
        token = int(result_obj.get("token") or 0)
        if token <= 0:
            return

        meta = self.ide._signature_request_meta.pop(token, None)
        if not isinstance(meta, dict):
            return
        editor_id = str(meta.get("editor_id") or "")
        if not editor_id:
            return
        if self.ide._signature_latest_by_editor.get(editor_id) != token:
            return

        ed = self._editor_by_id(editor_id)
        if ed is None:
            return

        request_revision = int(meta.get("request_revision") or 0)
        if request_revision and int(ed.document().revision()) != request_revision:
            return

        ed.apply_signature_lookup_result(
            {
                "request_id": int(meta.get("request_id") or 0),
                "label": str(meta.get("label") or ""),
                "signature": str(result_obj.get("signature") or ""),
                "documentation": str(result_obj.get("documentation") or ""),
                "full_name": str(result_obj.get("full_name") or ""),
                "module_name": str(result_obj.get("module_name") or ""),
                "source": str(result_obj.get("source") or ""),
            }
        )

    def _navigate_to_location(self, file_path: str, line: int, column: int):
        path = str(file_path or "").strip()
        if not path:
            self.statusBar().showMessage("Navigation target has no file path.", 2400)
            return
        if not os.path.exists(path):
            self.statusBar().showMessage(f"Target file not found: {path}", 2500)
            return
        self.open_file(path)

        ed = self._find_open_editor_for_path(path)
        if ed is None:
            self.statusBar().showMessage(f"Target not available: {path}", 2400)
            return

        line_num = max(1, int(line or 1))
        col_num = max(1, int(column or 1))
        block = ed.document().findBlockByNumber(line_num - 1)
        if not block.isValid():
            block = ed.document().lastBlock()
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, col_num - 1)
        ed.setTextCursor(cursor)
        ed.centerCursor()
        self._focus_editor(ed)

    def _on_definition_result_ready(self, result_obj: object):
        if not isinstance(result_obj, dict):
            return
        token = int(result_obj.get("token") or 0)
        if token <= 0:
            return

        meta = self.ide._definition_request_meta.pop(token, None)
        if not isinstance(meta, dict):
            return
        editor_id = str(meta.get("editor_id") or "")
        if not editor_id:
            return
        if self.ide._definition_latest_by_editor.get(editor_id) != token:
            return

        ed = self._editor_by_id(editor_id)
        if ed is None:
            return
        req_revision = int(meta.get("doc_revision") or 0)
        if req_revision and int(ed.document().revision()) != req_revision:
            return

        results_obj = result_obj.get("results")
        raw_results = results_obj if isinstance(results_obj, list) else []
        results: list[dict] = [r for r in raw_results if isinstance(r, dict)]
        if not results:
            self.statusBar().showMessage("No definition found.", 2200)
            return

        if len(results) == 1:
            item = results[0]
            self._navigate_to_location(
                str(item.get("file_path") or ""),
                int(item.get("line") or 1),
                int(item.get("column") or 1),
            )
            return

        options: list[str] = []
        mapping: dict[str, dict] = {}
        for item in results:
            fp = str(item.get("file_path") or "")
            ln = max(1, int(item.get("line") or 1))
            col = max(1, int(item.get("column") or 1))
            preview = str(item.get("preview") or item.get("description") or "").strip()
            label = f"{os.path.basename(fp) or fp}:{ln}:{col}  {preview[:90]}"
            options.append(label)
            mapping[label] = item

        chosen, ok = QInputDialog.getItem(
            self.ide,
            "Go to Definition",
            "Select target:",
            options,
            0,
            False,
        )
        if not ok or not chosen:
            return
        target = mapping.get(str(chosen))
        if not isinstance(target, dict):
            return
        self._navigate_to_location(
            str(target.get("file_path") or ""),
            int(target.get("line") or 1),
            int(target.get("column") or 1),
        )

    def _on_references_progress(self, result_obj: object):
        if not isinstance(result_obj, dict):
            return
        token = int(result_obj.get("token") or 0)
        if token <= 0:
            return
        rename_meta = self.ide._rename_request_meta.get(token)
        if isinstance(rename_meta, dict):
            hits = rename_meta.get("hits")
            if not isinstance(hits, list):
                hits = []
                rename_meta["hits"] = hits
            chunk = result_obj.get("results")
            if isinstance(chunk, list):
                hits.extend([item for item in chunk if isinstance(item, dict)])
            return
        if token != self.ide._active_usages_token:
            return
        if self.usages_panel is not None:
            self.usages_panel.append_results(result_obj.get("results"))

    def _on_references_done(self, result_obj: object):
        if not isinstance(result_obj, dict):
            return
        token = int(result_obj.get("token") or 0)
        if token <= 0:
            return
        if token in self.ide._rename_request_meta:
            self._on_rename_references_done(token, result_obj)
            return
        if token != self.ide._active_usages_token:
            return

        if self.usages_panel is not None:
            self.usages_panel.append_results(result_obj.get("results"))
            self.usages_panel.finish_search(
                canceled=bool(result_obj.get("canceled", False)),
                error=str(result_obj.get("error") or ""),
            )
        total = max(0, int(result_obj.get("processed") or 0))
        if bool(result_obj.get("canceled", False)):
            self.statusBar().showMessage("Usages search canceled.", 1800)
        else:
            self.statusBar().showMessage(f"{total} usage(s) found.", 2200)
        self.ide._usages_request_meta.pop(token, None)
        self.ide._active_usages_token = 0
