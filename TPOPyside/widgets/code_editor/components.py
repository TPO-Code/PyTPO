from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QWidget,
)

from .helpers import _COMPLETION_ROW_META_ROLE

if TYPE_CHECKING:
    from .editor import CodeEditor

class _CompletionItemDelegate(QStyledItemDelegate):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        row_h = max(base.height(), self._editor.fontMetrics().height() + 8)
        return QSize(base.width(), row_h)

    def paint(self, painter, option, index):
        meta = index.data(_COMPLETION_ROW_META_ROLE)
        if not isinstance(meta, dict):
            super().paint(painter, option, index)
            return

        style = option.widget.style() if option.widget is not None else QApplication.style()
        style_opt = QStyleOptionViewItem(option)
        style_opt.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, style_opt, painter, option.widget)

        rect = option.rect.adjusted(8, 0, -8, 0)
        if rect.width() <= 0:
            return

        primary = str(meta.get("primary") or "")
        right = str(meta.get("right") or "")
        kind = str(meta.get("kind_group") or "default")

        fm = option.fontMetrics
        selected = bool(option.state & QStyle.State_Selected)

        right_width = 0
        if right:
            right_width = min(max(36, fm.horizontalAdvance(right) + 8), int(rect.width() * 0.42))

        right_rect = QRect(rect.right() - right_width + 1, rect.top(), right_width, rect.height())
        main_rect = QRect(rect.left(), rect.top(), max(0, rect.width() - right_width - 10), rect.height())

        painter.save()
        if right and right_rect.width() > 0:
            right_pen = (
                option.palette.color(QPalette.HighlightedText)
                if selected
                else option.palette.color(QPalette.PlaceholderText)
            )
            painter.setPen(right_pen)
            painter.drawText(
                right_rect.adjusted(0, 0, -2, 0),
                Qt.AlignRight | Qt.AlignVCenter,
                fm.elidedText(right, Qt.ElideRight, right_rect.width()),
            )

        painter.setPen(self._editor._completion_kind_color(kind, option.palette, selected))
        painter.drawText(
            main_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            fm.elidedText(primary, Qt.ElideRight, main_rect.width()),
        )
        painter.restore()


class _EditorSearchBar(QFrame):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor
        self._replace_visible = False
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("editorSearchBar")
        self.setStyleSheet(
            """
            QFrame#editorSearchBar {
                background: #252526;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
            }
            QLineEdit, QPushButton, QCheckBox {
                font-size: 10pt;
            }
            QLineEdit {
                min-height: 24px;
                padding: 2px 6px;
                border: 1px solid #4a4a4a;
                background: #1e1e1e;
            }
            QPushButton {
                min-height: 24px;
                padding: 0 8px;
            }
            QCheckBox {
                spacing: 4px;
            }
            """
        )

        self.find_edit = QLineEdit(self)
        self.find_edit.setPlaceholderText("Find")
        self.find_edit.installEventFilter(self)

        self.replace_edit = QLineEdit(self)
        self.replace_edit.setPlaceholderText("Replace")
        self.replace_edit.installEventFilter(self)

        self.prev_btn = QPushButton("Prev", self)
        self.next_btn = QPushButton("Next", self)
        self.replace_btn = QPushButton("Replace", self)
        self.replace_all_btn = QPushButton("Replace All", self)
        self.case_box = QCheckBox("Case", self)
        self.word_box = QCheckBox("Word", self)
        self.regex_box = QCheckBox("Regex", self)
        self.selection_box = QCheckBox("Selection", self)
        self.count_lbl = QLabel("0 / 0", self)
        self.close_btn = QPushButton("X", self)
        self.close_btn.setFixedWidth(24)

        self.prev_btn.clicked.connect(self._editor.search_previous)
        self.next_btn.clicked.connect(self._editor.search_next)
        self.replace_btn.clicked.connect(self._editor.replace_current_or_next)
        self.replace_all_btn.clicked.connect(self._editor.replace_all_matches)
        self.close_btn.clicked.connect(self._editor.hide_search_bar)

        self.find_edit.textChanged.connect(self._editor._on_search_query_changed)
        self.replace_edit.textChanged.connect(self._editor._on_replace_query_changed)
        self.case_box.toggled.connect(self._editor._on_search_option_changed)
        self.word_box.toggled.connect(self._editor._on_search_option_changed)
        self.regex_box.toggled.connect(self._editor._on_search_option_changed)
        self.selection_box.toggled.connect(self._editor._on_search_option_changed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)
        lay.addWidget(self.find_edit, 2)
        lay.addWidget(self.replace_edit, 2)
        lay.addWidget(self.prev_btn)
        lay.addWidget(self.next_btn)
        lay.addWidget(self.replace_btn)
        lay.addWidget(self.replace_all_btn)
        lay.addWidget(self.case_box)
        lay.addWidget(self.word_box)
        lay.addWidget(self.regex_box)
        lay.addWidget(self.selection_box)
        lay.addWidget(self.count_lbl)
        lay.addWidget(self.close_btn)
        self.set_replace_visible(False)

    def set_replace_visible(self, visible: bool):
        self._replace_visible = bool(visible)
        self.replace_edit.setVisible(self._replace_visible)
        self.replace_btn.setVisible(self._replace_visible)
        self.replace_all_btn.setVisible(self._replace_visible)
        self.adjustSize()

    def is_replace_visible(self) -> bool:
        return self._replace_visible

    def set_count_text(self, text: str):
        self.count_lbl.setText(str(text or "0 / 0"))

    def set_in_selection_checked(self, checked: bool):
        blocker = self.selection_box.blockSignals(True)
        self.selection_box.setChecked(bool(checked))
        self.selection_box.blockSignals(blocker)

    def focus_find(self):
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def eventFilter(self, watched, event):
        if watched in {self.find_edit, self.replace_edit} and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    self._editor.search_previous()
                else:
                    self._editor.search_next()
                return True
            if key == Qt.Key_Escape:
                self._editor.hide_search_bar()
                return True
        return super().eventFilter(watched, event)


# ---------------- Code Editor with line numbers ----------------

class LineNumberArea(QWidget):
    def __init__(self, editor: 'CodeEditor'):
        super().__init__(editor)
        self.codeEditor = editor
    def sizeHint(self):
        return QSize(self.codeEditor.lineNumberAreaWidth(), 0)
    def paintEvent(self, event):
        self.codeEditor.lineNumberAreaPaintEvent(event)
    def mousePressEvent(self, event):
        self.codeEditor.lineNumberAreaMousePressEvent(event)
    def mouseDoubleClickEvent(self, event):
        self.codeEditor.lineNumberAreaMousePressEvent(event)


class OverviewMarkerArea(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.codeEditor = editor

    def sizeHint(self):
        return QSize(self.codeEditor.overviewMarkerAreaWidth(), 0)

    def paintEvent(self, event):
        self.codeEditor.overviewMarkerAreaPaintEvent(event)

    def mousePressEvent(self, event):
        self.codeEditor.overviewMarkerAreaMousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.codeEditor.overviewMarkerAreaMousePressEvent(event)




__all__ = [name for name in globals() if not name.startswith("__")]
