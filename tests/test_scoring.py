"""
Unit tests voor scoring module.
Gebaseerd op validatieset uit het plan document.
"""
import pytest
from datetime import datetime
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Deterministisch timestamp midden op de dag (zomertijd 11:00 NL = 09:00 UTC).
# Voorkomt flaky tests bij datetime.now() — score_hour past sinds blok 3 een
# daglicht-filter toe waardoor night-uren een 0-score krijgen.
_FIXED_TS = datetime(2025, 8, 6, 9, 0, 0)

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
            timestamp=_FIXED_TS,
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
            timestamp=_FIXED_TS,
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
            timestamp=_FIXED_TS,
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


class TestPeriodDependentTideWindow:
    """Test dat het optimale tij-venster afhangt van swell-periode (blok 2)."""

    def test_short_period_needs_higher_water(self):
        """Wind-sea (T<7s) bij laag tij scoort beduidend lager dan groundswell."""
        from src.scoring.hourly import score_tide_component
        short = score_tide_component(0.25, "opgaand", dominant_period_s=5.0)
        long = score_tide_component(0.25, "opgaand", dominant_period_s=10.0)
        # Bij norm=0.25: wind-sea venster begint pas bij 0.50, groundswell bij 0.20
        # → korte periode flink lager dan lange periode.
        assert short < long
        assert long >= 18  # Groundswell zit binnen venster
        assert short <= 12  # Wind-sea zit ver buiten venster

    def test_long_period_wider_window(self):
        """Groundswell krijgt vol level-score op niveaus waar wind-sea al daalt."""
        from src.scoring.hourly import score_tide_component
        # Norm 0.30: net buiten wind-sea venster [0.50, 0.90],
        # ruim binnen groundswell venster [0.20, 0.90].
        wind_sea = score_tide_component(0.30, "afgaand", dominant_period_s=5.0)
        groundswell = score_tide_component(0.30, "afgaand", dominant_period_s=10.0)
        assert groundswell > wind_sea
        assert groundswell == 18  # vol level, geen phase-bonus


class TestSpringNeapTideModulator:
    """Test springtij/doodtij modulator op het optimale venster (blok 2)."""

    def test_spring_tide_shrinks_window(self):
        """Springtij (≥2.0m range) maakt venster smaller → lagere edge-scores."""
        from src.scoring.hourly import score_tide_component
        # Norm 0.22 ligt net in groundswell venster [0.20, 0.90].
        # Springtij krimpt venster tot [0.25, 0.85] → 0.22 valt eruit.
        normal = score_tide_component(0.22, "afgaand", dominant_period_s=10.0, tide_range_m=1.8)
        spring = score_tide_component(0.22, "afgaand", dominant_period_s=10.0, tide_range_m=2.2)
        assert spring < normal
        assert normal == 18  # In normale venster

    def test_neap_tide_widens_window(self):
        """Doodtij (<1.6m range) verbreedt venster → hogere edge-scores."""
        from src.scoring.hourly import score_tide_component
        # Norm 0.19 ligt net buiten groundswell venster [0.20, 0.90].
        # Doodtij verbreedt naar [0.175, 0.925] → 0.19 valt erin.
        normal = score_tide_component(0.19, "afgaand", dominant_period_s=10.0, tide_range_m=1.8)
        neap = score_tide_component(0.19, "afgaand", dominant_period_s=10.0, tide_range_m=1.5)
        assert neap > normal
        assert neap == 18  # In verbreed venster


class TestTimingFitBonus:
    """Test timing-fit bonus (opgaand én 1-2.5u vóór HW) — blok 2."""

    def test_timing_bonus_at_edge_level(self):
        """Timing-fit voegt +1 toe wanneer level net buiten optimaal venster zit."""
        from src.scoring.hourly import score_tide_component
        # Norm 0.30 met T=8s zit net buiten venster [0.35, 0.85] →
        # level=14.57 + phase=2 = 16.57. Met timing-fit +1 = 17.57.
        no_timing = score_tide_component(0.30, "opgaand", dominant_period_s=8.0)
        with_timing = score_tide_component(0.30, "opgaand", dominant_period_s=8.0,
                                            hours_to_next_high=1.5)
        assert with_timing == no_timing + 1.0

    def test_timing_bonus_skipped_for_afgaand(self):
        """Timing-fit geldt alleen bij opgaand tij."""
        from src.scoring.hourly import score_tide_component
        no_timing = score_tide_component(0.30, "afgaand", dominant_period_s=8.0)
        with_timing = score_tide_component(0.30, "afgaand", dominant_period_s=8.0,
                                            hours_to_next_high=1.5)
        assert with_timing == no_timing

    def test_timing_bonus_outside_window(self):
        """Timing-fit alleen 1.0-2.5u vóór HW, niet eerder of later."""
        from src.scoring.hourly import score_tide_component
        base = score_tide_component(0.30, "opgaand", dominant_period_s=8.0)
        too_early = score_tide_component(0.30, "opgaand", dominant_period_s=8.0,
                                          hours_to_next_high=3.0)
        too_close = score_tide_component(0.30, "opgaand", dominant_period_s=8.0,
                                          hours_to_next_high=0.5)
        assert too_early == base
        assert too_close == base


class TestDaylightFilter:
    """Test dat night-uren in score_hour een 0-score krijgen (blok 3)."""

    def _make_state(self, ts: datetime):
        """Mini-helper: surfbaar weer met willekeurig tijdstip."""
        peak = SpectralPeak(
            frequency_mhz=100, period_s=10.0, height_m=1.2,
            direction_deg=300, type=SwellType.GROUND_SWELL
        )
        return HourState(
            timestamp=ts,
            location_name="Noordwijk",
            wave_spectrum=WaveSpectrum(
                timestamp=ts, significant_height_total=1.2,
                mean_period=10, mean_direction=300, peaks=[peak]
            ),
            wind=WindState(speed_kn=4, direction_deg=90),
            tide=TideState(
                level_m=0.5, phase="opgaand",
                next_low=ts, next_high=ts,
            ),
        )

    def test_summer_night_scores_zero(self):
        """Zomer 23:00 NL (na sunset + buffer) → night → score 0."""
        state = self._make_state(datetime(2025, 6, 21, 23, 0, 0))
        score = score_hour(state)
        assert score.total_score == 0

    def test_winter_early_morning_is_dark(self):
        """Winter 06:00 NL (zonsopgang pas ~08:50) → night → score 0."""
        state = self._make_state(datetime(2025, 12, 21, 6, 0, 0))
        score = score_hour(state)
        assert score.total_score == 0

    def test_summer_daytime_scores_normally(self):
        """Zomer 09:00 NL → vol daglicht → hoge score."""
        state = self._make_state(datetime(2025, 6, 21, 9, 0, 0))
        score = score_hour(state)
        assert score.total_score > 60

    def test_summer_dawn_4am_is_daylight(self):
        """Zomer 04:30 NL → dawn-buffer dekt dit, geldt als daglicht."""
        state = self._make_state(datetime(2025, 6, 21, 4, 30, 0))
        score = score_hour(state)
        assert score.total_score > 0  # daglicht → krijgt normale scoring

    def test_summer_3am_is_night(self):
        """Zomer 03:00 NL → vóór dawn-buffer → night → score 0."""
        state = self._make_state(datetime(2025, 6, 21, 3, 0, 0))
        score = score_hour(state)
        assert score.total_score == 0


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
        Verwacht: score 82-95 ochtend (v4 scoring: tij-cap omhoog naar 20 + opgaand
        krijgt phase-bonus i.p.v. afgaand, dus opgaand mid-tij groundswell scoort
        hoger dan in v3 baseline 75-85).
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
            timestamp=_FIXED_TS,
            significant_height_total=1.4,
            mean_period=8.0,
            mean_direction=315,
            peaks=[groundswell_peak, wind_sea_peak]
        )

        wind = WindState(speed_kn=4, direction_deg=180)  # Z offshore

        hour_state = HourState(
            timestamp=_FIXED_TS,
            location_name="Noordwijk",
            wave_spectrum=spectrum,
            wind=wind,
            tide=TideState(
                level_m=0.5,
                phase="opgaand",
                next_low=_FIXED_TS,
                next_high=_FIXED_TS
            )
        )

        score = score_hour(hour_state)

        # Verwacht: score 82-95 (v4 scoring met opgaand-tij bonus en tide_max=20)
        assert 82 <= score.total_score <= 95

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
            timestamp=_FIXED_TS,
            significant_height_total=0.9,
            mean_period=9.0,
            mean_direction=340,
            peaks=[groundswell_peak]
        )

        wind = WindState(speed_kn=2, direction_deg=180)  # Z offshore, heel rustig

        hour_state = HourState(
            timestamp=_FIXED_TS,
            location_name="Noordwijk",
            wave_spectrum=spectrum,
            wind=wind,
            tide=TideState(
                level_m=0.6,
                phase="afgaand",
                next_low=_FIXED_TS,
                next_high=_FIXED_TS
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
            timestamp=_FIXED_TS,
            significant_height_total=0.3,
            mean_period=4.0,
            mean_direction=270,
            peaks=[]
        )

        wind = WindState(speed_kn=6, direction_deg=90)  # O

        hour_state = HourState(
            timestamp=_FIXED_TS,
            location_name="Noordwijk",
            wave_spectrum=spectrum,
            wind=wind,
            tide=TideState(
                level_m=0.2,
                phase="afgaand",
                next_low=_FIXED_TS,
                next_high=_FIXED_TS
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
            timestamp=_FIXED_TS,
            significant_height_total=1.2,
            mean_period=10.0,
            mean_direction=10,
            peaks=[groundswell_peak]
        )

        wind = WindState(speed_kn=4, direction_deg=180)  # Z offshore

        hour_state = HourState(
            timestamp=_FIXED_TS,
            location_name="Noordwijk",
            wave_spectrum=spectrum,
            wind=wind,
            tide=TideState(
                level_m=0.5,
                phase="opgaand",
                next_low=_FIXED_TS,
                next_high=_FIXED_TS
            )
        )

        score = score_hour(hour_state)

        # Verwacht: lagere score door richting penalty (zou rond 65-75 moeten zijn zonder richting penalty)
        assert score.swell_dir_bonus == 0  # Geen bonus voor NNO


if __name__ == "__main__":
    pytest.main([__file__, "-v"])