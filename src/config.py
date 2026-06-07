"""
Configuratie module voor Noordwijk Surf Alert Systeem.
Bevat alle locatie parameters, drempelwaarden en boei definities.
"""
import os
from dataclasses import dataclass

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
    tide_station: str = 'scheveningen'  # RWS WATHTE location code

# Noordwijk configuratie (primaire spot).
# Blocked range 350-30 = NNO sector geblokkeerd door pier van IJmuiden.
# Wrap-around: 350-360 én 0-30.
# Tide station 'ijmuiden': geografisch ~13 km noord van Noordwijk, sluit
# strakker aan dan Scheveningen (~20 km zuid + 15-20 min eerder in de cyclus).
# RWS DDAPI20 location code is lowercase, fallback naar scheveningen als
# ijmuiden 404 geeft (zie fetch_tide_predictions).
NOORDWIJK = LocationConfig(
    name="Noordwijk",
    lat=52.241,
    lon=4.428,
    beach_normal_deg=285,  # WNW
    preferred_swell_dir_min=270,
    preferred_swell_dir_max=360,
    blocked_swell_dir_min=350,
    blocked_swell_dir_max=30,
    tide_station='ijmuiden.buitenhaven',
)

# Rijkswaterstaat boei definities.
# `code` is de externe label, `rws_code` is de exacte locatiecode in de nieuwe DDAPI20
# WaterWebservices (zie https://ddapi20-waterwebservices.rijkswaterstaat.nl).
#
# `quantities` lijst (optioneel) bepaalt welke Aquo-grootheden we feitelijk
# bevragen voor deze locatie. Empirisch gevalideerd tegen OphalenCatalogus +
# 24h/48h live probes (mei 2026). Het beperken voorkomt onnodige 204-calls op
# grootheden die deze sensor niet publiceert (RWS geeft 204 No Content i.p.v.
# 200/empty list, wat ons retry-mechanisme zou triggeren).
#
# Universeel beschikbaar bij DDAPI20: 'Hm0', 'Tm02'.
# Andere codes hieronder kunnen per station ontbreken — alleen opnemen waar
# bewezen werkend. Onbekende stations krijgen de full-set als default.
RWS_STATIONS = {
    'IJG1': {
        'name': 'IJgeul',
        'lat': 52.450,
        'lon': 4.050,
        'use_for': ['noordwijk', 'zandvoort', 'scheveningen'],
        'lead_time_hours': 1,
        'code': 'IJG1',
        'rws_code': 'ijgeul.1',
        # IJG1 publiceert geen golfrichting (Th0/Th3 leeg) en geen S0BH; wel
        # Hmax + Tm-10 (peak-periode proxy via spectrale momenten m-1/m0).
        'quantities': ['Hm0', 'Tm02', 'Hmax', 'Tm-10', 'H1/3'],
    },
    'A12': {
        'name': 'A12 platform',
        'lat': 55.400,
        'lon': 3.817,
        'use_for': ['early_warning_north_swell'],
        'lead_time_hours': 10,
        'code': 'A12',
        'rws_code': 'a12',
        # A12 levert wel Hm0/Tm02/Tm-10/H1/3 — geen Hmax/Th0 actief.
        'quantities': ['Hm0', 'Tm02', 'Tm-10', 'H1/3', 'T1/3'],
    },
    'K13': {
        'name': 'K13 platform',
        'lat': 53.217,
        'lon': 3.217,
        'use_for': ['early_warning_west_north'],
        'lead_time_hours': 4,
        'code': 'K13',
        'rws_code': 'k13a.1',
        # K13 publiceert alleen Hm0, T (water temp) en Th3 (deining-richting).
        # GEEN Tm02 of andere periodes — we gebruiken K13 puur voor Hs early-warning.
        'quantities': ['Hm0', 'Th3'],
    },
    'J6': {
        'name': 'J6 platform',
        'lat': 53.817,
        'lon': 2.950,
        'use_for': ['early_warning_north_swell_short'],
        'lead_time_hours': 5,
        'code': 'J6',
        'rws_code': 'j6',
        'quantities': ['Hm0', 'Tm02', 'Tm-10', 'H1/3'],
    },
    'MUN1': {
        'name': 'IJmuiden Munitiestort',
        'lat': 52.466,
        'lon': 4.583,
        'use_for': ['wijk_aan_zee'],
        'lead_time_hours': 0,
        'code': 'MUN1',
        'rws_code': 'ijmuiden.munitiestort.1',
        # MUN1 heeft de rijkste sensor-suite van onze stations: directionele
        # golfdata + watertemperatuur. Gebruikt als directie-donor wanneer
        # IJG1 niets levert.
        'quantities': ['Hm0', 'Tm02', 'Hmax', 'Tm-10', 'Th0', 'Th3', 'T'],
    },
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

# Scoring gewichten. Tij is in v4 verhoogd van 15→20 omdat surf-meteorologie het
# als top-3 factor classificeert voor beachbreaks (vergelijkbaar met wind). Golf en
# wind iets verlaagd zodat totaal 100 blijft.
SCORING_WEIGHTS = {
    'golf_max': 38,
    'wind_max': 32,
    'tide_max': 20,
    'swell_dir_max': 10
}

# Surf-window drempels. Twee niveaus omdat referentie-forecaster regelmatig "longboard-only"
# windows benoemt die voor shortboard te slap zijn — die mogen wel in de digest,
# maar geven géén alert.
#   surfable: shortboard-rideable, multi-board acceptabel
#   longboard: alleen longboard/fish, lagere kwaliteit maar wel surfable
# Drempels gekalibreerd tegen referentie-forecaster' 19:30-21u window (woensdag 20 mei 2026,
# berekende score ~47-49) en zijn 14-16u window (berekende score ~43-47).
SURF_THRESHOLDS = {
    'surfable': 60,    # shortboard, voor alert-candidate
    'longboard': 42,   # longboard-only, alleen voor digest
    # Minimum golf_score (de wave-energie zelf, niet de combo-score) per kind.
    # Voorkomt de bug waarbij een 0,16m wave-uur op score 60 kwam door perfecte
    # wind + tij + richting bonussen. Een uur is alleen surfbaar als ER ÉCHT
    # GOLVEN ZIJN, niet omdat de omgeving toevallig perfect is.
    'min_golf_surfable': 15,   # ~ 1,0m wave bij 6s periode
    'min_golf_longboard': 5,   # ~ 0,5-0,6m wave bij 5s periode
}

# Fysieke minimums waaronder NIETS surfbaar is, ongeacht score.
# Per referentie-forecaster' lexicon: "flat" / "rimpelsurf" / "20cm windhoogte" = niets doen.
# Internationale consensus: 4 sec is de absolute drempel waaronder zelfs
# longboards niets kunnen met de chop.
SURF_MINIMUMS = {
    'min_hs_m': 0.30,          # absolute golfhoogte-floor, ook longboard
    'min_period_s': 4.0,       # te kort = pure rimpel, breekt niet
    'min_hs_shortboard_m': 1.0, # shortboard heeft echte energie nodig
    'min_period_shortboard_s': 5.0,
    'min_hs_fish_m': 0.5,
    'min_hs_midlength_m': 0.4,
    'min_hs_longboard_m': 0.4,
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

# RWS DDAPI20 concurrency throttle. DDAPI20 retourneert lege bodies onder
# load (waargenomen bij 24 parallelle calls = 8 grootheden × 3 boeien).
# We limiten via een module-level asyncio.Semaphore. Configureerbaar via
# env RWS_CONCURRENCY voor productie-tuning zonder code-deploy.
RWS_CONCURRENCY_LIMIT = int(os.getenv('RWS_CONCURRENCY', '3'))
# Retry op empty-body / JSON-decode errors (rate-limiting symptoom).
RWS_EMPTY_BODY_RETRIES = int(os.getenv('RWS_EMPTY_BODY_RETRIES', '2'))
RWS_EMPTY_BODY_RETRY_DELAY_S = float(os.getenv('RWS_EMPTY_BODY_RETRY_DELAY_S', '1.0'))
# HTTP connection-pooling. Eén shared AsyncClient i.p.v. per-call open/close
# voorkomt connection-overload op DDAPI20.
RWS_HTTP_TIMEOUT_S = float(os.getenv('RWS_HTTP_TIMEOUT_S', '30.0'))
RWS_MAX_KEEPALIVE_CONNECTIONS = int(os.getenv('RWS_MAX_KEEPALIVE_CONNECTIONS', '4'))
RWS_MAX_CONNECTIONS = int(os.getenv('RWS_MAX_CONNECTIONS', '8'))
RWS_USER_AGENT = os.getenv(
    'RWS_USER_AGENT',
    'noordwijk-surf-alert/1.0 (github.com/kiliantargaryen/SurfWeerWorkflow)'
)
OPEN_METEO_USER_AGENT = os.getenv(
    'OPEN_METEO_USER_AGENT',
    'noordwijk-surf-alert/1.0 (github.com/kiliantargaryen/SurfWeerWorkflow)'
)

# Anthropic configuratie. claude-3-5-haiku-20241022 is uitgefaseerd;
# claude-haiku-4-5 is de huidige Haiku-generatie (snel, ~$1/$5 per M tokens).
ANTHROPIC_CONFIG = {
    'api_key': os.getenv('ANTHROPIC_API_KEY'),
    # Sonnet als primair model: rijkere Nederlandse taal, meer nuance, beter
    # in referentie-forecaster-stijl prose. ~3× duurder dan Haiku maar absoluut nog steeds
    # verwaarloosbaar: ~€0,50-€1/maand bij 30-60 calls. Gemeten output is
    # significant beter (wind-wave interactie expliciet benoemd, uncertainty
    # gerendered, kortperiode windswell-uitleg).
    'model': 'claude-sonnet-4-5',
    # Haiku als fallback: bij Sonnet-overload schakelen we naar Haiku (goedkoper,
    # nog steeds capable maar minder rijk). In praktijk pakt Sonnet 95%+ van
    # de calls; Haiku alleen bij echte Anthropic-side Sonnet-outage.
    'fallback_model': 'claude-haiku-4-5',
    'max_tokens': 800,   # Legacy default — behouden voor backward compat met
                         # code-paden die geen expliciete max_tokens passeren.
                         # Nieuwe code gebruikt max_tokens_alert / max_tokens_digest.
    # Per-call-type token-budgets. Alerts zijn ~200 chars (50-80 tokens) — 300
    # is ruim. Digest 4 dagen kan tot ~1000 tokens met few-shot prompt + lange
    # uitleg; 1200 geeft buffer zonder budget op te eten.
    'max_tokens_alert': 300,
    'max_tokens_digest': 1200,
    'temperature': 0.4   # Lager dan default 0.7: minder vrije associaties,
                         # belangrijk voor anti-hallucinatie.
}

# Twilio configuratie
TWILIO_CONFIG = {
    'account_sid': os.getenv('TWILIO_ACCOUNT_SID'),
    'auth_token': os.getenv('TWILIO_AUTH_TOKEN'),
    'from_number': os.getenv('TWILIO_PHONE_NUMBER'),
    'recipient': os.getenv('RECIPIENT_PHONE_NUMBER', ''),
}

# SMS / notifier length-caps. Gecentraliseerd zodat validator én notifier
# vanuit één bron werken.
# - SMS_VALIDATOR_MAX_LEN: hard maximum bij SMSValidator (faalt bij overschrijding).
# - TWILIO_DIGEST_MAX_LEN: 10 SMS-segments × 160 chars (GSM-7) — kosten-plafond
#   per digest-push bij Twilio (€0.07/segment).
# - TWILIO_ALERT_MAX_LEN: 2 SMS-segments — alerts moeten kort blijven.
SMS_VALIDATOR_MAX_LEN = 1800
TWILIO_DIGEST_MAX_LEN = 1600
TWILIO_ALERT_MAX_LEN = 320

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

# ---------------------------------------------------------------------------
# Sprint 2 — structurele verbeteringen
# ---------------------------------------------------------------------------

# Multi-model wind triangulatie via Open-Meteo. ECMWF + GFS + KNMI Harmonie
# zijn drie semi-onafhankelijke modellen (verschillende grid-resolution,
# verschillende cycle-tijden, verschillende fysica). Hun spread is een
# directe uncertainty-proxy. Open-Meteo accepteert deze als één
# multi-value `models=` parameter in dezelfde API-call (geen extra quota).
OPEN_METEO_MODELS = ['knmi_seamless', 'ecmwf_ifs025', 'gfs_seamless']

# Drempels voor wind-spread confidence-penalty (#8).
# Boven deze waarden geven de modellen genoeg onzekerheidssignaal om de
# golf_score met een multiplier 0.85-1.0 te penaliseren (lineaire schaal).
WIND_SPREAD_THRESHOLDS = {
    'speed_kn_warning': 5.0,      # spread > 5 kn (std dev) → start penalty
    'speed_kn_max': 12.0,         # spread > 12 kn → maximale penalty
    'direction_deg_warning': 25.0, # angular spread > 25° → start penalty
    'direction_deg_max': 60.0,    # > 60° → maximale penalty
    'min_factor': 0.85,           # multiplier op golf_score bij max spread
}

# Partition-aware scoring (#10): wind-zee partitie krijgt lagere multiplier
# dan swell-partitie. Bron: pro-forecaster cheat-sheet — een wind-zee veld
# levert minder schone face per kW dan een gelijkmatige swell van zelfde Hs.
PARTITION_WEIGHTS = {
    'swell_multiplier': 1.00,     # volledige weging swell-energie
    'wind_sea_multiplier': 0.65,  # wind-zee 65% van swell-energie
}

# Tide-flank features (#11) — bonus voor mid-rising sweet spot.
TIDE_FLANK = {
    'mid_low': 0.40,              # ondergrens "mid-tide"
    'mid_high': 0.70,              # bovengrens "mid-tide"
    'mid_rising_bonus': 2.0,       # bonus mid-rising
    'mid_falling_bonus': 1.0,      # bonus mid-falling (helft)
}

# Diurnal wind-decay (#12): rond zonsondergang valt zeebries-component weg
# bij lage bewolking. Toegepast als aftrek op effectieve wind_speed_kn.
DIURNAL_WIND_DECAY = {
    'hours_before_sunset': 2.0,    # start venster (uur vóór sunset)
    'hours_after_sunset': 1.0,     # einde venster (uur na sunset)
    'max_cloud_cover_pct': 50.0,   # alleen bij lage bewolking
    'speed_reduction_kn': 2.5,     # aftrek op effectieve wind-snelheid
}

# Continue refractie pier-shadow (#9). Pier-shadow center ~10° (NNO).
# Sigmoid-based: 0-15° NNO = zware shadow, 30°+ = vrijwel geen blokkade.
PIER_REFRACTION = {
    'shadow_center_deg': 10.0,
    'shadow_half_width_deg': 12.0, # waar transmissie 50% bereikt
    'min_transmission': 0.10,      # min energie-doorgang in shadow center
    'max_transmission': 1.00,      # geen blokkade buiten shadow
    'long_period_bonus': 0.15,     # +15% transmissie voor Tp ≥ 10s (refractie)
}

# Wind-face penalty (referentie-pariteit). Harde ONSHORE wind vernielt de
# wave-face (chop, closeouts) — los van golfhoogte. `wave_face_quality` (0,4-1,0)
# was als één van zes modifiers te verdund (weighted-sum, gewicht 0,20 → max
# ~12% effect), waardoor een uitgeblazen 2,7m-golf bij 22kn onshore alsnog golf
# ~35 hield en als 'surfable' uitkwam. Hier als EIGEN multiplier op de golf-score
# met fit-bare sterkte: strength=1,0 → volledige face_quality geldt, lager dempt.
# Seed 0,5 = MILD verdedigbaar (volledig geblazen face ≈ -30%): dempt een
# uitgeblazen grote golf zonder de verdicten van andere (matig-onshore) dagen om
# te gooien. Benchmark 2026-06-07 toonde dat een sterkere seed (0,85) overshoot
# (day-verdict-agreement 50%→25%). De échte sterkte wordt op data gefit zodra
# het corpus groot genoeg is — NIET hand-getuned op één dag.
WIND_FACE_PENALTY = {
    'strength': 0.5,
    'min_factor': 0.40,
}

# Hard size-cap met multiplicatieve aggregation (#13).
# `env_bonus_cap`: maximaal hoeveel de omgeving (wind+tide+dir) de golf
# kan boosten als percentage. 2.5 = max +250% bonus bij perfecte combo.
# Gekalibreerd zodat:
# - 0.3m golf (~5pt) × max env bonus → ~17 (terecht onder surfable=60)
# - 1.0m groundswell (~20pt) × max env bonus → ~70 (terecht surfable)
# - 1.5m+ wave (~30+pt) × max env bonus → bereikt additieve som
# De bedoeling van #13 is voorkomen dat MARGINALE golven (<0.5m) tot
# epic-score schalen door perfect environment — niet om reasonable
# small-but-quality groundswells (referentie-forecaster' "smal alert 11-12u zonder wind")
# uit te sluiten van surfable-threshold.
SIZE_CAP_AGGREGATION = {
    'env_bonus_cap': 2.5,          # max +250% via wind/tide/dir
    'use_multiplicative': True,    # multiplicatief naast additief, min van beide
    # Clean-only longboard-promotie (referentie-pariteit). Bij hoge env_fraction
    # (schone, aflandige/zwakke wind + gunstig tij) leunt de score-blend richting
    # additief, zodat een schoon klein golfje (golf>=5) de longboard-tier haalt
    # i.p.v. door de multiplicatieve cap als 'flat' te worden weggedrukt.
    # env_fraction <= floor → geen boost; >= full → volle boost; weight bepaalt
    # hoe hard richting additief geduwd wordt (0=uit, 1=volledig additief bij full).
    'clean_env_floor': 0.60,
    'clean_env_full': 0.90,
    'clean_alpha_weight': 0.7,
}

# ---------------------------------------------------------------------------
# GELEERDE PARAMETERS — data-driven override (referentie-pariteit, geen hardcoding)
# ---------------------------------------------------------------------------
# De drempels/calibratie hierboven zijn VOORLOPIGE seed-waarden (fysisch
# redelijk, maar met de hand gekozen). De leer-loop (scripts/calibrate.py) fit
# deze op het referentie-archief en schrijft de uitkomst naar
# `data/learned_params.json`. Staat dat bestand er, dan overschrijven de
# geleerde waarden de seed — zo zijn de parameters een FIT-OUTPUT i.p.v.
# hardcoded. Layout: {"SURF_THRESHOLDS": {...}, "ALERT_CONFIG": {...},
# "SIZE_CAP_AGGREGATION": {...}, "_meta": {"fitted_at":..., "n_pairs":...,
# "agreement":...}}.  Onbekende keys worden genegeerd; ontbrekend bestand = seed.
_LEARNED_PARAMS_PATH = os.getenv('LEARNED_PARAMS_PATH', 'data/learned_params.json')


def _apply_learned_params(path: str) -> dict:
    """Merge geleerde parameters over de seed-dicts. Returnt de _meta (of {})."""
    import json
    from pathlib import Path as _P
    p = _P(path)
    if not p.exists():
        return {}
    try:
        learned = json.loads(p.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}
    targets = {
        'SURF_THRESHOLDS': SURF_THRESHOLDS,
        'ALERT_CONFIG': ALERT_CONFIG,
        'SIZE_CAP_AGGREGATION': SIZE_CAP_AGGREGATION,
        'SCORING_WEIGHTS': SCORING_WEIGHTS,
        'WIND_FACE_PENALTY': WIND_FACE_PENALTY,
    }
    for group, target in targets.items():
        for k, v in (learned.get(group) or {}).items():
            if k in target and isinstance(v, (int, float)):
                target[k] = v
    return learned.get('_meta') or {}


LEARNED_PARAMS_META = _apply_learned_params(_LEARNED_PARAMS_PATH)
