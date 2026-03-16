from __future__ import annotations

import ast
import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QByteArray, QPoint, QSize, Qt
from PySide6.QtDBus import (
    QDBusArgument,
    QDBusConnection,
    QDBusInterface,
    QDBusMessage,
    QDBusObjectPath,
    QDBusSignature,
    QDBusVariant,
)
from PySide6.QtGui import QAction, QIcon, QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QToolButton, QWidget



from pathlib import Path
WATCHER_SERVICES = (
    "org.kde.StatusNotifierWatcher",
    "org.freedesktop.StatusNotifierWatcher",
)
WATCHER_INTERFACES = WATCHER_SERVICES
WATCHER_PATH = "/StatusNotifierWatcher"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
ITEM_INTERFACE = "org.kde.StatusNotifierItem"
MENU_INTERFACE = "com.canonical.dbusmenu"
DEFAULT_ITEM_PATH = "/StatusNotifierItem"
PREFERRED_ICON_SIZE = 22
BUSCTL = shutil.which("busctl")


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
            return None
    if isinstance(value, dict):
        return {str(dbus_to_python(key)): dbus_to_python(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [dbus_to_python(item) for item in value]
    return value


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def to_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, QByteArray):
        return bytes(value)
    if isinstance(value, list):
        try:
            return bytes(value)
        except ValueError:
            return b""
    return b""


def split_item_id(item_id: str) -> tuple[str, str]:
    text = item_id.strip()
    if not text:
        return "", DEFAULT_ITEM_PATH
    if text.startswith("/"):
        return "", text
    slash_index = text.find("/", 1 if text.startswith(":") else 0)
    if slash_index < 0:
        return text, DEFAULT_ITEM_PATH
    return text[:slash_index], text[slash_index:]


def discover_watcher_service(bus: QDBusConnection) -> str:
    bus_interface = bus.interface()
    if bus_interface is None:
        return ""
    reply = bus_interface.registeredServiceNames()
    if not reply.isValid():
        return ""
    registered = {str(name) for name in reply.value()}
    for service in WATCHER_SERVICES:
        if service in registered:
            return service
    return ""


def call_property(bus: QDBusConnection, service: str, path: str, interface: str, name: str) -> Any:
    if not service or not path:
        return None
    properties = QDBusInterface(service, path, PROPERTIES_INTERFACE, bus)
    if not properties.isValid():
        return None
    reply = properties.call("Get", interface, name)
    if reply.type() == QDBusMessage.ErrorMessage or not reply.arguments():
        return None
    return dbus_to_python(reply.arguments()[0])


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
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def call_property_via_busctl(service: str, path: str, interface: str, name: str) -> Any:
    if not service or not path:
        return None
    data = run_busctl_json("get-property", service, path, interface, name, timeout=1)
    if not isinstance(data, dict):
        return None
    return data.get("data")


def registered_item_ids(bus: QDBusConnection, watcher_service: str) -> list[str]:
    if not watcher_service:
        return []
    for interface_name in WATCHER_INTERFACES:
        value = call_property(
            bus,
            watcher_service,
            WATCHER_PATH,
            interface_name,
            "RegisteredStatusNotifierItems",
        )
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
    return []


def decode_icon_pixmaps(value: Any) -> QIcon:
    pixmaps = dbus_to_python(value)
    if not isinstance(pixmaps, (list, tuple)):
        return QIcon()

    candidates: list[tuple[int, int, bytes]] = []
    for entry in pixmaps:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            continue
        width = to_int(entry[0])
        height = to_int(entry[1])
        raw = to_bytes(entry[2])
        if width <= 0 or height <= 0 or len(raw) < width * height * 4:
            continue

        reordered = bytearray(len(raw))
        reordered[0::4] = raw[3::4]
        reordered[1::4] = raw[2::4]
        reordered[2::4] = raw[1::4]
        reordered[3::4] = raw[0::4]
        candidates.append((width, height, bytes(reordered)))

    if not candidates:
        return QIcon()

    candidates.sort(key=lambda item: abs(max(item[0], item[1]) - PREFERRED_ICON_SIZE))
    icon = QIcon()
    for width, height, raw in candidates:
        image = QImage(raw, width, height, width * 4, QImage.Format_ARGB32)
        if not image.isNull():
            icon.addPixmap(QPixmap.fromImage(image.copy()))
    return icon


def pleasant_fallback_icon() -> tuple[QIcon, str]:
    for icon_name in (
        "application-x-executable",
        "application-default-icon",
        "applications-other",
        "applications-system",
    ):
        icon = QIcon.fromTheme(icon_name)
        if not icon.isNull():
            return icon, icon_name

    fallback = QApplication.style().standardIcon(QStyle.SP_DesktopIcon)
    if fallback.isNull():
        fallback = QApplication.style().standardIcon(QStyle.SP_FileIcon)
    return fallback, ""


def choose_icon(bus: QDBusConnection, service: str, path: str) -> tuple[QIcon, str]:
    icon_name = str(call_property(bus, service, path, ITEM_INTERFACE, "IconName") or "").strip()
    if icon_name:
        if icon_name.startswith("/"):
            absolute_icon = QIcon(icon_name)
            if not absolute_icon.isNull():
                return absolute_icon, icon_name
        themed_icon = QIcon.fromTheme(icon_name)
        if not themed_icon.isNull():
            return themed_icon, icon_name

    pixmap_value = call_property(bus, service, path, ITEM_INTERFACE, "IconPixmap")
    if pixmap_value is None:
        pixmap_value = call_property_via_busctl(service, path, ITEM_INTERFACE, "IconPixmap")

    pixmap_icon = decode_icon_pixmaps(pixmap_value)
    if not pixmap_icon.isNull():
        return pixmap_icon, icon_name

    fallback, fallback_name = pleasant_fallback_icon()
    return fallback, icon_name or fallback_name


def tooltip_title(value: Any) -> str:
    tooltip = dbus_to_python(value)
    if not isinstance(tooltip, (list, tuple)) or len(tooltip) < 4:
        return ""
    title = str(tooltip[2] or "").strip()
    description = str(tooltip[3] or "").strip()
    return title or description


def best_text(bus: QDBusConnection, service: str, path: str) -> str:
    title = str(call_property(bus, service, path, ITEM_INTERFACE, "Title") or "").strip()
    if title:
        return title

    tip_title = tooltip_title(call_property(bus, service, path, ITEM_INTERFACE, "ToolTip"))
    if tip_title:
        return tip_title

    item_id = str(call_property(bus, service, path, ITEM_INTERFACE, "Id") or "").strip()
    if item_id:
        return item_id

    return service or path


def call_item_method(
    bus: QDBusConnection,
    service: str,
    path: str,
    method: str,
    *args: Any,
) -> tuple[bool, str]:
    iface = QDBusInterface(service, path, ITEM_INTERFACE, bus)
    if not iface.isValid():
        return False, "Item interface is not valid."

    reply = iface.call(method, *args)
    if reply.type() == QDBusMessage.ErrorMessage:
        return False, str(reply.errorMessage() or "Unknown D-Bus error")
    return True, ""


def _strip_gvariant_wrappers(text: str) -> str:
    replacements = {
        "true": "True",
        "false": "False",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    while "<" in text and ">" in text:
        start = text.find("<")
        end = text.find(">", start)
        if start < 0 or end < 0:
            break
        inner = text[start + 1:end]
        text = text[:start] + inner + text[end + 1:]

    text = text.replace("@av []", "[]")
    text = text.replace("uint32 ", "")
    return text


def parse_gdbus_getlayout_stdout(stdout: str) -> dict[str, Any] | None:
    """
    Parses gdbus output like:
        (1, (0, {'children-display': 'submenu'}, [ ... ]))
    after wrapper cleanup.
    """
    text = stdout.strip()
    if not text:
        return None

    cleaned = _strip_gvariant_wrappers(text)
    try:
        parsed = ast.literal_eval(cleaned)
    except Exception:
        return None

    if not isinstance(parsed, tuple) or len(parsed) != 2:
        return None

    _revision, root = parsed
    return _normalize_gdbus_menu_node(root)


def _normalize_gdbus_menu_node(node: Any) -> dict[str, Any] | None:
    if not isinstance(node, tuple) or len(node) != 3:
        return None

    node_id, props, children = node
    if not isinstance(props, dict):
        props = {}
    if not isinstance(children, list):
        children = []

    normalized_children: list[dict[str, Any]] = []
    for child in children:
        normalized = _normalize_gdbus_menu_node(child)
        if normalized is not None:
            normalized_children.append(normalized)

    return {
        "id": to_int(node_id, -1),
        "properties": props,
        "children": normalized_children,
    }

@dataclass(slots=True)
class ResolvedTrayIcon:
    icon: QIcon
    source: str
    icon_name: str = ""
    file_path: str = ""




class TrayIconResolver:
    def __init__(self, bus: QDBusConnection) -> None:
        self.bus = bus
        self._cache: dict[tuple[str, str, str], ResolvedTrayIcon] = {}

        home = Path.home()
        self.search_dirs: list[Path] = [
            Path("/usr/share/pixmaps"),
            home / ".local/share/pixmaps",
            home / ".local/share/icons",
            home / ".icons",
            home / ".local/share/Steam/public",
            home / ".steam",
        ]

    def resolve_for_item(self, service: str, path: str) -> ResolvedTrayIcon:
        status = str(call_property(self.bus, service, path, ITEM_INTERFACE, "Status") or "").strip()
        cache_key = (service, path, status)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        icon_name = str(call_property(self.bus, service, path, ITEM_INTERFACE, "IconName") or "").strip()
        overlay_name = str(call_property(self.bus, service, path, ITEM_INTERFACE, "OverlayIconName") or "").strip()
        attention_name = str(call_property(self.bus, service, path, ITEM_INTERFACE, "AttentionIconName") or "").strip()

        icon_pixmap = self._pixmap_property(service, path, "IconPixmap")
        overlay_pixmap = self._pixmap_property(service, path, "OverlayIconPixmap")
        attention_pixmap = self._pixmap_property(service, path, "AttentionIconPixmap")
        # 1. Try primary name
        if icon_name:
            resolved = self._resolve_name(icon_name)
            if resolved is not None:
                self._cache[cache_key] = resolved
                return resolved

        # 2. Try primary pixmap
        resolved = self._resolve_pixmap(icon_pixmap, source="IconPixmap")
        if resolved is not None:
            self._cache[cache_key] = resolved
            return resolved

        # 3. Try attention name/pixmap
        if attention_name:
            resolved = self._resolve_name(attention_name, source_prefix="AttentionIconName")
            if resolved is not None:
                self._cache[cache_key] = resolved
                return resolved

        resolved = self._resolve_pixmap(attention_pixmap, source="AttentionIconPixmap")
        if resolved is not None:
            self._cache[cache_key] = resolved
            return resolved

        # 4. Try overlay name/pixmap
        if overlay_name:
            resolved = self._resolve_name(overlay_name, source_prefix="OverlayIconName")
            if resolved is not None:
                self._cache[cache_key] = resolved
                return resolved

        resolved = self._resolve_pixmap(overlay_pixmap, source="OverlayIconPixmap")
        if resolved is not None:
            self._cache[cache_key] = resolved
            return resolved

        # 5. Final fallback
        fallback, fallback_name = pleasant_fallback_icon()
        resolved = ResolvedTrayIcon(
            icon=fallback,
            source="fallback",
            icon_name=fallback_name,
        )
        self._cache[cache_key] = resolved
        return resolved

    def _pixmap_property(self, service: str, path: str, name: str) -> Any:
        value = call_property(self.bus, service, path, ITEM_INTERFACE, name)
        resolved = self._resolve_pixmap(value, source=name)
        if resolved is not None:
            return value
        busctl_value = call_property_via_busctl(service, path, ITEM_INTERFACE, name)
        if busctl_value is not None:
            return busctl_value
        return value

    def _resolve_name(self, icon_name: str, source_prefix: str = "IconName") -> ResolvedTrayIcon | None:
        # absolute path
        if icon_name.startswith("/"):
            icon = QIcon(icon_name)
            if not icon.isNull():
                return ResolvedTrayIcon(
                    icon=icon,
                    source=f"{source_prefix}:absolute-path",
                    icon_name=icon_name,
                    file_path=icon_name,
                )

        # standard theme lookup
        icon = QIcon.fromTheme(icon_name)
        if not icon.isNull():
            return ResolvedTrayIcon(
                icon=icon,
                source=f"{source_prefix}:theme",
                icon_name=icon_name,
            )

        # loose file fallback
        file_path = self._find_icon_file(icon_name)
        if file_path is not None:
            icon = QIcon(str(file_path))
            if not icon.isNull():
                return ResolvedTrayIcon(
                    icon=icon,
                    source=f"{source_prefix}:file-fallback",
                    icon_name=icon_name,
                    file_path=str(file_path),
                )

        return None

    def _find_icon_file(self, icon_name: str) -> Path | None:
        """
        Try loose file locations for non-theme icon names like steam_tray_mono.
        """
        candidates = [
            icon_name,
            f"{icon_name}.png",
            f"{icon_name}.svg",
            f"{icon_name}.xpm",
            f"{icon_name}.ico",
        ]

        for directory in self.search_dirs:
            if not directory.exists():
                continue

            for candidate in candidates:
                path = directory / candidate
                if path.is_file():
                    return path

        # Slightly broader search for stubborn names
        for directory in self.search_dirs:
            if not directory.exists() or not directory.is_dir():
                continue
            for ext in ("png", "svg", "xpm", "ico"):
                matches = list(directory.rglob(f"{icon_name}.{ext}"))
                if matches:
                    return matches[0]

        return None

    def _resolve_pixmap(self, value: Any, source: str) -> ResolvedTrayIcon | None:
        icon = self._decode_icon_pixmaps(value)
        if icon.isNull():
            return None
        return ResolvedTrayIcon(
            icon=icon,
            source=source,
        )

    def _decode_icon_pixmaps(self, value: Any) -> QIcon:
        pixmaps = dbus_to_python(value)
        if not isinstance(pixmaps, (list, tuple)) or not pixmaps:
            return QIcon()
    
        candidates: list[tuple[int, int, bytes]] = []
        for entry in pixmaps:
            if not isinstance(entry, (list, tuple)) or len(entry) != 3:
                continue
    
            width = to_int(entry[0])
            height = to_int(entry[1])
            raw = to_bytes(entry[2])
            if width <= 0 or height <= 0:
                continue
            if len(raw) < width * height * 4:
                continue
    
            candidates.append((width, height, raw))
    
        if not candidates:
            return QIcon()
    
        candidates.sort(key=lambda item: abs(max(item[0], item[1]) - PREFERRED_ICON_SIZE))
    
        icon = QIcon()
        for width, height, raw in candidates:
            pixmap = self._pixmap_from_sni_bytes(raw, width, height)
            if pixmap is not None and not pixmap.isNull():
                icon.addPixmap(pixmap)
    
        return icon
        
    def _pixmap_from_sni_bytes(self, raw: bytes, width: int, height: int) -> QPixmap | None:
        expected = width * height * 4
        if len(raw) < expected:
            return None
        return self._pixmap_from_argb32(raw[:expected], width, height)
        
    def _pixmap_from_argb32(self, raw: bytes, width: int, height: int) -> QPixmap | None:
        """
        StatusNotifier IconPixmap is ARGB32 big-endian style data.
        QImage on little-endian machines usually wants BGRA byte order for Format_ARGB32.
        We try both sensible interpretations and keep the first non-null result.
        """
        if len(raw) < width * height * 4:
            return None

        # Attempt 1: reorder ARGB -> BGRA for QImage.Format_ARGB32
        bgra = bytearray(len(raw))
        bgra[0::4] = raw[3::4]  # B
        bgra[1::4] = raw[2::4]  # G
        bgra[2::4] = raw[1::4]  # R
        bgra[3::4] = raw[0::4]  # A

        image = QImage(bytes(bgra), width, height, width * 4, QImage.Format_ARGB32)
        if not image.isNull():
            pixmap = QPixmap.fromImage(image.copy())
            if not pixmap.isNull():
                return pixmap

        # Attempt 2: raw as RGBA8888 just in case the sender/backend is quirky
        image = QImage(raw, width, height, width * 4, QImage.Format_RGBA8888)
        if not image.isNull():
            pixmap = QPixmap.fromImage(image.copy())
            if not pixmap.isNull():
                return pixmap

        return None

@dataclass(slots=True)
class CompletedTrayItem:
    bus: QDBusConnection
    item_id: str
    service: str
    path: str
    menu_path: str
    item_is_menu: bool
    title: str
    status: str
    icon_name: str
    icon: QIcon

    @property
    def is_visible(self) -> bool:
        return self.status.strip().lower() != "passive"

    @property
    def has_menu_path(self) -> bool:
        return self.menu_path.startswith("/")

    def _menu_interface(self) -> QDBusInterface | None:
        if not self.has_menu_path:
            return None
        iface = QDBusInterface(self.service, self.menu_path, MENU_INTERFACE, self.bus)
        if not iface.isValid():
            return None
        return iface

    def activate_at_global(self, global_pos: QPoint) -> tuple[bool, str]:
        return call_item_method(
            self.bus,
            self.service,
            self.path,
            "Activate",
            global_pos.x(),
            global_pos.y(),
        )

    def secondary_activate_at_global(self, global_pos: QPoint) -> tuple[bool, str]:
        return call_item_method(
            self.bus,
            self.service,
            self.path,
            "SecondaryActivate",
            global_pos.x(),
            global_pos.y(),
        )

    def show_native_menu_at_global(self, global_pos: QPoint) -> tuple[bool, str]:
        return call_item_method(
            self.bus,
            self.service,
            self.path,
            "ContextMenu",
            global_pos.x(),
            global_pos.y(),
        )

    def _run_gdbus_menu_call(self, method: str, extra_args: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = [
            "gdbus",
            "call",
            "--session",
            "--dest", self.service,
            "--object-path", self.menu_path,
            "--method", f"{MENU_INTERFACE}.{method}",
            "--",
            *extra_args,
        ]
        return subprocess.run(cmd, capture_output=True, text=True)

    def _about_to_show_menu_id(self, menu_id: int) -> tuple[bool, str]:
        iface = self._menu_interface()
        if iface is not None:
            reply = iface.call("AboutToShow", menu_id)
            if reply.type() != QDBusMessage.ErrorMessage:
                return True, ""

        data = run_busctl_json(
            "call",
            self.service,
            self.menu_path,
            MENU_INTERFACE,
            "AboutToShow",
            "i",
            str(menu_id),
            timeout=1,
        )
        if data is not None:
            return True, ""
        return False, "AboutToShow failed."

    def fetch_menu_layout(self, parent_id: int = 0, recursion_depth: int = -1) -> tuple[bool, dict[str, Any] | None, str]:
        if not self.has_menu_path:
            return False, None, "This item does not expose a Menu object path."

        self._about_to_show_menu_id(parent_id)

        layout = self._run_gdbus_menu_call("GetLayout", [str(parent_id), str(recursion_depth), "[]"])
        if layout.returncode != 0:
            return False, None, layout.stderr.strip() or "GetLayout failed."

        parsed = parse_gdbus_getlayout_stdout(layout.stdout)
        if parsed is None:
            return False, None, "Could not parse gdbus GetLayout output."

        return True, parsed, layout.stdout

    def click_menu_id(self, menu_id: int) -> tuple[bool, str]:
        if not self.has_menu_path:
            return False, "This item does not expose a Menu object path."
        if menu_id < 0:
            return False, "Invalid menu item id."

        iface = self._menu_interface()
        if iface is not None:
            timestamp = 0
            reply = iface.call("Event", menu_id, "clicked", QDBusVariant(""), timestamp)
            if reply.type() != QDBusMessage.ErrorMessage:
                return True, ""

        data = run_busctl_json(
            "call",
            self.service,
            self.menu_path,
            MENU_INTERFACE,
            "Event",
            "isvu",
            str(menu_id),
            "clicked",
            "s",
            "",
            "0",
            timeout=1,
        )
        if data is not None:
            return True, ""
        return False, "dbusmenu Event failed."

    def build_qmenu(self, parent: QWidget | None = None) -> tuple[QMenu | None, str]:
        ok, layout, message = self.fetch_menu_layout()
        if not ok or layout is None:
            return None, message

        menu = QMenu(parent)
        self._populate_qmenu(menu, layout)
        return menu, ""

    def _populate_qmenu(self, menu: QMenu, node: dict[str, Any]) -> None:
        for child in node.get("children", []):
            props = child.get("properties", {})
            if not to_bool(props.get("visible", True), True):
                continue

            item_type = str(props.get("type") or "").strip().lower()
            if item_type == "separator":
                menu.addSeparator()
                continue

            label = str(props.get("label") or "Menu item").replace("_", "").strip() or "Menu item"
            enabled = to_bool(props.get("enabled", True), True)
            children_display = str(props.get("children-display") or "").strip().lower()
            children = child.get("children", [])
            is_submenu = children_display == "submenu" or bool(children)

            if is_submenu:
                submenu = menu.addMenu(label)
                submenu.setEnabled(enabled)
                self._populate_qmenu(submenu, child)
                menu_id = to_int(child.get("id"), -1)
                if menu_id >= 0:
                    submenu.aboutToShow.connect(
                        lambda menu_obj=submenu, item_id=menu_id: self._refresh_submenu(menu_obj, item_id)
                    )
                continue

            action = QAction(label, menu)
            action.setEnabled(enabled)

            toggle_type = str(props.get("toggle-type") or "").strip().lower()
            if toggle_type in {"checkmark", "radio"}:
                action.setCheckable(True)
                action.setChecked(to_int(props.get("toggle-state"), 0) == 1)

            menu_id = to_int(child.get("id"), -1)
            action.triggered.connect(
                lambda checked=False, menu_id=menu_id: self.click_menu_id(menu_id)
            )
            menu.addAction(action)

    def _refresh_submenu(self, menu: QMenu, parent_id: int) -> None:
        ok, node, _message = self.fetch_menu_layout(parent_id=parent_id, recursion_depth=1)
        if not ok or node is None:
            return
        menu.clear()
        self._populate_qmenu(menu, node)

    def show_best_menu_at_global(self, global_pos: QPoint, parent: QWidget | None = None) -> tuple[bool, str]:
        menu, error = self.build_qmenu(parent)
        if menu is not None and not menu.isEmpty():
            menu.exec(global_pos)
            return True, ""

        ok, native_error = self.show_native_menu_at_global(global_pos)
        if ok:
            return True, ""

        return False, error or native_error or "No usable menu backend."

    def create_button(
        self,
        parent: QWidget | None = None,
        icon_size: int = 22,
        button_size: int = 30,
    ) -> QToolButton:
        return CompletedTrayItemButton(self, parent=parent, icon_size=icon_size, button_size=button_size)


class CompletedTrayItemButton(QToolButton):
    def __init__(
        self,
        item: CompletedTrayItem,
        parent: QWidget | None = None,
        icon_size: int = 22,
        button_size: int = 30,
    ) -> None:
        super().__init__(parent)
        self.item = item

        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFixedSize(button_size, button_size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(
            """
            QToolButton {
                border: none;
                border-radius: 6px;
                padding: 2px;
                background: transparent;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 0.10);
            }
            QToolButton:pressed {
                background: rgba(255, 255, 255, 0.18);
            }
            """
        )
        self._apply_item()

    def _apply_item(self) -> None:
        self.setIcon(self.item.icon)
        self.setToolTip(self.item.title)
        self.setVisible(self.item.is_visible)

    def update_item(self, item: CompletedTrayItem) -> None:
        self.item = item
        self._apply_item()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        global_pos = event.globalPosition().toPoint()

        if event.button() == Qt.RightButton:
            self.item.show_best_menu_at_global(global_pos, self)
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            if self.item.item_is_menu:
                self.item.show_best_menu_at_global(global_pos, self)
            else:
                ok, _ = self.item.activate_at_global(global_pos)
                if not ok:
                    self.item.show_best_menu_at_global(global_pos, self)
            event.accept()
            return

        if event.button() == Qt.MiddleButton:
            self.item.secondary_activate_at_global(global_pos)
            event.accept()
            return

        super().mousePressEvent(event)


class TrayDiscovery:
    def __init__(self, bus: QDBusConnection | None = None) -> None:
        self.bus = bus or QDBusConnection.sessionBus()
        self.icon_resolver = TrayIconResolver(self.bus)

    def get_items(self, visible_only: bool = False) -> list[CompletedTrayItem]:
        if not self.bus.isConnected():
            return []

        self.icon_resolver._cache.clear()

        watcher_service = discover_watcher_service(self.bus)
        if not watcher_service:
            return []

        item_ids = registered_item_ids(self.bus, watcher_service)
        items: list[CompletedTrayItem] = []

        for item_id in item_ids:
            service, path = split_item_id(item_id)
            menu_path = str(call_property(self.bus, service, path, ITEM_INTERFACE, "Menu") or "").strip()
            item_is_menu = to_bool(
                call_property(self.bus, service, path, ITEM_INTERFACE, "ItemIsMenu"),
                default=False,
            )
            title = best_text(self.bus, service, path)
            status = str(call_property(self.bus, service, path, ITEM_INTERFACE, "Status") or "Unknown").strip() or "Unknown"
            resolved = self.icon_resolver.resolve_for_item(service, path)
            icon = resolved.icon
            icon_name = resolved.icon_name

            item = CompletedTrayItem(
                bus=self.bus,
                item_id=item_id,
                service=service,
                path=path,
                menu_path=menu_path,
                item_is_menu=item_is_menu,
                title=title,
                status=status,
                icon_name=icon_name,
                icon=icon,
            )

            if visible_only and not item.is_visible:
                continue
            items.append(item)
        
        return items
if __name__ == "__main__":
    import sys

    from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

    app = QApplication(sys.argv)
    app.setApplicationName("TrayDiscovery Demo")

    window = QWidget()
    window.setWindowTitle("TrayDiscovery Demo")
    window.resize(600, 80)

    root = QVBoxLayout(window)
    root.setContentsMargins(10, 10, 10, 10)
    root.setSpacing(8)

    status_label = QLabel("Discovered tray items:")
    root.addWidget(status_label)

    row = QHBoxLayout()
    row.setSpacing(4)
    root.addLayout(row)

    discovery = TrayDiscovery()
    items = discovery.get_items(visible_only=True)

    if not items:
        row.addWidget(QLabel("No tray items found"))
    else:
        for item in items:
            row.addWidget(item.create_button(window))

    row.addStretch(1)

    window.show()
    raise SystemExit(app.exec())
