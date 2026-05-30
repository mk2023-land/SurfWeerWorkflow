"""
Data models voor het Noordwijk Surf Alert Systeem.
Definieert alle data structuren die door het systeem worden gebruikt.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Literal
from enum import Enum


def _sigmoid(x: float) -> float:
    """Logistic sigmoid: 1 / (1 + exp(-x))."""
    # Numerieke stabiliteit: clamp x om overflow te voorkomen
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


class SwellType(Enum):
    """Type swell gebaseerd op periode."""
    WIND_SEA = "wind_sea"
    WIND_SWELL = "wind_swell"
    GROUND_SWELL = "ground_swell"


class AlertType(Enum):
    """Type alert dat kan worden getriggerd."""
    SWELL_ARRIVAL = "T1"      # Verre storm swell aankomend
    WIND_SHIFT = "T2"         # Koufront/trog passage
    WIND_DIP = "T3"           # Locale windstilte
    SUSTAINED_GROUNDSWELL = "T4"  # Aanhoudende groundswell
    TIDE_GATED = "T5"         # Combinatie window met gunstig tij


@dataclass
class SpectralPeak:
    """Energiepiek in een golf spectrum."""
    frequency_mhz: float
    period_s: float
    height_m: float
    direction_deg: int
    type: SwellType

    @classmethod
    def from_frequency(cls, frequency_mhz: float, height_m: float, direction_deg: int) -> 'SpectralPeak':
        """Maak SpectralPeak op basis van frequentie (mhz)."""
        period_s = 1000 / frequency_mhz

        # Classificeer type op basis van frequentie
        if frequency_mhz >= 200:
            swell_type = SwellType.WIND_SEA
        elif frequency_mhz >= 111:
            swell_type = SwellType.WIND_SWELL
        else:
            swell_type = SwellType.GROUND_SWELL

        return cls(
            frequency_mhz=frequency_mhz,
            period_s=period_s,
            height_m=height_m,
            direction_deg=direction_deg,
            type=swell_type
        )


@dataclass
class WaveSpectrum:
    """Volledig golf spectrum met meerdere pieken."""
    timestamp: datetime
    significant_height_total: float  # Hm0
    mean_period: float               # Tm02
    mean_direction: float            # Th0
    peaks: List[SpectralPeak] = field(default_factory=list)

    # Optionele boei-observatie velden (RWS IJG1). Alleen aanwezig wanneer een
    # recente boei-meting deze uren overschrijft (typisch t=0..3u nowcast).
    peak_period_observed_s: Optional[float] = None    # van boei Tp
    directional_spread_deg: Optional[float] = None    # van boei SObh

    def get_peak(self, swell_type: Optional[SwellType] = None) -> Optional[SpectralPeak]:
        """Retourneer hoogste peak; optioneel gefilterd op swell_type."""
        candidates = self.peaks if swell_type is None else [p for p in self.peaks if p.type == swell_type]
        return max(candidates, key=lambda p: p.height_m) if candidates else None

    # Deprecation shims — externe callers blijven werken; intern roepen we
    # de geconsolideerde `get_peak()` aan.
    def get_dominant_peak(self) -> Optional[SpectralPeak]:
        return self.get_peak()

    def get_groundswell_component(self) -> Optional[SpectralPeak]:
        return self.get_peak(SwellType.GROUND_SWELL)

    def get_wind_sea_component(self) -> Optional[SpectralPeak]:
        return self.get_peak(SwellType.WIND_SEA)


@dataclass
class WindState:
    """Wind state voor een specifiek tijdstip."""
    speed_kn: float
    direction_deg: int
    gusts_kn: Optional[float] = None


@dataclass
class TideState:
    """Tij state voor een specifiek tijdstip."""
    level_m: float              # Hoogte boven NAP
    phase: str                   # "opgaand", "afgaand", "hoogtij", "laagtij"
    next_low: datetime
    next_high: datetime
    # Dagelijkse tij-range (HW - LW) in meters. Gebruikt voor spring/doodtij
    # modulatie van het optimale tij-venster. None = onbekend → modulator uit.
    daily_range_m: Optional[float] = None
    # Tijden van het meest recente en eerstvolgende kerntij-event (HW of LW).
    # Gebruikt voor tidal-current modeling: stroming is 0 op een kentering
    # (HW/LW), piekt mid-cycle, en is asymmetrisch (HW→LW kan langer duren
    # dan LW→HW in NL door semi-diurnale ongelijkheid).
    last_turn_time: Optional[datetime] = None
    next_turn_time: Optional[datetime] = None

    @property
    def normalized_level(self) -> float:
        """Genormaliseerd niveau 0.0-1.0 (laagtij=0.0, hoogtij=1.0)."""
        # Simpele benadering: gebruik fase en niveau
        if self.phase in ["hoogtij", "laagtij"]:
            return 1.0 if self.phase == "hoogtij" else 0.0

        # Lineair interpoleren tussen laag en hoog
        total_range = 3.0  # Typische getij variatie in NL
        normalized = (self.level_m + 1.5) / total_range  # -1.5m = laag, +1.5m = hoog
        return max(0.0, min(1.0, normalized))

    def tidal_current_intensity(self, now: datetime) -> float:
        """
        Schatting van de horizontale tij-stroming-sterkte op moment `now`,
        genormaliseerd op 0.0 (slack water) tot ~1.2 (springtij mid-cycle).

        Theorie:
        - Op een kentering (HW of LW) is de stroming nul (slack water).
        - Halverwege het halve-tij-venster bereikt de stroming maximum.
        - Voor één halve cyclus: intensity = sin(π · fraction_through),
          met fraction_through = tijd_sinds_kentering / lengte_half_cycle.
        - Spring/neap modulator: bij springtij (daily_range ≥ 2.0m) wordt
          de stroming sterker, bij doodtij (< 1.6m) zwakker.

        Asymmetrische half-cycle lengte (typisch in NL: HW→LW ~6-8u,
        LW→HW ~4-6u) wordt expliciet meegenomen via last_turn_time en
        next_turn_time.

        Returns 0.0 als kentering-tijden niet beschikbaar zijn.
        """
        import math
        if not (self.last_turn_time and self.next_turn_time):
            return 0.0

        # Naive timestamp handling consistent met de rest van de codebase:
        # neem ruwe datetime-arithmetiek aan (alle tijden in dezelfde TZ).
        def _strip(d):
            return d.replace(tzinfo=None) if d.tzinfo else d

        now_n = _strip(now)
        last_n = _strip(self.last_turn_time)
        next_n = _strip(self.next_turn_time)

        half_cycle_h = (next_n - last_n).total_seconds() / 3600.0
        if half_cycle_h <= 0:
            return 0.0

        elapsed_h = (now_n - last_n).total_seconds() / 3600.0
        fraction = max(0.0, min(1.0, elapsed_h / half_cycle_h))
        intensity = math.sin(math.pi * fraction)

        # Spring/neap modulator. Range ~1.6m = doodtij (~0.8x),
        # ~2.0m = gemiddelde springtij (~1.0x), ~2.5m+ = extreme spring (~1.2x).
        if self.daily_range_m is not None:
            range_factor = max(0.6, min(1.25, self.daily_range_m / 2.0))
            intensity *= range_factor

        # HW→LW (eb / afgaand) is sterker dan LW→HW (vloed / opgaand) in NL.
        # Asymmetrie-factor: +15% bij eb, -15% bij vloed. Geen modifier op
        # exacte kentering (hoogtij/laagtij).
        if self.phase == 'afgaand':
            intensity *= 1.15
        elif self.phase == 'opgaand':
            intensity *= 0.85
        # 'hoogtij' / 'laagtij' / overig: geen modifier

        return intensity


@dataclass
class HourState:
    """Volledige state voor één uur."""
    timestamp: datetime
    location_name: str

    # Golf data
    wave_spectrum: WaveSpectrum

    # Wind data
    wind: WindState

    # Tij data
    tide: TideState

    # Forecast metadata
    forecast_source: str = "open-meteo"
    confidence: float = 1.0  # 0.0-1.0, model onzekerheid
    # Welke wave-bron leverde dit uur: 'primary' (ECMWAM, T+0..T+3.9) of
    # 'extended_fallback' (ecmwf_wam025 totals + gwam splitsing voor T+4+).
    # Gebruikt door de SMS-pipeline om de LLM een lagere-zekerheid-hint te
    # laten geven voor verre forecast-dagen.
    wave_source: str = "primary"

    # ---- Atmospheric context (Open-Meteo Forecast) — optioneel, default None ----
    air_temperature_c: Optional[float] = None
    precipitation_mm: Optional[float] = None
    visibility_m: Optional[float] = None
    weather_code: Optional[int] = None          # WMO code
    relative_humidity_pct: Optional[float] = None
    dew_point_c: Optional[float] = None
    uv_index: Optional[float] = None
    sunshine_duration_s: Optional[float] = None

    # ---- Atmospheric instability (primary-model shared) — optioneel ----
    cape_jkg: Optional[float] = None
    lifted_index: Optional[float] = None
    convective_inhibition_jkg: Optional[float] = None
    boundary_layer_height_m: Optional[float] = None

    # ---- Ocean context (Open-Meteo Marine + RWS) — optioneel ----
    sea_surface_temperature_c: Optional[float] = None
    ocean_current_velocity_ms: Optional[float] = None
    ocean_current_direction_deg: Optional[float] = None
    sea_level_height_msl_m: Optional[float] = None
    storm_surge_cm: Optional[float] = None      # van RWS (measured − astronomical)


@dataclass
class ScoreBreakdown:
    """Score breakdown voor één uur."""
    timestamp: datetime

    # Component scores — max waarden komen uit SCORING_WEIGHTS in config.py.
    # Hardcoded getallen niet vertrouwen; raadpleeg config bij twijfel. Huidige
    # waarden (v4): golf_max=38, wind_max=32, tide_max=20, swell_dir_max=10.
    golf_score: float        # max SCORING_WEIGHTS['golf_max']  (38)
    wind_score: float        # max SCORING_WEIGHTS['wind_max']  (32)
    tide_score: float        # max SCORING_WEIGHTS['tide_max']  (20)
    swell_dir_bonus: float   # max SCORING_WEIGHTS['swell_dir_max']  (10)

    # Probabilistische confidence (Sprint 3 #17). Default 1.0 = volle vertrouwen.
    # Sprint 2 (multi-model wind-spread) zet deze lager bij grote inter-model
    # afwijking. Sprint 3 levert hier alleen de struct: LLM-input vertaalt
    # >=0.85 → "hoog", >=0.65 → "matig", anders "laag".
    confidence: float = 1.0

    @property
    def total_score(self) -> float:
        """
        Totale score 0-100.

        Soft-blend tussen additief en multiplicatief regime, in plaats van
        de oude harde min()-transitie rond golf=15. Sigmoid-blend voorkomt
        score-jumps en is fysisch correcter: bij golf=10 dominant
        multiplicatief (kleine wave → environment niet boost), bij golf=20
        dominant additief (grote wave → standard scoring), tussenin gradueel.

            alpha = sigmoid((golf - 15) / 5)
            total = alpha * additive + (1 - alpha) * multiplicative

        Confidence-modulatie: total *= clamp(confidence, 0.7, 1.0).
        confidence=1.0 → geen effect, confidence=0.7 → 30% penalty.

        Zonder golven (golf_score < 1) tellen wind/tij/richting niet mee —
        vlak water blijft onsurfbaar ongeacht offshore wind of perfect tij.
        """
        if self.golf_score < 1:
            return round(self.golf_score, 1)

        # Additieve formule (oude versie behouden voor grote golven)
        additive = self.golf_score + self.wind_score + self.tide_score + self.swell_dir_bonus

        # Multiplicatieve formule: env_bonus 0..env_bonus_cap
        from src.config import SCORING_WEIGHTS, SIZE_CAP_AGGREGATION
        if not SIZE_CAP_AGGREGATION['use_multiplicative']:
            total = additive
        else:
            env_max = (
                SCORING_WEIGHTS['wind_max']
                + SCORING_WEIGHTS['tide_max']
                + SCORING_WEIGHTS['swell_dir_max']
            )
            env_score = self.wind_score + self.tide_score + self.swell_dir_bonus
            env_fraction = (env_score / env_max) if env_max > 0 else 0.0
            env_bonus = SIZE_CAP_AGGREGATION['env_bonus_cap'] * env_fraction
            multiplicative = self.golf_score * (1.0 + env_bonus)

            # Soft-blend: sigmoid op (golf - 15) / 5
            #   golf=10 → alpha ≈ 0.27 (mostly multiplicative)
            #   golf=15 → alpha = 0.50 (50/50)
            #   golf=20 → alpha ≈ 0.73 (mostly additive)
            alpha = _sigmoid((self.golf_score - 15.0) / 5.0)
            total = alpha * additive + (1.0 - alpha) * multiplicative

        # Confidence-penalty (cap 0.7-1.0 zodat low-confidence niet 0 wordt)
        conf = max(0.7, min(1.0, self.confidence))
        total *= conf

        return round(total, 1)

    def is_surfable(self) -> bool:
        """
        Controleer of score hoog genoeg is voor (shortboard) surfen.

        Twee voorwaarden: total_score boven drempel ÉN golf_score zelf hoog
        genoeg. Anders kan een uur met perfect wind/tij/richting maar geen
        echte golven onterecht als surfbaar uitgekomen.
        """
        from src.config import SURF_THRESHOLDS
        return (
            self.total_score >= SURF_THRESHOLDS['surfable'] and
            self.golf_score >= SURF_THRESHOLDS['min_golf_surfable']
        )

    def is_longboard_rideable(self) -> bool:
        """
        Controleer of score hoog genoeg is voor longboard-rideable condities.

        Strikt zwakker dan `is_surfable`: een uur dat surfbaar is, is ook
        longboard-rideable; een longboard-uur is niet noodzakelijk shortboard.
        Zelfde dubbele voorwaarde — score + minimum-golf.
        """
        from src.config import SURF_THRESHOLDS
        return (
            self.total_score >= SURF_THRESHOLDS['longboard'] and
            self.golf_score >= SURF_THRESHOLDS['min_golf_longboard']
        )


@dataclass
class SurfWindow:
    """Een aaneengesloten periode van goede surfcondities.

    `kind` onderscheidt 'surfable' (≥SURF_THRESHOLDS['surfable']) van
    'longboard' (alleen ≥SURF_THRESHOLDS['longboard']). Alleen 'surfable'
    windows zijn alert-candidate; 'longboard' windows verschijnen alleen
    in de digest om Tobias' "longboard prima" momenten te dekken.
    """
    start: datetime
    end: datetime
    peak_score: int
    median_score: int
    peak_hour: datetime
    triggers: List[AlertType]
    stability: float           # 0.0-1.0, hoe stabiel de score is
    rarity_percentile: float   # 0-100, vs seizoensbaseline
    hourly_scores: List[ScoreBreakdown] = field(default_factory=list)
    kind: str = 'surfable'     # 'surfable' of 'longboard'

    @property
    def duration_hours(self) -> float:
        """Duur van het window in uren."""
        return (self.end - self.start).total_seconds() / 3600

    @property
    def is_alertworthy(self) -> bool:
        """
        Controleer of dit window alert-waardig is.

        Longboard-only windows zijn nooit alert-waardig — die zijn voor
        ervaren surfers met longboards die rideability accepteren onder
        de shortboard-drempel.
        """
        if self.kind != 'surfable':
            return False
        return (
            self.peak_score >= 75 and
            len(self.triggers) >= 1 and
            self.stability >= 0.6 and
            self.rarity_percentile >= 70 and
            self.duration_hours >= 1
        )


@dataclass
class AlertCandidate:
    """Kandidaat alert dat kan worden verstuurd."""
    alert_type: AlertType
    window: SurfWindow
    detection_time: datetime
    explanation: str
    confidence: float  # 0.0-1.0

    def to_dict(self) -> Dict:
        """Converteer naar dictionary voor logging/LLM input."""
        return {
            'type': self.alert_type.value,
            'window': {
                'start': self.window.start.isoformat(),
                'end': self.window.end.isoformat(),
                'peak_score': self.window.peak_score,
                'duration_hours': self.window.duration_hours
            },
            'detection_time': self.detection_time.isoformat(),
            'explanation': self.explanation,
            'confidence': self.confidence
        }


@dataclass
class Decision:
    """Beslissing wat er moet gebeuren na evaluatie."""
    send_digest: bool
    send_alerts: List[AlertCandidate]
    skip_reason: Optional[str] = None

    @property
    def has_alert(self) -> bool:
        """Controleer of er alerts moeten worden verstuurd."""
        return len(self.send_alerts) > 0

    @property
    def action(self) -> Literal['digest', 'alert', 'skip']:
        """Actie die moet worden uitgevoerd."""
        if self.has_alert:
            return 'alert'
        elif self.send_digest:
            return 'digest'
        else:
            return 'skip'


@dataclass
class SystemState:
    """Runtime state van het systeem."""
    last_alert_time: Optional[datetime] = None
    alerts_sent_this_week: int = 0
    week_number: int = 0
    last_digest_time: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None

    def _now_utc(self) -> datetime:
        """B5: tz-aware UTC overal in SystemState voorkomt naive/aware mix bugs."""
        from datetime import timezone
        return datetime.now(timezone.utc)

    def _ensure_utc(self, dt: Optional[datetime]) -> Optional[datetime]:
        """Coerce een veld-datetime naar tz-aware UTC. Naive = UTC aanname
        (state.json wordt door dit proces zelf geschreven met isoformat)."""
        if dt is None:
            return None
        from datetime import timezone
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def should_send_alert(self, cooldown_hours: int, max_per_week: int) -> bool:
        """Controleer of een alert mag worden verstuurd."""
        now = self._now_utc()
        cooldown = self._ensure_utc(self.cooldown_until)

        # Check cooldown
        if cooldown and now < cooldown:
            return False

        # Check weekly cap
        current_week = now.isocalendar()[1]
        if current_week != self.week_number:
            self.week_number = current_week
            self.alerts_sent_this_week = 0

        return self.alerts_sent_this_week < max_per_week

    def record_alert(self, cooldown_hours: int):
        """Registreer dat een alert is verstuurd."""
        now = self._now_utc()
        self.last_alert_time = now
        self.alerts_sent_this_week += 1
        self.cooldown_until = now + timedelta(hours=cooldown_hours)


@dataclass
class RunLog:
    """Log entry voor elke run."""
    timestamp: datetime
    run_type: str  # "scheduled", "manual", "validation"
    scores_today_peak: int
    scores_tomorrow_peak: int
    alert_types_detected: List[str]
    windows_total: int
    windows_alertworthy: int
    decision: str  # "alert", "digest", "skip" — zie Decision.action
    sms_sent: Optional[str] = None
    sms_text_full: Optional[str] = None  # Volledige verzonden tekst (geen 100-char preview)
    llm_used: bool = False
    llm_validation_passed: bool = False
    llm_validation_issues: List[str] = field(default_factory=list)
    buoy_ijg1_height: Optional[float] = None
    buoy_ijg1_period: Optional[float] = None
    buoy_a12_period: Optional[float] = None
    # Fix #4: audit-velden voor orchestration-trail in forecasts_log.jsonl.
    bias_correction_applied: bool = False
    rws_status: str = 'unknown'         # 'ok' | 'partial' | 'failed' | 'unknown'
    openmeteo_status: str = 'unknown'   # 'ok' | 'partial' | 'failed' | 'unknown'
    seasonal_baseline_loaded: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        """Converteer naar dictionary voor JSON logging."""
        return {
            'timestamp': self.timestamp.isoformat(),
            'run_type': self.run_type,
            'scores_today_peak': self.scores_today_peak,
            'scores_tomorrow_peak': self.scores_tomorrow_peak,
            'alert_types_detected': self.alert_types_detected,
            'windows_total': self.windows_total,
            'windows_alertworthy': self.windows_alertworthy,
            'decision': self.decision,
            'sms_sent': self.sms_sent,
            'sms_text_full': self.sms_text_full,
            'llm_used': self.llm_used,
            'llm_validation_passed': self.llm_validation_passed,
            'llm_validation_issues': self.llm_validation_issues,
            'buoy_ijg1_height': self.buoy_ijg1_height,
            'buoy_ijg1_period': self.buoy_ijg1_period,
            'buoy_a12_period': self.buoy_a12_period,
            'bias_correction_applied': self.bias_correction_applied,
            'rws_status': self.rws_status,
            'openmeteo_status': self.openmeteo_status,
            'seasonal_baseline_loaded': self.seasonal_baseline_loaded,
            'error': self.error
        }


@dataclass
class HistoricalSMS:
    """Historische SMS uit validatieset."""
    date: str
    tobias_alert_explicit: bool
    tobias_noordwijk_assessment: str
    tobias_alert_type: Optional[str]
    expected_algorithm_output: Dict  # score range, alert ja/nee