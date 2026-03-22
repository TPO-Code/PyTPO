"""Read-only audio viewer/player tab for project assets."""

from __future__ import annotations

import math
import mimetypes
import os
import struct
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtMultimedia import (
    QAudioBuffer,
    QAudioDecoder,
    QAudioFormat,
    QAudioOutput,
    QMediaPlayer,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pytpo.ui.icons.asset_icons import (
    PAUSE_ICON_NAME,
    PLAY_ICON_NAME,
    STOP_ICON_NAME,
    app_palette_color_hex,
    asset_icon,
)


def _format_media_time(ms: int) -> str:
    total_seconds = max(0, int(round(max(0, ms) / 1000.0)))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_file_size(num_bytes: int) -> str:
    value = max(0, int(num_bytes))
    units = ["B", "KB", "MB", "GB"]
    size = float(value)
    unit = units[0]
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            break
        size /= 1024.0
    if unit == "B":
        return f"{int(size)} {unit}"
    return f"{size:.1f} {unit}"


def _format_bitrate(bits_per_second: int) -> str:
    value = max(0, int(bits_per_second))
    if value <= 0:
        return ""
    return f"{int(round(value / 1000.0))} kbps"


def _sample_format_label(sample_format: QAudioFormat.SampleFormat, *, bytes_per_sample: int) -> str:
    if sample_format == QAudioFormat.SampleFormat.UInt8:
        return "u8"
    if sample_format == QAudioFormat.SampleFormat.Int16:
        return "s16"
    if sample_format == QAudioFormat.SampleFormat.Int32:
        return "s32"
    if sample_format == QAudioFormat.SampleFormat.Float:
        return "float"
    if bytes_per_sample > 0:
        return f"{bytes_per_sample * 8}-bit"
    return ""


class AudioOverviewWidget(QWidget):
    seekRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PyTPOSoundOverview")
        self.setMinimumHeight(92)
        self.setCursor(Qt.PointingHandCursor)
        self._levels: list[float] = []
        self._playback_ratio = 0.0
        self._buffered_ratio = 0.0
        self._error_text = ""
        self._bg_color = QColor("#252526")

    def set_viewer_background(self, color: QColor) -> None:
        if color == self._bg_color:
            return
        self._bg_color = QColor(color)
        self.update()

    def set_levels(self, levels: list[float]) -> None:
        self._levels = [max(0.0, min(1.0, float(value))) for value in levels]
        self.update()

    def has_levels(self) -> bool:
        return bool(self._levels)

    def set_error_state(self, message: str) -> None:
        self._error_text = str(message or "").strip()
        self.update()

    def clear_error_state(self) -> None:
        if not self._error_text:
            return
        self._error_text = ""
        self.update()

    def set_buffered_ratio(self, ratio: float) -> None:
        clamped = max(0.0, min(1.0, float(ratio)))
        if abs(clamped - self._buffered_ratio) < 0.001:
            return
        self._buffered_ratio = clamped
        self.update()

    def set_playback_ratio(self, ratio: float) -> None:
        clamped = max(0.0, min(1.0, float(ratio)))
        if abs(clamped - self._playback_ratio) < 0.001:
            return
        old_x = self._playback_x()
        self._playback_ratio = clamped
        new_x = self._playback_x()
        margin = 4
        if old_x >= 0:
            self.update(QRect(max(0, old_x - margin), 0, margin * 2 + 1, self.height()))
        if new_x >= 0 and new_x != old_x:
            self.update(QRect(max(0, new_x - margin), 0, margin * 2 + 1, self.height()))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._emit_seek_for_pos(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton:
            self._emit_seek_for_pos(event.position().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        rect = self.rect().adjusted(1, 1, -1, -1)
        if rect.width() <= 2 or rect.height() <= 2:
            return

        bg = QColor(self._bg_color)
        border = bg.lighter(125)
        midline = bg.lighter(145)
        waveform = bg.lighter(195)
        played = bg.lighter(265)
        buffered = bg.lighter(118)
        head = bg.lighter(290)
        if bg.lightness() > 160:
            border = bg.darker(118)
            midline = bg.darker(110)
            waveform = bg.darker(180)
            played = bg.darker(225)
            buffered = bg.darker(106)
            head = bg.darker(250)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(rect, bg.darker(112))
        painter.setPen(border)
        painter.drawRect(rect)

        inner = rect.adjusted(8, 10, -8, -10)
        center_y = inner.center().y()
        painter.setPen(QPen(midline, 1))
        painter.drawLine(inner.left(), center_y, inner.right(), center_y)

        if self._levels:
            count = len(self._levels)
            width = max(1, inner.width())
            played_limit = inner.left() + int(round(width * self._playback_ratio))
            buffered_limit = inner.left() + int(round(width * self._buffered_ratio))
            for index, level in enumerate(self._levels):
                x = inner.left() + int(index * width / max(1, count - 1))
                amplitude = max(1, int(level * max(2, inner.height() // 2)))
                color = waveform
                if x <= buffered_limit:
                    color = buffered
                if x <= played_limit:
                    color = played
                painter.setPen(color)
                painter.drawLine(x, center_y - amplitude, x, center_y + amplitude)
        elif self._error_text:
            painter.setPen(border.lighter(140))
            painter.drawText(inner, Qt.AlignCenter, self._error_text)
        else:
            painter.setPen(border.lighter(130))
            painter.drawText(inner, Qt.AlignCenter, "Loading audio overview...")

        x = self._playback_x(inner)
        if x >= 0:
            painter.setPen(QPen(head, 2))
            painter.drawLine(x, inner.top(), x, inner.bottom())

    def _playback_x(self, rect: QRect | None = None) -> int:
        target = rect if isinstance(rect, QRect) else self.rect().adjusted(9, 10, -9, -10)
        if target.width() <= 0:
            return -1
        return target.left() + int(round(target.width() * self._playback_ratio))

    def _emit_seek_for_pos(self, pos: QPoint) -> None:
        rect = self.rect().adjusted(9, 10, -9, -10)
        if rect.width() <= 0:
            return
        ratio = (pos.x() - rect.left()) / float(max(1, rect.width()))
        self.seekRequested.emit(max(0.0, min(1.0, ratio)))


class AudioOverviewLoader(QObject):
    overviewReady = Signal(list)
    failed = Signal(str)
    formatDetected = Signal(dict)

    MAX_BINS = 1200

    def __init__(self, parent=None):
        super().__init__(parent)
        self._decoder: QAudioDecoder | None = None
        self._duration_us = 0
        self._levels: list[float] = []
        self._path = ""
        self._format_emitted = False

    def cancel(self) -> None:
        decoder = self._decoder
        self._decoder = None
        self._levels = []
        self._duration_us = 0
        self._path = ""
        if decoder is None:
            return
        try:
            decoder.stop()
        except Exception:
            pass
        decoder.deleteLater()

    def start(self, path: str, *, duration_ms: int) -> bool:
        self.cancel()
        clean = str(path or "").strip()
        if not clean or duration_ms <= 0:
            return False

        self._path = clean
        self._duration_us = max(1, int(duration_ms) * 1000)
        self._levels = [0.0] * self.MAX_BINS
        self._format_emitted = False
        decoder = QAudioDecoder(self)
        self._decoder = decoder
        decoder.bufferReady.connect(self._on_buffer_ready)
        decoder.finished.connect(self._on_finished)
        decoder.error.connect(self._on_error)
        decoder.setSource(QUrl.fromLocalFile(clean))
        decoder.start()
        return True

    def _on_buffer_ready(self) -> None:
        decoder = self._decoder
        if decoder is None:
            return
        while True:
            try:
                buffer = decoder.read()
            except Exception:
                break
            if not isinstance(buffer, QAudioBuffer) or not buffer.isValid():
                break
            self._consume_buffer(buffer)

    def _consume_buffer(self, buffer: QAudioBuffer) -> None:
        if self._duration_us <= 0:
            return

        audio_format = buffer.format()
        frame_count = int(buffer.frameCount())
        channel_count = max(1, int(audio_format.channelCount()))
        sample_rate = max(1, int(audio_format.sampleRate()))
        bytes_per_sample = max(1, int(audio_format.bytesPerSample()))
        bytes_per_frame = max(bytes_per_sample * channel_count, int(audio_format.bytesPerFrame()))
        sample_format = audio_format.sampleFormat()
        if frame_count <= 0 or bytes_per_frame <= 0:
            return
        if not self._format_emitted:
            self._format_emitted = True
            self.formatDetected.emit(
                {
                    "sample_rate": sample_rate,
                    "channels": channel_count,
                    "sample_format": _sample_format_label(
                        sample_format,
                        bytes_per_sample=bytes_per_sample,
                    ),
                }
            )

        try:
            data = bytes(buffer.constData())
        except Exception:
            return

        data_len = len(data)
        if data_len < bytes_per_frame:
            return

        start_us = max(0, int(buffer.startTime()))
        sample_step = max(1, frame_count // 768)
        max_bin = len(self._levels) - 1
        for frame_index in range(0, frame_count, sample_step):
            frame_offset = frame_index * bytes_per_frame
            if frame_offset + bytes_per_frame > data_len:
                break
            peak = 0.0
            for channel in range(channel_count):
                sample_offset = frame_offset + (channel * bytes_per_sample)
                sample = self._decode_sample(data, sample_offset, bytes_per_sample, sample_format)
                if sample > peak:
                    peak = sample
            time_us = start_us + int((frame_index * 1_000_000) / sample_rate)
            ratio = max(0.0, min(1.0, time_us / float(self._duration_us)))
            index = min(max_bin, max(0, int(ratio * max_bin)))
            if peak > self._levels[index]:
                self._levels[index] = peak

    @staticmethod
    def _decode_sample(
        raw: bytes,
        offset: int,
        bytes_per_sample: int,
        sample_format: QAudioFormat.SampleFormat,
    ) -> float:
        if sample_format == QAudioFormat.SampleFormat.UInt8:
            value = raw[offset]
            return abs((value - 128) / 128.0)
        if sample_format == QAudioFormat.SampleFormat.Int16 and offset + 2 <= len(raw):
            value = int.from_bytes(raw[offset:offset + 2], "little", signed=True)
            return min(1.0, abs(value) / 32768.0)
        if sample_format == QAudioFormat.SampleFormat.Int32 and offset + 4 <= len(raw):
            value = int.from_bytes(raw[offset:offset + 4], "little", signed=True)
            return min(1.0, abs(value) / 2147483648.0)
        if sample_format == QAudioFormat.SampleFormat.Float and offset + 4 <= len(raw):
            value = struct.unpack_from("<f", raw, offset)[0]
            return min(1.0, abs(float(value)))
        if bytes_per_sample == 1:
            value = raw[offset]
            return abs((value - 128) / 128.0)
        if bytes_per_sample == 2 and offset + 2 <= len(raw):
            value = int.from_bytes(raw[offset:offset + 2], "little", signed=True)
            return min(1.0, abs(value) / 32768.0)
        if bytes_per_sample == 4 and offset + 4 <= len(raw):
            value = int.from_bytes(raw[offset:offset + 4], "little", signed=True)
            return min(1.0, abs(value) / 2147483648.0)
        return 0.0

    def _on_finished(self) -> None:
        if self._decoder is None:
            return
        self._on_buffer_ready()
        levels = self._smoothed_levels(self._levels)
        self.cancel()
        self.overviewReady.emit(levels)

    def _on_error(self, *_args) -> None:
        if self._decoder is None:
            return
        decoder = self._decoder
        message = ""
        if decoder is not None:
            try:
                message = str(decoder.errorString() or "").strip()
            except Exception:
                message = ""
        self.cancel()
        self.failed.emit(message or "Audio overview is unavailable for this file.")

    @staticmethod
    def _smoothed_levels(values: list[float]) -> list[float]:
        if not values:
            return []
        out: list[float] = []
        max_value = max(values)
        scale = 1.0 / max_value if max_value > 0.0 else 1.0
        for index, value in enumerate(values):
            left = values[index - 1] if index > 0 else value
            right = values[index + 1] if index + 1 < len(values) else value
            blended = max(value, (left + value + right) / 3.0)
            out.append(math.sqrt(max(0.0, blended * scale)))
        return out


class SoundPlayerEditorWidget(QWidget):
    def __init__(self, *, file_path: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("PyTPOSoundPlayer")
        self.editor_id = str(uuid.uuid4())
        self.file_path: str | None = None
        self._viewer_bg = QColor("#252526")
        self._duration_ms = 0
        self._pending_seek_ratio: float | None = None
        self._slider_pressed = False
        self._overview_requested_for = ""

        self._audio_output: QAudioOutput | None = None
        self._player = QMediaPlayer(self)
        self._overview_loader = AudioOverviewLoader(self)

        self._title_label = QLabel("Audio asset", self)
        self._title_label.setObjectName("PyTPOSoundTitle")
        self._path_label = QLabel("", self)
        self._path_label.setObjectName("PyTPOSoundPath")
        self._path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._path_label.setWordWrap(False)
        self._meta_label = QLabel("", self)
        self._meta_label.setObjectName("PyTPOSoundMeta")
        self._meta_label.setWordWrap(False)
        self._meta_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._overview = AudioOverviewWidget(self)
        self._overview.seekRequested.connect(self._seek_to_ratio)

        self._play_button = QToolButton(self)
        self._stop_button = QToolButton(self)
        self._play_button.clicked.connect(self._toggle_playback)
        self._stop_button.clicked.connect(self.stop)

        self._position_slider = QSlider(Qt.Horizontal, self)
        self._position_slider.setRange(0, 0)
        self._position_slider.sliderPressed.connect(self._on_slider_pressed)
        self._position_slider.sliderReleased.connect(self._on_slider_released)
        self._position_slider.sliderMoved.connect(self._on_slider_moved)

        self._current_time_label = QLabel("0:00", self)
        self._total_time_label = QLabel("--:--", self)

        self._volume_slider = QSlider(Qt.Horizontal, self)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setMaximumWidth(120)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)

        self._status_label = QLabel("Ready", self)
        self._status_label.setObjectName("PyTPOSoundStatus")
        self._status_label.setWordWrap(False)
        self._status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._status_label.setMinimumHeight(self._status_label.sizeHint().height())

        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(2)
        header_layout.addWidget(self._title_label)
        header_layout.addWidget(self._path_label)
        header_layout.addWidget(self._meta_label)

        header_host = QWidget(self)
        header_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header_host.setLayout(header_layout)

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        controls_layout.addWidget(self._play_button)
        controls_layout.addWidget(self._stop_button)
        controls_layout.addWidget(self._current_time_label)
        controls_layout.addWidget(self._position_slider, 1)
        controls_layout.addWidget(self._total_time_label)
        controls_layout.addSpacing(6)
        controls_layout.addWidget(QLabel("Vol", self))
        controls_layout.addWidget(self._volume_slider)

        player_layout = QVBoxLayout()
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.setSpacing(8)
        player_layout.addWidget(self._overview)
        player_layout.addLayout(controls_layout)

        player_host = QWidget(self)
        player_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        player_host.setLayout(player_layout)

        center_layout = QVBoxLayout()
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addStretch(1)
        center_layout.addWidget(player_host, 0, Qt.AlignmentFlag.AlignHCenter)
        center_layout.addStretch(1)

        center_host = QWidget(self)
        center_host.setLayout(center_layout)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.addWidget(header_host, 0)
        root.addWidget(center_host, 1)
        root.addWidget(self._status_label, 0)

        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.playbackStateChanged.connect(self._refresh_play_button)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_player_error)
        self._overview_loader.overviewReady.connect(self._on_overview_ready)
        self._overview_loader.failed.connect(self._on_overview_failed)
        self._overview_loader.formatDetected.connect(self._on_format_detected)

        self._refresh_play_button()
        self.set_viewer_background("")
        self._set_status("Ready")
        self._set_controls_enabled(False)
        self._audio_meta: dict[str, object] = {}
        self._refresh_meta_label()

        if file_path:
            self.load_file(file_path)

    def display_name(self) -> str:
        return os.path.basename(self.file_path) if self.file_path else "Audio"

    def set_file_path(self, path: str | None) -> None:
        clean = str(path).strip() if isinstance(path, str) and path.strip() else None
        self.file_path = str(Path(clean).resolve()) if clean else None

    def set_viewer_background(self, value: str | QColor | None) -> None:
        color = QColor(value) if isinstance(value, QColor) else QColor(str(value or "").strip())
        if not color.isValid():
            color = QColor(self.palette().window().color())
        if not color.isValid():
            color = QColor("#252526")
        if color == self._viewer_bg:
            return
        self._viewer_bg = QColor(color)
        frame = self._viewer_bg.darker(112) if self._viewer_bg.lightness() < 160 else self._viewer_bg.lighter(106)
        text = self.palette().text().color().name()
        muted = self.palette().mid().color().name()
        self._overview.set_viewer_background(self._viewer_bg)
        self.setStyleSheet(
            f"#PyTPOSoundPlayer{{background:{self._viewer_bg.name()};}}"
            f"#PyTPOSoundTitle{{font-weight:600;color:{text};}}"
            f"#PyTPOSoundPath{{color:{muted};}}"
            f"#PyTPOSoundMeta{{color:{muted};}}"
            f"#PyTPOSoundStatus{{background:{frame.name()};border:1px solid {frame.lighter(125).name()};"
            f"border-radius:4px;padding:6px;color:{text};}}"
        )

    def load_file(self, path: str) -> bool:
        target = str(path or "").strip()
        if not target:
            self._set_error_state("No audio file was provided.")
            return False
        cpath = str(Path(target).resolve())
        self.set_file_path(cpath)
        self._title_label.setText(os.path.basename(cpath))
        self._path_label.setText(cpath)
        self._audio_meta = {}
        self._reset_transport()
        self._overview.set_levels([])
        self._overview.clear_error_state()
        self._overview_requested_for = ""
        self._refresh_meta_label()

        if not os.path.exists(cpath):
            self._set_error_state("This audio file is missing on disk.")
            return False
        if not os.access(cpath, os.R_OK):
            self._set_error_state("This audio file is not readable.")
            return False

        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(cpath))
        self._set_status("Loading audio...")
        self._set_controls_enabled(True)
        return True

    def stop(self) -> None:
        self._player.stop()
        if self._duration_ms <= 0:
            self._position_slider.setValue(0)
            self._current_time_label.setText("0:00")
            self._overview.set_playback_ratio(0.0)

    def closeEvent(self, event) -> None:
        try:
            self._overview_loader.cancel()
        except Exception:
            pass
        try:
            self._player.stop()
        except Exception:
            pass
        super().closeEvent(event)

    def _toggle_playback(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            return
        self._ensure_audio_output()
        self._player.play()

    def _on_slider_pressed(self) -> None:
        self._slider_pressed = True

    def _on_slider_moved(self, value: int) -> None:
        self._current_time_label.setText(_format_media_time(value))
        if self._duration_ms > 0:
            self._overview.set_playback_ratio(value / float(self._duration_ms))

    def _on_slider_released(self) -> None:
        self._slider_pressed = False
        self._apply_seek_value(int(self._position_slider.value()))

    def _seek_to_ratio(self, ratio: float) -> None:
        if self._duration_ms > 0:
            self._apply_seek_value(int(round(self._duration_ms * ratio)))
            return
        self._pending_seek_ratio = max(0.0, min(1.0, ratio))
        self._set_status("Seek position will apply once the file is ready.")

    def _apply_seek_value(self, value: int) -> None:
        if self._duration_ms <= 0:
            self._set_status("Seek position will apply once the file is ready.")
            return
        clamped = max(0, min(int(value), self._duration_ms))
        self._player.setPosition(clamped)
        self._position_slider.setValue(clamped)
        self._current_time_label.setText(_format_media_time(clamped))
        self._overview.set_playback_ratio(clamped / float(max(1, self._duration_ms)))
        self._set_status("Ready")

    def _on_volume_changed(self, value: int) -> None:
        output = self._audio_output
        if output is not None:
            output.setVolume(max(0.0, min(1.0, value / 100.0)))

    def _on_duration_changed(self, duration: int) -> None:
        self._duration_ms = max(0, int(duration))
        self._position_slider.setRange(0, self._duration_ms if self._duration_ms > 0 else 0)
        self._total_time_label.setText(_format_media_time(self._duration_ms) if self._duration_ms > 0 else "--:--")
        self._set_controls_enabled(True)
        self._refresh_meta_label()
        if self._duration_ms > 0 and self.file_path and self._overview_requested_for != self.file_path:
            self._overview_requested_for = self.file_path
            self._overview_loader.start(self.file_path, duration_ms=self._duration_ms)
        pending = self._pending_seek_ratio
        if pending is not None and self._duration_ms > 0:
            self._pending_seek_ratio = None
            QTimer.singleShot(0, lambda ratio=pending: self._seek_to_ratio(ratio))

    def _on_position_changed(self, position: int) -> None:
        pos = max(0, int(position))
        if not self._slider_pressed:
            self._position_slider.setValue(pos)
            self._current_time_label.setText(_format_media_time(pos))
        if self._duration_ms > 0:
            self._overview.set_playback_ratio(pos / float(self._duration_ms))

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._set_error_state("This audio file could not be played by the current Qt multimedia backend.")
            return
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._set_status("Ready")
        elif status == QMediaPlayer.MediaStatus.BufferingMedia:
            self._set_status("Buffering audio...")
        elif status == QMediaPlayer.MediaStatus.BufferedMedia:
            self._set_status("Ready")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._duration_ms > 0:
                self._position_slider.setValue(self._duration_ms)
                self._current_time_label.setText(_format_media_time(self._duration_ms))
                self._overview.set_playback_ratio(1.0)
            self._set_status("Playback finished.")
        buffered = 0.0
        try:
            buffered = float(self._player.bufferProgress())
        except Exception:
            buffered = 0.0
        self._overview.set_buffered_ratio(buffered)
        self._refresh_meta_label()

    def _on_player_error(self, *_args) -> None:
        message = str(self._player.errorString() or "").strip()
        self._set_error_state(message or "Playback failed for this audio file.")

    def _on_overview_ready(self, levels: list) -> None:
        normalized = [float(value) for value in levels]
        if normalized:
            self._overview.clear_error_state()
            self._overview.set_levels(normalized)

    def _on_overview_failed(self, message: str) -> None:
        if self._overview.has_levels():
            return
        self._overview.set_error_state(str(message or "Audio overview is unavailable."))

    def _on_format_detected(self, payload: dict) -> None:
        if isinstance(payload, dict):
            self._audio_meta.update(payload)
            self._refresh_meta_label()

    def _refresh_play_button(self, *_args) -> None:
        state = self._player.playbackState()
        disabled = app_palette_color_hex(
            group=QPalette.ColorGroup.Disabled,
        )
        if state == QMediaPlayer.PlaybackState.PlayingState:
            play_icon = asset_icon(PAUSE_ICON_NAME, foreground="#d8a43a" if self._play_button.isEnabled() else disabled)
            self._play_button.setIcon(play_icon)
            self._play_button.setToolTip("Pause")
        else:
            play_icon = asset_icon(PLAY_ICON_NAME, foreground="#2fbf71" if self._play_button.isEnabled() else disabled)
            self._play_button.setIcon(play_icon)
            self._play_button.setToolTip("Play")
        stop_icon = asset_icon(STOP_ICON_NAME, foreground="#d84f57" if self._stop_button.isEnabled() else disabled)
        self._stop_button.setIcon(stop_icon)
        self._stop_button.setToolTip("Stop")

    def _reset_transport(self) -> None:
        self._duration_ms = 0
        self._pending_seek_ratio = None
        self._position_slider.blockSignals(True)
        self._position_slider.setRange(0, 0)
        self._position_slider.setValue(0)
        self._position_slider.blockSignals(False)
        self._current_time_label.setText("0:00")
        self._total_time_label.setText("--:--")
        self._overview.set_playback_ratio(0.0)
        self._overview.set_buffered_ratio(0.0)
        self._overview_loader.cancel()
        self._refresh_meta_label()

    def _ensure_audio_output(self) -> None:
        if self._audio_output is not None:
            return
        output = QAudioOutput(self)
        output.setVolume(max(0.0, min(1.0, self._volume_slider.value() / 100.0)))
        self._audio_output = output
        self._player.setAudioOutput(output)

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = bool(enabled)
        self._play_button.setEnabled(state)
        self._stop_button.setEnabled(state)
        self._position_slider.setEnabled(state)
        self._volume_slider.setEnabled(state)
        self._refresh_play_button()

    def _set_status(self, text: str) -> None:
        self._status_label.setText(str(text or "").strip() or "Ready")

    def _set_error_state(self, text: str) -> None:
        message = str(text or "").strip() or "This audio file could not be opened."
        self._set_controls_enabled(False)
        self._overview.set_error_state(message)
        self._set_status(message)
        self._refresh_meta_label()

    def _refresh_meta_label(self) -> None:
        parts: list[str] = []
        path = str(self.file_path or "").strip()
        if path:
            suffix = str(Path(path).suffix or "").strip().lstrip(".").upper()
            if suffix:
                parts.append(suffix)
            guessed_mime, _encoding = mimetypes.guess_type(path)
            if guessed_mime and guessed_mime.startswith("audio/"):
                subtype = guessed_mime.split("/", 1)[1].strip().upper()
                if subtype.startswith("X-"):
                    subtype = subtype[2:]
                if subtype and subtype != suffix:
                    parts.append(subtype)
            try:
                parts.append(_format_file_size(os.path.getsize(path)))
            except Exception:
                pass

        meta = self._player.metaData()
        bitrate_text = ""
        try:
            raw_bitrate = meta.value(meta.Key.AudioBitRate)
            if raw_bitrate is not None:
                bitrate_text = _format_bitrate(int(raw_bitrate))
        except Exception:
            bitrate_text = ""
        if bitrate_text:
            parts.append(bitrate_text)

        sample_rate = int(self._audio_meta.get("sample_rate") or 0)
        if sample_rate > 0:
            parts.append(f"{sample_rate} Hz")

        channels = int(self._audio_meta.get("channels") or 0)
        if channels == 1:
            parts.append("mono")
        elif channels == 2:
            parts.append("stereo")
        elif channels > 2:
            parts.append(f"{channels} ch")

        sample_format = str(self._audio_meta.get("sample_format") or "").strip()
        if sample_format:
            parts.append(sample_format)

        if self._duration_ms > 0:
            parts.append(_format_media_time(self._duration_ms))

        deduped: list[str] = []
        seen: set[str] = set()
        for part in parts:
            clean = str(part or "").strip()
            key = clean.casefold()
            if not clean or key in seen:
                continue
            seen.add(key)
            deduped.append(clean)

        self._meta_label.setText(" | ".join(deduped) if deduped else "Audio asset")
