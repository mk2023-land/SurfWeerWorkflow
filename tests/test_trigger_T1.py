"""
Unit tests voor T1 swell-arrival detector (Sprint 3 #15).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.models import AlertType, SpectralPeak, SwellType, WaveSpectrum
from src.scoring.trigger_T1 import (
    append_buoy_snapshot,
    detect_swell_arrival,
    load_history,
)


def _spec(hours_ago: int, hs: float, period: float, direction: int = 320) -> WaveSpectrum:
    """Bouw een minimal WaveSpectrum met één dominante piek."""
    ts = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc) - timedelta(hours=hours_ago)
    peak = SpectralPeak(
        frequency_mhz=(1000.0 / period) if period > 0 else 0,
        period_s=period,
        height_m=hs,
        direction_deg=direction,
        type=SwellType.WIND_SEA if period < 7 else SwellType.GROUND_SWELL,
    )
    return WaveSpectrum(
        timestamp=ts,
        significant_height_total=hs,
        mean_period=period,
        mean_direction=direction,
        peaks=[peak],
    )


_NOW = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)


class TestAppendAndLoad:
    def test_append_writes_jsonl(self, tmp_path):
        path = tmp_path / "history.jsonl"
        n = append_buoy_snapshot({
            "A12": [_spec(0, 0.8, 5.5)],
            "K13": [_spec(0, 1.0, 6.0)],
        }, path=path)
        assert n == 2
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        recs = [json.loads(l) for l in lines]
        stations = {r["station"] for r in recs}
        assert stations == {"A12", "K13"}

    def test_append_skips_empty(self, tmp_path):
        path = tmp_path / "history.jsonl"
        n = append_buoy_snapshot({"A12": [], "K13": []}, path=path)
        assert n == 0

    def test_load_filters_by_age(self, tmp_path):
        path = tmp_path / "history.jsonl"
        # Schrijf een snapshot van 30 uur geleden (te oud bij max_age=24).
        append_buoy_snapshot({"A12": [_spec(30, 0.8, 5.5)]}, path=path)
        # Hack: schrijf zelf één recente.
        rec = {
            "station": "A12",
            "timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "hm0": 1.0, "tm02": 6.0, "th0": 320,
            "peak_period_s": 6.0, "peak_freq_mhz": 167,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

        snapshots = load_history(path=path, max_age_hours=24)
        assert len(snapshots) == 1
        assert snapshots[0]["station"] == "A12"
        assert snapshots[0]["hm0"] == 1.0

    def test_load_missing_file_returns_empty(self, tmp_path):
        assert load_history(path=tmp_path / "nonexistent.jsonl") == []


class TestDetectSwellArrival:
    def _make_history(self, station: str, old_hs: float, old_period: float,
                      new_hs: float, new_period: float, now=_NOW) -> list:
        """Bouw een history-lijst met 6+ uur tussen oud en nieuw snapshot."""
        old_ts = now - timedelta(hours=6)
        new_ts = now - timedelta(hours=0.5)
        return [
            {
                "station": station,
                "timestamp": old_ts.isoformat(),
                "_ts_parsed": old_ts,
                "hm0": old_hs,
                "peak_period_s": old_period,
            },
            {
                "station": station,
                "timestamp": new_ts.isoformat(),
                "_ts_parsed": new_ts,
                "hm0": new_hs,
                "peak_period_s": new_period,
            },
        ]

    def test_no_history_returns_none(self):
        assert detect_swell_arrival([], now=_NOW) is None

    def test_groundswell_arrival_detected(self):
        """Periode 5s → 8s én Hm0 0.8m → 1.2m in 6u = T1."""
        hist = self._make_history("A12", old_hs=0.8, old_period=5.0,
                                  new_hs=1.2, new_period=8.0)
        result = detect_swell_arrival(hist, now=_NOW)
        assert result == AlertType.SWELL_ARRIVAL

    def test_period_rise_alone_not_enough(self):
        """Periode stijgt maar amplitude blijft gelijk → geen T1."""
        hist = self._make_history("A12", old_hs=1.0, old_period=5.0,
                                  new_hs=1.05, new_period=8.0)
        result = detect_swell_arrival(hist, now=_NOW)
        assert result is None

    def test_amplitude_rise_alone_not_enough(self):
        """Hm0 verdubbelt maar periode blijft gelijk → wind-zee, geen T1."""
        hist = self._make_history("A12", old_hs=0.5, old_period=5.0,
                                  new_hs=1.5, new_period=5.2)
        result = detect_swell_arrival(hist, now=_NOW)
        assert result is None

    def test_period_decrease_not_t1(self):
        """Periode daalt = swell loopt af, géén arrival."""
        hist = self._make_history("A12", old_hs=0.8, old_period=10.0,
                                  new_hs=1.2, new_period=7.0)
        result = detect_swell_arrival(hist, now=_NOW)
        assert result is None

    def test_any_station_triggers(self):
        """Wint één station, dan T1 ongeacht andere stations."""
        hist = (
            self._make_history("A12", 1.0, 5.0, 1.1, 5.2)  # geen trigger
            + self._make_history("K13", 0.8, 5.0, 1.2, 8.0)  # wel trigger
        )
        result = detect_swell_arrival(hist, now=_NOW)
        assert result == AlertType.SWELL_ARRIVAL
