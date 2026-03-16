#!/usr/bin/env python3
"""
Generate sterile monochrome SVG status icons for:

- Internet connectivity: curved tapered cone (levels 0..4)
    - internet_0 is full-size outline only
    - internet_1..4 are filled, growing from small to large

- Volume: rounded-stroke waves (levels 1..3, plus muted)
    - source is a very short rounded stroke so it reads like a dot
    - volume_muted is full volume plus a strike-through

- Power: stroked power symbol
    - sized and weighted to match the other generated icons

Exports SVG files into ./generated_icons by default.

Usage:
    python generate_status_icons.py

All icons use `currentColor` so they inherit your UI/theme color.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

VIEWBOX_SIZE = 24
OUTPUT_DIR = Path("generated_icons")


@dataclass(frozen=True)
class InternetLevelSpec:
    height: float


# 0 is outline-only, but uses the same full-size geometry as level 4
INTERNET_LEVELS: dict[int, InternetLevelSpec] = {
    0: InternetLevelSpec(height=18.0),
    1: InternetLevelSpec(height=9.0),
    2: InternetLevelSpec(height=12.0),
    3: InternetLevelSpec(height=15.0),
    4: InternetLevelSpec(height=18.0),
}

# Internet icon placement / softness
INTERNET_CENTER_X = 12.0
INTERNET_BOTTOM_Y = 20.0
INTERNET_CONE_SPAN_DEG = 46.0
INTERNET_TOP_CORNER_RADIUS = 1.2
INTERNET_TOP_CURVE_DEPTH = 3
INTERNET_TIP_SOFTEN = 0.95

# Shoulder shaping as ratios of the top half-width / height
INTERNET_SHOULDER_INSET_RATIO = 0.28
INTERNET_SHOULDER_DROP_RATIO = 0.30
INTERNET_MIN_SHOULDER_INSET = 0.9
INTERNET_MIN_SHOULDER_DROP = 1.4

# Outline for internet_0
INTERNET_OUTLINE_STROKE = 2.2
INTERNET_OUTLINE_CAP = "round"
INTERNET_OUTLINE_JOIN = "round"


# Volume icon geometry
VOLUME_STROKE = 2.2
VOLUME_STROKE_LINECAP = "round"
VOLUME_STROKE_LINEJOIN = "round"

# Source is a short rounded vertical stroke so it reads like a dot.
VOLUME_SOURCE_X = 6.2
VOLUME_SOURCE_Y1 = 10.95
VOLUME_SOURCE_Y2 = 13.05

# Right-opening arcs
VOLUME_CENTER_X = 7.4
VOLUME_CENTER_Y = 12.0

# Radii for inner/mid/outer arcs
VOLUME_ARC_RADII = {
    1: 4.95,
    2: 7.68,
    3: 10.4,
}

# Arc span in degrees, symmetric around 0
VOLUME_ARC_SPAN_DEG = 46.0

# Muted slash
MUTE_SLASH_X1 = 7.8
MUTE_SLASH_Y1 = 6.7
MUTE_SLASH_X2 = 18.3
MUTE_SLASH_Y2 = 17.3
MUTE_SLASH_STROKE = 2.35


# Power icon geometry
POWER_STROKE = 2.2
POWER_LINECAP = "round"
POWER_LINEJOIN = "round"

POWER_CENTER_X = 12.0
POWER_CENTER_Y = 12.0

# Outer ring, with a gap at the top for the stem
POWER_RADIUS = 6.7
POWER_ARC_START_DEG = -50.0
POWER_ARC_END_DEG = 230.0

# Vertical stem
POWER_STEM_X = 12.0
POWER_STEM_Y1 = 4.2
POWER_STEM_Y2 = 11.1


# ---------------------------------------------------------------------
# SVG helpers
# ---------------------------------------------------------------------

def fmt(value: float) -> str:
    """Compact float formatting for SVG output."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text if text else "0"


def svg_document(body: str, viewbox_size: int = VIEWBOX_SIZE) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {viewbox_size} {viewbox_size}" fill="none">
{body}
</svg>
"""


def polar_to_cartesian(cx: float, cy: float, radius: float, angle_deg: float) -> tuple[float, float]:
    angle_rad = math.radians(angle_deg)
    return (
        cx + radius * math.cos(angle_rad),
        cy + radius * math.sin(angle_rad),
    )


def svg_arc_path(
    cx: float,
    cy: float,
    radius: float,
    start_deg: float,
    end_deg: float,
) -> str:
    """
    Create a simple SVG arc path from start angle to end angle.
    Assumes a small arc (< 180 degrees).
    """
    x1, y1 = polar_to_cartesian(cx, cy, radius, start_deg)
    x2, y2 = polar_to_cartesian(cx, cy, radius, end_deg)
    large_arc_flag = 0
    sweep_flag = 1
    return (
        f"M {fmt(x1)} {fmt(y1)} "
        f"A {fmt(radius)} {fmt(radius)} 0 {large_arc_flag} {sweep_flag} {fmt(x2)} {fmt(y2)}"
    )


def svg_arc_path_auto(
    cx: float,
    cy: float,
    radius: float,
    start_deg: float,
    end_deg: float,
) -> str:
    """
    Create an SVG arc path and automatically choose the large-arc flag
    based on the angular span.
    """
    x1, y1 = polar_to_cartesian(cx, cy, radius, start_deg)
    x2, y2 = polar_to_cartesian(cx, cy, radius, end_deg)

    delta = (end_deg - start_deg) % 360.0
    large_arc_flag = 1 if delta > 180.0 else 0
    sweep_flag = 1

    return (
        f"M {fmt(x1)} {fmt(y1)} "
        f"A {fmt(radius)} {fmt(radius)} 0 {large_arc_flag} {fmt(sweep_flag)} {fmt(x2)} {fmt(y2)}"
    )


def write_svg(path: Path, body: str) -> None:
    path.write_text(svg_document(body), encoding="utf-8")


# ---------------------------------------------------------------------
# Internet icon generation
# ---------------------------------------------------------------------

def internet_top_width_from_height(height: float, cone_span_deg: float) -> float:
    """
    Derive top width from height using a symmetric cone spread around the vertical axis.
    """
    half_width = height * math.tan(math.radians(cone_span_deg / 2.0))
    return half_width * 2.0


def internet_wedge_path(
    cx: float,
    bottom_y: float,
    height: float,
    cone_span_deg: float,
    top_corner_radius: float,
    top_curve_depth: float,
    tip_soften: float,
) -> str:
    """
    Symmetrical tapered cone with:
    - shallow crowned top
    - softened shoulders
    - softened tip

    Overall flare is driven by cone_span_deg.
    """
    top_width = internet_top_width_from_height(height, cone_span_deg)
    half_top = top_width / 2.0
    top_y = bottom_y - height

    left_top_x = cx - half_top
    right_top_x = cx + half_top

    r = min(top_corner_radius, half_top * 0.45, height * 0.22)

    # Shallow crowned top
    top_mid_x = cx
    top_mid_y = top_y - top_curve_depth

    # Shoulder points that lead down toward the tip
    shoulder_inset = max(half_top * INTERNET_SHOULDER_INSET_RATIO, INTERNET_MIN_SHOULDER_INSET)
    shoulder_drop = max(height * INTERNET_SHOULDER_DROP_RATIO, INTERNET_MIN_SHOULDER_DROP)

    left_shoulder_x = cx - shoulder_inset
    left_shoulder_y = bottom_y - shoulder_drop
    right_shoulder_x = cx + shoulder_inset
    right_shoulder_y = left_shoulder_y

    tip_ctrl_y = bottom_y - tip_soften

    d = (
        f"M {fmt(left_top_x + r)} {fmt(top_y)} "
        f"Q {fmt(top_mid_x)} {fmt(top_mid_y)} {fmt(right_top_x - r)} {fmt(top_y)} "
        f"Q {fmt(right_top_x)} {fmt(top_y)} {fmt(right_top_x)} {fmt(top_y + r)} "
        f"L {fmt(right_shoulder_x)} {fmt(right_shoulder_y)} "
        f"Q {fmt(cx)} {fmt(tip_ctrl_y)} {fmt(left_shoulder_x)} {fmt(left_shoulder_y)} "
        f"L {fmt(left_top_x)} {fmt(top_y + r)} "
        f"Q {fmt(left_top_x)} {fmt(top_y)} {fmt(left_top_x + r)} {fmt(top_y)} "
        f"Z"
    )
    return d


def make_internet_icon(level: int) -> str:
    spec = INTERNET_LEVELS[level]
    path_d = internet_wedge_path(
        cx=INTERNET_CENTER_X,
        bottom_y=INTERNET_BOTTOM_Y,
        height=spec.height,
        cone_span_deg=INTERNET_CONE_SPAN_DEG,
        top_corner_radius=INTERNET_TOP_CORNER_RADIUS,
        top_curve_depth=INTERNET_TOP_CURVE_DEPTH,
        tip_soften=INTERNET_TIP_SOFTEN,
    )

    if level == 0:
        return (
            f'  <path d="{path_d}" fill="none" stroke="currentColor" '
            f'stroke-width="{fmt(INTERNET_OUTLINE_STROKE)}" '
            f'stroke-linecap="{INTERNET_OUTLINE_CAP}" '
            f'stroke-linejoin="{INTERNET_OUTLINE_JOIN}"/>'
        )

    return f'  <path d="{path_d}" fill="currentColor"/>'


# ---------------------------------------------------------------------
# Volume icon generation
# ---------------------------------------------------------------------

def make_volume_source() -> str:
    return (
        f'  <path d="M {fmt(VOLUME_SOURCE_X)} {fmt(VOLUME_SOURCE_Y1)} '
        f'L {fmt(VOLUME_SOURCE_X)} {fmt(VOLUME_SOURCE_Y2)}" '
        f'stroke="currentColor" stroke-width="{fmt(VOLUME_STROKE)}" '
        f'stroke-linecap="{VOLUME_STROKE_LINECAP}" stroke-linejoin="{VOLUME_STROKE_LINEJOIN}"/>'
    )


def make_volume_arc(level: int) -> str:
    radius = VOLUME_ARC_RADII[level]
    d = svg_arc_path(
        cx=VOLUME_CENTER_X,
        cy=VOLUME_CENTER_Y,
        radius=radius,
        start_deg=-VOLUME_ARC_SPAN_DEG,
        end_deg=VOLUME_ARC_SPAN_DEG,
    )
    return (
        f'  <path d="{d}" stroke="currentColor" stroke-width="{fmt(VOLUME_STROKE)}" '
        f'stroke-linecap="{VOLUME_STROKE_LINECAP}" stroke-linejoin="{VOLUME_STROKE_LINEJOIN}"/>'
    )


def make_mute_slash() -> str:
    return (
        f'  <path d="M {fmt(MUTE_SLASH_X1)} {fmt(MUTE_SLASH_Y1)} '
        f'L {fmt(MUTE_SLASH_X2)} {fmt(MUTE_SLASH_Y2)}" '
        f'stroke="currentColor" stroke-width="{fmt(MUTE_SLASH_STROKE)}" '
        f'stroke-linecap="{VOLUME_STROKE_LINECAP}" stroke-linejoin="{VOLUME_STROKE_LINEJOIN}"/>'
    )


def make_volume_icon(level: int) -> str:
    parts = [make_volume_source()]
    for arc_level in range(1, level + 1):
        parts.append(make_volume_arc(arc_level))
    return "\n".join(parts)


def make_volume_muted_icon() -> str:
    parts = [make_volume_source()]
    for arc_level in range(1, 4):
        parts.append(make_volume_arc(arc_level))
    parts.append(make_mute_slash())
    return "\n".join(parts)


# ---------------------------------------------------------------------
# Power icon generation
# ---------------------------------------------------------------------

def make_power_ring() -> str:
    d = svg_arc_path_auto(
        cx=POWER_CENTER_X,
        cy=POWER_CENTER_Y,
        radius=POWER_RADIUS,
        start_deg=POWER_ARC_START_DEG,
        end_deg=POWER_ARC_END_DEG,
    )
    return (
        f'  <path d="{d}" stroke="currentColor" stroke-width="{fmt(POWER_STROKE)}" '
        f'stroke-linecap="{POWER_LINECAP}" stroke-linejoin="{POWER_LINEJOIN}"/>'
    )


def make_power_stem() -> str:
    return (
        f'  <path d="M {fmt(POWER_STEM_X)} {fmt(POWER_STEM_Y1)} '
        f'L {fmt(POWER_STEM_X)} {fmt(POWER_STEM_Y2)}" '
        f'stroke="currentColor" stroke-width="{fmt(POWER_STROKE)}" '
        f'stroke-linecap="{POWER_LINECAP}" stroke-linejoin="{POWER_LINEJOIN}"/>'
    )


def make_power_icon() -> str:
    return "\n".join([
        make_power_ring(),
        make_power_stem(),
    ])


# ---------------------------------------------------------------------
# Main export
# ---------------------------------------------------------------------

def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def export_icons(output_dir: Path) -> None:
    ensure_output_dir(output_dir)

    # Internet icons
    for level in range(5):
        body = make_internet_icon(level)
        write_svg(output_dir / f"internet_{level}.svg", body)

    # Volume icons
    for level in range(1, 4):
        body = make_volume_icon(level)
        write_svg(output_dir / f"volume_{level}.svg", body)

    write_svg(output_dir / "volume_muted.svg", make_volume_muted_icon())

    # Power icon
    write_svg(output_dir / "power.svg", make_power_icon())


def main() -> int:
    export_icons(OUTPUT_DIR)
    print(f"Generated icons in: {OUTPUT_DIR.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())