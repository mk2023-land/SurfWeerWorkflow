"""
Rijkswaterstaat WaterWebservices integratie (DDAPI20).

Gebruikt de nieuwe endpoints op `ddapi20-waterwebservices.rijkswaterstaat.nl`.
De oude `waterwebservices.rijkswaterstaat.nl/*_DBO/*` URLs zijn uitgefaseerd
per april 2026 en retourneren 301-redirects.

Response-shape sinds DDAPI20:
    {
      "Succesvol": true,
      "WaarnemingenLijst": [
        {
          "AquoMetadata": {"Grootheid": {"Code": "Hm0"}, "Eenheid": {"Code": "cm"}, ...},
          "MetingenLijst": [
            {"Tijdstip": "2026-05-18T20:30:00.000+01:00",
             "Meetwaarde": {"Waarde_Numeriek": 45.0}}
          ]
        }
      ]
    }
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from zoneinfo import ZoneInfo
import httpx
from src.data.models import WaveSpectrum, SpectralPeak, SwellType, TideState

from src.util import to_utc as _to_utc

from src.config import (
    API_ENDPOINTS,
    RWS_STATIONS,
    TIMEZONE,
    RWS_CONCURRENCY_LIMIT,
    RWS_EMPTY_BODY_RETRIES,
    RWS_EMPTY_BODY_RETRY_DELAY_S,
    RWS_HTTP_TIMEOUT_S,
    RWS_MAX_KEEPALIVE_CONNECTIONS,
    RWS_MAX_CONNECTIONS,
    RWS_USER_AGENT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level shared resources voor connection-pooling + throttling.
#
# DDAPI20 retourneert lege bodies (parse-error "Expecting value: line 1
# column 1 (char 0)") onder load. Twee mitigaties:
#   1. Semaphore beperkt aantal in-flight requests tot N (default 3).
#   2. Shared AsyncClient hergebruikt TCP-connecties (keep-alive).
# ---------------------------------------------------------------------------
_rws_semaphore = asyncio.Semaphore(RWS_CONCURRENCY_LIMIT)
_rws_client: Optional[httpx.AsyncClient] = None
_rws_client_lock = asyncio.Lock()


async def _get_shared_client() -> httpx.AsyncClient:
    """Lazy-init een shared httpx.AsyncClient voor RWS calls."""
    global _rws_client
    if _rws_client is None or _rws_client.is_closed:
        async with _rws_client_lock:
            if _rws_client is None or _rws_client.is_closed:
                _rws_client = httpx.AsyncClient(
                    timeout=RWS_HTTP_TIMEOUT_S,
                    limits=httpx.Limits(
                        max_keepalive_connections=RWS_MAX_KEEPALIVE_CONNECTIONS,
                        max_connections=RWS_MAX_CONNECTIONS,
                    ),
                    headers={'User-Agent': RWS_USER_AGENT},
                )
    return _rws_client


# Sanity-check bovengrenzen voor RWS-boei waarden. Buiten deze ranges
# corrupteert een sensor-glitch / unit-bug downstream scoring.
_RWS_HM0_MAX_M = 15.0
_RWS_PERIOD_MAX_S = 30.0
_RWS_HMAX_MAX_M = 30.0  # Hmax kan ~1.8× Hm0
_RWS_PRESSURE_MIN_HPA = 900.0
_RWS_PRESSURE_MAX_HPA = 1080.0
_RWS_AIR_TEMP_MIN_C = -30.0
_RWS_AIR_TEMP_MAX_C = 50.0

# Aquo-codes voor de quantities die we opvragen.
GROOTHEID_HM0 = 'Hm0'      # Significante golfhoogte in spectrale domein (cm)
GROOTHEID_TM02 = 'Tm02'    # Gemiddelde golfperiode uit m0/m2 (s)
GROOTHEID_TH0 = 'Th0'      # Gemiddelde golfrichting (graden)
GROOTHEID_WATHTE = 'WATHTE'  # Waterhoogte (cm t.o.v. NAP)
# Uitgebreide boei-grootheden (RWS DDAPI20).
GROOTHEID_TP = 'Tp'        # Peak-periode (s) — surfers/pro-forecasters
GROOTHEID_TP_FALLBACK = 'Tp001'  # Sommige DDAPI20-versies hanteren Tp001 i.p.v. Tp
GROOTHEID_SOBH = 'SObh'    # Directional spread / golfrichtingspreiding (°)
GROOTHEID_HMAX = 'Hmax'    # Max individuele golf in meet-interval (cm)
GROOTHEID_LUCHTDK = 'LUCHTDK'   # Luchtdruk gemeten bij boei (hPa)
GROOTHEID_LUCHTTPR = 'LUCHTTPR' # Luchttemperatuur bij boei (°C)
# Tide-uitbreiding voor storm surge residual.
# Bij sommige RWS-publicaties is het astronomisch tij gepubliceerd onder
# WATHTBRKD ("berekend"); de actuele/gemeten waterhoogte als WATHTE met
# ProcesType=metingen. We proberen beide te halen en berekenen
# `surge_cm = measured - astronomical` als beide beschikbaar zijn.
GROOTHEID_WATHTBRKD = 'WATHTBRKD'


def _parse_rws_timestamp(s: str) -> datetime:
    """Parse RWS-tijdstring '2026-05-18T20:30:00.000+01:00' naar datetime."""
    return datetime.fromisoformat(s)


def _height_factor_to_m(rows: List[Dict[str, Any]], grootheid: str) -> float:
    """
    Bepaal conversie-factor om RWS-hoogte naar meter te zetten op basis
    van `unit` field in de response. RWS publiceert Hm0/Hmax meestal in
    cm; bij toekomstige unit-switch (naar 'm') voorkomt deze functie een
    100× foutieve waarde.

    Returns:
        0.01 voor 'cm', 1.0 voor 'm', 0.01 als default met WARNING bij
        onbekende units (current behavior preserveren).
    """
    if not rows:
        return 0.01  # default cm-aanname
    unit = rows[0].get('unit')
    if unit == 'cm':
        return 0.01
    if unit == 'm':
        return 1.0
    if unit is None:
        return 0.01
    logger.warning(
        f"RWS grootheid {grootheid}: onbekende unit '{unit}', "
        f"assume cm (factor 0.01)"
    )
    return 0.01


def _sane_hm0_m(height_m: float, station_code: str) -> bool:
    """True als Hm0 binnen plausibele range; anders WARNING + False (drop)."""
    if height_m < 0:
        logger.warning(
            f"RWS Hm0 negatief ({height_m:.2f}m) @{station_code}; drop"
        )
        return False
    if height_m > _RWS_HM0_MAX_M:
        logger.warning(
            f"RWS Hm0 {height_m:.2f}m > {_RWS_HM0_MAX_M}m @{station_code}; drop"
        )
        return False
    return True


class RWSClient:
    """Async client voor RWS DDAPI20 WaterWebservices."""

    def __init__(self):
        self.timeout = RWS_HTTP_TIMEOUT_S
        self.max_retries = 3
        self.latest_url = API_ENDPOINTS['rws_latest']
        self.period_url = API_ENDPOINTS['rws_period']

    async def _post(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        POST met retry en exponential backoff. Gebruikt een shared
        httpx.AsyncClient (keep-alive, User-Agent) i.p.v. per-call
        open/close — DDAPI20 hapert minder bij connection-reuse.

        Empty-body responses worden onderscheiden van HTTP-errors: json.loads
        op een lege body raised json.JSONDecodeError, dat bubbelt naar
        `_fetch_series_safe` waar 2x retry plaatsvindt.
        """
        client = await _get_shared_client()
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                response = await client.post(url, json=body)
                response.raise_for_status()
                # response.json() raised json.JSONDecodeError bij empty body —
                # dat is exact de DDAPI20-load-symptoom die we willen vangen.
                return response.json()
            except httpx.HTTPError as e:
                last_err = e
                logger.warning(
                    f"RWS request failed (attempt {attempt + 1}/{self.max_retries}): {e}"
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        raise last_err or RuntimeError("RWS request failed")

    async def _fetch_series(
        self,
        location_code: str,
        grootheid: str,
        start: datetime,
        end: datetime,
        proces_type: Optional[str] = None,
        hoedanigheid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Haal één meetreeks op (één grootheid, één locatie, één periode).

        Returns:
            Lijst van {timestamp, value, unit} dicts, gesorteerd op timestamp.
        """
        aquo: Dict[str, Any] = {
            'Compartiment': {'Code': 'OW'},
            'Grootheid': {'Code': grootheid},
        }
        if hoedanigheid:
            aquo['Hoedanigheid'] = {'Code': hoedanigheid}
        if proces_type:
            aquo['ProcesType'] = proces_type

        body = {
            'Locatie': {'Code': location_code},
            'AquoPlusWaarnemingMetadata': {'AquoMetadata': aquo},
            'Periode': {
                'Begindatumtijd': start.isoformat(timespec='milliseconds'),
                'Einddatumtijd': end.isoformat(timespec='milliseconds'),
            },
        }

        data = await self._post(self.period_url, body)
        wl = data.get('WaarnemingenLijst') or []
        if not wl:
            return []

        # OphalenWaarnemingen retourneert één entry per AquoMetadata.
        waarneming = wl[0]
        unit = (waarneming.get('AquoMetadata', {}).get('Eenheid') or {}).get('Code')

        out = []
        for m in waarneming.get('MetingenLijst', []) or []:
            tijd = m.get('Tijdstip')
            waarde = (m.get('Meetwaarde') or {}).get('Waarde_Numeriek')
            if tijd is None or waarde is None:
                continue
            out.append({
                'timestamp': _parse_rws_timestamp(tijd),
                'value': float(waarde),
                'unit': unit,
            })
        out.sort(key=lambda x: x['timestamp'])
        return out

    async def _fetch_series_safe(
        self,
        location_code: str,
        grootheid: str,
        start: datetime,
        end: datetime,
        proces_type: Optional[str] = None,
        hoedanigheid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Wrapper rond `_fetch_series` die fouten en lege responses opvangt
        en gestructureerde per-grootheid logging emit (B5-fix-stijl).

        Concurrency-throttle: alle calls gaan via `_rws_semaphore` zodat
        nooit meer dan RWS_CONCURRENCY_LIMIT in-flight zijn — DDAPI20
        retourneert lege bodies bij teveel parallelle calls.

        Empty-body retry: bij json.JSONDecodeError / ValueError (DDAPI20
        retourneerde een lege body) proberen we tot RWS_EMPTY_BODY_RETRIES
        keer opnieuw met een korte delay. Vaak werkt de 2e/3e poging wel.

        Retourneert altijd een lijst — leeg bij error, zodat een individuele
        503/404 niet de hele boei kapotmaakt in `asyncio.gather(..., return_exceptions=True)`.
        """
        max_attempts = max(1, RWS_EMPTY_BODY_RETRIES + 1)
        async with _rws_semaphore:
            logger.debug(
                f"RWS semaphore acquired voor {grootheid}@{location_code}"
            )
            last_err: Optional[Exception] = None
            for attempt in range(max_attempts):
                try:
                    rows = await self._fetch_series(
                        location_code, grootheid, start, end,
                        proces_type=proces_type, hoedanigheid=hoedanigheid,
                    )
                    break
                except (json.JSONDecodeError, ValueError) as e:
                    last_err = e
                    if attempt < max_attempts - 1:
                        logger.info(
                            f"RWS empty-body retry {attempt + 1}/{max_attempts - 1} "
                            f"voor grootheid {grootheid}@{location_code}: {e}"
                        )
                        await asyncio.sleep(RWS_EMPTY_BODY_RETRY_DELAY_S)
                        continue
                    logger.warning(
                        f"RWS grootheid {grootheid}@{location_code} empty-body "
                        f"na {max_attempts} pogingen: {e}"
                    )
                    return []
                except Exception as e:
                    logger.warning(
                        f"RWS grootheid {grootheid}@{location_code} mislukt: {e}"
                    )
                    return []
            else:
                # Loop exhausted without break — alle retries faalden.
                logger.warning(
                    f"RWS grootheid {grootheid}@{location_code} faalde na retries: {last_err}"
                )
                return []
        # Buiten semaphore: logging + return.
        if not rows:
            logger.warning(
                f"RWS grootheid {grootheid}@{location_code} leverde 0 punten"
            )
            return rows
        logger.info(
            f"RWS grootheid {grootheid}@{location_code}: {len(rows)} punten"
        )
        return rows

    async def fetch_buoy_data(
        self,
        station_code: str,
        hours_back: int = 24,
        include_extras: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Haal recente boei-data op: significante golfhoogte (m), periode (s), richting (°).

        Bevraagt Hm0, Tm02 en Th0 parallel en mergt op tijdstip. Th0 wordt
        op 0 gezet als de boei geen richting publiceert (gebruikelijk bij
        offshore-platforms zonder directionele sensor).

        Wanneer `include_extras=True` (default) worden ook de uitgebreide
        DDAPI20-grootheden opgehaald en als extra dict-keys toegevoegd aan
        elk gemerged punt:
          - `tp_s`     : peak-periode (Tp of Tp001-fallback)
          - `sobh_deg` : directional spread
          - `hmax_m`   : maximale individuele golf in interval
          - `pressure_hpa`, `air_temp_c` : alleen bij IJG1 (LUCHTDK/LUCHTTPR)

        Per-grootheid failures (503, 404, lege response) zijn gracieus — de
        bijbehorende key ontbreekt of staat op None in het gemergede punt.
        """
        if station_code not in RWS_STATIONS:
            raise ValueError(f"Onbekend station: {station_code}")

        station = RWS_STATIONS[station_code]
        rws_code = station['rws_code']
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours_back)

        logger.info(f"Fetching buoy data for {station['name']} ({rws_code}), {hours_back}h history")

        # Basis-grootheden — backwards compatible.
        base_tasks = [
            self._fetch_series_safe(rws_code, GROOTHEID_HM0, start, end),
            self._fetch_series_safe(rws_code, GROOTHEID_TM02, start, end),
            self._fetch_series_safe(rws_code, GROOTHEID_TH0, start, end),
        ]

        # Uitgebreide grootheden — alleen wanneer expliciet gevraagd.
        # LUCHTDK/LUCHTTPR alleen voor IJG1 (primary, lokaal).
        extra_codes: List[str] = []
        if include_extras:
            extra_codes.extend([GROOTHEID_TP, GROOTHEID_SOBH, GROOTHEID_HMAX])
            if station_code == 'IJG1':
                extra_codes.extend([GROOTHEID_LUCHTDK, GROOTHEID_LUCHTTPR])
        extra_tasks = [
            self._fetch_series_safe(rws_code, code, start, end)
            for code in extra_codes
        ]

        results = await asyncio.gather(
            *base_tasks, *extra_tasks, return_exceptions=True
        )

        # `_fetch_series_safe` zou nooit moeten raisen, maar gather kan
        # alsnog een Exception terugkrijgen — degradeer naar [].
        def _list(x):
            return x if isinstance(x, list) else []

        hm0 = _list(results[0])
        tm02 = _list(results[1])
        th0 = _list(results[2])
        extras_lists = [_list(r) for r in results[3:]]
        extras_by_code: Dict[str, List[Dict[str, Any]]] = dict(
            zip(extra_codes, extras_lists)
        )

        # Tp/Tp001 fallback: als Tp niets oplevert, probeer Tp001 een keer.
        if include_extras and not extras_by_code.get(GROOTHEID_TP):
            logger.info(
                f"Tp leeg voor {station_code}, fallback naar {GROOTHEID_TP_FALLBACK}"
            )
            tp_fb = await self._fetch_series_safe(
                rws_code, GROOTHEID_TP_FALLBACK, start, end
            )
            if tp_fb:
                extras_by_code[GROOTHEID_TP] = tp_fb

        # Indexeren op tijdstip voor merge.
        tm02_by_ts = {row['timestamp']: row['value'] for row in tm02}
        th0_by_ts = {row['timestamp']: row['value'] for row in th0}
        tp_by_ts = {r['timestamp']: r['value']
                    for r in extras_by_code.get(GROOTHEID_TP, [])}
        sobh_by_ts = {r['timestamp']: r['value']
                      for r in extras_by_code.get(GROOTHEID_SOBH, [])}
        hmax_by_ts = {r['timestamp']: r['value']
                      for r in extras_by_code.get(GROOTHEID_HMAX, [])}
        pressure_by_ts = {r['timestamp']: r['value']
                          for r in extras_by_code.get(GROOTHEID_LUCHTDK, [])}
        airtemp_by_ts = {r['timestamp']: r['value']
                         for r in extras_by_code.get(GROOTHEID_LUCHTTPR, [])}

        # Unit-aware height conversie: RWS publiceert Hm0/Hmax meestal in cm
        # maar mogelijk in m. Lees `unit` uit de eerste record en kies de factor.
        hm0_factor = _height_factor_to_m(hm0, GROOTHEID_HM0)
        hmax_factor = _height_factor_to_m(
            extras_by_code.get(GROOTHEID_HMAX, []), GROOTHEID_HMAX
        )

        # Sanity-bepaling Hm0: bouw eerst een filtered list zodat een
        # negatieve / absurd hoge waarde de hele timestamp dropt (Tm02/Th0
        # zouden anders aan een glitch-Hm0 hangen).
        merged: List[Dict[str, Any]] = []
        for row in hm0:
            ts = row['timestamp']
            height_m = row['value'] * hm0_factor
            if not _sane_hm0_m(height_m, station_code):
                continue
            period_s = tm02_by_ts.get(ts)
            if period_s is None or period_s <= 0:
                continue  # zonder periode is een spectrale piek zinloos
            if period_s > _RWS_PERIOD_MAX_S:
                logger.warning(
                    f"RWS Tm02 {period_s}s buiten range @{station_code}; sla over"
                )
                continue
            direction = th0_by_ts.get(ts, 0.0)
            point: Dict[str, Any] = {
                'timestamp': ts,
                'station': station_code,
                'height_m': height_m,
                'period_s': period_s,
                'direction_deg': direction,
            }
            # Uitgebreide velden — alleen toevoegen als er data is, zodat
            # downstream-consumers `dict.get(..., default)` kunnen gebruiken.
            tp_val = tp_by_ts.get(ts)
            if tp_val is not None and tp_val > 0:
                if tp_val <= _RWS_PERIOD_MAX_S:
                    point['tp_s'] = float(tp_val)
                else:
                    logger.warning(
                        f"RWS Tp {tp_val}s buiten range @{station_code}; drop"
                    )
            sobh_val = sobh_by_ts.get(ts)
            if sobh_val is not None and 0 <= sobh_val <= 180:
                point['sobh_deg'] = float(sobh_val)
            hmax_val = hmax_by_ts.get(ts)
            if hmax_val is not None:
                hmax_m = float(hmax_val) * hmax_factor
                if 0 <= hmax_m <= _RWS_HMAX_MAX_M:
                    point['hmax_m'] = hmax_m
                else:
                    logger.warning(
                        f"RWS Hmax {hmax_m:.2f}m buiten range @{station_code}; drop"
                    )
            pressure_val = pressure_by_ts.get(ts)
            if pressure_val is not None:
                if _RWS_PRESSURE_MIN_HPA <= pressure_val <= _RWS_PRESSURE_MAX_HPA:
                    point['pressure_hpa'] = float(pressure_val)
                else:
                    logger.warning(
                        f"RWS LUCHTDK {pressure_val}hPa buiten range @{station_code}; drop"
                    )
            air_temp_val = airtemp_by_ts.get(ts)
            if air_temp_val is not None:
                if _RWS_AIR_TEMP_MIN_C <= air_temp_val <= _RWS_AIR_TEMP_MAX_C:
                    point['air_temp_c'] = float(air_temp_val)
                else:
                    logger.warning(
                        f"RWS LUCHTTPR {air_temp_val}°C buiten range @{station_code}; drop"
                    )
            merged.append(point)

        logger.info(f"Retrieved {len(merged)} merged observations for {station_code}")
        return merged

    async def fetch_tide_predictions(
        self,
        location_code: str = 'scheveningen',
        days_ahead: int = 7,
    ) -> Dict[str, Any]:
        """
        Haal astronomische tij-voorspellingen op voor `days_ahead` dagen vooruit.

        Daarnaast worden — best-effort — ook WATHTBRKD (berekend tij) én
        WATHTE/ProcesType=metingen (gemeten waterhoogte) opgehaald om de
        storm-surge residual te berekenen: `surge_cm = measured - astronomical`.

        Returns:
            {
              'tide_events':  [{timestamp, level_m, phase}, ...]  (10-min raster),
              'high_tides':   [...],
              'low_tides':    [...],
              'location':     code,
              'surge_residual_cm': [{timestamp, surge_cm}, ...],  # leeg als data ontbreekt
              'latest_surge_cm':   float | None,                   # meest recente residual
            }
        """
        start = datetime.now(timezone.utc) - timedelta(hours=2)  # iets vroeger ivm. interpolatie
        end = start + timedelta(days=days_ahead)

        logger.info(f"Fetching astronomical tide predictions for {location_code} ({days_ahead}d)")

        try:
            series = await self._fetch_series(
                location_code, GROOTHEID_WATHTE, start, end,
                proces_type='astronomisch', hoedanigheid='NAP',
            )
        except Exception as e:
            logger.error(f"Failed to fetch tide predictions for {location_code}: {e}")
            return {
                'tide_events': [], 'high_tides': [], 'low_tides': [],
                'location': location_code,
                'surge_residual_cm': [], 'latest_surge_cm': None,
            }

        # Unit-aware: RWS WATHTE in cm of m, lees `unit` uit eerste record.
        tide_factor = _height_factor_to_m(series, GROOTHEID_WATHTE)
        events = [
            {
                'timestamp': row['timestamp'],
                'level_m': row['value'] * tide_factor,
                'phase': 'onbekend',
            }
            for row in series
        ]

        # Fase op basis van trend t.o.v. vorige punt.
        for i in range(1, len(events)):
            if events[i]['level_m'] > events[i - 1]['level_m']:
                events[i]['phase'] = 'opgaand'
            elif events[i]['level_m'] < events[i - 1]['level_m']:
                events[i]['phase'] = 'afgaand'
            else:
                events[i]['phase'] = events[i - 1]['phase']
        if len(events) >= 2:
            events[0]['phase'] = events[1]['phase']

        # Hoogtij/laagtij = lokale max/min: vorige opgaand, volgende afgaand (en v.v.)
        high_tides, low_tides = [], []
        for i in range(1, len(events) - 1):
            prev_p, next_p = events[i - 1]['phase'], events[i + 1]['phase']
            if prev_p == 'opgaand' and next_p == 'afgaand':
                high_tides.append(events[i])
            elif prev_p == 'afgaand' and next_p == 'opgaand':
                low_tides.append(events[i])

        logger.info(
            f"Retrieved {len(events)} tide points, {len(high_tides)} high, {len(low_tides)} low"
        )

        # Storm-surge residual: surge = measured - astronomical.
        # Best-effort: WATHTBRKD (berekend tij) en gemeten WATHTE op een
        # korter historisch venster (we kennen geen toekomstige metingen).
        surge_window_start = datetime.now(timezone.utc) - timedelta(hours=12)
        surge_window_end = datetime.now(timezone.utc)
        surge_residual, latest_surge = await self._compute_surge_residual(
            location_code, surge_window_start, surge_window_end, events,
        )

        return {
            'tide_events': events,
            'high_tides': high_tides,
            'low_tides': low_tides,
            'location': location_code,
            'surge_residual_cm': surge_residual,
            'latest_surge_cm': latest_surge,
        }

    async def _compute_surge_residual(
        self,
        location_code: str,
        start: datetime,
        end: datetime,
        astronomical_events: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], Optional[float]]:
        """
        Bereken storm-surge residual = gemeten - astronomisch (in cm).

        Probeert eerst WATHTBRKD voor het astronomische been; bij missing
        valt terug op de al-opgehaalde `astronomical_events` (cm via *100).
        Voor het gemeten been gebruikt WATHTE/ProcesType=metingen.

        Per-grootheid failures zijn gracieus: bij missing data wordt een
        leeg lijstje en None-latest geretourneerd. Combineren we op exact
        tijdstip (10-min raster); een mismatch op één steekpunt slaan we
        gewoon over.
        """
        measured_task = self._fetch_series_safe(
            location_code, GROOTHEID_WATHTE, start, end,
            proces_type='metingen', hoedanigheid='NAP',
        )
        brkd_task = self._fetch_series_safe(
            location_code, GROOTHEID_WATHTBRKD, start, end,
            hoedanigheid='NAP',
        )
        measured, brkd = await asyncio.gather(
            measured_task, brkd_task, return_exceptions=True,
        )
        measured = measured if isinstance(measured, list) else []
        brkd = brkd if isinstance(brkd, list) else []

        if not measured:
            logger.warning(
                f"Surge residual @ {location_code}: geen gemeten WATHTE; residual leeg"
            )
            return [], None

        # Astronomisch-by-timestamp: WATHTBRKD heeft voorrang (aparte grootheid
        # voor BEREKEND tij). Fallback op de astronomical events die we al hebben.
        astro_by_ts: Dict[datetime, float] = {}
        if brkd:
            for r in brkd:
                astro_by_ts[r['timestamp']] = float(r['value'])  # cm
        else:
            for ev in astronomical_events:
                astro_by_ts[ev['timestamp']] = ev['level_m'] * 100.0  # m → cm

        residuals: List[Dict[str, Any]] = []
        for row in measured:
            ts = row['timestamp']
            astro_cm = astro_by_ts.get(ts)
            if astro_cm is None:
                continue
            surge_cm = float(row['value']) - astro_cm
            residuals.append({'timestamp': ts, 'surge_cm': surge_cm})

        residuals.sort(key=lambda x: x['timestamp'])
        latest = residuals[-1]['surge_cm'] if residuals else None
        if latest is not None:
            logger.info(
                f"Surge residual @ {location_code}: {len(residuals)} punten, latest={latest:.1f}cm"
            )
        else:
            logger.warning(
                f"Surge residual @ {location_code}: geen overlappende timestamps"
            )
        return residuals, latest

    def buoy_data_to_wave_spectrum(self, buoy_data: Dict[str, Any]) -> WaveSpectrum:
        """
        Converteer één RWS boei-meetpunt naar WaveSpectrum.

        RWS publiceert alleen integrale spectraal-momenten (Hm0, Tm02, Th0)
        en geen volledig spectrum, dus we modelleren als één piek.
        """
        timestamp = buoy_data['timestamp']
        height = buoy_data['height_m']
        period = buoy_data['period_s']
        direction = buoy_data['direction_deg']

        if period >= 9:
            swell_type = SwellType.GROUND_SWELL
        elif period >= 7:
            swell_type = SwellType.WIND_SWELL
        else:
            swell_type = SwellType.WIND_SEA

        peak = SpectralPeak(
            frequency_mhz=1000 / period if period > 0 else 0,
            period_s=period,
            height_m=height,
            direction_deg=int(direction),
            type=swell_type,
        )
        return WaveSpectrum(
            timestamp=timestamp,
            significant_height_total=height,
            mean_period=period,
            mean_direction=int(direction),
            peaks=[peak],
        )


def tide_state_at(tide_data: Dict[str, Any], when: datetime) -> TideState:
    """
    Bouw een `TideState` voor tijdstip `when` op basis van fetched tide data.

    Pakt het dichtstbijzijnde event (10-min raster), de eerstvolgende hoog/laag,
    en daarnaast `last_turn_time` (meest recente HW of LW) en `next_turn_time`
    (eerstvolgende HW of LW) — beide nodig voor de tidal-current modeling
    waarmee mid-cycle stroming-pieken worden onderscheiden van slack-water
    kentering-flanks.

    Daily range wordt afgeleid uit de HW/LW direct rondom `when` — gebruikt voor
    spring/doodtij modulatie in de scoring (springtij ≥ 2.0m, doodtij < 1.6m).
    Valt terug op een veilige placeholder als er geen data is.
    """
    events = tide_data.get('tide_events') or []
    if not events:
        return TideState(
            level_m=0.0,
            phase='onbekend',
            next_low=when + timedelta(hours=6),
            next_high=when + timedelta(hours=12),
            daily_range_m=None,
            last_turn_time=None,
            next_turn_time=None,
        )

    when_utc = _to_utc(when)
    nearest = min(events, key=lambda e: abs((_to_utc(e['timestamp']) - when_utc).total_seconds()))

    high_tides = tide_data.get('high_tides', [])
    low_tides = tide_data.get('low_tides', [])

    next_high = next(
        (h['timestamp'] for h in high_tides if _to_utc(h['timestamp']) >= when_utc),
        when + timedelta(hours=12),
    )
    next_low = next(
        (l['timestamp'] for l in low_tides if _to_utc(l['timestamp']) >= when_utc),
        when + timedelta(hours=6),
    )

    # Bepaal de meest recente kentering (HW of LW vóór `when`) en de
    # eerstvolgende (HW of LW na `when`) — beide nodig voor tidal-current
    # intensity berekening.
    all_turns = (
        [(_to_utc(h['timestamp']), h['timestamp']) for h in high_tides] +
        [(_to_utc(l['timestamp']), l['timestamp']) for l in low_tides]
    )
    past_turns = [t for t in all_turns if t[0] <= when_utc]
    future_turns = [t for t in all_turns if t[0] > when_utc]
    last_turn_time = max(past_turns, key=lambda x: x[0])[1] if past_turns else None
    next_turn_time = min(future_turns, key=lambda x: x[0])[1] if future_turns else None

    # Daily range = |dichtsbij HW level - dichtsbij LW level|. Pakt zo het
    # lokale semi-diurnale cycle, niet een willekeurige max-min over heel
    # de dataset (waar springtij-week alles overschaduwt).
    daily_range_m: Optional[float] = None
    if high_tides and low_tides:
        nearest_hw = min(high_tides,
                         key=lambda h: abs((_to_utc(h['timestamp']) - when_utc).total_seconds()))
        nearest_lw = min(low_tides,
                         key=lambda l: abs((_to_utc(l['timestamp']) - when_utc).total_seconds()))
        if nearest_hw.get('level_m') is not None and nearest_lw.get('level_m') is not None:
            daily_range_m = abs(nearest_hw['level_m'] - nearest_lw['level_m'])

    return TideState(
        level_m=nearest['level_m'],
        phase=nearest['phase'],
        next_low=next_low,
        next_high=next_high,
        daily_range_m=daily_range_m,
        last_turn_time=last_turn_time,
        next_turn_time=next_turn_time,
    )


async def fetch_primary_buoy_data() -> Dict[str, Any]:
    """Haal data op van primaire boei (IJG1, IJgeul)."""
    client = RWSClient()
    data = await client.fetch_buoy_data('IJG1', hours_back=24)
    spectra = [client.buoy_data_to_wave_spectrum(d) for d in data]
    return {
        'station': 'IJG1',
        'station_name': RWS_STATIONS['IJG1']['name'],
        'spectra': spectra,
        'raw_data': data,
    }


async def fetch_early_warning_buoys() -> Dict[str, Any]:
    """Haal data op van offshore early-warning boeien (A12, K13)."""
    client = RWSClient()
    a12_data, k13_data = await asyncio.gather(
        client.fetch_buoy_data('A12', hours_back=48),
        client.fetch_buoy_data('K13', hours_back=48),
    )
    return {
        'A12': {
            'station_name': RWS_STATIONS['A12']['name'],
            'spectra': [client.buoy_data_to_wave_spectrum(d) for d in a12_data],
            'raw_data': a12_data,
        },
        'K13': {
            'station_name': RWS_STATIONS['K13']['name'],
            'spectra': [client.buoy_data_to_wave_spectrum(d) for d in k13_data],
            'raw_data': k13_data,
        },
    }


async def fetch_all_rws_data() -> Dict[str, Any]:
    """
    Haal alle RWS-data op (primaire boei + early warning + tij).

    Tide-station komt uit NOORDWIJK.tide_station ('ijmuiden') — closer fit
    dan scheveningen voor de Noordwijk-cyclus. Als ijmuiden geen data
    levert, fallback naar scheveningen (impliciet in fetch_tide_predictions,
    die een lege placeholder returnt).

    Primary-buoy failover: als IJG1 leeg blijkt (boei offline, of alle
    grootheden faalden), gebruiken we A12 als fallback voor `primary_buoy`.
    Zonder een functionerende primaire boei kunnen T1/T4 alert-detectors
    niet draaien. De originele early-warning slot van A12 blijft staan
    in `early_warning_buoys` zodat downstream beide kunnen zien.

    Returns:
        Dict met keys:
          - 'primary_buoy': dict zoals fetch_primary_buoy_data, maar het
            kan IJG1 OF A12 zijn afhankelijk van fallback
          - 'primary_buoy_fallback': str | None — naam van het station dat
            als fallback is ingezet (alleen aanwezig als fallback was nodig)
          - 'early_warning_buoys': onveranderd
          - 'tide': onveranderd
    """
    from src.config import NOORDWIJK
    client = RWSClient()
    primary_station = NOORDWIJK.tide_station
    primary_buoy, early_warning, tide = await asyncio.gather(
        fetch_primary_buoy_data(),
        fetch_early_warning_buoys(),
        client.fetch_tide_predictions(primary_station, days_ahead=7),
    )
    # IJG1 → A12 failover. Als IJG1 raw_data leeg is, gebruik A12 als
    # primaire (A12 is een gerede vervanger qua coverage voor T1/T4).
    primary_buoy_fallback: Optional[str] = None
    if not (primary_buoy or {}).get('raw_data'):
        a12_block = (early_warning or {}).get('A12') or {}
        if a12_block.get('raw_data'):
            logger.warning(
                "IJG1 raw_data leeg; fallback naar A12 als primary_buoy"
            )
            primary_buoy_fallback = 'A12'
            primary_buoy = {
                'station': 'A12',
                'station_name': a12_block.get('station_name', 'A12'),
                'spectra': a12_block.get('spectra', []),
                'raw_data': a12_block.get('raw_data', []),
            }
        else:
            logger.warning(
                "IJG1 leeg én A12 leeg; geen primary_buoy beschikbaar"
            )

    # Fallback: als primaire station leeg blijft, probeer scheveningen
    if (not tide.get('tide_events')) and primary_station != 'scheveningen':
        logger.warning(
            f"Tide station '{primary_station}' leverde geen data; fallback naar scheveningen"
        )
        tide = await client.fetch_tide_predictions('scheveningen', days_ahead=7)
    result: Dict[str, Any] = {
        'primary_buoy': primary_buoy,
        'early_warning_buoys': early_warning,
        'tide': tide,
    }
    if primary_buoy_fallback is not None:
        result['primary_buoy_fallback'] = primary_buoy_fallback
    return result
