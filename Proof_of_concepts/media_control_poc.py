from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, unquote

from PySide6.QtCore import QTimer, Qt, QByteArray, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

APP_TITLE = "Media + Volume Proof of Concept"


def run_command(args: list[str], timeout: float = 2.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"Command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"Timed out: {' '.join(shlex.quote(a) for a in args)}"
    except Exception as exc:
        return 1, "", str(exc)


def parse_boolish(text: str, default: bool = False) -> bool:
    value = text.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def parse_first_percent(text: str) -> int:
    import re
    m = re.search(r"(\d+)%", text)
    return int(m.group(1)) if m else 0


def clamp(n: int, low: int, high: int) -> int:
    return max(low, min(high, n))


@dataclass
class PlayerInfo:
    name: str
    identity: str = ""
    status: str = "Unknown"
    title: str = ""
    artist: str = ""
    album: str = ""
    art_url: str = ""
    position_sec: float = 0.0
    length_sec: float = 0.0
    volume: float | None = None
    shuffle: str = ""
    loop_status: str = ""
    can_play: bool = False
    can_pause: bool = False
    can_seek: bool = False
    can_go_next: bool = False
    can_go_previous: bool = False
    can_control: bool = False
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class MprisClient:
    def __init__(self) -> None:
        self.playerctl_missing = False
        self.gdbus_missing = False

    @staticmethod
    def bus_name(player_name: str) -> str:
        return f"org.mpris.MediaPlayer2.{player_name}"

    def list_players(self) -> list[str]:
        code, out, err = run_command(["playerctl", "-l"])
        if code == 127:
            self.playerctl_missing = True
            return []
        players = [line.strip() for line in out.splitlines() if line.strip()]
        seen = set()
        unique = []
        for p in players:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return unique

    def _playerctl(self, player: str, *args: str) -> tuple[int, str, str]:
        return run_command(["playerctl", "-p", player, *args])

    def _gdbus_get(self, player: str, interface: str, prop: str) -> str:
        code, out, err = run_command([
            "gdbus", "call", "--session",
            "--dest", self.bus_name(player),
            "--object-path", "/org/mpris/MediaPlayer2",
            "--method", "org.freedesktop.DBus.Properties.Get",
            interface, prop
        ])
        if code == 127:
            self.gdbus_missing = True
            return ""
        return out

    def get_capabilities(self, player: str) -> dict[str, bool]:
        props = {
            "can_play": "CanPlay",
            "can_pause": "CanPause",
            "can_seek": "CanSeek",
            "can_go_next": "CanGoNext",
            "can_go_previous": "CanGoPrevious",
            "can_control": "CanControl",
        }
        result = {}
        for key, prop in props.items():
            out = self._gdbus_get(player, "org.mpris.MediaPlayer2.Player", prop)
            result[key] = "<true>" in out.lower()
        return result

    def get_identity(self, player: str) -> str:
        out = self._gdbus_get(player, "org.mpris.MediaPlayer2", "Identity")
        if "'" in out:
            parts = out.split("'")
            if len(parts) >= 2:
                return parts[1]
        return player

    def get_player(self, player: str) -> PlayerInfo:
        info = PlayerInfo(name=player)
        info.identity = self.get_identity(player)

        code, out, err = self._playerctl(player, "status")
        if code == 0 and out:
            info.status = out.strip()

        meta_format = json.dumps({
            "title": "{{title}}",
            "artist": "{{artist}}",
            "album": "{{album}}",
            "artUrl": "{{mpris:artUrl}}",
            "lengthUs": "{{mpris:length}}",
            "trackid": "{{mpris:trackid}}",
            "url": "{{xesam:url}}",
        })
        code, out, err = self._playerctl(player, "metadata", "--format", meta_format)
        if code == 0 and out:
            try:
                data = json.loads(out)
            except Exception:
                data = {}
            info.raw_metadata = data
            info.title = str(data.get("title", "") or "")
            info.artist = str(data.get("artist", "") or "")
            info.album = str(data.get("album", "") or "")
            info.art_url = str(data.get("artUrl", "") or "")
            try:
                info.length_sec = float(data.get("lengthUs", 0) or 0) / 1_000_000.0
            except Exception:
                info.length_sec = 0.0

        code, out, err = self._playerctl(player, "position")
        if code == 0 and out:
            try:
                info.position_sec = float(out.strip())
            except ValueError:
                pass

        code, out, err = self._playerctl(player, "volume")
        if code == 0 and out:
            try:
                info.volume = float(out.strip())
            except ValueError:
                info.volume = None

        code, out, err = self._playerctl(player, "shuffle")
        if code == 0:
            info.shuffle = out.strip()

        code, out, err = self._playerctl(player, "loop")
        if code == 0:
            info.loop_status = out.strip()

        caps = self.get_capabilities(player)
        for key, value in caps.items():
            setattr(info, key, value)

        return info

    def command(self, player: str, *args: str) -> bool:
        code, out, err = self._playerctl(player, *args)
        return code == 0

    def set_volume(self, player: str, value_0_to_1: float) -> bool:
        value_0_to_1 = max(0.0, min(1.0, value_0_to_1))
        return self.command(player, "volume", f"{value_0_to_1:.3f}")

    def set_position(self, player: str, seconds: float) -> bool:
        return self.command(player, "position", str(max(0.0, seconds)))

    def seek_relative(self, player: str, seconds_delta: int) -> bool:
        sign = "+" if seconds_delta >= 0 else "-"
        return self.command(player, "position", f"{abs(seconds_delta)}{sign}")

    def set_shuffle(self, player: str, enabled: bool) -> bool:
        return self.command(player, "shuffle", "On" if enabled else "Off")

    def set_loop(self, player: str, mode: str) -> bool:
        if mode not in {"None", "Track", "Playlist"}:
            return False
        return self.command(player, "loop", mode)


class PulseClient:
    def get_default_sink_name(self) -> str:
        code, out, err = run_command(["pactl", "get-default-sink"])
        return out.strip() if code == 0 else "@DEFAULT_SINK@"

    def get_volume_percent(self) -> int:
        code, out, err = run_command(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        return parse_first_percent(out) if code == 0 else 0

    def get_mute(self) -> bool:
        code, out, err = run_command(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        return parse_boolish(out.split(":")[-1]) if code == 0 else False

    def set_volume_percent(self, value: int) -> bool:
        value = clamp(value, 0, 150)
        code, out, err = run_command(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"])
        return code == 0

    def change_volume_percent(self, delta: int) -> bool:
        sign = "+" if delta >= 0 else "-"
        code, out, err = run_command(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{abs(delta)}%"])
        return code == 0

    def set_mute(self, muted: bool) -> bool:
        code, out, err = run_command(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if muted else "0"])
        return code == 0


def fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class PlayerCard(QFrame):
    def __init__(self, mpris: MprisClient, refresh_callback) -> None:
        super().__init__()
        self.mpris = mpris
        self.refresh_callback = refresh_callback
        self.player_name = ""
        self.network = QNetworkAccessManager(self)
        self.network.finished.connect(self._on_art_downloaded)
        self._loading_art_url = ""
        self._ignore_signals = False

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("playerCard")

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        top = QHBoxLayout()
        root.addLayout(top)

        self.art_label = QLabel()
        self.art_label.setFixedSize(72, 72)
        self.art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art_label.setStyleSheet("background: rgba(255,255,255,0.06); border-radius: 8px;")
        top.addWidget(self.art_label)

        text_box = QVBoxLayout()
        top.addLayout(text_box, 1)

        self.title_label = QLabel("No title")
        self.title_label.setStyleSheet("font-weight: 700; font-size: 15px;")
        self.artist_label = QLabel("")
        self.album_label = QLabel("")
        self.identity_label = QLabel("")
        self.status_label = QLabel("")
        for w in (self.artist_label, self.album_label, self.identity_label, self.status_label):
            w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_box.addWidget(self.title_label)
        text_box.addWidget(self.artist_label)
        text_box.addWidget(self.album_label)
        text_box.addWidget(self.identity_label)
        text_box.addWidget(self.status_label)

        row1 = QHBoxLayout()
        root.addLayout(row1)

        self.prev_btn = QToolButton(text="⏮")
        self.back_btn = QToolButton(text="⏪ 10s")
        self.play_pause_btn = QToolButton(text="⏯")
        self.stop_btn = QToolButton(text="⏹")
        self.fwd_btn = QToolButton(text="10s ⏩")
        self.next_btn = QToolButton(text="⏭")
        for btn in (self.prev_btn, self.back_btn, self.play_pause_btn, self.stop_btn, self.fwd_btn, self.next_btn):
            row1.addWidget(btn)

        row2 = QGridLayout()
        root.addLayout(row2)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_label = QLabel("0:00 / 0:00")
        row2.addWidget(QLabel("Position"), 0, 0)
        row2.addWidget(self.position_slider, 0, 1)
        row2.addWidget(self.position_label, 0, 2)

        self.player_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.player_volume_slider.setRange(0, 100)
        self.player_volume_label = QLabel("--")
        row2.addWidget(QLabel("Player Volume"), 1, 0)
        row2.addWidget(self.player_volume_slider, 1, 1)
        row2.addWidget(self.player_volume_label, 1, 2)

        self.shuffle_box = QCheckBox("Shuffle")
        self.loop_combo = QComboBox()
        self.loop_combo.addItems(["None", "Track", "Playlist"])
        extras = QHBoxLayout()
        extras.addWidget(self.shuffle_box)
        extras.addWidget(QLabel("Loop"))
        extras.addWidget(self.loop_combo)
        extras.addStretch(1)
        root.addLayout(extras)

        self.prev_btn.clicked.connect(lambda: self._do("previous"))
        self.play_pause_btn.clicked.connect(lambda: self._do("play-pause"))
        self.stop_btn.clicked.connect(lambda: self._do("stop"))
        self.next_btn.clicked.connect(lambda: self._do("next"))
        self.back_btn.clicked.connect(lambda: self._seek_relative(-10))
        self.fwd_btn.clicked.connect(lambda: self._seek_relative(10))
        self.position_slider.sliderReleased.connect(self._position_released)
        self.player_volume_slider.sliderReleased.connect(self._volume_released)
        self.shuffle_box.toggled.connect(self._shuffle_changed)
        self.loop_combo.currentTextChanged.connect(self._loop_changed)

    def bind(self, info: PlayerInfo) -> None:
        self.player_name = info.name
        self._ignore_signals = True
        try:
            self.title_label.setText(info.title or "(no title)")
            self.artist_label.setText(f"Artist: {info.artist or '—'}")
            self.album_label.setText(f"Album: {info.album or '—'}")
            self.identity_label.setText(f"Player: {info.identity or info.name}  [{info.name}]")
            self.status_label.setText(f"Status: {info.status}")

            max_pos = max(0, int(info.length_sec))
            current_pos = clamp(int(info.position_sec), 0, max_pos if max_pos > 0 else max(int(info.position_sec), 0))
            self.position_slider.setRange(0, max_pos if max_pos > 0 else 0)
            if not self.position_slider.isSliderDown():
                self.position_slider.setValue(current_pos)
            self.position_slider.setEnabled(info.can_seek and max_pos > 0)
            self.position_label.setText(f"{fmt_time(info.position_sec)} / {fmt_time(info.length_sec)}")

            if info.volume is None:
                self.player_volume_slider.setEnabled(False)
                self.player_volume_label.setText("N/A")
            else:
                self.player_volume_slider.setEnabled(True)
                if not self.player_volume_slider.isSliderDown():
                    self.player_volume_slider.setValue(clamp(int(round(info.volume * 100)), 0, 100))
                self.player_volume_label.setText(f"{int(round(info.volume * 100))}%")

            self.prev_btn.setEnabled(info.can_go_previous and info.can_control)
            self.next_btn.setEnabled(info.can_go_next and info.can_control)
            self.play_pause_btn.setEnabled((info.can_play or info.can_pause) and info.can_control)
            self.stop_btn.setEnabled(info.can_control)
            self.back_btn.setEnabled(info.can_seek and info.can_control)
            self.fwd_btn.setEnabled(info.can_seek and info.can_control)

            self.shuffle_box.setChecked(info.shuffle.lower() == "on")
            self.shuffle_box.setEnabled(info.can_control)
            loop_idx = self.loop_combo.findText(info.loop_status or "None")
            self.loop_combo.setCurrentIndex(max(loop_idx, 0))
            self.loop_combo.setEnabled(info.can_control)

            self._set_art(info.art_url)
        finally:
            self._ignore_signals = False

    def _set_art(self, art_url: str) -> None:
        if not art_url:
            self.art_label.setPixmap(QPixmap())
            self.art_label.setText("♪")
            return

        if art_url.startswith("file://"):
            parsed = urlparse(art_url)
            local_path = unquote(parsed.path)
            pixmap = QPixmap(local_path)
            if not pixmap.isNull():
                self.art_label.setText("")
                self.art_label.setPixmap(pixmap.scaled(
                    self.art_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                return

        if art_url.startswith("http://") or art_url.startswith("https://"):
            if art_url == self._loading_art_url:
                return
            self._loading_art_url = art_url
            self.art_label.setText("…")
            self.network.get(QNetworkRequest(QUrl(art_url)))
            return

        self.art_label.setPixmap(QPixmap())
        self.art_label.setText("♪")

    def _on_art_downloaded(self, reply) -> None:
        data: QByteArray = reply.readAll()
        pixmap = QPixmap()
        pixmap.loadFromData(bytes(data))
        if not pixmap.isNull():
            self.art_label.setText("")
            self.art_label.setPixmap(pixmap.scaled(
                self.art_label.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            self.art_label.setPixmap(QPixmap())
            self.art_label.setText("♪")
        reply.deleteLater()
        self._loading_art_url = ""

    def _do(self, command: str) -> None:
        if not self.player_name:
            return
        self.mpris.command(self.player_name, command)
        self.refresh_callback()

    def _seek_relative(self, delta: int) -> None:
        if not self.player_name:
            return
        self.mpris.seek_relative(self.player_name, delta)
        self.refresh_callback()

    def _position_released(self) -> None:
        if self._ignore_signals or not self.player_name:
            return
        self.mpris.set_position(self.player_name, float(self.position_slider.value()))
        self.refresh_callback()

    def _volume_released(self) -> None:
        if self._ignore_signals or not self.player_name:
            return
        value = self.player_volume_slider.value() / 100.0
        self.mpris.set_volume(self.player_name, value)
        self.refresh_callback()

    def _shuffle_changed(self, checked: bool) -> None:
        if self._ignore_signals or not self.player_name:
            return
        self.mpris.set_shuffle(self.player_name, checked)
        self.refresh_callback()

    def _loop_changed(self, mode: str) -> None:
        if self._ignore_signals or not self.player_name:
            return
        self.mpris.set_loop(self.player_name, mode)
        self.refresh_callback()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1180, 780)

        self.mpris = MprisClient()
        self.pulse = PulseClient()
        self.player_cards: dict[str, PlayerCard] = {}
        self._ignore_master_signals = False

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        left = QVBoxLayout()
        root.addLayout(left, 0)

        self.refresh_btn = QPushButton("Refresh Now")
        self.refresh_btn.clicked.connect(self.refresh_all)
        left.addWidget(self.refresh_btn)

        self.player_list = QListWidget()
        self.player_list.currentTextChanged.connect(self._scroll_to_player)
        left.addWidget(QLabel("Detected Players"))
        left.addWidget(self.player_list, 1)

        all_box = QGroupBox("All Players")
        all_layout = QGridLayout(all_box)
        self.all_play_btn = QPushButton("Play")
        self.all_pause_btn = QPushButton("Pause")
        self.all_play_pause_btn = QPushButton("Play/Pause")
        self.all_stop_btn = QPushButton("Stop")
        self.all_next_btn = QPushButton("Next")
        self.all_prev_btn = QPushButton("Previous")
        all_layout.addWidget(self.all_play_btn, 0, 0)
        all_layout.addWidget(self.all_pause_btn, 0, 1)
        all_layout.addWidget(self.all_play_pause_btn, 1, 0)
        all_layout.addWidget(self.all_stop_btn, 1, 1)
        all_layout.addWidget(self.all_prev_btn, 2, 0)
        all_layout.addWidget(self.all_next_btn, 2, 1)
        left.addWidget(all_box)

        self.all_play_btn.clicked.connect(lambda: self._all_players("play"))
        self.all_pause_btn.clicked.connect(lambda: self._all_players("pause"))
        self.all_play_pause_btn.clicked.connect(lambda: self._all_players("play-pause"))
        self.all_stop_btn.clicked.connect(lambda: self._all_players("stop"))
        self.all_next_btn.clicked.connect(lambda: self._all_players("next"))
        self.all_prev_btn.clicked.connect(lambda: self._all_players("previous"))

        volume_box = QGroupBox("Master Volume")
        vf = QFormLayout(volume_box)
        self.sink_name_label = QLabel("—")
        self.master_slider = QSlider(Qt.Orientation.Horizontal)
        self.master_slider.setRange(0, 150)
        self.master_label = QLabel("0%")
        self.master_mute_box = QCheckBox("Muted")
        self.master_down_btn = QPushButton("-5%")
        self.master_up_btn = QPushButton("+5%")
        row = QHBoxLayout()
        row.addWidget(self.master_slider, 1)
        row.addWidget(self.master_label)
        buttons = QHBoxLayout()
        buttons.addWidget(self.master_down_btn)
        buttons.addWidget(self.master_up_btn)
        vf.addRow("Sink", self.sink_name_label)
        vf.addRow("Volume", row)
        vf.addRow("", self.master_mute_box)
        vf.addRow("", buttons)
        left.addWidget(volume_box)

        self.master_slider.sliderReleased.connect(self._master_slider_released)
        self.master_mute_box.toggled.connect(self._master_mute_changed)
        self.master_down_btn.clicked.connect(lambda: self._change_master(-5))
        self.master_up_btn.clicked.connect(lambda: self._change_master(5))

        self.auto_refresh_box = QCheckBox("Auto refresh")
        self.auto_refresh_box.setChecked(True)
        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(500, 10000)
        self.refresh_spin.setValue(1500)
        self.refresh_spin.setSuffix(" ms")
        refresh_row = QHBoxLayout()
        refresh_row.addWidget(self.auto_refresh_box)
        refresh_row.addWidget(self.refresh_spin)
        left.addLayout(refresh_row)
        left.addStretch(1)

        right = QVBoxLayout()
        root.addLayout(right, 1)

        self.status_banner = QLabel("")
        self.status_banner.setWordWrap(True)
        right.addWidget(self.status_banner)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        right.addWidget(self.scroll, 1)

        self.cards_host = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch(1)
        self.scroll.setWidget(self.cards_host)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._timer_tick)
        self.auto_refresh_box.toggled.connect(self._sync_timer)
        self.refresh_spin.valueChanged.connect(self._sync_timer)
        self._sync_timer()

        self.refresh_all()

    def _sync_timer(self) -> None:
        self.timer.stop()
        if self.auto_refresh_box.isChecked():
            self.timer.start(self.refresh_spin.value())

    def _timer_tick(self) -> None:
        self.refresh_all()

    def _set_banner(self, text: str) -> None:
        self.status_banner.setText(text)

    def _change_master(self, delta: int) -> None:
        self.pulse.change_volume_percent(delta)
        self.refresh_master_volume()

    def _master_slider_released(self) -> None:
        if self._ignore_master_signals:
            return
        self.pulse.set_volume_percent(self.master_slider.value())
        self.refresh_master_volume()

    def _master_mute_changed(self, checked: bool) -> None:
        if self._ignore_master_signals:
            return
        self.pulse.set_mute(checked)
        self.refresh_master_volume()

    def refresh_master_volume(self) -> None:
        self._ignore_master_signals = True
        try:
            sink_name = self.pulse.get_default_sink_name()
            volume = self.pulse.get_volume_percent()
            muted = self.pulse.get_mute()
            self.sink_name_label.setText(sink_name or "@DEFAULT_SINK@")
            self.master_slider.setValue(volume)
            self.master_label.setText(f"{volume}%")
            self.master_mute_box.setChecked(muted)
        finally:
            self._ignore_master_signals = False

    def _all_players(self, command: str) -> None:
        players = self.mpris.list_players()
        for player in players:
            self.mpris.command(player, command)
        self.refresh_all()

    def _scroll_to_player(self, name: str) -> None:
        if not name or name not in self.player_cards:
            return
        widget = self.player_cards[name]
        self.scroll.ensureWidgetVisible(widget, 0, 50)

    def _rebuild_player_list(self, players: list[str]) -> None:
        current = self.player_list.currentItem().text() if self.player_list.currentItem() else ""
        self.player_list.clear()
        for p in players:
            QListWidgetItem(p, self.player_list)
        if current:
            matches = self.player_list.findItems(current, Qt.MatchFlag.MatchExactly)
            if matches:
                self.player_list.setCurrentItem(matches[0])
        elif players:
            self.player_list.setCurrentRow(0)

    def _remove_missing_cards(self, players: set[str]) -> None:
        to_remove = [name for name in self.player_cards if name not in players]
        for name in to_remove:
            card = self.player_cards.pop(name)
            self.cards_layout.removeWidget(card)
            card.deleteLater()

    def _ensure_card(self, name: str) -> PlayerCard:
        if name in self.player_cards:
            return self.player_cards[name]
        card = PlayerCard(self.mpris, self.refresh_all)
        self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.player_cards[name] = card
        return card

    def refresh_players(self) -> None:
        players = self.mpris.list_players()
        self._rebuild_player_list(players)
        self._remove_missing_cards(set(players))

        banner_parts = []
        if self.mpris.playerctl_missing:
            banner_parts.append("playerctl not found")
        if self.mpris.gdbus_missing:
            banner_parts.append("gdbus not found")

        if not players:
            self._set_banner(
                "No MPRIS players detected. Start something like VLC, Spotify, mpv, Firefox video, or Chromium media."
                + (" Missing tools: " + ", ".join(banner_parts) if banner_parts else "")
            )
            return

        infos = []
        for player in players:
            infos.append(self.mpris.get_player(player))

        active_bits = [f"{i.identity or i.name}: {i.status}" for i in infos]
        banner = " | ".join(active_bits)
        if banner_parts:
            banner += " | Missing tools: " + ", ".join(banner_parts)
        self._set_banner(banner)

        for info in infos:
            card = self._ensure_card(info.name)
            card.bind(info)

    def refresh_all(self) -> None:
        self.refresh_master_volume()
        self.refresh_players()


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())