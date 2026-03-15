from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


# ----------------------------
# helpers
# ----------------------------

def run_command(cmd: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def start_detached(cmd: list[str]) -> bool:
    try:
        subprocess.Popen(cmd)
        return True
    except Exception:
        return False


def launch_gnome_settings_panel(panel: str) -> bool:
    """
    Tries GNOME Control Center first with a specific panel,
    then falls back to the generic settings window.
    """
    candidates = [
        ["gnome-control-center", panel],
        ["gnome-control-center"],
    ]
    for cmd in candidates:
        if start_detached(cmd):
            return True
    return False


# ----------------------------
# wifi
# ----------------------------

@dataclass
class WifiNetwork:
    in_use: bool
    ssid: str
    security: str
    signal: int
    bars: str


class WifiManager:
    def current_ssid(self) -> str | None:
        code, out, _ = run_command(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"]
        )
        if code != 0:
            return None

        for line in out.splitlines():
            # format: yes:MyWifi
            if line.startswith("yes:"):
                return line.split(":", 1)[1].strip() or None
        return None

    def visible_networks(self) -> list[WifiNetwork]:
        code, out, _ = run_command(
            ["nmcli", "-t", "-f", "IN-USE,SSID,SECURITY,SIGNAL,BARS", "dev", "wifi", "list"]
        )
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

        networks.sort(key=lambda n: (not n.in_use, -n.signal, n.ssid.lower()))
        return networks

    def open_settings(self) -> bool:
        return launch_gnome_settings_panel("wifi")


# ----------------------------
# bluetooth
# ----------------------------

class BluetoothManager:
    def adapter_present(self) -> bool:
        code, out, _ = run_command(["bluetoothctl", "show"])
        return code == 0 and bool(out.strip())

    def powered(self) -> bool | None:
        code, out, _ = run_command(["bluetoothctl", "show"])
        if code != 0:
            return None

        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Powered:"):
                value = line.split(":", 1)[1].strip().lower()
                return value == "yes"
        return None

    def set_powered(self, enabled: bool) -> bool:
        cmd = ["bluetoothctl", "power", "on" if enabled else "off"]
        code, _, _ = run_command(cmd)
        return code == 0

    def open_settings(self) -> bool:
        return launch_gnome_settings_panel("bluetooth")


# ----------------------------
# dialog
# ----------------------------

class ConnectivityDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Connectivity")
        self.resize(520, 260)

        self.wifi = WifiManager()
        self.bt = BluetoothManager()

        self._build_ui()
        self.refresh_all()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        title = QLabel("Connectivity")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(title)

        subtitle = QLabel("Wi-Fi and Bluetooth quick controls")
        subtitle.setStyleSheet("color: palette(mid);")
        root.addWidget(subtitle)

        root.addSpacing(4)

        # Wi-Fi row
        wifi_row = QHBoxLayout()
        wifi_row.setSpacing(10)

        self.wifi_status = QLabel("Wi-Fi: …")
        self.wifi_status.setMinimumWidth(240)

        self.wifi_button = QToolButton()
        self.wifi_button.setText("Wi-Fi")
        self.wifi_button.setPopupMode(QToolButton.InstantPopup)
        self.wifi_menu = QMenu(self)
        self.wifi_button.setMenu(self.wifi_menu)
        self.wifi_menu.aboutToShow.connect(self.populate_wifi_menu)

        wifi_refresh = QPushButton("Refresh")
        wifi_refresh.clicked.connect(self.refresh_wifi)

        wifi_row.addWidget(self.wifi_status, 1)
        wifi_row.addWidget(self.wifi_button)
        wifi_row.addWidget(wifi_refresh)
        root.addLayout(wifi_row)

        # Bluetooth row
        bt_row = QHBoxLayout()
        bt_row.setSpacing(10)

        self.bt_status = QLabel("Bluetooth: …")
        self.bt_status.setMinimumWidth(240)

        self.bt_button = QToolButton()
        self.bt_button.setText("Bluetooth")
        self.bt_button.setPopupMode(QToolButton.InstantPopup)
        self.bt_menu = QMenu(self)
        self.bt_button.setMenu(self.bt_menu)
        self.bt_menu.aboutToShow.connect(self.populate_bt_menu)

        bt_refresh = QPushButton("Refresh")
        bt_refresh.clicked.connect(self.refresh_bluetooth)

        bt_row.addWidget(self.bt_status, 1)
        bt_row.addWidget(self.bt_button)
        bt_row.addWidget(bt_refresh)
        root.addLayout(bt_row)

        root.addStretch(1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)

        root.addLayout(bottom)

    # ----------------------------
    # refresh
    # ----------------------------

    def refresh_all(self) -> None:
        self.refresh_wifi()
        self.refresh_bluetooth()

    def refresh_wifi(self) -> None:
        ssid = self.wifi.current_ssid()
        if ssid:
            self.wifi_status.setText(f"Wi-Fi: connected to {ssid}")
        else:
            self.wifi_status.setText("Wi-Fi: not connected")

    def refresh_bluetooth(self) -> None:
        if not self.bt.adapter_present():
            self.bt_status.setText("Bluetooth: no adapter found")
            return

        powered = self.bt.powered()
        if powered is True:
            self.bt_status.setText("Bluetooth: on")
        elif powered is False:
            self.bt_status.setText("Bluetooth: off")
        else:
            self.bt_status.setText("Bluetooth: unknown")

    # ----------------------------
    # menus
    # ----------------------------

    def populate_wifi_menu(self) -> None:
        self.wifi_menu.clear()

        current = self.wifi.current_ssid()
        title_action = QAction(
            f"Connected: {current}" if current else "Connected: none",
            self
        )
        title_action.setEnabled(False)
        self.wifi_menu.addAction(title_action)
        self.wifi_menu.addSeparator()

        networks = self.wifi.visible_networks()
        if networks:
            for net in networks[:20]:
                label = self._format_wifi_label(net)
                act = QAction(label, self)
                # You said selecting the network is the OS thing,
                # so every network entry opens system Wi-Fi settings.
                act.triggered.connect(lambda checked=False, ssid=net.ssid: self.open_wifi_settings_for(ssid))
                self.wifi_menu.addAction(act)
        else:
            no_networks = QAction("No networks found", self)
            no_networks.setEnabled(False)
            self.wifi_menu.addAction(no_networks)

        self.wifi_menu.addSeparator()

        refresh_act = QAction("Refresh Wi-Fi", self)
        refresh_act.triggered.connect(self.refresh_wifi)
        self.wifi_menu.addAction(refresh_act)

        settings_act = QAction("Open Network Settings", self)
        settings_act.triggered.connect(self.open_network_settings)
        self.wifi_menu.addAction(settings_act)

    def populate_bt_menu(self) -> None:
        self.bt_menu.clear()

        if not self.bt.adapter_present():
            missing = QAction("No Bluetooth adapter found", self)
            missing.setEnabled(False)
            self.bt_menu.addAction(missing)
            self.bt_menu.addSeparator()

            open_act = QAction("Open Bluetooth Settings", self)
            open_act.triggered.connect(self.open_bluetooth_settings)
            self.bt_menu.addAction(open_act)
            return

        powered = self.bt.powered()
        status_text = "Bluetooth is On" if powered else "Bluetooth is Off"
        status_act = QAction(status_text, self)
        status_act.setEnabled(False)
        self.bt_menu.addAction(status_act)
        self.bt_menu.addSeparator()

        turn_on = QAction("Turn Bluetooth On", self)
        turn_on.setEnabled(powered is not True)
        turn_on.triggered.connect(lambda: self.set_bluetooth_power(True))
        self.bt_menu.addAction(turn_on)

        turn_off = QAction("Turn Bluetooth Off", self)
        turn_off.setEnabled(powered is not False)
        turn_off.triggered.connect(lambda: self.set_bluetooth_power(False))
        self.bt_menu.addAction(turn_off)

        self.bt_menu.addSeparator()

        refresh_act = QAction("Refresh Bluetooth", self)
        refresh_act.triggered.connect(self.refresh_bluetooth)
        self.bt_menu.addAction(refresh_act)

        settings_act = QAction("Open Bluetooth Settings", self)
        settings_act.triggered.connect(self.open_bluetooth_settings)
        self.bt_menu.addAction(settings_act)

    # ----------------------------
    # actions
    # ----------------------------

    def _format_wifi_label(self, net: WifiNetwork) -> str:
        lock = "🔒 " if net.security and net.security != "--" else ""
        current = "✓ " if net.in_use else ""
        signal = f"{net.signal}%"
        return f"{current}{lock}{net.ssid}   {signal}   {net.bars}"

    def open_wifi_settings_for(self, ssid: str) -> None:
        ok = self.wifi.open_settings()
        if not ok:
            QMessageBox.warning(
                self,
                "Open Settings Failed",
                f"Could not open network settings for {ssid!r}."
            )

    def open_network_settings(self) -> None:
        ok = self.wifi.open_settings()
        if not ok:
            QMessageBox.warning(
                self,
                "Open Settings Failed",
                "Could not open Network Settings."
            )

    def open_bluetooth_settings(self) -> None:
        ok = self.bt.open_settings()
        if not ok:
            QMessageBox.warning(
                self,
                "Open Settings Failed",
                "Could not open Bluetooth Settings."
            )

    def set_bluetooth_power(self, enabled: bool) -> None:
        ok = self.bt.set_powered(enabled)
        self.refresh_bluetooth()

        if not ok:
            QMessageBox.warning(
                self,
                "Bluetooth",
                f"Failed to turn Bluetooth {'on' if enabled else 'off'}."
            )


# ----------------------------
# demo
# ----------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    dlg = ConnectivityDialog()
    dlg.show()
    sys.exit(app.exec())