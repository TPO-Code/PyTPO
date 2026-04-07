"""Controller for autosave and external-file change detection."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from barley_ide.ui.editor_workspace import EditorWidget


class WorkspaceController(QObject):
    def __init__(self, ide, parent=None):
        super().__init__(parent or ide)
        self.ide = ide

        self._external_file_signatures: dict[str, tuple[bool, int, int]] = {}
        self._external_conflict_signatures: dict[str, tuple[bool, int, int]] = {}

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave_dirty_editors)

        self._external_file_watch_timer = QTimer(self)
        self._external_file_watch_timer.setInterval(1500)
        self._external_file_watch_timer.timeout.connect(self._check_external_file_updates)

        self.ide._autosave_timer = self._autosave_timer
        self.ide._external_file_watch_timer = self._external_file_watch_timer
        self.ide._external_file_signatures = self._external_file_signatures
        self.ide._external_conflict_signatures = self._external_conflict_signatures

    def __getattr__(self, name: str):
        return getattr(self.ide, name)

    @property
    def autosave_timer(self) -> QTimer:
        return self._autosave_timer

    @property
    def external_watch_timer(self) -> QTimer:
        return self._external_file_watch_timer

    def _configure_autosave_timer(self) -> None:
        if self.ide.is_project_read_only() or not bool(self._autosave_config().get("enabled", False)):
            self._autosave_timer.stop()

    def _schedule_autosave(self) -> None:
        if self.ide.is_project_read_only():
            self._autosave_timer.stop()
            return
        cfg = self._autosave_config()
        if not bool(cfg.get("enabled", False)):
            self._autosave_timer.stop()
            return
        try:
            delay_ms = max(250, min(30000, int(cfg.get("debounce_ms", 1200))))
        except Exception:
            delay_ms = 1200
        self._autosave_timer.start(delay_ms)

    def _autosave_dirty_editors(self) -> None:
        if self.ide.is_project_read_only():
            return
        if not bool(self._autosave_config().get("enabled", False)):
            return

        seen_docs: set[str] = set()
        save_targets: list[object] = []
        for widget in self._iter_open_document_widgets():
            path = self._document_widget_path(widget)
            if not path:
                continue
            code_editor = self._editor_from_document_widget(widget)
            if isinstance(code_editor, EditorWidget):
                doc_key = self._doc_key_for_editor(code_editor)
            else:
                doc_key = self._canonical_path(path)
            if doc_key in seen_docs:
                continue
            seen_docs.add(doc_key)

            doc_getter = getattr(widget, "document", None)
            if not callable(doc_getter):
                continue
            try:
                doc = doc_getter()
                modified = bool(doc.isModified())
            except Exception:
                continue
            if not modified:
                continue
            save_targets.append(widget)

        if not save_targets:
            return

        saved_count = 0
        refresh_dirs: set[str] = set()
        for widget in save_targets:
            path = self._document_widget_path(widget)
            if not path:
                continue
            saver = getattr(widget, "save_file", None)
            if not callable(saver):
                continue
            if not saver():
                continue
            saved_count += 1
            cpath = self._canonical_path(path)
            refresh_dirs.add(os.path.dirname(cpath))
            self._note_editor_saved(widget, source="autosave")
            if isinstance(code_editor, EditorWidget):
                self._attach_editor_lint_hooks(code_editor)
                self._request_lint_for_editor(code_editor, reason="save", include_source_if_modified=False)
            elif self._is_tdoc_related_path(cpath):
                self._schedule_tdoc_validation(cpath, delay_ms=0)

        for folder in refresh_dirs:
            self.refresh_subtree(folder)

        if saved_count:
            self.ide.statusBar().showMessage(f"Auto-saved {saved_count} file(s).", 1400)
            self.schedule_git_status_refresh(delay_ms=120)

    def _note_editor_saved(self, ed: object, *, source: str) -> None:
        path = self._document_widget_path(ed)
        if not path:
            return
        saved_path = self._canonical_path(path)
        code_editor = self._editor_from_document_widget(ed)
        if isinstance(code_editor, EditorWidget):
            cpp_pack = getattr(self.ide, "cpp_language_pack", None)
            on_saved = getattr(cpp_pack, "on_document_saved", None)
            if callable(on_saved):
                try:
                    on_saved(file_path=saved_path, source_text=code_editor.toPlainText())
                except Exception:
                    pass
            rust_pack = getattr(self.ide, "rust_language_pack", None)
            rust_on_saved = getattr(rust_pack, "on_document_saved", None)
            if callable(rust_on_saved):
                try:
                    rust_on_saved(file_path=saved_path, source_text=code_editor.toPlainText())
                except Exception:
                    pass
        elif self._is_tdoc_related_path(saved_path):
            self._schedule_tdoc_validation(saved_path, delay_ms=0)
        self._external_conflict_signatures.pop(saved_path, None)
        sig = self._external_file_signature(saved_path)
        if sig is not None:
            self._external_file_signatures[saved_path] = sig
        if self._is_project_config_path(saved_path) and not self.ide._project_config_reload_active:
            self._queue_project_config_reload(source=source, honor_open_editors=True)
        theme_controller = getattr(self.ide, "theme_controller", None)
        refresh_theme = getattr(theme_controller, "refresh_active_theme_for_saved_path", None)
        if callable(refresh_theme):
            try:
                refresh_theme(saved_path)
            except Exception:
                pass
        change_highlights = getattr(self.ide, "editor_change_highlight_service", None)
        notifier = getattr(change_highlights, "notify_file_saved", None)
        if callable(notifier):
            try:
                notifier(saved_path)
            except Exception:
                pass
        self._reload_non_text_views_for_path(saved_path, exclude_widget=ed)

    def _external_file_signature(self, path: str) -> tuple[bool, int, int] | None:
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return (False, 0, 0)
        except Exception:
            return None
        return (True, int(stat.st_mtime_ns), int(stat.st_size))

    def _seed_external_file_watch_state(self) -> None:
        current_paths = self._collect_open_file_paths()
        self._external_file_signatures.clear()

        stale_conflicts = [
            key
            for key in self._external_conflict_signatures.keys()
            if key not in current_paths
        ]
        for key in stale_conflicts:
            self._external_conflict_signatures.pop(key, None)

        for path in current_paths:
            sig = self._external_file_signature(path)
            if sig is not None:
                self._external_file_signatures[path] = sig

    def _check_external_file_updates(self) -> None:
        current_paths = self._collect_open_file_paths()

        stale = [key for key in self._external_file_signatures.keys() if key not in current_paths]
        for key in stale:
            self._external_file_signatures.pop(key, None)

        stale_conflicts = [key for key in self._external_conflict_signatures.keys() if key not in current_paths]
        for key in stale_conflicts:
            self._external_conflict_signatures.pop(key, None)

        for path in current_paths:
            sig = self._external_file_signature(path)
            if sig is None:
                continue

            previous = self._external_file_signatures.get(path)
            self._external_file_signatures[path] = sig
            if previous is None or previous == sig:
                continue

            self._handle_external_file_change(path, sig)

    def _open_document_widgets_for_path(self, path: str) -> list[object]:
        target = self._canonical_path(path)
        matches: list[object] = []
        seen_widgets: set[int] = set()
        for widget in self._iter_open_document_widgets():
            widget_path = self._document_widget_path(widget)
            if not widget_path:
                continue
            if self._canonical_path(widget_path) != target:
                continue
            key = int(id(widget))
            if key in seen_widgets:
                continue
            seen_widgets.add(key)
            matches.append(widget)
        return matches

    def _refresh_widget_tab_title(self, widget: object) -> None:
        for tabs in self.editor_workspace.all_tabs():
            idx = tabs.indexOf(widget)
            if idx >= 0:
                tabs._refresh_tab_title(widget)
                return

    def _reload_documentless_widget_from_disk(self, widget: object, path: str) -> bool:
        loader = getattr(widget, "load_file", None)
        if callable(loader):
            try:
                loaded = bool(loader(path))
            except Exception:
                return False
            if not loaded:
                return False
        elif callable(getattr(widget, "setPlainText", None)):
            try:
                widget.setPlainText(Path(path).read_text(encoding="utf-8"))
            except Exception:
                return False
        else:
            return False
        self._refresh_widget_tab_title(widget)
        if self._is_tdoc_related_path(path):
            self._schedule_tdoc_validation(path, delay_ms=0)
        return True

    def _reload_non_text_views_for_path(self, path: str, *, exclude_widget: object | None = None) -> None:
        for widget in self._open_document_widgets_for_path(path):
            if widget is exclude_widget:
                continue
            refresh_preview = getattr(widget, "refresh_preview_from_source", None)
            if callable(refresh_preview):
                try:
                    refresh_preview()
                except Exception:
                    pass
                continue
            if callable(getattr(widget, "document", None)):
                continue
            self._reload_documentless_widget_from_disk(widget, path)

    def _handle_external_file_change(self, path: str, sig: tuple[bool, int, int]) -> None:
        is_project_config = self._is_project_config_path(path)
        exists = bool(sig[0])
        if not exists:
            self.ide.statusBar().showMessage(f"File removed on disk: {os.path.basename(path)}", 2200)
            if is_project_config:
                self._queue_project_config_reload(source="project.json removed on disk", honor_open_editors=True)
            return

        widgets = self._open_document_widgets_for_path(path)
        if not widgets:
            if is_project_config:
                self._queue_project_config_reload(source="project.json changed on disk", honor_open_editors=True)
            return

        for widget in widgets:
            doc_getter = getattr(widget, "document", None)
            if not callable(doc_getter):
                continue
            try:
                modified = bool(doc_getter().isModified())
            except Exception:
                continue
            if not modified:
                continue
            previous_conflict = self._external_conflict_signatures.get(path)
            if previous_conflict != sig:
                self.ide.statusBar().showMessage(
                    f"Disk changed for {os.path.basename(path)} (kept local unsaved edits).",
                    2600,
                )
            self._external_conflict_signatures[path] = sig
            return

        disk_text: str | None = None
        disk_text_loaded = False
        self._external_conflict_signatures.pop(path, None)

        reloaded_any = False
        refreshed_doc_keys: set[str] = set()
        for widget in widgets:
            doc_getter = getattr(widget, "document", None)
            if not callable(doc_getter):
                reloaded_any = self._reload_documentless_widget_from_disk(widget, path) or reloaded_any
                continue

            try:
                doc = doc_getter()
            except Exception:
                continue

            code_editor = self._editor_from_document_widget(widget)
            if isinstance(code_editor, EditorWidget):
                doc_key = self._doc_key_for_editor(code_editor)
                if doc_key in refreshed_doc_keys:
                    continue
                refreshed_doc_keys.add(doc_key)

            if not disk_text_loaded:
                try:
                    disk_text = Path(path).read_text(encoding="utf-8")
                except Exception:
                    break
                disk_text_loaded = True

            serialize = getattr(widget, "serialized_text", None)
            if callable(serialize):
                try:
                    current_text = str(serialize())
                except Exception:
                    current_text = ""
            else:
                to_plain = getattr(widget, "toPlainText", None)
                try:
                    current_text = str(to_plain()) if callable(to_plain) else ""
                except Exception:
                    current_text = ""

            if current_text == str(disk_text or ""):
                try:
                    doc.setModified(False)
                except Exception:
                    pass
                if isinstance(code_editor, EditorWidget):
                    self._refresh_editor_title(code_editor)
                else:
                    self._refresh_widget_tab_title(widget)
                reloaded_any = True
                continue

            if isinstance(code_editor, EditorWidget):
                cursor = code_editor.textCursor()
                v_scroll = code_editor.verticalScrollBar().value()
                h_scroll = code_editor.horizontalScrollBar().value()
                try:
                    code_editor.setPlainText(str(disk_text or ""))
                    code_editor.document().setModified(False)
                    code_editor.setTextCursor(cursor)
                    code_editor.verticalScrollBar().setValue(v_scroll)
                    code_editor.horizontalScrollBar().setValue(h_scroll)
                except Exception:
                    continue
                self._refresh_editor_title(code_editor)
                self._attach_editor_lint_hooks(code_editor)
                self._request_lint_for_editor(code_editor, reason="open", include_source_if_modified=False)
            else:
                try:
                    widget.setPlainText(str(disk_text or ""))
                    doc.setModified(False)
                except Exception:
                    continue
                self._refresh_widget_tab_title(widget)
                if self._is_tdoc_related_path(path):
                    self._schedule_tdoc_validation(path, delay_ms=0)
            reloaded_any = True

        if not reloaded_any:
            return
        self.ide.statusBar().showMessage(f"Reloaded from disk: {os.path.basename(path)}", 1800)
        change_highlights = getattr(self.ide, "editor_change_highlight_service", None)
        notifier = getattr(change_highlights, "notify_file_reloaded", None)
        if callable(notifier):
            try:
                notifier(path)
            except Exception:
                pass
        if is_project_config:
            self._queue_project_config_reload(source="project.json changed on disk", honor_open_editors=True)

    def stop(self) -> None:
        self._autosave_timer.stop()
        self._external_file_watch_timer.stop()
