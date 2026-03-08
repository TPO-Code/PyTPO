"""IDE-side CodeEditor extension point.

The reusable base implementation remains in ``TPOPyside.widgets.code_editor``.
This module defines the IDE-owned subclass used by ``src`` so IDE-specific
behavior can be added without coupling ``TPOPyside`` to the IDE.
"""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QTextCursor, QTextFormat
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMenu, QMessageBox, QTextEdit

from TPOPyside.widgets.code_editor import (
    CodeEditor as BaseCodeEditor,
    _extract_compact_signature,
    _normalize_signature_text,
)
from src.ui.widgets.spellcheck_inputs import SpellcheckLineEdit


class CodeEditor(BaseCodeEditor):
    """IDE-local CodeEditor subclass.

    Starts as a behavior-preserving pass-through; extend here for IDE-only
    editor features.
    """

    debuggerBreakpointToggled = Signal(int, bool)
    debuggerBreakpointsChanged = Signal()

    def __init__(self, *args, **kwargs):
        self._debugger_breakpoints: dict[int, dict] = {}
        self._debugger_execution_line = -1
        super().__init__(*args, **kwargs)

    def lineNumberAreaWidth(self):
        return super().lineNumberAreaWidth() + 14

    def lineNumberAreaPaintEvent(self, event):
        super().lineNumberAreaPaintEvent(event)
        if not self._debugger_breakpoints and self._debugger_execution_line <= 0:
            return

        painter = QPainter(self.lineNumberArea)
        painter.setRenderHint(QPainter.Antialiasing, True)

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()
        fold_gutter = int(self._fold_gutter_width) if self._fold_provider is not None else 0
        marker_center_x = fold_gutter + 7

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_number = int(block_number + 1)
                line_height = int(self.fontMetrics().height())
                marker_y = int(top + max(2, (line_height - 9) // 2))

                spec = self._debugger_breakpoints.get(line_number)
                if spec is not None:
                    marker_color = QColor("#d54a3a")
                    if str(spec.get("log_message") or "").strip():
                        marker_color = QColor("#2d9c6b")
                    elif str(spec.get("condition") or "").strip() or int(spec.get("hit_count") or 0) > 0:
                        marker_color = QColor("#c98f1d")
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(marker_color)
                    painter.drawEllipse(marker_center_x - 4, marker_y, 9, 9)
                    badge = ""
                    if str(spec.get("log_message") or "").strip():
                        badge = "L"
                    elif str(spec.get("condition") or "").strip():
                        badge = "?"
                    elif int(spec.get("hit_count") or 0) > 0:
                        badge = "#"
                    if badge:
                        painter.setPen(QColor("white"))
                        painter.drawText(
                            marker_center_x - 5,
                            int(top),
                            9,
                            line_height,
                            Qt.AlignCenter,
                            badge,
                        )

                if line_number == self._debugger_execution_line:
                    painter.setPen(QColor("#f2c94c"))
                    painter.drawText(
                        marker_center_x + 5,
                        int(top),
                        10,
                        line_height,
                        Qt.AlignLeft | Qt.AlignVCenter,
                        "▶",
                    )

            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1

        painter.end()

    def lineNumberAreaMousePressEvent(self, event):
        point = event.position().toPoint() if hasattr(event, "position") else event.pos()
        fold_gutter = int(self._fold_gutter_width) if self._fold_provider is not None else 0
        if point.x() > fold_gutter:
            block_number = self._block_number_at_y(int(point.y()))
            if block_number >= 0:
                line_number = block_number + 1
                if event.button() == Qt.RightButton:
                    self._show_debugger_breakpoint_menu(line_number, self.mapToGlobal(point))
                else:
                    self.toggle_debugger_breakpoint(line_number)
                event.accept()
                return
        super().lineNumberAreaMousePressEvent(event)

    def _rebuild_extra_selections(self):
        super()._rebuild_extra_selections()
        if self._debugger_execution_line <= 0:
            return

        block = self.document().findBlockByLineNumber(self._debugger_execution_line - 1)
        if not block.isValid():
            return

        selection = QTextEdit.ExtraSelection()
        selection.cursor = QTextCursor(block)
        selection.cursor.clearSelection()
        selection.format.setProperty(QTextFormat.FullWidthSelection, True)
        selection.format.setBackground(QColor(255, 230, 120, 90))

        extra_selections = list(self.extraSelections())
        extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def debugger_breakpoints(self) -> set[int]:
        return set(self._debugger_breakpoints)

    def debugger_breakpoint_specs(self) -> list[dict]:
        return [
            {
                "line": int(line),
                "condition": str(spec.get("condition") or ""),
                "hit_count": max(0, int(spec.get("hit_count") or 0)),
                "log_message": str(spec.get("log_message") or ""),
            }
            for line, spec in sorted(self._debugger_breakpoints.items())
        ]

    def set_debugger_breakpoints(self, lines: set[int]) -> None:
        self.set_debugger_breakpoint_specs([{"line": int(line)} for line in lines if int(line) > 0])

    def set_debugger_breakpoint_specs(self, specs: list[dict]) -> None:
        normalized: dict[int, dict] = {}
        for raw in specs:
            if not isinstance(raw, dict):
                continue
            try:
                line = int(raw.get("line") or 0)
            except Exception:
                continue
            if line <= 0:
                continue
            normalized[line] = {
                "condition": str(raw.get("condition") or "").strip(),
                "hit_count": max(0, int(raw.get("hit_count") or 0)),
                "log_message": str(raw.get("log_message") or "").strip(),
            }
        if normalized == self._debugger_breakpoints:
            return
        self._debugger_breakpoints = normalized
        self.lineNumberArea.update()
        self.debuggerBreakpointsChanged.emit()

    def toggle_debugger_breakpoint(self, line_number: int) -> bool:
        line = int(line_number)
        if line <= 0:
            return False
        enabled = line not in self._debugger_breakpoints
        if enabled:
            self._debugger_breakpoints[line] = {
                "condition": "",
                "hit_count": 0,
                "log_message": "",
            }
        else:
            self._debugger_breakpoints.pop(line, None)
        self.lineNumberArea.update()
        self.debuggerBreakpointToggled.emit(line, enabled)
        self.debuggerBreakpointsChanged.emit()
        return enabled

    def set_debugger_breakpoint_options(
        self,
        line_number: int,
        *,
        condition: str | None = None,
        hit_count: int | None = None,
        log_message: str | None = None,
    ) -> None:
        line = int(line_number)
        if line <= 0:
            return
        spec = dict(self._debugger_breakpoints.get(line) or {})
        spec["condition"] = str(condition if condition is not None else spec.get("condition") or "").strip()
        spec["hit_count"] = max(0, int(hit_count if hit_count is not None else spec.get("hit_count") or 0))
        spec["log_message"] = str(log_message if log_message is not None else spec.get("log_message") or "").strip()
        self._debugger_breakpoints[line] = spec
        self.lineNumberArea.update()
        self.debuggerBreakpointsChanged.emit()

    def clear_debugger_breakpoint_options(self, line_number: int) -> None:
        line = int(line_number)
        if line <= 0 or line not in self._debugger_breakpoints:
            return
        self._debugger_breakpoints[line] = {
            "condition": "",
            "hit_count": 0,
            "log_message": "",
        }
        self.lineNumberArea.update()
        self.debuggerBreakpointsChanged.emit()

    def _show_debugger_breakpoint_menu(self, line_number: int, global_pos) -> None:
        menu = QMenu(self)
        has_breakpoint = line_number in self._debugger_breakpoints
        if has_breakpoint:
            edit_action = menu.addAction("Edit Breakpoint…")
            clear_options_action = menu.addAction("Clear Breakpoint Options")
            remove_action = menu.addAction("Remove Breakpoint")
        else:
            edit_action = menu.addAction("Add Breakpoint…")
            clear_options_action = None
            remove_action = None
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is edit_action:
            self._edit_debugger_breakpoint(line_number)
            return
        if clear_options_action is not None and chosen is clear_options_action:
            self.clear_debugger_breakpoint_options(line_number)
            return
        if remove_action is not None and chosen is remove_action:
            self._debugger_breakpoints.pop(int(line_number), None)
            self.lineNumberArea.update()
            self.debuggerBreakpointToggled.emit(int(line_number), False)
            self.debuggerBreakpointsChanged.emit()

    def _edit_debugger_breakpoint(self, line_number: int) -> None:
        line = int(line_number)
        if line <= 0:
            return
        created = line not in self._debugger_breakpoints
        if line not in self._debugger_breakpoints:
            self._debugger_breakpoints[line] = {"condition": "", "hit_count": 0, "log_message": ""}

        spec = dict(self._debugger_breakpoints.get(line) or {})
        options = [
            "Normal breakpoint",
            "Conditional breakpoint",
            "Hit-count breakpoint",
            "Logpoint",
        ]
        current_index = 0
        if str(spec.get("log_message") or "").strip():
            current_index = 3
        elif int(spec.get("hit_count") or 0) > 0:
            current_index = 2
        elif str(spec.get("condition") or "").strip():
            current_index = 1
        label, ok = QInputDialog.getItem(
            self,
            "Breakpoint",
            f"Breakpoint type for line {line}:",
            options,
            current_index,
            False,
        )
        if not ok:
            if created:
                self._debugger_breakpoints.pop(line, None)
            return

        selection = str(label or options[0])
        condition = ""
        hit_count = 0
        log_message = ""
        if selection == "Conditional breakpoint":
            condition, ok = QInputDialog.getText(
                self,
                "Conditional Breakpoint",
                "Pause when this expression is truthy:",
                text=str(spec.get("condition") or ""),
            )
            if not ok:
                return
            condition = str(condition or "").strip()
            if not condition:
                QMessageBox.warning(self, "Breakpoint", "A conditional breakpoint requires an expression.")
                return
        elif selection == "Hit-count breakpoint":
            hit_count, ok = QInputDialog.getInt(
                self,
                "Hit-count Breakpoint",
                "Pause after this many hits:",
                value=max(1, int(spec.get("hit_count") or 1)),
                minValue=1,
                maxValue=999999,
            )
            if not ok:
                return
        elif selection == "Logpoint":
            log_message, ok = QInputDialog.getText(
                self,
                "Logpoint",
                "Message to print when this line is hit:",
                text=str(spec.get("log_message") or ""),
            )
            if not ok:
                return
            log_message = str(log_message or "").strip()
            if not log_message:
                QMessageBox.warning(self, "Breakpoint", "A logpoint requires a message.")
                return

        self._debugger_breakpoints[line] = {
            "condition": condition,
            "hit_count": int(hit_count),
            "log_message": log_message,
        }
        self.lineNumberArea.update()
        self.debuggerBreakpointsChanged.emit()

    def set_debugger_execution_line(self, line_number: int) -> None:
        normalized = int(line_number)
        if self._debugger_execution_line == normalized:
            return
        self._debugger_execution_line = normalized
        self._rebuild_extra_selections()
        self.lineNumberArea.update()

    def clear_debugger_execution_line(self) -> None:
        if self._debugger_execution_line <= 0:
            return
        self._debugger_execution_line = -1
        self._rebuild_extra_selections()
        self.lineNumberArea.update()

    def _resolve_completion_insert_text(
        self,
        *,
        item: dict,
        insert_text: str,
        label: str,
        prefix_text: str,
        source_text: str,
    ) -> str:
        value = str(insert_text or "")
        if not value:
            return value

        # IDE behavior: some providers (notably Jedi) return only suffix text
        # in insert_text. Expand to the full label when suffix semantics match.
        if label and prefix_text:
            pfx_low = prefix_text.lower()
            lbl_low = label.lower()
            if lbl_low.startswith(pfx_low):
                suffix = label[len(prefix_text):]
                if value.lower() == suffix.lower():
                    return label
        return value

    def create_search_line_edit(self, *, parent=None, role: str = "find") -> QLineEdit:
        _ = role
        return SpellcheckLineEdit(parent)

    def _completion_matches_prefix(self, item: dict, prefix: str) -> bool:
        if bool(item.get("is_ai_suggestion")):
            return True
        label = str(item.get("label") or item.get("insert_text") or "")
        if not prefix:
            return True
        low = label.lower()
        p = prefix.lower()
        return low.startswith(p) or p in low

    def _completion_ui_sort_key(
        self,
        item: dict,
        prefix: str,
        base_index: int,
    ) -> tuple[int, int]:
        if bool(item.get("is_ai_suggestion")):
            return -1, base_index
        label = str(item.get("label") or item.get("insert_text") or "")
        demote = 0
        if label.startswith("__") and label.endswith("__") and not prefix.startswith("_"):
            demote = 2
        elif label.startswith("_") and prefix == "":
            demote = 1
        return demote, base_index

    def _completion_right_label_for_item(self, item: dict) -> str:
        for key in ("source_label", "type_label", "owner", "module"):
            value = str(item.get(key) or "").strip()
            if value:
                return value[:28]

        detail = str(item.get("detail") or "").strip()
        source = str(item.get("source") or "").strip()
        scope = str(item.get("source_scope") or "").strip().lower()
        kind = str(item.get("kind") or "").strip().lower()

        module_m = re.search(r"\bmodule\s+([A-Za-z_][\w\.]*)", detail)
        if module_m:
            return module_m.group(1)[:28]
        class_m = re.search(r"\bclass\s+([A-Za-z_]\w*)", detail)
        if class_m:
            return class_m.group(1)[:28]
        from_m = re.search(r"\bfrom\s+([A-Za-z_][\w\.]*)", detail)
        if from_m:
            return from_m.group(1)[:28]
        arrow_m = re.search(r"->\s*([A-Za-z_][\w\.\[\], ]*)$", detail)
        if arrow_m:
            return arrow_m.group(1).strip()[:28]

        if scope == "builtins":
            return "builtins"
        if scope == "interpreter_modules":
            return "stdlib"
        if scope == "project":
            return "project"
        if scope == "current_file":
            return "file"
        if source and source.lower() not in {"fallback", "jedi"}:
            return source[:28]
        if source:
            return source[:28]
        if kind == "keyword":
            return "keyword"
        return ""

    def _completion_primary_text_for_item(self, item: dict, *, label: str, detail: str) -> str:
        primary = str(label or "")
        if bool(self._completion_ui_cfg.get("show_signatures", True)) and self._is_callable_item(
            item
        ):
            sig = _extract_compact_signature(primary, detail)
            primary = sig if sig else self._best_effort_callable_signature(item, primary)
            primary = _normalize_signature_text(primary, label) or primary
        return primary or str(label or "")

    def _normalize_lint_diagnostic_item(self, item: object) -> dict | None:
        if not isinstance(item, dict):
            return None
        try:
            line = int(item.get("line") or 0)
            col = int(item.get("column") or 1)
        except Exception:
            return None
        if line <= 0:
            return None
        sev = str(item.get("severity") or "warning").strip().lower()
        try:
            end_line = int(item.get("end_line") or line)
        except Exception:
            end_line = line
        try:
            end_col = int(item.get("end_column") or item.get("end_col") or (col + 1))
        except Exception:
            end_col = col + 1
        return {
            "line": max(1, line),
            "column": max(1, col),
            "end_line": max(1, end_line),
            "end_column": max(1, end_col),
            "severity": sev,
        }

    def _signature_owner_from_lookup_payload(self, payload: dict, label: str) -> str:
        full_name = str(payload.get("full_name") or "").strip()
        if full_name:
            return full_name
        module_name = str(payload.get("module_name") or "").strip()
        if module_name:
            return f"{module_name}.{label}"
        return str(payload.get("source") or "")
