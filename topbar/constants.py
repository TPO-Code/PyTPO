from __future__ import annotations

import shutil

WATCHER_SERVICES = (
    "org.kde.StatusNotifierWatcher",
    "org.freedesktop.StatusNotifierWatcher",
)
WATCHER_INTERFACES = WATCHER_SERVICES
WATCHER_INTERFACE = WATCHER_INTERFACES[0]
WATCHER_PATH = "/StatusNotifierWatcher"

ITEM_INTERFACE = "org.kde.StatusNotifierItem"
MENU_INTERFACE = "com.canonical.dbusmenu"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

NOTIFICATIONS_SERVICE = "org.freedesktop.Notifications"
NOTIFICATIONS_PATH = "/org/freedesktop/Notifications"
NOTIFICATIONS_INTERFACE = "org.freedesktop.Notifications"

DBUS_OBJECT_ROOT = "/"
BUSCTL = shutil.which("busctl")
