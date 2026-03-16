from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .service import BluetoothService, ConnectivitySnapshot, WifiNetwork, WifiService


class ConnectivitySection(QWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        request_refresh: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.wifi = WifiService()
        self.bluetooth = BluetoothService()
        self._request_refresh = request_refresh
        self._current_ssid: str | None = None
        self._visible_networks: tuple[WifiNetwork, ...] = ()
        self._bt_adapter_present = False
        self._bt_powered: bool | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self.wifi_status = QLabel("Wi-Fi: ...", self)
        self.wifi_status.setObjectName("systemMenuStatus")
        self.wifi_button = QToolButton(self)
        self.wifi_button.setText("Wi-Fi")
        self.wifi_button.setPopupMode(QToolButton.InstantPopup)
        self.wifi_menu = QMenu(self)
        self.wifi_button.setMenu(self.wifi_menu)
        root.addLayout(self._build_status_row(self.wifi_status, self.wifi_button))

        self.bt_status = QLabel("Bluetooth: ...", self)
        self.bt_status.setObjectName("systemMenuStatus")
        self.bt_button = QToolButton(self)
        self.bt_button.setText("Bluetooth")
        self.bt_button.setPopupMode(QToolButton.InstantPopup)
        self.bt_menu = QMenu(self)
        self.bt_button.setMenu(self.bt_menu)
        root.addLayout(self._build_status_row(self.bt_status, self.bt_button))

        self._rebuild_wifi_menu()
        self._rebuild_bt_menu()

    def _build_status_row(self, label: QLabel, button: QToolButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(label, stretch=1)
        row.addWidget(button)
        return row

    def refresh(self) -> None:
        if self._request_refresh is not None:
            self._request_refresh()
            return
        adapter_present = self.bluetooth.adapter_present()
        self.apply_snapshot(
            ConnectivitySnapshot(
                current_ssid=self.wifi.current_ssid(),
                visible_networks=tuple(self.wifi.visible_networks()),
                bluetooth_adapter_present=adapter_present,
                bluetooth_powered=self.bluetooth.powered() if adapter_present else None,
            )
        )

    def apply_snapshot(self, snapshot: ConnectivitySnapshot) -> None:
        self._current_ssid = snapshot.current_ssid
        self._visible_networks = snapshot.visible_networks
        self._bt_adapter_present = snapshot.bluetooth_adapter_present
        self._bt_powered = snapshot.bluetooth_powered
        self.refresh_wifi()
        self.refresh_bluetooth()
        self._rebuild_wifi_menu()
        self._rebuild_bt_menu()

    def refresh_wifi(self) -> None:
        ssid = self._current_ssid
        self.wifi_status.setText(f"Wi-Fi: connected to {ssid}" if ssid else "Wi-Fi: not connected")

    def refresh_bluetooth(self) -> None:
        if not self._bt_adapter_present:
            self.bt_status.setText("Bluetooth: no adapter found")
            return
        powered = self._bt_powered
        if powered is True:
            self.bt_status.setText("Bluetooth: on")
        elif powered is False:
            self.bt_status.setText("Bluetooth: off")
        else:
            self.bt_status.setText("Bluetooth: unknown")

    def _rebuild_wifi_menu(self) -> None:
        self.wifi_menu.clear()

        title_action = QAction(
            f"Connected: {self._current_ssid}" if self._current_ssid else "Connected: none",
            self,
        )
        title_action.setEnabled(False)
        self.wifi_menu.addAction(title_action)
        self.wifi_menu.addSeparator()

        if self._visible_networks:
            for network in self._visible_networks[:20]:
                action = QAction(self._format_wifi_label(network), self)
                action.triggered.connect(
                    lambda checked=False, ssid=network.ssid: self.open_wifi_settings_for(ssid)
                )
                self.wifi_menu.addAction(action)
        else:
            no_networks = QAction("No networks found", self)
            no_networks.setEnabled(False)
            self.wifi_menu.addAction(no_networks)

        self.wifi_menu.addSeparator()

        refresh_action = QAction("Refresh Wi-Fi", self)
        refresh_action.triggered.connect(self.refresh)
        self.wifi_menu.addAction(refresh_action)

        settings_action = QAction("Open Network Settings", self)
        settings_action.triggered.connect(self.open_network_settings)
        self.wifi_menu.addAction(settings_action)

    def _rebuild_bt_menu(self) -> None:
        self.bt_menu.clear()

        if not self._bt_adapter_present:
            missing = QAction("No Bluetooth adapter found", self)
            missing.setEnabled(False)
            self.bt_menu.addAction(missing)
            self.bt_menu.addSeparator()

            settings_action = QAction("Open Bluetooth Settings", self)
            settings_action.triggered.connect(self.open_bluetooth_settings)
            self.bt_menu.addAction(settings_action)
            return

        powered = self._bt_powered
        status_action = QAction("Bluetooth is On" if powered else "Bluetooth is Off", self)
        status_action.setEnabled(False)
        self.bt_menu.addAction(status_action)
        self.bt_menu.addSeparator()

        turn_on = QAction("Turn Bluetooth On", self)
        turn_on.setEnabled(powered is not True)
        turn_on.triggered.connect(lambda checked=False: self.set_bluetooth_power(True))
        self.bt_menu.addAction(turn_on)

        turn_off = QAction("Turn Bluetooth Off", self)
        turn_off.setEnabled(powered is not False)
        turn_off.triggered.connect(lambda checked=False: self.set_bluetooth_power(False))
        self.bt_menu.addAction(turn_off)

        self.bt_menu.addSeparator()

        refresh_action = QAction("Refresh Bluetooth", self)
        refresh_action.triggered.connect(self.refresh)
        self.bt_menu.addAction(refresh_action)

        settings_action = QAction("Open Bluetooth Settings", self)
        settings_action.triggered.connect(self.open_bluetooth_settings)
        self.bt_menu.addAction(settings_action)

    def _format_wifi_label(self, network: WifiNetwork) -> str:
        lock = "Locked " if network.security and network.security != "--" else ""
        current = "Current " if network.in_use else ""
        return f"{current}{lock}{network.ssid}   {network.signal}%   {network.bars}"

    def open_wifi_settings_for(self, ssid: str) -> None:
        if self.wifi.open_settings():
            return
        QMessageBox.warning(self, "Open Settings Failed", f"Could not open network settings for {ssid!r}.")

    def open_network_settings(self) -> None:
        if self.wifi.open_settings():
            return
        QMessageBox.warning(self, "Open Settings Failed", "Could not open Network Settings.")

    def open_bluetooth_settings(self) -> None:
        if self.bluetooth.open_settings():
            return
        QMessageBox.warning(self, "Open Settings Failed", "Could not open Bluetooth Settings.")

    def set_bluetooth_power(self, enabled: bool) -> None:
        ok = self.bluetooth.set_powered(enabled)
        self.refresh()
        if ok:
            return
        QMessageBox.warning(self, "Bluetooth", f"Failed to turn Bluetooth {'on' if enabled else 'off'}.")
