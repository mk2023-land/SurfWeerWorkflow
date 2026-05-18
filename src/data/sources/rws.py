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
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from zoneinfo import ZoneInfo
import httpx
from src.data.models import WaveSpectrum, SpectralPeak, SwellType, TideState

# Open-Meteo retourneert naive timestamps in Europe/Amsterdam,
# RWS retourneert tz-aware timestamps. We normaliseren beide naar UTC voor vergelijking.
_AMSTERDAM = ZoneInfo('Europe/Amsterdam')


def _to_utc(dt: datetime) -> datetime:
    """Naive → Europe/Amsterdam → UTC. Aware → UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_AMSTERDAM)
    return dt.astimezone(timezone.utc)

from src.config import (
    API_ENDPOINTS,
    RWS_STATIONS,
    TIMEZONE,
)

logger = logging.getLogger(__name__)

# Aquo-codes voor de quantities die we opvragen.
GROOTHEID_HM0 = 'Hm0'      # Significante golfhoogte in spectrale domein (cm)
GROOTHEID_TM02 = 'Tm02'    # Gemiddelde golfperiode uit m0/m2 (s)
GROOTHEID_TH0 = 'Th0'      # Gemiddelde golfrichting (graden)
GROOTHEID_WATHTE = 'WATHTE'  # Waterhoogte (cm t.o.v. NAP)


def _parse_rws_timestamp(s: str) -> datetime:
    """Parse RWS-tijdstring '2026-05-18T20:30:00.000+01:00' naar datetime."""
    return datetime.fromisoformat(s)


class RWSClient:
    """Async client voor RWS DDAPI20 WaterWebservices."""

    def __init__(self):
        self.timeout = 30.0
        self.max_retries = 3
        self.latest_url = API_ENDPOINTS['rws_latest']
        self.period_url = API_ENDPOINTS['rws_period']

    async def _post(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """POST met retry en exponential backoff."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            last_err: Optional[Exception] = None
            for attempt in range(self.max_retries):
                try:
                    response = await client.post(url, json=body)
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPError as e:
                    last_err = e
                    logger.warning(f"RWS request failed (attempt {attempt + 1}/{self.max_retries}): {e}")
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

    async def fetch_buoy_data(
        self,
        station_code: str,
        hours_back: int = 24,
    ) -> List[Dict[str, Any]]:
        """
        Haal recente boei-data op: significante golfhoogte (m), periode (s), richting (°).

        Bevraagt Hm0, Tm02 en Th0 parallel en mergt op tijdstip. Th0 wordt
        op 0 gezet als de boei geen richting publiceert (gebruikelijk bij
        offshore-platforms zonder directionele sensor).
        """
        if station_code not in RWS_STATIONS:
            raise ValueError(f"Onbekend station: {station_code}")

        station = RWS_STATIONS[station_code]
        rws_code = station['rws_code']
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours_back)

        logger.info(f"Fetching buoy data for {station['name']} ({rws_code}), {hours_back}h history")

        try:
            hm0, tm02, th0 = await asyncio.gather(
                self._fetch_series(rws_code, GROOTHEID_HM0, start, end),
                self._fetch_series(rws_code, GROOTHEID_TM02, start, end),
                self._fetch_series(rws_code, GROOTHEID_TH0, start, end),
                return_exceptions=True,
            )
        except Exception as e:
            logger.error(f"Failed to fetch buoy data for {station_code}: {e}")
            return []

        # Onderdruk individuele fouten (bv. boei zonder Th0).
        hm0 = hm0 if isinstance(hm0, list) else []
        tm02 = tm02 if isinstance(tm02, list) else []
        th0 = th0 if isinstance(th0, list) else []

        # Th0 indexeren op exact tijdstip, dan binden aan Hm0/Tm02 (10-min raster).
        tm02_by_ts = {row['timestamp']: row['value'] for row in tm02}
        th0_by_ts = {row['timestamp']: row['value'] for row in th0}

        merged: List[Dict[str, Any]] = []
        for row in hm0:
            ts = row['timestamp']
            period_s = tm02_by_ts.get(ts)
            if period_s is None or period_s <= 0:
                continue  # zonder periode is een spectrale piek zinloos
            direction = th0_by_ts.get(ts, 0.0)
            merged.append({
                'timestamp': ts,
                'station': station_code,
                'height_m': row['value'] / 100.0,  # cm → m
                'period_s': period_s,
                'direction_deg': direction,
            })

        logger.info(f"Retrieved {len(merged)} merged observations for {station_code}")
        return merged

    async def fetch_tide_predictions(
        self,
        location_code: str = 'scheveningen',
        days_ahead: int = 7,
    ) -> Dict[str, Any]:
        """
        Haal astronomische tij-voorspellingen op voor `days_ahead` dagen vooruit.

        Returns:
            {
              'tide_events':  [{timestamp, level_m, phase}, ...]  (10-min raster),
              'high_tides':   [...],
              'low_tides':    [...],
              'location':     code,
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
            return {'tide_events': [], 'high_tides': [], 'low_tides': [], 'location': location_code}

        events = [
            {
                'timestamp': row['timestamp'],
                'level_m': row['value'] / 100.0,  # cm → m
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
        return {
            'tide_events': events,
            'high_tides': high_tides,
            'low_tides': low_tides,
            'location': location_code,
        }

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

    Pakt het dichtstbijzijnde event (10-min raster) en de eerstvolgende hoog/laag.
    Valt terug op een veilige placeholder als er geen data is.
    """
    events = tide_data.get('tide_events') or []
    if not events:
        return TideState(
            level_m=0.0,
            phase='onbekend',
            next_low=when + timedelta(hours=6),
            next_high=when + timedelta(hours=12),
        )

    when_utc = _to_utc(when)
    nearest = min(events, key=lambda e: abs((_to_utc(e['timestamp']) - when_utc).total_seconds()))

    next_high = next(
        (h['timestamp'] for h in tide_data.get('high_tides', []) if _to_utc(h['timestamp']) >= when_utc),
        when + timedelta(hours=12),
    )
    next_low = next(
        (l['timestamp'] for l in tide_data.get('low_tides', []) if _to_utc(l['timestamp']) >= when_utc),
        when + timedelta(hours=6),
    )

    return TideState(
        level_m=nearest['level_m'],
        phase=nearest['phase'],
        next_low=next_low,
        next_high=next_high,
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
    """Haal alle RWS-data op (primaire boei + early warning + tij)."""
    client = RWSClient()
    primary_buoy, early_warning, tide = await asyncio.gather(
        fetch_primary_buoy_data(),
        fetch_early_warning_buoys(),
        client.fetch_tide_predictions('scheveningen', days_ahead=7),
    )
    return {
        'primary_buoy': primary_buoy,
        'early_warning_buoys': early_warning,
        'tide': tide,
    }
