"""
RWS-boei real-time bias-correctie + bias-logging voor lange-termijn learning.

Sprint 3 fix #14 + #16 uit research_master_improvement_plan.md.

Wetenschappelijk gefundeerd: peer-reviewed Dutch-North-Sea-paper toonde
~22% RMSE-reductie via dit type post-processing op spot-niveau. Werkwijze:

1. Pak laatste 3-6 uur live IJG1 (of vergelijkbare) boei-observaties met
   timestamp.
2. Pak voor dezelfde uren de Open-Meteo model-voorspelling (Hm0 / Tp / Tm02).
3. Match per uur (binnen ±30 min) en bereken de MLE voor een
   multiplicatieve bias — de geometrische gemiddelde van per-sample
   verhoudingen:
       bias_factor_hs     = exp(mean(log(boei_hs     / model_hs)))
       bias_factor_period = exp(mean(log(boei_period / model_period)))
   Dit is de juiste schatter (vergelijk ratio-of-means: die overschat
   systematisch bij gemengde-magnitude samples — bv. obs=[0.2,2.0]
   model=[0.4,1.0] geeft RoM=1.57 terwijl GM=1.0 — geen netto bias).
   Beide factoren gecapt op [0.5, 2.0] zodat een uitschieter het forecast
   niet de stratosfeer in stuurt.
4. `apply_bias_to_forecast` past de factor toe op de eerstvolgende 6-12 uur
   forecast, met exponentiële decay terug naar 1.0 over 24-48u (de model-skill
   neemt weer over).

Graceful degradation: als boei-data of model-predicties ontbreken levert
`compute_buoy_bias` `{}` (lege dict) en is `apply_bias_to_forecast` een no-op
— het systeem valt door naar zonder-bias mode zonder te crashen.

Concurrent-safe logging: `log_bias_observation` opent het JSONL-bestand in
append-mode per regel (open/flush/close) zodat parallelle pipeline-runs niet
elkaar's geschreven regels corrumperen.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from src.util import to_utc

logger = logging.getLogger(__name__)

# Default file location (relative to repo root). Tests overschrijven via path arg.
DEFAULT_BIAS_LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "bias_log.jsonl"

# Hard caps op de bias-factor zodat één uitbijter de forecast niet kapot maakt.
BIAS_FACTOR_MIN = 0.5
BIAS_FACTOR_MAX = 2.0

# Default decay-parameters (tau in uren). exp(-Δh / tau) → 0.37 op t=tau,
# 0.14 op t=2·tau. Voor Δh > 36u is bias effectief uit.
DEFAULT_DECAY_TAU_H = 18.0


def _coerce_aware_utc(ts: datetime) -> datetime:
    """
    Maak een aware UTC datetime; delegeer aan `src.util.to_utc` zodat
    naive = Europe/Amsterdam (Open-Meteo convention) consistent met de
    rest van de scoring-stack.

    Voorheen: `naive → UTC` aanname → mismatch van 1-2u tussen naive
    Open-Meteo marine rows en aware RWS observations → match-window
    ±30min liep stuk → bias-correctie viel terug op no-bias mode en
    `bias_log.jsonl` bleef leeg (Sprint 4 training-data niet gegenereerd).
    """
    return to_utc(ts)


def _match_pairs(
    observations: list[dict[str, Any]],
    model_predictions: list[dict[str, Any]],
    when: datetime,
    lookback_hours: int = 6,
    tolerance_minutes: int = 30,
) -> list[dict[str, Any]]:
    """
    Pair boei-observaties met model-predicties op gelijk uur (±tolerance).

    `observations`  : lijst dicts met sleutels {timestamp, height_m, period_s}
    `model_predictions`: lijst dicts met sleutels {timestamp, wave_height, ...}
                        of `swell_wave_period`/`wave_period` voor de periode.
    `when`          : "nu" — alles binnen [when - lookback_hours, when] wordt
                      gepaird, latere predicties worden genegeerd.

    Returns lijst van {timestamp, obs_hs, obs_period, model_hs, model_period}.
    """
    if not observations or not model_predictions:
        return []

    when_utc = _coerce_aware_utc(when)
    cutoff = when_utc - timedelta(hours=lookback_hours)
    tol = timedelta(minutes=tolerance_minutes)

    # Index modellen op aware UTC timestamp.
    model_by_ts: list[tuple] = []
    for m in model_predictions:
        ts = m.get("timestamp")
        if ts is None:
            continue
        ts_utc = _coerce_aware_utc(ts)
        model_by_ts.append((ts_utc, m))

    pairs: list[dict[str, Any]] = []
    for obs in observations:
        obs_ts = obs.get("timestamp")
        if obs_ts is None:
            continue
        obs_ts_utc = _coerce_aware_utc(obs_ts)
        if obs_ts_utc < cutoff or obs_ts_utc > when_utc + tol:
            continue

        obs_hs = obs.get("height_m") or obs.get("hs") or obs.get("hm0")
        obs_period = obs.get("period_s") or obs.get("period") or obs.get("tp")
        if obs_hs is None or obs_period is None:
            continue

        # Vind dichtsbij model-prediction binnen tolerantie.
        best = None
        best_dt = tol
        for mts, m in model_by_ts:
            dt = abs(mts - obs_ts_utc)
            if dt <= best_dt:
                best_dt = dt
                best = m

        if best is None:
            continue

        m_hs = best.get("wave_height") or best.get("swell_wave_height") or best.get("hs")
        m_period = (
            best.get("wave_period")
            or best.get("swell_wave_period")
            or best.get("tp")
            or best.get("period_s")
        )
        if m_hs is None or m_period is None:
            continue

        pairs.append({
            "timestamp": obs_ts_utc,
            "obs_hs": float(obs_hs),
            "obs_period": float(obs_period),
            "model_hs": float(m_hs),
            "model_period": float(m_period),
        })

    return pairs


def compute_buoy_bias(
    boei_observations: list[dict[str, Any]],
    model_predictions: list[dict[str, Any]],
    when: datetime,
    lookback_hours: int = 6,
) -> dict[str, float]:
    """
    Bereken bias-factor tussen live boei en model-forecast.

    Args:
        boei_observations: Lijst RWS-boei rows. Vereiste velden per row:
            - 'timestamp' (datetime, aware UTC of naive Europe/Amsterdam —
              consistent met `src.util.to_utc`)
            - 'height_m'  (m, Hm0)
            - 'period_s'  (s, Tm02 of equivalent)
        model_predictions: Lijst Open-Meteo marine rows met dezelfde
            timestamps en velden 'wave_height' en 'wave_period' (of
            'swell_wave_period'). Kunnen forecast-rijen voor de afgelopen
            uren zijn (Open-Meteo geeft past_hours mee in dezelfde fetch).
        when: huidige tijd; lookback wordt vanaf hier teruggerekend.
        lookback_hours: hoeveel uren terug we vergelijken (default 6).

    Returns:
        Dict met keys:
            'hs_bias_factor'     — vermenigvuldigingsfactor voor Hs (1.0 = geen bias)
            'period_bias_factor' — idem voor periode
            'n_samples'          — aantal gematchte uren
            'lookback_hours'     — input voor traceability
        Bij onvoldoende samples (n < 2) → lege dict.
    """
    pairs = _match_pairs(boei_observations, model_predictions, when, lookback_hours)
    if len(pairs) < 2:
        logger.info(
            "Bias-correctie: onvoldoende gematchte samples (%d), fallback to no-bias mode",
            len(pairs),
        )
        return {}

    # Geometric mean of per-sample ratios = MLE voor multiplicatieve bias.
    # Defensieve filter: zowel obs als model > 0 (anders log undefined).
    valid_hs = [p for p in pairs if p["model_hs"] > 0.05 and p["obs_hs"] > 0.05]
    valid_period = [p for p in pairs if p["model_period"] > 0.5 and p["obs_period"] > 0.5]

    if not valid_hs or not valid_period:
        logger.info("Bias-correctie: onvoldoende valide samples (model of obs ~0)")
        return {}

    hs_log_ratios = [math.log(p["obs_hs"] / p["model_hs"]) for p in valid_hs]
    period_log_ratios = [math.log(p["obs_period"] / p["model_period"]) for p in valid_period]

    hs_factor = math.exp(sum(hs_log_ratios) / len(hs_log_ratios))
    period_factor = math.exp(sum(period_log_ratios) / len(period_log_ratios))

    hs_factor = max(BIAS_FACTOR_MIN, min(BIAS_FACTOR_MAX, hs_factor))
    period_factor = max(BIAS_FACTOR_MIN, min(BIAS_FACTOR_MAX, period_factor))

    logger.info(
        "Bias-correctie: n=%d, hs_factor=%.3f, period_factor=%.3f",
        len(pairs), hs_factor, period_factor,
    )

    return {
        "hs_bias_factor": hs_factor,
        "period_bias_factor": period_factor,
        "n_samples": len(pairs),
        "lookback_hours": lookback_hours,
    }


def _decay_weight(hours_ahead: float, tau_h: float = DEFAULT_DECAY_TAU_H) -> float:
    """
    Exponentiële decay: bias-correctie 100% op t=0, ~37% op tau, ~5% op 3·tau.

    Voor negatieve hours_ahead (uren in het verleden) geven we 1.0 (volledige
    correctie blijft staan — past niet bij forecast maar bij historie-rescore).
    """
    if hours_ahead <= 0:
        return 1.0
    return math.exp(-hours_ahead / tau_h)


def apply_bias_to_forecast(
    forecast: list[dict[str, Any]],
    bias: dict[str, float],
    when: datetime,
    tau_h: float = DEFAULT_DECAY_TAU_H,
) -> list[dict[str, Any]]:
    """
    Pas bias-correctie toe op een Open-Meteo marine forecast-lijst.

    Werkt in-place noch retourneert kopieën — voor backwards-compat geven
    we een nieuwe lijst van dicts terug, zodat caller kan kiezen.

    Toegepaste correctie:
        corrected_hs(t)     = model_hs(t)     × (1 + (hs_factor - 1) × decay(t))
        corrected_period(t) = model_period(t) × (1 + (per_factor - 1) × decay(t))

    Decay is exp(-Δh / tau). Voor tau=18h is bias effectief uit na ~36-48h.
    Voor uren vóór `when` (historische rescore) blijft de bias volledig.

    Bij lege bias-dict (graceful degradation pad): lijst wordt verbatim
    teruggegeven (geen wijziging).
    """
    if not bias or not forecast:
        return forecast

    hs_factor = bias.get("hs_bias_factor", 1.0)
    period_factor = bias.get("period_bias_factor", 1.0)
    if hs_factor == 1.0 and period_factor == 1.0:
        return forecast

    when_utc = _coerce_aware_utc(when)
    out: list[dict[str, Any]] = []
    for row in forecast:
        ts = row.get("timestamp")
        if ts is None:
            out.append(row)
            continue
        ts_utc = _coerce_aware_utc(ts)
        hours_ahead = (ts_utc - when_utc).total_seconds() / 3600.0
        weight = _decay_weight(hours_ahead, tau_h)

        new_row = dict(row)
        # (factor - 1) × weight + 1 → factor=1 op weight=0, factor=full op weight=1
        hs_eff = 1.0 + (hs_factor - 1.0) * weight
        per_eff = 1.0 + (period_factor - 1.0) * weight

        for key in ("wave_height", "swell_wave_height", "wind_wave_height"):
            if key in new_row and new_row[key] is not None:
                new_row[key] = float(new_row[key]) * hs_eff
        for key in ("wave_period", "swell_wave_period", "wind_wave_period"):
            if key in new_row and new_row[key] is not None:
                new_row[key] = float(new_row[key]) * per_eff

        new_row["_bias_applied"] = {
            "hs_eff_factor": round(hs_eff, 3),
            "period_eff_factor": round(per_eff, 3),
            "decay_weight": round(weight, 3),
        }
        out.append(new_row)

    return out


def log_bias_observation(
    timestamp: datetime,
    model_predictions: list[dict[str, Any]],
    actual_observations: dict[str, list[dict[str, Any]]],
    path: Optional[Path] = None,
) -> int:
    """
    Schrijf één regel per boei × gematcht uur naar `data/bias_log.jsonl`.

    Format per regel (JSON):
        {
          "timestamp_logged": "2026-05-19T08:00:00+00:00",
          "obs_timestamp":    "2026-05-19T05:00:00+00:00",
          "station":          "IJG1",
          "model_hs":         0.84,
          "model_tp":         6.2,
          "observed_hs":      0.96,
          "observed_tp":      5.9,
          "hs_residual":      0.12,
          "tp_residual":     -0.3
        }

    Voorbereiding voor Sprint 4 (XGBoost training-data): elke regel is een
    geverifieerd boei-vs-model sample.

    Args:
        timestamp:           run-tijd (UTC voorkeur).
        model_predictions:   list van Open-Meteo marine rows zoals in
                             `compute_buoy_bias`.
        actual_observations: mapping {station_code: [boei_rows...]}. Stations
                             die geen data hebben mogen ontbreken of leeg zijn.
        path:                override voor de jsonl-bestandslocatie (testen).

    Returns:
        aantal weggeschreven regels.
    """
    if path is None:
        path = DEFAULT_BIAS_LOG_PATH
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    log_ts = _coerce_aware_utc(timestamp).isoformat()
    written = 0

    for station, obs_list in (actual_observations or {}).items():
        if not obs_list:
            continue
        pairs = _match_pairs(obs_list, model_predictions, timestamp, lookback_hours=12)
        if not pairs:
            continue
        for p in pairs:
            hs_residual = p["obs_hs"] - p["model_hs"]
            tp_residual = p["obs_period"] - p["model_period"]
            entry = {
                "timestamp_logged": log_ts,
                "obs_timestamp": p["timestamp"].isoformat(),
                "station": station,
                "model_hs": round(p["model_hs"], 3),
                "model_tp": round(p["model_period"], 3),
                "observed_hs": round(p["obs_hs"], 3),
                "observed_tp": round(p["obs_period"], 3),
                "hs_residual": round(hs_residual, 3),
                "tp_residual": round(tp_residual, 3),
            }
            # Concurrent-safe append: open per regel, write + flush, close.
            # Bij parallelle pipeline-runs zijn POSIX-write-append-operaties
            # < 4 KB atomair (Linux PIPE_BUF), dus regels mengen niet door
            # elkaar.
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    f.flush()
                written += 1
            except OSError as e:
                logger.warning("Kon bias_log niet schrijven (%s): %s", path, e)
                return written

    if written:
        logger.info("Bias-log: %d observaties weggeschreven naar %s", written, path)
    return written
