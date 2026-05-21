"""
Orchestration tests voor main.py SurfAlertSystem.

Dekt de KRITIEKE bug-fixes die ervoor zorgen dat alerts ÜBERHAUPT kunnen
firen en dat bias-correctie daadwerkelijk in productie wordt gebruikt:

  Fix #1 — seasonal_baseline wiring: zonder dit retourneert
           calculate_rarity_percentile altijd 50 → is_alertworthy faalt
           altijd op de rarity-check (>=70). Hier bewijzen we dat
           analyze_windows nu met de baseline wordt aangeroepen en dat
           rarity_percentile dus ECHTE waarden retourneert.
  Fix #2 — bias_correction wiring: vergelijk laatste boei vs model en pas
           decay-correctie toe op de eerstvolgende forecast-uren. Hier
           bewijzen we dat de marine-rows ná main.py niet meer 1-op-1
           gelijk zijn aan de input wanneer er bias is.
  Fix #3 — record_alert PAS na succesvolle send: bij notifier-5xx mag de
           state NIET ge-update worden (anders ghost-cooldown van 4u +
           weekly counter ++).
  Fix #4 — RunLog audit fields: alert_types_detected, llm_validation_*,
           bias_correction_applied, rws_status, openmeteo_status,
           seasonal_baseline_loaded zijn gevuld na een run.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

# Force ALERTS_ENABLED=true voordat config geladen wordt — anders is
# `alerts_enabled` in ALERT_CONFIG False (default) en gaat evaluate_forecast
# nooit langs de cooldown/weekly-check logica heen waar we tests voor schrijven.
os.environ.setdefault('ALERTS_ENABLED', 'true')


# ---------------------------------------------------------------------------
# Fixtures: minimale mock-data voor open-meteo + rws + baseline.
# ---------------------------------------------------------------------------

def _utc(year=2026, month=5, day=20, hour=8) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)


def _now_aligned_start() -> datetime:
    """Start de stub-data 6u vóór 'nu' zodat de bias-correctie lookback (6u
    naar boei-obs) altijd minstens enkele samples in scope heeft, ongeacht
    wanneer de test-suite draait."""
    return datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    ) - timedelta(hours=6)


def _marine_rows(n: int = 48, hs: float = 1.0, period: float = 6.0,
                 start: datetime = None) -> list[dict[str, Any]]:
    if start is None:
        start = _now_aligned_start()
    out = []
    for i in range(n):
        ts = start + timedelta(hours=i)
        out.append({
            'timestamp': ts,
            'wave_height': hs,
            'wave_period': period,
            'wave_direction': 280.0,
            'swell_wave_height': hs * 0.6,
            'swell_wave_period': period + 2.0,
            'swell_wave_direction': 290.0,
            'wind_wave_height': hs * 0.4,
            'wind_wave_period': period - 1.0,
            'wind_wave_direction': 250.0,
            'sea_surface_temperature': 15.0,
            'ocean_current_velocity': 0.1,
            'ocean_current_direction': 100.0,
            'sea_level_height_msl': 0.5,
        })
    return out


def _forecast_rows(n: int = 48, wind_speed: float = 8.0, wind_dir: int = 100,
                   start: datetime = None) -> list[dict[str, Any]]:
    if start is None:
        start = _now_aligned_start()
    out = []
    for i in range(n):
        ts = start + timedelta(hours=i)
        out.append({
            'timestamp': ts,
            'wind_speed': wind_speed,
            'wind_direction': wind_dir,
            'wind_gusts': wind_speed + 3.0,
            'temperature': 14.0,
            'precipitation': 0.0,
            'visibility': 10000.0,
            'weather_code': 0,
            'relative_humidity': 70.0,
            'dew_point': 9.0,
            'uv_index': 3.0,
            'sunshine_duration': 1800.0,
            'pressure': 1015.0,
            'cloud_cover': 30.0,
            'cape': 0.0,
            'lifted_index': 0.0,
            'convective_inhibition': 0.0,
            'boundary_layer_height': 800.0,
        })
    return out


def _openmeteo_data(hs: float = 1.0, period: float = 6.0,
                    wind_speed: float = 8.0, wind_dir: int = 100,
                    n: int = 48) -> dict[str, Any]:
    """Volledig openmeteo_data stub voor main.py wiring."""
    start = _now_aligned_start()
    return {
        'marine': _marine_rows(n=n, hs=hs, period=period, start=start),
        'forecast': {
            'knmi_seamless': _forecast_rows(
                n=n, wind_speed=wind_speed, wind_dir=wind_dir, start=start
            ),
        },
    }


def _rws_data_with_buoy(hs_obs: float = 1.3, period_obs: float = 7.0,
                         n: int = 6) -> dict[str, Any]:
    """RWS-stub met IJG1 boei-data die structureel hoger meet dan model →
    biast positief."""
    base = _now_aligned_start() + timedelta(hours=1)
    raw = []
    for i in range(n):
        ts = base + timedelta(hours=i)
        raw.append({
            'timestamp': ts,
            'height_m': hs_obs,
            'period_s': period_obs,
            'tp_s': period_obs,
            'sobh_deg': 25.0,
            'hmax_m': hs_obs * 1.5,
        })
    return {
        'primary_buoy': {
            'spectra': [],
            'raw_data': raw,
        },
        'early_warning_buoys': {
            'A12': {'spectra': [], 'raw_data': []},
            'K13': {'spectra': [], 'raw_data': []},
        },
        'tide': {
            'latest_surge_cm': 5.0,
        },
    }


def _baseline_low_p70() -> dict[str, dict]:
    """Baseline waarin p70 LAAG ligt — bijna elke score >= 70e percentile.

    Bewijst dat zonder fix #1 (geen baseline) rarity_percentile altijd 50
    zou zijn → is_alertworthy.rarity >=70 faalt altijd, terwijl MET deze
    baseline scores wel boven 70e percentile uit kunnen komen.
    """
    out = {}
    for wk in range(1, 54):
        out[f'week_{wk}'] = {
            'p50': 20.0,
            'p70': 30.0,
            'p90': 50.0,
            'sample_size': 100,
        }
    return out


# ---------------------------------------------------------------------------
# Helpers: async-fetch patch + event-loop wrapper.
# ---------------------------------------------------------------------------

class _AsyncReturn:
    """Maakt een coroutine die direct een waarde retourneert (voor patch)."""

    def __init__(self, value):
        self.value = value

    def __call__(self, *args, **kwargs):
        async def _coro():
            return self.value
        return _coro()


def _run_async(coro):
    """Run async work zonder de globale event-loop voor andere tests te
    sluiten. Python 3.10's `asyncio.run` close't de loop en zet de policy
    op None — een volgende test die `asyncio.get_event_loop()` aanroept
    crasht dan met "no current event loop". Wij maken een eigen loop, runnen,
    en herstellen het policy-default na afloop (tests test_rws.py gebruiken
    `asyncio.get_event_loop()` direct)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture
def tmp_state_dir(tmp_path, monkeypatch):
    """Maak een schone tmp data-dir zodat state.json / forecasts_log.jsonl
    niet de echte data corrumperen tijdens tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    return data_dir


@pytest.fixture
def patched_system(tmp_state_dir, monkeypatch):
    """SurfAlertSystem instance met gepatchte data-fetchers + notifier."""
    from src import main as main_mod
    monkeypatch.setattr(
        main_mod, 'fetch_all_openmeteo_data',
        _AsyncReturn(_openmeteo_data()),
    )
    monkeypatch.setattr(
        main_mod, 'fetch_all_rws_data',
        _AsyncReturn(_rws_data_with_buoy()),
    )
    system = main_mod.SurfAlertSystem(dry_run=True)
    system.notifier = MagicMock()
    system.notifier.channel = 'mock'
    system.notifier.send_alert.return_value = {
        'success': True, 'channel': 'mock', 'message': 'ok'
    }
    system.notifier.send_digest.return_value = {
        'success': True, 'channel': 'mock', 'message': 'ok'
    }
    return system


# ---------------------------------------------------------------------------
# Fix #1 — seasonal baseline wiring.
# ---------------------------------------------------------------------------

class TestSeasonalBaselineWiring:
    """⭐ KRITIEK: bewijst dat alerts NU WEL kunnen firen.

    Voor de fix: analyze_windows kreeg geen baseline →
    calculate_rarity_percentile retourneerde altijd 50.0 →
    SurfWindow.is_alertworthy faalde altijd op `rarity_percentile >= 70`.
    """

    def test_analyze_windows_called_with_baseline(self, patched_system, monkeypatch):
        """analyze_windows MOET seasonal_baseline meekrijgen (niet None)."""
        baseline = _baseline_low_p70()

        from src import main as main_mod
        # Patch via main_mod.SeasonalBaselineBuilder — dit is dezelfde class
        # die main.py importeerde (zelfde id), dus de patch werkt op de
        # caller-binding.
        monkeypatch.setattr(
            main_mod.SeasonalBaselineBuilder,
            'load_baseline', lambda self: baseline,
        )

        recorded = {}
        original = main_mod.analyze_windows

        def _capture(hourly_scores, triggers_dict, **kwargs):
            recorded['seasonal_baseline'] = kwargs.get('seasonal_baseline')
            return original(hourly_scores, triggers_dict, **kwargs)

        monkeypatch.setattr(main_mod, 'analyze_windows', _capture)

        _run_async(patched_system.run())

        assert 'seasonal_baseline' in recorded, (
            "analyze_windows werd niet aangeroepen met seasonal_baseline kwarg"
        )
        assert recorded['seasonal_baseline'] is baseline, (
            "analyze_windows kreeg niet de geladen baseline mee — alerts kunnen "
            "niet firen want rarity_percentile is permanent 50.0"
        )

    def test_missing_baseline_falls_back_to_none(self, patched_system, monkeypatch):
        """Zonder baseline-file: WARNING log + self.seasonal_baseline = None,
        run mag niet crashen."""
        from src import main as main_mod
        monkeypatch.setattr(
            main_mod.SeasonalBaselineBuilder,
            'load_baseline', lambda self: {},
        )

        run_log = _run_async(patched_system.run())
        assert patched_system.seasonal_baseline is None
        assert run_log.seasonal_baseline_loaded is False
        assert run_log.error is None  # geen crash

    def test_rarity_percentile_actually_used(self, patched_system, monkeypatch):
        """Smoke-bewijs: met een baseline waarin p70 laag ligt, krijgen
        windows een rarity_percentile != 50.0. Voor de fix zou rarity_percentile
        altijd 50.0 zijn → harde "alerts kunnen niet firen" garantie."""
        baseline = _baseline_low_p70()
        from src import main as main_mod
        monkeypatch.setattr(
            main_mod.SeasonalBaselineBuilder,
            'load_baseline', lambda self: baseline,
        )

        captured = {}
        original = main_mod.analyze_windows

        def _capture(*args, **kwargs):
            result = original(*args, **kwargs)
            captured['windows'] = result
            return result

        monkeypatch.setattr(main_mod, 'analyze_windows', _capture)
        _run_async(patched_system.run())

        windows = captured.get('windows') or []
        if windows:
            non_default = [w for w in windows if abs(w.rarity_percentile - 50.0) > 0.01]
            assert non_default, (
                "Alle windows hebben rarity_percentile=50.0 — baseline lijkt "
                "niet gebruikt te zijn. Voor de fix WAS dit de productie-state "
                "en kon GEEN ENKELE alert firen."
            )


# ---------------------------------------------------------------------------
# Fix #2 — bias-correction wiring.
# ---------------------------------------------------------------------------

class TestBiasCorrectionWiring:
    """Bewijst dat de gewerkte+geteste bias_correction.py module nu daadwerkelijk
    de forecast aanpast, niet alleen logt."""

    def test_bias_applied_to_marine_rows(self, patched_system, monkeypatch):
        """Met IJG1-obs ~30% hoger dan model moeten de eerste marine-rows
        ná main.py de _bias_applied marker meekrijgen."""
        from src import main as main_mod
        monkeypatch.setattr(
            main_mod.SeasonalBaselineBuilder,
            'load_baseline', lambda self: {},
        )

        captured = {}
        original_build = main_mod.SurfAlertSystem._build_hour_states

        def _spy(self, openmeteo_data, rws_data):
            captured['marine_in_build'] = list(openmeteo_data.get('marine') or [])
            return original_build(self, openmeteo_data, rws_data)

        monkeypatch.setattr(
            main_mod.SurfAlertSystem, '_build_hour_states', _spy
        )

        run_log = _run_async(patched_system.run())

        marine = captured.get('marine_in_build') or []
        with_marker = [m for m in marine if '_bias_applied' in m]
        assert with_marker, (
            "Geen enkele marine-row heeft _bias_applied — bias-correctie is "
            "niet gewired in main.py (feature staat als 'dead code')."
        )
        assert run_log.bias_correction_applied is True

    def test_bias_skipped_when_no_buoy_data(self, patched_system, monkeypatch):
        """Lege boei-data → bias-correctie no-op, geen crash."""
        from src import main as main_mod
        monkeypatch.setattr(
            main_mod, 'fetch_all_rws_data',
            _AsyncReturn({}),
        )
        monkeypatch.setattr(
            main_mod.SeasonalBaselineBuilder,
            'load_baseline', lambda self: {},
        )

        run_log = _run_async(patched_system.run())
        assert run_log.bias_correction_applied is False
        assert run_log.error is None


# ---------------------------------------------------------------------------
# Fix #3 — record_alert PAS na succesvolle send.
# ---------------------------------------------------------------------------

class TestRecordAlertOnlyAfterSuccess:
    """Voor de fix: record_alert + save state werd uitgevoerd VOOR de notifier
    call. Bij 5xx-spike kon dat de hele weekly alert-budget kosten zonder
    ook maar één alert te verzenden (ghost-cooldown)."""

    def test_evaluate_forecast_does_not_record_alert(self, tmp_state_dir, monkeypatch):
        """evaluate_forecast mag NIET zelf state bijwerken (was de bug)."""
        from src.alerts import engine as engine_mod
        from src.alerts.engine import AlertEngine
        from src.data.models import (
            AlertType,
            ScoreBreakdown,
            SurfWindow,
        )
        # Forceer alerts_enabled True in engine's view op ALERT_CONFIG.
        engine_mod.ALERT_CONFIG['alerts_enabled'] = True

        state_file = tmp_state_dir / "state.json"
        engine = AlertEngine(state_file=str(state_file))

        ts0 = _utc(hour=10)
        scores = []
        for i in range(3):
            scores.append(ScoreBreakdown(
                timestamp=ts0 + timedelta(hours=i),
                golf_score=35.0, wind_score=30.0,
                tide_score=15.0, swell_dir_bonus=10.0,
                confidence=1.0,
            ))
        window = SurfWindow(
            start=scores[0].timestamp, end=scores[-1].timestamp,
            peak_score=90, median_score=90,
            peak_hour=scores[0].timestamp,
            triggers=[AlertType.SUSTAINED_GROUNDSWELL],
            stability=1.0, rarity_percentile=95.0,
            hourly_scores=scores, kind='surfable',
        )

        # `evaluate_forecast` roept nu `detect_all_with_candidates` aan
        # (tuple-return: Set + Dict[AlertType, AlertCandidate]). De legacy
        # `detect_all` blijft als backwards-compat shim bestaan voor main.py
        # en is hier irrelevant; we mocken de methode die echt wordt
        # aangeroepen door evaluate_forecast.
        engine.detector_engine.detect_all_with_candidates = MagicMock(
            return_value=({AlertType.SUSTAINED_GROUNDSWELL}, {})
        )

        before_alerts = engine.state.alerts_sent_this_week
        before_cooldown = engine.state.cooldown_until

        decision = engine.evaluate_forecast([], [], None, [window], False)
        assert decision.has_alert, "Setup: window moest alert-waardig zijn"

        # Belangrijkste assert: state is NIET bijgewerkt door evaluate_forecast.
        assert engine.state.alerts_sent_this_week == before_alerts, (
            "evaluate_forecast updatet weekly counter — dit is de bug die "
            "ghost-cooldowns gaf bij notifier-5xx."
        )
        assert engine.state.cooldown_until == before_cooldown

    def test_failed_notifier_does_not_update_state(self, patched_system, monkeypatch):
        """Notifier returnt success=False → state.last_alert_time blijft None,
        cooldown_until blijft None, weekly counter ongewijzigd."""
        baseline = _baseline_low_p70()
        from src import main as main_mod
        monkeypatch.setattr(
            main_mod.SeasonalBaselineBuilder,
            'load_baseline', lambda self: baseline,
        )
        from src.data.models import (
            AlertCandidate,
            AlertType,
            Decision,
            ScoreBreakdown,
            SurfWindow,
        )
        from src.llm.validator import ValidationResult

        ts0 = _utc(hour=10)
        scores = [ScoreBreakdown(
            timestamp=ts0, golf_score=35.0, wind_score=30.0,
            tide_score=15.0, swell_dir_bonus=10.0, confidence=1.0,
        )]
        window = SurfWindow(
            start=ts0, end=ts0, peak_score=90, median_score=90,
            peak_hour=ts0,
            triggers=[AlertType.SUSTAINED_GROUNDSWELL],
            stability=1.0, rarity_percentile=95.0,
            hourly_scores=scores, kind='surfable',
        )
        candidate = AlertCandidate(
            alert_type=AlertType.SUSTAINED_GROUNDSWELL,
            window=window,
            detection_time=datetime.now(),
            explanation='test',
            confidence=1.0,
        )

        def _fake_evaluate(forecast, history, buoy_history, windows, is_digest_time):
            return Decision(send_digest=False, send_alerts=[candidate])

        patched_system.notifier.send_alert.return_value = {
            'success': False, 'channel': 'mock', 'error': '5xx server error',
            'message': 'ALERT...',
        }
        patched_system.dry_run = False
        patched_system.alert_engine.evaluate_forecast = _fake_evaluate
        patched_system.sms_validator.validate_sms = MagicMock(
            return_value=ValidationResult(passed=True)
        )
        patched_system.sms_validator.validate_alert_format = MagicMock(
            return_value=ValidationResult(passed=True)
        )
        patched_system.sms_generator.generate_alert_sms = MagicMock(
            return_value='ALERT NWIJK 12:00 hs=1.0 score=90'
        )
        patched_system.sms_generator._prepare_alert_input = MagicMock(
            return_value={}
        )

        before_alerts = patched_system.alert_engine.state.alerts_sent_this_week
        _run_async(patched_system.run())

        # State is NIET bijgewerkt — ghost-cooldown vermeden.
        assert patched_system.alert_engine.state.alerts_sent_this_week == before_alerts
        assert patched_system.alert_engine.state.cooldown_until is None
        assert patched_system.alert_engine.state.last_alert_time is None


# ---------------------------------------------------------------------------
# Fix #4 — RunLog audit fields.
# ---------------------------------------------------------------------------

class TestRunLogAudit:
    """Bewijst dat de RunLog na een run alle audit-velden bevat."""

    def test_audit_fields_populated(self, patched_system, monkeypatch):
        baseline = _baseline_low_p70()
        from src import main as main_mod
        monkeypatch.setattr(
            main_mod.SeasonalBaselineBuilder,
            'load_baseline', lambda self: baseline,
        )
        run_log = _run_async(patched_system.run())
        assert run_log.seasonal_baseline_loaded is True
        assert run_log.rws_status in ('ok', 'partial', 'failed')
        assert run_log.openmeteo_status in ('ok', 'partial', 'failed')
        assert isinstance(run_log.bias_correction_applied, bool)
        assert isinstance(run_log.alert_types_detected, list)
        assert run_log.error is None

    def test_runlog_serializes_with_new_fields(self):
        """to_dict() bevat alle nieuwe audit-velden — JSON-trail volledig."""
        from src.data.models import RunLog
        rl = RunLog(
            timestamp=_utc(),
            run_type='manual',
            scores_today_peak=0,
            scores_tomorrow_peak=0,
            alert_types_detected=['T1'],
            windows_total=0,
            windows_alertworthy=0,
            decision='skip',
        )
        d = rl.to_dict()
        for key in (
            'bias_correction_applied', 'rws_status', 'openmeteo_status',
            'seasonal_baseline_loaded', 'llm_validation_passed',
            'llm_validation_issues', 'alert_types_detected',
        ):
            assert key in d, f"Audit-field {key!r} ontbreekt in to_dict()"
