"""
Rijkswaterstaat Waterinfo API integratie.
Haalt live boei data en tij voorspellingen op.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import httpx
from src.data.models import WaveSpectrum, SpectralPeak, SwellType

from src.config import (
    API_ENDPOINTS,
    RWS_STATIONS,
    TIMEZONE,
    DEBUG
)

logger = logging.getLogger(__name__)


class RWSClient:
    """Client voor Rijkswaterstaat Waterinfo API."""

    def __init__(self):
        self.timeout = 30.0
        self.max_retries = 3
        self.observation_url = API_ENDPOINTS['rws_observation']
        self.tide_url = API_ENDPOINTS['rws_tide']

    async def _request_with_retry(
        self,
        url: str,
        body: Dict[str, Any],
        method: str = "POST"
    ) -> Dict[str, Any]:
        """HTTP request met retry logica."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.request(method, url, json=body)
                    response.raise_for_status()
                    return response.json()

                except httpx.HTTPError as e:
                    logger.warning(f"RWS request failed (attempt {attempt + 1}/{self.max_retries}): {e}")

                    if attempt == self.max_retries - 1:
                        raise

                    # Exponential backoff
                    await asyncio.sleep(2 ** attempt)

            raise Exception("Max retries exceeded")

    async def fetch_buoy_data(
        self,
        station_code: str,
        hours_back: int = 24
    ) -> List[Dict[str, Any]]:
        """
        Haal live boei data op.

        Args:
            station_code: Station code (bijv. 'IJG1', 'A12', 'K13')
            hours_back: Aantal uren terug om data op te halen

        Returns:
            Lijst van uurlijkse data points
        """
        if station_code not in RWS_STATIONS:
            raise ValueError(f"Onbekend station: {station_code}")

        station = RWS_STATIONS[station_code]
        logger.info(f"Fetching buoy data from {station['name']} ({station_code})")

        # RWS API gebruikt Aquo metadata formaat
        # Hm0 = significante golfhoogte, Tm02 = gemiddelde periode, Th0 = gemiddelde richting
        body = {
            "AquoPlusWaarnemingMetadataLijst": [
                {
                    "AquoMetadata": {
                        "Compartiment": {"Code": "OW"},  # Oppervlakte water
                        "Grootheid": {"Code": "Hm0"}     # Significante golfhoogte
                    }
                },
                {
                    "AquoMetadata": {
                        "Compartiment": {"Code": "OW"},
                        "Grootheid": {"Code": "Tm02"}    # Gemiddelde periode
                    }
                },
                {
                    "AquoMetadata": {
                        "Compartiment": {"Code": "OW"},
                        "Grootheid": {"Code": "Th0"}     # Gemiddelde richting
                    }
                }
            ],
            "LocatieLijst": [{
                "X": station['lon'],
                "Y": station['lat'],
                "Code": station_code
            }]
        }

        try:
            data = await self._request_with_retry(self.observation_url, body)

            # Parse RWS response (formaat kan variëren)
            waarnemingen = data.get('WaarnemingenLijst', [])

            result = []
            for waarneming in waarnemingen:
                # Extraheer meetwaarden
                meetwaarden = waarneming.get('WaarnemingMetadata', {}).get('Meetwaarden', {})

                # Probeer Hm0, Tm02, Th0 te vinden
                hm0 = self._extract_meetwaarde(meetwaarden, 'Hm0')
                tm02 = self._extract_meetwaarde(meetwaarden, 'Tm02')
                th0 = self._extract_meetwaarde(meetwaarden, 'Th0')

                if hm0 is not None and tm02 is not None:
                    # Tijd van meting
                    tijd = waarneming.get('Tijd', '')
                    if tijd:
                        timestamp = datetime.fromisoformat(tijd.replace('Z', '+00:00'))
                    else:
                        timestamp = datetime.now()

                    result.append({
                        'timestamp': timestamp,
                        'station': station_code,
                        'height_m': hm0,
                        'period_s': tm02,
                        'direction_deg': th0 if th0 is not None else 0
                    })

            logger.info(f"Retrieved {len(result)} observations from {station_code}")
            return result

        except Exception as e:
            logger.error(f"Failed to fetch data from {station_code}: {e}")
            return []

    def _extract_meetwaarde(self, meetwaarden: Dict, code: str) -> Optional[float]:
        """Extraheer een specifieke meetwaarde uit RWS response."""
        for meetwaarde in meetwaarden.get('Meetwaarde', []):
            if meetwaarde.get('GrootheidCode') == code:
                waarde = meetwaarde.get('Waarde', {}).get('WaardeNumeriek')
                return float(waarde) if waarde is not None else None
        return None

    async def fetch_tide_predictions(
        self,
        location: str = "Scheveningen",
        days_ahead: int = 7
    ) -> Dict[str, Any]:
        """
        Haal tij voorspellingen op.

        Args:
            location: Locatie naam of code (bijv. 'Scheveningen', 'IJmuiden')
            days_ahead: Aantal dagen vooruit

        Returns:
            Dictionary met tij voorspellingen
        """
        logger.info(f"Fetching tide predictions for {location}")

        # RWS tij API vereist specifieke parameters
        body = {
            "AquoPlusWaarnemingMetadataLijst": [
                {
                    "AquoMetadata": {
                        "Compartiment": {"Code": "OW"},
                        "Grootheid": {"Code": "WATHTE"}  # Waterhoogte astronomisch
                    }
                }
            ],
            "LocatieLijst": [{
                "Code": location
            }],
            "Periode": {
                "EindDatumtijd": (datetime.now() + timedelta(days=days_ahead)).isoformat(),
                "StartDatumtijd": datetime.now().isoformat()
            }
        }

        try:
            data = await self._request_with_retry(self.tide_url, body)

            # Parse response
            waarnemingen = data.get('WaarnemingenLijst', [])

            tide_events = []
            current_phase = "onbekend"
            previous_level = None

            for waarneming in waarnemingen:
                tijd = waarneming.get('Tijd', '')
                if not tijd:
                    continue

                timestamp = datetime.fromisoformat(tijd.replace('Z', '+00:00'))
                meetwaarden = waarneming.get('WaarnemingMetadata', {}).get('Meetwaarden', {})
                level = self._extract_meetwaarde(meetwaarden, 'WATHTE')

                if level is not None:
                    # Bepaal fase
                    if previous_level is not None:
                        if level > previous_level:
                            current_phase = "opgaand"
                        elif level < previous_level:
                            current_phase = "afgaand"

                    tide_events.append({
                        'timestamp': timestamp,
                        'level_m': level,
                        'phase': current_phase
                    })

                    previous_level = level

            # Zoek hoogtij en laagtij momenten
            high_tides = []
            low_tides = []

            for i, event in enumerate(tide_events):
                if i > 0 and i < len(tide_events) - 1:
                    prev_phase = tide_events[i-1]['phase']
                    next_phase = tide_events[i+1]['phase']

                    if prev_phase == "opgaand" and next_phase == "afgaand":
                        high_tides.append(event)
                    elif prev_phase == "afgaand" and next_phase == "opgaand":
                        low_tides.append(event)

            logger.info(f"Retrieved {len(tide_events)} tide predictions, {len(high_tides)} high tides, {len(low_tides)} low tides")

            return {
                'tide_events': tide_events,
                'high_tides': high_tides,
                'low_tides': low_tides,
                'location': location
            }

        except Exception as e:
            logger.error(f"Failed to fetch tide predictions for {location}: {e}")
            return {
                'tide_events': [],
                'high_tides': [],
                'low_tides': [],
                'location': location
            }

    def buoy_data_to_wave_spectrum(self, buoy_data: Dict[str, Any]) -> WaveSpectrum:
        """
        Converteer RWS boei data naar WaveSpectrum.

        Opmerking: RWS geeft enkel gemiddelden (Hm0, Tm02, Th0),
        geen volledig spectrum. We maken een enkele piek.
        """
        timestamp = buoy_data['timestamp']
        height = buoy_data['height_m']
        period = buoy_data['period_s']
        direction = buoy_data['direction_deg']

        # Classificeer swell type op basis van periode
        if period >= 9:
            swell_type = SwellType.GROUND_SWELL
        elif period >= 7:
            swell_type = SwellType.WIND_SWELL
        else:
            swell_type = SwellType.WIND_SEA

        # Maak enkele spectrale piek
        peak = SpectralPeak(
            frequency_mhz=1000 / period if period > 0 else 0,
            period_s=period,
            height_m=height,
            direction_deg=int(direction),
            type=swell_type
        )

        return WaveSpectrum(
            timestamp=timestamp,
            significant_height_total=height,
            mean_period=period,
            mean_direction=int(direction),
            peaks=[peak]
        )


async def fetch_primary_buoy_data() -> Dict[str, Any]:
    """
    Haal data op van primaire boei voor Noordwijk (IJG1).

    Returns:
        Dictionary met IJG1 data en metadata
    """
    client = RWSClient()

    # Fetch IJG1 data
    ijg1_data = await client.fetch_buoy_data('IJG1', hours_back=24)

    # Converteer naar wave spectrum
    spectra = []
    for data_point in ijg1_data:
        spectrum = client.buoy_data_to_wave_spectrum(data_point)
        spectra.append(spectrum)

    return {
        'station': 'IJG1',
        'station_name': RWS_STATIONS['IJG1']['name'],
        'spectra': spectra,
        'raw_data': ijg1_data
    }


async def fetch_early_warning_buoys() -> Dict[str, Any]:
    """
    Haal data op van early warning boeien (A12, K13).

    Returns:
        Dictionary met data van alle early warning boeien
    """
    client = RWSClient()

    # Parallel requests
    a12_data, k13_data = await asyncio.gather(
        client.fetch_buoy_data('A12', hours_back=48),
        client.fetch_buoy_data('K13', hours_back=48)
    )

    # Converteer naar spectra
    result = {
        'A12': {
            'station_name': RWS_STATIONS['A12']['name'],
            'spectra': [client.buoy_data_to_wave_spectrum(d) for d in a12_data],
            'raw_data': a12_data
        },
        'K13': {
            'station_name': RWS_STATIONS['K13']['name'],
            'spectra': [client.buoy_data_to_wave_spectrum(d) for d in k13_data],
            'raw_data': k13_data
        }
    }

    return result


async def fetch_all_rws_data() -> Dict[str, Any]:
    """
    Haal alle RWS data op (primaire boei + early warning boeien + tij).

    Returns:
        Dictionary met alle RWS data
    """
    client = RWSClient()

    # Parallel requests
    primary_buoy, early_warning, tide = await asyncio.gather(
        fetch_primary_buoy_data(),
        fetch_early_warning_buoys(),
        client.fetch_tide_predictions("Scheveningen", days_ahead=7)
    )

    return {
        'primary_buoy': primary_buoy,
        'early_warning_buoys': early_warning,
        'tide': tide
    }