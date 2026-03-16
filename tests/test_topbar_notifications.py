from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from topbar.notifications import NotificationCenter, NotificationEntry, popup_groups


def _app() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


class NotificationCenterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._qt_app = _app()

    def test_archive_keeps_notification_in_history(self) -> None:
        center = NotificationCenter()

        notification_id = center.add_notification(
            app_name="notify-send",
            replaces_id=0,
            app_icon="",
            summary="Build done",
            body="Everything passed.",
            actions=[],
            hints={},
            expire_timeout=5000,
        )

        center.archive_notification(notification_id, reason=1)

        notifications = center.notifications()
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].notification_id, notification_id)
        self.assertEqual(notifications[0].closed_reason, 1)
        self.assertEqual(center.active_popup_notifications(), [])

    def test_close_notification_removes_history_entry(self) -> None:
        center = NotificationCenter()

        notification_id = center.add_notification(
            app_name="notify-send",
            replaces_id=0,
            app_icon="",
            summary="Remove me",
            body="Transient",
            actions=[],
            hints={},
            expire_timeout=5000,
        )

        center.close_notification(notification_id, reason=2)

        self.assertEqual(center.notifications(), [])
        self.assertEqual(center.active_popup_notifications(), [])

    def test_replacing_notification_reuses_id_and_updates_latest_entry(self) -> None:
        center = NotificationCenter()

        notification_id = center.add_notification(
            app_name="notify-send",
            replaces_id=0,
            app_icon="",
            summary="Old summary",
            body="Old body",
            actions=[],
            hints={},
            expire_timeout=5000,
        )

        replaced_id = center.add_notification(
            app_name="notify-send",
            replaces_id=notification_id,
            app_icon="",
            summary="New summary",
            body="New body",
            actions=[],
            hints={},
            expire_timeout=5000,
        )

        self.assertEqual(replaced_id, notification_id)
        notifications = center.notifications()
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].summary, "New summary")
        self.assertTrue(notifications[0].is_popup_active)

    def test_popup_groups_merge_overflow_into_final_popup(self) -> None:
        entries = [
            NotificationEntry(
                notification_id=index,
                app_name=f"App {index}",
                app_icon="",
                summary=f"Summary {index}",
                body="",
                actions=[],
                hints={},
                expire_timeout=5000,
                received_at="00:00:00",
            )
            for index in range(1, 6)
        ]

        groups = popup_groups(entries)

        self.assertEqual([len(group) for group in groups], [1, 1, 1, 2])
        self.assertEqual([entry.notification_id for entry in groups[-1]], [4, 5])


if __name__ == "__main__":
    unittest.main()
