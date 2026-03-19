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
    SEEK_STEP_SECONDS = 10.0
    POSITION_SLIDER_MAX = 1000

    def __init__(
        self,
        set_volume: Callable[[str, float], bool],
        send_command: Callable[[str, str], bool],
        seek_relative: Callable[[str, float], bool],
        set_position: Callable[[str, float], bool],
        set_loop_status: Callable[[str, str], bool],
        set_shuffle: Callable[[str, bool], bool],
        refresh_callback: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._set_volume = set_volume
        self._send_command = send_command
        self._seek_relative_cb = seek_relative
        self._set_position_cb = set_position
        self._set_loop_status_cb = set_loop_status
        self._set_shuffle_cb = set_shuffle
        self._refresh_callback = refresh_callback
        self._player_name = ""
        self._volume_syncing = False
        self._position_syncing = False
        self._last_nonzero_volume = 0.5
        self._position_seconds: float | None = None
        self._length_seconds: float | None = None
        self._loop_status: str = "None"
        self._shuffle: bool | None = None

        self.setObjectName("mediaPlayerCard")

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        root.addLayout(header_row)

        self.identity_label = QLabel("Player", self)
        self.identity_label.setObjectName("mediaPlayerIdentity")
        self.identity_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        header_row.addWidget(self.identity_label, stretch=1)

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("mediaPlayerStatus")
        self.status_label.hide()
        header_row.addWidget(self.status_label)

        self.title_label = QLabel("Nothing playing", self)
        self.title_label.setObjectName("mediaPlayerTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.title_label)

        self.meta_label = QLabel("", self)
        self.meta_label.setObjectName("mediaPlayerDetail")
        self.meta_label.setWordWrap(True)
        self.meta_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.meta_label.hide()
        root.addWidget(self.meta_label)

        self.transport_row = QHBoxLayout()
        self.transport_row.setContentsMargins(0, 0, 0, 0)
        self.transport_row.setSpacing(6)
        root.addLayout(self.transport_row)

        self.prev_button = QPushButton("⏮", self)
        self.play_button = QPushButton("▶", self)
        self.pause_button = QPushButton("⏸", self)
        self.play_pause_button = QPushButton("⏯", self)
        self.stop_button = QPushButton("⏹", self)
        self.next_button = QPushButton("⏭", self)

        self._transport_buttons = [
            self.prev_button,
            self.play_button,
            self.pause_button,
            self.play_pause_button,
            self.stop_button,
            self.next_button,
        ]
        for button in self._transport_buttons:
            button.setMinimumHeight(28)
            self.transport_row.addWidget(button)

        self.prev_button.clicked.connect(lambda: self._invoke("previous"))
        self.play_button.clicked.connect(lambda: self._invoke("play"))
        self.pause_button.clicked.connect(lambda: self._invoke("pause"))
        self.play_pause_button.clicked.connect(lambda: self._invoke("play-pause"))
        self.stop_button.clicked.connect(lambda: self._invoke("stop"))
        self.next_button.clicked.connect(lambda: self._invoke("next"))

        self.seek_row = QHBoxLayout()
        self.seek_row.setContentsMargins(0, 0, 0, 0)
        self.seek_row.setSpacing(6)
        root.addLayout(self.seek_row)

        self.seek_back_button = QPushButton("−10s", self)
        self.seek_forward_button = QPushButton("+10s", self)
        self.seek_back_button.setMinimumHeight(28)
        self.seek_forward_button.setMinimumHeight(28)
        self.seek_row.addWidget(self.seek_back_button)
        self.seek_row.addWidget(self.seek_forward_button)
        self.seek_row.addStretch(1)

        self.seek_back_button.clicked.connect(lambda: self._seek_relative(-self.SEEK_STEP_SECONDS))
        self.seek_forward_button.clicked.connect(lambda: self._seek_relative(self.SEEK_STEP_SECONDS))

        self.position_row = QHBoxLayout()
        self.position_row.setContentsMargins(0, 0, 0, 0)
        self.position_row.setSpacing(6)
        root.addLayout(self.position_row)

        self.position_label = QLabel("0:00", self)
        self.position_label.setObjectName("mediaPlayerPositionLabel")
        self.position_row.addWidget(self.position_label)

        self.position_slider = QSlider(Qt.Horizontal, self)
        self.position_slider.setRange(0, self.POSITION_SLIDER_MAX)
        self.position_slider.setEnabled(False)
        self.position_row.addWidget(self.position_slider, stretch=1)

        self.duration_label = QLabel("0:00", self)
        self.duration_label.setObjectName("mediaPlayerDurationLabel")
        self.position_row.addWidget(self.duration_label)

        self.position_slider.valueChanged.connect(self._on_position_changed)
        self.position_slider.sliderPressed.connect(self._on_position_slider_pressed)
        self.position_slider.sliderReleased.connect(self._commit_position)

        self.options_row = QHBoxLayout()
        self.options_row.setContentsMargins(0, 0, 0, 0)
        self.options_row.setSpacing(6)
        root.addLayout(self.options_row)

        self.loop_button = QPushButton("Loop: Off", self)
        self.shuffle_button = QPushButton("Shuffle: Off", self)
        self.loop_button.setMinimumHeight(28)
        self.shuffle_button.setMinimumHeight(28)
        self.options_row.addWidget(self.loop_button)
        self.options_row.addWidget(self.shuffle_button)
        self.options_row.addStretch(1)

        self.loop_button.clicked.connect(self._cycle_loop)
        self.shuffle_button.clicked.connect(self._toggle_shuffle)

        self.volume_row = QHBoxLayout()
        self.volume_row.setContentsMargins(0, 0, 0, 0)
        self.volume_row.setSpacing(6)
        root.addLayout(self.volume_row)

        self.volume_label = QLabel("Vol", self)
        self.volume_label.setObjectName("mediaPlayerVolumeLabel")
        self.volume_row.addWidget(self.volume_label)

        self.volume_slider = QSlider(Qt.Horizontal, self)
        self.volume_slider.setRange(0, 100)
        self.volume_row.addWidget(self.volume_slider, stretch=1)

        self.volume_value_label = QLabel("0%", self)
        self.volume_value_label.setObjectName("mediaPlayerVolumeValue")
        self.volume_row.addWidget(self.volume_value_label)

        self.mute_button = QPushButton("Mute", self)
        self.mute_button.setMinimumHeight(28)
        self.volume_row.addWidget(self.mute_button)

        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_slider.sliderReleased.connect(self._commit_volume)
        self.mute_button.clicked.connect(self._toggle_mute)

    def bind(self, info: PlayerInfo) -> None:
        self._player_name = info.name
        self._position_seconds = info.position_seconds
        self._length_seconds = info.length_seconds
        self._loop_status = info.loop_status or "None"
        self._shuffle = info.shuffle

        identity = (info.identity or info.name or "Player").strip()
        status = (info.status or "").strip()
        title = (info.title or "").strip()
        artist = (info.artist or "").strip()
        album = (info.album or "").strip()

        self.identity_label.setText(identity)

        if status:
            self.status_label.setText(status)
            self.status_label.show()
        else:
            self.status_label.clear()
            self.status_label.hide()

        self.title_label.setText(title or identity)

        meta_bits = [bit for bit in (artist, album) if bit]
        if meta_bits:
            self.meta_label.setText(" • ".join(meta_bits))
            self.meta_label.show()
        else:
            self.meta_label.clear()
            self.meta_label.hide()

        can_control = bool(info.can_control)

        show_prev = can_control and bool(info.can_go_previous)
        show_next = can_control and bool(info.can_go_next)
        show_play = can_control and bool(info.can_play)
        show_pause = can_control and bool(info.can_pause)
        show_stop = can_control and (show_play or show_pause or status.lower() in {"playing", "paused", "stopped"})

        self.prev_button.setVisible(show_prev)
        self.next_button.setVisible(show_next)
        self.stop_button.setVisible(show_stop)

        if show_play and show_pause:
            self.play_button.hide()
            self.pause_button.hide()
            self.play_pause_button.show()
            if status.lower() == "playing":
                self.play_pause_button.setText("⏸")
                self.play_pause_button.setToolTip("Pause")
            else:
                self.play_pause_button.setText("▶")
                self.play_pause_button.setToolTip("Play")
        else:
            self.play_pause_button.hide()
            self.play_button.setVisible(show_play)
            self.pause_button.setVisible(show_pause)
            self.play_button.setToolTip("Play")
            self.pause_button.setToolTip("Pause")

        any_transport_visible = any(button.isVisible() for button in self._transport_buttons)
        self._set_layout_visible(self.transport_row, any_transport_visible)

        can_seek = can_control and bool(info.can_seek)
        self.seek_back_button.setVisible(can_seek)
        self.seek_forward_button.setVisible(can_seek)
        self._set_layout_visible(self.seek_row, can_seek)

        self._position_syncing = True
        try:
            has_position = (
                can_seek
                and info.position_seconds is not None
                and info.length_seconds is not None
                and info.length_seconds > 0
            )
            if has_position:
                ratio = max(0.0, min(1.0, info.position_seconds / info.length_seconds))
                slider_value = int(round(ratio * self.POSITION_SLIDER_MAX))
                self.position_slider.setEnabled(True)
                self.position_slider.setValue(slider_value)
                self.position_label.setText(self._format_time(info.position_seconds))
                self.duration_label.setText(self._format_time(info.length_seconds))
                self._set_layout_visible(self.position_row, True)
            else:
                self.position_slider.setEnabled(False)
                self.position_slider.setValue(0)
                self.position_label.setText("0:00")
                self.duration_label.setText("0:00")
                self._set_layout_visible(self.position_row, False)
        finally:
            self._position_syncing = False

        show_loop = can_control
        self.loop_button.setVisible(show_loop)
        if show_loop:
            self.loop_button.setText(f"Loop: {self._loop_label(self._loop_status)}")

        show_shuffle = can_control and info.shuffle is not None
        self.shuffle_button.setVisible(show_shuffle)
        if show_shuffle:
            self.shuffle_button.setText("Shuffle: On" if info.shuffle else "Shuffle: Off")

        self._set_layout_visible(self.options_row, show_loop or show_shuffle)

        self._volume_syncing = True
        try:
            if info.volume is None:
                self.volume_slider.setEnabled(False)
                self.mute_button.setEnabled(False)
                self.volume_slider.setValue(0)
                self.volume_value_label.setText("N/A")
                self.mute_button.setText("Mute")
                self._set_layout_visible(self.volume_row, False)
            else:
                percent = max(0, min(100, int(round(info.volume * 100.0))))
                self.volume_slider.setEnabled(True)
                self.mute_button.setEnabled(True)
                self.volume_slider.setValue(percent)
                self.volume_value_label.setText(f"{percent}%")
                self.mute_button.setText("Unmute" if percent == 0 else "Mute")
                self._set_layout_visible(self.volume_row, True)
                if percent > 0:
                    self._last_nonzero_volume = max(0.01, info.volume)
        finally:
            self._volume_syncing = False

    def _set_layout_visible(self, layout: QHBoxLayout, visible: bool) -> None:
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget()
            if widget is not None:
                widget.setVisible(visible)

    def _format_time(self, seconds: float | None) -> str:
        if seconds is None or seconds < 0:
            return "0:00"
        total_seconds = int(round(seconds))
        minutes, secs = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _loop_label(self, value: str) -> str:
        normalized = (value or "None").strip().capitalize()
        if normalized == "Track":
            return "Track"
        if normalized == "Playlist":
            return "Playlist"
        return "Off"

    def _invoke(self, command: str) -> None:
        if not self._player_name:
            return
        self._send_command(self._player_name, command)
        self._refresh_callback()

    def _seek_relative(self, delta_seconds: float) -> None:
        if not self._player_name:
            return
        self._seek_relative_cb(self._player_name, delta_seconds)
        self._refresh_callback()

    def _on_position_slider_pressed(self) -> None:
        # Prevent bind() updates from fighting the user's drag.
        self._position_syncing = False

    @Slot(int)
    def _on_position_changed(self, value: int) -> None:
        if self._position_syncing or self._length_seconds is None or self._length_seconds <= 0:
            return
        ratio = max(0.0, min(1.0, value / self.POSITION_SLIDER_MAX))
        seconds = ratio * self._length_seconds
        self.position_label.setText(self._format_time(seconds))

    @Slot()
    def _commit_position(self) -> None:
        if (
            not self._player_name
            or not self.position_slider.isEnabled()
            or self._length_seconds is None
            or self._length_seconds <= 0
        ):
            return

        ratio = max(0.0, min(1.0, self.position_slider.value() / self.POSITION_SLIDER_MAX))
        target_seconds = ratio * self._length_seconds
        self._set_position_cb(self._player_name, target_seconds)
        self._refresh_callback()

    @Slot()
    def _cycle_loop(self) -> None:
        if not self._player_name:
            return

        current = (self._loop_status or "None").strip().capitalize()
        if current == "None":
            next_value = "Track"
        elif current == "Track":
            next_value = "Playlist"
        else:
            next_value = "None"

        if self._set_loop_status_cb(self._player_name, next_value):
            self._loop_status = next_value
        self._refresh_callback()

    @Slot()
    def _toggle_shuffle(self) -> None:
        if not self._player_name or self._shuffle is None:
            return

        next_value = not self._shuffle
        if self._set_shuffle_cb(self._player_name, next_value):
            self._shuffle = next_value
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