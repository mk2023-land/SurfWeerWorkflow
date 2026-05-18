"""
Configuratie module voor Noordwijk Surf Alert Systeem.
Bevat alle locatie parameters, drempelwaarden en boei definities.
"""
import os
from dataclasses import dataclass
from typing import Dict, List
from dotenv import load_dotenv

load_dotenv()

@dataclass
class LocationConfig:
    """Configuratie voor een surf spot."""
    name: str
    lat: float
    lon: float
    beach_normal_deg: int  # Richting waar het strand naar wijst
    preferred_swell_dir_min: int
    preferred_swell_dir_max: int
    blocked_swell_dir_min: int  # Geblokkeerd door obstakels (bijv. pier)
    blocked_swell_dir_max: int

# Noordwijk configuratie (primaire spot)
NOORDWIJK = LocationConfig(
    name="Noordwijk",
    lat=52.241,
    lon=4.428,
    beach_normal_deg=285,  # WNW
    preferred_swell_dir_min=270,  # W (start voorkeursgebied)
    preferred_swell_dir_max=360,  # Via N naar NW (volledige cirkel)
    blocked_swell_dir_min=0,  # Geen harde blokkering
    blocked_swell_dir_max=0
)

# Rijkswaterstaat boei definities
RWS_STATIONS = {
    'IJG1': {
        'name': 'IJgeul',
        'lat': 52.450,
        'lon': 4.050,
        'use_for': ['noordwijk', 'zandvoort', 'scheveningen'],
        'lead_time_hours': 1,
        'code': 'IJG1'
    },
    'A12': {
        'name': 'A12 platform',
        'lat': 55.400,
        'lon': 3.817,
        'use_for': ['early_warning_north_swell'],
        'lead_time_hours': 10,
        'code': 'A12'
    },
    'K13': {
        'name': 'K13 platform',
        'lat': 53.217,
        'lon': 3.217,
        'use_for': ['early_warning_west_north'],
        'lead_time_hours': 4,
        'code': 'K13'
    },
    'J6': {
        'name': 'J6 platform',
        'lat': 53.817,
        'lon': 2.950,
        'use_for': ['early_warning_north_swell_short'],
        'lead_time_hours': 5,
        'code': 'J6'
    },
    'MUN1': {
        'name': 'IJmuiden Munitiestort',
        'lat': 52.466,
        'lon': 4.583,
        'use_for': ['wijk_aan_zee'],
        'lead_time_hours': 0,
        'code': 'MUN1'
    }
}

# Alert configuratie drempelwaarden
ALERT_CONFIG = {
    'min_peak_score': 75,
    'min_window_duration_hours': 1,
    'max_score_drop_in_window': 15,
    'min_rarity_percentile': 70,
    'cooldown_hours_between_alerts': int(os.getenv('COOLDOWN_HOURS', '4')),
    'max_alerts_per_week': int(os.getenv('MAX_ALERTS_PER_WEEK', '8')),
    'alerts_enabled': os.getenv('ALERTS_ENABLED', 'false').lower() == 'true'
}

# Scoring gewichten
SCORING_WEIGHTS = {
    'golf_max': 40,
    'wind_max': 35,
    'tide_max': 15,
    'swell_dir_max': 10
}

# Wind richtingen voor Noordwijk
WIND_DIRECTIONS = {
    'onshore': (225, 315),     # WZW tot NW
    'offshore': (75, 135),     # OZO tot ZZO
    'side_offshore': (135, 225), # ZZO tot WZW
    'side_onshore': (315, 360) # NW tot N
}

# API endpoints
API_ENDPOINTS = {
    'open_meteo_marine': 'https://marine-api.open-meteo.com/v1/marine',
    'open_meteo_forecast': 'https://api.open-meteo.com/v1/forecast',
    'open_meteo_archive': 'https://archive-api.open-meteo.com/v1/archive',
    'rws_observation': 'https://waterwebservices.rijkswaterstaat.nl/ONLINEWAARNEMINGENSERVICES_DBO/OphalenLaatsteWaarnemingen',
    'rws_tide': 'https://waterwebservices.rijkswaterstaat.nl/ONLINEWAARNEMINGENSERVICES_DBO/OphalenWaarnemingen'
}

# Anthropic configuratie
ANTHROPIC_CONFIG = {
    'api_key': os.getenv('ANTHROPIC_API_KEY'),
    'model': 'claude-3-5-haiku-20241022',
    'max_tokens': 320,
    'temperature': 0.7
}

# MessageBird configuratie
MESSAGEBIRD_CONFIG = {
    'api_key': os.getenv('MESSAGEBIRD_API_KEY'),
    'originator': os.getenv('MESSAGEBIRD_ORIGINATOR', 'SurfAlert'),
    'recipient': os.getenv('RECIPIENT_PHONE_NUMBER', '')
}

# Debug configuratie
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'

# Timezone
TIMEZONE = 'Europe/Amsterdam'

# Swell type classificatie op basis van periode
SWELL_TYPES = {
    'wind_sea': (0, 7),       # < 7 seconden
    'wind_swell': (7, 9),     # 7-9 seconden
    'ground_swell': (9, 999)  # >= 9 seconden
}

# Frequentie ranges (mhz → seconden)
FREQUENCY_RANGES = {
    'wind_sea': (200, 999),      # ≥200 mhz = ≤5 sec
    'wind_swell': (111, 200),    # 111-200 mhz = 5-9 sec
    'ground_swell': (0, 111)     # ≤111 mhz = ≥9 sec
}

# Validatie thresholds
VALIDATION_CONFIG = {
    'max_score_deviation': 15,        # Max score verschil in validatie
    'min_validation_accuracy': 0.70,  # Min 70% van cases moeten kloppen
    'number_precision': 0.1           # Getallen precisie voor validatie
}