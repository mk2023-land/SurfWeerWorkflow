"""
Unit tests voor src/alerts/detectors.py — T1-T5 alert detectors.

Regressie voor B2: SwellArrivalDetector vergeleek `a12_data[0]` als
"~12 uur geleden", maar bij hours_back=48 met 10-min raster is dat
48u terug. De fix zoekt de spectrum die het dichtst bij (current - 12h)
ligt binnen ±2u tolerantie.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.alerts.detectors import SwellArrivalDetector
from src.alerts.engine import PRIMARY_ALERT_PRIORITY, select_primary_alert_type
from src.data.models import (
    AlertType,
    HourState,
    SpectralPeak,
    SwellType,
    TideState,
    WaveSpectrum,
    WindState,
)

_NOW = datetime(2026, 5, 20, 12, 0, 0)


def _make_spec(ts: datetime, period_s: float, height_m: float) -> WaveSpectrum:
    peak = SpectralPeak(
        frequency_mhz=1000 / period_s,
        period_s=period_s,
        height_m=height_m,
        direction_deg=300,
        type=SwellType.GROUND_SWELL if period_s >= 9 else SwellType.WIND_SEA,
    )
    return WaveSpectrum(
        timestamp=ts,
        significant_height_total=height_m,
        mean_period=period_s,
        mean_direction=300,
        peaks=[peak],
    )


def _current_state() -> HourState:
    return HourState(
        timestamp=_NOW,
        location_name="Noordwijk",
        wave_spectrum=_make_spec(_NOW, 9.0, 1.4),
        wind=WindState(speed_kn=10.0, direction_deg=100, gusts_kn=12.0),
        tide=TideState(
            level_m=0.0, phase="opgaand",
            next_high=_NOW + timedelta(hours=2),
            next_low=_NOW + timedelta(hours=8),
            daily_range_m=2.0,
        ),
        forecast_source="test",
        confidence=1.0,
    )


class TestSwellArrivalDetectorB2Fix:
    """
    Bevestigt dat de detector de juiste oude spectrum kiest (~12u terug)
    en niet de oudste in de buffer (mogelijk 48u terug).
    """

    def _buoy_history_48h_raster(self) -> dict:
        """
        Simuleer A12 buffer met 10-min raster over 48u (= 289 entries).
        Oudste (48u terug): stabiel klein wind-veld 0.6m@5s.
        12u terug: zelfde klein wind-veld 0.7m@5s (geen swell-arrival signaal).
        Nu: nieuwe groundswell 1.2m@9s.

        OUDE bug-code: vergeleek nu (1.2m@9s) met 48u-terug (0.6m@5s)
        → period_increase=4s, height_increase=100% → vals-positief T1 alert.

        NIEUWE fix-code: vergelijkt nu (1.2m@9s) met 12u-terug (0.7m@5s)
        → period_increase=4s, height_increase=71% → t1 alert legitiem.

        Voor de regressie willen we het SCENARIO waar 48u-terug en 12u-terug
        verschillen. Laat 48u-terug een hoge groundswell hebben (1.0m@8s),
        en 12u-terug een laag wind-veld (0.7m@5s). Nu = 1.2m@9s:
        - Oude bug: vergelijkt met 48u-terug groundswell → period_increase=1s
          (onder drempel 1.5s) → GEEN T1 (vals-negatief in dit geval).
        - Nieuwe fix: vergelijkt met 12u-terug wind → period_increase=4s,
          height_increase=71% → T1 detected.
        """
        spectra = []
        # 289 entries van 48u terug tot nu, raster 10 min
        for i in range(289):
            ts = _NOW - timedelta(minutes=10 * (288 - i))
            hours_back = (288 - i) * 10 / 60.0

            if hours_back >= 44:
                # 48u-44u terug: een eerdere groundswell die wegtrekt
                spec = _make_spec(ts, period_s=8.0, height_m=1.0)
            elif hours_back >= 2:
                # 44u-2u terug: stilte (laag wind-veld)
                spec = _make_spec(ts, period_s=5.0, height_m=0.7)
            else:
                # laatste 2u: groundswell arrival
                spec = _make_spec(ts, period_s=9.0, height_m=1.2)
            spectra.append(spec)
        return {"A12": spectra}

    def test_uses_12h_old_spectrum_not_oldest(self):
        detector = SwellArrivalDetector()
        history = self._buoy_history_48h_raster()
        result = detector.detect(
            history=[], current=_current_state(), buoy_history=history,
        )
        # Met de fix moet T1 vuren (vergelijking met 12u-terug wind-veld).
        # Met de oude bug zou het géén T1 zijn (vergelijking met 48u-terug groundswell).
        assert result is not None, \
            "T1 moet vuren wanneer 12u-terug wind was en nu groundswell aankomt"
        assert "period" in result.explanation.lower() or "swell" in result.explanation.lower()

    def test_no_history_returns_none(self):
        detector = SwellArrivalDetector()
        result = detector.detect(history=[], current=_current_state(), buoy_history={})
        assert result is None

    def test_insufficient_buffer_returns_none(self):
        detector = SwellArrivalDetector()
        # Slechts 1 entry → kan geen vergelijking maken
        history = {"A12": [_make_spec(_NOW, 9.0, 1.2)]}
        result = detector.detect(
            history=[], current=_current_state(), buoy_history=history,
        )
        assert result is None

    def test_buffer_only_too_old_returns_none(self):
        """
        Buffer bevat alleen spectra ouder dan 12h±2u (geen entry tussen
        9.5h en 14h terug). Fix moet None retourneren ipv toch te vergelijken
        met de oudste entry.
        """
        detector = SwellArrivalDetector()
        # Entries van 20u, 30u, 40u terug + nu
        spectra = [
            _make_spec(_NOW - timedelta(hours=40), 5.0, 0.7),
            _make_spec(_NOW - timedelta(hours=30), 5.0, 0.7),
            _make_spec(_NOW - timedelta(hours=20), 5.0, 0.7),
            _make_spec(_NOW, 9.0, 1.2),
        ]
        result = detector.detect(
            history=[], current=_current_state(), buoy_history={"A12": spectra},
        )
        assert result is None, "Zonder spectrum in 10-14h venster moet T1 None zijn"


class TestB9PrimaryAlertSelection:
    """
    B9 regressie: select_primary_alert_type kiest deterministisch via
    PRIMARY_ALERT_PRIORITY ipv arbitrair set.pop().
    """

    def test_empty_returns_none(self):
        assert select_primary_alert_type(set()) is None

    def test_single_type_returned(self):
        assert select_primary_alert_type({AlertType.WIND_DIP}) == AlertType.WIND_DIP

    def test_priority_order_t1_beats_t5(self):
        triggered = {AlertType.TIDE_GATED, AlertType.SWELL_ARRIVAL}
        assert select_primary_alert_type(triggered) == AlertType.SWELL_ARRIVAL

    def test_priority_order_t4_beats_t3(self):
        triggered = {AlertType.WIND_DIP, AlertType.SUSTAINED_GROUNDSWELL}
        assert select_primary_alert_type(triggered) == AlertType.SUSTAINED_GROUNDSWELL

    def test_deterministic_across_calls(self):
        """Identieke input → identieke output, ongeacht set-iteratie volgorde."""
        triggered = {AlertType.WIND_SHIFT, AlertType.TIDE_GATED, AlertType.WIND_DIP}
        results = [select_primary_alert_type(triggered) for _ in range(20)]
        assert len(set(results)) == 1, f"Niet-deterministische output: {results}"
        # T2 (WIND_SHIFT) heeft hoogste priority van de drie
        assert results[0] == AlertType.WIND_SHIFT

    def test_priority_covers_all_known_types(self):
        """Alle 5 AlertTypes moeten in de priority-lijst staan (regressie)."""
        assert set(PRIMARY_ALERT_PRIORITY) == set(AlertType)
