"""
Data models voor het Noordwijk Surf Alert Systeem.
Definieert alle data structuren die door het systeem worden gebruikt.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Literal
from enum import Enum


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

    def get_dominant_peak(self) -> Optional[SpectralPeak]:
        """Retourneer de dominante piek (hoogste amplitude)."""
        if not self.peaks:
            return None
        return max(self.peaks, key=lambda p: p.height_m)

    def get_groundswell_component(self) -> Optional[SpectralPeak]:
        """Retourneer de groundswell piek indien aanwezig."""
        groundswell_peaks = [p for p in self.peaks if p.type == SwellType.GROUND_SWELL]
        return max(groundswell_peaks, key=lambda p: p.height_m) if groundswell_peaks else None

    def get_wind_sea_component(self) -> Optional[SpectralPeak]:
        """Retourneer de wind sea piek indien aanwezig."""
        wind_sea_peaks = [p for p in self.peaks if p.type == SwellType.WIND_SEA]
        return max(wind_sea_peaks, key=lambda p: p.height_m) if wind_sea_peaks else None


@dataclass
class WindState:
    """Wind state voor een specifiek tijdstip."""
    speed_kn: float
    direction_deg: int
    gusts_kn: Optional[float] = None

    @property
    def is_offshore(self) -> bool:
        """Controleer of wind offshore is (75°-135°)."""
        return 75 <= self.direction_deg <= 135

    @property
    def is_side_offshore(self) -> bool:
        """Controleer of wind side-offshore is (135°-225°)."""
        return 135 <= self.direction_deg <= 225

    @property
    def is_onshore(self) -> bool:
        """Controleer of wind onshore is (225°-315°)."""
        return 225 <= self.direction_deg <= 315


@dataclass
class TideState:
    """Tij state voor een specifiek tijdstip."""
    level_m: float              # Hoogte boven NAP
    phase: str                   # "opgaand", "afgaand", "hoogtij", "laagtij"
    next_low: datetime
    next_high: datetime

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


@dataclass
class ScoreBreakdown:
    """Score breakdown voor één uur."""
    timestamp: datetime

    # Component scores (max waarden uit config)
    golf_score: float        # max 40
    wind_score: float        # max 35
    tide_score: float        # max 15
    swell_dir_bonus: float   # max 10

    @property
    def total_score(self) -> float:
        """Totale score 0-100."""
        return round(self.golf_score + self.wind_score + self.tide_score + self.swell_dir_bonus, 1)

    def is_surfable(self) -> bool:
        """Controleer of score hoog genoeg is voor surfen."""
        return self.total_score >= 60


@dataclass
class SurfWindow:
    """Een aaneengesloten periode van goede surfcondities."""
    start: datetime
    end: datetime
    peak_score: int
    median_score: int
    peak_hour: datetime
    triggers: List[AlertType]
    stability: float           # 0.0-1.0, hoe stabiel de score is
    rarity_percentile: float   # 0-100, vs seizoensbaseline
    hourly_scores: List[ScoreBreakdown] = field(default_factory=list)

    @property
    def duration_hours(self) -> float:
        """Duur van het window in uren."""
        return (self.end - self.start).total_seconds() / 3600

    @property
    def is_alertworthy(self) -> bool:
        """Controleer of dit window alert-waardig is."""
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

    def should_send_alert(self, cooldown_hours: int, max_per_week: int) -> bool:
        """Controleer of een alert mag worden verstuurd."""
        now = datetime.now()

        # Check cooldown
        if self.cooldown_until and now < self.cooldown_until:
            return False

        # Check weekly cap
        current_week = now.isocalendar()[1]
        if current_week != self.week_number:
            self.week_number = current_week
            self.alerts_sent_this_week = 0

        return self.alerts_sent_this_week < max_per_week

    def record_alert(self, cooldown_hours: int):
        """Registreer dat een alert is verstuurd."""
        now = datetime.now()
        self.last_alert_time = now
        self.alerts_sent_this_week += 1
        self.cooldown_until = now.replace(hour=now.hour + cooldown_hours)


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
    decision: str  # "send_alert", "send_digest", "skip"
    sms_sent: Optional[str] = None
    llm_used: bool = False
    llm_validation_passed: bool = False
    llm_validation_issues: List[str] = field(default_factory=list)
    buoy_ijg1_height: Optional[float] = None
    buoy_ijg1_period: Optional[float] = None
    buoy_a12_period: Optional[float] = None
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
            'llm_used': self.llm_used,
            'llm_validation_passed': self.llm_validation_passed,
            'llm_validation_issues': self.llm_validation_issues,
            'buoy_ijg1_height': self.buoy_ijg1_height,
            'buoy_ijg1_period': self.buoy_ijg1_period,
            'buoy_a12_period': self.buoy_a12_period,
            'error': self.error
        }


@dataclass
class HistoricalSMS:
    """Historische SMS uit validatieset."""
    date: str
    ref_alert_explicit: bool
    ref_noordwijk_assessment: str
    ref_alert_type: Optional[str]
    expected_algorithm_output: Dict  # score range, alert ja/nee