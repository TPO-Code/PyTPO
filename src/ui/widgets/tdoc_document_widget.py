"""IDE-owned TDOC widget extensions."""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit

from TPOPyside.widgets.tdoc_support import TDocDocumentWidget as BaseTDocDocumentWidget
from src.ui.widgets.spellcheck_inputs import SpellcheckLineEdit


class TDocDocumentWidget(BaseTDocDocumentWidget):
    """TDOC document widget with IDE-specific search-field spell checking."""

    def create_search_line_edit(self, *, parent=None, role: str = "find") -> QLineEdit:
        _ = role
        return SpellcheckLineEdit(parent)
