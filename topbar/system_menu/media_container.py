from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .service import MediaSnapshot, MprisService, PlayerInfo
from .media_widget import MediaPlayerCard

LOGGER = logging.getLogger("topbar.media")


class MediaContainer(QWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        request_refresh: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.mpris = MprisService()
        self._request_refresh = request_refresh
        self.player_cards: dict[str, MediaPlayerCard] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.media_title = QLabel("Media", self)
        self.media_title.setObjectName("systemMenuSectionTitle")
        root.addWidget(self.media_title)

        self.media_summary = QLabel(self)
        self.media_summary.setObjectName("systemMenuStatus")
        self.media_summary.setWordWrap(True)
        self.media_summary.hide()
        root.addWidget(self.media_summary)

        self.media_empty_label = QLabel(self)
        self.media_empty_label.setObjectName("systemMenuMutedText")
        self.media_empty_label.setWordWrap(True)
        self.media_empty_label.hide()
        root.addWidget(self.media_empty_label)

        self.media_scroll = QScrollArea(self)
        self.media_scroll.setWidgetResizable(True)
        self.media_scroll.setFrameShape(QFrame.NoFrame)
        self.media_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.media_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.media_scroll.setMaximumHeight(250)
        self.media_scroll.setObjectName("mediaScroll")
        self.media_scroll.hide()
        root.addWidget(self.media_scroll)

        self.media_cards_host = QWidget(self)
        self.media_cards_layout = QVBoxLayout(self.media_cards_host)
        self.media_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.media_cards_layout.setSpacing(10)
        self.media_cards_layout.addStretch(1)
        self.media_scroll.setWidget(self.media_cards_host)

        self.hide()

    def _ensure_player_card(self, name: str) -> MediaPlayerCard:
        card = self.player_cards.get(name)
        if card is not None:
            return card

        card = MediaPlayerCard(
            self.mpris.set_volume,
            self.mpris.command,
            self.refresh,
            self.media_cards_host,
        )
        self.player_cards[name] = card
        self.media_cards_layout.insertWidget(self.media_cards_layout.count() - 1, card)
        return card

    def _remove_missing_player_cards(self, active_players: set[str]) -> None:
        for name in [player_name for player_name in self.player_cards if player_name not in active_players]:
            card = self.player_cards.pop(name)
            self.media_cards_layout.removeWidget(card)
            card.deleteLater()

    def _set_summary_text(self, text: str) -> None:
        text = text.strip()
        if text:
            self.media_summary.setText(text)
            self.media_summary.show()
        else:
            self.media_summary.clear()
            self.media_summary.hide()

    def _set_empty_text(self, text: str) -> None:
        text = text.strip()
        if text:
            self.media_empty_label.setText(text)
            self.media_empty_label.show()
        else:
            self.media_empty_label.clear()
            self.media_empty_label.hide()

    def _player_text(self, value: str | None) -> str:
        return (value or "").strip()

    def _player_status_key(self, info: PlayerInfo) -> str:
        return self._player_text(info.status).lower()

    def _same_logical_player(self, left: PlayerInfo, right: PlayerInfo) -> bool:
        return (
            self._player_text(left.identity) == self._player_text(right.identity)
            and self._player_status_key(left) == self._player_status_key(right)
            and self._player_text(left.title) == self._player_text(right.title)
            and self._player_text(left.artist) == self._player_text(right.artist)
            and self._player_text(left.album) == self._player_text(right.album)
        )

    def _player_score(self, info: PlayerInfo) -> tuple[int, int, int, int, int]:
        status = self._player_status_key(info)
        status_score = 2 if status == "playing" else 1 if status == "paused" else 0
        metadata_score = sum(
            1 for value in (info.title, info.artist, info.album) if self._player_text(value)
        )
        control_score = 1 if info.can_control else 0
        volume_score = 1 if info.volume is not None else 0
        instance_score = 1 if ".instance" in info.name else 0
        return (
            status_score,
            metadata_score,
            control_score,
            volume_score,
            instance_score,
        )

    def _pick_better_player(self, left: PlayerInfo, right: PlayerInfo) -> PlayerInfo:
        return left if self._player_score(left) >= self._player_score(right) else right

    def _dedupe_players(self, players: tuple[PlayerInfo, ...]) -> tuple[PlayerInfo, ...]:
        vlc_base = next((player for player in players if player.name == "vlc"), None)
        if vlc_base is None:
            return players

        vlc_instances = [player for player in players if player.name.startswith("vlc.instance")]
        if not vlc_instances:
            return players

        kept: list[PlayerInfo] = []
        dropped_names: set[str] = set()

        for instance_player in vlc_instances:
            if self._same_logical_player(vlc_base, instance_player):
                preferred = self._pick_better_player(vlc_base, instance_player)
                dropped = instance_player if preferred is vlc_base else vlc_base
                dropped_names.add(dropped.name)
                LOGGER.info(
                    "Collapsed VLC duplicate alias: kept=%r dropped=%r",
                    preferred.name,
                    dropped.name,
                )
                break

        for player in players:
            if player.name not in dropped_names:
                kept.append(player)

        return tuple(kept)

    def refresh(self) -> None:
        if self._request_refresh is not None:
            self._request_refresh()
            return

        players = tuple(self.mpris.get_player(player_name) for player_name in self.mpris.list_players())
        self.apply_snapshot(
            MediaSnapshot(
                playerctl_missing=self.mpris.playerctl_missing,
                gdbus_missing=self.mpris.gdbus_missing,
                players=players,
            )
        )

    def apply_snapshot(self, snapshot: MediaSnapshot) -> None:
        players = self._dedupe_players(snapshot.players)
        active_players = {player.name for player in players}
        self._remove_missing_player_cards(active_players)

        missing_dependencies: list[str] = []
        if snapshot.playerctl_missing:
            missing_dependencies.append("playerctl")
        if snapshot.gdbus_missing:
            missing_dependencies.append("gdbus")

        if missing_dependencies:
            deps_text = ", ".join(missing_dependencies)
            self.media_title.show()
            self._set_summary_text(f"Media integration is unavailable because {deps_text} is not installed.")
            self._set_empty_text(f"Install {deps_text} to enable media detection and controls.")
            self.media_scroll.hide()
            self.show()
            return

        if not players:
            self._set_summary_text("")
            self._set_empty_text("")
            self.media_scroll.hide()
            self.hide()
            return

        LOGGER.info(
            "Visible players: %s",
            " || ".join(
                f"name={info.name!r}, identity={info.identity!r}, status={info.status!r}"
                for info in players
            ),
        )

        summary_bits = [f"{info.identity or info.name}: {info.status}" for info in players]
        self._set_summary_text(" | ".join(summary_bits))

        for info in players:
            self._ensure_player_card(info.name).bind(info)

        self._set_empty_text("")
        self.media_title.show()
        self.media_scroll.show()
        self.show()