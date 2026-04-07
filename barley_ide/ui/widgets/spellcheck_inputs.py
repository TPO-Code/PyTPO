"""Spellcheck-enabled text input widgets for IDE-owned UI surfaces."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QColor, QContextMenuEvent, QPainter, QPen, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QStyle,
    QStyleOptionFrame,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from barley_ide.storage_paths import ide_spell_words_path

try:
    from spellchecker import SpellChecker
except Exception:  # pragma: no cover - optional runtime dependency.
    SpellChecker = None

_DEFAULT_COLOR = "#66C07A"
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']{1,}")
_WORD_SPAN_CHARS_RE = re.compile(r"[A-Za-z']")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

_AUTO_ALLOW_WORDS: set[str] = {
    "api",
    "args",
    "cpp",
    "env",
    "ide",
    "json",
    "md",
    "pyside",
    "pyside6",
    "pytpo",
    "qdialog",
    "qlineedit",
    "qmainwindow",
    "qobject",
    "qplain",
    "qplaintextedit",
    "qtextedit",
    "qwidget",
    "repo",
    "rust",
    "tdoc",
    "todo",
    "toml",
    "ui",
    "url",
    "yaml",
}

_DICT_CACHE: dict[str, tuple[tuple[int, int] | None, set[str]]] = {}


def _normalize_word(raw: str) -> str:
    lowered = str(raw or "").strip().strip("'").lower()
    return lowered.replace("'", "")


def _resolve_ide(widget: QWidget | None) -> object | None:
    current = widget
    while isinstance(current, QWidget):
        if hasattr(current, "ide_app_dir") and hasattr(current, "settings_manager"):
            return current
        current = current.parentWidget()

    app = QApplication.instance()
    if app is None:
        return None

    fallback = None
    for top in app.topLevelWidgets():
        if hasattr(top, "ide_app_dir") and hasattr(top, "settings_manager"):
            fallback = top
            if top.isActiveWindow():
                return top
    return fallback


def _dictionary_path_for_widget(widget: QWidget | None) -> Path:
    ide = _resolve_ide(widget)
    if ide is not None:
        try:
            return ide_spell_words_path()
        except Exception:
            pass
    return ide_spell_words_path()


def _read_dictionary_words(path: Path) -> set[str]:
    pkey = str(path)
    if not path.exists():
        _DICT_CACHE[pkey] = (None, set())
        return set()

    try:
        stat = path.stat()
        sig = (int(stat.st_mtime_ns), int(stat.st_size))
    except Exception:
        sig = None

    cached = _DICT_CACHE.get(pkey)
    if cached is not None and cached[0] == sig:
        return set(cached[1])

    words: set[str] = set()
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = str(raw_line or "").strip()
            if not line or line.startswith("#"):
                continue
            normalized = _normalize_word(line)
            if normalized and normalized.isalpha():
                words.add(normalized)
    except Exception:
        if cached is not None:
            return set(cached[1])
        return set()

    _DICT_CACHE[pkey] = (sig, set(words))
    return set(words)


def _invalidate_dictionary_cache(path: Path) -> None:
    _DICT_CACHE.pop(str(path), None)


def _append_word_to_dictionary(path: Path, word: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_dictionary_words(path)
        existing.add(word)
        payload = "\n".join(sorted(existing)) + "\n"
        path.write_text(payload, encoding="utf-8")
    except Exception:
        return False
    _invalidate_dictionary_cache(path)
    return True


def _status_message(widget: QWidget | None, text: str, timeout_ms: int = 2200) -> None:
    ide = _resolve_ide(widget)
    if ide is None:
        return
    getter = getattr(ide, "statusBar", None)
    if not callable(getter):
        return
    try:
        bar = getter()
    except Exception:
        return
    shower = getattr(bar, "showMessage", None)
    if callable(shower):
        try:
            shower(str(text), int(timeout_ms))
        except Exception:
            pass


def _color_for_widget(widget: QWidget | None) -> QColor:
    color = _DEFAULT_COLOR
    ide = _resolve_ide(widget)
    if ide is not None:
        mgr = getattr(ide, "settings_manager", None)
        getter = getattr(mgr, "get", None)
        if callable(getter):
            try:
                value = str(getter("editor.spellcheck.color", scope_preference="ide", default=_DEFAULT_COLOR) or "").strip()
                if _HEX_COLOR_RE.match(value):
                    color = value
            except Exception:
                pass
    return QColor(color)


def _checker_for_widget(widget: QWidget | None) -> SpellChecker | None:
    if SpellChecker is None:
        return None
    checker = SpellChecker(distance=1)
    checker.word_frequency.load_words(_AUTO_ALLOW_WORDS)
    checker.word_frequency.load_words(_read_dictionary_words(_dictionary_path_for_widget(widget)))
    return checker


def _unknown_word_set(widget: QWidget | None, words: set[str]) -> set[str]:
    if not words:
        return set()
    checker = _checker_for_widget(widget)
    if checker is None:
        return set()
    return set(checker.unknown(words))


def _should_check_word(word: str) -> bool:
    if not word or len(word) < 3:
        return False
    if not word.isalpha():
        return False
    if word in _AUTO_ALLOW_WORDS:
        return False
    return True


def _collect_occurrences(text: str) -> list[tuple[int, int, str]]:
    items: list[tuple[int, int, str]] = []
    for match in _WORD_RE.finditer(text):
        raw = match.group(0)
        normalized = _normalize_word(raw)
        if not _should_check_word(normalized):
            continue
        items.append((int(match.start()), int(match.end()), normalized))
    return items


def _misspelled_spans_for_text(
    widget: QWidget | None,
    text: str,
    *,
    max_highlights: int,
) -> list[tuple[int, int]]:
    occurrences = _collect_occurrences(text)
    if not occurrences:
        return []
    unknown = _unknown_word_set(widget, {word for _, _, word in occurrences})
    if not unknown:
        return []

    spans: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for start, end, word in occurrences:
        if word not in unknown:
            continue
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        spans.append(key)
        if len(spans) >= max(32, int(max_highlights)):
            break
    return spans


def _suggestions_for_word(widget: QWidget | None, raw_word: str, *, limit: int = 7) -> list[str]:
    normalized = _normalize_word(raw_word)
    if not _should_check_word(normalized):
        return []
    checker = _checker_for_widget(widget)
    if checker is None:
        return []
    if not checker.unknown([normalized]):
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
        bonus = 1 if correction and text == correction else 0
        try:
            freq = float(checker.word_usage_frequency(text))
        except Exception:
            freq = 0.0
        return (-bonus, -freq, abs(len(text) - len(normalized)), text)

    ranked = sorted((str(item) for item in candidates), key=_score)
    return [str(item) for item in ranked[: max(1, int(limit))]]


def _word_span_around(text: str, pos: int) -> tuple[int, int, str] | None:
    if not text:
        return None
    index = max(0, min(int(pos), len(text)))
    if index >= len(text):
        index = max(0, len(text) - 1)

    if index < len(text) and not _WORD_SPAN_CHARS_RE.match(text[index]):
        if index > 0 and _WORD_SPAN_CHARS_RE.match(text[index - 1]):
            index -= 1
        else:
            return None

    start = index
    while start > 0 and _WORD_SPAN_CHARS_RE.match(text[start - 1]):
        start -= 1
    end = index + 1
    while end < len(text) and _WORD_SPAN_CHARS_RE.match(text[end]):
        end += 1

    raw = text[start:end]
    if not raw.strip():
        return None
    return (int(start), int(end), raw)


def _add_word_for_widget(widget: QWidget | None, raw_word: str) -> bool:
    word = _normalize_word(raw_word)
    if not word or not word.isalpha():
        return False
    path = _dictionary_path_for_widget(widget)
    if _append_word_to_dictionary(path, word):
        _status_message(widget, f"Added '{word}' to IDE dictionary.")
        return True
    _status_message(widget, "Could not update IDE dictionary.", 2800)
    return False


def _draw_squiggle_line(painter: QPainter, x1: int, x2: int, y: int) -> None:
    if x2 <= x1:
        return
    x = x1
    up = False
    while x < x2:
        nx = min(x + 3, x2)
        painter.drawLine(x, y + (1 if up else -1), nx, y + (-1 if up else 1))
        up = not up
        x = nx


class SpellcheckLineEdit(QLineEdit):
    """Single-line edit with inline spell highlights and correction menu."""

    def __init__(self, text: str | QWidget | None = None, parent: QWidget | None = None):
        if isinstance(text, QWidget) and parent is None:
            parent = text
            text = ""
        super().__init__(str(text or ""), parent)
        self._spellcheck_spans: list[tuple[int, int]] = []
        self._spellcheck_timer = QTimer(self)
        self._spellcheck_timer.setSingleShot(True)
        self._spellcheck_timer.setInterval(280)
        self._spellcheck_timer.timeout.connect(self._recompute_spellcheck)
        self.textChanged.connect(self._schedule_spellcheck)
        self._schedule_spellcheck()

    def refresh_spellcheck(self) -> None:
        self._recompute_spellcheck()

    def _schedule_spellcheck(self) -> None:
        if SpellChecker is None or self.echoMode() != QLineEdit.Normal:
            self._spellcheck_spans = []
            self.update()
            return
        self._spellcheck_timer.start()

    def _recompute_spellcheck(self) -> None:
        if SpellChecker is None or self.echoMode() != QLineEdit.Normal:
            self._spellcheck_spans = []
            self.update()
            return
        self._spellcheck_spans = _misspelled_spans_for_text(self, str(self.text() or ""), max_highlights=128)
        self.update()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = self.createStandardContextMenu()
        try:
            self._append_spell_actions(menu, event.pos())
            menu.exec(event.globalPos())
        finally:
            menu.deleteLater()

    def _append_spell_actions(self, menu: QMenu, local_pos: QPoint) -> None:
        span = _word_span_around(str(self.text() or ""), int(self.cursorPositionAt(local_pos)))
        if span is None:
            return
        start, end, raw_word = span
        normalized = _normalize_word(raw_word)
        if not _should_check_word(normalized):
            return
        if normalized not in _unknown_word_set(self, {normalized}):
            return

        menu.addSeparator()
        spell_menu = menu.addMenu(f"Spelling: {raw_word}")
        suggestions = _suggestions_for_word(self, normalized, limit=7)
        if suggestions:
            for suggestion in suggestions:
                action = spell_menu.addAction(str(suggestion))
                action.triggered.connect(
                    lambda _checked=False, s=start, e=end, repl=suggestion: self._replace_span(s, e, repl)
                )
        else:
            none_action = spell_menu.addAction("No suggestions")
            none_action.setEnabled(False)

        spell_menu.addSeparator()
        add_action = spell_menu.addAction("Add to IDE Dictionary")
        add_action.triggered.connect(lambda _checked=False, word=raw_word: self._add_word_and_refresh(word))

    def _replace_span(self, start: int, end: int, replacement: str) -> None:
        text = str(self.text() or "")
        if not text:
            return
        repl = str(replacement or "").strip()
        if not repl:
            return
        start_i = max(0, min(int(start), len(text)))
        end_i = max(start_i, min(int(end), len(text)))
        updated = text[:start_i] + repl + text[end_i:]
        self.setText(updated)
        self.setCursorPosition(start_i + len(repl))

    def _add_word_and_refresh(self, raw_word: str) -> None:
        if _add_word_for_widget(self, raw_word):
            self._recompute_spellcheck()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._spellcheck_spans:
            return
        text = str(self.text() or "")
        if not text:
            return

        option = QStyleOptionFrame()
        self.initStyleOption(option)
        content_rect = self.style().subElementRect(QStyle.SE_LineEditContents, option, self)
        if content_rect.width() <= 0:
            return

        fm = self.fontMetrics()
        baseline = content_rect.y() + ((content_rect.height() + fm.ascent() - fm.descent()) // 2)
        y = baseline + 1

        painter = QPainter(self)
        painter.setClipRect(content_rect)
        pen = QPen(_color_for_widget(self))
        pen.setWidth(1)
        painter.setPen(pen)

        left = int(content_rect.left())
        right = int(content_rect.right())
        hit_y = int(content_rect.center().y())

        def _x_for_pos(target_pos: int) -> int:
            pos = max(0, min(int(target_pos), len(text)))
            lo = left
            hi = right + 1
            while lo < hi:
                mid = (lo + hi) // 2
                cursor_pos = int(self.cursorPositionAt(QPoint(mid, hit_y)))
                if cursor_pos < pos:
                    lo = mid + 1
                else:
                    hi = mid
            return max(left, min(right, lo))

        for start, end in self._spellcheck_spans:
            if end <= start:
                continue
            x1 = _x_for_pos(start)
            x2 = _x_for_pos(end)
            if x2 < left or x1 > right:
                continue
            _draw_squiggle_line(painter, max(left, x1), min(right, x2), y)


class SpellcheckPlainTextEdit(QPlainTextEdit):
    """Plain text edit with always-on spell checking for utility dialogs."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._spellcheck_timer = QTimer(self)
        self._spellcheck_timer.setSingleShot(True)
        self._spellcheck_timer.setInterval(320)
        self._spellcheck_timer.timeout.connect(self._recompute_spellcheck)
        self.textChanged.connect(self._schedule_spellcheck)
        self._spellcheck_spans: list[tuple[int, int]] = []
        self._schedule_spellcheck()

    def refresh_spellcheck(self) -> None:
        self._recompute_spellcheck()

    def _schedule_spellcheck(self) -> None:
        if SpellChecker is None:
            self._spellcheck_spans = []
            self.setExtraSelections([])
            return
        self._spellcheck_timer.start()

    def _recompute_spellcheck(self) -> None:
        text = str(self.toPlainText() or "")
        self._spellcheck_spans = _misspelled_spans_for_text(self, text, max_highlights=600)
        self._apply_spellcheck_selections()

    def _apply_spellcheck_selections(self) -> None:
        if not self._spellcheck_spans:
            self.setExtraSelections([])
            return
        fmt = QTextCharFormat()
        fmt.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
        fmt.setUnderlineColor(_color_for_widget(self))

        selections: list[QTextEdit.ExtraSelection] = []
        for start, end in self._spellcheck_spans:
            if end <= start:
                continue
            cur = QTextCursor(self.document())
            cur.setPosition(max(0, int(start)))
            cur.setPosition(max(0, int(end)), QTextCursor.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            sel.format = fmt
            selections.append(sel)
        self.setExtraSelections(selections)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = self.createStandardContextMenu(event.pos())
        try:
            self._append_spell_actions(menu, event.pos())
            menu.exec(event.globalPos())
        finally:
            menu.deleteLater()

    def _append_spell_actions(self, menu: QMenu, local_pos: QPoint) -> None:
        cursor = self.cursorForPosition(local_pos)
        text = str(self.toPlainText() or "")
        span = _word_span_around(text, int(cursor.position()))
        if span is None:
            return
        start, end, raw_word = span
        normalized = _normalize_word(raw_word)
        if not _should_check_word(normalized):
            return
        if normalized not in _unknown_word_set(self, {normalized}):
            return

        menu.addSeparator()
        spell_menu = menu.addMenu(f"Spelling: {raw_word}")
        suggestions = _suggestions_for_word(self, normalized, limit=7)
        if suggestions:
            for suggestion in suggestions:
                action = spell_menu.addAction(str(suggestion))
                action.triggered.connect(
                    lambda _checked=False, s=start, e=end, repl=suggestion: self._replace_span(s, e, repl)
                )
        else:
            none_action = spell_menu.addAction("No suggestions")
            none_action.setEnabled(False)

        spell_menu.addSeparator()
        add_action = spell_menu.addAction("Add to IDE Dictionary")
        add_action.triggered.connect(lambda _checked=False, word=raw_word: self._add_word_and_refresh(word))

    def _replace_span(self, start: int, end: int, replacement: str) -> None:
        value = str(replacement or "").strip()
        if not value:
            return
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        try:
            edit.setPosition(max(0, int(start)))
            edit.setPosition(max(0, int(end)), QTextCursor.KeepAnchor)
            edit.insertText(value)
        finally:
            edit.endEditBlock()
        self._schedule_spellcheck()

    def _add_word_and_refresh(self, raw_word: str) -> None:
        if _add_word_for_widget(self, raw_word):
            self._recompute_spellcheck()


class SpellcheckTextEdit(QTextEdit):
    """Rich text edit with spell underline overlays for plain text workflows."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._spellcheck_timer = QTimer(self)
        self._spellcheck_timer.setSingleShot(True)
        self._spellcheck_timer.setInterval(320)
        self._spellcheck_timer.timeout.connect(self._recompute_spellcheck)
        self.textChanged.connect(self._schedule_spellcheck)
        self._spellcheck_spans: list[tuple[int, int]] = []
        self._schedule_spellcheck()

    def refresh_spellcheck(self) -> None:
        self._recompute_spellcheck()

    def _schedule_spellcheck(self) -> None:
        if SpellChecker is None:
            self._spellcheck_spans = []
            self.setExtraSelections([])
            return
        self._spellcheck_timer.start()

    def _recompute_spellcheck(self) -> None:
        text = str(self.toPlainText() or "")
        self._spellcheck_spans = _misspelled_spans_for_text(self, text, max_highlights=700)
        self._apply_spellcheck_selections()

    def _apply_spellcheck_selections(self) -> None:
        if not self._spellcheck_spans:
            self.setExtraSelections([])
            return
        fmt = QTextCharFormat()
        fmt.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
        fmt.setUnderlineColor(_color_for_widget(self))

        selections: list[QTextEdit.ExtraSelection] = []
        for start, end in self._spellcheck_spans:
            if end <= start:
                continue
            cur = QTextCursor(self.document())
            cur.setPosition(max(0, int(start)))
            cur.setPosition(max(0, int(end)), QTextCursor.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            sel.format = fmt
            selections.append(sel)
        self.setExtraSelections(selections)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = self.createStandardContextMenu(event.pos())
        try:
            self._append_spell_actions(menu, event.pos())
            menu.exec(event.globalPos())
        finally:
            menu.deleteLater()

    def _append_spell_actions(self, menu: QMenu, local_pos: QPoint) -> None:
        cursor = self.cursorForPosition(local_pos)
        text = str(self.toPlainText() or "")
        span = _word_span_around(text, int(cursor.position()))
        if span is None:
            return
        start, end, raw_word = span
        normalized = _normalize_word(raw_word)
        if not _should_check_word(normalized):
            return
        if normalized not in _unknown_word_set(self, {normalized}):
            return

        menu.addSeparator()
        spell_menu = menu.addMenu(f"Spelling: {raw_word}")
        suggestions = _suggestions_for_word(self, normalized, limit=7)
        if suggestions:
            for suggestion in suggestions:
                action = spell_menu.addAction(str(suggestion))
                action.triggered.connect(
                    lambda _checked=False, s=start, e=end, repl=suggestion: self._replace_span(s, e, repl)
                )
        else:
            none_action = spell_menu.addAction("No suggestions")
            none_action.setEnabled(False)

        spell_menu.addSeparator()
        add_action = spell_menu.addAction("Add to IDE Dictionary")
        add_action.triggered.connect(lambda _checked=False, word=raw_word: self._add_word_and_refresh(word))

    def _replace_span(self, start: int, end: int, replacement: str) -> None:
        value = str(replacement or "").strip()
        if not value:
            return
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        try:
            edit.setPosition(max(0, int(start)))
            edit.setPosition(max(0, int(end)), QTextCursor.KeepAnchor)
            edit.insertText(value)
        finally:
            edit.endEditBlock()
        self._schedule_spellcheck()

    def _add_word_and_refresh(self, raw_word: str) -> None:
        if _add_word_for_widget(self, raw_word):
            self._recompute_spellcheck()


class SpellcheckTextPromptDialog(QDialog):
    """Simple text prompt with spellchecked line edit."""

    def __init__(
        self,
        *,
        title: str,
        label: str,
        value: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(str(title or "Input"))
        self.resize(460, 130)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(str(label or "Value:"), self))
        self.value_edit = SpellcheckLineEdit(self)
        self.value_edit.setText(str(value or ""))
        self.value_edit.selectAll()
        layout.addWidget(self.value_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> str:
        return str(self.value_edit.text() or "")


def get_spellcheck_text(
    parent: QWidget | None,
    title: str,
    label: str,
    *,
    text: str = "",
) -> tuple[str, bool]:
    """Prompt for a single line of text with spell checking enabled."""

    dialog = SpellcheckTextPromptDialog(
        title=str(title or "Input"),
        label=str(label or "Value:"),
        value=str(text or ""),
        parent=parent,
    )
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return dialog.value(), bool(accepted)
