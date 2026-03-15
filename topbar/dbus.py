from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from typing import Any

from PySide6.QtCore import QObject, SLOT
from PySide6.QtDBus import (
    QDBusArgument,
    QDBusConnection,
    QDBusConnectionInterface,
    QDBusError,
    QDBusObjectPath,
    QDBusSignature,
    QDBusVariant,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .constants import BUSCTL, ITEM_INTERFACE, MENU_INTERFACE, NOTIFICATIONS_SERVICE, WATCHER_SERVICES

LOGGER = logging.getLogger("topbar.dbus")
_DBUS_STARTUP_SNAPSHOT_LOGGED = False


def topbar_instance_name(entrypoint_path: str) -> str:
    canonical = os.path.realpath(entrypoint_path).lower().encode("utf-8", errors="replace")
    digest = hashlib.sha1(canonical).hexdigest()
    return f"pytpo-topbar-{digest}"


def topbar_instance_running(server_name: str, timeout_ms: int = 150) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    connected = socket.waitForConnected(timeout_ms)
    if connected:
        socket.disconnectFromServer()
    return connected


class SingleInstanceGuard(QObject):
    def __init__(self, server_name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server_name = server_name
        self.server = QLocalServer(self)

    def acquire(self) -> bool:
        if topbar_instance_running(self.server_name):
            return False
        if self.server.listen(self.server_name):
            return True
        if topbar_instance_running(self.server_name):
            return False

        QLocalServer.removeServer(self.server_name)
        return self.server.listen(self.server_name)

    def close(self) -> None:
        try:
            self.server.close()
        finally:
            QLocalServer.removeServer(self.server_name)


def dbus_slot(signature: str) -> bytes:
    return SLOT(signature).encode()


def try_connect_dbus_signal(bus, service, path, interface, name, receiver, slot_signature) -> bool:
    try:
        return bus.connect(service, path, interface, name, receiver, dbus_slot(slot_signature))
    except (TypeError, ValueError) as exc:
        LOGGER.debug(
            "Failed to connect DBus signal %s.%s at %s from %s: %r",
            interface,
            name,
            path,
            service,
            exc,
        )
        return False


def dbus_error_text(error_or_bus: QDBusError | QDBusConnection | None) -> str:
    if isinstance(error_or_bus, QDBusConnection):
        error = error_or_bus.lastError()
    else:
        error = error_or_bus
    if error is None:
        return "unknown DBus error"
    name = ""
    message = ""
    try:
        name = str(error.name() or "")
    except Exception:
        name = ""
    try:
        message = str(error.message() or "")
    except Exception:
        message = ""
    if name and message:
        return f"{name}: {message}"
    return name or message or "unknown DBus error"


def bus_base_service_name(bus: QDBusConnection) -> str:
    try:
        return str(bus.baseService() or "")
    except Exception:
        return ""


def service_owner_name(bus: QDBusConnection, service_name: str) -> str:
    if not bus.isConnected():
        return ""
    bus_interface = bus.interface()
    if bus_interface is None:
        return ""
    try:
        reply = bus_interface.serviceOwner(service_name)
    except Exception as exc:
        LOGGER.debug("serviceOwner lookup failed for %s: %r", service_name, exc)
        return ""
    if reply.isValid():
        return str(reply.value() or "")
    return ""


def service_pid(bus: QDBusConnection, service_name: str) -> int:
    if not bus.isConnected():
        return 0
    bus_interface = bus.interface()
    if bus_interface is None:
        return 0
    try:
        reply = bus_interface.servicePid(service_name)
    except Exception as exc:
        LOGGER.debug("servicePid lookup failed for %s: %r", service_name, exc)
        return 0
    if reply.isValid():
        return intish(reply.value(), default=0)
    return 0


def process_executable_path(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except Exception:
        return ""


def service_owner_details(bus: QDBusConnection, service_name: str) -> dict[str, Any]:
    owner = service_owner_name(bus, service_name)
    pid = service_pid(bus, owner) if owner else 0
    if pid <= 0 and service_name:
        pid = service_pid(bus, service_name)
    executable = process_executable_path(pid)
    return {
        "service": service_name,
        "owner": owner,
        "pid": pid,
        "executable": executable,
    }


def format_service_owner_details(details: dict[str, Any]) -> str:
    owner = str(details.get("owner") or "")
    pid = intish(details.get("pid"), default=0)
    executable = str(details.get("executable") or "")
    if not owner:
        return "owner=<none> pid=<none> exe=<none>"
    return f"owner={owner} pid={pid or '<none>'} exe={executable or '<none>'}"


def register_service_reply_name(result: Any) -> str:
    code = register_service_reply_code(result)
    names = {
        0: "ServiceNotRegistered",
        1: "ServiceRegistered",
        2: "ServiceQueued",
    }
    if code in names:
        return names[code]
    return str(getattr(result, "name", result))


def register_service_reply_code(result: Any) -> int:
    raw_value = getattr(result, "value", result)
    return intish(raw_value, default=-1)


def virtual_object_mode_name(option: Any) -> str:
    return str(getattr(option, "name", option))


def message_type_name(message: QDBusMessage) -> str:
    try:
        return str(message.type().name)
    except Exception:
        try:
            return str(message.type())
        except Exception:
            return "unknown"


def preview_dbus_value(value: Any, *, max_length: int = 400) -> str:
    text = repr(dbus_to_python(value))
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text


def format_dbus_message_summary(message: QDBusMessage) -> str:
    try:
        arguments = message.arguments()
        argument_count = len(arguments)
    except Exception:
        argument_count = -1
    return (
        f"path={message.path() or '<none>'} "
        f"interface={message.interface() or '<none>'} "
        f"member={message.member() or '<none>'} "
        f"sender={message.service() or '<none>'} "
        f"type={message_type_name(message)} "
        f"argc={argument_count}"
    )


def format_dbus_message_arguments(message: QDBusMessage, *, max_length: int = 400) -> str:
    try:
        return preview_dbus_value(message.arguments(), max_length=max_length)
    except Exception as exc:
        return f"<failed to decode arguments: {exc!r}>"


def log_dbus_environment_snapshot(bus: QDBusConnection) -> None:
    global _DBUS_STARTUP_SNAPSHOT_LOGGED
    if _DBUS_STARTUP_SNAPSHOT_LOGGED:
        return
    _DBUS_STARTUP_SNAPSHOT_LOGGED = True

    connected = bus.isConnected()
    base_service = bus_base_service_name(bus)
    LOGGER.info(
        "DBus environment snapshot connected=%s base_service=%s pid=%s",
        connected,
        base_service or "<none>",
        os.getpid(),
    )
    for service_name in (NOTIFICATIONS_SERVICE, *WATCHER_SERVICES):
        details = service_owner_details(bus, service_name)
        LOGGER.info(
            "DBus environment owner service=%s %s",
            service_name,
            format_service_owner_details(details),
        )


def discover_watcher_service(bus: QDBusConnection) -> str:
    if not bus.isConnected():
        return ""
    bus_interface = bus.interface()
    if bus_interface is None:
        return ""
    try:
        reply = bus_interface.registeredServiceNames()
        if reply.isValid():
            service_names = {str(name) for name in reply.value()}
            for candidate in WATCHER_SERVICES:
                if candidate in service_names:
                    return candidate
    except Exception as exc:
        LOGGER.debug("Failed to inspect watcher services: %r", exc)
    return ""


def claim_dbus_service(bus: QDBusConnection, service_name: str) -> tuple[bool, str]:
    log_dbus_environment_snapshot(bus)

    connected = bus.isConnected()
    base_service = bus_base_service_name(bus)
    existing_owner = service_owner_details(bus, service_name)
    LOGGER.info(
        "DBus service claim attempt service=%s connected=%s base_service=%s existing_%s",
        service_name,
        connected,
        base_service or "<none>",
        format_service_owner_details(existing_owner),
    )

    if not bus.isConnected():
        return False, "session bus is not connected"
    bus_interface = bus.interface()
    if bus_interface is None:
        return False, "session bus interface is unavailable"

    try:
        reply = bus_interface.registerService(
            service_name,
            QDBusConnectionInterface.ServiceQueueOptions.ReplaceExistingService,
            QDBusConnectionInterface.ServiceReplacementOptions.AllowReplacement,
        )
    except Exception as exc:
        LOGGER.error("DBus registerService raised service=%s error=%r", service_name, exc)
        return False, f"failed to register {service_name}: {exc!r}"

    if not reply.isValid():
        error = reply.error() if reply.error().isValid() else bus.lastError()
        owner_details = service_owner_details(bus, service_name)
        owner = str(owner_details.get("owner") or "")
        owner_pid = intish(owner_details.get("pid"), default=0)
        if owner and (owner == base_service or owner_pid == os.getpid()):
            ownership_reason = "already owned by this process"
        elif owner:
            ownership_reason = "already owned by another process"
        else:
            ownership_reason = "generic register failure"
        LOGGER.error(
            "DBus registerService failed service=%s valid=%s error=%s ownership_reason=%s current_%s",
            service_name,
            reply.isValid(),
            dbus_error_text(error),
            ownership_reason,
            format_service_owner_details(owner_details),
        )
        return False, (
            f"failed to register {service_name}: {ownership_reason}; "
            f"{format_service_owner_details(owner_details)}; qt_error={dbus_error_text(error)}"
        )

    result = reply.value()
    result_code = register_service_reply_code(result)
    result_name = register_service_reply_name(result)
    LOGGER.info(
        "DBus registerService result service=%s result=%s code=%s connected=%s base_service=%s last_error=%s",
        service_name,
        result_name,
        result_code,
        connected,
        base_service or "<none>",
        dbus_error_text(bus),
    )
    if result_code == 1:
        owner_details = service_owner_details(bus, service_name)
        LOGGER.info(
            "Claimed D-Bus service %s current_%s",
            service_name,
            format_service_owner_details(owner_details),
        )
        return True, ""
    if result_code == 2:
        owner_details = service_owner_details(bus, service_name)
        owner = str(owner_details.get("owner") or "")
        owner_pid = intish(owner_details.get("pid"), default=0)
        if owner and (owner == base_service or owner_pid == os.getpid()):
            ownership_reason = "already owned by this process"
        elif owner:
            ownership_reason = "already owned by another process"
        else:
            ownership_reason = "generic register failure"
        LOGGER.warning(
            "DBus service queued service=%s ownership_reason=%s current_%s",
            service_name,
            ownership_reason,
            format_service_owner_details(owner_details),
        )
        return False, (
            f"{service_name} was queued instead of acquired: {ownership_reason}; "
            f"{format_service_owner_details(owner_details)}"
        )

    if result_code == 0:
        ownership_reason = "generic register failure"
    else:
        ownership_reason = "generic register failure"
    owner_details = service_owner_details(bus, service_name)
    owner = str(owner_details.get("owner") or "")
    owner_pid = intish(owner_details.get("pid"), default=0)
    if owner and (owner == base_service or owner_pid == os.getpid()):
        ownership_reason = "already owned by this process"
    elif owner:
        ownership_reason = "already owned by another process"
    else:
        ownership_reason = "generic register failure"
    LOGGER.error(
        "DBus service claim unsuccessful service=%s result=%s ownership_reason=%s current_%s last_error=%s",
        service_name,
        f"{result_name}({result_code})",
        ownership_reason,
        format_service_owner_details(owner_details),
        dbus_error_text(bus),
    )
    return False, (
        f"failed to register {service_name}: {ownership_reason}; "
        f"{format_service_owner_details(owner_details)}; "
        f"result={result_name}({result_code}); qt_error={dbus_error_text(bus)}"
    )


def dbus_to_python(value: Any) -> Any:
    if isinstance(value, QDBusVariant):
        return dbus_to_python(value.variant())
    if isinstance(value, QDBusObjectPath):
        return value.path()
    if isinstance(value, QDBusSignature):
        return value.signature()
    if isinstance(value, QDBusArgument):
        try:
            return dbus_to_python(value.asVariant())
        except Exception:
            return value
    if isinstance(value, dict):
        return {str(dbus_to_python(key)): dbus_to_python(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [dbus_to_python(item) for item in value]
    if isinstance(value, str):
        return value
    return value


def extract_service_and_path(identifier: str) -> tuple[str, str]:
    if not identifier:
        return "", "/StatusNotifierItem"
    if identifier.startswith("/"):
        return "", identifier
    slash_index = identifier.find("/", 1)
    if slash_index == -1:
        return identifier, "/StatusNotifierItem"
    return identifier[:slash_index], identifier[slash_index:]


def run_busctl_json(*args: str, timeout: float = 1.5) -> Any | None:
    if not BUSCTL:
        return None
    try:
        result = subprocess.run(
            [BUSCTL, "--user", "--json=short", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        LOGGER.debug("busctl %s failed: %r", " ".join(args), exc)
        return None
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        LOGGER.debug("busctl %s exited with %s: %s", " ".join(args), result.returncode, stderr)
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        LOGGER.debug("Failed to decode busctl JSON for %s: %r", " ".join(args), exc)
        return None


def unwrap_busctl_json(value: Any) -> Any:
    if isinstance(value, dict):
        if "data" in value and ("type" in value or len(value) == 1):
            return unwrap_busctl_json(value["data"])
        return {str(key): unwrap_busctl_json(val) for key, val in value.items()}
    if isinstance(value, list):
        return [unwrap_busctl_json(item) for item in value]
    return value


def fetch_property_via_busctl(service: str, path: str, interface: str, prop: str) -> Any | None:
    data = run_busctl_json("get-property", service, path, interface, prop, timeout=1)
    if data is None:
        return None
    return unwrap_busctl_json(data.get("data"))


def boolish(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return boolish(value[0], default=default)
    text = str(value).strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return default


def intish(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_menu_label(value: Any) -> str:
    return str(value or "").replace("_", "").strip()


def resource_id(value: Any) -> int:
    if value is None:
        return 0
    value_id = getattr(value, "id", None)
    if value_id is not None:
        try:
            return int(value_id)
        except (TypeError, ValueError):
            return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def load_xlib():
    try:
        from Xlib import X, Xatom, display
    except Exception as exc:
        raise RuntimeError("python-xlib is required for X11 tray ownership.") from exc
    return X, Xatom, display


def normalize_menu_properties(raw_props: Any) -> dict[str, Any]:
    props = unwrap_busctl_json(raw_props)
    if not isinstance(props, dict):
        return {}
    return {str(key).lower(): unwrap_busctl_json(val) for key, val in props.items()}


def normalize_menu_layout(raw_layout: Any) -> dict[str, Any] | None:
    layout = unwrap_busctl_json(raw_layout)
    if not isinstance(layout, (list, tuple)) or len(layout) < 2:
        return None
    children: list[dict[str, Any]] = []
    raw_children = layout[2] if len(layout) > 2 else []
    if isinstance(raw_children, (list, tuple)):
        for child in raw_children:
            normalized_child = normalize_menu_layout(child)
            if normalized_child is not None:
                children.append(normalized_child)
    return {
        "id": intish(layout[0]),
        "properties": normalize_menu_properties(layout[1]),
        "children": children,
    }


def menu_property(props: dict[str, Any], name: str, default: Any = None) -> Any:
    return props.get(name.lower(), default)


def launch_background_command(*command: str) -> tuple[bool, str]:
    try:
        subprocess.Popen(
            list(command),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        return False, f"{' '.join(command)} failed: {exc!r}"
    return True, " ".join(command)


def run_logout_command() -> tuple[bool, str]:
    commands: list[list[str]] = []
    if shutil.which("gnome-session-quit"):
        commands.append(["gnome-session-quit", "--logout", "--no-prompt"])

    session_id = os.environ.get("XDG_SESSION_ID", "").strip()
    if shutil.which("loginctl") and session_id:
        commands.append(["loginctl", "terminate-session", session_id])

    last_error = "no supported logout command was found"
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception as exc:
            last_error = f"{' '.join(command)} failed: {exc!r}"
            continue
        if result.returncode == 0:
            return True, " ".join(command)
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        last_error = stderr or stdout or f"{' '.join(command)} exited with {result.returncode}"
    return False, last_error
