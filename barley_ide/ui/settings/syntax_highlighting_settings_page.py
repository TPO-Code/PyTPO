from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from TPOPyside.dialogs.color_picker_dialog import ColorPickerDialog
from TPOPyside.widgets.code_editor.syntax_highlighters import syntax_language_labels, syntax_token_defaults
from barley_ide.services.syntax_highlighting_config import is_valid_syntax_color, normalize_syntax_highlighting_settings
from barley_ide.settings_models import SettingsScope
from barley_ide.ui.theme_runtime import apply_settings_color_swatch_size


@dataclass(slots=True)
class _ColorField:
    token: str
    default_color: str
    row_widget: QWidget
    edit: QLineEdit
    swatch: QPushButton
    reset: QPushButton


class SyntaxHighlightingSettingsPage(QWidget):
    def __init__(self, *, manager: Any, scope: SettingsScope, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._scope = scope
        self._updating = False
        self._base_settings: dict[str, Any] = {}

        self._token_defaults = syntax_token_defaults()
        self._language_labels = syntax_language_labels()
        self._languages = sorted(
            self._token_defaults.keys(),
            key=lambda key: str(self._language_labels.get(key, key)).lower(),
        )
        self._global_defaults = self._collect_global_token_defaults()
        self._global_fields: dict[str, _ColorField] = {}
        self._language_fields: dict[str, _ColorField] = {}
        self._global_overrides: dict[str, str] = {}
        self._language_overrides: dict[str, dict[str, str]] = {}

        self._build_ui()
        initial = self._manager.get("editor.syntax_highlighting", scope_preference=self._scope, default={})
        self._set_settings_value(initial)
        self._base_settings = self._current_settings_value()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        self.help_label = QLabel(
            "Use global token overrides for all languages, and set language overrides only when needed."
        )
        self.help_label.setWordWrap(True)
        root.addWidget(self.help_label)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_global_tab(), "Global Tokens")
        self.tabs.addTab(self._build_language_tab(), "Language Overrides")
        root.addWidget(self.tabs, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

    def _build_global_tab(self) -> QWidget:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        top = QLabel("Blank value uses built-in defaults.")
        top.setWordWrap(True)
        layout.addWidget(top)

        scroll = QScrollArea(host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget(scroll)
        form = QFormLayout(container)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)

        for token, default_color in self._global_defaults.items():
            field = self._create_color_field(
                token=token,
                default_color=default_color,
                reset_label="Use Built-in",
            )
            self._global_fields[token] = field
            form.addRow(self._token_label(token), field.row_widget)
            field.edit.textChanged.connect(lambda _text, t=token: self._on_global_token_changed(t))
            field.swatch.clicked.connect(lambda _checked=False, t=token: self._pick_global_color(t))
            field.reset.clicked.connect(lambda _checked=False, t=token: self._clear_global_token(t))

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        return host

    def _build_language_tab(self) -> QWidget:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self.language_combo = QComboBox(host)
        for language_id in self._languages:
            self.language_combo.addItem(self._language_label(language_id), language_id)
        self.language_combo.currentIndexChanged.connect(lambda *_args: self._on_language_changed())
        header.addWidget(self.language_combo, 1)

        self.reset_language_btn = QPushButton("Reset Selected Language")
        self.reset_language_btn.clicked.connect(self._clear_current_language_overrides)
        header.addWidget(self.reset_language_btn)

        layout.addLayout(header)

        self.language_hint = QLabel("Blank value uses global override first, then built-in default.")
        self.language_hint.setWordWrap(True)
        layout.addWidget(self.language_hint)

        self.language_scroll = QScrollArea(host)
        self.language_scroll.setWidgetResizable(True)
        self.language_scroll.setFrameShape(QFrame.NoFrame)
        self.language_container = QWidget(self.language_scroll)
        self.language_form = QFormLayout(self.language_container)
        self.language_form.setHorizontalSpacing(14)
        self.language_form.setVerticalSpacing(8)
        self.language_scroll.setWidget(self.language_container)
        layout.addWidget(self.language_scroll, 1)
        return host

    def _collect_global_token_defaults(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for language in self._languages:
            tokens = self._token_defaults.get(language) or {}
            if not isinstance(tokens, dict):
                continue
            for token, default_color in tokens.items():
                key = str(token or "").strip().lower()
                if not key or key in merged:
                    continue
                merged[key] = str(default_color or "").strip()
        return merged

    def _create_color_field(self, *, token: str, default_color: str, reset_label: str) -> _ColorField:
        edit = QLineEdit()
        edit.setPlaceholderText(default_color)

        swatch = QPushButton("")
        apply_settings_color_swatch_size(swatch)
        swatch.setCursor(Qt.PointingHandCursor)
        swatch.setToolTip("Pick color")

        reset = QPushButton(reset_label)

        row_widget = QWidget(self)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        row_layout.addWidget(edit, 1)
        row_layout.addWidget(swatch)
        row_layout.addWidget(reset)

        return _ColorField(
            token=token,
            default_color=default_color,
            row_widget=row_widget,
            edit=edit,
            swatch=swatch,
            reset=reset,
        )

    def _current_language(self) -> str:
        if self.language_combo.count() <= 0:
            return ""
        value = self.language_combo.currentData()
        if isinstance(value, str) and value.strip():
            return value.strip()
        return str(self.language_combo.currentText() or "").strip().lower()

    def _on_global_token_changed(self, token: str) -> None:
        if self._updating:
            return
        field = self._global_fields.get(token)
        if field is None:
            return
        text = str(field.edit.text() or "").strip()
        if text:
            self._global_overrides[token] = text
        else:
            self._global_overrides.pop(token, None)
        self._refresh_global_field_visual(token)
        self._refresh_all_language_field_visuals()
        self._notify_pending_changed()

    def _on_language_token_changed(self, token: str) -> None:
        if self._updating:
            return
        language = self._current_language()
        if not language:
            return
        field = self._language_fields.get(token)
        if field is None:
            return
        text = str(field.edit.text() or "").strip()
        if text:
            self._language_overrides.setdefault(language, {})[token] = text
        else:
            token_map = self._language_overrides.get(language)
            if isinstance(token_map, dict):
                token_map.pop(token, None)
                if not token_map:
                    self._language_overrides.pop(language, None)
        self._refresh_language_field_visual(token)
        self._notify_pending_changed()

    def _on_language_changed(self) -> None:
        self._rebuild_language_rows()
        self._notify_pending_changed()

    def _rebuild_language_rows(self) -> None:
        while self.language_form.rowCount() > 0:
            self.language_form.removeRow(0)
        self._language_fields.clear()

        language = self._current_language()
        token_defaults = self._token_defaults.get(language) if language else {}
        if not isinstance(token_defaults, dict):
            token_defaults = {}

        for token, default_color in token_defaults.items():
            token_key = str(token or "").strip().lower()
            if not token_key:
                continue
            field = self._create_color_field(
                token=token_key,
                default_color=str(default_color or "").strip(),
                reset_label="Use Global/Default",
            )
            self._language_fields[token_key] = field
            self.language_form.addRow(self._token_label(token_key), field.row_widget)
            field.edit.textChanged.connect(lambda _text, t=token_key: self._on_language_token_changed(t))
            field.swatch.clicked.connect(lambda _checked=False, t=token_key: self._pick_language_color(t))
            field.reset.clicked.connect(lambda _checked=False, t=token_key: self._clear_language_token(t))

        self._updating = True
        try:
            overrides = self._language_overrides.get(language, {}) if language else {}
            for token, field in self._language_fields.items():
                value = overrides.get(token, "") if isinstance(overrides, dict) else ""
                field.edit.setText(str(value or ""))
        finally:
            self._updating = False

        self._refresh_all_language_field_visuals()

    def _clear_global_token(self, token: str) -> None:
        field = self._global_fields.get(token)
        if field is not None:
            field.edit.setText("")

    def _clear_language_token(self, token: str) -> None:
        field = self._language_fields.get(token)
        if field is not None:
            field.edit.setText("")

    def _clear_current_language_overrides(self) -> None:
        language = self._current_language()
        if not language:
            return
        self._language_overrides.pop(language, None)
        for field in self._language_fields.values():
            field.edit.setText("")
        self._notify_pending_changed()

    def _pick_global_color(self, token: str) -> None:
        field = self._global_fields.get(token)
        if field is None:
            return
        current = str(field.edit.text() or "").strip()
        initial = current if is_valid_syntax_color(current) else field.default_color
        picked = self._pick_color(initial)
        if picked:
            field.edit.setText(picked)

    def _pick_language_color(self, token: str) -> None:
        field = self._language_fields.get(token)
        if field is None:
            return
        language = self._current_language()
        current = str(field.edit.text() or "").strip()
        initial = current if is_valid_syntax_color(current) else self._effective_color(language, token, field.default_color)
        picked = self._pick_color(initial)
        if picked:
            field.edit.setText(picked)

    def _pick_color(self, initial_text: str) -> str | None:
        initial = QColor(str(initial_text or "").strip() or "#ffffff")
        if not initial.isValid():
            initial = QColor("#ffffff")
        dialog = ColorPickerDialog(initial, self)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return None
        color = dialog.get_color()
        if not isinstance(color, QColor) or not color.isValid():
            return None
        return color.name(QColor.HexRgb)

    def _effective_color(self, language: str, token: str, default_color: str) -> str:
        lang_map = self._language_overrides.get(language)
        if isinstance(lang_map, dict):
            language_override = str(lang_map.get(token) or "").strip()
            if is_valid_syntax_color(language_override):
                return language_override
        global_override = str(self._global_overrides.get(token) or "").strip()
        if is_valid_syntax_color(global_override):
            return global_override
        return default_color

    def _refresh_global_field_visual(self, token: str) -> None:
        field = self._global_fields.get(token)
        if field is None:
            return
        text = str(field.edit.text() or "").strip()
        if text and not is_valid_syntax_color(text):
            self._set_swatch_invalid(field.swatch)
            return
        color = text if text else field.default_color
        self._set_swatch_color(field.swatch, color)

    def _refresh_language_field_visual(self, token: str) -> None:
        field = self._language_fields.get(token)
        if field is None:
            return
        text = str(field.edit.text() or "").strip()
        if text and not is_valid_syntax_color(text):
            self._set_swatch_invalid(field.swatch)
            return
        language = self._current_language()
        color = self._effective_color(language, token, field.default_color)
        self._set_swatch_color(field.swatch, color)

    def _refresh_all_global_field_visuals(self) -> None:
        for token in self._global_fields:
            self._refresh_global_field_visual(token)

    def _refresh_all_language_field_visuals(self) -> None:
        for token in self._language_fields:
            self._refresh_language_field_visual(token)

    @staticmethod
    def _set_swatch_color(button: QPushButton, color_text: str) -> None:
        color = QColor(str(color_text or "").strip())
        if not color.isValid():
            SyntaxHighlightingSettingsPage._set_swatch_invalid(button)
            return
        button.setStyleSheet(
            "QPushButton { "
            f"background-color: {color.name(QColor.HexRgb)}; "
            "border: 1px solid #6a6a6a; "
            "border-radius: 3px; "
            "min-height: 0px; "
            "min-width: 0px; "
            "padding: 0px; "
            "}"
        )
        button.setText("")

    @staticmethod
    def _set_swatch_invalid(button: QPushButton) -> None:
        button.setStyleSheet(
            "QPushButton { "
            "background-color: #2f2f2f; "
            "border: 1px solid #6a6a6a; "
            "border-radius: 3px; "
            "min-height: 0px; "
            "min-width: 0px; "
            "padding: 0px; "
            "}"
        )
        button.setText("?")

    def _language_label(self, language_id: str) -> str:
        return str(self._language_labels.get(language_id, language_id))

    @staticmethod
    def _token_label(token: str) -> str:
        text = str(token or "").strip().replace("_", " ")
        text = text.replace("  ", " ").strip()
        return text.title() if text else token

    def _set_settings_value(self, value: Any) -> None:
        normalized = normalize_syntax_highlighting_settings(value)
        self._updating = True
        try:
            self._global_overrides = dict(normalized.get("global_tokens") or {})
            raw_languages = normalized.get("language_overrides") or {}
            self._language_overrides = {
                str(language): dict(tokens)
                for language, tokens in raw_languages.items()
                if isinstance(tokens, dict)
            }

            for token, field in self._global_fields.items():
                field.edit.setText(str(self._global_overrides.get(token) or ""))
        finally:
            self._updating = False

        if self.language_combo.count() > 0 and self.language_combo.currentIndex() < 0:
            self.language_combo.setCurrentIndex(0)
        self._rebuild_language_rows()
        self._refresh_all_global_field_visuals()

    def _current_settings_value(self) -> dict[str, Any]:
        language_overrides: dict[str, dict[str, str]] = {}
        for language, token_map in self._language_overrides.items():
            if not isinstance(token_map, dict):
                continue
            cleaned = {
                str(token): str(color or "").strip()
                for token, color in token_map.items()
                if str(color or "").strip()
            }
            if cleaned:
                language_overrides[str(language)] = cleaned
        return {
            "global_tokens": {
                str(token): str(color or "").strip()
                for token, color in self._global_overrides.items()
                if str(color or "").strip()
            },
            "language_overrides": language_overrides,
        }

    def _validation_errors(self) -> list[str]:
        errors: list[str] = []
        for token, color in (self._global_overrides or {}).items():
            text = str(color or "").strip()
            if not text:
                continue
            if not is_valid_syntax_color(text):
                errors.append(f"Global {self._token_label(token)}: invalid color.")
        for language, token_map in (self._language_overrides or {}).items():
            label = self._language_label(language)
            if not isinstance(token_map, dict):
                continue
            for token, color in token_map.items():
                text = str(color or "").strip()
                if not text:
                    continue
                if not is_valid_syntax_color(text):
                    errors.append(f"{label} {self._token_label(token)}: invalid color.")
        return errors

    def has_pending_settings_changes(self) -> bool:
        return self._current_settings_value() != self._base_settings

    def apply_settings_changes(self) -> list[str]:
        errors = self._validation_errors()
        if errors:
            self.status_label.setText("\n".join(errors[:3]))
            return errors
        try:
            payload = normalize_syntax_highlighting_settings(self._current_settings_value())
            self._manager.set("editor.syntax_highlighting", payload, self._scope)
            self._set_settings_value(payload)
            self._base_settings = deepcopy(self._current_settings_value())
            self.status_label.setText("No syntax highlighting settings changes.")
            self._notify_pending_changed()
            return []
        except Exception as exc:
            return [str(exc)]

    def _notify_pending_changed(self) -> None:
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "_refresh_dirty_state"):
            parent = parent.parentWidget()
        if parent is None:
            return
        refresh = getattr(parent, "_refresh_dirty_state", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass

