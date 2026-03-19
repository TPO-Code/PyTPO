from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QTimer, Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QToolButton,
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
        self._volume_dragging = False
        self._position_dragging = False
        self._last_nonzero_volume = 0.5
        self._position_seconds: float | None = None
        self._length_seconds: float | None = None
        self._track_id = ""
        self._loop_status: str = "None"
        self._shuffle: bool | None = None
        self._position_pending_seconds: float | None = None
        self._position_pending_track_id = ""
        self._position_hold_until = 0.0
        self._volume_pending_percent: int | None = None
        self._volume_hold_until = 0.0

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_callback)

        self.setObjectName("mediaPlayerCard")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)
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
        self.transport_row.setSpacing(4)
        root.addLayout(self.transport_row)

        self.prev_button = QToolButton(self)
        self.seek_back_button = QToolButton(self)
        self.play_pause_button = QToolButton(self)
        self.seek_forward_button = QToolButton(self)
        self.stop_button = QToolButton(self)
        self.next_button = QToolButton(self)

        self.prev_button.setText("⏮")
        self.seek_back_button.setText("⏪")
        self.play_pause_button.setText("⏯")
        self.seek_forward_button.setText("⏩")
        self.stop_button.setText("⏹")
        self.next_button.setText("⏭")
        self.prev_button.setToolTip("Previous")
        self.seek_back_button.setToolTip("Back 10 seconds")
        self.play_pause_button.setToolTip("Play/Pause")
        self.seek_forward_button.setToolTip("Forward 10 seconds")
        self.stop_button.setToolTip("Stop")
        self.next_button.setToolTip("Next")

        self._transport_buttons = [
            self.prev_button,
            self.seek_back_button,
            self.play_pause_button,
            self.seek_forward_button,
            self.stop_button,
            self.next_button,
        ]
        for button in self._transport_buttons:
            button.setAutoRaise(True)
            button.setFixedSize(26, 24)
            self.transport_row.addWidget(button)

        self.prev_button.clicked.connect(lambda: self._invoke("previous"))
        self.seek_back_button.clicked.connect(lambda: self._seek_relative(-self.SEEK_STEP_SECONDS))
        self.play_pause_button.clicked.connect(lambda: self._invoke("play-pause"))
        self.seek_forward_button.clicked.connect(lambda: self._seek_relative(self.SEEK_STEP_SECONDS))
        self.stop_button.clicked.connect(lambda: self._invoke("stop"))
        self.next_button.clicked.connect(lambda: self._invoke("next"))

        self.position_row = QHBoxLayout()
        self.position_row.setContentsMargins(0, 0, 0, 0)
        self.position_row.setSpacing(4)
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
        self.options_row.setSpacing(4)
        root.addLayout(self.options_row)

        self.loop_button = QPushButton("Loop: Off", self)
        self.shuffle_button = QPushButton("Shuffle: Off", self)
        self.loop_button.setMinimumHeight(24)
        self.shuffle_button.setMinimumHeight(24)
        self.options_row.addWidget(self.loop_button)
        self.options_row.addWidget(self.shuffle_button)
        self.options_row.addStretch(1)

        self.loop_button.clicked.connect(self._cycle_loop)
        self.shuffle_button.clicked.connect(self._toggle_shuffle)

        self.volume_row = QHBoxLayout()
        self.volume_row.setContentsMargins(0, 0, 0, 0)
        self.volume_row.setSpacing(4)
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
        self.mute_button.setMinimumHeight(24)
        self.volume_row.addWidget(self.mute_button)

        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_slider.sliderPressed.connect(self._on_volume_slider_pressed)
        self.volume_slider.sliderReleased.connect(self._commit_volume)
        self.mute_button.clicked.connect(self._toggle_mute)

    def bind(self, info: PlayerInfo) -> None:
        self._player_name = info.name
        self._position_seconds = info.position_seconds
        self._length_seconds = info.length_seconds
        previous_track_id = self._track_id
        self._track_id = info.track_id or ""
        self._loop_status = info.loop_status or "None"
        self._shuffle = info.shuffle

        if previous_track_id and self._track_id != previous_track_id:
            self._position_pending_seconds = None
            self._position_pending_track_id = ""

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

        control_fallback = bool(info.track_id or title or artist or album) and status.lower() in {"playing", "paused"}
        can_control = bool(info.can_control) or control_fallback
        transport_fallback = can_control and control_fallback

        show_prev = can_control and (bool(info.can_go_previous) or transport_fallback)
        show_next = can_control and (bool(info.can_go_next) or transport_fallback)
        show_play_pause = can_control and (
            bool(info.can_play)
            or bool(info.can_pause)
            or status.lower() in {"playing", "paused", "stopped"}
        )
        show_stop = False

        can_seek = can_control and (bool(info.can_seek) or bool(info.length_seconds and info.length_seconds > 0))
        any_transport_visible = show_prev or show_next or show_play_pause or can_seek or show_stop
        self._set_layout_visible(self.transport_row, any_transport_visible)

        self.prev_button.setVisible(show_prev)
        self.seek_back_button.setVisible(can_seek)
        self.next_button.setVisible(show_next)
        self.seek_forward_button.setVisible(can_seek)
        self.stop_button.setVisible(show_stop)
        self.play_pause_button.setVisible(show_play_pause)
        if status.lower() == "playing":
            self.play_pause_button.setText("⏸")
            self.play_pause_button.setToolTip("Pause")
        else:
            self.play_pause_button.setText("▶")
            self.play_pause_button.setToolTip("Play")

        self._position_syncing = True
        try:
            has_position = (
                can_seek
                and info.position_seconds is not None
                and info.length_seconds is not None
                and info.length_seconds > 0
            )
            if has_position:
                self.position_slider.setEnabled(True)
                self.duration_label.setText(self._format_time(info.length_seconds))
                self._set_layout_visible(self.position_row, True)
                if not self._position_dragging:
                    display_seconds = self._display_position_seconds(info)
                    ratio = max(0.0, min(1.0, display_seconds / info.length_seconds))
                    slider_value = int(round(ratio * self.POSITION_SLIDER_MAX))
                    self.position_slider.setValue(slider_value)
                    self.position_label.setText(self._format_time(display_seconds))
            else:
                self._position_pending_seconds = None
                self._position_pending_track_id = ""
                self.position_slider.setEnabled(False)
                self.position_slider.setValue(0)
                self.position_label.setText("0:00")
                self.duration_label.setText("0:00")
                self._set_layout_visible(self.position_row, False)
        finally:
            self._position_syncing = False

        show_loop = can_control
        show_shuffle = can_control and info.shuffle is not None
        self._set_layout_visible(self.options_row, show_loop or show_shuffle)
        self.loop_button.setVisible(show_loop)
        if show_loop:
            self.loop_button.setText(f"Loop: {self._loop_label(self._loop_status)}")

        self.shuffle_button.setVisible(show_shuffle)
        if show_shuffle:
            self.shuffle_button.setText("Shuffle: On" if info.shuffle else "Shuffle: Off")

        self._volume_syncing = True
        try:
            if info.volume is None:
                self._volume_pending_percent = None
                self.volume_slider.setEnabled(False)
                self.mute_button.setEnabled(False)
                self.volume_slider.setValue(0)
                self.volume_value_label.setText("N/A")
                self.mute_button.setText("Mute")
                self._set_layout_visible(self.volume_row, False)
            else:
                percent = max(0, min(100, int(round(info.volume * 100.0))))
                display_percent = self._display_volume_percent(percent)
                self.volume_slider.setEnabled(True)
                self.mute_button.setEnabled(True)
                if not self._volume_dragging:
                    self.volume_slider.setValue(display_percent)
                self.volume_value_label.setText(f"{display_percent}%")
                self.mute_button.setText("Unmute" if display_percent == 0 else "Mute")
                self._set_layout_visible(self.volume_row, True)
                if display_percent > 0:
                    self._last_nonzero_volume = max(0.01, display_percent / 100.0)
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

    def _schedule_refresh(self, delay_ms: int) -> None:
        self._refresh_timer.start(max(0, int(delay_ms)))

    def _display_position_seconds(self, info: PlayerInfo) -> float:
        if self._position_dragging and self._length_seconds and self._length_seconds > 0:
            ratio = max(0.0, min(1.0, self.position_slider.value() / self.POSITION_SLIDER_MAX))
            return ratio * self._length_seconds

        pending_seconds = self._active_pending_position_seconds(info)
        if pending_seconds is not None:
            return pending_seconds
        return max(0.0, float(info.position_seconds or 0.0))

    def _active_pending_position_seconds(self, info: PlayerInfo) -> float | None:
        pending_seconds = self._position_pending_seconds
        if pending_seconds is None:
            return None
        if time.monotonic() >= self._position_hold_until:
            self._position_pending_seconds = None
            self._position_pending_track_id = ""
            return None
        if self._position_pending_track_id and info.track_id and info.track_id != self._position_pending_track_id:
            self._position_pending_seconds = None
            self._position_pending_track_id = ""
            return None
        if info.position_seconds is not None and abs(info.position_seconds - pending_seconds) <= 1.0:
            self._position_pending_seconds = None
            self._position_pending_track_id = ""
            return None
        return pending_seconds

    def _set_local_position_preview(self, seconds: float) -> None:
        self._position_seconds = seconds
        if self._length_seconds is None or self._length_seconds <= 0:
            return
        ratio = max(0.0, min(1.0, seconds / self._length_seconds))
        slider_value = int(round(ratio * self.POSITION_SLIDER_MAX))
        self._position_syncing = True
        try:
            self.position_slider.setValue(slider_value)
            self.position_label.setText(self._format_time(seconds))
        finally:
            self._position_syncing = False

    def _display_volume_percent(self, remote_percent: int) -> int:
        if self._volume_dragging:
            return max(0, min(100, int(self.volume_slider.value())))

        pending_percent = self._volume_pending_percent
        if pending_percent is None:
            return remote_percent
        if time.monotonic() >= self._volume_hold_until:
            self._volume_pending_percent = None
            return remote_percent
        if abs(remote_percent - pending_percent) <= 2:
            self._volume_pending_percent = None
            return remote_percent
        return pending_percent

    def _invoke(self, command: str) -> None:
        if not self._player_name:
            return
        self._send_command(self._player_name, command)
        self._schedule_refresh(180)

    def _seek_relative(self, delta_seconds: float) -> None:
        if not self._player_name:
            return
        if self._seek_relative_cb(self._player_name, delta_seconds):
            base_seconds = self._position_pending_seconds
            if base_seconds is None:
                base_seconds = self._position_seconds
            if base_seconds is not None:
                target_seconds = max(0.0, base_seconds + float(delta_seconds))
                if self._length_seconds is not None and self._length_seconds > 0:
                    target_seconds = min(target_seconds, self._length_seconds)
                self._position_pending_seconds = target_seconds
                self._position_pending_track_id = self._track_id
                self._position_hold_until = time.monotonic() + 1.2
                self._set_local_position_preview(target_seconds)
            self._schedule_refresh(320)
            return
        self._schedule_refresh(0)

    def _on_position_slider_pressed(self) -> None:
        # Prevent bind() updates from fighting the user's drag.
        self._position_syncing = False
        self._position_dragging = True

    @Slot(int)
    def _on_position_changed(self, value: int) -> None:
        if self._position_syncing or self._length_seconds is None or self._length_seconds <= 0:
            return
        ratio = max(0.0, min(1.0, value / self.POSITION_SLIDER_MAX))
        seconds = ratio * self._length_seconds
        self.position_label.setText(self._format_time(seconds))

    @Slot()
    def _commit_position(self) -> None:
        self._position_dragging = False
        if (
            not self._player_name
            or not self.position_slider.isEnabled()
            or self._length_seconds is None
            or self._length_seconds <= 0
        ):
            return

        ratio = max(0.0, min(1.0, self.position_slider.value() / self.POSITION_SLIDER_MAX))
        target_seconds = ratio * self._length_seconds
        if self._set_position_cb(self._player_name, target_seconds):
            self._position_pending_seconds = target_seconds
            self._position_pending_track_id = self._track_id
            self._position_hold_until = time.monotonic() + 1.2
            self._set_local_position_preview(target_seconds)
            self._schedule_refresh(320)
            return
        self._schedule_refresh(0)

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
        self._schedule_refresh(180)

    @Slot()
    def _toggle_shuffle(self) -> None:
        if not self._player_name or self._shuffle is None:
            return

        next_value = not self._shuffle
        if self._set_shuffle_cb(self._player_name, next_value):
            self._shuffle = next_value
        self._schedule_refresh(180)

    @Slot(int)
    def _on_volume_changed(self, value: int) -> None:
        if self._volume_syncing:
            return
        clamped = max(0, min(100, int(value)))
        self.volume_value_label.setText(f"{clamped}%")
        self.mute_button.setText("Unmute" if clamped == 0 else "Mute")

    @Slot()
    def _on_volume_slider_pressed(self) -> None:
        self._volume_dragging = True

    @Slot()
    def _commit_volume(self) -> None:
        self._volume_dragging = False
        if self._volume_syncing or not self._player_name or not self.volume_slider.isEnabled():
            return
        value = max(0, min(100, int(self.volume_slider.value())))
        if value > 0:
            self._last_nonzero_volume = value / 100.0
        if self._set_volume(self._player_name, value / 100.0):
            self._volume_pending_percent = value
            self._volume_hold_until = time.monotonic() + 0.9
            self._schedule_refresh(240)
            return
        self._schedule_refresh(0)

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

        if self._set_volume(self._player_name, target):
            target_percent = max(0, min(100, int(round(target * 100.0))))
            self._volume_pending_percent = target_percent
            self._volume_hold_until = time.monotonic() + 0.9
            self._volume_dragging = False
            self._volume_syncing = True
            try:
                self.volume_slider.setValue(target_percent)
                self.volume_value_label.setText(f"{target_percent}%")
                self.mute_button.setText("Unmute" if target_percent == 0 else "Mute")
            finally:
                self._volume_syncing = False
            self._schedule_refresh(240)
            return
        self._schedule_refresh(0)
