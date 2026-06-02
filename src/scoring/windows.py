"""
Window analysis module.
Detecteert en analyseert aaneengesloten periodes van goede surfcondities.
"""
import logging
import statistics
from datetime import datetime
from typing import Optional

from src.config import SURF_THRESHOLDS
from src.data.models import AlertType, ScoreBreakdown, SurfWindow

logger = logging.getLogger(__name__)


def cluster_consecutive_hours(
    scores: list[ScoreBreakdown],
    min_score: int = None,
    min_golf: float = 0.0,
    max_dip_hours: int = 1,
    max_dip_depth: float = 5.0,
) -> list[list[ScoreBreakdown]]:
    """
    Cluster aaneengesloten uren met score boven minimum, met tolerantie
    voor korte dips (max_dip_hours uren tot max_dip_depth onder threshold)
    als zowel het uur ervoor als erna boven de threshold zit.

    Voorbeeld: scores [62, 63, 58, 61, 62] met threshold 60 →
    de 58 is een 1-uurs dip van 2pt onder threshold, met buren 63 en 61 →
    één cluster van 5 uur i.p.v. 2 clusters van 2 uur.

    Args:
        scores: Lijst van uurlijkse scores (chronologisch gesorteerd)
        min_score: Minimum score om als surfbaar te beschouwen
        min_golf: Minimum golf_score (anti-wind-only-cluster)
        max_dip_hours: Max aantal opeenvolgende uren onder drempel
        max_dip_depth: Max diepte onder min_score (in pt) die nog dip-tolerantie krijgt

    Returns:
        Lijst van clusters, elk cluster is een lijst van opeenvolgende uren
    """
    if not scores:
        return []
    if min_score is None:
        min_score = SURF_THRESHOLDS['surfable']

    def _qualifies(s):
        return s.total_score >= min_score and s.golf_score >= min_golf

    def _within_dip(s):
        # Een uur kan een dip zijn als:
        # - golf_score nog redelijk is (boven min_golf) en
        # - total_score niet meer dan max_dip_depth onder threshold zit
        return (
            s.golf_score >= min_golf
            and (min_score - s.total_score) <= max_dip_depth
            and s.total_score < min_score
        )

    clusters = []
    current_cluster = []
    pending_dip = []  # Lijst van dip-uren in afwachting van een "qualifies" terug

    for _i, score in enumerate(scores):
        if _qualifies(score):
            # Eerst eventuele pending dip absorberen (we hebben nu een 'na'-buur).
            if current_cluster and pending_dip and len(pending_dip) <= max_dip_hours:
                current_cluster.extend(pending_dip)
            elif pending_dip and not current_cluster:
                # Dip zonder voorgaande cluster — gewoon verwerpen
                pass
            pending_dip = []
            current_cluster.append(score)
        elif current_cluster and _within_dip(score) and len(pending_dip) < max_dip_hours:
            # Mogelijke dip; in afwachting van bevestiging via volgende "qualifies"-uur
            pending_dip.append(score)
        else:
            # Echte break: sluit current cluster, vergeet pending dip
            if current_cluster:
                clusters.append(current_cluster)
                current_cluster = []
            pending_dip = []

    if current_cluster:
        clusters.append(current_cluster)

    return clusters


def calculate_window_stability(scores: list[ScoreBreakdown]) -> float:
    """
    Bereken stabiliteit van een window (0.0-1.0).

    Stabiliteit = 1.0 als alle scores gelijk zijn,
    0.0 als maximale variatie.

    Args:
        scores: Scores binnen het window

    Returns:
        Stabiliteit score 0.0-1.0
    """
    if len(scores) <= 1:
        return 1.0

    total_scores = [s.total_score for s in scores]
    mean_score = statistics.mean(total_scores)

    if mean_score == 0:
        return 1.0

    # Bereken gemiddelde absolute afwijking van mean
    mad = statistics.mean(abs(s - mean_score) for s in total_scores)

    # Normaliseer: mad=0 → stability=1.0, mad=mean → stability=0.5
    stability = max(0.0, 1.0 - (mad / mean_score))

    return stability


def calculate_rarity_percentile(
    score: int,
    seasonal_baseline: dict,
    when: Optional[datetime] = None,
) -> float:
    """
    Bereken rarity percentile van een score.

    Args:
        score: De score om te evalueren
        seasonal_baseline: Seizoensbaseline data
        when: Tijdstip waarop deze score geldt — bepaalt welke week-baseline
            wordt gebruikt. Default ``None`` → ``datetime.now()`` (backwards-
            compat). Voor forecast-uren ≥1 dag vooruit MOET dit het forecast-
            tijdstip zijn, anders kruist de jaar-grens (week 52 → week 1)
            naar de verkeerde baseline-bucket.

    Returns:
        Percentile 0-100
    """
    if not seasonal_baseline:
        return 50.0  # Geen baseline = gemiddeld

    # Bepaal week van jaar — voor het tijdstip waar de score op slaat,
    # niet het wall-clock-now. Dit voorkomt baseline-mismatch rond
    # jaargrens en bij multi-dag forecast-windows.
    reference = when if when is not None else datetime.now()
    week_number = reference.isocalendar()[1]

    # Haal baseline voor deze week
    week_key = f"week_{week_number}"
    week_baseline = seasonal_baseline.get(week_key)

    if not week_baseline:
        return 50.0

    # Interpoleer percentile op basis van score
    p50 = week_baseline.get('p50', 50)
    p70 = week_baseline.get('p70', 70)
    p90 = week_baseline.get('p90', 90)

    if score <= p50:
        # Lineair interpoleren 0-50
        percentile = (score / p50) * 50
    elif score <= p70:
        # Lineair interpoleren 50-70
        percentile = 50 + ((score - p50) / (p70 - p50)) * 20
    elif score <= p90:
        # Lineair interpoleren 70-90
        percentile = 70 + ((score - p70) / (p90 - p70)) * 20
    else:
        # Boven 90e percentile
        percentile = 90 + ((score - p90) / (100 - p90)) * 10

    return min(100.0, max(0.0, percentile))


def create_surf_window(
    scores: list[ScoreBreakdown],
    triggers: list[AlertType],
    seasonal_baseline: dict = None,
    kind: str = 'surfable',
) -> SurfWindow:
    """
    Maak een SurfWindow object van een cluster van scores.

    Args:
        scores: Scores binnen het window
        triggers: Alert types die dit window triggerden
        seasonal_baseline: Seizoensbaseline data
        kind: 'surfable' (shortboard-rideable) of 'longboard'

    Returns:
        SurfWindow object
    """
    start = scores[0].timestamp
    end = scores[-1].timestamp

    # Peak score en hour
    peak_score = max(s.total_score for s in scores)
    peak_hour = max(scores, key=lambda s: s.total_score).timestamp

    # Median score
    median_score = int(statistics.median(s.total_score for s in scores))

    # Stabiliteit
    stability = calculate_window_stability(scores)

    # Rarity percentile (gebruik peak score). Geef het window-start-tijdstip
    # mee zodat de juiste week-baseline geraadpleegd wordt voor forecast-
    # uren die niet op vandaag vallen (jaargrens 52→1 etc.).
    rarity_percentile = calculate_rarity_percentile(
        peak_score, seasonal_baseline, when=scores[0].timestamp
    )

    return SurfWindow(
        start=start,
        end=end,
        peak_score=int(peak_score),
        median_score=median_score,
        peak_hour=peak_hour,
        triggers=triggers,
        stability=stability,
        rarity_percentile=rarity_percentile,
        hourly_scores=scores,
        kind=kind,
    )


def analyze_windows(
    hourly_scores: list[ScoreBreakdown],
    triggers_dict: dict = None,
    seasonal_baseline: dict = None
) -> list[SurfWindow]:
    """
    Analyseer alle surf windows in de forecast.

    Eén venster = een aaneengesloten span van RIJDBARE uren
    (score ≥ SURF_THRESHOLDS['longboard']). De `kind` van het venster volgt
    uit de piek binnen die span: 'surfable' als de piek ≥ surfable-drempel
    (shortboard-rideable), anders 'longboard' (alleen longboard/fish).

    Waarom de span op de longboard-drempel ligt en niet op de surfable-drempel:
    een dag als "1,5-2m WZW, hele dag longboard-baar met een paar uur dat de
    60-drempel haalt" hoort als ÉÉN venster ("6-9u, top vroeg") naar buiten te
    komen, niet als losse 1-uurs surfable-pieken. De oude dual-pass logica
    bouwde aparte surfable-clusters (≥60) én longboard-clusters (≥42) en gooide
    vervolgens élke longboard-cluster weg die toevallig één surfable-uur bevatte
    — waardoor een 10-uurs longboard-span verdween en alleen het losse 60+-uur
    overbleef. Resultaat: cryptische enkel-tijdstippen i.p.v. tijdsvensters.
    Door de span op de longboard-drempel te leggen en `kind` op de piek te
    bepalen verdwijnt die dubbeltelling én blijft het venster intact.

    Args:
        hourly_scores: Uurlijkse scores
        triggers_dict: Dictionary mapping timestamp → list of AlertType
        seasonal_baseline: Seizoensbaseline data

    Returns:
        Lijst SurfWindow objecten met `kind` ∈ {'surfable', 'longboard'}.
    """
    if triggers_dict is None:
        triggers_dict = {}

    surfable_threshold = SURF_THRESHOLDS['surfable']
    longboard_threshold = SURF_THRESHOLDS['longboard']
    min_golf_longboard = SURF_THRESHOLDS['min_golf_longboard']

    # Eén pass op de longboard-drempel levert de rijdbare spans. Een surfable-uur
    # (≥60, golf ≥ min_golf_surfable=15) zit per definitie óók boven de
    # longboard-drempel (≥42, golf ≥ min_golf_longboard=5), dus geen enkel
    # surfbaar uur gaat verloren door alleen deze pass te draaien.
    clusters = cluster_consecutive_hours(
        hourly_scores, min_score=longboard_threshold, min_golf=min_golf_longboard
    )

    windows = []
    for cluster in clusters:
        cluster_triggers = set()
        for score in cluster:
            if score.timestamp in triggers_dict:
                cluster_triggers.update(triggers_dict[score.timestamp])
        peak_score = max(s.total_score for s in cluster)
        kind = 'surfable' if peak_score >= surfable_threshold else 'longboard'
        windows.append(create_surf_window(
            scores=cluster,
            triggers=list(cluster_triggers),
            seasonal_baseline=seasonal_baseline,
            kind=kind,
        ))

    n_surfable = sum(1 for w in windows if w.kind == 'surfable')
    logger.info(
        f"Found {len(windows)} windows ({n_surfable} surfable, "
        f"{len(windows) - n_surfable} longboard-only)"
    )
    return windows


def filter_alertworthy_windows(windows: list[SurfWindow]) -> list[SurfWindow]:
    """
    Filter windows die alert-waardig zijn.

    Criteria:
    - Peak score >= 75
    - Minimaal 1 trigger
    - Stabiliteit >= 0.6
    - Rarity >= 70e percentile
    - Duur >= 1 uur

    Args:
        windows: Alle surf windows

    Returns:
        Lijst van alert-waardige windows
    """
    alertworthy = [w for w in windows if w.is_alertworthy]

    logger.info(f"Found {len(alertworthy)} alert-worthy windows out of {len(windows)} total windows")
    return alertworthy


def get_best_window(windows: list[SurfWindow]) -> Optional[SurfWindow]:
    """
    Kies het beste window uit een lijst.

    Criteria (in volgorde van prioriteit):
    1. Hoogste peak score
    2. Langste duur
    3. Hoogste stabiliteit

    Args:
        windows: Lijst van windows

    Returns:
        Beste window of None
    """
    if not windows:
        return None

    # Sorteer op criteria
    def sort_key(window):
        return (
            window.peak_score,      # Hoogste score eerst
            window.duration_hours,   # Langste duur tweede
            window.stability         # Hoogste stabiliteit derde
        )

    return max(windows, key=sort_key)
