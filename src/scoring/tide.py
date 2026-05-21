"""
Tij-scoring en gerelateerde helpers.

Tide-flank bonus, velocity schatter, score_tide_component met periode-
afhankelijk venster + spring/doodtij modulator + timing-fit + tidal-current.
"""
import math
from datetime import datetime
from typing import Optional

from src.config import SCORING_WEIGHTS, TIDE_FLANK
from src.util import to_utc


def tide_flank_bonus(tide_level_normalized: float, is_rising: bool) -> float:
    """
    Bonus voor mid-tide flank (NL beachbreak sweet spot).

    Mid-rising = +2, mid-falling = +1, buiten flanken = 0.
    """
    if not (TIDE_FLANK['mid_low'] <= tide_level_normalized <= TIDE_FLANK['mid_high']):
        return 0.0
    return TIDE_FLANK['mid_rising_bonus'] if is_rising else TIDE_FLANK['mid_falling_bonus']


def tide_velocity_mh(
    last_turn_time: Optional[datetime],
    next_turn_time: Optional[datetime],
    tide_range_m: Optional[float],
) -> float:
    """
    Schatting verticale tij-snelheid (m/u) — piek-snelheid mid-cycle.

    Halve cyclus ~6.2u in NL. Peak velocity = π · range / (2 · half_cycle_h).
    """
    if not (last_turn_time and next_turn_time and tide_range_m):
        return 0.0
    last = last_turn_time.replace(tzinfo=None) if last_turn_time.tzinfo else last_turn_time
    nxt = next_turn_time.replace(tzinfo=None) if next_turn_time.tzinfo else next_turn_time
    half_cycle_h = (nxt - last).total_seconds() / 3600.0
    if half_cycle_h <= 0:
        return 0.0
    return math.pi * tide_range_m / (2.0 * half_cycle_h)


def score_tide_component(
    tide_level_normalized: float,
    tide_phase: str,
    dominant_period_s: float = 8.0,
    tide_range_m: Optional[float] = None,
    hours_to_next_high: Optional[float] = None,
    tidal_current_intensity: float = 0.0,
) -> float:
    """
    Bereken tij score (max 20 punten).

    Periode-afhankelijk optimaal venster, spring/doodtij modulator, phase-bonus,
    timing-fit, tidal-current penalty (kwadratisch), mid-flank bonus.
    """
    # 1) Optimaal niveau-venster per dominante periode.
    if dominant_period_s >= 9:
        lo, hi = 0.20, 0.90
    elif dominant_period_s >= 7:
        lo, hi = 0.30, 0.85
    else:
        lo, hi = 0.35, 0.85

    # 2) Spring/doodtij modulator.
    if tide_range_m is not None:
        if tide_range_m >= 2.0:
            lo += 0.05
            hi -= 0.05
        elif tide_range_m < 1.6:
            lo = max(0.0, lo - 0.025)
            hi = min(1.0, hi + 0.025)

    # 3) Level-score binnen het venster (vlakke max), lineair afval daarbuiten.
    if lo <= tide_level_normalized <= hi:
        level_score = 18.0
    elif tide_level_normalized < lo:
        level_score = (tide_level_normalized / lo) * 17 if lo > 0 else 0.0
    else:
        level_score = ((1.0 - tide_level_normalized) / (1.0 - hi)) * 17 if hi < 1 else 0.0

    # 4) Phase-bonus voor opgaand.
    phase_bonus = 2.0 if tide_phase == "opgaand" else 0.0

    # 5) Timing-fit: opgaand + 1-2.5u vóór HW.
    timing_bonus = 0.0
    if (tide_phase == "opgaand"
            and hours_to_next_high is not None
            and 1.0 <= hours_to_next_high <= 2.5):
        timing_bonus = 1.0

    # 6) Tidal-current penalty (kwadratisch, max -8pt).
    current_penalty = -8.0 * (tidal_current_intensity ** 2)

    # 7) Tide-flank bonus.
    is_rising = (tide_phase == "opgaand")
    flank_bonus = tide_flank_bonus(tide_level_normalized, is_rising)

    total = level_score + phase_bonus + timing_bonus + current_penalty + flank_bonus
    return max(0.0, min(SCORING_WEIGHTS['tide_max'], total))


def hours_until(when: datetime, target: Optional[datetime]) -> Optional[float]:
    """
    Aantal uren tussen `when` en `target` (positief als toekomst).

    Naive (Open-Meteo) en aware (RWS) timestamps consistent via to_utc.
    """
    if target is None:
        return None
    delta = (to_utc(target) - to_utc(when)).total_seconds() / 3600.0
    return delta if delta >= 0 else None
