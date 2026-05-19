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
    SURF_MINIMUMS,
    WIND_DIRECTIONS,
)


def _wind_direction_cosine(wind_dir_deg: int, beach_normal_deg: int) -> float:
    """
    Cosinus van de hoek tussen de wind-richting en de pure offshore-richting.

    Pure offshore = anti-beach-normal (beach faces 285° → offshore wind FROM 105°).
    Return value: +1.0 = pure offshore, 0.0 = pure cross-shore, −1.0 = pure onshore.

    Dit vervangt de oude 4-bucket aanpak (offshore/side-offshore/onshore) met een
    continu signaal dat geen kunstmatige sprongen rond 225°/315° heeft.
    """
    offshore_dir = (beach_normal_deg + 180) % 360
    delta_raw = (wind_dir_deg - offshore_dir) % 360
    delta = delta_raw if delta_raw <= 180 else 360 - delta_raw
    return math.cos(math.radians(delta))


def recommend_boards(
    hs_m: float,
    tp_s: float,
    wind_speed_kn: float,
    wind_direction_deg: int,
    beach_normal_deg: int = None,
) -> list:
    """
    Geef terug welke board-types op dit moment surfbaar zijn.

    Returns: subset van ['longboard', 'midlength', 'fish', 'shortboard'].
    Lege lijst = niet surfbaar (te klein, te korte periode, of stormwind).

    Categorisering (NL-context, ervaring + Tobias' lexicon):

    - **Longboard** (8'-10'+): drijfvermogen vangt elke knietjeshoge golf.
      Min Hs 0.30m. Tolereert chop en aanlandige wind redelijk.

    - **Midlength** (6'8"-7'10"): brug tussen long en short, iets minder
      forgiving dan longboard. Min Hs 0.40m.

    - **Fish** (5'4"-6'2" breed): houdt van punchy wind-sea, snel paddel.
      Heeft wat power nodig — min Hs 0.50m. Begint te falen bij >25kn wind.

    - **Shortboard** (5'8"-6'4" thruster): wil clean face, voldoende energie.
      Min Hs 1.00m, Tp ≥ 5s. Sterke aanlandige wind (cos ≤ -0.5, >12kn)
      maakt shortboard frustrerend onmogelijk; longboard kan dan nog wel.

    Hard floor: Hs < 0.30m OF Tp < 4.0s OF wind > 28kn = niets.
    """
    from src.config import SURF_MINIMUMS

    if hs_m < SURF_MINIMUMS['min_hs_m']:
        return []
    if tp_s < SURF_MINIMUMS['min_period_s']:
        return []
    if wind_speed_kn > 28:
        return []  # storm-wind, alles plat

    if beach_normal_deg is None:
        beach_normal_deg = NOORDWIJK.beach_normal_deg

    cos_offshore = _wind_direction_cosine(wind_direction_deg, beach_normal_deg)

    boards = []

    # Longboard: laagste lat. Pakt knietjeshoge golven, drijfvermogen wint.
    if hs_m >= SURF_MINIMUMS['min_hs_longboard_m']:
        boards.append('longboard')

    # Midlength: iets meer hoogte nodig dan longboard.
    if hs_m >= SURF_MINIMUMS['min_hs_midlength_m']:
        boards.append('midlength')

    # Fish: wil echte energie + niet té veel wind (anders gewoon rommel).
    if hs_m >= SURF_MINIMUMS['min_hs_fish_m'] and wind_speed_kn < 25:
        boards.append('fish')

    # Shortboard: strenge criteria — moet écht werken.
    # Min hoogte 1.0m, min periode 5s, EN clean face (geen significante onshore).
    # cos > -0.3 = max side-onshore acceptabel. Pure cross-shore (cos=0) of meer
    # offshore is prima; alles wat 30%+ onshore is, ruïneert de wave face voor
    # shortboard maar laat longboard wel werken.
    # Uitzondering: lichte wind (≤10kn) kan zelfs bij onshore nog shortboard
    # toelaten omdat de chop minimaal is.
    if (hs_m >= SURF_MINIMUMS['min_hs_shortboard_m']
            and tp_s >= SURF_MINIMUMS['min_period_shortboard_s']):
        if cos_offshore > -0.3 or wind_speed_kn <= 10:
            boards.append('shortboard')

    return boards

from src.scoring.deconstruct import (
    decompose_spectrum,
    has_groundswell_through_windsea,
    is_clean_swell
)
from src.scoring.daylight import is_daylight_noordwijk

logger = logging.getLogger(__name__)


def _period_factor(period_s: float) -> float:
    """
    Continue periode-multiplier voor golf-score.

    Vervangt de oude binaire type_multiplier (0.8/1.0/1.2 op basis van
    SwellType bucket). Tobias' eigen uitleg: "vanaf 5s wordt het pas een
    beetje surfbaar; ideaal is 6,5-7s omdat dan niet te veel energie
    verloren gaat over de zandbanken". Voor groundswell (≥9s) extra premium.

    Curve:
      <4s    : 0.60  (te kort, choppy/onsurfbaar)
      4-5s   : 0.60 → 0.75 (wind sea)
      5-6s   : 0.75 → 0.85
      6-7s   : 0.85 → 1.00 (NL sweet spot wind-swell)
      7-9s   : 1.00 → 1.15 (kwaliteits-windswell)
      9-13s  : 1.15 (groundswell plateau)
      13-17s : 1.15 → 0.95 (te lang, beachbreak closeout)
      >17s   : 0.90
    """
    T = period_s
    if T < 4:
        return 0.60
    if T < 5:
        return 0.60 + (T - 4) * 0.15
    if T < 6:
        return 0.75 + (T - 5) * 0.10
    if T < 7:
        return 0.85 + (T - 6) * 0.15
    if T < 9:
        return 1.00 + (T - 7) * 0.075
    if T < 13:
        return 1.15
    if T < 17:
        return 1.15 - (T - 13) * 0.05
    return 0.90


def score_golf_component(wave_spectrum: WaveSpectrum) -> float:
    """
    Bereken golf score (max SCORING_WEIGHTS['golf_max']).

    Factoren:
    - Totale hoogte (0-1.0m = 0-20pt, 1.0-2.0m = 20-40pt, >2.0m = 40pt)
    - Periode-factor (continue curve, optimum 7-13s — zie `_period_factor`)
    - Groundswell door wind sea bonus (+1pt)
    - Clean swell bonus (+1pt)

    Periode komt uit de dominante (hoogste) spectrale piek; bij geen pieken
    gebruiken we de Tm02 mean_period uit het spectrum.
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

    # Periode uit dominante piek; fallback mean_period
    if wave_spectrum.peaks:
        dominant = max(wave_spectrum.peaks, key=lambda p: p.height_m)
        T = dominant.period_s
    else:
        T = wave_spectrum.mean_period or 5.0

    height_score *= _period_factor(T)

    if has_groundswell_through_windsea(wave_spectrum):
        height_score += 1

    if is_clean_swell(wave_spectrum):
        height_score += 1

    return min(SCORING_WEIGHTS['golf_max'], height_score)


def score_wind_component(wind_speed_kn: float, wind_direction_deg: int) -> float:
    """
    Bereken wind score (max SCORING_WEIGHTS['wind_max'], default 32).

    Speed + direction worden ADDITIEF gecombineerd, niet multiplicatief.
    Oude versie was te punitief op 15-22 kn (cruciaal NL-bereik):
    een 17 kn ZW wind kreeg score 3 en filterde Tobias' longboard-windows weg.

    Speed-score (max 25, monotoon dalend):
      0-8 kn   : 25      (sweet spot offshore én onshore)
      8-15 kn  : 25 → 17 (toenemend chop)
      15-22 kn : 17 → 8  (rideable, longboard-territorium NL)
      22-30 kn : 8 → 0   (stormwind, surf-onvriendelijk)

    Direction-bonus (additief, ±7):
      pure offshore : +7
      pure cross    : 0
      pure onshore  : -7
      Continu via cosinus, geen sprongen.

    Totaal wordt geclamped op [0, wind_max].
    """
    # Speed-score in segmenten — monotoon dalend, gladde overgangen
    if wind_speed_kn <= 8:
        speed_score = 25.0
    elif wind_speed_kn <= 15:
        speed_score = 25.0 - (wind_speed_kn - 8) * (8.0 / 7.0)       # 25 → 17
    elif wind_speed_kn <= 22:
        speed_score = 17.0 - (wind_speed_kn - 15) * (9.0 / 7.0)      # 17 → 8
    elif wind_speed_kn <= 30:
        speed_score = max(0.0, 8.0 - (wind_speed_kn - 22) * (8.0 / 8.0))  # 8 → 0
    else:
        speed_score = 0.0

    # Direction-bonus: cosinus van hoek t.o.v. pure offshore, schaal ±7
    cos_term = _wind_direction_cosine(wind_direction_deg, NOORDWIJK.beach_normal_deg)
    direction_bonus = 7.0 * cos_term

    return max(0.0, min(SCORING_WEIGHTS['wind_max'], speed_score + direction_bonus))


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
    (klassieke "push naar HW" — surfweer-conventie).

    Tidal-current penalty (Tobias' "vloedstroom"): horizontale stroming piekt
    mid-cycle (3u na slack) en is nul op kentering. Sterke stroming maakt het
    moeilijker te peddelen en kort surf-vensters in. Penalty schaalt
    kwadratisch: penalty = -8 · intensity² (max -8pt bij springtij mid-cycle).

    Soft cap blijft op SCORING_WEIGHTS['tide_max'].
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

    # 6) Tidal-current penalty: piekt mid-cycle, nul op kentering.
    # Squared om de hoek mid-cycle te accentueren — kentering-flanks krijgen
    # nauwelijks penalty, mid-cycle krijgt vol penalty.
    current_penalty = -8.0 * (tidal_current_intensity ** 2)

    total = level_score + phase_bonus + timing_bonus + current_penalty
    return max(0.0, min(SCORING_WEIGHTS['tide_max'], total))


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

    # FYSIEKE MINIMUM-GATE: onder een absolute Hs- of Tp-drempel is GEEN bord
    # in NL surfbaar — ongeacht hoe goed de wind, het tij of de richting is.
    # Voorheen liet de combinatie "perfect wind + perfect tij + clean_swell-bonus"
    # een 0,16m wave-hour tot score 60 oplopen (= 'surfable'). Dit is fysiek
    # onmogelijk — Tobias noemt zo'n dag "windhoogte 20cm, rimpelsurf, niets aan".
    # De gate zet ALLES op 0 zodat het uur niet in windows of als piek verschijnt.
    Hs = state.wave_spectrum.significant_height_total
    Tp = _dominant_period_for_tide(state.wave_spectrum)
    if Hs < SURF_MINIMUMS['min_hs_m'] or Tp < SURF_MINIMUMS['min_period_s']:
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
    # + tidal-current penalty (Tobias' "vloedstroom" effect)
    dominant_period_s = _dominant_period_for_tide(state.wave_spectrum)
    hours_to_high = _hours_until(state.timestamp, state.tide.next_high)
    tidal_current = state.tide.tidal_current_intensity(state.timestamp)
    tide_score = score_tide_component(
        state.tide.normalized_level,
        state.tide.phase,
        dominant_period_s=dominant_period_s,
        tide_range_m=state.tide.daily_range_m,
        hours_to_next_high=hours_to_high,
        tidal_current_intensity=tidal_current,
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