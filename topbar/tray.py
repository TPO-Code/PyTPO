from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Slot, Signal
from PySide6.QtDBus import (
    QDBusConnection,
    QDBusInterface,
    QDBusMessage,
    QDBusVariant,
    QDBusVirtualObject,
)
from PySide6.QtGui import QAction, QCursor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QSizePolicy, QStyle, QToolButton, QWidget

from .constants import (
    BUSCTL,
    ITEM_INTERFACE,
    MENU_INTERFACE,
    PROPERTIES_INTERFACE,
    WATCHER_INTERFACE,
    WATCHER_INTERFACES,
    WATCHER_PATH,
    WATCHER_SERVICES,
)
from .dbus import (
    boolish,
    bus_base_service_name,
    claim_dbus_service,
    clean_menu_label,
    dbus_error_text,
    dbus_to_python,
    discover_watcher_service,
    extract_service_and_path,
    fetch_property_via_busctl,
    format_dbus_message_arguments,
    format_dbus_message_summary,
    intish,
    load_xlib,
    message_type_name,
    menu_property,
    normalize_menu_layout,
    preview_dbus_value,
    resource_id,
    run_busctl_json,
    try_connect_dbus_signal,
    unwrap_busctl_json,
    virtual_object_mode_name,
)

LOGGER = logging.getLogger("topbar.tray")

DBUS_NEXT_IMPORT_ERROR: Exception | None = None
try:
    from dbus_next.aio import MessageBus as DBusNextMessageBus
    from dbus_next.constants import BusType as DBusNextBusType
    from dbus_next.constants import MessageType as DBusNextMessageType
    from dbus_next.message import Message as DBusNextMessage
    from dbus_next.signature import Variant as DBusNextVariant
except Exception as exc:
    DBUS_NEXT_IMPORT_ERROR = exc
    DBusNextMessageBus = None
    DBusNextBusType = None
    DBusNextMessageType = None
    DBusNextMessage = None
    DBusNextVariant = None


def dbus_next_to_python(value: Any) -> Any:
    if DBusNextVariant is not None and isinstance(value, DBusNextVariant):
        return dbus_next_to_python(value.value)
    if isinstance(value, dict):
        return {str(dbus_next_to_python(key)): dbus_next_to_python(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [dbus_next_to_python(item) for item in value]
    return value


def fetch_raw_pixmap_via_busctl(service: str, path: str) -> QIcon:
    if not BUSCTL:
        return QIcon()
    try:
        result = run_busctl_json("get-property", service, path, ITEM_INTERFACE, "IconPixmap", timeout=1)
        if not isinstance(result, dict):
            return QIcon()

        entries = result.get("data", [])
        candidates = []
        for entry in entries:
            if len(entry) != 3:
                continue
            width, height, pixels = entry
            raw_array = bytearray(pixels)
            if width <= 0 or height <= 0 or len(raw_array) < width * height * 4:
                continue

            a = raw_array[0::4]
            r = raw_array[1::4]
            g = raw_array[2::4]
            b = raw_array[3::4]

            reordered = bytearray(len(raw_array))
            reordered[0::4] = b
            reordered[1::4] = g
            reordered[2::4] = r
            reordered[3::4] = a
            candidates.append((width, height, bytes(reordered)))

        if not candidates:
            return QIcon()

        candidates.sort(key=lambda item: abs(max(item[0], item[1]) - 22))
        icon = QIcon()
        for width, height, raw in candidates:
            image = QImage(raw, width, height, width * 4, QImage.Format_ARGB32)
            if not image.isNull():
                icon.addPixmap(QPixmap.fromImage(image.copy()))
        return icon
    except Exception as exc:
        LOGGER.debug("Failed to decode IconPixmap via busctl for %s%s: %r", service, path, exc)
        return QIcon()


def item_icon(props: dict[str, Any], service: str, path: str) -> QIcon:
    icon_name = str(props.get("IconName") or "").strip()
    if icon_name and icon_name.startswith("/"):
        return QIcon(icon_name)

    if icon_name:
        theme_icon = QIcon.fromTheme(icon_name)
        if not theme_icon.isNull():
            return theme_icon

    pixmap_icon = fetch_raw_pixmap_via_busctl(service, path)
    if not pixmap_icon.isNull():
        return pixmap_icon

    app = QApplication.instance()
    return app.style().standardIcon(QStyle.SP_TitleBarMenuButton)


class DBusMenuProxy:
    def __init__(self, service: str, menu_path: str):
        self.service = service
        self.menu_path = menu_path

    def _dbus_next_call_sync(self, member: str, signature: str = "", body: list[Any] | None = None) -> Any:
        if DBusNextMessageBus is None or DBusNextMessage is None or DBusNextBusType is None:
            raise RuntimeError(f"dbus-next unavailable: {DBUS_NEXT_IMPORT_ERROR or 'module import failed'}")

        async def _run_call() -> Any:
            bus = await DBusNextMessageBus(bus_type=DBusNextBusType.SESSION).connect()
            try:
                reply = await bus.call(
                    DBusNextMessage(
                        destination=self.service,
                        path=self.menu_path,
                        interface=MENU_INTERFACE,
                        member=member,
                        signature=signature,
                        body=body or [],
                    )
                )
                if DBusNextMessageType is not None and reply.message_type == DBusNextMessageType.ERROR:
                    raise RuntimeError(f"{reply.error_name or 'dbus-next error'}: {reply.body!r}")
                return reply.body
            finally:
                bus.disconnect()

        return asyncio.run(_run_call())

    def fetch_layout_via_dbus_next(self, parent_id: int, recursion_depth: int) -> dict[str, Any] | None:
        if DBusNextVariant is None:
            return None
        try:
            payload = self._dbus_next_call_sync("GetLayout", "iias", [parent_id, recursion_depth, []])
        except Exception as exc:
            LOGGER.debug("dbus-next GetLayout failed for %s%s: %r", self.service, self.menu_path, exc)
            return None
        if not isinstance(payload, list) or len(payload) < 2:
            return None
        return normalize_menu_layout(dbus_next_to_python(payload[1]))

    def fetch_layout_via_busctl(self, parent_id: int, recursion_depth: int) -> dict[str, Any] | None:
        data = run_busctl_json(
            "call",
            self.service,
            self.menu_path,
            MENU_INTERFACE,
            "GetLayout",
            "iias",
            str(parent_id),
            str(recursion_depth),
            "0",
        )
        if data is None:
            return None
        payload = unwrap_busctl_json(data.get("data"))
        if not isinstance(payload, list) or len(payload) < 2:
            return None
        return normalize_menu_layout(payload[1])

    def fetch_layout(self, parent_id: int, recursion_depth: int) -> dict[str, Any] | None:
        return self.fetch_layout_via_dbus_next(parent_id, recursion_depth) or self.fetch_layout_via_busctl(
            parent_id, recursion_depth
        )

    def about_to_show(self, item_id: int) -> None:
        if DBusNextVariant is not None:
            try:
                self._dbus_next_call_sync("AboutToShow", "i", [item_id])
                return
            except Exception as exc:
                LOGGER.debug("dbus-next AboutToShow failed for %s%s: %r", self.service, self.menu_path, exc)
        run_busctl_json(
            "call",
            self.service,
            self.menu_path,
            MENU_INTERFACE,
            "AboutToShow",
            "i",
            str(item_id),
        )

    def emit_event(self, item_id: int, event_id: str = "clicked") -> bool:
        if DBusNextVariant is not None:
            try:
                self._dbus_next_call_sync("Event", "isvu", [item_id, event_id, DBusNextVariant("s", ""), 0])
                return True
            except Exception as exc:
                LOGGER.debug("dbus-next Event failed for %s%s: %r", self.service, self.menu_path, exc)
        if not BUSCTL:
            return False
        try:
            result = run_busctl_json(
                "call",
                self.service,
                self.menu_path,
                MENU_INTERFACE,
                "Event",
                "isvu",
                str(item_id),
                event_id,
                "s",
                "",
                "0",
            )
        except Exception:
            return False
        return result is not None


class StatusNotifierWatcher(QDBusVirtualObject):
    itemsChanged = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.bus = QDBusConnection.sessionBus()
        self.is_active = False
        self.last_error = ""
        self._service_names: list[str] = []
        self._registered_items: list[str] = []
        self._registered_hosts: list[str] = []
        self._register()
        try_connect_dbus_signal(
            self.bus,
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            self,
            "_on_name_owner_changed(QString,QString,QString)",
        )

    @property
    def primary_service_name(self) -> str:
        return self._service_names[0] if self._service_names else ""

    def registered_items(self) -> list[str]:
        return list(self._registered_items)

    def status_text(self) -> str:
        if self.is_active:
            names = ", ".join(self._service_names)
            return f"Tray watcher active on {names or WATCHER_SERVICES[0]}"
        return f"Tray watcher inactive: {self.last_error or 'unknown error'}"

    def _register(self) -> None:
        if not self.bus.isConnected():
            self.last_error = "session bus is not connected"
            LOGGER.error(self.last_error)
            return

        LOGGER.info(
            "Registering watcher services=%s object=%s connected=%s base_service=%s",
            ", ".join(WATCHER_SERVICES),
            WATCHER_PATH,
            self.bus.isConnected(),
            bus_base_service_name(self.bus) or "<none>",
        )
        for service_name in WATCHER_SERVICES:
            ok, error = claim_dbus_service(self.bus, service_name)
            if ok:
                self._service_names.append(service_name)
            elif not self.last_error:
                self.last_error = error
                LOGGER.warning("Watcher service claim failed for %s: %s", service_name, error)

        if not self._service_names:
            return

        mode = QDBusConnection.VirtualObjectRegisterOption.SingleNode
        LOGGER.info(
            "Watcher virtual object register attempt object=%s target=%s mode=%s claimed_services=%s",
            WATCHER_PATH,
            WATCHER_PATH,
            virtual_object_mode_name(mode),
            self._service_names,
        )
        # Register the exact watcher path so it does not collide with the notification virtual object.
        registered = self.bus.registerVirtualObject(
            WATCHER_PATH,
            self,
            mode,
        )
        LOGGER.info(
            "Watcher virtual object register result object=%s target=%s mode=%s success=%s last_error=%s",
            WATCHER_PATH,
            WATCHER_PATH,
            virtual_object_mode_name(mode),
            registered,
            dbus_error_text(self.bus),
        )
        if not registered:
            for service_name in self._service_names:
                self.bus.unregisterService(service_name)
            self._service_names.clear()
            self.last_error = f"failed to register virtual object at {WATCHER_PATH}: {dbus_error_text(self.bus)}"
            LOGGER.error(self.last_error)
            return

        self.is_active = True
        self.last_error = ""
        self._register_host(self.primary_service_name or WATCHER_SERVICES[0])
        LOGGER.info(
            "StatusNotifierWatcher ready services=%s root=%s target=%s mode=%s",
            ", ".join(self._service_names),
            WATCHER_PATH,
            WATCHER_PATH,
            virtual_object_mode_name(mode),
        )

    def introspect(self, path: str) -> str:
        xml = ""
        summary = ""
        if path == WATCHER_PATH:
            interfaces_xml = "\n".join(self._watcher_interface_xml(name) for name in WATCHER_INTERFACES)
            xml = f"""
            <node>
              {interfaces_xml}
              <interface name="{PROPERTIES_INTERFACE}">
                <method name="Get">
                  <arg direction="in" type="s" name="interface"/>
                  <arg direction="in" type="s" name="property"/>
                  <arg direction="out" type="v" name="value"/>
                </method>
                <method name="GetAll">
                  <arg direction="in" type="s" name="interface"/>
                  <arg direction="out" type="a{{sv}}" name="properties"/>
                </method>
                <method name="Set">
                  <arg direction="in" type="s" name="interface"/>
                  <arg direction="in" type="s" name="property"/>
                  <arg direction="in" type="v" name="value"/>
                </method>
              </interface>
            </node>
            """.strip()
            summary = f"interfaces={','.join(WATCHER_INTERFACES)} path={WATCHER_PATH}"

        if xml:
            LOGGER.debug("DBus introspect object=StatusNotifierWatcher path=%s result=xml summary=%s", path, summary)
        else:
            LOGGER.debug("DBus introspect object=StatusNotifierWatcher path=%s result=empty", path)
        return xml

    def _watcher_interface_xml(self, interface_name: str) -> str:
        return f"""
        <interface name="{interface_name}">
          <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
          <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
          <property name="ProtocolVersion" type="i" access="read"/>
          <method name="RegisterStatusNotifierItem">
            <arg direction="in" type="s" name="service_or_path"/>
          </method>
          <method name="RegisterStatusNotifierHost">
            <arg direction="in" type="s" name="service"/>
          </method>
          <signal name="StatusNotifierItemRegistered">
            <arg type="s" name="service"/>
          </signal>
          <signal name="StatusNotifierItemUnregistered">
            <arg type="s" name="service"/>
          </signal>
          <signal name="StatusNotifierHostRegistered"/>
          <signal name="StatusNotifierHostUnregistered"/>
        </interface>
        """.strip()

    def handleMessage(self, message: QDBusMessage, connection: QDBusConnection) -> bool:
        LOGGER.debug("DBus incoming object=StatusNotifierWatcher %s", format_dbus_message_summary(message))
        if message.path() != WATCHER_PATH:
            LOGGER.debug(
                "DBus rejected object=StatusNotifierWatcher reason=path-mismatch expected=%s actual=%s",
                WATCHER_PATH,
                message.path() or "<none>",
            )
            return False

        interface = str(message.interface() or "")
        member = str(message.member() or "")
        arguments = message.arguments()
        LOGGER.debug(
            "DBus accepted object=StatusNotifierWatcher member=%s interface=%s decoded_args=%s",
            member or "<none>",
            interface or "<none>",
            format_dbus_message_arguments(message),
        )

        if interface in WATCHER_INTERFACES:
            if member == "RegisterStatusNotifierItem":
                raw_identifier = str(arguments[0] or "") if arguments else ""
                item_id = self._normalize_item_identifier(message.service(), raw_identifier)
                LOGGER.info(
                    "Watcher RegisterStatusNotifierItem sender=%s raw_identifier=%r normalized_identifier=%r",
                    message.service() or "<none>",
                    raw_identifier,
                    item_id,
                )
                if item_id:
                    self._register_item(item_id)
                self._send_reply(connection, message, [], context="RegisterStatusNotifierItem")
                return True

            if member == "RegisterStatusNotifierHost":
                host_name = str(arguments[0] or "") if arguments else ""
                if not host_name:
                    host_name = str(message.service() or "")
                LOGGER.info("Registering tray host %s", host_name)
                self._register_host(host_name or self.primary_service_name or WATCHER_SERVICES[0])
                self._send_reply(connection, message, [], context="RegisterStatusNotifierHost")
                return True

            self._send_error_reply(
                connection,
                message,
                "org.freedesktop.DBus.Error.UnknownMethod",
                f"Unknown watcher method: {member}",
                context="UnknownWatcherMethod",
            )
            return True

        if interface == PROPERTIES_INTERFACE:
            if member == "Get" and len(arguments) >= 2:
                iface_name = str(arguments[0] or "")
                prop_name = str(arguments[1] or "")
                if iface_name not in WATCHER_INTERFACES:
                    self._send_error_reply(
                        connection,
                        message,
                        "org.freedesktop.DBus.Error.UnknownInterface",
                        f"Unknown interface: {iface_name}",
                        context="Properties.Get",
                    )
                    return True
                if prop_name not in {"RegisteredStatusNotifierItems", "IsStatusNotifierHostRegistered", "ProtocolVersion"}:
                    self._send_error_reply(
                        connection,
                        message,
                        "org.freedesktop.DBus.Error.UnknownProperty",
                        f"Unknown property: {prop_name}",
                        context="Properties.Get",
                    )
                    return True
                property_value = self._property_value(prop_name)
                payload = [QDBusVariant(property_value)]
                LOGGER.info(
                    "Watcher Properties.Get interface=%s property=%s reply=%s",
                    iface_name,
                    prop_name,
                    preview_dbus_value(property_value),
                )
                self._send_reply(connection, message, payload, context=f"Properties.Get.{prop_name}")
                return True

            if member == "GetAll" and arguments:
                iface_name = str(arguments[0] or "")
                if iface_name not in WATCHER_INTERFACES:
                    LOGGER.info("Watcher Properties.GetAll interface=%s reply=%s", iface_name, preview_dbus_value({}))
                    self._send_reply(connection, message, [{}], context="Properties.GetAll.UnknownInterface")
                    return True
                properties = self._properties_payload()
                LOGGER.info("Watcher Properties.GetAll interface=%s reply=%s", iface_name, preview_dbus_value(properties))
                self._send_reply(connection, message, [properties], context="Properties.GetAll")
                return True

            if member == "Set":
                self._send_error_reply(
                    connection,
                    message,
                    "org.freedesktop.DBus.Error.PropertyReadOnly",
                    "StatusNotifierWatcher properties are read-only",
                    context="Properties.Set",
                )
                return True

        LOGGER.debug(
            "DBus rejected object=StatusNotifierWatcher reason=unsupported-interface interface=%s member=%s",
            interface or "<none>",
            member or "<none>",
        )
        return False

    def _normalize_item_identifier(self, sender_service: str, raw_identifier: str) -> str:
        identifier = raw_identifier.strip()
        if identifier.startswith("/"):
            return f"{sender_service or ''}{identifier}".strip()
        if identifier:
            service, path = extract_service_and_path(identifier)
            return f"{service}{path}"
        if sender_service:
            return f"{sender_service}/StatusNotifierItem"
        return ""

    def _register_item(self, item_id: str) -> None:
        if item_id in self._registered_items:
            LOGGER.debug("Watcher register item skipped item=%s reason=already-registered", item_id)
            return
        self._registered_items.append(item_id)
        LOGGER.info("Watcher registered item=%s items=%s", item_id, preview_dbus_value(self._registered_items))
        self._emit_signal("StatusNotifierItemRegistered", [item_id])
        self.itemsChanged.emit()

    def _unregister_item(self, item_id: str) -> None:
        if item_id not in self._registered_items:
            return
        self._registered_items.remove(item_id)
        LOGGER.info("Watcher unregistered item=%s items=%s", item_id, preview_dbus_value(self._registered_items))
        self._emit_signal("StatusNotifierItemUnregistered", [item_id])
        self.itemsChanged.emit()

    def _register_host(self, host_name: str) -> None:
        if not host_name or host_name in self._registered_hosts:
            return
        self._registered_hosts.append(host_name)
        LOGGER.info("Watcher registered host=%s hosts=%s", host_name, preview_dbus_value(self._registered_hosts))
        self._emit_signal("StatusNotifierHostRegistered", [])

    def _unregister_host(self, host_name: str) -> None:
        if host_name not in self._registered_hosts:
            return
        self._registered_hosts.remove(host_name)
        LOGGER.info("Watcher unregistered host=%s hosts=%s", host_name, preview_dbus_value(self._registered_hosts))
        self._emit_signal("StatusNotifierHostUnregistered", [])

    def _property_value(self, name: str) -> Any:
        if name == "RegisteredStatusNotifierItems":
            return list(self._registered_items)
        if name == "IsStatusNotifierHostRegistered":
            return bool(self._registered_hosts)
        if name == "ProtocolVersion":
            return 0
        return None

    def _properties_payload(self) -> dict[str, QDBusVariant]:
        return {
            "RegisteredStatusNotifierItems": QDBusVariant(list(self._registered_items)),
            "IsStatusNotifierHostRegistered": QDBusVariant(bool(self._registered_hosts)),
            "ProtocolVersion": QDBusVariant(0),
        }

    def _emit_signal(self, name: str, arguments: list[Any]) -> None:
        for interface_name in WATCHER_INTERFACES:
            signal = QDBusMessage.createSignal(WATCHER_PATH, interface_name, name)
            signal.setArguments(arguments)
            sent = self.bus.send(signal)
            LOGGER.info(
                "DBus signal path=%s interface=%s member=%s args=%s sent=%s",
                WATCHER_PATH,
                interface_name,
                name,
                preview_dbus_value(arguments),
                sent,
            )

    @Slot(str, str, str)
    def _on_name_owner_changed(self, name: str, old_owner: str, new_owner: str) -> None:
        LOGGER.info(
            "Watcher NameOwnerChanged name=%s old_owner=%s new_owner=%s",
            name,
            old_owner or "<none>",
            new_owner or "<none>",
        )
        if new_owner:
            return
        removals: list[str] = []
        for item_id in list(self._registered_items):
            service, _path = extract_service_and_path(item_id)
            if service == name:
                removals.append(f"item:{item_id}")
                self._unregister_item(item_id)
        if name in self._registered_hosts:
            removals.append(f"host:{name}")
            self._unregister_host(name)
        LOGGER.info("Watcher NameOwnerChanged removals name=%s removals=%s", name, preview_dbus_value(removals))

    def _send_reply(
        self,
        connection: QDBusConnection,
        request_message: QDBusMessage,
        payload: list[Any],
        *,
        context: str,
    ) -> None:
        reply = request_message.createReply()
        reply.setArguments(payload)
        sent = connection.send(reply)
        LOGGER.debug(
            "DBus reply object=StatusNotifierWatcher context=%s sent=%s reply_type=%s reply_signature=%s payload=%s reply_args=%s",
            context,
            sent,
            message_type_name(reply),
            reply.signature() or "<empty>",
            preview_dbus_value(payload),
            preview_dbus_value(reply.arguments()),
        )

    def _send_error_reply(
        self,
        connection: QDBusConnection,
        request_message: QDBusMessage,
        error_name: str,
        error_message: str,
        *,
        context: str,
    ) -> None:
        reply = request_message.createErrorReply(error_name, error_message)
        sent = connection.send(reply)
        LOGGER.warning(
            "DBus error reply object=StatusNotifierWatcher context=%s sent=%s error_name=%s error_message=%s",
            context,
            sent,
            error_name,
            error_message,
        )


class X11TraySelectionManager(QObject):
    def __init__(self, widget: QWidget, parent: QObject | None = None):
        super().__init__(parent or widget)
        self._widget = widget
        self.is_owner = False
        self.last_error = ""
        self.selection_name = ""
        self.owner_window_id = 0

    def is_supported(self) -> bool:
        app = QApplication.instance()
        platform_name = app.platformName().lower() if app is not None else ""
        return platform_name == "xcb"

    def status_text(self) -> str:
        if not self.is_supported():
            return "X11 tray manager unavailable on this platform"
        if self.is_owner:
            return f"X11 tray owner for {self.selection_name or '_NET_SYSTEM_TRAY_S0'}"
        return f"X11 tray owner inactive: {self.last_error or 'unknown error'}"

    def claim(self) -> None:
        self.is_owner = False
        self.last_error = ""

        if not self.is_supported():
            self.last_error = "Qt platform is not xcb"
            LOGGER.warning(self.last_error)
            return

        try:
            native_window_id = int(self._widget.winId())
        except Exception:
            native_window_id = 0
        if native_window_id <= 0:
            self.last_error = "window has no native X11 id yet"
            LOGGER.warning(self.last_error)
            return

        try:
            X, Xatom, display = load_xlib()
        except Exception as exc:
            self.last_error = str(exc)
            LOGGER.error(self.last_error)
            return
        try:
            from Xlib.protocol import event
        except Exception as exc:
            self.last_error = f"python-xlib event support is unavailable: {exc!r}"
            LOGGER.error(self.last_error)
            return

        try:
            x_display = display.Display()
        except Exception as exc:
            self.last_error = f"failed to connect to the X server: {exc!r}"
            LOGGER.error(self.last_error)
            return

        try:
            screen_index = x_display.get_default_screen()
            self.selection_name = f"_NET_SYSTEM_TRAY_S{screen_index}"
            selection_atom = x_display.intern_atom(self.selection_name)
            manager_atom = x_display.intern_atom("MANAGER")
            orientation_atom = x_display.intern_atom("_NET_SYSTEM_TRAY_ORIENTATION")
            owner_window = x_display.create_resource_object("window", native_window_id)
            root = x_display.screen(screen_index).root

            owner_window.change_property(orientation_atom, Xatom.CARDINAL, 32, [0], X.PropModeReplace)
            owner_window.set_selection_owner(selection_atom, X.CurrentTime)
            x_display.sync()

            current_owner = x_display.get_selection_owner(selection_atom)
            current_owner_id = resource_id(current_owner)
            if current_owner_id != native_window_id:
                self.last_error = f"{self.selection_name} is still owned by X11 window {current_owner_id or 'unknown'}"
                LOGGER.warning(self.last_error)
                return

            manager_event = event.ClientMessage(
                window=root,
                client_type=manager_atom,
                data=(32, [X.CurrentTime, selection_atom, native_window_id, 0, 0]),
            )
            root.send_event(manager_event, event_mask=X.StructureNotifyMask)
            x_display.flush()
            x_display.sync()

            self.owner_window_id = native_window_id
            self.is_owner = True
            LOGGER.info("Claimed X11 tray selection %s for window %s", self.selection_name, native_window_id)
        except Exception as exc:
            self.last_error = f"failed to claim {self.selection_name or '_NET_SYSTEM_TRAY_S0'}: {exc!r}"
            LOGGER.error(self.last_error)
        finally:
            try:
                x_display.close()
            except Exception:
                pass


class StatusNotifierButton(QToolButton):
    def __init__(self, item_id: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.item_id = item_id
        self.service, self.path = extract_service_and_path(item_id)
        self._bus = QDBusConnection.sessionBus()
        self._props_iface = QDBusInterface(self.service, self.path, PROPERTIES_INTERFACE, self._bus)
        self._item_iface = QDBusInterface(self.service, self.path, ITEM_INTERFACE, self._bus)
        self._props: dict[str, Any] = {}
        self._menu_proxy: DBusMenuProxy | None = None

        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setIconSize(QSize(20, 20))
        self.setFixedSize(28, 28)
        self.setStyleSheet(
            """
            QToolButton { background: transparent; border: none; border-radius: 4px; padding: 0px; }
            QToolButton:hover { background: rgba(255, 255, 255, 0.12); }
            QToolButton:pressed { background: rgba(255, 255, 255, 0.18); }
            """
        )

        try_connect_dbus_signal(self._bus, self.service, self.path, ITEM_INTERFACE, "NewIcon", self, "_on_item_changed()")
        try_connect_dbus_signal(self._bus, self.service, self.path, ITEM_INTERFACE, "NewTitle", self, "_on_item_changed()")
        try_connect_dbus_signal(
            self._bus,
            self.service,
            self.path,
            ITEM_INTERFACE,
            "NewStatus",
            self,
            "_on_status_changed(QString)",
        )

        self.refresh()

    def refresh(self) -> None:
        if not self._props_iface.isValid():
            LOGGER.debug("Tray item properties interface invalid for %s", self.item_id)
            self.setVisible(False)
            return

        self._props = {}
        for prop in ["Status", "IconName", "Title", "ItemIsMenu", "Menu"]:
            reply = self._props_iface.call("Get", ITEM_INTERFACE, prop)
            if reply.type() != QDBusMessage.ErrorMessage and reply.arguments():
                self._props[prop] = dbus_to_python(reply.arguments()[0])
        for prop in ("Menu", "ItemIsMenu", "Title", "Status", "IconName"):
            if prop not in self._props:
                value = fetch_property_via_busctl(self.service, self.path, ITEM_INTERFACE, prop)
                if value is not None:
                    self._props[prop] = value

        title = self._props.get("Title", "Unknown")
        self.setToolTip(str(title))
        self.setIcon(item_icon(self._props, self.service, self.path))
        self.setVisible(True)

    def _call_item(self, method: str, *args: Any) -> None:
        if self._item_iface.isValid():
            LOGGER.debug("Calling tray item %s.%s%r", self.service, method, args)
            self._item_iface.call(method, *args)

    def _menu_proxy_for_item(self) -> DBusMenuProxy | None:
        menu_path = str(self._props.get("Menu") or "").strip()
        if not menu_path.startswith("/"):
            return None
        if self._menu_proxy is None or self._menu_proxy.menu_path != menu_path:
            self._menu_proxy = DBusMenuProxy(self.service, menu_path)
        return self._menu_proxy

    def _build_qmenu(self) -> QMenu | None:
        menu_proxy = self._menu_proxy_for_item()
        if menu_proxy is None:
            return None
        menu_proxy.about_to_show(0)
        root = menu_proxy.fetch_layout(0, -1)
        if root is None:
            return None
        menu = QMenu(self)
        self._populate_menu(menu, menu_proxy, root)
        if menu.isEmpty():
            return None
        return menu

    def _populate_menu(self, menu: QMenu, menu_proxy: DBusMenuProxy, node: dict[str, Any]) -> None:
        for child in node.get("children", []):
            props = child.get("properties", {})
            if not boolish(menu_property(props, "visible", True), default=True):
                continue

            item_type = str(menu_property(props, "type", "") or "").strip().lower()
            if item_type == "separator":
                menu.addSeparator()
                continue

            label = clean_menu_label(menu_property(props, "label"))
            enabled = boolish(menu_property(props, "enabled", True), default=True)
            icon_name = str(menu_property(props, "icon-name", "") or "").strip()
            has_children = bool(child.get("children"))
            children_display = str(menu_property(props, "children-display", "") or "").strip().lower()
            is_submenu = children_display == "submenu" or has_children

            if is_submenu:
                submenu = menu.addMenu(label or "Menu")
                submenu.setEnabled(enabled)
                if icon_name:
                    submenu.setIcon(QIcon.fromTheme(icon_name))
                self._populate_menu(submenu, menu_proxy, child)
                submenu.aboutToShow.connect(
                    lambda menu_obj=submenu, item_id=child["id"], proxy=menu_proxy: self._refresh_submenu(
                        menu_obj,
                        proxy,
                        item_id,
                    )
                )
                continue

            action = QAction(label or "Unnamed", menu)
            action.setEnabled(enabled)
            if icon_name:
                action.setIcon(QIcon.fromTheme(icon_name))
            toggle_type = str(menu_property(props, "toggle-type", "") or "").strip().lower()
            if toggle_type in {"checkmark", "radio"}:
                action.setCheckable(True)
                action.setChecked(intish(menu_property(props, "toggle-state")) == 1)
            action.triggered.connect(
                lambda checked=False, item_id=child["id"], proxy=menu_proxy: proxy.emit_event(item_id)
            )
            menu.addAction(action)

    def _refresh_submenu(self, menu: QMenu, menu_proxy: DBusMenuProxy, parent_id: int) -> None:
        menu_proxy.about_to_show(parent_id)
        node = menu_proxy.fetch_layout(parent_id, 1)
        if node is None:
            return
        menu.clear()
        self._populate_menu(menu, menu_proxy, node)

    def _show_dbusmenu(self, global_pos) -> bool:
        menu = self._build_qmenu()
        if menu is None:
            return False
        menu.exec(global_pos)
        return True

    @Slot()
    def _on_item_changed(self) -> None:
        self.refresh()

    @Slot(str)
    def _on_status_changed(self, _status: str) -> None:
        self.refresh()

    def mouseReleaseEvent(self, event) -> None:
        point = self.mapToGlobal(self.rect().bottomLeft())
        x, y = point.x(), point.y()
        menu_pos = QCursor.pos()
        item_is_menu = boolish(self._props.get("ItemIsMenu"), default=False)

        if event.button() == Qt.LeftButton:
            if item_is_menu:
                if not self._show_dbusmenu(menu_pos):
                    self._call_item("ContextMenu", x, y)
            else:
                self._call_item("Activate", x, y)
        elif event.button() == Qt.RightButton:
            if not self._show_dbusmenu(menu_pos):
                self._call_item("ContextMenu", x, y)
        event.accept()


class StatusNotifierTrayArea(QWidget):
    def __init__(
        self,
        watcher: StatusNotifierWatcher | None = None,
        tray_selection_manager: X11TraySelectionManager | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._bus = QDBusConnection.sessionBus()
        self._local_watcher = watcher
        self._tray_selection_manager = tray_selection_manager
        self._watcher_service = ""
        self._watcher_props: QDBusInterface | None = None
        self._buttons: dict[str, StatusNotifierButton] = {}

        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(3000)
        self._refresh_timer.timeout.connect(self.sync_items)
        self._refresh_timer.start()
        if self._local_watcher is not None:
            self._local_watcher.itemsChanged.connect(self.sync_items)
        self.sync_items()

    def sync_items(self) -> None:
        self._refresh_watcher_interface()
        if self._local_watcher is not None and self._local_watcher.is_active:
            ordered_ids = self._local_watcher.registered_items()
        else:
            ordered_ids = self._remote_registered_items()

        current_ids = set(self._buttons)
        target_ids = set(ordered_ids)

        for item_id in current_ids - target_ids:
            LOGGER.debug("Removing tray button for %s", item_id)
            button = self._buttons.pop(item_id)
            self._layout.removeWidget(button)
            button.deleteLater()

        for item_id in ordered_ids:
            if item_id not in self._buttons:
                LOGGER.debug("Creating tray button for %s", item_id)
                button = StatusNotifierButton(item_id, self)
                self._buttons[item_id] = button
                self._layout.addWidget(button)

        status_parts: list[str] = []
        if self._local_watcher is not None:
            status_parts.append(self._local_watcher.status_text())
        elif self._watcher_service:
            status_parts.append(f"Using remote tray watcher {self._watcher_service}")
        else:
            status_parts.append("No tray watcher service available")
        if self._tray_selection_manager is not None:
            status_parts.append(self._tray_selection_manager.status_text())
        self.setToolTip("\n".join(status_parts))

    def _remote_registered_items(self) -> list[str]:
        if self._watcher_props is None or not self._watcher_props.isValid():
            return []
        for interface_name in WATCHER_INTERFACES:
            reply = self._watcher_props.call("Get", interface_name, "RegisteredStatusNotifierItems")
            if reply.type() == QDBusMessage.ErrorMessage or not reply.arguments():
                continue
            raw_items = dbus_to_python(reply.arguments()[0])
            if isinstance(raw_items, (list, tuple)):
                return [str(item) for item in raw_items if str(item).strip()]
        return []

    def _refresh_watcher_interface(self) -> None:
        if self._local_watcher is not None and self._local_watcher.is_active:
            target_service = self._local_watcher.primary_service_name or WATCHER_SERVICES[0]
        else:
            target_service = discover_watcher_service(self._bus)

        if target_service == self._watcher_service and self._watcher_props is not None:
            return
        self._watcher_service = target_service
        if target_service:
            LOGGER.debug("Using tray watcher service %s", target_service)
            self._watcher_props = QDBusInterface(target_service, WATCHER_PATH, PROPERTIES_INTERFACE, self._bus)
            return
        self._watcher_props = None
