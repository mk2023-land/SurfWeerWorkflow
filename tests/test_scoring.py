"""
Unit tests voor scoring module.
Gebaseerd op validatieset uit het plan document.
"""
import pytest
from datetime import datetime
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.models import (
    WaveSpectrum,
    WindState,
    TideState,
    HourState,
    SpectralPeak,
    SwellType,
    ScoreBreakdown
)

from src.scoring.hourly import score_hour, score_golf_component, score_wind_component

from src.config import NOORDWIJK


class TestGolfScoring:
    """Test golf score component."""

    def test_low_wave_score(self):
        """Lage golf (<0.5m) geeft 0 punten."""
        spectrum = WaveSpectrum(
            timestamp=datetime.now(),
            significant_height_total=0.3,
            mean_period=5.0,
            mean_direction=270,
            peaks=[]
        )
        score = score_golf_component(spectrum)
        assert score == 0

    def test_medium_wave_score(self):
        """Medium golf (1.0m) geeft ~20 punten."""
        spectrum = WaveSpectrum(
            timestamp=datetime.now(),
            significant_height_total=1.0,
            mean_period=7.0,
            mean_direction=270,
            peaks=[]
        )
        score = score_golf_component(spectrum)
        assert 15 <= score <= 25

    def test_high_wave_score(self):
        """Hoge golf (1.5m groundswell) geeft hoge score."""
        groundswell_peak = SpectralPeak(
            frequency_mhz=100,  # 10s periode
            period_s=10.0,
            height_m=1.5,
            direction_deg=330,
            type=SwellType.GROUND_SWELL
        )

        spectrum = WaveSpectrum(
            timestamp=datetime.now(),
            significant_height_total=1.5,
            mean_period=10.0,
            mean_direction=330,
            peaks=[groundswell_peak]
        )
        score = score_golf_component(spectrum)
        assert score >= 35  # Groundswell bonus


class TestWindScoring:
    """Test wind score component."""

    def test_offshore_light_wind(self):
        """Offshore wind (<5kn) geeft maximale score."""
        score = score_wind_component(4, 90)  # O (offshore voor Noordwijk)
        assert score >= 30  # Met offshore bonus

    def test_onshore_strong_wind(self):
        """Onshore wind (>15kn) geeft lage score."""
        score = score_wind_component(18, 270)  # W (onshore)
        assert score <= 10

    def test_side_offshore_moderate(self):
        """Side-offshore wind (10kn) geeft medium score."""
        score = score_wind_component(10, 180)  # Z (side-offshore)
        assert 10 <= score <= 25


class TestTideScoring:
    """Test tij score component."""

    def test_mid_tide_score(self):
        """Mid-tijd geeft maximale score."""
        from src.scoring.hourly import score_tide_component
        score = score_tide_component(0.5, "opgaand")
        assert score >= 12

    def test_extreme_tide_score(self):
        """Extreem tij geeft lagere score."""
        from src.scoring.hourly import score_tide_component
        score = score_tide_component(0.1, "afgaand")
        assert score <= 8


class TestSwellDirectionBonus:
    """Test swell richting bonus."""

    def test_preferred_direction_bonus(self):
        """Voorkeursrichting (W-NNW) geeft maximale bonus."""
        from src.scoring.hourly import score_swell_direction_bonus
        score = score_swell_direction_bonus(300)  # WNW
        assert score == 10

    def test_blocked_direction_penalty(self):
        """Geblokkeerde richting (NNO) geeft 0 punten."""
        from src.scoring.hourly import score_swell_direction_bonus
        score = score_swell_direction_bonus(10)  # NNO
        assert score == 0

    def test_ok_direction_partial_bonus(self):
        """OK richting (ZW) geeft gedeeltelijke bonus."""
        from src.scoring.hourly import score_swell_direction_bonus
        score = score_swell_direction_bonus(240)  # ZW
        assert 3 <= score <= 7


class TestValidatieCases:
    """Test cases gebaseerd op validatietabel uit plan document."""

    def test_case_6_augustus_groundswell(self):
        """
        6-8-2025: groundswell alert (Type 4).
        1.4m swell op 100mhz (10s) door windgolven heen.
        Verwacht: score 75-85 ochtend.
        """
        # Groundswell piek (10s, 1.2m)
        groundswell_peak = SpectralPeak(
            frequency_mhz=100,
            period_s=10.0,
            height_m=1.2,
            direction_deg=330,
            type=SwellType.GROUND_SWELL
        )

        # Wind sea piek (5s, 0.4m)
        wind_sea_peak = SpectralPeak(
            frequency_mhz=200,
            period_s=5.0,
            height_m=0.4,
            direction_deg=270,
            type=SwellType.WIND_SEA
        )

        spectrum = WaveSpectrum(
            timestamp=datetime.now(),
            significant_height_total=1.4,
            mean_period=8.0,
            mean_direction=315,
            peaks=[groundswell_peak, wind_sea_peak]
        )

        wind = WindState(speed_kn=4, direction_deg=180)  # Z offshore

        hour_state = HourState(
            timestamp=datetime.now(),
            location_name="Noordwijk",
            wave_spectrum=spectrum,
            wind=wind,
            tide=TideState(
                level_m=0.5,
                phase="opgaand",
                next_low=datetime.now(),
                next_high=datetime.now()
            )
        )

        score = score_hour(hour_state)

        # Verwacht: score 75-85
        assert 75 <= score.total_score <= 85

    def test_case_16_mei_windstilte(self):
        """
        16-5-2026: windstilte window (Type 3 + Type 5).
        Verwacht: score 70-80 in smal window.
        """
        # Groundswell piek (9s, 0.9m)
        groundswell_peak = SpectralPeak(
            frequency_mhz=111,
            period_s=9.0,
            height_m=0.9,
            direction_deg=340,
            type=SwellType.GROUND_SWELL
        )

        spectrum = WaveSpectrum(
            timestamp=datetime.now(),
            significant_height_total=0.9,
            mean_period=9.0,
            mean_direction=340,
            peaks=[groundswell_peak]
        )

        wind = WindState(speed_kn=2, direction_deg=180)  # Z offshore, heel rustig

        hour_state = HourState(
            timestamp=datetime.now(),
            location_name="Noordwijk",
            wave_spectrum=spectrum,
            wind=wind,
            tide=TideState(
                level_m=0.6,
                phase="afgaand",
                next_low=datetime.now(),
                next_high=datetime.now()
            )
        )

        score = score_hour(hour_state)

        # Verwacht: score 70-80
        assert 70 <= score.total_score <= 80

    def test_case_flat_conditions(self):
        """
        9-9-2025: flat condities.
        Verwacht: score <15.
        """
        spectrum = WaveSpectrum(
            timestamp=datetime.now(),
            significant_height_total=0.3,
            mean_period=4.0,
            mean_direction=270,
            peaks=[]
        )

        wind = WindState(speed_kn=6, direction_deg=90)  # O

        hour_state = HourState(
            timestamp=datetime.now(),
            location_name="Noordwijk",
            wave_spectrum=spectrum,
            wind=wind,
            tide=TideState(
                level_m=0.2,
                phase="afgaand",
                next_low=datetime.now(),
                next_high=datetime.now()
            )
        )

        score = score_hour(hour_state)

        # Verwacht: score <15
        assert score.total_score < 15

    def test_case_blocked_swell(self):
        """
        NNO swell (geblokkeerd door IJmuiden pier).
        Verwacht: lagere score door richting penalty.
        """
        groundswell_peak = SpectralPeak(
            frequency_mhz=100,
            period_s=10.0,
            height_m=1.2,
            direction_deg=10,  # NNO - geblokkeerd
            type=SwellType.GROUND_SWELL
        )

        spectrum = WaveSpectrum(
            timestamp=datetime.now(),
            significant_height_total=1.2,
            mean_period=10.0,
            mean_direction=10,
            peaks=[groundswell_peak]
        )

        wind = WindState(speed_kn=4, direction_deg=180)  # Z offshore

        hour_state = HourState(
            timestamp=datetime.now(),
            location_name="Noordwijk",
            wave_spectrum=spectrum,
            wind=wind,
            tide=TideState(
                level_m=0.5,
                phase="opgaand",
                next_low=datetime.now(),
                next_high=datetime.now()
            )
        )

        score = score_hour(hour_state)

        # Verwacht: lagere score door richting penalty (zou rond 65-75 moeten zijn zonder richting penalty)
        assert score.swell_dir_bonus == 0  # Geen bonus voor NNO


if __name__ == "__main__":
    pytest.main([__file__, "-v"])