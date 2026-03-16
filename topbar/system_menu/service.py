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

    def volume_percent(self) -> int | None:
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
    can_play: bool = True
    can_pause: bool = True
    can_go_next: bool = True
    can_go_previous: bool = True
    can_control: bool = True


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

    def get_capabilities(self, player: str) -> dict[str, bool]:
        if not self._gdbus:
            return {
                "can_play": True,
                "can_pause": True,
                "can_go_next": True,
                "can_go_previous": True,
                "can_control": True,
            }

        props = {
            "can_play": "CanPlay",
            "can_pause": "CanPause",
            "can_go_next": "CanGoNext",
            "can_go_previous": "CanGoPrevious",
            "can_control": "CanControl",
        }
        capabilities: dict[str, bool] = {}
        for key, prop in props.items():
            out = self._gdbus_get(player, "org.mpris.MediaPlayer2.Player", prop)
            capabilities[key] = "<true>" in out.lower()
        return capabilities

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
        return info

    def command(self, player: str, *args: str) -> bool:
        code, _, _ = self._playerctl_command(player, *args)
        return code == 0

    def set_volume(self, player: str, value: float) -> bool:
        clamped = max(0.0, min(1.0, value))
        return self.command(player, "volume", f"{clamped:.3f}")


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
    sound = SoundSnapshot(
        available=volume.available(),
        volume_percent=volume.volume_percent(),
        is_muted=volume.is_muted(),
    )
    media = MediaSnapshot(
        playerctl_missing=mpris.playerctl_missing,
        gdbus_missing=mpris.gdbus_missing,
        players=players,
    )
    return SystemMenuSnapshot(connectivity=connectivity, sound=sound, media=media)
