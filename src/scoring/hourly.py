"""
Per-uur scoring module.
Berekent scores voor golf, wind, tij en swell richting.
"""
import logging
from datetime import datetime
from typing import Optional
import math

from src.util import to_utc

from src.data.models import (
    HourState,
    ScoreBreakdown,
    WaveSpectrum,
    SpectralPeak,
    SwellType
)

from src.config import (
    NOORDWIJK,
    SCORING_WEIGHTS,
    WIND_DIRECTIONS
)

from src.scoring.deconstruct import (
    decompose_spectrum,
    has_groundswell_through_windsea,
    is_clean_swell
)
from src.scoring.daylight import is_daylight_noordwijk

logger = logging.getLogger(__name__)


def score_golf_component(wave_spectrum: WaveSpectrum) -> float:
    """
    Bereken golf score (max 40 punten).

    Factoren:
    - Totale hoogte (0-1.0m = 0-20pt, 1.0-2.0m = 20-40pt, >2.0m = 40pt)
    - Swell type (groundswell = 1.2x multiplier, wind swell = 1.0x, wind sea = 0.8x)
    - Groundswell door wind sea bonus (+1pt)
    - Clean swell bonus (+1pt)

    Bonussen zijn klein zodat een 1.4m golf niet aan dezelfde cap zit als een 2m+ golf.
    """
    decomposition = decompose_spectrum(wave_spectrum)
    total_height = decomposition['total_height']

    if total_height < 0.5:
        height_score = 0
    elif total_height < 1.0:
        height_score = (total_height - 0.5) * 40
    elif total_height < 2.0:
        height_score = 20 + (total_height - 1.0) * 20
    else:
        height_score = 40

    if decomposition['ground_swell']:
        type_multiplier = 1.2
    elif decomposition['wind_swell']:
        type_multiplier = 1.0
    else:
        type_multiplier = 0.8

    height_score *= type_multiplier

    if has_groundswell_through_windsea(wave_spectrum):
        height_score += 1

    if is_clean_swell(wave_spectrum):
        height_score += 1

    return min(SCORING_WEIGHTS['golf_max'], height_score)


def score_wind_component(wind_speed_kn: float, wind_direction_deg: int) -> float:
    """
    Bereken wind score (max 35 punten).

    Factoren:
    - Snelheid (0-5kn = 35pt, 5-10kn = 30-25pt, 10-15kn = 25-10pt, >15kn = 10-0pt)
    - Richting (offshore = 1.2x, side-offshore = 1.0x, onshore = 0.5x)
    """
    # Score op snelheid (lager is beter)
    if wind_speed_kn <= 5:
        speed_score = 35
    elif wind_speed_kn <= 10:
        speed_score = 30 - (wind_speed_kn - 5) * 1.0  # 30-25pt
    elif wind_speed_kn <= 15:
        speed_score = 25 - (wind_speed_kn - 10) * 3.0  # 25-10pt
    else:
        speed_score = max(0, 10 - (wind_speed_kn - 15) * 2.0)  # 10-0pt

    # Richting multiplier. Side-offshore is cross-shore, niet vergelijkbaar met
    # echte offshore — daarom 0.73 ipv 1.0.
    if WIND_DIRECTIONS['offshore'][0] <= wind_direction_deg <= WIND_DIRECTIONS['offshore'][1]:
        direction_multiplier = 1.2
    elif WIND_DIRECTIONS['side_offshore'][0] <= wind_direction_deg <= WIND_DIRECTIONS['side_offshore'][1]:
        direction_multiplier = 0.73
    elif WIND_DIRECTIONS['onshore'][0] <= wind_direction_deg <= WIND_DIRECTIONS['onshore'][1]:
        direction_multiplier = 0.5
    else:
        direction_multiplier = 0.8  # Side-onshore

    return min(SCORING_WEIGHTS['wind_max'], speed_score * direction_multiplier)


def score_tide_component(
    tide_level_normalized: float,
    tide_phase: str,
    dominant_period_s: float = 8.0,
    tide_range_m: Optional[float] = None,
    hours_to_next_high: Optional[float] = None,
) -> float:
    """
    Bereken tij score (max 20 punten).

    Periode-afhankelijk optimaal niveau-venster (blok 2):
    - Groundswell T≥9s: venster [0.20, 0.90] — lange swell voelt bodem eerder,
      werkt op breder tij-venster.
    - Wind-swell 7-9s: venster [0.35, 0.85] — middenklasse.
    - Wind-sea T<7s: venster [0.50, 0.90] — korte swell heeft hoger water nodig
      om door te breken op NL-zandbanken.

    Spring/doodtij modulator:
    - Springtij (range ≥ 2.0m): venster krimpt 5% aan beide zijden (sterkere
      cross-shore stroming verkort het surfbare venster).
    - Doodtij (range < 1.6m): venster verbreedt 2.5% (stabieler water).

    Phase bonus +2 voor opgaand (push laag→mid is gunstig op NL-beachbreaks).

    Timing-fit modifier: +1 wanneer we 1-2.5u vóór hoogtij zitten én tij stijgt
    (klassieke "push naar HW" — surfweer-conventie). Soft cap blijft op
    SCORING_WEIGHTS['tide_max'].
    """
    # 1) Bepaal optimaal niveau-venster op basis van dominante periode
    if dominant_period_s >= 9:
        lo, hi = 0.20, 0.90
    elif dominant_period_s >= 7:
        lo, hi = 0.35, 0.85
    else:
        lo, hi = 0.50, 0.90

    # 2) Spring/doodtij modulator op het venster
    if tide_range_m is not None:
        if tide_range_m >= 2.0:
            lo += 0.05
            hi -= 0.05
        elif tide_range_m < 1.6:
            lo = max(0.0, lo - 0.025)
            hi = min(1.0, hi + 0.025)

    # 3) Level-score binnen het venster (vlakke max), lineair afval daarbuiten
    if lo <= tide_level_normalized <= hi:
        level_score = 18.0
    elif tide_level_normalized < lo:
        level_score = (tide_level_normalized / lo) * 17 if lo > 0 else 0.0
    else:
        level_score = ((1.0 - tide_level_normalized) / (1.0 - hi)) * 17 if hi < 1 else 0.0

    # 4) Phase-bonus voor opgaand tij
    phase_bonus = 2.0 if tide_phase == "opgaand" else 0.0

    # 5) Timing-fit: kleine bonus voor "push naar HW" (opgaand én 1-2.5u vóór HW)
    timing_bonus = 0.0
    if (tide_phase == "opgaand"
            and hours_to_next_high is not None
            and 1.0 <= hours_to_next_high <= 2.5):
        timing_bonus = 1.0

    return min(SCORING_WEIGHTS['tide_max'], level_score + phase_bonus + timing_bonus)


def score_swell_direction_bonus(swell_direction_deg: int) -> float:
    """
    Bereken swell richting bonus voor Noordwijk (max 10 punten).

    Geblokkeerde sector (pier IJmuiden) krijgt 0 punten. Buiten dat gelden
    voorkeuren: klassieke NL swell (W/NW/N) hoog, NO redelijk, zuid laag.
    """
    direction = swell_direction_deg % 360

    # Geblokkeerd door obstakels (bv. pier van IJmuiden): wrap-around-range.
    blocked_min = NOORDWIJK.blocked_swell_dir_min
    blocked_max = NOORDWIJK.blocked_swell_dir_max
    if not (blocked_min == 0 and blocked_max == 0):
        if blocked_min <= blocked_max:
            is_blocked = blocked_min <= direction <= blocked_max
        else:  # wrap-around: bv. 350-30 → 350-360 én 0-30
            is_blocked = direction >= blocked_min or direction <= blocked_max
        if is_blocked:
            return 0.0

    # Beste richtingen: W -> N
    if 270 <= direction <= 360:
        return 10.0

    # Goede richtingen: NO/ONO
    if 45 <= direction <= 90:
        return 8.0

    # Redelijke richtingen: NNO (niet geblokkeerd deel) / O / ZO
    if 0 <= direction <= 45 or 90 <= direction <= 135:
        return 5.0

    # Mindere richtingen: Z/ZZO/ZZW
    if 135 <= direction <= 225:
        return 3.0

    return 5.0


def _dominant_period_for_tide(spectrum: WaveSpectrum) -> float:
    """
    Bepaal de dominante swell-periode voor tij-scoring.

    Voorkeur: hoogste spectrale piek; fallback: spectrum.mean_period; bij geen
    info default 8.0 (mid-band — neutraal venster).
    """
    if spectrum.peaks:
        dominant = max(spectrum.peaks, key=lambda p: p.height_m)
        return dominant.period_s
    if spectrum.mean_period and spectrum.mean_period > 0:
        return spectrum.mean_period
    return 8.0


def _hours_until(when: datetime, target: Optional[datetime]) -> Optional[float]:
    """
    Aantal uren tussen `when` en `target` (positief als target in toekomst).
    Naive datetimes (Open-Meteo) worden als Europe/Amsterdam local geïnterpreteerd,
    aware datetimes (RWS) worden naar UTC genormaliseerd — beide consistent.
    """
    if target is None:
        return None
    delta = (to_utc(target) - to_utc(when)).total_seconds() / 3600.0
    return delta if delta >= 0 else None


def score_hour(state: HourState) -> ScoreBreakdown:
    """
    Bereken totale score voor één uur.

    Args:
        state: HourState met alle data

    Returns:
        ScoreBreakdown met component scores en totaal
    """
    # Daglicht-filter: 's nachts surfen is op Noordwijk niet zinvol. Score = 0
    # zodat night-uren niet in surf-windows of als piek-uren verschijnen.
    if not is_daylight_noordwijk(state.timestamp):
        return ScoreBreakdown(
            timestamp=state.timestamp,
            golf_score=0.0,
            wind_score=0.0,
            tide_score=0.0,
            swell_dir_bonus=0.0,
        )

    # Golf component
    golf_score = score_golf_component(state.wave_spectrum)

    # Wind component
    wind_score = score_wind_component(state.wind.speed_kn, state.wind.direction_deg)

    # Tij component — periode-afhankelijk venster + spring/doodtij + timing-fit
    dominant_period_s = _dominant_period_for_tide(state.wave_spectrum)
    hours_to_high = _hours_until(state.timestamp, state.tide.next_high)
    tide_score = score_tide_component(
        state.tide.normalized_level,
        state.tide.phase,
        dominant_period_s=dominant_period_s,
        tide_range_m=state.tide.daily_range_m,
        hours_to_next_high=hours_to_high,
    )

    # Swell richting bonus (gebruik dominant swell richting)
    decomposition = decompose_spectrum(state.wave_spectrum)
    swell_dir_deg = 0

    if decomposition['ground_swell']:
        swell_dir_deg = decomposition['ground_swell'].direction_deg
    elif decomposition['wind_swell']:
        swell_dir_deg = decomposition['wind_swell'].direction_deg
    elif decomposition['wind_sea']:
        swell_dir_deg = decomposition['wind_sea'].direction_deg
    else:
        swell_dir_deg = state.wave_spectrum.mean_direction

    swell_dir_bonus = score_swell_direction_bonus(swell_dir_deg)

    return ScoreBreakdown(
        timestamp=state.timestamp,
        golf_score=golf_score,
        wind_score=wind_score,
        tide_score=tide_score,
        swell_dir_bonus=swell_dir_bonus
    )


def calculate_confidence(forecast_sources: dict) -> float:
    """
    Bereken confidence score op basis van model spread.

    Args:
        forecast_sources: Dictionary met forecasts van verschillende modellen

    Returns:
        Confidence score 0.0-1.0
    """
    if len(forecast_sources) <= 1:
        return 1.0  # Geen spread als één model

    # Bereken spread in wind speed en richting
    wind_speeds = []
    wind_directions = []

    for model_name, forecast_data in forecast_sources.items():
        if forecast_data and len(forecast_data) > 0:
            # Gebruik eerste uur als voorbeeld
            first_hour = forecast_data[0]
            if 'wind_speed' in first_hour:
                wind_speeds.append(first_hour['wind_speed'])
            if 'wind_direction' in first_hour:
                wind_directions.append(first_hour['wind_direction'])

    if len(wind_speeds) <= 1:
        return 1.0

    # Bereken standaard deviatie
    wind_speed_std = math.sqrt(sum((x - sum(wind_speeds) / len(wind_speeds)) ** 2 for x in wind_speeds) / len(wind_speeds))

    # Confidence: lage spread = hoge confidence
    # Spread van 0kn = confidence 1.0, spread van 20kn = confidence 0.5
    confidence = max(0.5, 1.0 - (wind_speed_std / 40.0))

    return confidence