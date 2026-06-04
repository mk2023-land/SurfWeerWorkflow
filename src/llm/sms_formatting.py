"""
Pure formatting helpers — kompasrichtingen, wind-categorieën, peak-block
detectie, maan-fase berekening. Geen externe afhankelijkheden buiten
src.config / src.util / stdlib.

Gesplitst uit generator.py zodat de scoring-laag en notify-laag deze
helpers kunnen importeren zonder de hele LLM-module mee te trekken.
"""
from datetime import datetime, timezone
from typing import Optional

from src.config import NOORDWIJK
from src.util import to_utc

_COMPASS_16 = ['N', 'NNO', 'NO', 'ONO', 'O', 'OZO', 'ZO', 'ZZO',
               'Z', 'ZZW', 'ZW', 'WZW', 'W', 'WNW', 'NW', 'NNW']

_DAY_NL_SHORT = ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo']


def degrees_to_compass(deg: float) -> str:
    """Vertaal hoek (graden) naar 16-punts kompasrichting (NL).

    Geeft '?' bij een ontbrekende richting (None) i.p.v. te crashen — de
    fallback-template is de laatste vangnet-laag en mag niet sneuvelen op een
    golfpiek zonder richtingsveld.
    """
    if deg is None:
        return "?"
    idx = int(((deg % 360) + 11.25) / 22.5) % 16
    return _COMPASS_16[idx]


def wind_label_for_noordwijk(wind_dir_deg: int) -> str:
    """Wind-categorie voor Noordwijk: aflandig / zijaflandig / aanlandig / zij-aanlandig."""
    from src.config import WIND_DIRECTIONS
    d = wind_dir_deg % 360
    if WIND_DIRECTIONS['offshore'][0] <= d <= WIND_DIRECTIONS['offshore'][1]:
        return 'aflandig'
    if WIND_DIRECTIONS['side_offshore'][0] <= d <= WIND_DIRECTIONS['side_offshore'][1]:
        return 'zijaflandig'
    if WIND_DIRECTIONS['onshore'][0] <= d <= WIND_DIRECTIONS['onshore'][1]:
        return 'aanlandig'
    return 'zij-aanlandig'


def is_blocked_by_ijmuiden_pier(swell_dir_deg: int) -> bool:
    """True als swell-richting binnen de NNO-sector valt die door IJmuiden-pier wordt afgeschermd."""
    blocked_min = NOORDWIJK.blocked_swell_dir_min
    blocked_max = NOORDWIJK.blocked_swell_dir_max
    if blocked_min == 0 and blocked_max == 0:
        return False
    d = swell_dir_deg % 360
    if blocked_min <= blocked_max:
        return blocked_min <= d <= blocked_max
    return d >= blocked_min or d <= blocked_max


def _hours_to(when: datetime, target: Optional[datetime]) -> Optional[float]:
    """
    Uren tussen `when` en `target` (positief als target in toekomst, anders None).
    Naive timestamps worden als Europe/Amsterdam local geïnterpreteerd (consistent
    met Open-Meteo input), aware timestamps converteren naar UTC.
    """
    if target is None:
        return None
    delta = (to_utc(target) - to_utc(when)).total_seconds() / 3600.0
    return round(delta, 1) if delta >= 0 else None


def peak_block(window) -> dict:
    """
    Vind de aaneengesloten uren binnen `window` waar de totaal-score binnen 10
    punten van de piek zit. Levert een mini-venster ("14:00-16:00") binnen het
    hoofdvenster ("14:00-19:00") zodat de LLM kan schrijven "14-19 surfbaar,
    piek 14-16u".

    Returns: {"start_time", "end_time", "duration_hours"} of {} als window leeg.
    """
    scores = window.hourly_scores
    if not scores:
        return {}

    peak_total = max(s.total_score for s in scores)
    threshold = peak_total - 10.0

    peak_idx = max(range(len(scores)), key=lambda i: scores[i].total_score)

    left = peak_idx
    while left > 0 and scores[left - 1].total_score >= threshold:
        left -= 1
    right = peak_idx
    while right < len(scores) - 1 and scores[right + 1].total_score >= threshold:
        right += 1

    return {
        "start_time": scores[left].timestamp.strftime("%H:%M"),
        "end_time": scores[right].timestamp.strftime("%H:%M"),
        "duration_hours": right - left + 1,
    }


def _tide_window_quality(tide_norm: float, dominant_period_s: float) -> str:
    """
    Label tij-venster kwaliteit op basis van niveau + dominante periode. Gebruikt
    dezelfde venster-grenzen als score_tide_component zodat tekst en score op
    elkaar aansluiten.

    - "good": binnen optimaal venster (groundswell ruim, wind-sea smal)
    - "fair": net buiten venster — surfen kan maar niet ideaal
    - "poor": ver buiten venster (extreem hoog/laag)
    """
    if dominant_period_s >= 9:
        lo, hi = 0.20, 0.90
    elif dominant_period_s >= 7:
        lo, hi = 0.35, 0.85
    else:
        lo, hi = 0.50, 0.90

    if lo <= tide_norm <= hi:
        return "good"
    # 'Fair' = tot ~30% buiten venster aan dezelfde kant.
    fair_margin = 0.15
    if (lo - fair_margin) <= tide_norm <= (hi + fair_margin):
        return "fair"
    return "poor"


def moon_phase_info(when: datetime) -> tuple[float, str, bool]:
    """
    Simpele maan-fase berekening (synodische maand 29.53 dagen, referentie nieuwe maan
    2000-01-06 18:14 UTC). Goed genoeg voor "springtij of niet".

    Returns:
        (phase_age_days, label_nl, is_spring_tide).
        is_spring_tide = binnen 2 dagen van nieuwe of volle maan.
    """
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    when_utc = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)
    days = (when_utc - ref).total_seconds() / 86400.0
    age = days % 29.530588
    # Labels per ~3.7-dagen kwart.
    if age < 1.85 or age >= 27.68:
        label = 'nieuwe maan'
    elif age < 5.54:
        label = 'wassende sikkel'
    elif age < 9.23:
        label = 'eerste kwartier'
    elif age < 12.92:
        label = 'wassende maan'
    elif age < 16.61:
        label = 'volle maan'
    elif age < 20.30:
        label = 'afnemende maan'
    elif age < 23.99:
        label = 'laatste kwartier'
    else:
        label = 'afnemende sikkel'
    # Springtij-venster: <2 dagen rond nieuwe maan (0/29.53) of volle maan (14.77).
    distance_new = min(age, 29.530588 - age)
    distance_full = abs(age - 14.765)
    is_spring = distance_new < 2.0 or distance_full < 2.0
    return age, label, is_spring
