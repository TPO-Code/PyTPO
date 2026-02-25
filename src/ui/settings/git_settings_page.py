from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from TPOPyside.dialogs.color_picker_dialog import ColorPickerDialog
from src.settings_models import SettingsScope


class GitSettingsPage(QWidget):
    def __init__(self, *, manager: Any, scope: SettingsScope, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._scope = scope
        self._base_settings: dict[str, Any] = {}
        self._build_ui()
        initial = self._manager.get("git", scope_preference=self._scope, default={})
        self._set_settings_value(initial)
        self._base_settings = dict(self._current_settings_value())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(12)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        self.enable_tint_chk = QCheckBox("Enable Git file tinting in Project Explorer")
        form.addRow(self.enable_tint_chk)

        self.clean_color_edit = QLineEdit()
        self.clean_color_edit.setPlaceholderText("#7fbf7f")
        self.clean_color_pick = self._build_color_swatch()
        form.addRow("Tracked Clean Color", self._color_row(self.clean_color_edit, self.clean_color_pick))

        self.dirty_color_edit = QLineEdit()
        self.dirty_color_edit.setPlaceholderText("#e69f6b")
        self.dirty_color_pick = self._build_color_swatch()
        form.addRow("Tracked Dirty Color", self._color_row(self.dirty_color_edit, self.dirty_color_pick))

        self.untracked_color_edit = QLineEdit()
        self.untracked_color_edit.setPlaceholderText("#c8c8c8")
        self.untracked_color_pick = self._build_color_swatch()
        form.addRow("Untracked Color", self._color_row(self.untracked_color_edit, self.untracked_color_pick))

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        root.addLayout(form)
        root.addWidget(self.status_label)
        root.addStretch(1)

        self.clean_color_pick.clicked.connect(lambda: self._pick_color(self.clean_color_edit))
        self.dirty_color_pick.clicked.connect(lambda: self._pick_color(self.dirty_color_edit))
        self.untracked_color_pick.clicked.connect(lambda: self._pick_color(self.untracked_color_edit))
        self.clean_color_edit.textChanged.connect(lambda *_args: self._sync_color_swatch(self.clean_color_pick, self.clean_color_edit))
        self.dirty_color_edit.textChanged.connect(lambda *_args: self._sync_color_swatch(self.dirty_color_pick, self.dirty_color_edit))
        self.untracked_color_edit.textChanged.connect(
            lambda *_args: self._sync_color_swatch(self.untracked_color_pick, self.untracked_color_edit)
        )
        self.enable_tint_chk.toggled.connect(lambda *_args: self._notify_pending_changed())
        self.clean_color_edit.textChanged.connect(lambda *_args: self._notify_pending_changed())
        self.dirty_color_edit.textChanged.connect(lambda *_args: self._notify_pending_changed())
        self.untracked_color_edit.textChanged.connect(lambda *_args: self._notify_pending_changed())
        self._sync_color_swatch(self.clean_color_pick, self.clean_color_edit)
        self._sync_color_swatch(self.dirty_color_pick, self.dirty_color_edit)
        self._sync_color_swatch(self.untracked_color_pick, self.untracked_color_edit)

    @staticmethod
    def _build_color_swatch() -> QPushButton:
        swatch = QPushButton("")
        swatch.setFixedSize(34, 20)
        swatch.setCursor(Qt.PointingHandCursor)
        swatch.setToolTip("Pick color")
        return swatch

    def _color_row(self, edit: QLineEdit, button: QPushButton) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return holder

    @staticmethod
    def _sync_color_swatch(button: QPushButton, edit: QLineEdit) -> None:
        color = QColor(str(edit.text() or "").strip())
        if color.isValid():
            button.setStyleSheet(
                f"QPushButton {{ background-color: {color.name(QColor.HexRgb)}; border: 1px solid #6a6a6a; border-radius: 3px; }}"
            )
            button.setText("")
            return
        button.setStyleSheet("QPushButton { background-color: #2f2f2f; border: 1px solid #6a6a6a; border-radius: 3px; }")
        button.setText("?")

    def _pick_color(self, edit: QLineEdit) -> None:
        initial = QColor(str(edit.text() or "").strip() or "#ffffff")
        if not initial.isValid():
            initial = QColor("#ffffff")
        dialog = ColorPickerDialog(initial, self)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        color = dialog.get_color()
        if not isinstance(color, QColor) or not color.isValid():
            return
        edit.setText(color.name(QColor.HexRgb))

    def _current_settings_value(self) -> dict[str, Any]:
        return {
            "enable_file_tinting": bool(self.enable_tint_chk.isChecked()),
            "tracked_clean_color": str(self.clean_color_edit.text() or "").strip(),
            "tracked_dirty_color": str(self.dirty_color_edit.text() or "").strip(),
            "untracked_color": str(self.untracked_color_edit.text() or "").strip(),
        }

    def _set_settings_value(self, value: Any) -> None:
        raw = value if isinstance(value, dict) else {}
        self.enable_tint_chk.setChecked(bool(raw.get("enable_file_tinting", True)))
        self.clean_color_edit.setText(str(raw.get("tracked_clean_color") or "#7fbf7f").strip() or "#7fbf7f")
        self.dirty_color_edit.setText(str(raw.get("tracked_dirty_color") or "#e69f6b").strip() or "#e69f6b")
        self.untracked_color_edit.setText(str(raw.get("untracked_color") or "#c8c8c8").strip() or "#c8c8c8")

    def has_pending_settings_changes(self) -> bool:
        return self._current_settings_value() != self._base_settings

    def apply_settings_changes(self) -> list[str]:
        errors = self._validate_color_values()
        if errors:
            self.status_label.setText("\n".join(errors[:3]))
            return errors
        try:
            value = self._current_settings_value()
            self._manager.set("git", value, self._scope)
            self._base_settings = dict(value)
            self.status_label.setText("No git settings changes.")
            self._notify_pending_changed()
            return []
        except Exception as exc:
            return [str(exc)]

    def _validate_color_values(self) -> list[str]:
        errors: list[str] = []
        for label, edit in (
            ("Tracked Clean Color", self.clean_color_edit),
            ("Tracked Dirty Color", self.dirty_color_edit),
            ("Untracked Color", self.untracked_color_edit),
        ):
            text = str(edit.text() or "").strip()
            color = QColor(text)
            if not text:
                errors.append(f"{label}: color is required.")
            elif not color.isValid():
                errors.append(f"{label}: invalid color.")
        return errors

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

    def create_bindings(self, binding_cls: Callable[..., Any], scope: SettingsScope) -> list[Any]:
        bindings: list[Any] = []

        def _mk(
            key: str,
            widget: QWidget,
            getter: Callable[[], Any],
            setter: Callable[[Any], None],
            connector: Callable[[Callable[..., None]], None],
            validate: Callable[[], list[str]] | None = None,
        ) -> None:
            bindings.append(binding_cls(key, scope, widget, getter, setter, connector, validate or (lambda: [])))

        _mk(
            "git.enable_file_tinting",
            self.enable_tint_chk,
            lambda: bool(self.enable_tint_chk.isChecked()),
            lambda value: self.enable_tint_chk.setChecked(bool(value)),
            lambda cb: self.enable_tint_chk.toggled.connect(cb),
        )

        def _valid_hex(text: str) -> bool:
            color = QColor(str(text or "").strip())
            return color.isValid()

        def _color_validate(label: str, edit: QLineEdit) -> list[str]:
            text = str(edit.text() or "").strip()
            if not text:
                return [f"{label}: color is required."]
            if not _valid_hex(text):
                return [f"{label}: invalid color."]
            return []

        _mk(
            "git.tracked_clean_color",
            self.clean_color_edit,
            lambda: str(self.clean_color_edit.text() or "").strip(),
            lambda value: self.clean_color_edit.setText(str(value or "#7fbf7f").strip()),
            lambda cb: self.clean_color_edit.textChanged.connect(cb),
            lambda: _color_validate("Tracked Clean Color", self.clean_color_edit),
        )
        _mk(
            "git.tracked_dirty_color",
            self.dirty_color_edit,
            lambda: str(self.dirty_color_edit.text() or "").strip(),
            lambda value: self.dirty_color_edit.setText(str(value or "#e69f6b").strip()),
            lambda cb: self.dirty_color_edit.textChanged.connect(cb),
            lambda: _color_validate("Tracked Dirty Color", self.dirty_color_edit),
        )
        _mk(
            "git.untracked_color",
            self.untracked_color_edit,
            lambda: str(self.untracked_color_edit.text() or "").strip(),
            lambda value: self.untracked_color_edit.setText(str(value or "#c8c8c8").strip()),
            lambda cb: self.untracked_color_edit.textChanged.connect(cb),
            lambda: _color_validate("Untracked Color", self.untracked_color_edit),
        )
        return bindings


def create_git_settings_page(
    *,
    manager: Any,
    scope: SettingsScope,
    binding_cls: Callable[..., Any],
    parent: QWidget | None = None,
) -> tuple[QWidget, list[Any]]:
    page = GitSettingsPage(manager=manager, scope=scope, parent=None)
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(page)
    return scroll, page.create_bindings(binding_cls, scope)
