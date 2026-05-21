"""
Wind-gerelateerde modifiers, factoren en scoring.

Direction-cosine, face quality, gust penalty, trend, atmospheric stability,
diurnal sea-breeze decay, multi-model spread + score_wind_component.
"""
import math
from datetime import datetime
from typing import Optional

from src.config import (
    DIURNAL_WIND_DECAY,
    NOORDWIJK,
    SCORING_WEIGHTS,
    WIND_SPREAD_THRESHOLDS,
)


def _wind_direction_cosine(wind_dir_deg: int, beach_normal_deg: int) -> float:
    """
    Cosinus van de hoek tussen wind-richting en pure offshore-richting.

    +1.0 = pure offshore, 0.0 = pure cross-shore, −1.0 = pure onshore.
    """
    offshore_dir = (beach_normal_deg + 180) % 360
    delta_raw = (wind_dir_deg - offshore_dir) % 360
    delta = delta_raw if delta_raw <= 180 else 360 - delta_raw
    return math.cos(math.radians(delta))


def wave_face_quality(wind_speed_kn: float, cos_offshore: float) -> float:
    """Multiplier (0.4-1.0) op effectiviteit op basis van wave-face wind-impact."""
    if wind_speed_kn < 3:
        return 1.0
    onshore = max(0.0, -cos_offshore)
    onshore_kn = onshore * wind_speed_kn
    penalty = min(0.60, 0.033 * onshore_kn)
    return 1.0 - penalty


def wind_gust_penalty(wind_speed_kn: float, wind_gust_kn: Optional[float]) -> float:
    """Penalty op wind_score voor vlagerige condities (gust/sustained > 1.3)."""
    if not wind_gust_kn or wind_speed_kn < 4:
        return 0.0
    ratio = wind_gust_kn / wind_speed_kn
    if ratio < 1.3:
        return 0.0
    if ratio < 1.5:
        return -1.0 * ((ratio - 1.3) / 0.2) * 2.0
    if ratio < 2.0:
        return -2.0 - ((ratio - 1.5) / 0.5) * 3.0
    return -5.0


def wind_trend_factor(wind_history_kn: list, wave_history_m: list) -> float:
    """
    Bonus/penalty op wind-trend in afgelopen 2u.

    Wind weg + wave hoog = clean opening (Tobias-sweet spot, +15%).
    Wind sterk omhoog = jonge wind-zee, chop (-15%).
    """
    if len(wind_history_kn) < 3 or len(wave_history_m) < 3:
        return 1.0
    wind_delta = wind_history_kn[-1] - wind_history_kn[0]
    wave_now = wave_history_m[-1]
    wave_max_recent = max(wave_history_m)
    wave_holding = wave_now >= 0.85 * wave_max_recent
    if wind_delta <= -4.0 and wave_holding:
        magnitude = min(1.0, abs(wind_delta) / 8.0)
        return 1.0 + 0.15 * magnitude
    if wind_delta >= 4.0:
        magnitude = min(1.0, wind_delta / 8.0)
        return 1.0 - 0.15 * magnitude
    return 1.0


def atmospheric_stability_factor(
    air_temp_c: Optional[float],
    sst_c: Optional[float],
) -> float:
    """Soft multiplier op wind-score op basis van ΔT = T_air - SST."""
    if air_temp_c is None or sst_c is None:
        return 1.0
    delta = air_temp_c - sst_c
    if delta > 5.0:
        return 1.05
    if delta > 2.0:
        return 1.02
    if delta >= -2.0:
        return 1.00
    if delta >= -5.0:
        return 0.97
    return 0.93


def pressure_gradient_factor(pressure_history_hpa: list) -> float:
    """
    Synoptische storing detector via OLS slope op 4-uurs druk-venster.

    |dp/dt| > 1.5 hPa/uur = front passing → penalty op wind_score.
    """
    if not pressure_history_hpa or len(pressure_history_hpa) < 4:
        return 1.0
    ts = list(range(len(pressure_history_hpa)))
    n = len(ts)
    mean_t = sum(ts) / n
    mean_p = sum(pressure_history_hpa) / n
    denom = sum((t - mean_t) ** 2 for t in ts)
    if denom == 0:
        return 1.0
    dp_dt = sum(
        (t - mean_t) * (p - mean_p)
        for t, p in zip(ts, pressure_history_hpa)
    ) / denom
    abs_grad = abs(dp_dt)
    if abs_grad < 1.5:
        return 1.0
    factor = 1.0 - min(0.15, (abs_grad - 1.5) * (0.15 / 2.5))
    return factor


def diurnal_wind_decay_kn(
    timestamp: datetime,
    wind_speed_kn: float,
    cloud_cover_pct: Optional[float],
) -> float:
    """
    Diurnal sea-breeze decay rond zonsondergang.

    Bij lage bewolking (<50%) en 2u rond sunset valt sea-breeze weg.
    """
    if cloud_cover_pct is None or cloud_cover_pct >= DIURNAL_WIND_DECAY['max_cloud_cover_pct']:
        return wind_speed_kn

    from src.scoring.daylight import _sunrise_sunset_utc_hours
    from src.util import to_utc

    dt_utc = to_utc(timestamp)
    _, sunset_utc_h = _sunrise_sunset_utc_hours(dt_utc.date())
    hour_utc = dt_utc.hour + dt_utc.minute / 60.0

    start_h = sunset_utc_h - DIURNAL_WIND_DECAY['hours_before_sunset']
    end_h = sunset_utc_h + DIURNAL_WIND_DECAY['hours_after_sunset']

    if not (start_h <= hour_utc <= end_h):
        return wind_speed_kn

    if hour_utc < sunset_utc_h:
        ramp = (hour_utc - start_h) / DIURNAL_WIND_DECAY['hours_before_sunset']
    else:
        ramp = 1.0
    reduction = ramp * DIURNAL_WIND_DECAY['speed_reduction_kn']
    return max(0.0, wind_speed_kn - reduction)


def angular_spread_deg(directions_deg: list) -> float:
    """Bereken circular std-dev tussen richting-waarden (graden, 0-180)."""
    if not directions_deg or len(directions_deg) < 2:
        return 0.0
    rads = [math.radians(d % 360) for d in directions_deg]
    sin_sum = sum(math.sin(r) for r in rads) / len(rads)
    cos_sum = sum(math.cos(r) for r in rads) / len(rads)
    R = math.sqrt(sin_sum ** 2 + cos_sum ** 2)
    if R <= 0:
        return 180.0
    R = min(R, 0.999999)
    circ_std_rad = math.sqrt(-2.0 * math.log(R))
    return math.degrees(circ_std_rad)


def wind_spread_confidence(
    speed_std_kn: Optional[float],
    direction_spread_deg: Optional[float],
) -> float:
    """
    Multiplier op golf_score op basis van inter-model spread (multi-model).

    Spread > 5 kn of > 25° → start penalty (1.0 → 0.85 lineair).
    """
    if speed_std_kn is None and direction_spread_deg is None:
        return 1.0

    s_warn = WIND_SPREAD_THRESHOLDS['speed_kn_warning']
    s_max = WIND_SPREAD_THRESHOLDS['speed_kn_max']
    d_warn = WIND_SPREAD_THRESHOLDS['direction_deg_warning']
    d_max = WIND_SPREAD_THRESHOLDS['direction_deg_max']
    min_factor = WIND_SPREAD_THRESHOLDS['min_factor']

    speed_severity = 0.0
    if speed_std_kn is not None and speed_std_kn > s_warn:
        speed_severity = min(1.0, (speed_std_kn - s_warn) / (s_max - s_warn))

    dir_severity = 0.0
    if direction_spread_deg is not None and direction_spread_deg > d_warn:
        dir_severity = min(1.0, (direction_spread_deg - d_warn) / (d_max - d_warn))

    severity = max(speed_severity, dir_severity)
    if severity <= 0:
        return 1.0
    return 1.0 - severity * (1.0 - min_factor)


def compute_wind_spread_per_hour(model_forecasts: dict) -> list:
    """
    Bereken per uur de spread tussen multi-model wind-forecast.

    Args:
        model_forecasts: dict van model-naam → lijst hourly dicts met
            'timestamp', 'wind_speed', 'wind_direction'.

    Returns: lijst dicts {timestamp, speed_std_kn, speed_max_min_kn,
        direction_spread_deg, n_models}.
    """
    if not model_forecasts:
        return []
    model_names = list(model_forecasts.keys())
    if not model_names:
        return []
    base = model_forecasts[model_names[0]]
    n_hours = len(base)

    out = []
    for i in range(n_hours):
        speeds = []
        directions = []
        for name in model_names:
            ser = model_forecasts.get(name) or []
            if i >= len(ser):
                continue
            row = ser[i]
            sp = row.get('wind_speed')
            di = row.get('wind_direction')
            if sp is not None:
                speeds.append(float(sp))
            if di is not None:
                directions.append(float(di))

        if len(speeds) >= 2:
            mean_sp = sum(speeds) / len(speeds)
            speed_std = math.sqrt(sum((x - mean_sp) ** 2 for x in speeds) / len(speeds))
            speed_range = max(speeds) - min(speeds)
        else:
            speed_std = 0.0
            speed_range = 0.0

        dir_spread = angular_spread_deg(directions) if len(directions) >= 2 else 0.0

        out.append({
            'timestamp': base[i]['timestamp'],
            'speed_std_kn': speed_std,
            'speed_max_min_kn': speed_range,
            'direction_spread_deg': dir_spread,
            'n_models': len(speeds),
        })
    return out


def score_wind_component(wind_speed_kn: float, wind_direction_deg: int) -> float:
    """
    Bereken wind score (max SCORING_WEIGHTS['wind_max'], default 32).

    Speed-score in segmenten (25 → 17 → 8 → 0), additief met direction-bonus
    ±7 via cosinus.
    """
    if wind_speed_kn <= 8:
        speed_score = 25.0
    elif wind_speed_kn <= 15:
        speed_score = 25.0 - (wind_speed_kn - 8) * (8.0 / 7.0)
    elif wind_speed_kn <= 22:
        speed_score = 17.0 - (wind_speed_kn - 15) * (9.0 / 7.0)
    elif wind_speed_kn <= 30:
        speed_score = max(0.0, 8.0 - (wind_speed_kn - 22) * (8.0 / 8.0))
    else:
        speed_score = 0.0

    cos_term = _wind_direction_cosine(wind_direction_deg, NOORDWIJK.beach_normal_deg)
    direction_bonus = 7.0 * cos_term

    return max(0.0, min(SCORING_WEIGHTS['wind_max'], speed_score + direction_bonus))
