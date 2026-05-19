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

# Noordwijk configuratie (primaire spot).
# Blocked range 350-30 = NNO sector geblokkeerd door pier van IJmuiden.
# Wrap-around: 350-360 én 0-30.
NOORDWIJK = LocationConfig(
    name="Noordwijk",
    lat=52.241,
    lon=4.428,
    beach_normal_deg=285,  # WNW
    preferred_swell_dir_min=270,
    preferred_swell_dir_max=360,
    blocked_swell_dir_min=350,
    blocked_swell_dir_max=30
)

# Rijkswaterstaat boei definities.
# `code` is de externe label, `rws_code` is de exacte locatiecode in de nieuwe DDAPI20
# WaterWebservices (zie https://ddapi20-waterwebservices.rijkswaterstaat.nl).
RWS_STATIONS = {
    'IJG1': {
        'name': 'IJgeul',
        'lat': 52.450,
        'lon': 4.050,
        'use_for': ['noordwijk', 'zandvoort', 'scheveningen'],
        'lead_time_hours': 1,
        'code': 'IJG1',
        'rws_code': 'ijgeul.1'
    },
    'A12': {
        'name': 'A12 platform',
        'lat': 55.400,
        'lon': 3.817,
        'use_for': ['early_warning_north_swell'],
        'lead_time_hours': 10,
        'code': 'A12',
        'rws_code': 'a12'
    },
    'K13': {
        'name': 'K13 platform',
        'lat': 53.217,
        'lon': 3.217,
        'use_for': ['early_warning_west_north'],
        'lead_time_hours': 4,
        'code': 'K13',
        'rws_code': 'k13a.1'
    },
    'J6': {
        'name': 'J6 platform',
        'lat': 53.817,
        'lon': 2.950,
        'use_for': ['early_warning_north_swell_short'],
        'lead_time_hours': 5,
        'code': 'J6',
        'rws_code': 'j6'
    },
    'MUN1': {
        'name': 'IJmuiden Munitiestort',
        'lat': 52.466,
        'lon': 4.583,
        'use_for': ['wijk_aan_zee'],
        'lead_time_hours': 0,
        'code': 'MUN1',
        'rws_code': 'ijmuiden.munitiestort.1'
    }
}

# Alert configuratie drempelwaarden
ALERT_CONFIG = {
    'min_peak_score': 75,
    'min_window_duration_hours': 1,
    'max_score_drop_in_window': 15,
    'min_rarity_percentile': 70,
    'cooldown_hours_between_alerts': int(os.getenv('COOLDOWN_HOURS', '4')),
    'max_alerts_per_week': int(os.getenv('MAX_ALERTS_PER_WEEK', '8')),  # Geen limiet meer
    'alerts_enabled': os.getenv('ALERTS_ENABLED', 'false').lower() == 'true',
    # max_sms_cost_per_month_eur fungeert als hard plafond voor de Twilio-fallback.
    # Bij NOTIFIER=ntfy of NOTIFIER=email zijn de notificatie-kosten €0 en is de cap niet relevant.
    'max_sms_cost_per_month_eur': 5.0,
    'max_anthropic_cost_per_month_eur': 3.0,  # ~3000 Claude Haiku calls
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

# API endpoints. RWS migreerde de WaterWebservices in 2026 van
# waterwebservices.rijkswaterstaat.nl (deprecated, retourneert 301) naar
# ddapi20-waterwebservices.rijkswaterstaat.nl/ONLINEWAARNEMINGENSERVICES.
API_ENDPOINTS = {
    'open_meteo_marine': 'https://marine-api.open-meteo.com/v1/marine',
    'open_meteo_forecast': 'https://api.open-meteo.com/v1/forecast',
    'open_meteo_archive': 'https://archive-api.open-meteo.com/v1/archive',
    'rws_latest': 'https://ddapi20-waterwebservices.rijkswaterstaat.nl/ONLINEWAARNEMINGENSERVICES/OphalenLaatsteWaarnemingen',
    'rws_period': 'https://ddapi20-waterwebservices.rijkswaterstaat.nl/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen'
}

# Anthropic configuratie. claude-3-5-haiku-20241022 is uitgefaseerd;
# claude-haiku-4-5 is de huidige Haiku-generatie (snel, ~$1/$5 per M tokens).
ANTHROPIC_CONFIG = {
    'api_key': os.getenv('ANTHROPIC_API_KEY'),
    'model': 'claude-haiku-4-5',
    'max_tokens': 320,
    'temperature': 0.7
}

# Twilio configuratie
TWILIO_CONFIG = {
    'account_sid': os.getenv('TWILIO_ACCOUNT_SID'),
    'auth_token': os.getenv('TWILIO_AUTH_TOKEN'),
    'from_number': os.getenv('TWILIO_PHONE_NUMBER'),
    'recipient': os.getenv('RECIPIENT_PHONE_NUMBER', ''),
}

# Debug configuratie — alleen log-level. Voor "niet verzenden" gebruik --dry-run.
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