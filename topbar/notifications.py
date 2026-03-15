from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import ClassInfo, QDateTime, QEvent, QObject, QPoint, QTimer, Qt, Signal, Slot
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusContext, QDBusMessage
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .constants import NOTIFICATIONS_INTERFACE, NOTIFICATIONS_PATH, NOTIFICATIONS_SERVICE
from .dbus import claim_dbus_service, dbus_error_text, dbus_to_python

LOGGER = logging.getLogger("topbar.notifications")

DEFAULT_NOTIFICATION_TIMEOUT_MS = 5000
MAX_VISIBLE_POPUPS = 4
HISTORY_PANEL_WIDTH = 380
POPUP_WIDTH = 360


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
        self.server.center.archive_notification(notification_id, reason=3)


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
    is_popup_active: bool = True
    closed_reason: int | None = None


def popup_groups(entries: list[NotificationEntry], max_visible: int = MAX_VISIBLE_POPUPS) -> list[list[NotificationEntry]]:
    if len(entries) <= max_visible:
        return [[entry] for entry in entries]
    visible_singles = max_visible - 1
    groups = [[entry] for entry in entries[:visible_singles]]
    groups.append(entries[visible_singles:])
    return groups


def notification_icon(entry: NotificationEntry) -> QIcon:
    icon_name = (entry.app_icon or "").strip()
    if icon_name.startswith("/"):
        icon = QIcon(icon_name)
        if not icon.isNull():
            return icon
    if icon_name:
        icon = QIcon.fromTheme(icon_name)
        if not icon.isNull():
            return icon

    for hint_name in ("image-path", "image_path", "desktop-entry"):
        hint_value = entry.hints.get(hint_name)
        if not hint_value:
            continue
        if isinstance(hint_value, str) and hint_value.startswith("/"):
            icon = QIcon(hint_value)
        else:
            icon = QIcon.fromTheme(str(hint_value))
        if not icon.isNull():
            return icon

    fallback = QIcon.fromTheme("preferences-system-notifications")
    if not fallback.isNull():
        return fallback

    app = QApplication.instance()
    return app.style().standardIcon(QStyle.SP_MessageBoxInformation) if app is not None else QIcon()


class NotificationCenter(QObject):
    notificationsChanged = Signal()
    popupStateChanged = Signal()
    notificationAdded = Signal(int)
    notificationUpdated = Signal(int)
    notificationClosed = Signal(int, int)
    notificationRemoved = Signal(int)
    actionInvoked = Signal(int, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._next_notification_id = 1
        self._notifications: dict[int, NotificationEntry] = {}
        self._order: list[int] = []
        self._popup_order: list[int] = []
        self._popup_timers: dict[int, QTimer] = {}

    def notifications(self) -> list[NotificationEntry]:
        return [self._notifications[item_id] for item_id in self._order if item_id in self._notifications]

    def active_popup_notifications(self) -> list[NotificationEntry]:
        return [self._notifications[item_id] for item_id in self._popup_order if item_id in self._notifications]

    def notification(self, notification_id: int) -> NotificationEntry | None:
        return self._notifications.get(notification_id)

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
        is_update = notification_id in self._notifications
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
        self._activate_popup(notification_id, expire_timeout)

        LOGGER.info("Stored notification id=%s app=%r summary=%r", notification_id, entry.app_name, entry.summary)
        self.notificationsChanged.emit()
        self.popupStateChanged.emit()
        if is_update:
            self.notificationUpdated.emit(notification_id)
        else:
            self.notificationAdded.emit(notification_id)
        return notification_id

    def archive_notification(self, notification_id: int, reason: int = 2) -> None:
        entry = self._notifications.get(notification_id)
        if entry is None:
            return

        popup_changed = self._deactivate_popup(notification_id)
        state_changed = entry.closed_reason is None
        if state_changed:
            entry.closed_reason = reason
            self.notificationClosed.emit(notification_id, reason)

        if popup_changed:
            self.popupStateChanged.emit()
        if popup_changed or state_changed:
            self.notificationsChanged.emit()

    def close_notification(self, notification_id: int, reason: int = 2) -> None:
        entry = self._notifications.get(notification_id)
        if entry is None:
            return

        LOGGER.debug("Removing notification id=%s reason=%s", notification_id, reason)
        popup_changed = self._deactivate_popup(notification_id)
        if entry.closed_reason is None:
            entry.closed_reason = reason
            self.notificationClosed.emit(notification_id, reason)

        self._notifications.pop(notification_id, None)
        if notification_id in self._order:
            self._order.remove(notification_id)

        if popup_changed:
            self.popupStateChanged.emit()
        self.notificationsChanged.emit()
        self.notificationRemoved.emit(notification_id)

    def clear_all(self) -> None:
        for notification_id in list(self._order):
            self.close_notification(notification_id, reason=2)

    def invoke_action(self, notification_id: int, action_key: str) -> None:
        if notification_id not in self._notifications:
            return
        LOGGER.info("Notification action invoked id=%s action=%r", notification_id, action_key)
        self.actionInvoked.emit(notification_id, action_key)

    def _activate_popup(self, notification_id: int, expire_timeout: int) -> None:
        entry = self._notifications[notification_id]
        entry.is_popup_active = True
        entry.closed_reason = None
        self._stop_popup_timer(notification_id)
        if notification_id in self._popup_order:
            self._popup_order.remove(notification_id)
        self._popup_order.insert(0, notification_id)

        if expire_timeout == 0:
            return
        if expire_timeout < 0:
            expire_timeout = DEFAULT_NOTIFICATION_TIMEOUT_MS

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(expire_timeout)
        timer.timeout.connect(lambda item_id=notification_id: self.archive_notification(item_id, reason=1))
        timer.start()
        self._popup_timers[notification_id] = timer

    def _deactivate_popup(self, notification_id: int) -> bool:
        entry = self._notifications.get(notification_id)
        if entry is None:
            return False

        self._stop_popup_timer(notification_id)
        popup_changed = False
        if notification_id in self._popup_order:
            self._popup_order.remove(notification_id)
            popup_changed = True
        if entry.is_popup_active:
            entry.is_popup_active = False
            popup_changed = True
        return popup_changed

    def _stop_popup_timer(self, notification_id: int) -> None:
        timer = self._popup_timers.pop(notification_id, None)
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


class NotificationHistoryItem(QFrame):
    def __init__(self, center: NotificationCenter, entry: NotificationEntry, parent: QWidget | None = None):
        super().__init__(parent)
        self.center = center
        self.entry = entry

        self.setObjectName("notificationHistoryItem")
        self.setFrameShape(QFrame.StyledPanel)

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(28, 28)
        root_layout.addWidget(self.icon_label, alignment=Qt.AlignTop)

        content_layout = QVBoxLayout()
        content_layout.setSpacing(4)
        root_layout.addLayout(content_layout, stretch=1)

        self.meta_label = QLabel(self)
        self.meta_label.setObjectName("notificationMeta")
        content_layout.addWidget(self.meta_label)

        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setObjectName("notificationSummary")
        content_layout.addWidget(self.summary_label)

        self.body_label = QLabel(self)
        self.body_label.setWordWrap(True)
        self.body_label.setObjectName("notificationBody")
        content_layout.addWidget(self.body_label)

        self.state_label = QLabel(self)
        self.state_label.setObjectName("notificationState")
        content_layout.addWidget(self.state_label)

        self.actions_row = QHBoxLayout()
        self.actions_row.setSpacing(6)
        content_layout.addLayout(self.actions_row)

        self.remove_button = QPushButton("Remove", self)
        self.remove_button.clicked.connect(self._remove_notification)
        root_layout.addWidget(self.remove_button, alignment=Qt.AlignTop)

        self._apply_style()
        self.refresh(entry)

    def refresh(self, entry: NotificationEntry) -> None:
        self.entry = entry
        self._set_icon(notification_icon(entry))
        self.meta_label.setText(f"{entry.app_name}  •  {entry.received_at}")
        self.summary_label.setText(entry.summary)
        self.body_label.setVisible(bool(entry.body))
        self.body_label.setText(entry.body)

        if entry.closed_reason is None and entry.is_popup_active:
            state_text = "Showing now"
        elif entry.closed_reason == 1:
            state_text = "Auto-dismissed"
        elif entry.closed_reason is None:
            state_text = ""
        else:
            state_text = "Dismissed"
        self.state_label.setVisible(bool(state_text))
        self.state_label.setText(state_text)

        self._clear_actions_row()
        if entry.closed_reason is None:
            for index in range(0, len(entry.actions), 2):
                if index + 1 >= len(entry.actions):
                    break
                action_key = entry.actions[index]
                action_label = entry.actions[index + 1]
                button = QPushButton(action_label, self)
                button.clicked.connect(
                    lambda checked=False, item_id=entry.notification_id, key=action_key: self.center.invoke_action(
                        item_id, key
                    )
                )
                self.actions_row.addWidget(button)
        self.actions_row.addStretch(1)

    def _set_icon(self, icon: QIcon) -> None:
        pixmap = icon.pixmap(24, 24)
        if pixmap.isNull():
            self.icon_label.clear()
            return
        self.icon_label.setPixmap(pixmap)

    def _remove_notification(self) -> None:
        self.center.close_notification(self.entry.notification_id, reason=2)

    def _clear_actions_row(self) -> None:
        while self.actions_row.count():
            item = self.actions_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QFrame#notificationHistoryItem {
                background: #4d4d4d;
                border: 1px solid #666666;
                border-radius: 12px;
            }
            QLabel#notificationMeta {
                color: #c7d3d7;
                font-size: 11px;
            }
            QLabel#notificationSummary {
                color: #f4f4f4;
                font-size: 14px;
                font-weight: 600;
            }
            QLabel#notificationBody {
                color: #dde2e5;
                font-size: 12px;
            }
            QLabel#notificationState {
                color: #8fd5bf;
                font-size: 11px;
            }
            QPushButton {
                background: #5e6b6f;
                color: #f7f7f7;
                border: 1px solid #748286;
                border-radius: 8px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background: #708085;
            }
            """
        )


class NotificationCenterPanel(QFrame):
    def __init__(self, center: NotificationCenter, parent: QWidget | None = None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.center = center
        self._anchor: QWidget | None = None

        self.setObjectName("notificationCenterPanel")
        self.setFixedWidth(HISTORY_PANEL_WIDTH)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        root_layout.addLayout(header_layout)

        title = QLabel("Notifications", self)
        title.setObjectName("notificationPanelTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.count_label = QLabel(self)
        self.count_label.setObjectName("notificationPanelCount")
        header_layout.addWidget(self.count_label)

        self.clear_all_button = QPushButton("Clear all", self)
        self.clear_all_button.clicked.connect(self.center.clear_all)
        header_layout.addWidget(self.clear_all_button)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        root_layout.addWidget(self.scroll_area)

        self.content_widget = QWidget(self.scroll_area)
        self.items_layout = QVBoxLayout(self.content_widget)
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(10)
        self.scroll_area.setWidget(self.content_widget)

        self.center.notificationsChanged.connect(self.refresh)
        self._apply_style()
        self.refresh()

    def toggle(self, anchor: QWidget) -> None:
        self._anchor = anchor
        if self.isVisible():
            self.hide()
            return
        self.refresh()
        self.reposition(anchor)
        self.show()
        self.raise_()

    def reposition(self, anchor: QWidget | None = None) -> None:
        anchor_widget = anchor or self._anchor
        if anchor_widget is None:
            return

        self.adjustSize()
        anchor_bottom_right = anchor_widget.mapToGlobal(anchor_widget.rect().bottomRight())
        anchor_bottom_left = anchor_widget.mapToGlobal(anchor_widget.rect().bottomLeft())
        screen = QApplication.screenAt(anchor_bottom_right) or anchor_widget.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QApplication.primaryScreen().availableGeometry()

        x = anchor_bottom_right.x() - self.width()
        x = max(available.left() + 12, min(x, available.right() - self.width() - 12))
        y = anchor_bottom_left.y() + 8
        y = min(y, available.bottom() - self.height() - 12)
        y = max(available.top() + 12, y)
        self.move(QPoint(x, y))

    def refresh(self) -> None:
        notifications = self.center.notifications()
        self.count_label.setText(str(len(notifications)))
        self.clear_all_button.setEnabled(bool(notifications))

        while self.items_layout.count():
            item = self.items_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not notifications:
            empty_label = QLabel("No notifications yet.", self.content_widget)
            empty_label.setObjectName("notificationPanelEmpty")
            empty_label.setWordWrap(True)
            self.items_layout.addWidget(empty_label)
        else:
            for entry in notifications:
                self.items_layout.addWidget(NotificationHistoryItem(self.center, entry, self.content_widget))
        self.items_layout.addStretch(1)

        visible_items = min(max(len(notifications), 1), 5)
        self.resize(HISTORY_PANEL_WIDTH, 80 + visible_items * 92)
        if self.isVisible() and self._anchor is not None:
            self.reposition(self._anchor)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QFrame#notificationCenterPanel {
                background: #3f474a;
                border: 1px solid #657175;
                border-radius: 16px;
            }
            QLabel#notificationPanelTitle {
                color: #f4f4f4;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#notificationPanelCount {
                color: #d7e0e2;
                background: #566367;
                border-radius: 9px;
                padding: 2px 8px;
                font-weight: 600;
            }
            QLabel#notificationPanelEmpty {
                color: #d9e0e2;
                background: #4d5659;
                border: 1px dashed #6c787c;
                border-radius: 12px;
                padding: 18px;
            }
            QPushButton {
                background: #5a6a6f;
                color: #f5f7f8;
                border: 1px solid #738186;
                border-radius: 8px;
                padding: 5px 12px;
            }
            QPushButton:hover {
                background: #697b80;
            }
            QPushButton:disabled {
                background: #50585b;
                color: #adb8bb;
                border-color: #5f686b;
            }
            """
        )


class NotificationPopupWidget(QFrame):
    def __init__(self, center: NotificationCenter, entries: list[NotificationEntry], parent: QWidget | None = None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.center = center
        self.entries = entries

        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setObjectName("notificationPopup")
        self.setFixedWidth(POPUP_WIDTH)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        root_layout.addLayout(header_layout)

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(24, 24)
        header_layout.addWidget(self.icon_label, alignment=Qt.AlignTop)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        header_layout.addLayout(title_layout, stretch=1)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("notificationPopupTitle")
        self.title_label.setWordWrap(True)
        title_layout.addWidget(self.title_label)

        self.meta_label = QLabel(self)
        self.meta_label.setObjectName("notificationPopupMeta")
        title_layout.addWidget(self.meta_label)

        self.close_button = QToolButton(self)
        self.close_button.setText("x")
        self.close_button.setAutoRaise(True)
        self.close_button.clicked.connect(self._dismiss_entries)
        header_layout.addWidget(self.close_button, alignment=Qt.AlignTop)

        self.body_label = QLabel(self)
        self.body_label.setObjectName("notificationPopupBody")
        self.body_label.setWordWrap(True)
        root_layout.addWidget(self.body_label)

        self._apply_style()
        self.refresh(entries)

    def refresh(self, entries: list[NotificationEntry]) -> None:
        self.entries = entries
        self._set_icon(entries)

        if len(entries) == 1:
            entry = entries[0]
            self.title_label.setText(entry.summary)
            self.meta_label.setText(f"{entry.app_name}  •  {entry.received_at}")
            self.body_label.setVisible(bool(entry.body))
            self.body_label.setText(entry.body)
        else:
            overflow_lines = [f"{entry.app_name}: {entry.summary}" for entry in entries[:4]]
            remaining = len(entries) - len(overflow_lines)
            if remaining > 0:
                overflow_lines.append(f"+{remaining} more")
            self.title_label.setText(f"{len(entries)} more notifications")
            self.meta_label.setText("Stack condensed to avoid vertical overflow")
            self.body_label.setVisible(True)
            self.body_label.setText("\n".join(overflow_lines))

        self.adjustSize()

    def _dismiss_entries(self) -> None:
        for entry in self.entries:
            self.center.archive_notification(entry.notification_id, reason=2)

    def _set_icon(self, entries: list[NotificationEntry]) -> None:
        icon = notification_icon(entries[0]) if len(entries) == 1 else QIcon.fromTheme("preferences-system-notifications")
        if icon.isNull():
            app = QApplication.instance()
            icon = app.style().standardIcon(QStyle.SP_MessageBoxInformation) if app is not None else QIcon()
        pixmap = icon.pixmap(24, 24)
        if pixmap.isNull():
            self.icon_label.clear()
            return
        self.icon_label.setPixmap(pixmap)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QFrame#notificationPopup {
                background: #2f3d40;
                border: 1px solid #708488;
                border-radius: 16px;
            }
            QLabel#notificationPopupTitle {
                color: #f6f6f6;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#notificationPopupMeta {
                color: #c9d4d6;
                font-size: 11px;
            }
            QLabel#notificationPopupBody {
                color: #eef3f4;
                font-size: 12px;
                line-height: 1.3em;
            }
            QToolButton {
                color: #f0f4f5;
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 9px;
                min-width: 18px;
                min-height: 18px;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 0.16);
            }
            """
        )


class NotificationPopupManager(QObject):
    def __init__(self, center: NotificationCenter, anchor_widget: QWidget, parent: QObject | None = None):
        super().__init__(parent)
        self.center = center
        self.anchor_widget = anchor_widget
        self._popup_widgets: list[NotificationPopupWidget] = []

        self.center.popupStateChanged.connect(self.sync)
        self.anchor_widget.installEventFilter(self)
        if self.anchor_widget.window() is not None:
            self.anchor_widget.window().installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.WindowStateChange) and self._popup_widgets:
            QTimer.singleShot(0, self._position_popups)
        return super().eventFilter(watched, event)

    @Slot()
    def sync(self) -> None:
        groups = popup_groups(self.center.active_popup_notifications())

        for widget in self._popup_widgets:
            widget.hide()
            widget.deleteLater()
        self._popup_widgets = []

        if not groups:
            return

        for group in groups:
            widget = NotificationPopupWidget(self.center, group)
            widget.show()
            widget.raise_()
            self._popup_widgets.append(widget)

        self._position_popups()

    def _position_popups(self) -> None:
        if not self._popup_widgets:
            return

        anchor_point = self.anchor_widget.mapToGlobal(self.anchor_widget.rect().bottomRight())
        topbar_bottom = self.anchor_widget.window().mapToGlobal(QPoint(0, self.anchor_widget.window().height()))
        screen = QApplication.screenAt(anchor_point) or self.anchor_widget.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QApplication.primaryScreen().availableGeometry()

        y = max(topbar_bottom.y() + 12, available.top() + 12)
        for widget in self._popup_widgets:
            widget.adjustSize()
            x = available.right() - widget.width() - 16
            if x < available.left() + 12:
                x = available.left() + 12
            widget.move(QPoint(x, y))
            y += widget.height() + 10


class NotificationCenterButton(QToolButton):
    def __init__(self, center: NotificationCenter, server: NotificationServer, parent: QWidget | None = None):
        super().__init__(parent)
        self.center = center
        self.server = server
        self._panel = NotificationCenterPanel(center, self.window())
        self._popup_manager = NotificationPopupManager(center, self, self)

        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(28)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setStyleSheet(
            """
            QToolButton {
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 8px;
                padding: 0 10px;
                color: white;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 0.14);
            }
            """
        )

        notification_icon = QIcon.fromTheme("preferences-system-notifications")
        if notification_icon.isNull():
            app = QApplication.instance()
            if app is not None:
                notification_icon = app.style().standardIcon(QStyle.SP_MessageBoxInformation)
        self.setIcon(notification_icon)

        self.clicked.connect(self._toggle_panel)
        self.center.notificationsChanged.connect(self.refresh)
        self.installEventFilter(self)
        if self.window() is not None:
            self.window().installEventFilter(self)
        self.refresh()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if self._panel.isVisible() and event.type() in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.WindowStateChange):
            QTimer.singleShot(0, lambda: self._panel.reposition(self))
        return super().eventFilter(watched, event)

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

    def _toggle_panel(self) -> None:
        self._panel.toggle(self)
