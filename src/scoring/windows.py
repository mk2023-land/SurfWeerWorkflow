"""
Window analysis module.
Detecteert en analyseert aaneengesloten periodes van goede surfcondities.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional
import statistics

from src.config import SURF_THRESHOLDS
from src.data.models import (
    ScoreBreakdown,
    SurfWindow,
    AlertType
)

logger = logging.getLogger(__name__)


def cluster_consecutive_hours(
    scores: List[ScoreBreakdown],
    min_score: int = None,
    min_golf: float = 0.0,
) -> List[List[ScoreBreakdown]]:
    """
    Cluster aaneengesloten uren met score boven minimum.

    Args:
        scores: Lijst van uurlijkse scores (chronologisch gesorteerd)
        min_score: Minimum score om als surfbaar te beschouwen

    Returns:
        Lijst van clusters, elk cluster is een lijst van opeenvolgende uren
    """
    if not scores:
        return []
    if min_score is None:
        min_score = SURF_THRESHOLDS['surfable']

    clusters = []
    current_cluster = []

    for score in scores:
        # Beide voorwaarden moeten gelden: total-score boven combo-drempel
        # ÉN golf_score boven wave-energy drempel. Dit voorkomt dat een uur
        # met "alleen perfect tij/wind" maar zonder echte golven als
        # surfbaar wordt geclusterd.
        if score.total_score >= min_score and score.golf_score >= min_golf:
            current_cluster.append(score)
        else:
            if current_cluster:
                clusters.append(current_cluster)
                current_cluster = []

    if current_cluster:
        clusters.append(current_cluster)

    return clusters


def calculate_window_stability(scores: List[ScoreBreakdown]) -> float:
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


def calculate_rarity_percentile(score: int, seasonal_baseline: dict) -> float:
    """
    Bereken rarity percentile van een score.

    Args:
        score: De score om te evalueren
        seasonal_baseline: Seizoensbaseline data

    Returns:
        Percentile 0-100
    """
    if not seasonal_baseline:
        return 50.0  # Geen baseline = gemiddeld

    # Bepaal week van jaar
    now = datetime.now()
    week_number = now.isocalendar()[1]

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
    scores: List[ScoreBreakdown],
    triggers: List[AlertType],
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

    # Rarity percentile (gebruik peak score)
    rarity_percentile = calculate_rarity_percentile(peak_score, seasonal_baseline)

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
    hourly_scores: List[ScoreBreakdown],
    triggers_dict: dict = None,
    seasonal_baseline: dict = None
) -> List[SurfWindow]:
    """
    Analyseer alle surf windows in de forecast — zowel shortboard-surfable
    (peak ≥ SURF_THRESHOLDS['surfable']) als longboard-only
    (peak ≥ SURF_THRESHOLDS['longboard'] maar < surfable).

    Een longboard-cluster dat een surfable-cluster volledig bevat wordt
    overgeslagen om dubbeltelling te voorkomen — alleen "echte" longboard-
    windows (die niet upgrade-baar zijn naar surfable) komen erbij.

    Args:
        hourly_scores: Uurlijkse scores
        triggers_dict: Dictionary mapping timestamp → list of AlertType
        seasonal_baseline: Seizoensbaseline data

    Returns:
        Gecombineerde lijst SurfWindow objecten met `kind` ∈ {'surfable', 'longboard'}.
    """
    if triggers_dict is None:
        triggers_dict = {}

    surfable_threshold = SURF_THRESHOLDS['surfable']
    longboard_threshold = SURF_THRESHOLDS['longboard']
    min_golf_surfable = SURF_THRESHOLDS['min_golf_surfable']
    min_golf_longboard = SURF_THRESHOLDS['min_golf_longboard']

    def _build(clusters, kind):
        out = []
        for cluster in clusters:
            cluster_triggers = set()
            for score in cluster:
                if score.timestamp in triggers_dict:
                    cluster_triggers.update(triggers_dict[score.timestamp])
            out.append(create_surf_window(
                scores=cluster,
                triggers=list(cluster_triggers),
                seasonal_baseline=seasonal_baseline,
                kind=kind,
            ))
        return out

    surfable_clusters = cluster_consecutive_hours(
        hourly_scores, min_score=surfable_threshold, min_golf=min_golf_surfable
    )
    surfable_windows = _build(surfable_clusters, 'surfable')

    longboard_clusters = cluster_consecutive_hours(
        hourly_scores, min_score=longboard_threshold, min_golf=min_golf_longboard
    )
    surfable_hours = {s.timestamp for w in surfable_windows for s in w.hourly_scores}

    # Een longboard-cluster wordt alleen toegevoegd als peak NIET surfable is —
    # anders is het al gedekt door de surfable-window.
    longboard_only_windows = []
    for cluster in longboard_clusters:
        peak_score = max(s.total_score for s in cluster)
        if peak_score >= surfable_threshold:
            continue  # dit is een surfable window, al gedekt
        # Check geen overlap met surfable hours (kan voorkomen rond drempel-flanks)
        if any(s.timestamp in surfable_hours for s in cluster):
            continue
        longboard_only_windows.extend(_build([cluster], 'longboard'))

    all_windows = surfable_windows + longboard_only_windows
    logger.info(
        f"Found {len(surfable_windows)} surfable + {len(longboard_only_windows)} longboard-only windows"
    )
    return all_windows


def filter_alertworthy_windows(windows: List[SurfWindow]) -> List[SurfWindow]:
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


def get_best_window(windows: List[SurfWindow]) -> Optional[SurfWindow]:
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