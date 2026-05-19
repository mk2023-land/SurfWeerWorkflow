"""
Open-Meteo API integratie voor weergegevens.
Ondersteunt Marine, Forecast en Archive APIs met async en retry logica.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import httpx
from ..models import HourState, WaveSpectrum, WindState, TideState, SpectralPeak, SwellType

from src.config import (
    API_ENDPOINTS,
    NOORDWIJK,
    TIMEZONE,
)

logger = logging.getLogger(__name__)


class OpenMeteoClient:
    """Client voor Open-Meteo APIs."""

    def __init__(self):
        self.timeout = 30.0
        self.max_retries = 3
        self.base_url = API_ENDPOINTS['open_meteo_forecast']
        self.marine_url = API_ENDPOINTS['open_meteo_marine']
        self.archive_url = API_ENDPOINTS['open_meteo_archive']

    async def _request_with_retry(
        self,
        url: str,
        params: Dict[str, Any],
        method: str = "GET"
    ) -> Dict[str, Any]:
        """HTTP request met retry logica."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.request(method, url, params=params)
                    response.raise_for_status()
                    return response.json()

                except httpx.HTTPError as e:
                    logger.warning(f"Open-Meteo request failed (attempt {attempt + 1}/{self.max_retries}): {e}")

                    if attempt == self.max_retries - 1:
                        raise

                    # Exponential backoff
                    await asyncio.sleep(2 ** attempt)

            raise Exception("Max retries exceeded")

    async def fetch_marine_data(
        self,
        lat: float = None,
        lon: float = None,
        hours: int = 168  # 7 dagen
    ) -> List[Dict[str, Any]]:
        """
        Haal marine data op (golfhoogtes, periodes, richtingen).

        Returns:
            Lijst van uurlijkse data points
        """
        if lat is None:
            lat = NOORDWIJK.lat
        if lon is None:
            lon = NOORDWIJK.lon

        params = {
            'latitude': lat,
            'longitude': lon,
            'hourly': ','.join([
                'wave_height',
                'wave_direction',
                'wave_period',
                'wind_wave_height',
                'wind_wave_direction',
                'wind_wave_period',
                'wind_wave_peak_period',
                'swell_wave_height',
                'swell_wave_direction',
                'swell_wave_period'
            ]),
            'timezone': TIMEZONE,
            'forecast_days': min(7, hours // 24 + 1)
        }

        logger.info(f"Fetching marine data from Open-Meteo for {lat}, {lon}")
        data = await self._request_with_retry(self.marine_url, params)

        # Parse response
        hourly = data.get('hourly', {})
        times = hourly.get('time', [])

        result = []
        for i, time_str in enumerate(times):
            result.append({
                'timestamp': datetime.fromisoformat(time_str.replace('Z', '+00:00')),
                'wave_height': hourly.get('wave_height', [])[i],
                'wave_direction': hourly.get('wave_direction', [])[i],
                'wave_period': hourly.get('wave_period', [])[i],
                'wind_wave_height': hourly.get('wind_wave_height', [])[i],
                'wind_wave_direction': hourly.get('wind_wave_direction', [])[i],
                'wind_wave_period': hourly.get('wind_wave_period', [])[i],
                'wind_wave_peak_period': hourly.get('wind_wave_peak_period', [])[i],
                'swell_wave_height': hourly.get('swell_wave_height', [])[i],
                'swell_wave_direction': hourly.get('swell_wave_direction', [])[i],
                'swell_wave_period': hourly.get('swell_wave_period', [])[i]
            })

        logger.info(f"Retrieved {len(result)} hours of marine data")
        return result

    async def fetch_forecast_data(
        self,
        lat: float = None,
        lon: float = None,
        models: List[str] = None,
        hours: int = 168
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Haal forecast data op (wind, temperatuur, neerslag) van Open-Meteo.

        Returns:
            Dictionary met 'knmi_seamless' als key en lijst van uurlijkse data als value.
        """
        if lat is None:
            lat = NOORDWIJK.lat
        if lon is None:
            lon = NOORDWIJK.lon

        params = {
            'latitude': lat,
            'longitude': lon,
            'hourly': ','.join([
                'wind_speed_10m',
                'wind_direction_10m',
                'wind_gusts_10m',
                'temperature_2m',
                'precipitation',
                'pressure_msl',
                'cloud_cover'
            ]),
            'wind_speed_unit': 'kn',
            'timezone': TIMEZONE,
            'forecast_days': min(16, hours // 24 + 1)
        }

        logger.info(f"Fetching forecast data from Open-Meteo for {lat}, {lon}")
        data = await self._request_with_retry(self.base_url, params)

        hourly = data.get('hourly', {})
        times = hourly.get('time', [])

        model_result = []
        for i, time_str in enumerate(times):
            model_result.append({
                'timestamp': datetime.fromisoformat(time_str.replace('Z', '+00:00')),
                'wind_speed': hourly.get('wind_speed_10m', [])[i],
                'wind_direction': hourly.get('wind_direction_10m', [])[i],
                'wind_gusts': hourly.get('wind_gusts_10m', [])[i],
                'temperature': hourly.get('temperature_2m', [])[i],
                'precipitation': hourly.get('precipitation', [])[i],
                'pressure': hourly.get('pressure_msl', [])[i],
                'cloud_cover': hourly.get('cloud_cover', [])[i]
            })

        logger.info(f"Retrieved {len(model_result)} hours of forecast data")
        return {'knmi_seamless': model_result}

    async def fetch_archive_data(
        self,
        start_date: str,
        end_date: str,
        lat: float = None,
        lon: float = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Haal historische data op voor backtesting.

        Args:
            start_date: Start datum (YYYY-MM-DD)
            end_date: Eind datum (YYYY-MM-DD)

        Returns:
            Dictionary met 'weather' en 'marine' data
        """
        if lat is None:
            lat = NOORDWIJK.lat
        if lon is None:
            lon = NOORDWIJK.lon

        # Weather archive
        weather_params = {
            'latitude': lat,
            'longitude': lon,
            'start_date': start_date,
            'end_date': end_date,
            'hourly': ','.join([
                'wind_speed_10m',
                'wind_direction_10m',
                'temperature_2m'
            ]),
            'wind_speed_unit': 'kn',
            'timezone': TIMEZONE
        }

        # Marine archive
        marine_params = {
            'latitude': lat,
            'longitude': lon,
            'start_date': start_date,
            'end_date': end_date,
            'hourly': ','.join([
                'wave_height',
                'wave_direction',
                'wave_period',
                'swell_wave_height',
                'swell_wave_period'
            ]),
            'timezone': TIMEZONE
        }

        logger.info(f"Fetching archive data from {start_date} to {end_date}")

        # Parallel requests
        weather_data, marine_data = await asyncio.gather(
            self._request_with_retry(self.archive_url, weather_params),
            self._request_with_retry(self.archive_url, marine_params)
        )

        # Parse responses
        result = {'weather': [], 'marine': []}

        # Weather data
        weather_hourly = weather_data.get('hourly', {})
        weather_times = weather_hourly.get('time', [])

        for i, time_str in enumerate(weather_times):
            result['weather'].append({
                'timestamp': datetime.fromisoformat(time_str.replace('Z', '+00:00')),
                'wind_speed': weather_hourly.get('wind_speed_10m', [])[i],
                'wind_direction': weather_hourly.get('wind_direction_10m', [])[i],
                'temperature': weather_hourly.get('temperature_2m', [])[i]
            })

        # Marine data
        marine_hourly = marine_data.get('hourly', {})
        marine_times = marine_hourly.get('time', [])

        for i, time_str in enumerate(marine_times):
            result['marine'].append({
                'timestamp': datetime.fromisoformat(time_str.replace('Z', '+00:00')),
                'wave_height': marine_hourly.get('wave_height', [])[i],
                'wave_direction': marine_hourly.get('wave_direction', [])[i],
                'wave_period': marine_hourly.get('wave_period', [])[i],
                'swell_wave_height': marine_hourly.get('swell_wave_height', [])[i],
                'swell_wave_period': marine_hourly.get('swell_wave_period', [])[i]
            })

        logger.info(f"Retrieved {len(result['weather'])} hours of weather and {len(result['marine'])} hours of marine archive data")
        return result

    def marine_data_to_wave_spectrum(self, marine_data: Dict[str, Any]) -> WaveSpectrum:
        """
        Converteer Open-Meteo marine data naar WaveSpectrum.

        Open-Meteo splitst al in wind_wave_* (lokaal opgewekt) vs swell_wave_*
        (van elders gepropageerd). Voor wind_wave kiezen we `peak_period` (Tp)
        boven `period` (Tm02): Tp is wat surfers en pro forecasters gebruiken
        om swell-vorm te beoordelen — Tm02 is een spectraal gemiddelde dat
        consistent lager uitvalt en chop/wind-sea als "korter" laat ogen.

        Open-Meteo levert geen swell_wave_peak_period; daar blijft `period` de
        beste beschikbare proxy.
        """
        timestamp = marine_data['timestamp']

        def _num(key: str) -> float:
            """Coerce None / missing → 0.0 (Open-Meteo returns null voor lege uren)."""
            return marine_data.get(key) or 0.0

        peaks = []

        wind_wave_height = _num('wind_wave_height')
        # Voorkeur: peak period (Tp). Fallback: mean period (Tm02) — bij missing data.
        wind_wave_period = _num('wind_wave_peak_period') or _num('wind_wave_period')
        if wind_wave_height > 0.1 and wind_wave_period > 0:
            peaks.append(SpectralPeak(
                frequency_mhz=1000 / wind_wave_period,
                period_s=wind_wave_period,
                height_m=wind_wave_height,
                direction_deg=int(_num('wind_wave_direction')),
                type=SwellType.WIND_SEA
            ))

        swell_height = _num('swell_wave_height')
        swell_period = _num('swell_wave_period')
        if swell_height > 0.1 and swell_period > 0:
            if swell_period >= 9:
                swell_type = SwellType.GROUND_SWELL
            elif swell_period >= 7:
                swell_type = SwellType.WIND_SWELL
            else:
                swell_type = SwellType.WIND_SEA

            peaks.append(SpectralPeak(
                frequency_mhz=1000 / swell_period,
                period_s=swell_period,
                height_m=swell_height,
                direction_deg=int(_num('swell_wave_direction')),
                type=swell_type
            ))

        wave_height = _num('wave_height')
        wave_period = _num('wave_period')
        if not peaks and wave_height > 0 and wave_period > 0:
            peaks.append(SpectralPeak(
                frequency_mhz=1000 / wave_period,
                period_s=wave_period,
                height_m=wave_height,
                direction_deg=int(_num('wave_direction')),
                type=SwellType.WIND_SEA
            ))

        return WaveSpectrum(
            timestamp=timestamp,
            significant_height_total=wave_height,
            mean_period=wave_period,
            mean_direction=int(_num('wave_direction')),
            peaks=peaks
        )


async def fetch_all_openmeteo_data(
    lat: float = None,
    lon: float = None,
    hours: int = 168
) -> Dict[str, Any]:
    """
    Haal alle Open-Meteo data op (marine + forecast).

    Returns:
        Dictionary met 'marine' en 'forecast' data
    """
    client = OpenMeteoClient()

    # Parallel requests
    marine_data, forecast_data = await asyncio.gather(
        client.fetch_marine_data(lat, lon, hours),
        client.fetch_forecast_data(lat, lon, hours=hours)
    )

    return {
        'marine': marine_data,
        'forecast': forecast_data
    }