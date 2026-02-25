"""Controller for lint diagnostics routing and import quick-fix orchestration."""

from __future__ import annotations

import importlib
import os
import pkgutil
import re
import weakref

from PySide6.QtGui import QCursor, QTextCursor
from PySide6.QtWidgets import QMenu

from src.services.ast_query import (
    is_unused_import_diagnostic,
    missing_symbol_from_diagnostic,
    modules_mentioned_in_imports,
    project_file_exported_names,
    project_module_name_for_file,
    symbol_used_as_module,
    unused_import_name_from_diagnostic,
)
from src.services.file_search_service import iter_indexable_python_files
from src.services.refactor_engine import (
    insert_from_import,
    insert_module_import,
    remove_unused_import_from_source,
)
from src.services.symbol_index_service import SymbolIndexService
from src.ui.editor_workspace import EditorWidget

try:
    from shiboken6 import isValid as _is_qobject_valid
except Exception:
    def _is_qobject_valid(_obj) -> bool:
        return True


class DiagnosticsController:
    def __init__(self, ide, project_context):
        self.ide = ide
        self.project_context = project_context
        self._symbol_index = SymbolIndexService()
        self._import_symbol_probe_cache: dict[tuple[str, str], bool] = {}
        self._import_module_probe_cache: dict[str, bool] = {}
        self._qt_symbol_namespace_cache: dict[str, list[tuple[str, str]]] = {}

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    def _attach_all_editor_lint_hooks(self):
        for ed in self.editor_workspace.all_editors():
            self._attach_editor_lint_hooks(ed)

    def _attach_editor_lint_hooks(self, ed: EditorWidget):
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return

        self._apply_editor_background_to_editor(ed)
        self._apply_completion_ui_settings_to_editor(ed)
        self._apply_lint_visual_settings_to_editor(ed)

        editor_id = str(getattr(ed, "editor_id", "") or id(ed))
        if editor_id in self.ide._lint_hooked_editors:
            self._apply_lint_to_editor(ed)
            return
        self.ide._lint_hooked_editors.add(editor_id)

        ed_ref = weakref.ref(ed)

        def _on_contents_changed():
            obj = ed_ref()
            if obj is None or not _is_qobject_valid(obj):
                return
            self._on_editor_document_changed(obj)

        ed.textChanged.connect(_on_contents_changed)
        ed.textChanged.connect(lambda e=ed_ref: self._on_editor_text_changed_for_autosave(e))
        ed.cursorPositionChanged.connect(lambda e=ed_ref: self._on_editor_cursor_or_selection_changed(e))
        ed.selectionChanged.connect(lambda e=ed_ref: self._on_editor_cursor_or_selection_changed(e))
        ed.completionRequested.connect(lambda reason, e=ed_ref: self._on_editor_completion_requested(e, reason))
        ed.aiAssistRequested.connect(lambda reason, e=ed_ref: self._on_editor_ai_assist_requested(e, reason))
        ed.signatureRequested.connect(
            lambda payload, e=ed_ref: self._on_editor_signature_requested(e, payload)
        )
        ed.definitionRequested.connect(
            lambda payload, e=ed_ref: self._on_editor_definition_requested(e, payload)
        )
        ed.usagesRequested.connect(
            lambda payload, e=ed_ref: self._on_editor_usages_requested(e, payload)
        )
        ed.renameRequested.connect(
            lambda payload, e=ed_ref: self._on_editor_rename_requested(e, payload)
        )
        quick_fix_signal = getattr(ed, "quickFixRequested", None)
        if quick_fix_signal is not None and hasattr(quick_fix_signal, "connect"):
            quick_fix_signal.connect(
                lambda payload, e=ed_ref: self._on_editor_quick_fix_requested(e, payload)
            )
        ed.extractVariableRequested.connect(
            lambda payload, e=ed_ref: self._on_editor_extract_variable_requested(e, payload)
        )
        ed.extractMethodRequested.connect(
            lambda payload, e=ed_ref: self._on_editor_extract_method_requested(e, payload)
        )
        ed.editorFontSizeStepRequested.connect(
            lambda step, e=ed_ref: self._on_editor_font_size_step_requested(e, step)
        )
        ed.contextMenuAboutToShow.connect(
            lambda menu, payload, e=ed_ref: self._on_editor_context_menu_about_to_show(e, menu, payload)
        )
        wrap_pref_signal = getattr(ed, "wordWrapPreferenceChanged", None)
        if wrap_pref_signal is not None and hasattr(wrap_pref_signal, "connect"):
            wrap_pref_signal.connect(
                lambda payload, e=ed_ref: self.ide._on_editor_word_wrap_preference_changed(e, payload)
            )

        def _on_editor_destroyed(*_args):
            self.ide._lint_hooked_editors.discard(editor_id)
            self.ide._completion_latest_by_editor.pop(editor_id, None)
            self.ide._signature_latest_by_editor.pop(editor_id, None)
            self.ide._definition_latest_by_editor.pop(editor_id, None)
            self.inline_suggestion_controller.cancel_for_editor(editor_id, clear=False)
            cpp_pack = getattr(self.ide, "cpp_language_pack", None)
            detach = getattr(cpp_pack, "on_editor_detached", None)
            if callable(detach):
                try:
                    detach(editor_id)
                except Exception:
                    pass
            rust_pack = getattr(self.ide, "rust_language_pack", None)
            rust_detach = getattr(rust_pack, "on_editor_detached", None)
            if callable(rust_detach):
                try:
                    rust_detach(editor_id)
                except Exception:
                    pass

        ed.destroyed.connect(_on_editor_destroyed)
        self._apply_lint_to_editor(ed)

    def _on_editor_cursor_or_selection_changed(self, ed_ref) -> None:
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if ed is self.current_editor():
            self._refresh_runtime_action_states()

    def _request_lint_for_editor(
        self,
        ed: EditorWidget,
        reason: str,
        include_source_if_modified: bool = True,
    ):
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if not ed.file_path:
            return

        file_path = self._canonical_path(ed.file_path)
        if not self._is_python_file_path(file_path):
            return

        source_text = None
        if include_source_if_modified and ed.document().isModified():
            source_text = ed.toPlainText()

        self.lint_manager.request_lint_file(
            file_path=file_path,
            source_text=source_text,
            reason=reason,
        )

    def _apply_lint_to_editor(self, ed: EditorWidget):
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if not ed.file_path:
            ed.clear_lint_diagnostics()
            return
        key = self._canonical_path(ed.file_path)
        diagnostics = self.ide._diagnostics_by_file.get(key, [])
        if diagnostics:
            ed.set_lint_diagnostics(diagnostics)
        else:
            ed.clear_lint_diagnostics()

    def _apply_lint_to_open_editors_for_file(self, file_path: str):
        key = self._canonical_path(file_path)
        for ed in self.editor_workspace.all_editors():
            if not ed.file_path:
                continue
            if self._canonical_path(ed.file_path) != key:
                continue
            self._apply_lint_to_editor(ed)

    def _set_problems_panel_data(self):
        if self.problems_panel is None:
            return
        self.problems_panel.set_diagnostics(self.ide._diagnostics_by_file)

    def _on_file_diagnostics_updated(self, file_path: str, diagnostics_obj: object):
        key = self._canonical_path(file_path)
        diagnostics = diagnostics_obj if isinstance(diagnostics_obj, list) else []
        normalized = [d for d in diagnostics if isinstance(d, dict)]
        if normalized:
            self.ide._diagnostics_by_file[key] = normalized
        else:
            self.ide._diagnostics_by_file.pop(key, None)
        self._set_problems_panel_data()
        self._apply_lint_to_open_editors_for_file(key)

    def _on_file_diagnostics_cleared(self, file_path: str):
        key = self._canonical_path(file_path)
        self.ide._diagnostics_by_file.pop(key, None)
        self._set_problems_panel_data()
        self._apply_lint_to_open_editors_for_file(key)

    def _on_all_diagnostics_cleared(self):
        preserved: dict[str, list[dict]] = {}
        for file_path, diagnostics in self.ide._diagnostics_by_file.items():
            if not isinstance(diagnostics, list):
                continue
            keep_rows: list[dict] = []
            for diag in diagnostics:
                if not isinstance(diag, dict):
                    continue
                source = str(diag.get("source") or "").strip().lower()
                if source in {"ruff", "pyflakes", "ast"}:
                    continue
                keep_rows.append(diag)
            if keep_rows:
                preserved[file_path] = keep_rows

        self.ide._diagnostics_by_file = preserved
        if self.problems_panel is not None:
            self.problems_panel.set_diagnostics(self.ide._diagnostics_by_file)
        for ed in self.editor_workspace.all_editors():
            self._apply_lint_to_editor(ed)
        self._on_problem_count_changed(sum(len(rows) for rows in self.ide._diagnostics_by_file.values()))

    def _on_problem_count_changed(self, count: int):
        if self.dock_problems is not None:
            self.dock_problems.setWindowTitle(f"Problems ({max(0, int(count))})")

    def _on_problem_activated(self, file_path: str, line: int, col: int):
        if not file_path:
            return
        cpath = self._canonical_path(file_path)
        if os.path.exists(cpath):
            self.open_file(cpath)

        ed = self._find_open_editor_for_path(cpath)
        if ed is None:
            self.ide.statusBar().showMessage(f"File not available: {cpath}", 2500)
            return

        line_num = max(1, int(line or 1))
        col_num = max(1, int(col or 1))
        block = ed.document().findBlockByNumber(line_num - 1)
        if not block.isValid():
            block = ed.document().lastBlock()
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, col_num - 1)
        ed.setTextCursor(cursor)
        ed.centerCursor()
        self._focus_editor(ed)

    def _diagnostics_for_editor_line(self, ed: EditorWidget, line_num: int) -> list[dict]:
        if not isinstance(ed, EditorWidget) or not ed.file_path:
            return []
        if line_num <= 0:
            return []
        key = self._canonical_path(ed.file_path)
        rows = self.ide._diagnostics_by_file.get(key, [])
        out: list[dict] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            try:
                dline = int(item.get("line") or 0)
            except Exception:
                continue
            if dline == line_num:
                out.append(item)
        return out

    def _on_editor_context_menu_about_to_show(self, ed_ref, menu_obj: object, payload_obj: object):
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        if not isinstance(menu_obj, QMenu):
            return
        if not ed.file_path or not self._is_python_file_path(ed.file_path):
            return

        payload = payload_obj if isinstance(payload_obj, dict) else {}
        try:
            line_num = int(payload.get("line") or 0)
        except Exception:
            line_num = 0
        if line_num <= 0:
            line_num = ed.textCursor().blockNumber() + 1

        diagnostics = self._diagnostics_for_editor_line(ed, line_num)
        if not diagnostics:
            return

        source_text = ed.toPlainText()
        symbol_candidates: list[tuple[str, list[dict]]] = []
        seen_symbols: set[str] = set()
        unused_import_diags: list[dict] = []
        seen_unused: set[tuple[int, str]] = set()
        for diag in diagnostics:
            if self._is_unused_import_diagnostic(diag):
                try:
                    dline = int(diag.get("line") or line_num)
                except Exception:
                    dline = line_num
                target = self._unused_import_name_from_diagnostic(diag)
                unused_key = (dline, target.lower())
                if unused_key not in seen_unused:
                    seen_unused.add(unused_key)
                    unused_import_diags.append(dict(diag))

            symbol = self._missing_symbol_from_diagnostic(diag)
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            prefer_module = self._symbol_used_as_module(source_text, diag, symbol)
            candidates = self._resolve_import_candidates(
                symbol=symbol,
                source_text=source_text,
                prefer_module_import=prefer_module,
                current_file_path=ed.file_path,
            )
            if candidates:
                symbol_candidates.append((symbol, candidates))

        if not symbol_candidates and not unused_import_diags:
            return

        menu_obj.addSeparator()
        if symbol_candidates:
            if len(symbol_candidates) == 1:
                symbol, candidates = symbol_candidates[0]
                self._append_import_fix_actions_to_menu(menu_obj, ed_ref, symbol, candidates)
            else:
                root = menu_obj.addMenu("Quick Fix Imports")
                for symbol, candidates in symbol_candidates:
                    self._append_import_fix_actions_to_menu(root, ed_ref, symbol, candidates)

        for diag in unused_import_diags:
            name = self._unused_import_name_from_diagnostic(diag)
            action_text = f"Remove unused import '{name}'" if name else "Remove unused import"
            act = menu_obj.addAction(action_text)
            act.triggered.connect(
                lambda _checked=False, e=ed_ref, d=dict(diag): self._apply_remove_unused_import_from_context_menu(e, d)
            )

    def _append_import_fix_actions_to_menu(self, parent_menu: QMenu, ed_ref, symbol: str, candidates: list[dict]) -> None:
        if not candidates:
            return
        symbol_text = str(symbol or "").strip()
        if not symbol_text:
            return

        if len(candidates) == 1:
            candidate = candidates[0]
            label = str(candidate.get("label") or "").strip()
            action_text = f"Import '{symbol_text}' ({label})" if label else f"Import '{symbol_text}'"
            act = parent_menu.addAction(action_text)
            act.triggered.connect(
                lambda _checked=False, e=ed_ref, cand=candidate, sym=symbol_text: self._apply_import_candidate_from_context_menu(e, cand, sym)
            )
            return

        submenu = parent_menu.addMenu(f"Import '{symbol_text}'")
        for candidate in candidates:
            label = str(candidate.get("label") or "").strip()
            if not label:
                continue
            act = submenu.addAction(label)
            act.triggered.connect(
                lambda _checked=False, e=ed_ref, cand=candidate, sym=symbol_text: self._apply_import_candidate_from_context_menu(e, cand, sym)
            )

    def _apply_import_candidate_from_context_menu(self, ed_ref, candidate_obj: object, symbol: str):
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        candidate = candidate_obj if isinstance(candidate_obj, dict) else {}
        self._apply_import_candidate_to_editor(ed, candidate, symbol)

    def _apply_remove_unused_import_from_context_menu(self, ed_ref, diag_obj: object) -> None:
        ed = ed_ref() if callable(ed_ref) else ed_ref
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            return
        diag = diag_obj if isinstance(diag_obj, dict) else {}
        self._remove_unused_import_from_editor(ed, diag)

    def _on_problem_import_symbol_requested(self, diag_obj: object):
        diag = diag_obj if isinstance(diag_obj, dict) else None
        symbol = self._missing_symbol_from_diagnostic(diag)
        if not symbol:
            self.ide.statusBar().showMessage("Selected problem is not a missing-symbol lint error.", 2200)
            return

        file_path = str((diag or {}).get("file_path") or "").strip()
        if not file_path:
            self.ide.statusBar().showMessage("Diagnostic is missing file path.", 2200)
            return
        cpath = self._canonical_path(file_path)
        if not os.path.isfile(cpath):
            self.ide.statusBar().showMessage(f"File not available: {cpath}", 2200)
            return
        if not self._is_python_file_path(cpath):
            self.ide.statusBar().showMessage("Auto-import is available for Python files only.", 2200)
            return

        self.open_file(cpath)
        ed = self._find_open_editor_for_path(cpath)
        if not isinstance(ed, EditorWidget):
            self.ide.statusBar().showMessage(f"Could not open editor for {cpath}", 2400)
            return
        self._focus_editor(ed)

        source_text = ed.toPlainText()
        prefer_module_import = self._symbol_used_as_module(source_text, diag, symbol)
        candidates = self._resolve_import_candidates(
            symbol=symbol,
            source_text=source_text,
            prefer_module_import=prefer_module_import,
            current_file_path=cpath,
        )
        if not candidates:
            self.ide.statusBar().showMessage(f"No import candidates found for '{symbol}'.", 2600)
            return

        selected = candidates[0]
        if len(candidates) > 1:
            chooser = QMenu(self.ide)
            choices = chooser.addMenu(f"Import '{symbol}'")
            for candidate in candidates:
                label = str(candidate.get("label") or "").strip()
                if not label:
                    continue
                act = choices.addAction(label)
                act.setData(candidate)
            chosen = chooser.exec(QCursor.pos())
            if chosen is None:
                return
            data = chosen.data()
            if isinstance(data, dict):
                selected = data

        self._apply_import_candidate_to_editor(ed, selected, symbol)

    def _on_problem_remove_unused_import_requested(self, diag_obj: object):
        diag = diag_obj if isinstance(diag_obj, dict) else None
        if not self._is_unused_import_diagnostic(diag):
            self.ide.statusBar().showMessage("Selected problem is not an unused-import lint error.", 2200)
            return

        file_path = str((diag or {}).get("file_path") or "").strip()
        if not file_path:
            self.ide.statusBar().showMessage("Diagnostic is missing file path.", 2200)
            return
        cpath = self._canonical_path(file_path)
        if not os.path.isfile(cpath):
            self.ide.statusBar().showMessage(f"File not available: {cpath}", 2200)
            return
        if not self._is_python_file_path(cpath):
            self.ide.statusBar().showMessage("Unused-import quick fix is available for Python files only.", 2200)
            return

        self.open_file(cpath)
        ed = self._find_open_editor_for_path(cpath)
        if not isinstance(ed, EditorWidget):
            self.ide.statusBar().showMessage(f"Could not open editor for {cpath}", 2400)
            return
        self._focus_editor(ed)

        self._remove_unused_import_from_editor(ed, diag or {})

    def _apply_import_candidate_to_editor(self, ed: EditorWidget, candidate: dict, symbol: str) -> str:
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            self.ide.statusBar().showMessage(f"Could not apply import fix for '{symbol}'.", 2600)
            return "error"

        cand = candidate if isinstance(candidate, dict) else {}
        status = self._insert_import_candidate(ed, cand)
        chosen_label = str(cand.get("label") or f"'{symbol}'")
        if status == "already":
            self.ide.statusBar().showMessage(f"{chosen_label} is already in scope.", 2200)
            return status
        if status == "error":
            self.ide.statusBar().showMessage(f"Could not apply import fix for '{symbol}'.", 2600)
            return status

        self._attach_editor_lint_hooks(ed)
        self._request_lint_for_editor(ed, reason="manual", include_source_if_modified=True)
        self.ide.statusBar().showMessage(f"Applied quick fix: {chosen_label}", 2600)
        return status

    def _remove_unused_import_from_editor(self, ed: EditorWidget, diag_obj: object) -> str:
        if not isinstance(ed, EditorWidget) or not _is_qobject_valid(ed):
            self.ide.statusBar().showMessage("Could not apply unused-import quick fix.", 2600)
            return "error"
        if not ed.file_path or not self._is_python_file_path(ed.file_path):
            self.ide.statusBar().showMessage("Unused-import quick fix is available for Python files only.", 2200)
            return "error"

        diag = diag_obj if isinstance(diag_obj, dict) else {}
        if not self._is_unused_import_diagnostic(diag):
            self.ide.statusBar().showMessage("Selected diagnostic is not an unused import.", 2200)
            return "error"

        source_text = ed.toPlainText()
        result = self._remove_unused_import_from_source(source_text, diag)
        if result.status == "already":
            self.ide.statusBar().showMessage("Unused import is already removed.", 2000)
            return result.status
        if result.status == "error":
            self.ide.statusBar().showMessage("Could not remove unused import from this statement.", 2600)
            return result.status

        self._replace_editor_text_preserve_cursor(ed, result.source_text)
        self._attach_editor_lint_hooks(ed)
        self._request_lint_for_editor(ed, reason="manual", include_source_if_modified=True)
        shown = result.removed_name or self._unused_import_name_from_diagnostic(diag) or "import"
        self.ide.statusBar().showMessage(f"Removed unused import: {shown}", 2600)
        return "updated"

    def _remove_unused_import_from_source(self, source_text: str, diag: dict | None):
        return remove_unused_import_from_source(source_text, diag)

    def _insert_import_candidate(self, ed: EditorWidget, candidate: dict) -> str:
        if not isinstance(ed, EditorWidget):
            return "error"
        kind = str(candidate.get("kind") or "").strip()
        module = str(candidate.get("module") or "").strip()
        export_name = str(candidate.get("name") or "").strip()
        bind_name = str(candidate.get("bind") or "").strip()
        if not kind or not module:
            return "error"
        if kind == "import_module":
            return self._insert_module_import(ed, module_name=module, bind_name=bind_name or module)
        if kind == "from_import":
            return self._insert_from_import(
                ed,
                module_name=module,
                export_name=export_name,
                bind_name=bind_name or export_name,
            )
        return "error"

    def _insert_module_import(self, ed: EditorWidget, module_name: str, bind_name: str) -> str:
        result = insert_module_import(ed.toPlainText(), module_name, bind_name)
        if result.status == "updated":
            self._replace_editor_text_preserve_cursor(ed, result.source_text)
        return result.status

    def _insert_from_import(self, ed: EditorWidget, module_name: str, export_name: str, bind_name: str) -> str:
        result = insert_from_import(ed.toPlainText(), module_name, export_name, bind_name)
        if result.status == "updated":
            self._replace_editor_text_preserve_cursor(ed, result.source_text)
        return result.status

    def _missing_symbol_from_diagnostic(self, diag: dict | None) -> str:
        return missing_symbol_from_diagnostic(diag)

    def _is_unused_import_diagnostic(self, diag: dict | None) -> bool:
        return is_unused_import_diagnostic(diag)

    def _unused_import_name_from_diagnostic(self, diag: dict | None) -> str:
        return unused_import_name_from_diagnostic(diag)

    def _symbol_used_as_module(self, source_text: str, diag: dict | None, symbol: str) -> bool:
        return symbol_used_as_module(source_text, diag, symbol)

    def _modules_mentioned_in_imports(self, source_text: str) -> set[str]:
        return modules_mentioned_in_imports(source_text)

    def _project_file_exported_names(self, file_path: str) -> set[str]:
        return project_file_exported_names(file_path)

    def _project_module_name_for_file(self, file_path: str) -> str:
        return project_module_name_for_file(
            file_path,
            canonicalize=self._canonical_path,
            rel_to_project=self._rel_to_project,
            normalize_rel=self._normalize_rel,
        )

    def _iter_indexable_python_files(self) -> list[str]:
        return iter_indexable_python_files(
            self.project_context.project_root,
            canonicalize=self._canonical_path,
            path_has_prefix=self._path_has_prefix,
            is_path_excluded=lambda path: self.is_path_excluded(path, for_feature="indexing"),
            follow_symlinks=self.project_context.lint_follow_symlinks(),
        )

    def _refresh_project_symbol_index(self) -> None:
        self._symbol_index.refresh(
            self._iter_indexable_python_files(),
            module_name_for_file=self._project_module_name_for_file,
            exported_names_for_file=self._project_file_exported_names,
        )

    def _project_local_symbol_modules(self, symbol: str, *, current_file_path: str = "") -> list[str]:
        self._refresh_project_symbol_index()
        current_module = self._project_module_name_for_file(current_file_path) if current_file_path else ""
        return self._symbol_index.modules_for_symbol(symbol, current_module=current_module)

    def _module_exports_symbol(self, module_name: str, symbol: str) -> bool:
        module = str(module_name or "").strip()
        name = str(symbol or "").strip()
        if not module or not name:
            return False
        key = (module, name)
        cached = self._import_symbol_probe_cache.get(key)
        if cached is not None:
            return bool(cached)
        try:
            mod = importlib.import_module(module)
            result = hasattr(mod, name)
        except Exception:
            result = False
        self._import_symbol_probe_cache[key] = bool(result)
        return bool(result)

    def _can_import_module(self, module_name: str) -> bool:
        module = str(module_name or "").strip()
        if not module or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$", module):
            return False
        cached = self._import_module_probe_cache.get(module)
        if cached is not None:
            return bool(cached)
        try:
            importlib.import_module(module)
            result = True
        except Exception:
            result = False
        self._import_module_probe_cache[module] = bool(result)
        return bool(result)

    def _qt_symbol_candidates(self, symbol: str) -> list[tuple[str, str]]:
        name = str(symbol or "").strip()
        if not name:
            return []
        cached = self._qt_symbol_namespace_cache.get(name)
        if isinstance(cached, list):
            return list(cached)

        exact: list[tuple[str, str]] = []
        folded: list[tuple[str, str]] = []
        lower_name = name.lower()
        for root in ("PySide6", "PyQt6"):
            try:
                pkg = importlib.import_module(root)
            except Exception:
                continue
            module_names = {
                "QtWidgets",
                "QtCore",
                "QtGui",
                "QtNetwork",
                "QtSql",
                "QtSvg",
                "QtPrintSupport",
                "QtMultimedia",
                "QtOpenGL",
                "QtOpenGLWidgets",
                "QtQuick",
                "QtQml",
                "QtXml",
            }
            try:
                pkg_path = getattr(pkg, "__path__", None)
                if pkg_path:
                    for entry in pkgutil.iter_modules(pkg_path):
                        mod_name = str(entry.name or "").strip()
                        if mod_name.startswith("Qt"):
                            module_names.add(mod_name)
            except Exception:
                pass
            for mod_name in sorted(module_names):
                full_name = f"{root}.{mod_name}"
                if self._module_exports_symbol(full_name, name):
                    exact.append((full_name, name))
                    continue
                try:
                    mod = importlib.import_module(full_name)
                    for attr in dir(mod):
                        if str(attr).lower() == lower_name:
                            folded.append((full_name, str(attr)))
                            break
                except Exception:
                    continue

        candidates = exact if exact else folded
        self._qt_symbol_namespace_cache[name] = list(dict.fromkeys(candidates))
        return list(self._qt_symbol_namespace_cache[name])

    def _resolve_import_candidates(
        self,
        *,
        symbol: str,
        source_text: str,
        prefer_module_import: bool,
        current_file_path: str = "",
    ) -> list[dict]:
        target = str(symbol or "").strip()
        if not target:
            return []
        existing_modules = self._modules_mentioned_in_imports(source_text)
        candidates: list[dict] = []
        seen: set[tuple[str, str, str, str]] = set()

        def _push_candidate(kind: str, module_name: str, export_name: str, bind_name: str, *, source_kind: str) -> None:
            module = str(module_name or "").strip()
            export = str(export_name or "").strip()
            bind = str(bind_name or "").strip()
            if kind not in {"import_module", "from_import"} or not module or not bind:
                return
            if kind == "from_import" and not export:
                return

            if kind == "import_module":
                label = f"import {module}"
            else:
                if export == bind:
                    label = f"from {module} import {export}"
                else:
                    label = f"from {module} import {export} as {bind}"

            key = (kind, module, export, bind)
            if key in seen:
                return
            seen.add(key)
            candidates.append(
                {
                    "kind": kind,
                    "module": module,
                    "name": export,
                    "bind": bind,
                    "label": label,
                    "source_kind": source_kind,
                    "in_file": module in existing_modules,
                }
            )

        for module_name in sorted(existing_modules):
            if self._module_exports_symbol(module_name, target):
                _push_candidate("from_import", module_name, target, target, source_kind="existing_import")

        for module_name in self._project_local_symbol_modules(target, current_file_path=current_file_path):
            _push_candidate("from_import", module_name, target, target, source_kind="project_local")

        if self._can_import_module(target):
            _push_candidate("import_module", target, "", target, source_kind="module_self")

        for module_name, export_name in self._qt_symbol_candidates(target):
            bind_name = target
            _push_candidate("from_import", module_name, export_name, bind_name, source_kind="qt")

        if not candidates:
            return []

        has_pyside = any(name.startswith("PySide6.") or name == "PySide6" for name in existing_modules)
        has_pyqt = any(name.startswith("PyQt6.") or name == "PyQt6" for name in existing_modules)
        preferred_root = "PySide6" if has_pyside else ("PyQt6" if has_pyqt else "")

        def _sort_key(item: dict) -> tuple[int, int, int, str]:
            kind = str(item.get("kind") or "")
            module_name = str(item.get("module") or "")
            source_kind = str(item.get("source_kind") or "")
            in_file = 0 if bool(item.get("in_file")) else 1

            if prefer_module_import:
                kind_rank = 0 if kind == "import_module" else 1
            else:
                kind_rank = 0 if kind == "from_import" else 1

            if source_kind == "existing_import":
                source_rank = 0
            elif source_kind == "project_local":
                source_rank = 1
            elif source_kind == "module_self":
                source_rank = 2
            else:
                source_rank = 3

            same_root = 1
            if preferred_root:
                same_root = 0 if module_name.startswith(preferred_root + ".") else 1
            elif module_name.startswith("PySide6."):
                same_root = 0
            elif module_name.startswith("PyQt6."):
                same_root = 1

            return (kind_rank, source_rank + in_file + same_root, in_file, str(item.get("label") or "").lower())

        return sorted(candidates, key=_sort_key)
