"""
Context-helpers voor scoring: periode, board-aanbeveling, weersrisico's.

period_factor, dominant period, board recommendations, weather warnings
(convective, visibility, storm surge).
"""
from typing import Optional

from src.config import NOORDWIJK, SURF_MINIMUMS
from src.data.models import WaveSpectrum
from src.scoring.wave_modifiers import partition_energy_components
from src.scoring.wind import _wind_direction_cosine


def period_factor(period_s: float) -> float:
    """
    Continue periode-multiplier voor golf-score.

    NL sweet spot rond 6.5-7s (wind-swell), groundswell plateau 9-13s.
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


def dominant_period_partition_based(spectrum: WaveSpectrum) -> float:
    """
    Bepaal dominante periode via partition-energy (E ∝ H²·T).

    Een 1.1m@4s wind-sea verslaat geen 0.9m@12s groundswell in periode-keuze.
    """
    parts = partition_energy_components(spectrum)
    if parts['dominant_period_s'] and parts['dominant_period_s'] > 0:
        return parts['dominant_period_s']
    if spectrum.mean_period and spectrum.mean_period > 0:
        return spectrum.mean_period
    return 8.0


def recommend_boards(
    hs_m: float,
    tp_s: float,
    wind_speed_kn: float,
    wind_direction_deg: int,
    beach_normal_deg: Optional[int] = None,
) -> list:
    """
    Geef terug welke board-types op dit moment surfbaar zijn.

    Returns: subset van ['longboard', 'midlength', 'fish', 'shortboard'].
    Lege lijst = niet surfbaar (te klein/kort/stormig).
    """
    if hs_m < SURF_MINIMUMS['min_hs_m']:
        return []
    if tp_s < SURF_MINIMUMS['min_period_s']:
        return []
    if wind_speed_kn > 28:
        return []

    if beach_normal_deg is None:
        beach_normal_deg = NOORDWIJK.beach_normal_deg

    cos_offshore = _wind_direction_cosine(wind_direction_deg, beach_normal_deg)
    boards = []

    if hs_m >= SURF_MINIMUMS['min_hs_longboard_m']:
        boards.append('longboard')
    if hs_m >= SURF_MINIMUMS['min_hs_midlength_m']:
        boards.append('midlength')
    if hs_m >= SURF_MINIMUMS['min_hs_fish_m'] and wind_speed_kn < 25:
        boards.append('fish')
    if (hs_m >= SURF_MINIMUMS['min_hs_shortboard_m']
            and tp_s >= SURF_MINIMUMS['min_period_shortboard_s']):
        if cos_offshore > -0.3 or wind_speed_kn <= 10:
            boards.append('shortboard')

    return boards


def verdict_from_boards(boards: list) -> str:
    """Canoniek surf-verdict uit de board-aanbeveling — DEZELFDE bron als de
    verstuurde digest (src/llm/sms_fallback.py), zodat het gelogde snapshot-
    verdict niet meer kan divergeren van wat we mensen sturen.

    Board-based bleek empirisch accurater dan de peak_score-drempel: 61%/89%
    referentie-pariteit (exact/rideable) vs 54%/69% voor de peak_score-verdict.
    Het her-tieren van fish→longboard is expliciet NIET gedaan: dat maakte het
    juist slechter (36%), want de referentie labelt fish/windsee-dagen als surfable.

    - shortboard/fish/midlength aanwezig → 'surfable'
    - alleen longboard → 'longboard'
    - niets rijdbaar → 'flat'
    """
    if not boards:
        return 'flat'
    if any(b in boards for b in ('shortboard', 'fish', 'midlength')):
        return 'surfable'
    return 'longboard'


def convective_warning(cape: Optional[float], lifted_index: Optional[float]) -> bool:
    """
    Onweer-risico flag voor LLM-context.

    CAPE > 500 J/kg én lifted_index < -2 = onstabiele troposfeer.
    """
    if cape is None or lifted_index is None:
        return False
    try:
        return float(cape) > 500.0 and float(lifted_index) < -2.0
    except (TypeError, ValueError):
        return False


def visibility_concern(
    visibility_m: Optional[float],
    dew_point_c: Optional[float],
    air_temp_c: Optional[float],
) -> Optional[str]:
    """
    Classificeer zicht-condities als string-flag voor LLM.

    - "dichte_mist" / "haarmist_risico" / "matig_zicht" / "goed" / None.
    """
    if visibility_m is None:
        return None
    try:
        v = float(visibility_m)
    except (TypeError, ValueError):
        return None
    if v < 1000.0:
        return "dichte_mist"
    if v < 5000.0 and (
        air_temp_c is not None and dew_point_c is not None
        and (float(air_temp_c) - float(dew_point_c)) < 2.0
    ):
        return "haarmist_risico"
    if v < 10000.0:
        return "matig_zicht"
    return "goed"


def storm_surge_warning(surge_cm: Optional[float]) -> bool:
    """Surge ≥ 30cm = noemenswaardige opzet bovenop astronomisch tij."""
    if surge_cm is None:
        return False
    try:
        return abs(float(surge_cm)) >= 30.0
    except (TypeError, ValueError):
        return False
