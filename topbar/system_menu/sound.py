from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QTimer, Qt, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .service import SoundSnapshot, VolumeService


class SoundSection(QWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        request_refresh: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.volume = VolumeService()
        self._request_refresh = request_refresh
        self._volume_syncing = False

        self._volume_apply_timer = QTimer(self)
        self._volume_apply_timer.setSingleShot(True)
        self._volume_apply_timer.setInterval(120)
        self._volume_apply_timer.timeout.connect(self._apply_pending_volume)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        volume_title_row = QHBoxLayout()
        volume_title_row.setSpacing(8)
        root.addLayout(volume_title_row)

        volume_title = QLabel("Volume", self)
        volume_title.setObjectName("systemMenuSectionTitle")
        volume_title_row.addWidget(volume_title)
        volume_title_row.addStretch(1)

        self.volume_value_label = QLabel("--", self)
        self.volume_value_label.setObjectName("systemMenuValue")
        volume_title_row.addWidget(self.volume_value_label)

        self.volume_mute_button = QPushButton("Mute", self)
        self.volume_mute_button.clicked.connect(self.toggle_mute)
        volume_title_row.addWidget(self.volume_mute_button)

        self.volume_slider = QSlider(Qt.Horizontal, self)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.valueChanged.connect(self._on_volume_slider_changed)
        root.addWidget(self.volume_slider)

        sound_row = QHBoxLayout()
        sound_row.setSpacing(8)
        root.addLayout(sound_row)

        sound_refresh = QPushButton("Refresh Sound", self)
        sound_refresh.clicked.connect(self.refresh)
        sound_row.addWidget(sound_refresh)

        sound_settings = QPushButton("Sound Settings", self)
        sound_settings.clicked.connect(self.open_sound_settings)
        sound_row.addWidget(sound_settings)

        self._set_initial_state()

    def _set_initial_state(self) -> None:
        available = self.volume.available()
        self.volume_slider.setEnabled(available)
        self.volume_mute_button.setEnabled(available)
        if not available:
            self.volume_value_label.setText("Unavailable")

    def refresh(self) -> None:
        if self._request_refresh is not None:
            self._request_refresh()
            return
        self.apply_snapshot(
            SoundSnapshot(
                available=self.volume.available(),
                volume_percent=self.volume.volume_percent(),
                is_muted=self.volume.is_muted(),
            )
        )

    def apply_snapshot(self, snapshot: SoundSnapshot) -> None:
        available = snapshot.available
        self.volume_slider.setEnabled(available)
        self.volume_mute_button.setEnabled(available)
        if not available:
            self.volume_value_label.setText("Unavailable")
            self.volume_mute_button.setText("Mute")
            return

        percent = snapshot.volume_percent
        muted = snapshot.is_muted
        if percent is None:
            self.volume_value_label.setText("Unknown")
            return

        previous_state = self.volume_slider.blockSignals(True)
        self._volume_syncing = True
        self.volume_slider.setValue(percent)
        self._volume_syncing = False
        self.volume_slider.blockSignals(previous_state)

        suffix = " muted" if muted else ""
        self.volume_value_label.setText(f"{percent}%{suffix}")
        self.volume_mute_button.setText("Unmute" if muted else "Mute")

    def open_sound_settings(self) -> None:
        if self.volume.open_settings():
            return
        QMessageBox.warning(self, "Open Settings Failed", "Could not open sound settings.")

    def toggle_mute(self) -> None:
        if self.volume.toggle_mute():
            self.refresh()
            return
        QMessageBox.warning(self, "Volume", "Failed to toggle mute.")

    @Slot(int)
    def _on_volume_slider_changed(self, value: int) -> None:
        if self._volume_syncing:
            return
        self.volume_value_label.setText(f"{max(0, min(100, int(value)))}%")
        self._volume_apply_timer.start()

    @Slot()
    def _apply_pending_volume(self) -> None:
        value = self.volume_slider.value()
        if self.volume.set_volume_percent(value):
            self.refresh()
            return
        QMessageBox.warning(self, "Volume", "Failed to set the system volume.")
        self.refresh()
