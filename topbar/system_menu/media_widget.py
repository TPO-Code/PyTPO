from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .service import PlayerInfo


class MediaPlayerCard(QFrame):
    def __init__(self, set_volume: Callable[[str, float], bool], send_command: Callable[[str, str], bool], refresh_callback: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._set_volume = set_volume
        self._send_command = send_command
        self._refresh_callback = refresh_callback
        self._player_name = ""
        self._volume_syncing = False
        self._last_nonzero_volume = 0.5

        self.setObjectName("mediaPlayerCard")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        root.addLayout(top_row)

        self.identity_label = QLabel("Player", self)
        self.identity_label.setObjectName("mediaPlayerIdentity")
        top_row.addWidget(self.identity_label, stretch=1)

        self.status_label = QLabel("Unknown", self)
        self.status_label.setObjectName("mediaPlayerStatus")
        top_row.addWidget(self.status_label)

        self.title_label = QLabel("Nothing playing", self)
        self.title_label.setObjectName("mediaPlayerTitle")
        self.title_label.setWordWrap(True)
        root.addWidget(self.title_label)

        self.detail_label = QLabel("", self)
        self.detail_label.setObjectName("mediaPlayerDetail")
        self.detail_label.setWordWrap(True)
        root.addWidget(self.detail_label)

        volume_row = QHBoxLayout()
        volume_row.setSpacing(8)
        root.addLayout(volume_row)

        self.volume_label = QLabel("Player Volume", self)
        self.volume_label.setObjectName("mediaPlayerVolumeLabel")
        volume_row.addWidget(self.volume_label)

        self.volume_slider = QSlider(Qt.Horizontal, self)
        self.volume_slider.setRange(0, 100)
        volume_row.addWidget(self.volume_slider, stretch=1)

        self.volume_value_label = QLabel("N/A", self)
        self.volume_value_label.setObjectName("mediaPlayerVolumeValue")
        volume_row.addWidget(self.volume_value_label)

        self.mute_button = QPushButton("Mute", self)
        volume_row.addWidget(self.mute_button)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        root.addLayout(controls)

        self.prev_button = QPushButton("Prev", self)
        self.play_pause_button = QPushButton("Play", self)
        self.next_button = QPushButton("Next", self)
        for button in (self.prev_button, self.play_pause_button, self.next_button):
            controls.addWidget(button)

        self.prev_button.clicked.connect(lambda: self._invoke("previous"))
        self.play_pause_button.clicked.connect(lambda: self._invoke("play-pause"))
        self.next_button.clicked.connect(lambda: self._invoke("next"))
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_slider.sliderReleased.connect(self._commit_volume)
        self.mute_button.clicked.connect(self._toggle_mute)

    def bind(self, info: PlayerInfo) -> None:
        self._player_name = info.name
        self.identity_label.setText(info.identity or info.name)
        self.status_label.setText(info.status or "Unknown")
        self.title_label.setText(info.title or info.identity or info.name)

        details = [bit for bit in (info.artist, info.album) if bit]
        if details:
            self.detail_label.setText("  |  ".join(details))
        else:
            self.detail_label.setText("Media controls are available for this player.")

        self._volume_syncing = True
        try:
            if info.volume is None:
                self.volume_slider.setEnabled(False)
                self.mute_button.setEnabled(False)
                self.volume_value_label.setText("N/A")
                self.mute_button.setText("Mute")
            else:
                percent = max(0, min(100, int(round(info.volume * 100.0))))
                self.volume_slider.setEnabled(True)
                self.mute_button.setEnabled(True)
                self.volume_slider.setValue(percent)
                self.volume_value_label.setText(f"{percent}%")
                self.mute_button.setText("Unmute" if percent == 0 else "Mute")
                if percent > 0:
                    self._last_nonzero_volume = max(0.01, info.volume)
        finally:
            self._volume_syncing = False

        is_playing = (info.status or "").strip().lower() == "playing"
        self.play_pause_button.setText("Pause" if is_playing else "Play")
        self.prev_button.setEnabled(info.can_go_previous and info.can_control)
        self.play_pause_button.setEnabled((info.can_play or info.can_pause) and info.can_control)
        self.next_button.setEnabled(info.can_go_next and info.can_control)

    def _invoke(self, command: str) -> None:
        if not self._player_name:
            return
        self._send_command(self._player_name, command)
        self._refresh_callback()

    @Slot(int)
    def _on_volume_changed(self, value: int) -> None:
        if self._volume_syncing:
            return
        clamped = max(0, min(100, int(value)))
        self.volume_value_label.setText(f"{clamped}%")
        self.mute_button.setText("Unmute" if clamped == 0 else "Mute")

    @Slot()
    def _commit_volume(self) -> None:
        if self._volume_syncing or not self._player_name or not self.volume_slider.isEnabled():
            return
        value = max(0, min(100, int(self.volume_slider.value())))
        if value > 0:
            self._last_nonzero_volume = value / 100.0
        self._set_volume(self._player_name, value / 100.0)
        self._refresh_callback()

    @Slot()
    def _toggle_mute(self) -> None:
        if not self._player_name or not self.volume_slider.isEnabled():
            return
        current_value = max(0, min(100, int(self.volume_slider.value())))
        if current_value > 0:
            self._last_nonzero_volume = current_value / 100.0
            target = 0.0
        else:
            target = self._last_nonzero_volume if self._last_nonzero_volume > 0 else 0.5
        self._set_volume(self._player_name, target)
        self._refresh_callback()
