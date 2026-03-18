from PySide6.QtCore import QDate, QDateTime, QTimer, QTime, Qt
from PySide6.QtGui import QFont, QColor, QTextCharFormat
from PySide6.QtWidgets import QCalendarWidget, QWidget, QVBoxLayout

class CalendarPopup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("CalendarPopup")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.calendar = QCalendarWidget(self)
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.setHorizontalHeaderFormat(QCalendarWidget.ShortDayNames)

        self._last_today = None
        self.refresh_today_highlight()

        # Fires only when the date actually rolls over.
        self._midnight_timer = QTimer(self)
        self._midnight_timer.setSingleShot(True)
        self._midnight_timer.timeout.connect(self._handle_date_rollover)
        self._schedule_next_midnight_refresh()

        self.setStyleSheet("""
            QWidget#CalendarPopup {
                background-color: #ffffff;
                border: 1px solid #c0c0c0;
                border-radius: 4px;
            }
            QCalendarWidget QWidget#qt_calendar_navigationbar {
                background-color: #f5f5f5;
                border-bottom: 1px solid #e0e0e0;
                padding: 2px;
            }
            QCalendarWidget QToolButton {
                color: #333333;
                background-color: transparent;
                border: none;
                margin: 2px;
                padding: 4px;
            }
            QCalendarWidget QToolButton:hover {
                background-color: #e4e4e4;
                border-radius: 3px;
            }
            QCalendarWidget QAbstractItemView {
                background-color: #ffffff;
                color: #333333;
                selection-background-color: #0078d7;
                selection-color: white;
                outline: 0px;
            }
            QCalendarWidget QSpinBox {
                background: transparent;
                border: none;
                color: #333333;
            }
        """)

        layout.addWidget(self.calendar)

    def _today_format(self) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#e0f0ff"))
        fmt.setForeground(QColor("#0078d7"))
        fmt.setFontWeight(QFont.Weight.Bold)
        return fmt

    def refresh_today_highlight(self):
        """
        Update only the custom 'today' formatting.

        This does not change:
        - selected date
        - visible month/page
        - anything the user is currently doing
        """
        today = QDate.currentDate()

        if self._last_today == today:
            return

        if self._last_today is not None:
            self.calendar.setDateTextFormat(self._last_today, QTextCharFormat())

        self.calendar.setDateTextFormat(today, self._today_format())
        self._last_today = today

    def reset_for_open(self):
        """
        Every time the popup opens:
        - recompute today's date
        - jump to today's month/year
        - select today

        This is intentionally disruptive because it is only used on open.
        """
        today = QDate.currentDate()
        self.refresh_today_highlight()
        self.calendar.setCurrentPage(today.year(), today.month())
        self.calendar.setSelectedDate(today)

    def _handle_date_rollover(self):
        """
        Midnight passed while the app was running.

        Refresh the 'today' highlight only.
        Do not reset the page or selection, because the user may be browsing.
        """
        self.refresh_today_highlight()
        self._schedule_next_midnight_refresh()

    def _schedule_next_midnight_refresh(self):
        now = QDateTime.currentDateTime()
        next_midnight = QDateTime(now.date().addDays(1), QTime(0, 0, 0))

        msecs_until_midnight = now.msecsTo(next_midnight)
        if msecs_until_midnight <= 0:
            msecs_until_midnight = 1000

        self._midnight_timer.start(msecs_until_midnight)