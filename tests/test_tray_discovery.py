from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtDBus import QDBusMessage
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from TPOPyside.components.tray_discovery import ResolvedTrayIcon, TrayDiscovery, item_is_alive


def _app() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


class _FakeValueReply:
    def __init__(self, value, valid: bool = True) -> None:
        self._value = value
        self._valid = valid

    def isValid(self) -> bool:
        return self._valid

    def value(self):
        return self._value


class _FakeBusInterface:
    def __init__(self, services: list[str]) -> None:
        self._services = services

    def registeredServiceNames(self) -> _FakeValueReply:
        return _FakeValueReply(self._services)


class _FakeBus:
    def __init__(self, services: list[str] | None = None, connected: bool = True) -> None:
        self._services = services or []
        self._connected = connected

    def isConnected(self) -> bool:
        return self._connected

    def interface(self) -> _FakeBusInterface:
        return _FakeBusInterface(self._services)


class _FakeCallReply:
    def __init__(self, arguments, message_type) -> None:
        self._arguments = arguments
        self._message_type = message_type

    def type(self):
        return self._message_type

    def arguments(self):
        return self._arguments


class _FakeInterface:
    def __init__(self, valid: bool, reply: _FakeCallReply | None = None) -> None:
        self._valid = valid
        self._reply = reply or _FakeCallReply([], QDBusMessage.ErrorMessage)

    def isValid(self) -> bool:
        return self._valid

    def call(self, *_args):
        return self._reply


class TrayDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._qt_app = _app()

    def test_item_is_alive_rejects_missing_service_names(self) -> None:
        bus = _FakeBus(services=["org.example.Live"])

        self.assertFalse(
            item_is_alive(bus, "org.example.Dead", "/StatusNotifierItem", {"org.example.Live"})
        )

    def test_item_is_alive_accepts_properties_fallback_when_item_iface_is_invalid(self) -> None:
        bus = _FakeBus(services=["org.example.Live"])

        def fake_qdbus_interface(service, path, interface, _bus):
            if interface == "org.kde.StatusNotifierItem":
                return _FakeInterface(valid=False)
            return _FakeInterface(
                valid=True,
                reply=_FakeCallReply([{"Status": "Active"}], QDBusMessage.ReplyMessage),
            )

        with patch("TPOPyside.components.tray_discovery.QDBusInterface", side_effect=fake_qdbus_interface):
            self.assertTrue(
                item_is_alive(bus, "org.example.Live", "/StatusNotifierItem", {"org.example.Live"})
            )

    def test_get_items_skips_dead_registered_entries(self) -> None:
        bus = _FakeBus(services=["org.example.Live"])
        discovery = TrayDiscovery(bus)

        def fake_call_property(_bus, service, _path, _interface, name):
            values = {
                ("org.example.Live", "Menu"): "/MenuBar",
                ("org.example.Live", "ItemIsMenu"): False,
                ("org.example.Live", "Status"): "Active",
            }
            return values.get((service, name))

        with (
            patch("TPOPyside.components.tray_discovery.discover_watcher_service", return_value="org.example.Watcher"),
            patch(
                "TPOPyside.components.tray_discovery.registered_item_ids",
                return_value=[
                    "org.example.Live/StatusNotifierItem",
                    "org.example.Dead/StatusNotifierItem",
                ],
            ),
            patch(
                "TPOPyside.components.tray_discovery.item_is_alive",
                side_effect=lambda _bus, service, _path, _active: service == "org.example.Live",
            ),
            patch("TPOPyside.components.tray_discovery.call_property", side_effect=fake_call_property),
            patch("TPOPyside.components.tray_discovery.best_text", return_value="Live app"),
            patch.object(
                discovery.icon_resolver,
                "resolve_for_item",
                return_value=ResolvedTrayIcon(icon=QIcon(), source="test"),
            ),
        ):
            items = discovery.get_items(visible_only=True)

        self.assertEqual([item.item_id for item in items], ["org.example.Live/StatusNotifierItem"])
        self.assertEqual(items[0].title, "Live app")


if __name__ == "__main__":
    unittest.main()
