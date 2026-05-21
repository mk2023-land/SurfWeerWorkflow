"""
T1 — Swell-arrival detector op basis van offshore-boei spectrum-history.

Sprint 3 fix #15 uit research_master_improvement_plan.md.

Idee: een verre groundswell verraadt zichzelf op A12 (10 uur lead-time) en K13
(4u lead-time) door twee gelijktijdige signalen:

  (1) Piek-periode schuift in een paar uur naar HOGER (bv. 5s → 8s in 6u).
      Langere periode = energie van een verdere storm; korte periode = lokale
      wind-sea. Een snelle stijging is klassiek "swell starts to fill in".
  (2) Amplitude (Hm0) stijgt tegelijkertijd met > 20% in dezelfde 6u.
      Hogere energie en langere periode samen = nieuwe groundswell, niet alleen
      lokale wind die op het wateroppervlak rommelt.

Pas wanneer beide gelden activeert dit T1. Dat voorkomt false-positives bij
ruwe wind-zee zonder echte groundswell.

History wordt geschreven naar `data/buoy_spectra_history.jsonl` (append-only,
gitignored). Per pipeline-run wordt voor A12 en K13 één snapshot bewaard, plus
optioneel piek-frequency uit de bestaande SpectralPeak-lijst.

Concurrent-safe: append-mode + flush per regel, idem als bias_log.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from src.data.models import AlertType, WaveSpectrum

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parents[2] / "data" / "buoy_spectra_history.jsonl"

# Detectie-drempels. Te slap → false-positives bij wind-zee ruis. Te streng →
# we missen lichte groundswells (CASE 5 historisch).
PERIOD_RISE_S_OVER_6H = 2.0   # piek-periode moet ≥ 2.0 s gestegen zijn in 6 uur
AMPLITUDE_RISE_FRAC = 0.20     # Hm0 moet ≥ 20% gestegen zijn in 6 uur
HISTORY_WINDOW_HOURS = 6       # vensterlengte voor vergelijking


def _coerce_aware_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _spectrum_to_snapshot(station: str, spectrum: WaveSpectrum) -> dict[str, Any]:
    """Reduceer een WaveSpectrum tot een dunne snapshot voor de history-file."""
    # Pak dominante piek (hoogste amplitude), zo niet beschikbaar val terug op
    # mean_period.
    peak_period = spectrum.mean_period
    peak_height = spectrum.significant_height_total
    if spectrum.peaks:
        dom = max(spectrum.peaks, key=lambda p: p.height_m)
        peak_period = dom.period_s
        # peak_height blijft Hm0 (totaal) — boei publiceert per-partition niet,
        # dus dominante partition-hoogte is meestal Hm0 zelf.

    peak_freq_mhz = (1000.0 / peak_period) if peak_period > 0 else None

    return {
        "station": station,
        "timestamp": _coerce_aware_utc(spectrum.timestamp).isoformat(),
        "hm0": round(peak_height, 3),
        "tm02": round(spectrum.mean_period, 3),
        "th0": int(spectrum.mean_direction),
        "peak_period_s": round(peak_period, 3),
        "peak_freq_mhz": round(peak_freq_mhz, 1) if peak_freq_mhz else None,
    }


def append_buoy_snapshot(
    buoy_spectra: dict[str, list[WaveSpectrum]],
    path: Optional[Path] = None,
) -> int:
    """
    Schrijf voor elk offshore-station (A12, K13) de meest recente spectrum-snapshot
    naar `data/buoy_spectra_history.jsonl` (append-only).

    Args:
        buoy_spectra: mapping {station_code: [WaveSpectrum...]}. Lege lijsten
                      en ontbrekende stations worden overgeslagen.
        path:         override (testen).

    Returns:
        aantal weggeschreven snapshots.
    """
    if path is None:
        path = DEFAULT_HISTORY_PATH
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    for station, spectra in (buoy_spectra or {}).items():
        if not spectra:
            continue
        latest = spectra[-1]
        snapshot = _spectrum_to_snapshot(station, latest)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
                f.flush()
            written += 1
        except OSError as e:
            logger.warning("Kon buoy-spectra-history niet schrijven (%s): %s", path, e)
            return written
    if written:
        logger.info("Buoy-spectra history: %d snapshots opgeslagen", written)
    return written


def load_history(
    path: Optional[Path] = None,
    max_age_hours: int = 24,
) -> list[dict[str, Any]]:
    """
    Lees de jsonl-history file, filter op snapshots van de laatste `max_age_hours`.

    Stille fail bij ontbrekende file of corrupte regel (één run gemist mag
    nooit een crash veroorzaken).
    """
    if path is None:
        path = DEFAULT_HISTORY_PATH
    path = Path(path)
    if not path.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    snapshots: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = rec.get("timestamp")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                ts = _coerce_aware_utc(ts)
                if ts < cutoff:
                    continue
                rec["_ts_parsed"] = ts
                snapshots.append(rec)
    except OSError as e:
        logger.warning("Kon buoy-spectra-history niet lezen (%s): %s", path, e)
        return []

    snapshots.sort(key=lambda r: r["_ts_parsed"])
    return snapshots


def detect_swell_arrival(
    history: list[dict[str, Any]],
    now: Optional[datetime] = None,
    window_hours: int = HISTORY_WINDOW_HOURS,
) -> Optional[AlertType]:
    """
    Detecteer of een nieuwe groundswell aankomt op basis van offshore-boei
    spectrum-history.

    Criteria (per station, met OF over stations):
      - peak_period_s nu ≥ peak_period_s `window_hours` uur geleden + PERIOD_RISE
      - hm0 nu        ≥ hm0 `window_hours` uur geleden × (1 + AMPLITUDE_RISE)

    Beide moeten gelden in dezelfde station-tijdreeks. Wint één station de
    detectie, dan T1.

    Args:
        history: lijst snapshots zoals `load_history` geeft. Mag leeg zijn.
        now:     referentie-tijd; default `datetime.now(UTC)`.
        window_hours: vensterlengte voor vergelijking (default 6).

    Returns:
        AlertType.SWELL_ARRIVAL als beide condities gelden voor ≥ 1 station,
        anders None.
    """
    if not history:
        return None

    now = _coerce_aware_utc(now) if now else datetime.now(timezone.utc)
    cutoff_old = now - timedelta(hours=window_hours + 1)
    cutoff_recent = now - timedelta(hours=2)  # "nu" is de laatste 2u

    # Groepeer per station als (timestamp, record)-tuples zodat we kunnen sorteren.
    by_station: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    for rec in history:
        ts = rec.get("_ts_parsed") or _coerce_aware_utc(
            datetime.fromisoformat(rec["timestamp"])
        )
        by_station.setdefault(rec["station"], []).append((ts, rec))

    for station, items in by_station.items():
        items.sort(key=lambda x: x[0])
        recent = [r for ts, r in items if ts >= cutoff_recent]
        older = [r for ts, r in items if ts <= cutoff_old + timedelta(hours=1)
                 and ts >= cutoff_old - timedelta(hours=1)]

        if not recent or not older:
            continue

        latest = recent[-1]
        ref = older[-1]

        latest_period = latest.get("peak_period_s")
        ref_period = ref.get("peak_period_s")
        latest_hm0 = latest.get("hm0")
        ref_hm0 = ref.get("hm0")

        if None in (latest_period, ref_period, latest_hm0, ref_hm0):
            continue
        if ref_hm0 <= 0:
            continue

        period_rise = latest_period - ref_period
        amplitude_rise_frac = (latest_hm0 - ref_hm0) / ref_hm0

        if period_rise >= PERIOD_RISE_S_OVER_6H and amplitude_rise_frac >= AMPLITUDE_RISE_FRAC:
            logger.info(
                "T1 swell-arrival gedetecteerd op %s: periode %.1fs → %.1fs (+%.1fs), "
                "Hm0 %.2fm → %.2fm (+%.0f%%)",
                station, ref_period, latest_period, period_rise,
                ref_hm0, latest_hm0, amplitude_rise_frac * 100,
            )
            return AlertType.SWELL_ARRIVAL

    return None
