"""
Gedeelde utility-functies.

`to_utc` is essentieel voor alle tijd-vergelijkingen in de pipeline. Open-Meteo
retourneert naive timestamps in Europe/Amsterdam (wij vragen TIMEZONE=...); RWS
retourneert tz-aware UTC. Beide moeten we naar UTC normaliseren voor o.a.
daylight-filtering en `hours_to_next_high/low` berekeningen.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_AMSTERDAM = ZoneInfo('Europe/Amsterdam')


def to_utc(dt: datetime) -> datetime:
    """
    Normaliseer een datetime naar tz-aware UTC.

    - Naive datetime → wordt als Europe/Amsterdam local time geïnterpreteerd
      (matched aan Open-Meteo's response-format) → naar UTC.
    - Aware datetime → converteer rechtstreeks naar UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_AMSTERDAM)
    return dt.astimezone(timezone.utc)
