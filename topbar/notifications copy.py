from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QDateTime, QObject, QTimer, Qt, Signal, ClassInfo, Slot
from .constants import NOTIFICATIONS_INTERFACE, NOTIFICATIONS_PATH, NOTIFICATIONS_SERVICE
from .dbus import (
    claim_dbus_service,
    dbus_to_python,
    dbus_error_text,
)

from PySide6.QtWidgets import QMenu, QToolButton
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusContext, QDBusAbstractAdaptor
from PySide6.QtGui import QIcon

LOGGER = logging.getLogger("topbar.notifications")



@ClassInfo({"D-Bus Interface": NOTIFICATIONS_INTERFACE})
class NotificationAdaptor(QDBusAbstractAdaptor, QDBusContext):
    NotificationClosed = Signal("uint", "uint")
    ActionInvoked = Signal("uint", str)

    def __init__(self, server: "NotificationServer"):
        super().__init__(server)
        self.server = server
        self.setAutoRelaySignals(False)

    @Slot(result="QStringList")
    def GetCapabilities(self) -> list[str]:
        LOGGER.info("DBus Notification GetCapabilities called")
        return ["body", "actions", "persistence"]

    @Slot(QDBusMessage)
    def GetServerInformation(self, message: QDBusMessage) -> None:
        LOGGER.info("DBus Notification GetServerInformation called")
        message.setDelayedReply(True)
        reply = message.createReply()
        reply.setArguments(["PyTPO TopBar", "PyTPO", "0.1", "1.2"])
        self.server.bus.send(reply)

    @Slot(str, "uint", str, str, str, "QStringList", "QVariantMap", int, result="uint")
    def Notify(
        self,
        app_name: str,
        replaces_id: int,
        app_icon: str,
        summary: str,
        body: str,
        actions: list[str],
        hints: dict,
        expire_timeout: int,
    ) -> int:
        LOGGER.info("DBus Notification Notify called: summary=%r", summary)
        notification_id = self.server.center.add_notification(
            app_name=app_name,
            replaces_id=replaces_id,
            app_icon=app_icon,
            summary=summary,
            body=body,
            actions=actions if isinstance(actions, list) else [],
            hints=self.server._coerce_hints(hints),
            expire_timeout=expire_timeout,
        )
        LOGGER.info(
            "Stored via DBus notification_id=%s app=%r summary=%r",
            notification_id,
            app_name,
            summary,
        )
        return notification_id

    @Slot("uint")
    def CloseNotification(self, notification_id: int) -> None:
        LOGGER.info("DBus Notification CloseNotification requested for id=%s", notification_id)
        self.server.center.close_notification(notification_id, reason=3)


@dataclass
class NotificationEntry:
    notification_id: int
    app_name: str
    app_icon: str
    summary: str
    body: str
    actions: list[str]
    hints: dict[str, Any]
    expire_timeout: int
    received_at: str

class NotificationCenter(QObject):
    notificationsChanged = Signal()
    notificationClosed = Signal(int, int)
    actionInvoked = Signal(int, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._next_notification_id = 1
        self._notifications: dict[int, NotificationEntry] = {}
        self._order: list[int] = []
        self._timers: dict[int, QTimer] = {}

    def notifications(self) -> list[NotificationEntry]:
        return [self._notifications[item_id] for item_id in self._order if item_id in self._notifications]

    def add_notification(
        self,
        app_name: str,
        replaces_id: int,
        app_icon: str,
        summary: str,
        body: str,
        actions: list[str],
        hints: dict[str, Any],
        expire_timeout: int,
    ) -> int:
        notification_id = replaces_id if replaces_id and replaces_id in self._notifications else self._next_notification_id
        if notification_id == self._next_notification_id:
            self._next_notification_id += 1

        entry = NotificationEntry(
            notification_id=notification_id,
            app_name=app_name or "Unknown",
            app_icon=app_icon or "",
            summary=summary or "(no summary)",
            body=body or "",
            actions=actions,
            hints=hints,
            expire_timeout=expire_timeout,
            received_at=QDateTime.currentDateTime().toString("hh:mm:ss"),
        )
        self._notifications[notification_id] = entry
        if notification_id in self._order:
            self._order.remove(notification_id)
        self._order.insert(0, notification_id)
        self._reset_expiry_timer(notification_id, expire_timeout)
        LOGGER.info("Stored notification id=%s app=%r summary=%r", notification_id, entry.app_name, entry.summary)
        self.notificationsChanged.emit()
        return notification_id

    def close_notification(self, notification_id: int, reason: int = 2) -> None:
        if notification_id not in self._notifications:
            return
        LOGGER.debug("Closing notification id=%s reason=%s", notification_id, reason)
        self._stop_timer(notification_id)
        self._notifications.pop(notification_id, None)
        if notification_id in self._order:
            self._order.remove(notification_id)
        self.notificationsChanged.emit()
        self.notificationClosed.emit(notification_id, reason)

    def clear_all(self) -> None:
        for notification_id in list(self._order):
            self.close_notification(notification_id, reason=2)

    def invoke_action(self, notification_id: int, action_key: str) -> None:
        if notification_id not in self._notifications:
            return
        LOGGER.info("Notification action invoked id=%s action=%r", notification_id, action_key)
        self.actionInvoked.emit(notification_id, action_key)

    def _reset_expiry_timer(self, notification_id: int, expire_timeout: int) -> None:
        self._stop_timer(notification_id)
        if expire_timeout == 0:
            return
        if expire_timeout < 0:
            expire_timeout = 5000  # or your preferred default
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(expire_timeout)
        timer.timeout.connect(lambda item_id=notification_id: self.close_notification(item_id, reason=1))
        timer.start()
        self._timers[notification_id] = timer

    def _stop_timer(self, notification_id: int) -> None:
        timer = self._timers.pop(notification_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()



class NotificationServer(QObject):
    def __init__(self, center: NotificationCenter, parent: QObject | None = None):
        super().__init__(parent)
        self.center = center
        self.bus = QDBusConnection.sessionBus()
        self.is_active = False
        self.last_error = ""
        self._adaptor = NotificationAdaptor(self)

        self.center.notificationClosed.connect(self._emit_notification_closed)
        self.center.actionInvoked.connect(self._emit_action_invoked)

        self._register()


    def _register(self) -> None:
        LOGGER.info(
            "Registering notification server service=%s object=%s",
            NOTIFICATIONS_SERVICE,
            NOTIFICATIONS_PATH,
        )
    
        ok, error = claim_dbus_service(self.bus, NOTIFICATIONS_SERVICE)
        if not ok:
            self.last_error = error
            LOGGER.warning("Notification service claim failed: %s", error)
            return
    
        registered = self.bus.registerObject(
            NOTIFICATIONS_PATH,
            self,
            QDBusConnection.ExportAdaptors,
        )
    
        if not registered:
            self.bus.unregisterService(NOTIFICATIONS_SERVICE)
            self.last_error = f"failed to register object at {NOTIFICATIONS_PATH}: {dbus_error_text(self.bus)}"
            LOGGER.error("Notification object registration failed root=%s error=%s", NOTIFICATIONS_PATH, self.last_error)
            return
    
        self.is_active = True
        self.last_error = ""
        LOGGER.info(
            "Notification server ready service=%s root=%s interface=%s",
            NOTIFICATIONS_SERVICE,
            NOTIFICATIONS_PATH,
            NOTIFICATIONS_INTERFACE,
        )

    def _coerce_hints(self, hints: Any) -> dict[str, Any]:
        raw_hints = dbus_to_python(hints)
        if not isinstance(raw_hints, dict):
            return {}
        return {str(key): value for key, value in raw_hints.items()}
        
    def _emit_notification_closed(self, notification_id: int, reason: int) -> None:
        signal = QDBusMessage.createSignal(
            NOTIFICATIONS_PATH,
            NOTIFICATIONS_INTERFACE,
            "NotificationClosed",
        )
        signal.setArguments([notification_id, reason])
        self.bus.send(signal)
    
    def _emit_action_invoked(self, notification_id: int, action_key: str) -> None:
        signal = QDBusMessage.createSignal(
            NOTIFICATIONS_PATH,
            NOTIFICATIONS_INTERFACE,
            "ActionInvoked",
        )
        signal.setArguments([notification_id, action_key])
        self.bus.send(signal)
        
class NotificationCenterButton(QToolButton):
    def __init__(self, center: NotificationCenter, server: NotificationServer, parent=None):
        super().__init__(parent)
        self.center = center
        self.server = server
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(28)
        self.setStyleSheet(
            """
            QToolButton { background: transparent; border: none; border-radius: 4px; padding: 0 8px; color: white; }
            QToolButton:hover { background: rgba(255, 255, 255, 0.12); }
            """
        )
        notification_icon = QIcon.fromTheme("preferences-system-notifications")
        if not notification_icon.isNull():
            self.setIcon(notification_icon)
        self.clicked.connect(self._show_menu)
        self.center.notificationsChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        notifications = self.center.notifications()
        count = len(notifications)
        self.setText(f"N {count}")
        if self.server.is_active:
            status = "Notification server active"
        else:
            status = f"Notification server inactive: {self.server.last_error or 'unknown error'}"
        latest = notifications[0] if notifications else None
        if latest is None:
            self.setToolTip(status)
        else:
            self.setToolTip(f"{status}\nLatest: {latest.app_name} - {latest.summary}")

    def _show_menu(self) -> None:
        menu = QMenu(self)
        notifications = self.center.notifications()
        if not notifications:
            empty = menu.addAction("No notifications")
            empty.setEnabled(False)
        else:
            for entry in notifications:
                submenu = menu.addMenu(f"{entry.app_name}: {entry.summary}")
                if entry.body:
                    body_action = submenu.addAction(entry.body)
                    body_action.setEnabled(False)
                for index in range(0, len(entry.actions), 2):
                    if index + 1 >= len(entry.actions):
                        break
                    action_key = entry.actions[index]
                    action_label = entry.actions[index + 1]
                    action = submenu.addAction(action_label)
                    action.triggered.connect(
                        lambda checked=False, item_id=entry.notification_id, key=action_key: self.center.invoke_action(
                            item_id, key
                        )
                    )
                dismiss = submenu.addAction("Dismiss")
                dismiss.triggered.connect(
                    lambda checked=False, item_id=entry.notification_id: self.center.close_notification(item_id, reason=2)
                )
            menu.addSeparator()
            clear_all = menu.addAction("Clear all")
            clear_all.triggered.connect(self.center.clear_all)
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))
"""
gdbus call --session \
  --dest org.freedesktop.Notifications \
  --object-path /org/freedesktop/Notifications \
  --method org.freedesktop.Notifications.GetCapabilities

gdbus call --session \
  --dest org.freedesktop.Notifications \
  --object-path /org/freedesktop/Notifications \
  --method org.freedesktop.Notifications.GetServerInformation

gdbus call --session \
  --dest org.freedesktop.Notifications \
  --object-path /org/freedesktop/Notifications \
  --method org.freedesktop.Notifications.Notify \
  "Terminal" 0 "" "Janet test" "Direct DBus hello" [] "{}" 5000
  
gdbus call --session \
  --dest org.freedesktop.Notifications \
  --object-path /org/freedesktop/Notifications \
  --method org.freedesktop.Notifications.GetServerInformation
"""
