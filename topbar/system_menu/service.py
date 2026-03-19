from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass

from .commands import launch_gnome_settings_panel, run_command, start_detached


@dataclass
class WifiNetwork:
    in_use: bool
    ssid: str
    security: str
    signal: int
    bars: str


@dataclass(frozen=True)
class ConnectivitySnapshot:
    current_ssid: str | None
    visible_networks: tuple[WifiNetwork, ...]
    bluetooth_adapter_present: bool
    bluetooth_powered: bool | None


@dataclass(frozen=True)
class SoundSnapshot:
    available: bool
    volume_percent: int | None
    is_muted: bool | None
    streams: tuple["AudioStreamInfo", ...] = ()


@dataclass(frozen=True)
class AudioStreamInfo:
    stream_id: int
    app_name: str
    title: str
    icon_name: str
    volume_percent: int | None
    is_muted: bool | None


@dataclass(frozen=True)
class MediaSnapshot:
    playerctl_missing: bool
    gdbus_missing: bool
    players: tuple["PlayerInfo", ...]


@dataclass(frozen=True)
class SystemMenuSnapshot:
    connectivity: ConnectivitySnapshot
    sound: SoundSnapshot
    media: MediaSnapshot


class WifiService:
    def current_ssid(self) -> str | None:
        code, out, _ = run_command(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"])
        if code != 0:
            return None
        for line in out.splitlines():
            if line.startswith("yes:"):
                return line.split(":", 1)[1].strip() or None
        return None

    def visible_networks(self) -> list[WifiNetwork]:
        code, out, _ = run_command(["nmcli", "-t", "-f", "IN-USE,SSID,SECURITY,SIGNAL,BARS", "dev", "wifi", "list"])
        if code != 0:
            return []

        networks: list[WifiNetwork] = []
        seen: set[tuple[str, str]] = set()
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) < 5:
                continue

            in_use_raw, ssid, security, signal_raw, bars = parts[:5]
            ssid = ssid.strip()
            security = security.strip()
            bars = bars.strip()
            if not ssid:
                continue

            try:
                signal = int(signal_raw)
            except ValueError:
                signal = 0

            key = (ssid, security)
            if key in seen:
                continue
            seen.add(key)
            networks.append(
                WifiNetwork(
                    in_use=(in_use_raw.strip() == "*"),
                    ssid=ssid,
                    security=security,
                    signal=signal,
                    bars=bars,
                )
            )

        networks.sort(key=lambda net: (not net.in_use, -net.signal, net.ssid.lower()))
        return networks

    def open_settings(self) -> bool:
        return launch_gnome_settings_panel("wifi")


class BluetoothService:
    def _show_output(self) -> tuple[int, str]:
        code, out, _ = run_command(["bluetoothctl", "show"])
        return code, out

    def adapter_present(self) -> bool:
        code, out = self._show_output()
        return code == 0 and bool(out.strip())

    def powered(self) -> bool | None:
        code, out = self._show_output()
        if code != 0:
            return None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Powered:"):
                return line.split(":", 1)[1].strip().lower() == "yes"
        return None

    def set_powered(self, enabled: bool) -> bool:
        code, _, _ = run_command(["bluetoothctl", "power", "on" if enabled else "off"], timeout=5.0)
        return code == 0

    def open_settings(self) -> bool:
        return launch_gnome_settings_panel("bluetooth")


class VolumeService:
    def __init__(self) -> None:
        self._wpctl = shutil.which("wpctl")
        self._pactl = shutil.which("pactl")
        self._amixer = shutil.which("amixer")

    def available(self) -> bool:
        return any((self._wpctl, self._pactl, self._amixer))

    def has_pactl(self) -> bool:
        return bool(self._pactl)

    def pactl_path(self) -> str:
        return str(self._pactl or "")

    @staticmethod
    def _parse_first_percent(text: str) -> int | None:
        match = re.search(r"(\d+)%", text or "")
        if not match:
            return None
        return max(0, min(100, int(match.group(1))))

    @staticmethod
    def _split_pactl_sections(text: str, header_prefix: str) -> list[list[str]]:
        sections: list[list[str]] = []
        current: list[str] = []
        for raw_line in (text or "").splitlines():
            line = raw_line.rstrip()
            if line.startswith(header_prefix):
                if current:
                    sections.append(current)
                current = [line]
                continue
            if current:
                current.append(line)
        if current:
            sections.append(current)
        return sections

    @staticmethod
    def _unquote_pactl_value(value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            return value[1:-1]
        return value

    @classmethod
    def _extract_percent_values_from_json(cls, value) -> list[int]:
        values: list[int] = []
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"value_percent", "volume_percent"}:
                    percent = cls._parse_first_percent(str(child))
                    if percent is not None:
                        values.append(percent)
                values.extend(cls._extract_percent_values_from_json(child))
            return values
        if isinstance(value, (list, tuple)):
            for child in value:
                values.extend(cls._extract_percent_values_from_json(child))
            return values
        percent = cls._parse_first_percent(str(value))
        if percent is not None:
            values.append(percent)
        return values

    @classmethod
    def _volume_percent_from_json(cls, value) -> int | None:
        percents = cls._extract_percent_values_from_json(value)
        if not percents:
            return None
        return max(0, min(100, round(sum(percents) / len(percents))))

    @staticmethod
    def _bool_from_json(value) -> bool | None:
        if isinstance(value, bool):
            return value
        lowered = str(value or "").strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
        return None

    def default_sink_name(self) -> str | None:
        if not self._pactl:
            return None
        code, out, _ = run_command([self._pactl, "get-default-sink"])
        if code != 0:
            return None
        return out.strip() or None

    def _default_sink_state_from_pactl(self) -> tuple[int | None, bool | None]:
        if not self._pactl:
            return None, None

        default_name = self.default_sink_name()
        if not default_name:
            return None, None

        code, out, _ = run_command([self._pactl, "--format=json", "list", "sinks"], timeout=3.0)
        if code == 0 and out:
            try:
                payload = json.loads(out)
            except Exception:
                payload = None
            if isinstance(payload, list):
                for item in payload:
                    if str(item.get("name") or "").strip() != default_name:
                        continue
                    percent = self._volume_percent_from_json(item.get("volume"))
                    muted = self._bool_from_json(item.get("mute"))
                    return percent, muted

        code, out, _ = run_command([self._pactl, "list", "sinks"], timeout=3.0)
        if code != 0:
            return None, None

        for section in self._split_pactl_sections(out, "Sink #"):
            section_name = next(
                (line.split(":", 1)[1].strip() for line in section if line.strip().startswith("Name:")),
                "",
            )
            if section_name != default_name:
                continue

            percent: int | None = None
            muted: bool | None = None
            for line in section:
                stripped = line.strip()
                if stripped.startswith("Volume:") and percent is None:
                    percent = self._parse_first_percent(stripped)
                elif stripped.startswith("Mute:"):
                    muted = stripped.split(":", 1)[1].strip().lower() == "yes"
            return percent, muted

        return None, None

    def volume_percent(self) -> int | None:
        percent, _muted = self._default_sink_state_from_pactl()
        if percent is not None:
            return percent

        if self._wpctl:
            code, out, _ = run_command([self._wpctl, "get-volume", "@DEFAULT_AUDIO_SINK@"])
            if code == 0:
                match = re.search(r"Volume:\s*([0-9]*\.?[0-9]+)", out)
                if match:
                    return max(0, min(100, round(float(match.group(1)) * 100.0)))

        if self._pactl:
            code, out, _ = run_command([self._pactl, "get-sink-volume", "@DEFAULT_SINK@"])
            if code == 0:
                match = re.search(r"(\d+)%", out)
                if match:
                    return max(0, min(100, int(match.group(1))))

        if self._amixer:
            code, out, _ = run_command([self._amixer, "get", "Master"])
            if code == 0:
                match = re.search(r"\[(\d+)%\]", out)
                if match:
                    return max(0, min(100, int(match.group(1))))

        return None

    def is_muted(self) -> bool | None:
        _percent, muted = self._default_sink_state_from_pactl()
        if muted is not None:
            return muted

        if self._wpctl:
            code, out, _ = run_command([self._wpctl, "get-volume", "@DEFAULT_AUDIO_SINK@"])
            if code == 0:
                return "[MUTED]" in out

        if self._pactl:
            code, out, _ = run_command([self._pactl, "get-sink-mute", "@DEFAULT_SINK@"])
            if code == 0:
                return "yes" in out.lower()

        if self._amixer:
            code, out, _ = run_command([self._amixer, "get", "Master"])
            if code == 0:
                return "[off]" in out.lower()

        return None

    def set_volume_percent(self, percent: int) -> bool:
        clamped = max(0, min(100, int(percent)))

        if self._wpctl:
            code, _, _ = run_command([self._wpctl, "set-volume", "@DEFAULT_AUDIO_SINK@", f"{clamped}%"])
            return code == 0

        if self._pactl:
            code, _, _ = run_command([self._pactl, "set-sink-volume", "@DEFAULT_SINK@", f"{clamped}%"])
            return code == 0

        if self._amixer:
            code, _, _ = run_command([self._amixer, "set", "Master", f"{clamped}%"])
            return code == 0

        return False

    def toggle_mute(self) -> bool:
        if self._wpctl:
            code, _, _ = run_command([self._wpctl, "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
            return code == 0

        if self._pactl:
            code, _, _ = run_command([self._pactl, "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
            return code == 0

        if self._amixer:
            code, _, _ = run_command([self._amixer, "set", "Master", "toggle"])
            return code == 0

        return False

    def application_streams(self) -> list[AudioStreamInfo]:
        if not self._pactl:
            return []

        code, out, _ = run_command([self._pactl, "list", "sink-inputs"], timeout=3.0)
        if code == 0 and out:
            streams: list[AudioStreamInfo] = []
            for section in self._split_pactl_sections(out, "Sink Input #"):
                header = section[0].strip()
                match = re.match(r"Sink Input #(\d+)", header)
                if not match:
                    continue

                stream_id = int(match.group(1))
                muted: bool | None = None
                volume_percent: int | None = None
                props: dict[str, str] = {}

                for line in section[1:]:
                    stripped = line.strip()
                    if stripped.startswith("Mute:"):
                        muted = stripped.split(":", 1)[1].strip().lower() == "yes"
                        continue
                    if stripped.startswith("Volume:") and volume_percent is None:
                        volume_percent = self._parse_first_percent(stripped)
                        continue
                    if "=" not in stripped:
                        continue
                    key, value = stripped.split("=", 1)
                    props[key.strip()] = self._unquote_pactl_value(value.strip())

                app_name = (
                    props.get("application.name")
                    or props.get("media.name")
                    or props.get("application.process.binary")
                    or f"Stream {stream_id}"
                )
                title = props.get("media.title") or props.get("media.name") or ""
                if title == app_name:
                    title = ""
                icon_name = props.get("application.icon_name") or props.get("media.icon_name") or ""

                streams.append(
                    AudioStreamInfo(
                        stream_id=stream_id,
                        app_name=app_name.strip(),
                        title=title.strip(),
                        icon_name=icon_name.strip(),
                        volume_percent=volume_percent,
                        is_muted=muted,
                    )
                )

            streams.sort(key=lambda item: (item.app_name.lower(), item.title.lower(), item.stream_id))
            return streams

        code, out, _ = run_command([self._pactl, "--format=json", "list", "sink-inputs"], timeout=3.0)
        if code != 0 or not out:
            return []

        try:
            payload = json.loads(out)
        except Exception:
            return []

        if not isinstance(payload, list):
            return []

        streams: list[AudioStreamInfo] = []
        for item in payload:
            stream_id = int(item.get("index", -1))
            if stream_id < 0:
                continue
            props = item.get("properties")
            if not isinstance(props, dict):
                props = {}

            app_name = (
                str(props.get("application.name") or "")
                or str(props.get("media.name") or "")
                or str(props.get("application.process.binary") or "")
                or f"Stream {stream_id}"
            )
            title = str(props.get("media.title") or props.get("media.name") or "").strip()
            if title == app_name:
                title = ""
            icon_name = str(props.get("application.icon_name") or props.get("media.icon_name") or "").strip()
            streams.append(
                AudioStreamInfo(
                    stream_id=stream_id,
                    app_name=app_name.strip(),
                    title=title,
                    icon_name=icon_name,
                    volume_percent=self._volume_percent_from_json(item.get("volume")),
                    is_muted=self._bool_from_json(item.get("mute")),
                )
            )

        streams.sort(key=lambda item: (item.app_name.lower(), item.title.lower(), item.stream_id))
        return streams

    def set_stream_volume_percent(self, stream_id: int, percent: int) -> bool:
        if not self._pactl or stream_id < 0:
            return False
        clamped = max(0, min(100, int(percent)))
        code, _, _ = run_command([self._pactl, "set-sink-input-volume", str(stream_id), f"{clamped}%"])
        return code == 0

    def toggle_stream_mute(self, stream_id: int) -> bool:
        if not self._pactl or stream_id < 0:
            return False
        code, _, _ = run_command([self._pactl, "set-sink-input-mute", str(stream_id), "toggle"])
        return code == 0

    def sound_snapshot(self) -> SoundSnapshot:
        return SoundSnapshot(
            available=self.available(),
            volume_percent=self.volume_percent(),
            is_muted=self.is_muted(),
            streams=tuple(self.application_streams()),
        )

    def open_settings(self) -> bool:
        for cmd in (["pavucontrol"], ["gnome-control-center", "sound"], ["gnome-control-center"]):
            if start_detached(cmd):
                return True
        return False


@dataclass
class PlayerInfo:
    name: str
    identity: str = ""
    status: str = "Unknown"
    title: str = ""
    artist: str = ""
    album: str = ""
    volume: float | None = None

    can_play: bool = False
    can_pause: bool = False
    can_go_next: bool = False
    can_go_previous: bool = False
    can_control: bool = False
    can_seek: bool = False

    position_seconds: float | None = None
    length_seconds: float | None = None
    track_id: str = ""

    loop_status: str = "None"
    shuffle: bool | None = None


class MprisService:
    def __init__(self) -> None:
        self._playerctl = shutil.which("playerctl")
        self._gdbus = shutil.which("gdbus")
        self.playerctl_missing = self._playerctl is None
        self.gdbus_missing = self._gdbus is None

    @staticmethod
    def bus_name(player_name: str) -> str:
        return f"org.mpris.MediaPlayer2.{player_name}"

    def list_players(self) -> list[str]:
        if not self._playerctl:
            self.playerctl_missing = True
            return []

        code, out, _ = run_command([self._playerctl, "-l"])
        if code != 0:
            return []

        players: list[str] = []
        seen: set[str] = set()
        for line in out.splitlines():
            player = line.strip()
            if not player or player in seen:
                continue
            seen.add(player)
            players.append(player)
        return players

    def _playerctl_command(self, player: str, *args: str) -> tuple[int, str, str]:
        if not self._playerctl:
            return 127, "", "playerctl not found"
        return run_command([self._playerctl, "-p", player, *args])

    def _gdbus_get(self, player: str, interface: str, prop: str) -> str:
        if not self._gdbus:
            self.gdbus_missing = True
            return ""
        code, out, _ = run_command(
            [
                self._gdbus,
                "call",
                "--session",
                "--dest",
                self.bus_name(player),
                "--object-path",
                "/org/mpris/MediaPlayer2",
                "--method",
                "org.freedesktop.DBus.Properties.Get",
                interface,
                prop,
            ]
        )
        return out if code == 0 else ""

    @staticmethod
    def _parse_gdbus_bool(output: str) -> bool | None:
        lowered = str(output or "").strip().lower()
        if not lowered:
            return None
        if re.search(r"\btrue\b", lowered):
            return True
        if re.search(r"\bfalse\b", lowered):
            return False
        return None

    def get_capabilities(self, player: str) -> dict[str, bool]:
        if not self._gdbus:
            return {
                "can_play": False,
                "can_pause": False,
                "can_go_next": False,
                "can_go_previous": False,
                "can_control": False,
                "can_seek": False,
            }

        props = {
            "can_play": "CanPlay",
            "can_pause": "CanPause",
            "can_go_next": "CanGoNext",
            "can_go_previous": "CanGoPrevious",
            "can_control": "CanControl",
            "can_seek": "CanSeek",
        }
        capabilities: dict[str, bool] = {}
        for key, prop in props.items():
            out = self._gdbus_get(player, "org.mpris.MediaPlayer2.Player", prop)
            capabilities[key] = bool(self._parse_gdbus_bool(out))
        return capabilities

    def get_identity(self, player: str) -> str:
        out = self._gdbus_get(player, "org.mpris.MediaPlayer2", "Identity")
        if "'" in out:
            parts = out.split("'")
            if len(parts) >= 2:
                return parts[1]
        return player

    def get_position_seconds(self, player: str) -> float | None:
        code, out, _ = self._playerctl_command(player, "position")
        if code != 0 or not out.strip():
            return None
        try:
            return float(out.strip())
        except ValueError:
            return None

    def get_metadata_value(self, player: str, key: str) -> str:
        code, out, _ = self._playerctl_command(player, "metadata", key)
        if code != 0:
            return ""
        return out.strip()

    def get_track_length_seconds(self, player: str) -> float | None:
        raw = self.get_metadata_value(player, "mpris:length")
        if not raw:
            return None
        try:
            return max(0.0, float(raw) / 1_000_000.0)
        except ValueError:
            return None

    def get_track_id(self, player: str) -> str:
        return self.get_metadata_value(player, "mpris:trackid")

    def get_loop_status(self, player: str) -> str:
        code, out, _ = self._playerctl_command(player, "loop")
        if code != 0:
            return "None"
        value = out.strip()
        return value if value in {"None", "Track", "Playlist"} else "None"

    def get_shuffle(self, player: str) -> bool | None:
        code, out, _ = self._playerctl_command(player, "shuffle")
        if code != 0:
            return None
        value = out.strip().lower()
        if value == "on":
            return True
        if value == "off":
            return False
        return None

    def get_player(self, player: str) -> PlayerInfo:
        info = PlayerInfo(name=player)
        info.identity = self.get_identity(player)

        code, out, _ = self._playerctl_command(player, "status")
        if code == 0 and out:
            info.status = out.strip()

        metadata_format = json.dumps(
            {
                "title": "{{title}}",
                "artist": "{{artist}}",
                "album": "{{album}}",
            }
        )
        code, out, _ = self._playerctl_command(player, "metadata", "--format", metadata_format)
        if code == 0 and out:
            try:
                metadata = json.loads(out)
            except Exception:
                metadata = {}
            info.title = str(metadata.get("title", "") or "")
            info.artist = str(metadata.get("artist", "") or "")
            info.album = str(metadata.get("album", "") or "")

        code, out, _ = self._playerctl_command(player, "volume")
        if code == 0 and out:
            try:
                info.volume = float(out.strip())
            except ValueError:
                info.volume = None

        for key, value in self.get_capabilities(player).items():
            setattr(info, key, value)

        info.position_seconds = self.get_position_seconds(player)
        info.length_seconds = self.get_track_length_seconds(player)
        info.track_id = self.get_track_id(player)
        info.loop_status = self.get_loop_status(player)
        info.shuffle = self.get_shuffle(player)

        return info

    def command(self, player: str, *args: str) -> bool:
        code, _, _ = self._playerctl_command(player, *args)
        return code == 0

    def set_volume(self, player: str, value: float) -> bool:
        clamped = max(0.0, min(1.0, value))
        return self.command(player, "volume", f"{clamped:.3f}")

    def stop(self, player: str) -> bool:
        return self.command(player, "stop")

    def seek_relative(self, player: str, delta_seconds: float) -> bool:
        if not self._playerctl:
            return False

        sign = "+" if delta_seconds >= 0 else "-"
        magnitude = abs(float(delta_seconds))
        code, _, _ = self._playerctl_command(player, "position", f"{magnitude:.3f}{sign}")
        return code == 0

    def set_position(self, player: str, position_seconds: float) -> bool:
        if not self._playerctl:
            return False

        clamped = max(0.0, float(position_seconds))
        code, _, _ = self._playerctl_command(player, "position", f"{clamped:.3f}")
        return code == 0

    def set_loop_status(self, player: str, value: str) -> bool:
        normalized = value.strip().capitalize()
        if normalized not in {"None", "Track", "Playlist"}:
            return False
        code, _, _ = self._playerctl_command(player, "loop", normalized)
        return code == 0

    def set_shuffle(self, player: str, enabled: bool) -> bool:
        code, _, _ = self._playerctl_command(player, "shuffle", "On" if enabled else "Off")
        return code == 0


def collect_system_menu_snapshot() -> SystemMenuSnapshot:
    wifi = WifiService()
    bluetooth = BluetoothService()
    volume = VolumeService()
    mpris = MprisService()

    visible_networks = tuple(wifi.visible_networks())
    current_ssid = next((network.ssid for network in visible_networks if network.in_use), None)
    if current_ssid is None:
        current_ssid = wifi.current_ssid()

    bluetooth_show_code, bluetooth_show_out = bluetooth._show_output()
    bluetooth_adapter_present = bluetooth_show_code == 0 and bool(bluetooth_show_out.strip())
    bluetooth_powered: bool | None = None
    if bluetooth_show_code == 0:
        for line in bluetooth_show_out.splitlines():
            line = line.strip()
            if line.startswith("Powered:"):
                bluetooth_powered = line.split(":", 1)[1].strip().lower() == "yes"
                break

    players = tuple(mpris.get_player(player_name) for player_name in mpris.list_players())

    connectivity = ConnectivitySnapshot(
        current_ssid=current_ssid,
        visible_networks=visible_networks,
        bluetooth_adapter_present=bluetooth_adapter_present,
        bluetooth_powered=bluetooth_powered,
    )
    sound = volume.sound_snapshot()
    media = MediaSnapshot(
        playerctl_missing=mpris.playerctl_missing,
        gdbus_missing=mpris.gdbus_missing,
        players=players,
    )
    return SystemMenuSnapshot(connectivity=connectivity, sound=sound, media=media)
