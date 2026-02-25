"""Controller for Find-in-Files UI orchestration using pure search services."""

from __future__ import annotations

import os
import re

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QDockWidget

from src.services.file_search_service import (
    iter_indexable_python_files,
    replace_in_indexed_files,
    search_indexed_files,
)
from src.ui.dialogs.find_in_files_dialog import FindInFilesDialog
from src.ui.editor_workspace import EditorWidget
from src.ui.widgets.find_in_files_results import FindInFilesResultsWidget

try:
    from shiboken6 import isValid as _is_qobject_valid
except Exception:
    def _is_qobject_valid(_obj) -> bool:
        return True


class SearchController(QObject):
    def __init__(self, ide, project_context, parent=None):
        super().__init__(parent or ide)
        self.ide = ide
        self.project_context = project_context

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    def open_find_in_files_dialog(self) -> None:
        dialog = self.ide._find_in_files_dialog
        if dialog is None:
            dialog = FindInFilesDialog(self.ide)
            dialog.findRequested.connect(self._on_find_in_files_requested)
            dialog.replaceRequested.connect(self._on_replace_in_files_requested)
            dialog.addDockRequested.connect(self._on_add_find_results_dock_requested)
            dialog.results_widget.resultActivated.connect(self._on_problem_activated)
            self.ide._find_in_files_dialog = dialog

        ed = self.current_editor()
        if isinstance(ed, EditorWidget):
            selected_text = str(ed.textCursor().selectedText() or "")
            selected_text = selected_text.replace("\u2029", "\n").strip()
            if selected_text and "\n" not in selected_text:
                dialog.set_find_text_if_empty(selected_text)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _normalize_find_in_files_request(self, payload: object) -> dict | None:
        request = payload if isinstance(payload, dict) else {}
        query = str(request.get("query") or "")
        if not query:
            self.ide.statusBar().showMessage("Find in Files: enter search text.", 2200)
            return None
        return {
            "query": query,
            "replace_text": str(request.get("replace_text") or ""),
            "case_sensitive": bool(request.get("case_sensitive", False)),
            "whole_word": bool(request.get("whole_word", False)),
            "use_regex": bool(request.get("use_regex", False)),
        }

    def _compile_find_in_files_pattern(self, request: dict) -> re.Pattern[str] | None:
        query = str(request.get("query") or "")
        use_regex = bool(request.get("use_regex", False))
        whole_word = bool(request.get("whole_word", False))
        case_sensitive = bool(request.get("case_sensitive", False))

        pattern_text = query if use_regex else re.escape(query)
        if whole_word:
            pattern_text = r"\b" + pattern_text + r"\b"
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(pattern_text, flags)
        except re.error as exc:
            self.ide.statusBar().showMessage(f"Find in Files: invalid pattern ({exc}).", 3200)
            return None
        return pattern

    def _find_in_files_targets(self) -> list[str]:
        if self.ide.no_project_mode:
            return []
        return iter_indexable_python_files(
            self.project_context.project_root,
            canonicalize=self._canonical_path,
            path_has_prefix=self._path_has_prefix,
            is_path_excluded=lambda path: self.is_path_excluded(path, for_feature="indexing"),
            follow_symlinks=self.project_context.lint_follow_symlinks(),
        )

    def _search_indexed_files(
        self,
        pattern: re.Pattern[str],
        targets: list[str],
        *,
        max_results: int = 20000,
    ) -> list[dict]:
        return [
            item.to_dict()
            for item in search_indexed_files(pattern, targets, max_results=max_results)
        ]

    def _apply_replaced_text_to_open_editor(self, file_path: str, disk_text: str) -> None:
        ed = self._find_open_editor_for_path(file_path)
        if not isinstance(ed, EditorWidget):
            return
        if ed.document().isModified():
            sig = self._external_file_signature(file_path)
            if sig is not None:
                self.ide._external_conflict_signatures[file_path] = sig
            return
        if ed.toPlainText() == disk_text:
            ed.document().setModified(False)
            self._refresh_editor_title(ed)
            return

        cursor = ed.textCursor()
        v_scroll = ed.verticalScrollBar().value()
        h_scroll = ed.horizontalScrollBar().value()
        ed.setPlainText(disk_text)
        ed.document().setModified(False)
        ed.setTextCursor(cursor)
        ed.verticalScrollBar().setValue(v_scroll)
        ed.horizontalScrollBar().setValue(h_scroll)
        self._refresh_editor_title(ed)
        self._attach_editor_lint_hooks(ed)
        self._request_lint_for_editor(ed, reason="open", include_source_if_modified=False)

    def _replace_in_indexed_files(
        self,
        pattern: re.Pattern[str],
        replace_text: str,
        targets: list[str],
    ) -> tuple[int, int, list[str]]:
        result = replace_in_indexed_files(pattern, replace_text, targets)
        for file_path, new_text in result.updated_text_by_path.items():
            self._apply_replaced_text_to_open_editor(file_path, new_text)
        return result.changed_files, result.replacements_total, result.changed_paths

    def _set_find_in_files_results(self, results: list[dict], summary_text: str) -> None:
        dialog = self.ide._find_in_files_dialog
        if dialog is None:
            return
        dialog.set_results(results, summary_text=summary_text)

    def _on_find_in_files_requested(self, payload: object) -> None:
        request = self._normalize_find_in_files_request(payload)
        if request is None:
            return
        pattern = self._compile_find_in_files_pattern(request)
        if pattern is None:
            return

        targets = self._find_in_files_targets()
        if not targets:
            summary = "No indexed files available."
            self._set_find_in_files_results([], summary)
            self.ide.statusBar().showMessage(summary, 2200)
            return

        results = self._search_indexed_files(pattern, targets)
        summary = f"{len(results)} match(es) in {len(targets)} indexed file(s)."
        self._set_find_in_files_results(results, summary)
        self.ide.statusBar().showMessage(summary, 2200)

    def _on_replace_in_files_requested(self, payload: object) -> None:
        request = self._normalize_find_in_files_request(payload)
        if request is None:
            return
        pattern = self._compile_find_in_files_pattern(request)
        if pattern is None:
            return

        targets = self._find_in_files_targets()
        if not targets:
            summary = "No indexed files available."
            self._set_find_in_files_results([], summary)
            self.ide.statusBar().showMessage(summary, 2200)
            return

        replace_text = str(request.get("replace_text") or "")
        changed_files, replaced_count, changed_paths = self._replace_in_indexed_files(pattern, replace_text, targets)

        refresh_dirs = {self._canonical_path(os.path.dirname(path)) for path in changed_paths}
        for folder in sorted(refresh_dirs):
            self.refresh_subtree(folder)
        if changed_paths:
            self._refresh_project_symbol_index()
            self.schedule_git_status_refresh(delay_ms=80, force=True)
        self._seed_external_file_watch_state()

        remaining_results = self._search_indexed_files(pattern, targets)
        summary = (
            f"Replaced {replaced_count} match(es) in {changed_files} file(s). "
            f"{len(remaining_results)} match(es) remain."
        )
        self._set_find_in_files_results(remaining_results, summary)
        self.ide.statusBar().showMessage(summary, 3000)

    def _prune_find_results_docks(self) -> None:
        dock = self.ide._find_results_dock
        if not isinstance(dock, QDockWidget):
            self.ide._find_results_dock = None
            return
        if not _is_qobject_valid(dock):
            self.ide._find_results_dock = None
            return

    def _create_find_results_dock(self, payload: dict) -> None:
        self._prune_find_results_docks()
        query = str(payload.get("query") or "").strip()
        title = "Search Results"
        if query:
            short_query = query if len(query) <= 36 else (query[:33] + "...")
            title = f"Search Results: {short_query}"

        dock = self.ide._find_results_dock
        created = False
        if not isinstance(dock, QDockWidget) or not _is_qobject_valid(dock):
            dock = QDockWidget(title, self.ide)
            dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.RightDockWidgetArea)
            dock.setFeatures(
                QDockWidget.DockWidgetMovable
                | QDockWidget.DockWidgetFloatable
                | QDockWidget.DockWidgetClosable
            )
            dock.setMinimumHeight(90)
            self.ide._find_results_dock = dock
            created = True
            dock.destroyed.connect(lambda *_: setattr(self.ide, "_find_results_dock", None))

        panel = dock.widget()
        if not isinstance(panel, FindInFilesResultsWidget) or not _is_qobject_valid(panel):
            panel = FindInFilesResultsWidget(self.ide)
            panel.resultActivated.connect(self._on_problem_activated)
            dock.setWidget(panel)

        panel.set_results(
            payload.get("results"),
            summary_text=str(payload.get("summary_text") or ""),
        )
        dock.setWindowTitle(title)

        if created:
            self.ide.addDockWidget(Qt.BottomDockWidgetArea, dock)
            anchor = self.ide.dock_usages
            if isinstance(anchor, QDockWidget) and _is_qobject_valid(anchor):
                try:
                    self.ide.tabifyDockWidget(anchor, dock)
                except Exception:
                    pass
        dock.show()
        dock.raise_()

    def _on_add_find_results_dock_requested(self, payload: object) -> None:
        request = payload if isinstance(payload, dict) else {}
        self._create_find_results_dock(request)
