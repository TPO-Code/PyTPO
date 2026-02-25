from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.ui.custom_dialog import DialogWindow


class FontSelectionDialog(DialogWindow):
    """Dialog for selecting an editor font family with live preview."""

    def __init__(
        self,
        *,
        initial_family: str = "",
        use_native_chrome: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(use_native_chrome=use_native_chrome, resizable=True, parent=parent)
        self.setWindowTitle("Select Editor Font")
        self.resize(700, 520)

        self._all_families = sorted(str(name) for name in QFontDatabase.families())
        self._selected_family = str(initial_family or "").strip()

        host = QWidget(self)
        root = QVBoxLayout(host)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.search = QLineEdit(host)
        self.search.setPlaceholderText("Search fonts...")
        self.search.textChanged.connect(self._refresh_font_list)
        row.addWidget(self.search, 1)

        self.preview_size = QSpinBox(host)
        self.preview_size.setRange(8, 42)
        self.preview_size.setValue(13)
        self.preview_size.valueChanged.connect(self._refresh_preview)
        row.addWidget(QLabel("Preview Size", host))
        row.addWidget(self.preview_size)
        root.addLayout(row)

        self.font_list = QListWidget(host)
        self.font_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.font_list.itemDoubleClicked.connect(lambda _item: self.accept())
        root.addWidget(self.font_list, 1)

        self.preview = QLabel("The quick brown fox jumps over the lazy dog 1234567890", host)
        self.preview.setWordWrap(True)
        self.preview.setMinimumHeight(72)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.preview)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=host)
        buttons.accepted.connect(self._accept_if_selected)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.set_content_widget(host)
        self._refresh_font_list()
        self._select_initial_family()
        self._refresh_preview()

    def selected_family(self) -> str:
        return str(self._selected_family or "").strip()

    def _refresh_font_list(self) -> None:
        query = str(self.search.text() or "").strip().lower()
        current = self.selected_family()

        self.font_list.blockSignals(True)
        self.font_list.clear()
        for family in self._all_families:
            if query and query not in family.lower():
                continue
            item = QListWidgetItem(family)
            item.setData(Qt.ItemDataRole.UserRole, family)
            self.font_list.addItem(item)
        self.font_list.blockSignals(False)

        if current:
            self._select_family(current)
        elif self.font_list.count() > 0:
            self.font_list.setCurrentRow(0)

    def _select_initial_family(self) -> None:
        if self._selected_family:
            self._select_family(self._selected_family)
            return
        if self.font_list.count() > 0:
            self.font_list.setCurrentRow(0)
            item = self.font_list.currentItem()
            if item is not None:
                self._selected_family = str(item.data(Qt.ItemDataRole.UserRole) or item.text() or "")

    def _select_family(self, family: str) -> None:
        target = str(family or "").strip().lower()
        if not target:
            return
        for idx in range(self.font_list.count()):
            item = self.font_list.item(idx)
            name = str(item.data(Qt.ItemDataRole.UserRole) or item.text() or "")
            if name.lower() != target:
                continue
            self.font_list.setCurrentItem(item)
            self.font_list.scrollToItem(item)
            self._selected_family = name
            return

    def _on_selection_changed(self) -> None:
        item = self.font_list.currentItem()
        if item is None:
            return
        self._selected_family = str(item.data(Qt.ItemDataRole.UserRole) or item.text() or "").strip()
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        font = QFont()
        if self._selected_family:
            font.setFamily(self._selected_family)
        font.setPointSize(int(self.preview_size.value()))
        self.preview.setFont(font)

    def _accept_if_selected(self) -> None:
        family = self.selected_family()
        if not family and self.font_list.count() > 0:
            item = self.font_list.currentItem() or self.font_list.item(0)
            if item is not None:
                family = str(item.data(Qt.ItemDataRole.UserRole) or item.text() or "").strip()
                self._selected_family = family
        if not family:
            return
        self.accept()


__all__ = ["FontSelectionDialog"]

