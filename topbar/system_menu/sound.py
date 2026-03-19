from __future__ import annotations

import concurrent.futures
import time
from typing import Callable

from PySide6.QtCore import QCoreApplication, QObject, QProcess, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .service import AudioStreamInfo, SoundSnapshot, VolumeService

_SHARED_SOUND_BACKEND: "LiveSoundBackend | None" = None


class LiveSoundBackend(QObject):
    snapshotChanged = Signal(object)

    def __init__(self, volume: VolumeService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._volume = volume
        self._shutting_down = False
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="topbar-sound")
        self._future: concurrent.futures.Future | None = None
        self._refresh_requested_while_busy = False

        self._result_pump = QTimer(self)
        self._result_pump.setInterval(40)
        self._result_pump.timeout.connect(self._drain_future)

        self._event_refresh_timer = QTimer(self)
        self._event_refresh_timer.setSingleShot(True)
        self._event_refresh_timer.setInterval(80)
        self._event_refresh_timer.timeout.connect(self.refresh)

        self._subscribe = QProcess(self)
        self._subscribe.readyReadStandardOutput.connect(self._on_subscribe_output)
        self._subscribe.readyReadStandardError.connect(self._on_subscribe_output)
        self._subscribe.errorOccurred.connect(lambda *_args: self._restart_subscribe_later())
        self._subscribe.finished.connect(lambda *_args: self._restart_subscribe_later())

    def start(self) -> None:
        if self._shutting_down:
            return
        self.refresh()
        self._start_subscribe()

    def refresh(self) -> None:
        if self._shutting_down:
            return
        if self._future is not None:
            self._refresh_requested_while_busy = True
            return
        try:
            self._future = self._executor.submit(self._volume.sound_snapshot)
        except Exception:
            self._future = None
            return
        if not self._result_pump.isActive():
            self._result_pump.start()

    def schedule_refresh(self, delay_ms: int = 80) -> None:
        if self._shutting_down:
            return
        self._event_refresh_timer.start(max(0, int(delay_ms)))

    def set_default_volume_percent(self, percent: int) -> bool:
        if not self._volume.set_volume_percent(percent):
            return False
        self.schedule_refresh(60)
        return True

    def toggle_default_mute(self) -> bool:
        if not self._volume.toggle_mute():
            return False
        self.schedule_refresh(60)
        return True

    def set_stream_volume_percent(self, stream_id: int, percent: int) -> bool:
        if not self._volume.set_stream_volume_percent(stream_id, percent):
            return False
        self.schedule_refresh(60)
        return True

    def toggle_stream_mute(self, stream_id: int) -> bool:
        if not self._volume.toggle_stream_mute(stream_id):
            return False
        self.schedule_refresh(60)
        return True

    def _drain_future(self) -> None:
        if self._shutting_down:
            self._future = None
            self._result_pump.stop()
            return
        future = self._future
        if future is None:
            self._result_pump.stop()
            return
        if not future.done():
            return

        self._future = None
        self._result_pump.stop()
        try:
            snapshot = future.result()
        except Exception:
            snapshot = None
        if isinstance(snapshot, SoundSnapshot):
            self.snapshotChanged.emit(snapshot)
        if self._refresh_requested_while_busy:
            self._refresh_requested_while_busy = False
            self.refresh()

    def _start_subscribe(self) -> None:
        if self._shutting_down:
            return
        pactl = self._volume.pactl_path()
        if not pactl or self._subscribe.state() != QProcess.NotRunning:
            return
        self._subscribe.start(pactl, ["subscribe"])

    def _restart_subscribe_later(self) -> None:
        if self._shutting_down or QCoreApplication.closingDown():
            return
        if not self._volume.has_pactl():
            return
        if self._subscribe.state() != QProcess.NotRunning:
            return
        QTimer.singleShot(1000, self._start_subscribe)

    def _on_subscribe_output(self) -> None:
        if self._shutting_down:
            return
        text = bytes(self._subscribe.readAllStandardOutput()).decode("utf-8", errors="replace")
        text += bytes(self._subscribe.readAllStandardError()).decode("utf-8", errors="replace")
        if any(token in text.lower() for token in ("sink", "sink-input", "server")):
            self.schedule_refresh(80)

    @Slot()
    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._event_refresh_timer.stop()
        self._result_pump.stop()
        if self._subscribe.state() != QProcess.NotRunning:
            self._subscribe.kill()
            self._subscribe.waitForFinished(250)
        future = self._future
        self._future = None
        if future is not None:
            try:
                future.cancel()
            except Exception:
                pass
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass


def shared_sound_backend() -> LiveSoundBackend | None:
    global _SHARED_SOUND_BACKEND
    if _SHARED_SOUND_BACKEND is not None:
        return _SHARED_SOUND_BACKEND

    app = QApplication.instance()
    if app is None:
        return None

    volume = VolumeService()
    if not volume.has_pactl():
        return None

    backend = LiveSoundBackend(volume, app)
    app.aboutToQuit.connect(backend.shutdown)
    backend.start()
    _SHARED_SOUND_BACKEND = backend
    return backend


class ApplicationVolumeCard(QFrame):
    def __init__(
        self,
        set_volume: Callable[[int, int], bool],
        toggle_mute: Callable[[int], bool],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._set_volume = set_volume
        self._toggle_mute = toggle_mute
        self._stream_id = -1
        self._volume_syncing = False
        self._volume_dragging = False
        self._pending_volume_percent: int | None = None
        self._volume_hold_until = 0.0
        self._last_nonzero_percent = 50

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(40)
        self._apply_timer.timeout.connect(self._apply_pending_volume)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        root.addLayout(title_row)

        self.app_label = QLabel("Application", self)
        self.app_label.setObjectName("systemMenuValue")
        title_row.addWidget(self.app_label, stretch=1)

        self.mute_button = QPushButton("Mute", self)
        self.mute_button.clicked.connect(self.toggle_mute)
        title_row.addWidget(self.mute_button)

        self.title_label = QLabel("", self)
        self.title_label.setObjectName("systemMenuMutedText")
        self.title_label.setWordWrap(True)
        self.title_label.hide()
        root.addWidget(self.title_label)

        slider_row = QHBoxLayout()
        slider_row.setSpacing(8)
        root.addLayout(slider_row)

        self.volume_slider = QSlider(Qt.Horizontal, self)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_slider.sliderPressed.connect(self._on_slider_pressed)
        self.volume_slider.sliderReleased.connect(self._on_slider_released)
        slider_row.addWidget(self.volume_slider, stretch=1)

        self.volume_value_label = QLabel("--", self)
        self.volume_value_label.setObjectName("systemMenuValue")
        slider_row.addWidget(self.volume_value_label)

    def bind(self, info: AudioStreamInfo) -> None:
        self._stream_id = info.stream_id
        self.app_label.setText(info.app_name or f"Stream {info.stream_id}")
        detail = (info.title or "").strip()
        if detail:
            self.title_label.setText(detail)
            self.title_label.show()
        else:
            self.title_label.hide()
            self.title_label.clear()

        percent = info.volume_percent
        if percent is None:
            self.volume_slider.setEnabled(False)
            self.mute_button.setEnabled(False)
            self.volume_value_label.setText("N/A")
            return

        display_percent = self._display_volume_percent(percent)
        if display_percent > 0:
            self._last_nonzero_percent = display_percent

        previous_state = self.volume_slider.blockSignals(True)
        self._volume_syncing = True
        if not self._volume_dragging:
            self.volume_slider.setValue(display_percent)
        self._volume_syncing = False
        self.volume_slider.blockSignals(previous_state)

        self.volume_slider.setEnabled(True)
        self.mute_button.setEnabled(True)
        self.volume_value_label.setText(f"{display_percent}%")
        self.mute_button.setText("Unmute" if bool(info.is_muted) or display_percent == 0 else "Mute")

    def _display_volume_percent(self, remote_percent: int) -> int:
        if self._volume_dragging:
            return max(0, min(100, int(self.volume_slider.value())))
        pending = self._pending_volume_percent
        if pending is None:
            return remote_percent
        if time.monotonic() >= self._volume_hold_until:
            self._pending_volume_percent = None
            return remote_percent
        if abs(remote_percent - pending) <= 2:
            self._pending_volume_percent = None
            return remote_percent
        return pending

    @Slot(int)
    def _on_volume_changed(self, value: int) -> None:
        if self._volume_syncing or self._stream_id < 0:
            return
        clamped = max(0, min(100, int(value)))
        if clamped > 0:
            self._last_nonzero_percent = clamped
        self._pending_volume_percent = clamped
        self._volume_hold_until = time.monotonic() + 0.9
        self.volume_value_label.setText(f"{clamped}%")
        self._apply_timer.start()

    @Slot()
    def _on_slider_pressed(self) -> None:
        self._volume_dragging = True

    @Slot()
    def _on_slider_released(self) -> None:
        self._volume_dragging = False
        self._apply_timer.start(0)

    @Slot()
    def _apply_pending_volume(self) -> None:
        if self._stream_id < 0:
            return
        value = self._pending_volume_percent
        if value is None:
            value = self.volume_slider.value()
        if not self._set_volume(self._stream_id, value):
            QMessageBox.warning(self, "Application Volume", "Failed to set the application volume.")

    @Slot()
    def toggle_mute(self) -> None:
        if self._stream_id < 0 or not self.mute_button.isEnabled():
            return
        current_value = max(0, min(100, int(self.volume_slider.value())))
        if current_value > 0:
            self._last_nonzero_percent = current_value
            target_percent = 0
        else:
            target_percent = max(1, int(self._last_nonzero_percent or 50))

        if not self._toggle_mute(self._stream_id):
            QMessageBox.warning(self, "Application Volume", "Failed to toggle the application mute state.")
            return

        self._pending_volume_percent = target_percent
        self._volume_hold_until = time.monotonic() + 0.9
        previous_state = self.volume_slider.blockSignals(True)
        self._volume_syncing = True
        self.volume_slider.setValue(target_percent)
        self._volume_syncing = False
        self.volume_slider.blockSignals(previous_state)
        self.volume_value_label.setText(f"{target_percent}%")
        self.mute_button.setText("Unmute" if target_percent == 0 else "Mute")


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
        self._volume_dragging = False
        self._pending_volume_percent: int | None = None
        self._volume_hold_until = 0.0
        self._last_nonzero_percent = 50
        self._stream_cards: dict[int, ApplicationVolumeCard] = {}

        self._volume_apply_timer = QTimer(self)
        self._volume_apply_timer.setSingleShot(True)
        self._volume_apply_timer.setInterval(40)
        self._volume_apply_timer.timeout.connect(self._apply_pending_volume)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)

        self._live_backend: LiveSoundBackend | None = None
        if self.volume.has_pactl():
            self._live_backend = shared_sound_backend()
        if self._live_backend is not None:
            self._live_backend.snapshotChanged.connect(self.apply_snapshot)

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
        self.volume_slider.sliderPressed.connect(self._on_slider_pressed)
        self.volume_slider.sliderReleased.connect(self._on_slider_released)
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

        self.streams_title = QLabel("Applications", self)
        self.streams_title.setObjectName("systemMenuSectionTitle")
        self.streams_title.hide()
        root.addWidget(self.streams_title)

        self.streams_empty_label = QLabel("", self)
        self.streams_empty_label.setObjectName("systemMenuMutedText")
        self.streams_empty_label.setWordWrap(True)
        self.streams_empty_label.hide()
        root.addWidget(self.streams_empty_label)

        self.streams_host = QWidget(self)
        self.streams_layout = QVBoxLayout(self.streams_host)
        self.streams_layout.setContentsMargins(0, 0, 0, 0)
        self.streams_layout.setSpacing(8)
        self.streams_layout.addStretch(1)
        root.addWidget(self.streams_host)

        self._set_initial_state()

    def _set_initial_state(self) -> None:
        available = self.volume.available()
        self.volume_slider.setEnabled(available)
        self.volume_mute_button.setEnabled(available)
        if not available:
            self.volume_value_label.setText("Unavailable")
        if not self.volume.has_pactl():
            self.streams_empty_label.setText("Install pactl support to enable application audio stream controls.")
            self.streams_empty_label.show()

    def refresh(self) -> None:
        if self._live_backend is not None:
            self._live_backend.refresh()
            return
        if self._request_refresh is not None:
            self._request_refresh()
            return
        self.apply_snapshot(self.volume.sound_snapshot())

    def apply_snapshot(self, snapshot: SoundSnapshot) -> None:
        available = snapshot.available
        self.volume_slider.setEnabled(available)
        self.volume_mute_button.setEnabled(available)
        if not available:
            self._pending_volume_percent = None
            self.volume_value_label.setText("Unavailable")
            self.volume_mute_button.setText("Mute")
            self._sync_stream_cards(())
            return

        percent = snapshot.volume_percent
        muted = snapshot.is_muted
        if percent is None:
            self.volume_value_label.setText("Unknown")
        else:
            display_percent = self._display_volume_percent(percent)
            if display_percent > 0:
                self._last_nonzero_percent = display_percent
            previous_state = self.volume_slider.blockSignals(True)
            self._volume_syncing = True
            if not self._volume_dragging:
                self.volume_slider.setValue(display_percent)
            self._volume_syncing = False
            self.volume_slider.blockSignals(previous_state)

            suffix = " muted" if muted and display_percent == 0 else ""
            self.volume_value_label.setText(f"{display_percent}%{suffix}")
            self.volume_mute_button.setText("Unmute" if muted or display_percent == 0 else "Mute")

        self._sync_stream_cards(snapshot.streams)

    def _sync_stream_cards(self, streams: tuple[AudioStreamInfo, ...]) -> None:
        active_ids = {stream.stream_id for stream in streams}
        for stream_id in [sid for sid in self._stream_cards if sid not in active_ids]:
            card = self._stream_cards.pop(stream_id)
            self.streams_layout.removeWidget(card)
            card.deleteLater()

        for stream in streams:
            card = self._stream_cards.get(stream.stream_id)
            if card is None:
                card = ApplicationVolumeCard(
                    self._set_stream_volume_percent,
                    self._toggle_stream_mute,
                    self.streams_host,
                )
                self._stream_cards[stream.stream_id] = card
                self.streams_layout.insertWidget(self.streams_layout.count() - 1, card)
            card.bind(stream)

        has_streams = bool(streams)
        self.streams_title.setVisible(has_streams)
        self.streams_host.setVisible(has_streams)
        if self.volume.has_pactl():
            self.streams_empty_label.hide()

    def open_sound_settings(self) -> None:
        if self.volume.open_settings():
            return
        QMessageBox.warning(self, "Open Settings Failed", "Could not open sound settings.")

    def toggle_mute(self) -> None:
        current_value = max(0, min(100, int(self.volume_slider.value())))
        if current_value > 0:
            self._last_nonzero_percent = current_value
            target_percent = 0
        else:
            target_percent = max(1, int(self._last_nonzero_percent or 50))

        ok = self._live_backend.toggle_default_mute() if self._live_backend is not None else self.volume.toggle_mute()
        if not ok:
            QMessageBox.warning(self, "Volume", "Failed to toggle mute.")
            return

        self._pending_volume_percent = target_percent
        self._volume_hold_until = time.monotonic() + 0.9
        previous_state = self.volume_slider.blockSignals(True)
        self._volume_syncing = True
        self.volume_slider.setValue(target_percent)
        self._volume_syncing = False
        self.volume_slider.blockSignals(previous_state)
        self.volume_value_label.setText(f"{target_percent}%")
        self.volume_mute_button.setText("Unmute" if target_percent == 0 else "Mute")
        if self._live_backend is None:
            self._schedule_refresh(260)

    @Slot(int)
    def _on_volume_slider_changed(self, value: int) -> None:
        if self._volume_syncing:
            return
        clamped = max(0, min(100, int(value)))
        if clamped > 0:
            self._last_nonzero_percent = clamped
        self._pending_volume_percent = clamped
        self._volume_hold_until = time.monotonic() + 0.9
        self.volume_value_label.setText(f"{clamped}%")
        self._volume_apply_timer.start()

    @Slot()
    def _on_slider_pressed(self) -> None:
        self._volume_dragging = True

    @Slot()
    def _on_slider_released(self) -> None:
        self._volume_dragging = False
        self._volume_apply_timer.start(0)

    @Slot()
    def _apply_pending_volume(self) -> None:
        value = self._pending_volume_percent
        if value is None:
            value = self.volume_slider.value()
        ok = (
            self._live_backend.set_default_volume_percent(value)
            if self._live_backend is not None
            else self.volume.set_volume_percent(value)
        )
        if ok:
            if self._live_backend is None:
                self._schedule_refresh(260)
            return
        QMessageBox.warning(self, "Volume", "Failed to set the system volume.")
        self.refresh()

    def _display_volume_percent(self, remote_percent: int) -> int:
        if self._volume_dragging:
            return max(0, min(100, int(self.volume_slider.value())))
        pending = self._pending_volume_percent
        if pending is None:
            return remote_percent
        if time.monotonic() >= self._volume_hold_until:
            self._pending_volume_percent = None
            return remote_percent
        if abs(remote_percent - pending) <= 2:
            self._pending_volume_percent = None
            return remote_percent
        return pending

    def _schedule_refresh(self, delay_ms: int) -> None:
        self._refresh_timer.start(max(0, int(delay_ms)))

    def _set_stream_volume_percent(self, stream_id: int, percent: int) -> bool:
        if self._live_backend is not None:
            return self._live_backend.set_stream_volume_percent(stream_id, percent)
        return self.volume.set_stream_volume_percent(stream_id, percent)

    def _toggle_stream_mute(self, stream_id: int) -> bool:
        if self._live_backend is not None:
            return self._live_backend.toggle_stream_mute(stream_id)
        return self.volume.toggle_stream_mute(stream_id)
