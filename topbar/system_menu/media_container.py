from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..settings import TopBarBehaviorSettings
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
        self._settings = TopBarBehaviorSettings()
        self._snapshot = MediaSnapshot(playerctl_missing=False, gdbus_missing=False, players=())
        self.player_cards: dict[str, MediaPlayerCard] = {}

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(4)

        self.media_title = QLabel("Media", self)
        self.media_title.setObjectName("systemMenuSectionTitle")
        self._root_layout.addWidget(self.media_title)

        self.media_empty_label = QLabel(self)
        self.media_empty_label.setObjectName("systemMenuMutedText")
        self.media_empty_label.setWordWrap(True)
        self.media_empty_label.hide()
        self._root_layout.addWidget(self.media_empty_label)

        self.media_cards_host = QWidget(self)
        self.media_cards_layout = QVBoxLayout(self.media_cards_host)
        self.media_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.media_cards_layout.setSpacing(6)
        self.media_cards_layout.addStretch(1)
        self._root_layout.addWidget(self.media_cards_host)

        self.apply_settings(self._settings)
        self.hide()

    def _ensure_player_card(self, name: str) -> MediaPlayerCard:
        card = self.player_cards.get(name)
        if card is not None:
            return card
    
        card = MediaPlayerCard(
            self.mpris.set_volume,
            self.mpris.command,
            self.mpris.seek_relative,
            self.mpris.set_position,
            self.mpris.set_loop_status,
            self.mpris.set_shuffle,
            self.refresh,
            self.media_cards_host,
        )
        card.apply_settings(self._settings)
        self.player_cards[name] = card
        self.media_cards_layout.insertWidget(self.media_cards_layout.count() - 1, card)
        return card

    def _remove_missing_player_cards(self, active_players: set[str]) -> None:
        for name in [player_name for player_name in self.player_cards if player_name not in active_players]:
            card = self.player_cards.pop(name)
            self.media_cards_layout.removeWidget(card)
            card.deleteLater()

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

    def apply_settings(self, settings: TopBarBehaviorSettings) -> None:
        self._settings = settings
        self.media_cards_layout.setSpacing(max(0, int(settings.media_cards_spacing)))
        for card in self.player_cards.values():
            card.apply_settings(settings)
        self._apply_state(self._snapshot)

    def _sorted_players(self, players: tuple[PlayerInfo, ...]) -> tuple[PlayerInfo, ...]:
        if not self._settings.media_controls_prefer_active_player_first:
            return players
        return tuple(sorted(players, key=self._player_score, reverse=True))

    def _apply_state(self, snapshot: MediaSnapshot) -> None:
        if not self._settings.media_controls_show_media_players:
            self._set_empty_text("")
            self.hide()
            return

        players = self._sorted_players(self._dedupe_players(snapshot.players))
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
            self._set_empty_text(f"Install {deps_text} to enable media detection and controls.")
            self.media_cards_host.hide()
            self.show()
            return

        if not players:
            self._set_empty_text("")
            self.media_cards_host.hide()
            self.hide()
            return

        for info in players:
            self._ensure_player_card(info.name).bind(info)

        self._set_empty_text("")
        self.media_title.show()
        self.media_cards_host.show()
        self.show()

    def apply_snapshot(self, snapshot: MediaSnapshot) -> None:
        self._snapshot = snapshot
        self._apply_state(snapshot)
