from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect


@dataclass(frozen=True, slots=True)
class TopBarStrutReservation:
    strut: tuple[int, int, int, int]
    strut_partial: tuple[int, int, int, int, int, int, int, int, int, int, int, int]


def build_top_strut_reservation(
    *,
    window_rect: QRect,
    screen_rect: QRect,
    reserve_height: int,
) -> TopBarStrutReservation:
    top_strut = max(0, min(int(reserve_height), max(0, screen_rect.height())))
    if top_strut <= 0:
        return TopBarStrutReservation(
            strut=(0, 0, 0, 0),
            strut_partial=(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        )

    left = max(screen_rect.left(), window_rect.left())
    right = min(screen_rect.right(), window_rect.right())
    if right < left:
        left = screen_rect.left()
        right = screen_rect.right()

    return TopBarStrutReservation(
        strut=(0, 0, top_strut, 0),
        strut_partial=(0, 0, top_strut, 0, 0, 0, 0, 0, left, right, 0, 0),
    )
