"""Read-only unified diff viewer widget for IDE editor tabs."""

from __future__ import annotations

import os
import uuid

from PySide6.QtGui import QColor, QTextCursor, QTextFormat
from PySide6.QtWidgets import QMenu, QTextEdit

from src.ui.widgets.code_editor import CodeEditor


class UnifiedDiffEditor(CodeEditor):
    """Single-page unified diff view with line-based highlighting."""

    _DIFF_LINE_COLORS = {
        "header": QColor(142, 108, 54, 82),
        "hunk": QColor(62, 105, 166, 96),
        "added": QColor(47, 118, 63, 96),
        "removed": QColor(137, 49, 49, 104),
        "context": QColor(120, 120, 120, 30),
    }

    def __init__(
        self,
        *,
        source_file_path: str,
        diff_text: str = "",
        display_name: str | None = None,
        parent=None,
    ) -> None:
        # Initialize line groups before base initialization because the base
        # editor can trigger virtual callbacks (for example setReadOnly/selection
        # rebuild) during construction.
        self._header_lines: set[int] = set()
        self._hunk_lines: set[int] = set()
        self._added_lines: set[int] = set()
        self._removed_lines: set[int] = set()
        self._context_lines: set[int] = set()

        super().__init__(parent)
        self.editor_id = str(uuid.uuid4())
        # Keep this empty so file-open path routing never mistakes this for the source editor tab.
        self.file_path: str | None = None
        self._source_file_path = str(source_file_path or "").strip()
        self._display_name = str(display_name or "").strip() or self._default_display_name()

        self.setLineWrapMode(CodeEditor.LineWrapMode.NoWrap)
        self.set_word_wrap_enabled(False)
        self.set_code_folding_enabled(False)
        self.set_occurrence_highlighting_enabled(False)
        self.update_overview_marker_settings({"enabled": False})
        self.update_spellcheck_visual_settings({"enabled": False})
        self.setReadOnly(True)

        self.set_diff_content(diff_text)

    @property
    def source_file_path(self) -> str:
        return str(self._source_file_path or "")

    def _default_display_name(self) -> str:
        name = os.path.basename(self._source_file_path) if self._source_file_path else "File"
        return f"Diff: {name}"

    def display_name(self) -> str:
        return str(self._display_name or "Diff")

    def set_display_name(self, value: str) -> None:
        text = str(value or "").strip()
        if text:
            self._display_name = text

    def setReadOnly(self, _read_only: bool) -> None:
        # Diff view is always read-only, regardless of project read-only toggles.
        super().setReadOnly(True)
        if hasattr(self, "_context_lines"):
            self._rebuild_extra_selections()

    def set_word_wrap_enabled(self, _enabled: bool) -> None:
        super().set_word_wrap_enabled(False)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)

        act_copy = menu.addAction("Copy")
        act_copy.setEnabled(bool(self.textCursor().hasSelection()))

        act_select_all = menu.addAction("Select All")
        act_find = menu.addAction("Find")

        pos = event.pos() if hasattr(event, "pos") else event.position().toPoint()
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is act_copy:
            self.copy()
        elif chosen is act_select_all:
            self.selectAll()
        elif chosen is act_find:
            self.show_find_bar()

    def update_overview_marker_settings(self, overview_cfg: dict) -> None:
        cfg = dict(overview_cfg) if isinstance(overview_cfg, dict) else {}
        cfg["enabled"] = False
        super().update_overview_marker_settings(cfg)

    def update_spellcheck_visual_settings(self, cfg: dict | None) -> None:
        payload = dict(cfg) if isinstance(cfg, dict) else {}
        payload["enabled"] = False
        super().update_spellcheck_visual_settings(payload)

    def set_diff_content(self, diff_text: str) -> None:
        text = str(diff_text or "")
        self.setPlainText(text)
        self.document().setModified(False)
        self._recompute_line_groups(text)
        self._rebuild_extra_selections()

    def _recompute_line_groups(self, text: str) -> None:
        self._header_lines = set()
        self._hunk_lines = set()
        self._added_lines = set()
        self._removed_lines = set()
        self._context_lines = set()

        for line_number, line in enumerate(str(text).splitlines(), start=1):
            if line.startswith("@@"):
                self._hunk_lines.add(line_number)
                continue
            if line.startswith("+") and not line.startswith("+++"):
                self._added_lines.add(line_number)
                continue
            if line.startswith("-") and not line.startswith("---"):
                self._removed_lines.add(line_number)
                continue
            if line.startswith(" "):
                self._context_lines.add(line_number)
                continue
            if (
                line.startswith("diff --git ")
                or line.startswith("index ")
                or line.startswith("--- ")
                or line.startswith("+++ ")
                or line.startswith("\\ No newline at end of file")
                or line.startswith("# ")
            ):
                self._header_lines.add(line_number)

    def _line_extra_selection(self, line_number: int, color: QColor) -> QTextEdit.ExtraSelection | None:
        block = self.document().findBlockByLineNumber(max(0, int(line_number) - 1))
        if not block.isValid():
            return None
        selection = QTextEdit.ExtraSelection()
        selection.cursor = QTextCursor(block)
        selection.cursor.clearSelection()
        selection.format.setProperty(QTextFormat.FullWidthSelection, True)
        selection.format.setBackground(color)
        return selection

    def _append_line_group_selections(
        self,
        target: list[QTextEdit.ExtraSelection],
        line_numbers: set[int],
        *,
        group: str,
    ) -> None:
        color = self._DIFF_LINE_COLORS[group]
        for line_number in sorted(line_numbers):
            selection = self._line_extra_selection(line_number, color)
            if selection is not None:
                target.append(selection)

    def _rebuild_extra_selections(self):
        super()._rebuild_extra_selections()
        if not hasattr(self, "_context_lines"):
            return
        selections = list(self.extraSelections())
        # Paint low-contrast context first, then semantic lines above it.
        self._append_line_group_selections(selections, set(getattr(self, "_context_lines", set())), group="context")
        self._append_line_group_selections(selections, set(getattr(self, "_header_lines", set())), group="header")
        self._append_line_group_selections(selections, set(getattr(self, "_hunk_lines", set())), group="hunk")
        self._append_line_group_selections(selections, set(getattr(self, "_added_lines", set())), group="added")
        self._append_line_group_selections(selections, set(getattr(self, "_removed_lines", set())), group="removed")
        self.setExtraSelections(selections)
