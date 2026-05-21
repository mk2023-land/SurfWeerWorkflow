"""
Open-Meteo API integratie voor weergegevens.
Ondersteunt Marine, Forecast en Archive APIs met async en retry logica.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import httpx
from ..models import HourState, WaveSpectrum, WindState, TideState, SpectralPeak, SwellType

from src.config import (
    API_ENDPOINTS,
    NOORDWIJK,
    TIMEZONE,
    OPEN_METEO_MODELS,
    OPEN_METEO_USER_AGENT,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared httpx.AsyncClient voor Open-Meteo: keep-alive + User-Agent.
# Voorkomt herhaalde TCP-handshakes bij parallelle marine/forecast calls.
# ---------------------------------------------------------------------------
_open_meteo_client: Optional[httpx.AsyncClient] = None
_open_meteo_client_lock = asyncio.Lock()


async def _get_shared_open_meteo_client(timeout: float = 30.0) -> httpx.AsyncClient:
    global _open_meteo_client
    if _open_meteo_client is None or _open_meteo_client.is_closed:
        async with _open_meteo_client_lock:
            if _open_meteo_client is None or _open_meteo_client.is_closed:
                _open_meteo_client = httpx.AsyncClient(
                    timeout=timeout,
                    limits=httpx.Limits(
                        max_keepalive_connections=4,
                        max_connections=8,
                    ),
                    headers={'User-Agent': OPEN_METEO_USER_AGENT},
                )
    return _open_meteo_client


# ---------------------------------------------------------------------------
# Sanity-bovengrenzen (per veld). Buiten range → None + WARNING.
# Voorkomt dat een API-glitch (negatieve Hs, 200kn wind, etc.) downstream
# scoring corrupteert.
# ---------------------------------------------------------------------------
_OM_WAVE_HEIGHT_MAX_M = 15.0
_OM_WAVE_PERIOD_MAX_S = 30.0
_OM_WIND_SPEED_MAX_KN = 100.0
_OM_GUST_MAX_KN = 150.0
_OM_TEMP_MIN_C = -30.0
_OM_TEMP_MAX_C = 50.0
_OM_PRESSURE_MIN_HPA = 900.0
_OM_PRESSURE_MAX_HPA = 1080.0


def _check_range(
    value: Optional[float],
    lo: float,
    hi: float,
    field: str,
) -> Optional[float]:
    """
    Sanity-check op een numeriek veld. None-pass-through (we onderscheiden
    None = missing van waarde-uit-range). Out-of-range → None + WARNING.
    """
    if value is None:
        return None
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return None
    if fv < lo or fv > hi:
        logger.warning(
            f"Open-Meteo {field} buiten range ({fv} not in [{lo}, {hi}]); → None"
        )
        return None
    return fv


def _sanity_check_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pas range-checks toe op alle numerieke velden in een parsed row.
    Werkt in-place én retourneert row. Onbekende/None-keys raken niet
    aangetast (None blijft None — geen valse 0.0).
    """
    # Marine wave velden
    for key in ('wave_height', 'wind_wave_height', 'swell_wave_height'):
        if key in row:
            row[key] = _check_range(row.get(key), -0.01, _OM_WAVE_HEIGHT_MAX_M, key)
            if row.get(key) is not None and row[key] < 0:
                logger.warning(
                    f"Open-Meteo {key} negatief; → None"
                )
                row[key] = None
    for key in (
        'wave_period', 'wind_wave_period', 'wind_wave_peak_period',
        'swell_wave_period',
    ):
        if key in row:
            row[key] = _check_range(row.get(key), 0.0, _OM_WAVE_PERIOD_MAX_S, key)
    # Forecast meteo
    if 'wind_speed' in row:
        row['wind_speed'] = _check_range(row.get('wind_speed'), 0.0, _OM_WIND_SPEED_MAX_KN, 'wind_speed')
    if 'wind_gusts' in row:
        row['wind_gusts'] = _check_range(row.get('wind_gusts'), 0.0, _OM_GUST_MAX_KN, 'wind_gusts')
    if 'temperature' in row:
        row['temperature'] = _check_range(row.get('temperature'), _OM_TEMP_MIN_C, _OM_TEMP_MAX_C, 'temperature')
    if 'apparent_temperature' in row:
        row['apparent_temperature'] = _check_range(
            row.get('apparent_temperature'), _OM_TEMP_MIN_C, _OM_TEMP_MAX_C, 'apparent_temperature'
        )
    if 'dew_point' in row:
        row['dew_point'] = _check_range(row.get('dew_point'), _OM_TEMP_MIN_C, _OM_TEMP_MAX_C, 'dew_point')
    if 'pressure' in row:
        row['pressure'] = _check_range(row.get('pressure'), _OM_PRESSURE_MIN_HPA, _OM_PRESSURE_MAX_HPA, 'pressure')
    if 'sea_surface_temperature' in row:
        row['sea_surface_temperature'] = _check_range(
            row.get('sea_surface_temperature'), -5.0, 40.0, 'sea_surface_temperature'
        )
    return row


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
        """
        HTTP request met retry logica. Gebruikt shared AsyncClient zodat
        TCP-connecties hergebruikt worden voor parallelle marine+forecast
        calls (geen connection-overload).
        """
        client = await _get_shared_open_meteo_client(timeout=self.timeout)
        for attempt in range(self.max_retries):
            try:
                response = await client.request(method, url, params=params)
                response.raise_for_status()
                return response.json()

            except httpx.HTTPError as e:
                logger.warning(f"Open-Meteo request failed (attempt {attempt + 1}/{self.max_retries}): {e}")

                if attempt == self.max_retries - 1:
                    raise

                # Exponential backoff met jitter — voorkomt thundering-herd
                # wanneer parallelle marine+forecast-calls tegelijkertijd in
                # retry-state belanden.
                await asyncio.sleep(2 ** attempt + random.uniform(0, 0.5))

        raise Exception("Max retries exceeded")

    # Marine velden basis (primary ECMWAM-model)
    _MARINE_BASE_FIELDS = (
        'wave_height',
        'wave_direction',
        'wave_period',
        'wind_wave_height',
        'wind_wave_direction',
        'wind_wave_period',
        'wind_wave_peak_period',
        'swell_wave_height',
        'swell_wave_direction',
        'swell_wave_period',
    )

    # Nieuwe gratis Open-Meteo marine-velden (zee-oppervlakte temperatuur,
    # echte stroming en sea-level fields). Open-Meteo retourneert null voor
    # uren waar deze niet beschikbaar zijn — _get() handelt dat af.
    _MARINE_EXTRA_FIELDS = (
        'sea_surface_temperature',
        'ocean_current_velocity',
        'ocean_current_direction',
        'sea_level_height_msl',
        'invert_barometer_height',
    )

    async def fetch_marine_data(
        self,
        lat: float = None,
        lon: float = None,
        hours: int = 168,  # 7 dagen
        models: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Haal marine data op (golfhoogtes, periodes, richtingen).

        Args:
            models: Optionele lijst van extra wave-modellen naast de standaard
                ECMWAM (de Open-Meteo default). Voorbeeld: ``['ewam']`` voegt
                DWD EWAM 5km Europese kust-resolutie toe. Bij multi-model
                retourneert Open-Meteo per veld een suffixed kolom
                (``wave_height_ewam``) — die wordt als
                ``wave_height_ewam`` in de output-row meegegeven.

                Default: ``None`` (geen extra models, single-source).

        Returns:
            Lijst van uurlijkse data points. Per row staan de basis-velden
            (wave_height, swell_*, etc.) plus de nieuwe extra-velden
            (sea_surface_temperature, ocean_current_*, sea_level_height_msl,
            invert_barometer_height). Bij ``models=['ewam']`` worden ook
            ``wave_height_ewam``, ``wave_period_ewam``, ``wave_direction_ewam``
            (en gelijksoortige suffixed keys) toegevoegd als optionele keys.
        """
        if lat is None:
            lat = NOORDWIJK.lat
        if lon is None:
            lon = NOORDWIJK.lon

        all_fields = list(self._MARINE_BASE_FIELDS) + list(self._MARINE_EXTRA_FIELDS)

        params = {
            'latitude': lat,
            'longitude': lon,
            'hourly': ','.join(all_fields),
            'timezone': TIMEZONE,
            'forecast_days': min(7, hours // 24 + 1),
        }
        if models:
            params['models'] = ','.join(models)

        logger.info(
            f"Fetching marine data from Open-Meteo for {lat}, {lon} "
            f"(extra_models={models or 'none'})"
        )
        data = await self._request_with_retry(self.marine_url, params)

        # Parse response
        hourly = data.get('hourly', {})
        times = hourly.get('time', [])

        def _get(field: str, i: int):
            col = hourly.get(field)
            if not col or i >= len(col):
                return None
            return col[i]

        result: List[Dict[str, Any]] = []
        for i, time_str in enumerate(times):
            row: Dict[str, Any] = {
                'timestamp': datetime.fromisoformat(time_str.replace('Z', '+00:00')),
                # Basis-velden
                'wave_height': _get('wave_height', i),
                'wave_direction': _get('wave_direction', i),
                'wave_period': _get('wave_period', i),
                'wind_wave_height': _get('wind_wave_height', i),
                'wind_wave_direction': _get('wind_wave_direction', i),
                'wind_wave_period': _get('wind_wave_period', i),
                'wind_wave_peak_period': _get('wind_wave_peak_period', i),
                'swell_wave_height': _get('swell_wave_height', i),
                'swell_wave_direction': _get('swell_wave_direction', i),
                'swell_wave_period': _get('swell_wave_period', i),
                # Nieuwe extra-velden (optioneel — kunnen None zijn)
                'sea_surface_temperature': _get('sea_surface_temperature', i),
                'ocean_current_velocity': _get('ocean_current_velocity', i),
                'ocean_current_direction': _get('ocean_current_direction', i),
                'sea_level_height_msl': _get('sea_level_height_msl', i),
                'invert_barometer_height': _get('invert_barometer_height', i),
            }

            # Multi-model suffixed velden (bv. DWD EWAM). Open-Meteo gebruikt
            # bij multi-model een suffix per kolom: 'wave_height_ewam' etc.
            # We laten de suffixed keys 1-op-1 doorvloeien zodat downstream
            # callers eenvoudig kunnen toetsen op spread tussen modellen.
            if models:
                for model in models:
                    for field in self._MARINE_BASE_FIELDS:
                        suffixed = f"{field}_{model}"
                        if suffixed in hourly:
                            row[suffixed] = _get(suffixed, i)

            # Sanity-check op alle gerelateerde velden (None bij out-of-range,
            # geen 0.0-mapping zodat None vs legitimate-zero onderscheiden blijft).
            _sanity_check_row(row)
            result.append(row)

        logger.info(
            f"Retrieved {len(result)} hours of marine data "
            f"(fields={len(all_fields)}, extra_models={models or 'none'})"
        )
        return result

    async def fetch_marine_data_ewam(
        self,
        lat: float = None,
        lon: float = None,
        hours: int = 168,
    ) -> List[Dict[str, Any]]:
        """
        Helper: marine data met DWD EWAM 5km als enige model.

        Levert dezelfde shape als ``fetch_marine_data`` (rows met de basis-
        en extra-velden), maar met ``wave_height_ewam`` etc. als suffixed
        keys naast de basis. Handig wanneer alleen de EWAM-bron nodig is
        (bv. voor backtests of bias-onderzoek).
        """
        return await self.fetch_marine_data(
            lat=lat, lon=lon, hours=hours, models=['ewam']
        )

    async def fetch_forecast_data(
        self,
        lat: float = None,
        lon: float = None,
        models: List[str] = None,
        hours: int = 168
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Haal forecast data op (wind, temperatuur, neerslag) van Open-Meteo.

        Sprint 2 #8: vraagt MULTIPLE models op in één API-call. Open-Meteo
        accepteert `models=knmi_seamless,ecmwf_ifs025,gfs_seamless` en
        retourneert dan per uur per model een aparte serie. Geen extra
        API-quota — één request, drie wind-streams.

        Returns:
            Dictionary met per-model key (bv. 'knmi_seamless', 'ecmwf_ifs025',
            'gfs_seamless') → lijst van uurlijkse data dicts.
        """
        if lat is None:
            lat = NOORDWIJK.lat
        if lon is None:
            lon = NOORDWIJK.lon
        if models is None:
            models = OPEN_METEO_MODELS

        # Per-model meteo fields (each model heeft eigen serie).
        per_model_fields = [
            'wind_speed_10m',
            'wind_direction_10m',
            'wind_gusts_10m',
            'temperature_2m',
            'precipitation',
            'pressure_msl',
            'cloud_cover',
            'apparent_temperature',
            'relative_humidity_2m',
            'dew_point_2m',
            'visibility',
            'weather_code',
            'is_day',
            'uv_index',
            'sunshine_duration',
        ]

        # Atmospheric-stability / convectie fields: niet zinvol om per model
        # te vergelijken (vaak alleen door ICON/GFS geleverd, niet per regional
        # model). Open-Meteo retourneert deze bij multi-model met suffix van
        # het PRIMARY model — we accepteren zowel bare als suffixed keys en
        # vallen terug op wat beschikbaar is (zie _stability_get hieronder).
        stability_fields = [
            'cape',
            'lifted_index',
            'convective_inhibition',
            'boundary_layer_height',
        ]

        params = {
            'latitude': lat,
            'longitude': lon,
            'hourly': ','.join(per_model_fields + stability_fields),
            'wind_speed_unit': 'kn',
            'timezone': TIMEZONE,
            'forecast_days': min(16, hours // 24 + 1),
            'models': ','.join(models),
        }

        logger.info(
            f"Fetching forecast data from Open-Meteo for {lat}, {lon} "
            f"with models={models}"
        )
        data = await self._request_with_retry(self.base_url, params)

        hourly = data.get('hourly', {})
        times = hourly.get('time', [])

        # Bij meerdere modellen retourneert Open-Meteo per veld varianten met
        # `_modelname` suffix. Bij single-model is er geen suffix.
        # Voorbeeld bij multi-model:
        #   'wind_speed_10m_knmi_seamless': [...],
        #   'wind_speed_10m_ecmwf_ifs025': [...],
        #   'wind_speed_10m_gfs_seamless': [...]
        # Bij single-model:
        #   'wind_speed_10m': [...]

        multi_model = len(models) > 1

        def _key(field: str, model: str) -> Optional[str]:
            """
            Vind kolomnaam voor (field, model).

            Bij multi-model is een suffixed key VERPLICHT — terugvallen op de
            bare key zou alle modellen naar dezelfde kolom laten resolven en
            de wind-spread silently nul maken (Sprint 2 #8 dead-feature bug).
            Returns None als de suffixed key ontbreekt → caller logt en
            slaat dit model over.

            Bij single-model is bare prima.
            """
            suffixed = f"{field}_{model}"
            if suffixed in hourly:
                return suffixed
            if multi_model:
                return None
            return field if field in hourly else None

        def _stability_key(field: str) -> Optional[str]:
            """
            Vind kolomnaam voor een stability/convectie veld. Open-Meteo
            kan bij multi-model alleen suffixed kolommen retourneren
            (cape_knmi_seamless, cape_ecmwf_ifs025, …). We pakken de eerste
            beschikbare (PRIMARY model preference: knmi → ecmwf → gfs → any).
            Bij single-model is de bare key prima.
            """
            if field in hourly:
                return field
            # Probeer modellen in voorkeursvolgorde.
            for preferred in models:
                suffixed = f"{field}_{preferred}"
                if suffixed in hourly:
                    return suffixed
            # Laatste redmiddel: scan alle keys op prefix.
            for k in hourly.keys():
                if k.startswith(f"{field}_"):
                    return k
            return None

        # Pre-resolve stability keys (1x per call — niet per model loop).
        cape_key = _stability_key('cape')
        li_key = _stability_key('lifted_index')
        cin_key = _stability_key('convective_inhibition')
        pbl_key = _stability_key('boundary_layer_height')

        result: Dict[str, List[Dict[str, Any]]] = {}
        for model in models:
            ws_key = _key('wind_speed_10m', model)
            wd_key = _key('wind_direction_10m', model)
            wg_key = _key('wind_gusts_10m', model)
            t_key = _key('temperature_2m', model)
            pr_key = _key('precipitation', model)
            p_key = _key('pressure_msl', model)
            cc_key = _key('cloud_cover', model)
            # Nieuwe per-model fields
            at_key = _key('apparent_temperature', model)
            rh_key = _key('relative_humidity_2m', model)
            dp_key = _key('dew_point_2m', model)
            vis_key = _key('visibility', model)
            wc_key = _key('weather_code', model)
            isday_key = _key('is_day', model)
            uv_key = _key('uv_index', model)
            sun_key = _key('sunshine_duration', model)

            # Essentiële velden (wind speed + dir) moeten aanwezig zijn.
            if ws_key is None or wd_key is None:
                logger.warning(
                    "Model '%s' ontbreekt suffixed wind-keys in Open-Meteo "
                    "response; sla over (multi_model=%s)",
                    model, multi_model,
                )
                continue

            model_result: List[Dict[str, Any]] = []
            for i, time_str in enumerate(times):
                def _get(key: Optional[str]):
                    if key is None:
                        return None
                    col = hourly.get(key, [])
                    return col[i] if i < len(col) else None
                row = {
                    'timestamp': datetime.fromisoformat(time_str.replace('Z', '+00:00')),
                    'wind_speed': _get(ws_key),
                    'wind_direction': _get(wd_key),
                    'wind_gusts': _get(wg_key),
                    'temperature': _get(t_key),
                    'precipitation': _get(pr_key),
                    'pressure': _get(p_key),
                    'cloud_cover': _get(cc_key),
                    # NIEUW: per-model atmospheric / display fields
                    'apparent_temperature': _get(at_key),
                    'relative_humidity': _get(rh_key),
                    'dew_point': _get(dp_key),
                    'visibility': _get(vis_key),
                    'weather_code': _get(wc_key),
                    'is_day': _get(isday_key),
                    'uv_index': _get(uv_key),
                    'sunshine_duration': _get(sun_key),
                    # NIEUW: shared stability fields (zelfde voor elk model)
                    'cape': _get(cape_key),
                    'lifted_index': _get(li_key),
                    'convective_inhibition': _get(cin_key),
                    'boundary_layer_height': _get(pbl_key),
                }
                _sanity_check_row(row)
                model_result.append(row)
            result[model] = model_result

        # Zorg dat 'knmi_seamless' altijd aanwezig is (fallback voor callers
        # die de oude single-model interface verwachten).
        if 'knmi_seamless' not in result and result:
            result['knmi_seamless'] = next(iter(result.values()))

        # Sanity check: bij multi-model moeten de wind-snelheid series
        # daadwerkelijk verschillen. Anders is iets misgegaan in het parsen
        # (of geeft Open-Meteo identieke series terug — zeldzaam maar
        # we willen het wel zien in de logs).
        if multi_model and len(result) >= 2 and times:
            sample_n = min(12, len(times))
            speed_signature = {}
            for name, series in result.items():
                sig = tuple(
                    round(row['wind_speed'], 3) if row['wind_speed'] is not None else None
                    for row in series[:sample_n]
                )
                speed_signature[name] = sig
            unique_signatures = set(speed_signature.values())
            if len(unique_signatures) < 2:
                logger.warning(
                    "Multi-model wind data collapsed to single source — alle "
                    "modellen identieke wind_speed reeksen: %s. "
                    "wind-spread confidence zal 0 zijn.",
                    list(speed_signature.keys()),
                )

        logger.info(
            f"Retrieved {len(times)} hours of forecast data for "
            f"{len(result)} model(s): {list(result.keys())}"
        )
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

        # Helper: length-safe index access. Voorkomt IndexError als één kolom
        # korter is dan time[] (Open-Meteo doet dat soms voor velden die nog
        # niet beschikbaar zijn op het einde van het archive-window).
        def _get_at(hourly: Dict[str, Any], field: str, i: int):
            col = hourly.get(field)
            if not col or i >= len(col):
                return None
            return col[i]

        # Weather data
        weather_hourly = weather_data.get('hourly', {})
        weather_times = weather_hourly.get('time', [])

        for i, time_str in enumerate(weather_times):
            row = {
                'timestamp': datetime.fromisoformat(time_str.replace('Z', '+00:00')),
                'wind_speed': _get_at(weather_hourly, 'wind_speed_10m', i),
                'wind_direction': _get_at(weather_hourly, 'wind_direction_10m', i),
                'temperature': _get_at(weather_hourly, 'temperature_2m', i),
            }
            _sanity_check_row(row)
            result['weather'].append(row)

        # Marine data
        marine_hourly = marine_data.get('hourly', {})
        marine_times = marine_hourly.get('time', [])

        for i, time_str in enumerate(marine_times):
            row = {
                'timestamp': datetime.fromisoformat(time_str.replace('Z', '+00:00')),
                'wave_height': _get_at(marine_hourly, 'wave_height', i),
                'wave_direction': _get_at(marine_hourly, 'wave_direction', i),
                'wave_period': _get_at(marine_hourly, 'wave_period', i),
                'swell_wave_height': _get_at(marine_hourly, 'swell_wave_height', i),
                'swell_wave_period': _get_at(marine_hourly, 'swell_wave_period', i),
            }
            _sanity_check_row(row)
            result['marine'].append(row)

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

        None vs zero: voor heights geldt dat 0 een legitieme "flat" waarde is —
        we mappen None → 0.0 (legacy gedrag). Voor periodes/richtingen
        betekent None "geen meting" en blijft None door — een peak met
        onbekende periode is namelijk zinloos.
        """
        timestamp = marine_data['timestamp']

        def _num_safe(key: str) -> float:
            """Coerce None / missing → 0.0 (legitiem voor heights & flat-water uren)."""
            v = marine_data.get(key)
            if v is None:
                return 0.0
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        def _num_optional(key: str) -> Optional[float]:
            """Retourneer None bij missing/None — voor periode/richting waar None != 0."""
            v = marine_data.get(key)
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        peaks = []

        wind_wave_height = _num_safe('wind_wave_height')
        # Voorkeur: peak period (Tp). Fallback: mean period (Tm02) — bij missing data.
        # Optional-helpers: als beide None zijn, blijft de peak gewoon achterwege
        # (geen valse 0.0 die later als geldig wordt geïnterpreteerd).
        wwpp = _num_optional('wind_wave_peak_period')
        wwp = _num_optional('wind_wave_period')
        wind_wave_period = wwpp if (wwpp is not None and wwpp > 0) else wwp
        wind_wave_dir = _num_optional('wind_wave_direction')
        if (
            wind_wave_height > 0.1
            and wind_wave_period is not None
            and wind_wave_period > 0
        ):
            peaks.append(SpectralPeak(
                frequency_mhz=1000 / wind_wave_period,
                period_s=wind_wave_period,
                height_m=wind_wave_height,
                direction_deg=int(wind_wave_dir) if wind_wave_dir is not None else 0,
                type=SwellType.WIND_SEA
            ))

        swell_height = _num_safe('swell_wave_height')
        swell_period = _num_optional('swell_wave_period')
        swell_dir = _num_optional('swell_wave_direction')
        if (
            swell_height > 0.1
            and swell_period is not None
            and swell_period > 0
        ):
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
                direction_deg=int(swell_dir) if swell_dir is not None else 0,
                type=swell_type
            ))

        wave_height = _num_safe('wave_height')
        wave_period_opt = _num_optional('wave_period')
        wave_dir_opt = _num_optional('wave_direction')
        if (
            not peaks
            and wave_height > 0
            and wave_period_opt is not None
            and wave_period_opt > 0
        ):
            peaks.append(SpectralPeak(
                frequency_mhz=1000 / wave_period_opt,
                period_s=wave_period_opt,
                height_m=wave_height,
                direction_deg=int(wave_dir_opt) if wave_dir_opt is not None else 0,
                type=SwellType.WIND_SEA
            ))

        return WaveSpectrum(
            timestamp=timestamp,
            significant_height_total=wave_height,
            mean_period=wave_period_opt if wave_period_opt is not None else 0.0,
            mean_direction=int(wave_dir_opt) if wave_dir_opt is not None else 0,
            peaks=peaks
        )


# ---------------------------------------------------------------------------
# Module-level singleton voor OpenMeteoClient. Construction is goedkoop maar
# we willen één gedeelde instance zodat callers consistent dezelfde stateless
# config gebruiken (en, indirect, dezelfde shared httpx.AsyncClient via
# `_get_shared_open_meteo_client`).
# ---------------------------------------------------------------------------
_openmeteo_client_singleton: Optional[OpenMeteoClient] = None


def _get_openmeteo_client() -> OpenMeteoClient:
    """Lazy-init singleton accessor voor OpenMeteoClient."""
    global _openmeteo_client_singleton
    if _openmeteo_client_singleton is None:
        _openmeteo_client_singleton = OpenMeteoClient()
    return _openmeteo_client_singleton


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
    client = _get_openmeteo_client()

    # Parallel requests
    marine_data, forecast_data = await asyncio.gather(
        client.fetch_marine_data(lat, lon, hours),
        client.fetch_forecast_data(lat, lon, hours=hours)
    )

    return {
        'marine': marine_data,
        'forecast': forecast_data
    }