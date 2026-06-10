"""
Per-uur scoring orkestrator.

Compositie-laag bovenop wave_modifiers, wind, tide, swell_direction en
context. De public API (score_hour, score_hour_series, score_golf_component,
compute_wind_spread_per_hour) blijft hier gehost zodat callers stabiel zijn.

Andere functies worden re-exported voor backwards-compat.
"""
import logging
from typing import Optional

from src.config import (
    NOORDWIJK,
    SCORING_WEIGHTS,
    SURF_MINIMUMS,
    WIND_FACE_PENALTY,
)
from src.data.models import HourState, ScoreBreakdown, WaveSpectrum
from src.scoring.context import (
    convective_warning,
    dominant_period_partition_based,
    period_factor,
    recommend_boards,
    storm_surge_warning,
    visibility_concern,
)
from src.scoring.daylight import is_daylight_noordwijk
from src.scoring.deconstruct import (
    decompose_spectrum,
    has_groundswell_through_windsea,
    is_clean_swell,
)
from src.scoring.swell_direction import (
    pier_transmission_factor,
    score_swell_direction_bonus,
)
from src.scoring.tide import (
    hours_until as _hours_until,
)
from src.scoring.tide import (
    score_tide_component,
    tide_flank_bonus,
    tide_velocity_mh,
)
from src.scoring.wave_modifiers import (
    _combine_golf_modifiers,
    iribarren_factor,
    mixed_sea_penalty,
    partition_energy_components,
    wave_age,
    wave_age_factor,
    wave_energy_factor,
    wave_energy_flux,
    wave_quality_spread_factor,
)
from src.scoring.wind import (
    _wind_direction_cosine,
    angular_spread_deg,
    atmospheric_stability_factor,
    compute_wind_spread_per_hour,
    diurnal_wind_decay_kn,
    pressure_gradient_factor,
    score_wind_component,
    wave_face_quality,
    wind_gust_penalty,
    wind_spread_confidence,
    wind_trend_factor,
)

# Backwards-compat alias (oude callers gebruiken _period_factor / _dominant_period_partition_based)
_period_factor = period_factor
_dominant_period_partition_based = dominant_period_partition_based

logger = logging.getLogger(__name__)


def golf_height_curve(eff_height: float) -> float:
    """Effectieve golfhoogte (m) → ruwe hoogte-score (vóór period_factor en de
    modifiers). Eén bron-van-waarheid, gedeeld door `score_golf_component` én
    de leer-loop (`scripts/calibrate.py`), zodat een re-score onder geleerde
    parameters exact dezelfde curve gebruikt als de live scoring (geen drift).
    Zie commentaar in score_golf_component voor de inimini-band-rationale."""
    if eff_height < 0.35:
        return 0.0
    elif eff_height < 0.5:
        return (eff_height - 0.35) * 60   # 0,35→0, 0,5→9
    elif eff_height < 1.0:
        return 9 + (eff_height - 0.5) * 22  # 0,5→9, 1,0→20
    elif eff_height < 2.0:
        return 20 + (eff_height - 1.0) * 20
    else:
        return 40.0


def score_golf_component(
    wave_spectrum: WaveSpectrum,
    cos_offshore: Optional[float] = None,
    wind_speed_kn: Optional[float] = None,
) -> float:
    """
    Bereken golf score (max SCORING_WEIGHTS['golf_max']).

    Partition-aware: effective_height_m combineert swell + 0.65×wind-zee
    kwadratisch. Periode-factor uit zwaarste partitie. T4 groundswell-door-
    windsea bonus (+8, +4 extra bij Tp≥11s) en clean-swell bonus (+1).

    Bij aflandige, gematigde wind telt de windzee vollediger mee (grooming),
    zie partition_energy_components.
    """
    partitions = partition_energy_components(wave_spectrum, cos_offshore, wind_speed_kn)
    eff_height = partitions['effective_height_m']

    # referentie-pariteit (kleine schone longboardgolven): voorheen kreeg ALLES
    # onder 0,5m nul punten en net daarboven bijna niets, waardoor een schoon
    # 0,5m-golfje bij hoogwater (referentie: "inimini maar clean longboard") als
    # "flat" uitkwam. De inimini-band 0,35-0,5m geeft nu een niet-nul basis
    # zodat zo'n golf — NA period_factor en de face/grooming-modifiers — de
    # longboard-cluster (golf>=5) kan halen. Cleanliness blijft gegated: korte
    # periode (lage period_factor) en aanlandige/harde wind (lage face_quality)
    # drukken een vuile kleine golf alsnog onder de drempel → blijft flat.
    height_score = golf_height_curve(eff_height)

    T = partitions['dominant_period_s']
    height_score *= period_factor(T)

    decomp = decompose_spectrum(wave_spectrum)
    if decomp.get('ground_swell') and decomp.get('wind_sea'):
        gs = decomp['ground_swell']
        ws = decomp['wind_sea']
        gs_substantial = gs.height_m >= 0.7 and gs.period_s >= 9.0
        gs_dominant = gs.height_m >= 0.6 * ws.height_m
        if gs_substantial and gs_dominant:
            t4_bonus = 8.0
            if gs.period_s >= 11.0:
                t4_bonus += 4.0
            height_score += t4_bonus
        elif has_groundswell_through_windsea(wave_spectrum):
            height_score += 1

    if is_clean_swell(wave_spectrum):
        height_score += 1

    return min(SCORING_WEIGHTS['golf_max'], height_score)


def score_hour(state: HourState, context: Optional[dict] = None) -> ScoreBreakdown:
    """
    Bereken totale score voor één uur.

    Daglicht-filter eerst (geen night-pieken). Daarna fysieke minimum-gate
    (Hs<0.30m of Tp<4s → score 0, ongeacht omgeving). Daarna golf-component
    met weighted-sum van 6 modifiers (anti-collapse), additieve wind- en
    tij-componenten met eigen modifiers, en swell-richting bonus.
    """
    if not is_daylight_noordwijk(state.timestamp):
        return ScoreBreakdown(
            timestamp=state.timestamp,
            golf_score=0.0,
            wind_score=0.0,
            tide_score=0.0,
            swell_dir_bonus=0.0,
        )

    # Fysieke minimum-gate: onder Hs/Tp-floor is geen bord surfbaar.
    Hs = state.wave_spectrum.significant_height_total
    Tp = dominant_period_partition_based(state.wave_spectrum)
    if Hs < SURF_MINIMUMS['min_hs_m'] or Tp < SURF_MINIMUMS['min_period_s']:
        return ScoreBreakdown(
            timestamp=state.timestamp,
            golf_score=0.0,
            wind_score=0.0,
            tide_score=0.0,
            swell_dir_bonus=0.0,
        )

    # Offshore-context vóór de golf-component: aflandige grooming laat de
    # windzee vollediger meetellen in de effectieve hoogte (referentie-forecaster' clean windlijn).
    cos_offshore = _wind_direction_cosine(state.wind.direction_deg, NOORDWIJK.beach_normal_deg)
    golf_score = score_golf_component(state.wave_spectrum, cos_offshore, state.wind.speed_kn)

    # Bereken alle 6 golf-modifiers afzonderlijk en combineer via weighted-sum.
    we_factor = wave_energy_factor(Hs, Tp)
    age_factor = wave_age_factor(Tp, state.wind.speed_kn)
    iri_factor = iribarren_factor(Hs, Tp, tide_normalized=state.tide.normalized_level)
    face_q = wave_face_quality(state.wind.speed_kn, cos_offshore)

    trend = 1.0
    if context:
        trend = wind_trend_factor(
            context.get('wind_history_kn') or [],
            context.get('wave_history_m') or [],
        )

    conf_mult = 1.0
    if context:
        spread = context.get('wind_spread') or {}
        conf_mult = wind_spread_confidence(
            spread.get('speed_std_kn'),
            spread.get('direction_spread_deg'),
        )

    modifier_factors = {
        'wave_energy': we_factor,
        'wave_age': age_factor,
        'iribarren': iri_factor,
        'face_quality': face_q,
        'wind_trend': trend,
        'wind_spread': conf_mult,
    }
    combined_factor = _combine_golf_modifiers(modifier_factors)
    logger.info(
        "score_hour golf_modifiers combined=%.3f | we=%.3f age=%.3f iri=%.3f "
        "face=%.3f trend=%.3f spread=%.3f",
        combined_factor, we_factor, age_factor, iri_factor, face_q, trend, conf_mult,
    )
    golf_score *= combined_factor

    # Wind-face penalty als EIGEN multiplier (referentie-pariteit): harde onshore
    # wind vernielt de face → drukt de golf-score los van hoogte, zodat een
    # uitgeblazen grote golf niet alleen op hoogte 'surfable' wordt. face_q
    # (0,4-1,0) komt uit wave_face_quality; sterkte is een fit-seed
    # (WIND_FACE_PENALTY), niet hand-getuned op één dag.
    face_pen = 1.0 - WIND_FACE_PENALTY['strength'] * (1.0 - face_q)
    face_pen = max(WIND_FACE_PENALTY['min_factor'], face_pen)
    golf_score *= face_pen

    is_mixed_sea, mixed_pen = mixed_sea_penalty(
        state.wave_spectrum,
        cos_offshore=cos_offshore,
        wind_speed_kn=state.wind.speed_kn,
    )
    if is_mixed_sea:
        golf_score = max(0.0, golf_score + mixed_pen)

    # Diurnal sea-breeze decay (bij lage bewolking + 2u rond sunset).
    cloud_cover_pct = context.get('cloud_cover_pct') if context else None
    effective_wind_kn = diurnal_wind_decay_kn(
        state.timestamp, state.wind.speed_kn, cloud_cover_pct
    )

    wind_score = score_wind_component(effective_wind_kn, state.wind.direction_deg)

    # Wind-gust ratio penalty (gebruikt raw wind, niet decay-versie).
    gust_pen = wind_gust_penalty(state.wind.speed_kn, state.wind.gusts_kn)
    wind_score = max(0.0, wind_score + gust_pen)

    # Atmospheric stability (ΔT = T_air - SST) soft multiplier.
    stab_factor = atmospheric_stability_factor(
        state.air_temperature_c, state.sea_surface_temperature_c
    )
    wind_score *= stab_factor

    # Wave quality op basis van directionele spreiding (boei-observatie).
    if state.wave_spectrum.directional_spread_deg is not None:
        spread_factor = wave_quality_spread_factor(
            state.wave_spectrum.directional_spread_deg
        )
        golf_score *= spread_factor

    # Pressure gradient (synoptische storing).
    if context:
        pres_factor = pressure_gradient_factor(
            context.get('pressure_history_hpa') or []
        )
        wind_score *= pres_factor

    # Tij component — partition-based Tp consistent met de min-period gate.
    dominant_period_s = Tp
    hours_to_high = _hours_until(state.timestamp, state.tide.next_high)
    tidal_current = state.tide.tidal_current_intensity(state.timestamp)
    tide_score = score_tide_component(
        state.tide.normalized_level,
        state.tide.phase,
        dominant_period_s=dominant_period_s,
        tide_range_m=state.tide.daily_range_m,
        hours_to_next_high=hours_to_high,
        tidal_current_intensity=tidal_current,
    )

    # Swell richting bonus (periode-afhankelijke pier-refractie).
    decomposition = decompose_spectrum(state.wave_spectrum)
    if decomposition['ground_swell']:
        swell_dir_deg = decomposition['ground_swell'].direction_deg
        swell_period_s = decomposition['ground_swell'].period_s
    elif decomposition['wind_swell']:
        swell_dir_deg = decomposition['wind_swell'].direction_deg
        swell_period_s = decomposition['wind_swell'].period_s
    elif decomposition['wind_sea']:
        swell_dir_deg = decomposition['wind_sea'].direction_deg
        swell_period_s = decomposition['wind_sea'].period_s
    else:
        swell_dir_deg = state.wave_spectrum.mean_direction
        swell_period_s = state.wave_spectrum.mean_period or 7.0

    swell_dir_bonus = score_swell_direction_bonus(swell_dir_deg, period_s=swell_period_s)

    return ScoreBreakdown(
        timestamp=state.timestamp,
        golf_score=golf_score,
        wind_score=wind_score,
        tide_score=tide_score,
        swell_dir_bonus=swell_dir_bonus,
        confidence=conf_mult,
    )


def score_hour_series(
    states: list,
    pressure_series: Optional[list] = None,
    cloud_cover_series: Optional[list] = None,
    wind_spread_series: Optional[list] = None,
) -> list:
    """
    Score een tijdreeks van HourStates met wind-trend + druk-gradient context.

    Bouwt rolling windows voor wind-trend (3u) en druk-gradient (4u), en
    geeft per uur cloud-cover + multi-model spread mee als beschikbaar.
    """
    scores = []
    have_pressure = pressure_series is not None and len(pressure_series) == len(states)
    have_cloud = cloud_cover_series is not None and len(cloud_cover_series) == len(states)
    have_spread = wind_spread_series is not None and len(wind_spread_series) == len(states)

    for i, state in enumerate(states):
        hist_start = max(0, i - 2)
        hist_states = states[hist_start:i + 1]
        while len(hist_states) < 3:
            hist_states = [hist_states[0]] + hist_states
        wind_hist = [s.wind.speed_kn for s in hist_states]
        wave_hist = [s.wave_spectrum.significant_height_total for s in hist_states]

        ctx = {'wind_history_kn': wind_hist, 'wave_history_m': wave_hist}
        if have_pressure:
            p_start = max(0, i - 3)
            p_hist = list(pressure_series[p_start:i + 1])
            while len(p_hist) < 4:
                p_hist = [p_hist[0]] + p_hist
            ctx['pressure_history_hpa'] = p_hist
        if have_cloud:
            ctx['cloud_cover_pct'] = cloud_cover_series[i]
        if have_spread:
            ctx['wind_spread'] = wind_spread_series[i]

        scores.append(score_hour(state, context=ctx))
    return scores


__all__ = [
    # Public API
    'score_hour',
    'score_hour_series',
    'score_golf_component',
    'golf_height_curve',
    'score_wind_component',
    'score_tide_component',
    'score_swell_direction_bonus',
    'compute_wind_spread_per_hour',
    # Modifiers (re-exported for backwards-compat)
    '_combine_golf_modifiers',
    'wave_energy_flux',
    'wave_energy_factor',
    'wave_age',
    'wave_age_factor',
    'iribarren_factor',
    'mixed_sea_penalty',
    'wave_quality_spread_factor',
    'partition_energy_components',
    'wind_spread_confidence',
    'angular_spread_deg',
    'diurnal_wind_decay_kn',
    'wind_gust_penalty',
    'pressure_gradient_factor',
    '_wind_direction_cosine',
    'wave_face_quality',
    'wind_trend_factor',
    'atmospheric_stability_factor',
    # Tide
    'tide_flank_bonus',
    'tide_velocity_mh',
    # Pier
    'pier_transmission_factor',
    # Context
    'period_factor',
    '_period_factor',
    'dominant_period_partition_based',
    '_dominant_period_partition_based',
    'recommend_boards',
    'convective_warning',
    'visibility_concern',
    'storm_surge_warning',
]
