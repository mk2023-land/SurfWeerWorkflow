"""
Unit tests voor RWS-boei bias-correctie + bias-logger (Sprint 3 #14 & #16).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scoring.bias_correction import (
    BIAS_FACTOR_MAX,
    BIAS_FACTOR_MIN,
    apply_bias_to_forecast,
    compute_buoy_bias,
    log_bias_observation,
)


def _ts(hours_ago: int) -> datetime:
    return datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc) - timedelta(hours=hours_ago)


_NOW = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)


def _obs(hours_ago: int, hs: float, period: float) -> dict:
    return {"timestamp": _ts(hours_ago), "height_m": hs, "period_s": period}


def _model(hours_ago: int, hs: float, period: float) -> dict:
    return {
        "timestamp": _ts(hours_ago),
        "wave_height": hs,
        "wave_period": period,
    }


class TestComputeBuoyBias:
    def test_no_bias_when_perfect_match(self):
        obs = [_obs(h, 1.0, 6.0) for h in range(1, 6)]
        model = [_model(h, 1.0, 6.0) for h in range(1, 6)]
        bias = compute_buoy_bias(obs, model, _NOW)
        assert bias["hs_bias_factor"] == pytest.approx(1.0)
        assert bias["period_bias_factor"] == pytest.approx(1.0)
        assert bias["n_samples"] >= 4

    def test_positive_bias_when_observed_higher(self):
        """Boei meet structureel 25% hoger Hs dan model → factor ~1.25."""
        obs = [_obs(h, 1.25, 6.0) for h in range(1, 6)]
        model = [_model(h, 1.0, 6.0) for h in range(1, 6)]
        bias = compute_buoy_bias(obs, model, _NOW)
        assert bias["hs_bias_factor"] == pytest.approx(1.25, abs=0.01)
        assert bias["period_bias_factor"] == pytest.approx(1.0, abs=0.01)

    def test_factor_is_capped_at_max(self):
        """Extreem boei-overschot (5x) wordt gecapt op BIAS_FACTOR_MAX."""
        obs = [_obs(h, 5.0, 6.0) for h in range(1, 6)]
        model = [_model(h, 1.0, 6.0) for h in range(1, 6)]
        bias = compute_buoy_bias(obs, model, _NOW)
        assert bias["hs_bias_factor"] == BIAS_FACTOR_MAX

    def test_factor_is_capped_at_min(self):
        obs = [_obs(h, 0.1, 6.0) for h in range(1, 6)]
        model = [_model(h, 1.0, 6.0) for h in range(1, 6)]
        bias = compute_buoy_bias(obs, model, _NOW)
        assert bias["hs_bias_factor"] == BIAS_FACTOR_MIN

    def test_empty_returns_empty_dict(self):
        assert compute_buoy_bias([], [], _NOW) == {}
        assert compute_buoy_bias([], [_model(1, 1.0, 6.0)], _NOW) == {}
        assert compute_buoy_bias([_obs(1, 1.0, 6.0)], [], _NOW) == {}

    def test_insufficient_samples_returns_empty(self):
        """Slechts één gematcht paar = onvoldoende → graceful empty."""
        obs = [_obs(1, 1.0, 6.0)]
        model = [_model(1, 1.0, 6.0)]
        assert compute_buoy_bias(obs, model, _NOW) == {}

    def test_geometric_mean_on_mixed_magnitude(self):
        """
        Regressietest: bij gemengde-magnitude samples moet de schatter
        netto-géén bias zien wanneer per-sample ratios elkaar opheffen.

        obs=[0.2, 2.0], model=[0.4, 1.0] → per-sample ratios 0.5 en 2.0
        (multiplicatief symmetrisch rond 1). De fout-statistische
        ratio-of-means zou 1.57 geven; de MLE (geometric mean) geeft 1.0.
        """
        obs = [_obs(2, 0.2, 6.0), _obs(1, 2.0, 6.0)]
        model = [_model(2, 0.4, 6.0), _model(1, 1.0, 6.0)]
        bias = compute_buoy_bias(obs, model, _NOW)
        assert bias["hs_bias_factor"] == pytest.approx(1.0, abs=0.01)

    def test_naive_timestamps_handled(self):
        """
        Naive datetimes worden als Europe/Amsterdam geïnterpreteerd
        (consistent met src.util.to_utc — B5 fix). Bij gelijke wallclock
        in beide series moet match-window niet stuk lopen.
        """
        obs = [{"timestamp": _ts(h).replace(tzinfo=None), "height_m": 1.0, "period_s": 6.0}
               for h in range(1, 6)]
        model = [{"timestamp": _ts(h).replace(tzinfo=None), "wave_height": 1.0, "wave_period": 6.0}
                 for h in range(1, 6)]
        bias = compute_buoy_bias(obs, model, _NOW.replace(tzinfo=None))
        assert bias != {}

    def test_b5_mixed_naive_om_and_aware_rws_timestamps(self):
        """
        B5 regressie: Open-Meteo retourneert naive Europe/Amsterdam,
        RWS retourneert aware UTC. Voorheen interpreteerde
        `_coerce_aware_utc` naive als UTC → 2u offset → match-window ±30min
        miste alle paren → bias-correctie permanent {} → Sprint 4 trainings-
        data nooit gegenereerd.

        Test: zelfde wallclock-uur, één naive (NL) en één aware (UTC).
        Met de fix moeten de paren matchen.
        """
        # Wallclock 10:00 NL = 08:00 UTC (CEST in mei)
        from datetime import timezone as _tz
        wallclock_naive = datetime(2026, 5, 19, 10, 0, 0)  # 10:00 NL (naive)
        rws_utc = datetime(2026, 5, 19, 8, 0, 0, tzinfo=_tz.utc)  # zelfde tijd UTC

        obs = [
            {"timestamp": rws_utc - timedelta(hours=h),
             "height_m": 1.2, "period_s": 6.0}
            for h in range(0, 4)
        ]
        model = [
            {"timestamp": wallclock_naive - timedelta(hours=h),
             "wave_height": 1.0, "wave_period": 6.0}
            for h in range(0, 4)
        ]
        bias = compute_buoy_bias(obs, model, rws_utc)
        assert bias != {}, \
            "B5 fix: mix van naive-NL en aware-UTC moet matchen op zelfde wallclock"
        assert bias["hs_bias_factor"] == pytest.approx(1.2, abs=0.05)


class TestApplyBiasToForecast:
    def test_noop_with_empty_bias(self):
        fc = [_model(-h, 1.0, 6.0) for h in range(0, 6)]
        assert apply_bias_to_forecast(fc, {}, _NOW) is fc

    def test_full_correction_at_t_zero(self):
        """Op t=0 (now) krijgt forecast volledige bias-correctie."""
        fc = [{"timestamp": _NOW, "wave_height": 1.0, "wave_period": 6.0}]
        bias = {"hs_bias_factor": 1.5, "period_bias_factor": 1.0}
        out = apply_bias_to_forecast(fc, bias, _NOW)
        assert out[0]["wave_height"] == pytest.approx(1.5, abs=0.01)
        assert out[0]["wave_period"] == pytest.approx(6.0, abs=0.01)

    def test_decay_reduces_correction_far_in_future(self):
        """Op t=72h (4·tau) is bias-correctie effectief weg."""
        fc = [{"timestamp": _NOW + timedelta(hours=72),
               "wave_height": 1.0, "wave_period": 6.0}]
        bias = {"hs_bias_factor": 1.5, "period_bias_factor": 1.0}
        out = apply_bias_to_forecast(fc, bias, _NOW)
        # decay weight ~exp(-72/18)=0.018 → effective factor ~1.009
        assert out[0]["wave_height"] == pytest.approx(1.009, abs=0.02)

    def test_decay_intermediate(self):
        """Op t=tau (18h) is correctie ~37% van de bias."""
        fc = [{"timestamp": _NOW + timedelta(hours=18),
               "wave_height": 1.0, "wave_period": 6.0}]
        bias = {"hs_bias_factor": 2.0, "period_bias_factor": 1.0}
        out = apply_bias_to_forecast(fc, bias, _NOW)
        # weight = e^-1 = 0.368. factor = 1 + 1.0*0.368 = 1.368
        assert out[0]["wave_height"] == pytest.approx(1.368, abs=0.02)

    def test_bias_application_metadata(self):
        fc = [{"timestamp": _NOW, "wave_height": 1.0, "wave_period": 6.0}]
        bias = {"hs_bias_factor": 1.5, "period_bias_factor": 1.2}
        out = apply_bias_to_forecast(fc, bias, _NOW)
        assert "_bias_applied" in out[0]
        assert out[0]["_bias_applied"]["decay_weight"] == pytest.approx(1.0, abs=0.01)


class TestLogBiasObservation:
    def test_writes_jsonl_lines(self, tmp_path):
        log_path = tmp_path / "bias_log.jsonl"
        obs = [_obs(h, 1.2, 5.9) for h in range(1, 4)]
        model = [_model(h, 1.0, 6.0) for h in range(1, 4)]
        n = log_bias_observation(_NOW, model, {"IJG1": obs}, path=log_path)
        assert n == 3
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 3
        rec = json.loads(lines[0])
        assert rec["station"] == "IJG1"
        assert rec["observed_hs"] == 1.2
        assert rec["model_hs"] == 1.0
        assert rec["hs_residual"] == pytest.approx(0.2, abs=0.001)

    def test_append_mode(self, tmp_path):
        """Tweede call appendt, overschrijft niet."""
        log_path = tmp_path / "bias_log.jsonl"
        obs = [_obs(h, 1.0, 6.0) for h in range(1, 3)]
        model = [_model(h, 1.0, 6.0) for h in range(1, 3)]
        log_bias_observation(_NOW, model, {"IJG1": obs}, path=log_path)
        log_bias_observation(_NOW, model, {"A12": obs}, path=log_path)
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 4
        stations = {json.loads(line)["station"] for line in lines}
        assert stations == {"IJG1", "A12"}

    def test_empty_observations_no_write(self, tmp_path):
        log_path = tmp_path / "bias_log.jsonl"
        n = log_bias_observation(_NOW, [_model(1, 1.0, 6.0)], {}, path=log_path)
        assert n == 0
        assert not log_path.exists() or log_path.read_text() == ""
