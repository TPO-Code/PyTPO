from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .service import MprisService, PlayerInfo
from .media_widget import MediaPlayerCard


class MediaContainer(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mpris = MprisService()
        self.player_cards: dict[str, MediaPlayerCard] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        media_title = QLabel("Media", self)
        media_title.setObjectName("systemMenuSectionTitle")
        root.addWidget(media_title)

        self.media_summary = QLabel("Looking for MPRIS players...", self)
        self.media_summary.setObjectName("systemMenuStatus")
        self.media_summary.setWordWrap(True)
        root.addWidget(self.media_summary)

        self.media_empty_label = QLabel("No media players detected.", self)
        self.media_empty_label.setObjectName("systemMenuMutedText")
        self.media_empty_label.setWordWrap(True)
        root.addWidget(self.media_empty_label)

        self.media_scroll = QScrollArea(self)
        self.media_scroll.setWidgetResizable(True)
        self.media_scroll.setFrameShape(QFrame.NoFrame)
        self.media_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.media_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.media_scroll.setMaximumHeight(250)
        self.media_scroll.setObjectName("mediaScroll")
        root.addWidget(self.media_scroll)

        self.media_cards_host = QWidget(self)
        self.media_cards_layout = QVBoxLayout(self.media_cards_host)
        self.media_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.media_cards_layout.setSpacing(10)
        self.media_cards_layout.addStretch(1)
        self.media_scroll.setWidget(self.media_cards_host)

        self.media_scroll.hide()

    def _ensure_player_card(self, name: str) -> MediaPlayerCard:
        card = self.player_cards.get(name)
        if card is not None:
            return card

        card = MediaPlayerCard(self.mpris.set_volume, self.mpris.command, self.refresh, self.media_cards_host)
        self.player_cards[name] = card
        self.media_cards_layout.insertWidget(self.media_cards_layout.count() - 1, card)
        return card

    def _remove_missing_player_cards(self, active_players: set[str]) -> None:
        for name in [player_name for player_name in self.player_cards if player_name not in active_players]:
            card = self.player_cards.pop(name)
            self.media_cards_layout.removeWidget(card)
            card.deleteLater()

    def refresh(self) -> None:
        players = self.mpris.list_players()
        active_players = set(players)
        self._remove_missing_player_cards(active_players)

        if self.mpris.playerctl_missing:
            self.media_summary.setText("Media controls need playerctl to be installed.")
            self.media_empty_label.setText("Install playerctl to show active media players here.")
            self.media_empty_label.show()
            self.media_scroll.hide()
            return

        if not players:
            summary = "No active MPRIS media players found."
            if self.mpris.gdbus_missing:
                summary += " Capability detection is limited because gdbus is missing."
            self.media_summary.setText(summary)
            self.media_empty_label.setText(
                "Start playback in apps like VLC, Spotify, mpv, Firefox, or Chromium and controls will appear here."
            )
            self.media_empty_label.show()
            self.media_scroll.hide()
            return

        infos: list[PlayerInfo] = []
        for player in players:
            infos.append(self.mpris.get_player(player))

        summary_bits = [f"{info.identity or info.name}: {info.status}" for info in infos]
        if self.mpris.gdbus_missing:
            summary_bits.append("gdbus missing; control capability detection is limited")
        self.media_summary.setText(" | ".join(summary_bits))

        for info in infos:
            self._ensure_player_card(info.name).bind(info)

        self.media_empty_label.hide()
        self.media_scroll.show()
