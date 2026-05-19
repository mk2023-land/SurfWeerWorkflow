"""
Daglicht-filter voor Noordwijk (lat 52.241°N, lon 4.428°E).

Eenvoudige astronomische formule (zonnedeclinatie + uurhoek), ±5 min nauwkeurig
— voldoende om night/day onderscheid te maken. Niet bedoeld voor astronomie.

Het surf-daglicht-venster loopt rond civil twilight (zon 6° onder horizon):
- Start: zonsopgang - 0.5u
- Einde: zonsondergang + 0.5u

Een ruimere buffer (zoals 1.5u 's ochtends) maakte de LLM kwetsbaar voor het
benoemen van pre-dawn uren als "piek" — in mei begint daglicht pas rond 05:47
lokaal, dus een 1.5u buffer liet 04:17 al door als surfbaar. Civil twilight
is het minimum waarbij je werkelijk de golven kunt zien.

Buiten dit venster geeft score_hour een 0-score, waardoor night-uren niet als
piek of in surf-windows verschijnen.
"""
import math
from datetime import date, datetime
from typing import Tuple

from src.util import to_utc


_LAT_DEG = 52.241
_LON_DEG = 4.428  # positief = oost
_LAT_RAD = math.radians(_LAT_DEG)


def _sunrise_sunset_utc_hours(d: date) -> Tuple[float, float]:
    """
    Bereken zonsopgang en -ondergang in UTC-uren (decimaal) voor `d`.
    Cooper's-equation voor declinatie + standaard uurhoek-formule.
    """
    n = d.timetuple().tm_yday  # day-of-year, 1..366

    # Solar declination (radians) — Cooper.
    decl = math.radians(23.45 * math.sin(math.radians(360.0 * (284 + n) / 365.0)))

    # Uurhoek bij horizon (zonsmiddelpunt op horizon, refractie verwaarloosd).
    cos_h = -math.tan(_LAT_RAD) * math.tan(decl)
    cos_h = max(-1.0, min(1.0, cos_h))  # clamp voor poolnacht/middernachtszon
    h_hours = math.degrees(math.acos(cos_h)) / 15.0

    # Equation of time in minuten (eenvoudige approximatie).
    b = math.radians(360.0 * (n - 81) / 365.0)
    eot_min = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)

    # Solar noon in UTC: 12 - lon/15 (longitude offset) - EoT.
    solar_noon_utc = 12.0 - _LON_DEG / 15.0 - eot_min / 60.0

    return solar_noon_utc - h_hours, solar_noon_utc + h_hours


def is_daylight_noordwijk(
    dt: datetime,
    morning_buffer_h: float = 0.5,
    evening_buffer_h: float = 0.5,
) -> bool:
    """
    True als `dt` binnen het surf-daglicht-venster van Noordwijk valt.

    Default (civil twilight, symmetrisch):
    - Start = zonsopgang - `morning_buffer_h` (default 0.5u ≈ civil twilight)
    - Einde = zonsondergang + `evening_buffer_h` (default 0.5u)

    Tz-naive datetimes worden als Europe/Amsterdam local geïnterpreteerd
    (matched aan Open-Meteo) en vervolgens naar UTC genormaliseerd.
    """
    dt_utc = to_utc(dt)
    sr_utc, ss_utc = _sunrise_sunset_utc_hours(dt_utc.date())
    hour_utc = dt_utc.hour + dt_utc.minute / 60.0

    return (sr_utc - morning_buffer_h) <= hour_utc <= (ss_utc + evening_buffer_h)
