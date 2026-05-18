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
    DEBUG
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
        Haal forecast data op met meerdere modellen.

        Args:
            models: Lijst van model namen (knmi_seamless, ecmwf_ifs025, gfs_seamless, ukmo_global_deterministic)

        Returns:
            Dictionary met model naam als key en lijst van uurlijkse data als value
        """
        if lat is None:
            lat = NOORDWIJK.lat
        if lon is None:
            lon = NOORDWIJK.lon

        if models is None:
            models = []  # Geen models, gebruikt default

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
            'forecast_days': min(16, hours // 24 + 1),
            'models': ','.join(models)
        }
        """
        Haal forecast data op met meerdere modellen.

        Args:
            models: Lijst van model namen (knmi_seamless, ecmwf_ifs025, gfs_seamless, ukmo_global_deterministic)

        Returns:
            Dictionary met model naam als key en lijst van uurlijkse data als value
        """
        if lat is None:
            lat = NOORDWIJK.lat
        if lon is None:
            lon = NOORDWIJK.lon

        if models is None:
            models = ['knmi_seamless', 'ecmwf_ifs025']

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
            'forecast_days': min(16, hours // 24 + 1),
            'models': ','.join(models)
        }

        logger.info(f"Fetching forecast data (default model)")
        data = await self._request_with_retry(self.base_url, params)

        # Parse response (single model, default)
        result = {}
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

        result['default'] = model_result
        logger.info(f"Retrieved {len(model_result)} hours of forecast data")

            if not model_data:
                logger.warning(f"No data returned for model {model}")
                continue

            hourly = model_data.get('hourly', {})
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

            result[model] = model_result
            logger.info(f"Retrieved {len(model_result)} hours of forecast data from {model}")

        return result

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

        Opmerking: Open-Meteo geeft al gesplitste data (wind_wave vs swell_wave),
        dus we kunnen dit direct gebruiken.
        """
        timestamp = marine_data['timestamp']

        # Maak spectrale pieken voor wind sea en swell
        peaks = []

        # Wind sea piek (indien aanwezig)
        if marine_data.get('wind_wave_height', 0) > 0.1:
            wind_sea_peak = SpectralPeak(
                frequency_mhz=1000 / marine_data['wind_wave_period'],
                period_s=marine_data['wind_wave_period'],
                height_m=marine_data['wind_wave_height'],
                direction_deg=int(marine_data['wind_wave_direction']),
                type=SwellType.WIND_SEA
            )
            peaks.append(wind_sea_peak)

        # Swell piek (indien aanwezig)
        if marine_data.get('swell_wave_height', 0) > 0.1:
            swell_period = marine_data['swell_wave_period']

            # Classificeer swell type
            if swell_period >= 9:
                swell_type = SwellType.GROUND_SWELL
            elif swell_period >= 7:
                swell_type = SwellType.WIND_SWELL
            else:
                swell_type = SwellType.WIND_SEA

            swell_peak = SpectralPeak(
                frequency_mhz=1000 / swell_period,
                period_s=swell_period,
                height_m=marine_data['swell_wave_height'],
                direction_deg=int(marine_data['swell_wave_direction']),
                type=swell_type
            )
            peaks.append(swell_peak)

        # Als geen pieken, maak dummy piek op basis van totaal
        if not peaks and marine_data.get('wave_height', 0) > 0:
            total_peak = SpectralPeak(
                frequency_mhz=1000 / marine_data['wave_period'],
                period_s=marine_data['wave_period'],
                height_m=marine_data['wave_height'],
                direction_deg=int(marine_data['wave_direction']),
                type=SwellType.WIND_SEA  # Default
            )
            peaks.append(total_peak)

        return WaveSpectrum(
            timestamp=timestamp,
            significant_height_total=marine_data.get('wave_height', 0.0),
            mean_period=marine_data.get('wave_period', 0.0),
            mean_direction=int(marine_data.get('wave_direction', 0.0)),
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