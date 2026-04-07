"""Spell checking manager for active editor tabs."""

from __future__ import annotations

import re
import weakref
import os
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QMenu
from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Literal, Name

from barley_ide.storage_paths import ide_spell_words_path
from barley_ide.ui.editor_workspace import EditorWidget
from TPOPyside.widgets.tdoc_support import TDocDocumentWidget

try:
    from spellchecker import SpellChecker
except Exception:  # pragma: no cover - runtime optional dependency.
    SpellChecker = None


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']{1,}")
_IDENTIFIER_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IDENTIFIER_PART_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+")
_TEXTLIKE_LANGUAGE_IDS: set[str] = {
    "plaintext",
    "text",
    "markdown",
    "md",
    "todo",
}
_WORD_SPAN_CHARS_RE = re.compile(r"[A-Za-z']")

_LANGUAGE_TO_LEXER: dict[str, str] = {
    "python": "python",
    "cpp": "cpp",
    "c": "c",
    "rust": "rust",
    "json": "json",
    "javascript": "javascript",
    "js": "javascript",
    "css": "css",
    "html": "html",
    "markdown": "markdown",
    "md": "markdown",
    "bash": "bash",
    "sh": "bash",
    "plaintext": "text",
    "text": "text",
}

_AUTO_ALLOW_WORDS: set[str] = {
    "ai",
    "api",
    "args",
    "autosave",
    "bool",
    "cfg",
    "clangd",
    "config",
    "configs",
    "cpp",
    "ctrl",
    "debounce",
    "dialog",
    "doctype",
    "env",
    "filepath",
    "fixme",
    "gui",
    "ide",
    "init",
    "json",
    "kwargs",
    "lint",
    "localhost",
    "lsp",
    "md",
    "pyside",
    "pyside6",
    "pyqt",
    "pytpo",
    "qdialog",
    "qmainwindow",
    "qobject",
    "qwidget",
    "repo",
    "rust",
    "stderr",
    "stdin",
    "stdout",
    "tdoc",
    "todo",
    "toml",
    "ui",
    "url",
    "utf",
    "ux",
    "yaml",
}


class SpellcheckManager(QObject):
    """Handles active-tab spell checking and editor underline updates."""

    def __init__(self, ide, parent=None):
        super().__init__(parent or ide)
        self.ide = ide
        self._active_widget_ref: weakref.ReferenceType | None = None
        self._enabled = False
        self._debounce_ms = 420
        self._color = "#66C07A"
        self._check_identifiers_in_code = False
        self._max_highlights = 1400
        self._backend_unavailable_notified = False
        self._user_words_cache: set[str] = set()
        self._user_words_sig: tuple[int, int] | None = None
        self._project_words_cache: set[str] = set()
        self._project_words_sig: tuple[int, int] | None = None
        self._session_words: set[str] = set()
        self._ignore_by_language: dict[str, set[str]] = {}
        self._ignore_by_file: dict[str, set[str]] = {}

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(self._debounce_ms)
        self._debounce_timer.timeout.connect(self._run_spellcheck_for_active_widget)

        self.reload_settings(initial=True)

    def shutdown(self) -> None:
        self._debounce_timer.stop()

    @staticmethod
    def _coerce_bool(value: object, *, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on", "y"}:
            return True
        if text in {"0", "false", "no", "off", "n", ""}:
            return False
        return bool(default)

    def is_enabled(self) -> bool:
        return bool(self._enabled)

    def set_enabled(self, enabled: bool, *, persist: bool = True) -> bool:
        requested = bool(enabled)
        if requested and SpellChecker is None:
            requested = False
            if not self._backend_unavailable_notified:
                self.ide.statusBar().showMessage(
                    "Spell check backend missing. Install pyspellchecker.",
                    3600,
                )
                self._backend_unavailable_notified = True

        changed = requested != self._enabled
        self._enabled = requested
        self._apply_visual_settings_to_open_widgets()

        if persist:
            self.ide.settings_manager.set("editor.spellcheck.enabled", bool(self._enabled), "ide")
            try:
                self.ide.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
            except Exception:
                pass

        if not self._enabled:
            self._debounce_timer.stop()
            self.clear_all_highlights()
        elif changed:
            self.refresh_active_widget(immediate=True)

        return bool(self._enabled)

    def reload_settings(self, *, initial: bool = False) -> None:
        raw = self.ide.settings_manager.get("editor.spellcheck", scope_preference="ide", default={})
        cfg = raw if isinstance(raw, dict) else {}
        self._ignore_by_language = self._normalize_ignore_map(cfg.get("ignore_by_language"), key_mode="language")
        project_ignores = self.ide.settings_manager.get(
            "spellcheck.ignore_by_file",
            scope_preference="project",
            default={},
        )
        self._ignore_by_file = self._normalize_ignore_map(project_ignores, key_mode="file")

        try:
            debounce = int(cfg.get("debounce_ms", 420))
        except Exception:
            debounce = 420
        self._debounce_ms = max(120, min(2400, debounce))
        self._debounce_timer.setInterval(self._debounce_ms)

        color = str(cfg.get("color") or "#66C07A").strip()
        if re.match(r"^#[0-9a-fA-F]{6}$", color):
            self._color = color
        else:
            self._color = "#66C07A"

        self._check_identifiers_in_code = self._coerce_bool(
            cfg.get("check_identifiers_in_code", False),
            default=False,
        )
        try:
            max_marks = int(cfg.get("max_highlights", 1400))
        except Exception:
            max_marks = 1400
        self._max_highlights = max(100, min(5000, max_marks))

        requested_enabled = self._coerce_bool(cfg.get("enabled", False), default=False)
        if requested_enabled and SpellChecker is None:
            if not self._backend_unavailable_notified:
                self.ide.statusBar().showMessage(
                    "Spell check backend missing. Install pyspellchecker.",
                    3600,
                )
                self._backend_unavailable_notified = True
            self.ide.settings_manager.set("editor.spellcheck.enabled", False, "ide")
            try:
                self.ide.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
            except Exception:
                pass
            requested_enabled = False
        self._enabled = bool(requested_enabled and SpellChecker is not None)
        self._apply_visual_settings_to_open_widgets()

        if not self._enabled:
            self.clear_all_highlights()
            return
        if not initial:
            self.refresh_active_widget(immediate=True)

    def refresh_active_widget(self, *, immediate: bool = False) -> None:
        current = self._coerce_spellcheck_widget(self.ide._current_document_widget())
        previous = self._active_widget()
        if previous is not None and previous is not current:
            self._clear_widget_highlights(previous)

        self._active_widget_ref = weakref.ref(current) if current is not None else None
        if current is not None:
            self._apply_visual_settings_to_widget(current)
            if self._widget_uses_reduced_capability_mode(current):
                self._clear_widget_highlights(current)

        if not self._enabled or current is None:
            return
        if self._widget_uses_reduced_capability_mode(current):
            return
        self._schedule_check(immediate=immediate)

    def on_document_text_changed(self, widget: object) -> None:
        if not self._enabled:
            return
        target = self._coerce_spellcheck_widget(widget)
        if target is None:
            return
        if self._widget_uses_reduced_capability_mode(target):
            self._clear_widget_highlights(target)
            return
        active = self._active_widget()
        if active is None or active is not target:
            return
        self._schedule_check(immediate=False)

    def clear_all_highlights(self) -> None:
        for widget in self._iter_open_document_widgets_safe():
            target = self._coerce_spellcheck_widget(widget)
            if target is None:
                continue
            self._clear_widget_highlights(target)

    def visual_settings_payload(self) -> dict[str, object]:
        return {
            "enabled": bool(self._enabled),
            "color": str(self._color),
        }

    def append_context_menu_actions(
        self,
        widget: object,
        menu_obj: object,
        payload_obj: object,
    ) -> None:
        widget_obj = self._coerce_spellcheck_widget(widget)
        if widget_obj is None:
            return
        if self._widget_uses_reduced_capability_mode(widget_obj):
            return
        if not isinstance(menu_obj, QMenu):
            return
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        span = self._word_span_from_payload(widget_obj, payload)
        if span is None:
            return
        start, end, raw_word = span
        normalized = self._normalize_word(raw_word)
        if not self._should_spellcheck_word(normalized, from_identifier=False):
            return

        menu_obj.addSeparator()
        spell_menu = menu_obj.addMenu(f"Spelling: {raw_word}")
        try:
            suggestions = self._suggestions_for_word(normalized, limit=7)
        except Exception:
            suggestions = []
        if suggestions:
            for suggestion in suggestions:
                action = spell_menu.addAction(str(suggestion))
                action.triggered.connect(
                    lambda _checked=False, w=widget_obj, s=start, e=end, repl=suggestion: self._replace_word_span(
                        w, s, e, repl
                    )
                )
        else:
            empty = spell_menu.addAction("No suggestions")
            empty.setEnabled(False)

        spell_menu.addSeparator()
        add_session = spell_menu.addAction("Add to Session Dictionary")
        add_session.triggered.connect(
            lambda _checked=False, word=raw_word: self._add_word_from_context(word, "session")
        )
        add_project = spell_menu.addAction("Add to Project Dictionary")
        add_project.triggered.connect(
            lambda _checked=False, word=raw_word: self._add_word_from_context(word, "project")
        )
        add_ide = spell_menu.addAction("Add to IDE Dictionary")
        add_ide.triggered.connect(
            lambda _checked=False, word=raw_word: self._add_word_from_context(word, "ide")
        )
        spell_menu.addSeparator()
        file_key = self._file_key_for_widget(widget_obj)
        if self._is_word_ignored_in_file(raw_word, widget_obj):
            ignore_file = spell_menu.addAction(
                f"Unignore in This File ({self._file_action_label(widget_obj)})"
            )
            ignore_file.setEnabled(bool(file_key))
            ignore_file.triggered.connect(
                lambda _checked=False, word=raw_word, w=widget_obj: self._unignore_word_for_file(word, w)
            )
        else:
            ignore_file = spell_menu.addAction(
                f"Ignore in This File ({self._file_action_label(widget_obj)})"
            )
            ignore_file.setEnabled(bool(file_key))
            ignore_file.triggered.connect(
                lambda _checked=False, word=raw_word, w=widget_obj: self._ignore_word_for_file(word, w)
            )
        language_label = self._language_action_label(widget_obj)
        if self._is_word_ignored_in_language(raw_word, widget_obj):
            ignore_language = spell_menu.addAction(f"Unignore in Language ({language_label})")
            ignore_language.setEnabled(bool(language_label))
            ignore_language.triggered.connect(
                lambda _checked=False, word=raw_word, w=widget_obj: self._unignore_word_for_language(word, w)
            )
        else:
            ignore_language = spell_menu.addAction(f"Ignore in Language ({language_label})")
            ignore_language.setEnabled(bool(language_label))
            ignore_language.triggered.connect(
                lambda _checked=False, word=raw_word, w=widget_obj: self._ignore_word_for_language(word, w)
            )

    def _active_widget(self) -> EditorWidget | TDocDocumentWidget | None:
        if self._active_widget_ref is None:
            return None
        try:
            obj = self._active_widget_ref()
        except Exception:
            obj = None
        return self._coerce_spellcheck_widget(obj)

    def _schedule_check(self, *, immediate: bool) -> None:
        self._debounce_timer.stop()
        if immediate:
            self._run_spellcheck_for_active_widget()
            return
        self._debounce_timer.start(self._debounce_ms)

    def _run_spellcheck_for_active_widget(self) -> None:
        if not self._enabled or SpellChecker is None:
            return
        widget = self._active_widget()
        if widget is None:
            return
        if self._widget_uses_reduced_capability_mode(widget):
            self._clear_widget_highlights(widget)
            return
        text = self._document_text(widget)
        if not text:
            self._clear_widget_highlights(widget)
            return

        occurrences = self._collect_word_occurrences(widget, text)
        if not occurrences:
            self._set_widget_diagnostics(widget, [])
            return

        checker = SpellChecker(distance=1)
        checker.word_frequency.load_words(_AUTO_ALLOW_WORDS)
        checker.word_frequency.load_words(self._session_words)
        checker.word_frequency.load_words(self._load_project_words())
        checker.word_frequency.load_words(self._load_user_words())
        checker.word_frequency.load_words(self._ignored_words_for_widget(widget))

        unique_words = {word for _, _, word in occurrences}
        unknown = checker.unknown(unique_words)
        if not unknown:
            self._set_widget_diagnostics(widget, [])
            return

        diagnostics: list[dict[str, int]] = []
        seen_spans: set[tuple[int, int]] = set()
        for start, end, normalized in occurrences:
            if normalized not in unknown:
                continue
            key = (start, end)
            if key in seen_spans:
                continue
            seen_spans.add(key)
            diagnostics.append({"start": start, "end": end})
            if len(diagnostics) >= self._max_highlights:
                break
        self._set_widget_diagnostics(widget, diagnostics)

    def _collect_word_occurrences(
        self,
        widget: EditorWidget | TDocDocumentWidget,
        text: str,
    ) -> list[tuple[int, int, str]]:
        occurrences: list[tuple[int, int, str]] = []
        if isinstance(widget, TDocDocumentWidget):
            spans = [(0, len(text), False)]
        else:
            language_id = str(widget.language_id() or "").strip().lower()
            if language_id in _TEXTLIKE_LANGUAGE_IDS:
                spans = [(0, len(text), False)]
            else:
                spans = self._code_spans(
                    text=text,
                    language_id=language_id,
                    include_identifiers=bool(self._check_identifiers_in_code),
                )

        for start, end, is_identifier in spans:
            if end <= start:
                continue
            fragment = text[start:end]
            if not fragment:
                continue
            if is_identifier:
                for word, rel_start, rel_end in self._identifier_parts(fragment):
                    normalized = self._normalize_word(word)
                    if not self._should_spellcheck_word(normalized, from_identifier=True):
                        continue
                    occurrences.append((start + rel_start, start + rel_end, normalized))
                continue
            for match in _WORD_RE.finditer(fragment):
                raw = match.group(0)
                normalized = self._normalize_word(raw)
                if not self._should_spellcheck_word(normalized, from_identifier=False):
                    continue
                occurrences.append((start + int(match.start()), start + int(match.end()), normalized))
        return occurrences

    def _code_spans(
        self,
        *,
        text: str,
        language_id: str,
        include_identifiers: bool,
    ) -> list[tuple[int, int, bool]]:
        spans: list[tuple[int, int, bool]] = []
        lexer_name = _LANGUAGE_TO_LEXER.get(language_id, language_id or "text")
        try:
            lexer = get_lexer_by_name(lexer_name)
        except Exception:
            lexer = None

        if lexer is None:
            if include_identifiers:
                spans.append((0, len(text), True))
            return spans

        offset = 0
        for token_type, value in lex(text, lexer):
            length = len(value)
            if length <= 0:
                continue
            start = offset
            end = start + length
            offset = end
            if token_type in Comment or token_type in Literal.String:
                spans.append((start, end, False))
                continue
            if include_identifiers and token_type in Name:
                spans.append((start, end, True))
        return spans

    def _identifier_parts(self, fragment: str) -> list[tuple[str, int, int]]:
        parts: list[tuple[str, int, int]] = []
        for token in _IDENTIFIER_TOKEN_RE.finditer(fragment):
            token_text = token.group(0)
            token_start = int(token.start())
            for chunk in re.finditer(r"[A-Za-z]+", token_text):
                chunk_text = chunk.group(0)
                chunk_start = token_start + int(chunk.start())
                for part in _IDENTIFIER_PART_RE.finditer(chunk_text):
                    parts.append(
                        (
                            part.group(0),
                            chunk_start + int(part.start()),
                            chunk_start + int(part.end()),
                        )
                    )
        return parts

    @staticmethod
    def _normalize_word(raw: str) -> str:
        lowered = str(raw or "").strip().strip("'").lower()
        return lowered.replace("'", "")

    def _should_spellcheck_word(self, word: str, *, from_identifier: bool) -> bool:
        if not word:
            return False
        if len(word) < 3:
            return False
        if not word.isalpha():
            return False
        if word in _AUTO_ALLOW_WORDS:
            return False
        if from_identifier and len(word) < 4:
            return False
        return True

    def _load_user_words(self) -> set[str]:
        path = self._ide_dictionary_path()
        if not path.exists():
            self._user_words_cache = set()
            self._user_words_sig = None
            return set()
        try:
            stat = path.stat()
            sig = (int(stat.st_mtime_ns), int(stat.st_size))
        except Exception:
            return set(self._user_words_cache)
        if self._user_words_sig == sig:
            return set(self._user_words_cache)

        words: set[str] = set()
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = str(raw_line or "").strip()
                if not line or line.startswith("#"):
                    continue
                normalized = self._normalize_word(line)
                if not normalized or not normalized.isalpha():
                    continue
                words.add(normalized)
        except Exception:
            return set(self._user_words_cache)

        self._user_words_sig = sig
        self._user_words_cache = words
        return set(words)

    def _load_project_words(self) -> set[str]:
        path = self._project_dictionary_path()
        if not path.exists():
            self._project_words_cache = set()
            self._project_words_sig = None
            return set()
        try:
            stat = path.stat()
            sig = (int(stat.st_mtime_ns), int(stat.st_size))
        except Exception:
            return set(self._project_words_cache)
        if self._project_words_sig == sig:
            return set(self._project_words_cache)

        words: set[str] = set()
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = str(raw_line or "").strip()
                if not line or line.startswith("#"):
                    continue
                normalized = self._normalize_word(line)
                if not normalized or not normalized.isalpha():
                    continue
                words.add(normalized)
        except Exception:
            return set(self._project_words_cache)
        self._project_words_sig = sig
        self._project_words_cache = words
        return set(words)

    def _ide_dictionary_path(self) -> Path:
        return ide_spell_words_path()

    def _project_dictionary_path(self) -> Path:
        return Path(str(self.ide.project_root)) / ".tide" / "spell-project-words.txt"

    def _document_text(self, widget: EditorWidget | TDocDocumentWidget) -> str:
        getter = getattr(widget, "toPlainText", None)
        if callable(getter):
            try:
                return str(getter())
            except Exception:
                pass
        serializer = getattr(widget, "serialized_text", None)
        if callable(serializer):
            try:
                return str(serializer())
            except Exception:
                return ""
        return ""

    def _coerce_spellcheck_widget(self, widget: object) -> EditorWidget | TDocDocumentWidget | None:
        if isinstance(widget, (EditorWidget, TDocDocumentWidget)):
            return widget
        getter = getattr(widget, "editor_widget", None)
        if callable(getter):
            try:
                resolved = getter()
            except Exception:
                resolved = None
            if isinstance(resolved, (EditorWidget, TDocDocumentWidget)):
                return resolved
        return None

    def _apply_visual_settings_to_open_widgets(self) -> None:
        for widget in self._iter_open_document_widgets_safe():
            target = self._coerce_spellcheck_widget(widget)
            if target is None:
                continue
            self._apply_visual_settings_to_widget(target)

    def _iter_open_document_widgets_safe(self) -> list[object]:
        getter = getattr(self.ide, "_iter_open_document_widgets", None)
        if not callable(getter):
            return []
        try:
            rows = getter()
        except Exception:
            return []
        return rows if isinstance(rows, list) else []

    def _apply_visual_settings_to_widget(self, widget: EditorWidget | TDocDocumentWidget) -> None:
        setter = getattr(widget, "update_spellcheck_visual_settings", None)
        if not callable(setter):
            return
        try:
            payload = self.visual_settings_payload()
            if self._widget_uses_reduced_capability_mode(widget):
                payload = dict(payload, enabled=False)
            setter(payload)
        except Exception:
            pass

    @staticmethod
    def _widget_uses_reduced_capability_mode(widget: object) -> bool:
        getter = getattr(widget, "is_reduced_capability_mode", None)
        if not callable(getter):
            return False
        try:
            return bool(getter())
        except Exception:
            return False

    def _set_widget_diagnostics(
        self,
        widget: EditorWidget | TDocDocumentWidget,
        diagnostics: list[dict[str, int]],
    ) -> None:
        setter = getattr(widget, "set_spellcheck_diagnostics", None)
        if not callable(setter):
            return
        try:
            setter(diagnostics)
        except Exception:
            pass

    def _clear_widget_highlights(self, widget: EditorWidget | TDocDocumentWidget) -> None:
        clearer = getattr(widget, "clear_spellcheck_diagnostics", None)
        if not callable(clearer):
            return
        try:
            clearer()
        except Exception:
            pass

    def _suggestions_for_word(self, word: str, *, limit: int = 7) -> list[str]:
        normalized = self._normalize_word(word)
        if not normalized or SpellChecker is None:
            return []
        checker = SpellChecker(distance=1)
        checker.word_frequency.load_words(_AUTO_ALLOW_WORDS)
        checker.word_frequency.load_words(self._session_words)
        checker.word_frequency.load_words(self._load_project_words())
        checker.word_frequency.load_words(self._load_user_words())
        # Include configured ignore sets so ignored words do not produce alternatives.
        for words in self._ignore_by_language.values():
            checker.word_frequency.load_words(words)
        for words in self._ignore_by_file.values():
            checker.word_frequency.load_words(words)
        unknown = checker.unknown([normalized])
        if not unknown:
            return []
        try:
            candidates = set(checker.candidates(normalized) or [])
        except Exception:
            candidates = set()
        candidates.discard(normalized)
        if not candidates:
            return []
        correction = str(checker.correction(normalized) or "").strip().lower()

        def _score(item: str) -> tuple[int, float, int, str]:
            text = str(item or "").strip().lower()
            bonus = 1 if text == correction and correction else 0
            try:
                freq = float(checker.word_usage_frequency(text))
            except Exception:
                freq = 0.0
            return (-bonus, -freq, abs(len(text) - len(normalized)), text)

        ranked = sorted((str(item) for item in candidates), key=_score)
        return [str(item) for item in ranked[: max(1, int(limit))]]

    def _word_span_from_payload(
        self,
        widget: EditorWidget | TDocDocumentWidget,
        payload: dict,
    ) -> tuple[int, int, str] | None:
        cursor = None
        local_pos = payload.get("local_pos")
        if isinstance(local_pos, QPoint):
            getter = getattr(widget, "cursorForPosition", None)
            if callable(getter):
                try:
                    cursor = getter(local_pos)
                except Exception:
                    cursor = None
        if not isinstance(cursor, QTextCursor):
            try:
                cursor = QTextCursor(widget.textCursor())
            except Exception:
                return None

        text = self._document_text(widget)
        if not text:
            return None
        pos = max(0, min(int(cursor.position()), len(text)))
        if pos >= len(text):
            pos = max(0, len(text) - 1)
        if not text:
            return None
        if pos < len(text) and not _WORD_SPAN_CHARS_RE.match(text[pos]):
            if pos > 0 and _WORD_SPAN_CHARS_RE.match(text[pos - 1]):
                pos -= 1
            else:
                return None

        start = pos
        while start > 0 and _WORD_SPAN_CHARS_RE.match(text[start - 1]):
            start -= 1
        end = pos + 1
        while end < len(text) and _WORD_SPAN_CHARS_RE.match(text[end]):
            end += 1
        raw_word = text[start:end]
        if not raw_word.strip():
            return None
        return (int(start), int(end), raw_word)

    def _replace_word_span(
        self,
        widget: EditorWidget | TDocDocumentWidget,
        start: int,
        end: int,
        replacement: str,
    ) -> None:
        value = str(replacement or "").strip()
        if not value:
            return
        doc = widget.document()
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        try:
            edit.setPosition(max(0, int(start)))
            edit.setPosition(max(0, int(end)), QTextCursor.KeepAnchor)
            edit.insertText(value)
        finally:
            edit.endEditBlock()
        self.refresh_active_widget(immediate=False)

    def _add_word_from_context(
        self,
        raw_word: str,
        scope: str,
    ) -> None:
        word = self._normalize_word(raw_word)
        if not word or not word.isalpha():
            return
        if scope == "session":
            self._session_words.add(word)
            self.ide.statusBar().showMessage(f"Added '{word}' to session dictionary.", 2000)
            self.refresh_active_widget(immediate=True)
            return

        if scope == "project":
            path = self._project_dictionary_path()
            label = "project"
        else:
            path = self._ide_dictionary_path()
            label = "IDE"

        if self._append_word_to_dictionary_file(path, word):
            if scope == "project":
                self._project_words_sig = None
            else:
                self._user_words_sig = None
            self.ide.statusBar().showMessage(f"Added '{word}' to {label} dictionary.", 2200)
            self.refresh_active_widget(immediate=True)
            return
        self.ide.statusBar().showMessage(f"Could not update {label} dictionary.", 2600)

    def _ignore_word_for_file(self, raw_word: str, widget: EditorWidget | TDocDocumentWidget) -> None:
        word = self._normalize_word(raw_word)
        key = self._file_key_for_widget(widget)
        if not word or not word.isalpha() or not key:
            return
        bucket = self._ignore_by_file.setdefault(key, set())
        if word in bucket:
            self.ide.statusBar().showMessage(f"'{word}' is already ignored in this file.", 1800)
            return
        bucket.add(word)
        self._persist_file_ignores()
        self.ide.statusBar().showMessage(f"Ignoring '{word}' in this file.", 2200)
        self.refresh_active_widget(immediate=True)

    def _ignore_word_for_language(self, raw_word: str, widget: EditorWidget | TDocDocumentWidget) -> None:
        word = self._normalize_word(raw_word)
        language = self._language_key_for_widget(widget)
        if not word or not word.isalpha() or not language:
            return
        bucket = self._ignore_by_language.setdefault(language, set())
        if word in bucket:
            self.ide.statusBar().showMessage(f"'{word}' is already ignored for {language}.", 1800)
            return
        bucket.add(word)
        self._persist_language_ignores()
        self.ide.statusBar().showMessage(f"Ignoring '{word}' for {language}.", 2200)
        self.refresh_active_widget(immediate=True)

    def _unignore_word_for_file(self, raw_word: str, widget: EditorWidget | TDocDocumentWidget) -> None:
        word = self._normalize_word(raw_word)
        key = self._file_key_for_widget(widget)
        if not word or not key:
            return
        bucket = self._ignore_by_file.get(key)
        if not isinstance(bucket, set) or word not in bucket:
            return
        bucket.discard(word)
        if not bucket:
            self._ignore_by_file.pop(key, None)
        self._persist_file_ignores()
        self.ide.statusBar().showMessage(f"Removed file ignore for '{word}'.", 2200)
        self.refresh_active_widget(immediate=True)

    def _unignore_word_for_language(self, raw_word: str, widget: EditorWidget | TDocDocumentWidget) -> None:
        word = self._normalize_word(raw_word)
        language = self._language_key_for_widget(widget)
        if not word or not language:
            return
        bucket = self._ignore_by_language.get(language)
        if not isinstance(bucket, set) or word not in bucket:
            return
        bucket.discard(word)
        if not bucket:
            self._ignore_by_language.pop(language, None)
        self._persist_language_ignores()
        self.ide.statusBar().showMessage(f"Removed {language} ignore for '{word}'.", 2200)
        self.refresh_active_widget(immediate=True)

    def _append_word_to_dictionary_file(self, path: Path, word: str) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existing: set[str] = set()
            if path.exists():
                for raw_line in path.read_text(encoding="utf-8").splitlines():
                    normalized = self._normalize_word(raw_line)
                    if normalized and normalized.isalpha():
                        existing.add(normalized)
            existing.add(word)
            payload = "\n".join(sorted(existing)) + "\n"
            path.write_text(payload, encoding="utf-8")
            return True
        except Exception:
            return False

    def _file_action_label(self, widget: EditorWidget | TDocDocumentWidget) -> str:
        key = self._file_key_for_widget(widget)
        if not key:
            return "Unsaved"
        return os.path.basename(key) or key

    def _language_action_label(self, widget: EditorWidget | TDocDocumentWidget) -> str:
        language = self._language_key_for_widget(widget)
        return language or ""

    def _language_key_for_widget(self, widget: EditorWidget | TDocDocumentWidget) -> str:
        if isinstance(widget, TDocDocumentWidget):
            return "tdoc"
        try:
            return str(widget.language_id() or "").strip().lower()
        except Exception:
            return ""

    def _file_key_for_widget(self, widget: EditorWidget | TDocDocumentWidget) -> str:
        path = str(getattr(widget, "file_path", "") or "").strip()
        if not path:
            return ""
        canonicalizer = getattr(self.ide, "_canonical_path", None)
        if callable(canonicalizer):
            try:
                return str(canonicalizer(path))
            except Exception:
                pass
        return path

    def _ignored_words_for_widget(self, widget: EditorWidget | TDocDocumentWidget) -> set[str]:
        words: set[str] = set()
        language = self._language_key_for_widget(widget)
        if language:
            words.update(self._ignore_by_language.get(language, set()))
        file_key = self._file_key_for_widget(widget)
        if file_key:
            words.update(self._ignore_by_file.get(file_key, set()))
        return words

    def _is_word_ignored_in_file(self, raw_word: str, widget: EditorWidget | TDocDocumentWidget) -> bool:
        word = self._normalize_word(raw_word)
        key = self._file_key_for_widget(widget)
        if not word or not key:
            return False
        return word in self._ignore_by_file.get(key, set())

    def _is_word_ignored_in_language(self, raw_word: str, widget: EditorWidget | TDocDocumentWidget) -> bool:
        word = self._normalize_word(raw_word)
        language = self._language_key_for_widget(widget)
        if not word or not language:
            return False
        return word in self._ignore_by_language.get(language, set())

    def _normalize_ignore_map(self, raw_map: object, *, key_mode: str) -> dict[str, set[str]]:
        if not isinstance(raw_map, dict):
            return {}
        out: dict[str, set[str]] = {}
        for raw_key, raw_values in raw_map.items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            if key_mode == "language":
                key = key.lower()
            if not isinstance(raw_values, list):
                continue
            words: set[str] = set()
            for item in raw_values:
                normalized = self._normalize_word(item)
                if normalized and normalized.isalpha():
                    words.add(normalized)
            if words:
                out[key] = words
        return out

    @staticmethod
    def _serialize_ignore_map(data: dict[str, set[str]]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for key, words in data.items():
            text = str(key or "").strip()
            if not text:
                continue
            clean = sorted({str(word or "").strip().lower() for word in words if str(word or "").strip()})
            if clean:
                out[text] = clean
        return out

    def _persist_language_ignores(self) -> None:
        self.ide.settings_manager.set(
            "editor.spellcheck.ignore_by_language",
            self._serialize_ignore_map(self._ignore_by_language),
            "ide",
        )
        try:
            self.ide.settings_manager.save_all(scopes={"ide"}, only_dirty=True)
        except Exception:
            pass

    def _persist_file_ignores(self) -> None:
        self.ide.settings_manager.set(
            "spellcheck.ignore_by_file",
            self._serialize_ignore_map(self._ignore_by_file),
            "project",
        )
        try:
            self.ide.settings_manager.save_all(scopes={"project"}, only_dirty=True)
        except Exception:
            pass
