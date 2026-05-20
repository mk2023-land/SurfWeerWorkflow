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
        """Wind-sea (T<7s) bij laag tij scoort lager dan groundswell."""
        from src.scoring.hourly import score_tide_component
        short = score_tide_component(0.25, "opgaand", dominant_period_s=5.0)
        long = score_tide_component(0.25, "opgaand", dominant_period_s=10.0)
        # Bij norm=0.25: wind-sea venster begint pas bij 0.35 (versoepeld na
        # referentie-forecaster-benchmark waarin LW-kentering ook surfbaar bleek), groundswell
        # bij 0.20. Korte periode nog steeds lager dan lange periode.
        assert short < long
        assert long >= 18  # Groundswell zit binnen venster
        assert short <= 16  # Wind-sea net buiten venster (was 12 bij oude lo=0.50)

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
        """Timing-fit voegt +1 toe wanneer level binnen window zit en niet capt."""
        from src.scoring.hourly import score_tide_component
        # Norm 0.25 met T=8s zit net buiten venster [0.30, 0.85] →
        # level wordt < 18, dus timing-bonus +1 levert echt iets op (cap = 20).
        no_timing = score_tide_component(0.25, "opgaand", dominant_period_s=8.0)
        with_timing = score_tide_component(0.25, "opgaand", dominant_period_s=8.0,
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


class TestPeakBlock:
    """Test peak_block helper: range van top-uren binnen een SurfWindow."""

    def _make_window(self, score_sequence):
        """Bouw een SurfWindow met de gegeven uurlijkse total-scores."""
        from src.data.models import SurfWindow, ScoreBreakdown
        from datetime import timedelta
        start_ts = datetime(2025, 8, 6, 12, 0, 0)
        breakdowns = []
        for i, total in enumerate(score_sequence):
            # Verdeel score-totaal over componenten zodat total_score == target.
            # Simplest: zet golf=total (cap 38 → wat hoger schaalt naar 0).
            ts = start_ts + timedelta(hours=i)
            breakdowns.append(ScoreBreakdown(
                timestamp=ts, golf_score=float(total),
                wind_score=0.0, tide_score=0.0, swell_dir_bonus=0.0,
            ))
        peak_idx = max(range(len(breakdowns)), key=lambda i: breakdowns[i].total_score)
        return SurfWindow(
            start=breakdowns[0].timestamp,
            end=breakdowns[-1].timestamp,
            peak_score=int(max(score_sequence)),
            median_score=int(score_sequence[len(score_sequence) // 2]),
            peak_hour=breakdowns[peak_idx].timestamp,
            triggers=[], stability=0.9, rarity_percentile=80,
            hourly_scores=breakdowns,
        )

    def test_peak_block_contracts_to_high_score_range(self):
        """Window 12-18u met scores [62,75,85,80,65,60] → peak_block 13-15u (binnen 10pt van piek 85)."""
        from src.llm.generator import peak_block
        window = self._make_window([62, 75, 85, 80, 65, 60])
        block = peak_block(window)
        assert block["start_time"] == "13:00"
        assert block["end_time"] == "15:00"
        assert block["duration_hours"] == 3

    def test_peak_block_full_window_when_flat(self):
        """Bij vlakke scores (alle binnen 10pt) is peak_block het hele window."""
        from src.llm.generator import peak_block
        window = self._make_window([78, 80, 82, 79, 81])
        block = peak_block(window)
        assert block["start_time"] == "12:00"
        assert block["end_time"] == "16:00"
        assert block["duration_hours"] == 5

    def test_peak_block_single_hour_when_sharp_peak(self):
        """Scherpe piek (één uur ver boven de rest) levert 1-uurs peak_block op."""
        from src.llm.generator import peak_block
        window = self._make_window([62, 64, 95, 63, 61])
        block = peak_block(window)
        assert block["start_time"] == "14:00"
        assert block["end_time"] == "14:00"
        assert block["duration_hours"] == 1


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

    def test_summer_pre_dawn_5am_in_may_is_night(self):
        """Mei 05:00 NL → vóór zonsopgang (~05:47) + 0.5u civil-twilight buffer → night.

        Dit was eerder een bug: een 1.5u morning buffer liet 05:00 in mei als
        surfbaar door, waarna de LLM pre-dawn uren als 'piek' presenteerde.
        """
        state = self._make_state(datetime(2026, 5, 20, 5, 0, 0))
        score = score_hour(state)
        assert score.total_score == 0

    def test_summer_dawn_5am_in_june_is_daylight(self):
        """Juni 05:00 NL → zonsopgang ~05:20 lokaal, dus 05:00 valt binnen civil twilight (-0.5u)."""
        state = self._make_state(datetime(2025, 6, 21, 5, 0, 0))
        score = score_hour(state)
        assert score.total_score > 0

    def test_summer_3am_is_night(self):
        """Zomer 03:00 NL → ver vóór civil twilight → night → score 0."""
        state = self._make_state(datetime(2025, 6, 21, 3, 0, 0))
        score = score_hour(state)
        assert score.total_score == 0


class TestSwellDirectionBonus:
    """Test swell richting bonus."""

    def test_preferred_direction_bonus(self):
        """Voorkeursrichting (W-NNW) geeft (vrijwel) maximale bonus."""
        from src.scoring.hourly import score_swell_direction_bonus
        score = score_swell_direction_bonus(300)  # WNW
        # Sprint 2 #9: continue refractie geeft 99.9...% transmissie ver
        # weg van shadow center, niet exact 10. Tolerantie 0.1pt.
        assert 9.8 <= score <= 10.01

    def test_blocked_direction_penalty(self):
        """Geblokkeerde richting (NNO) geeft sterk gereduceerde bonus."""
        from src.scoring.hourly import score_swell_direction_bonus
        # Sprint 2 #9: pier-shadow center op 10°, met sigmoid-curve. Bij
        # exact 10° NNO komt slechts ~10% transmissie door → raw richting-
        # bonus 5pt × 0.10 ≈ 0.5pt. Geen harde 0 meer.
        score = score_swell_direction_bonus(10)  # NNO
        assert 0.0 <= score <= 1.5

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

        # Sprint 2 #13: multiplicatieve aggregation cap. Met golf_score ~38
        # (cap) en env_score ~57 → multiplicative ~38 × 1.92 ≈ 73-78,
        # additive ~96. Min van beide is ~73-78. Dit blijft ruim boven
        # de surfable-threshold van 60 en is duidelijk ALERT-waardig.
        # Verschuiving t.o.v. Sprint 1 is bewust: industry-consensus eist
        # dat de score niet te ver boven golf_max + reasonable environment
        # bonus uitkomt.
        assert 70 <= score.total_score <= 100

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

        # Sprint 2 #13: hard size-cap reduceert deze score t.o.v. Sprint 1
        # baseline (was 70-80). 0.9m golf op 9s = matige groundswell met
        # perfect environment → multiplicatief plafond ~60-70. Dit is nog
        # steeds boven longboard-threshold (42), maar onder shortboard-
        # surfable (60). Past bij referentie-forecaster' "smal-alert" karakter: het is
        # NIET een dichte-bank "alles werkt" dag, het is een 1-2u
        # rustige-conditie longboard-window.
        assert 55 <= score.total_score <= 75

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

        # Sprint 2 #9: continue refractie ipv binaire blocked sector. Bij
        # 10° NNO (shadow center) blijft <15% transmissie over, dus bonus
        # is sterk gereduceerd maar niet exact 0. Met Tp=10s long-period
        # bonus zit het iets hoger maar nog steeds onder 1.5pt.
        assert score.swell_dir_bonus < 1.5  # Sterk gereduceerde bonus voor NNO


class TestSprint2PartitionAwareScoring:
    """Sprint 2 #10 — partition-aware scoring (swell + wind-sea apart wegen)."""

    def _state(self, peaks, hs_total, wind_speed=4, wind_dir=180, tide_phase="opgaand", tide_level=0.5):
        spectrum = WaveSpectrum(
            timestamp=_FIXED_TS,
            significant_height_total=hs_total,
            mean_period=peaks[0].period_s if peaks else 5.0,
            mean_direction=peaks[0].direction_deg if peaks else 270,
            peaks=peaks,
        )
        return HourState(
            timestamp=_FIXED_TS,
            location_name="Noordwijk",
            wave_spectrum=spectrum,
            wind=WindState(speed_kn=wind_speed, direction_deg=wind_dir),
            tide=TideState(level_m=tide_level, phase=tide_phase,
                           next_low=_FIXED_TS, next_high=_FIXED_TS),
        )

    def test_secondary_swell_lifts_score_over_pure_wind_sea(self):
        """1.0m wind-chop ALLEEN scoort lager dan 1.0m wind-chop + 0.5m NW swell."""
        from src.scoring.hourly import score_golf_component

        wind_only = WaveSpectrum(
            timestamp=_FIXED_TS, significant_height_total=1.0,
            mean_period=4.5, mean_direction=270,
            peaks=[SpectralPeak(frequency_mhz=222, period_s=4.5, height_m=1.0,
                                direction_deg=270, type=SwellType.WIND_SEA)],
        )
        with_swell = WaveSpectrum(
            timestamp=_FIXED_TS, significant_height_total=1.118,  # sqrt(1+0.25)
            mean_period=5.5, mean_direction=290,
            peaks=[
                SpectralPeak(frequency_mhz=222, period_s=4.5, height_m=1.0,
                             direction_deg=270, type=SwellType.WIND_SEA),
                SpectralPeak(frequency_mhz=125, period_s=8.0, height_m=0.5,
                             direction_deg=300, type=SwellType.WIND_SWELL),
            ],
        )
        s1 = score_golf_component(wind_only)
        s2 = score_golf_component(with_swell)
        assert s2 > s1, f"Secondary swell should lift score: {s1} → {s2}"

    def test_pure_swell_outscores_equal_height_wind_chop(self):
        """0.8m clean groundswell (10s) scoort hoger dan 0.8m wind-chop (4s)."""
        from src.scoring.hourly import score_golf_component

        chop = WaveSpectrum(
            timestamp=_FIXED_TS, significant_height_total=0.8,
            mean_period=4.0, mean_direction=270,
            peaks=[SpectralPeak(frequency_mhz=250, period_s=4.0, height_m=0.8,
                                direction_deg=270, type=SwellType.WIND_SEA)],
        )
        gs = WaveSpectrum(
            timestamp=_FIXED_TS, significant_height_total=0.8,
            mean_period=10.0, mean_direction=300,
            peaks=[SpectralPeak(frequency_mhz=100, period_s=10.0, height_m=0.8,
                                direction_deg=300, type=SwellType.GROUND_SWELL)],
        )
        assert score_golf_component(gs) > score_golf_component(chop)


class TestSprint2ContinuousRefraction:
    """Sprint 2 #9 — continue pier-refractie ipv binaire knip."""

    def test_long_period_refracts_better_than_short(self):
        """N-swell (0°) op 10s krijgt hogere bonus dan zelfde richting op 5s."""
        from src.scoring.hourly import score_swell_direction_bonus
        short = score_swell_direction_bonus(0, period_s=5.0)
        long = score_swell_direction_bonus(0, period_s=10.0)
        assert long > short, f"Long-period refractie-bonus moet groter: {short} vs {long}"

    def test_shadow_center_strongly_reduced(self):
        """Bij exact 10° NNO (shadow center) komt slechts ~10% transmissie door."""
        from src.scoring.hourly import pier_transmission_factor
        t = pier_transmission_factor(10, period_s=6.0)
        assert 0.05 <= t <= 0.20, f"Shadow center transmission moet ~10%: {t}"

    def test_far_from_shadow_full_transmission(self):
        """45° NO is buiten shadow → ~100% transmissie."""
        from src.scoring.hourly import pier_transmission_factor
        t = pier_transmission_factor(45, period_s=6.0)
        assert t > 0.95

    def test_continuous_transition(self):
        """Transmissie stijgt monotoon bij wegbewegen van shadow center."""
        from src.scoring.hourly import pier_transmission_factor
        prev = pier_transmission_factor(10, period_s=7.0)
        for offset in [15, 20, 25, 30, 40, 50]:
            curr = pier_transmission_factor(10 + offset, period_s=7.0)
            assert curr > prev, f"Niet monotoon bij offset {offset}: {prev} → {curr}"
            prev = curr


class TestSprint2WindSpreadConfidence:
    """Sprint 2 #8 — multi-model wind-spread confidence-penalty."""

    def test_low_spread_no_penalty(self):
        from src.scoring.hourly import wind_spread_confidence
        assert wind_spread_confidence(2.0, 10.0) == 1.0

    def test_high_speed_spread_triggers_penalty(self):
        from src.scoring.hourly import wind_spread_confidence
        f = wind_spread_confidence(12.0, 0.0)
        assert 0.84 <= f <= 0.86, f"Max penalty bij 12kn spread → ~0.85: {f}"

    def test_intermediate_spread_partial_penalty(self):
        from src.scoring.hourly import wind_spread_confidence
        f = wind_spread_confidence(8.0, 0.0)
        assert 0.90 <= f <= 0.95

    def test_direction_spread_alone_can_trigger(self):
        from src.scoring.hourly import wind_spread_confidence
        f_low = wind_spread_confidence(0.0, 10.0)
        f_high = wind_spread_confidence(0.0, 60.0)
        assert f_low == 1.0
        assert f_high < 0.90

    def test_angular_spread_360_wrap(self):
        from src.scoring.hourly import angular_spread_deg
        # Twee modellen rond N: 5° en 355° zijn maar 10° uit elkaar
        spread = angular_spread_deg([5, 355])
        assert spread < 15, f"Wrap-around spread moet klein zijn: {spread}"


class TestSprint2DiurnalWindDecay:
    """Sprint 2 #12 — diurnal wind-decay rond zonsondergang."""

    def test_clear_sky_evening_reduces_wind(self):
        """Lage bewolking 1u vóór sunset → wind-reductie."""
        from src.scoring.hourly import diurnal_wind_decay_kn
        # Sunset NL juni rond 21:00 lokaal = 19:00 UTC. 1u voor = 18:00 UTC = 20:00 lokaal.
        evening = datetime(2025, 6, 21, 20, 0, 0)
        effective = diurnal_wind_decay_kn(evening, 10.0, cloud_cover_pct=20.0)
        assert effective < 10.0, f"Wind moet zakken bij lage bewolking: {effective}"

    def test_cloudy_no_decay(self):
        """Hoge bewolking → geen diurnal effect."""
        from src.scoring.hourly import diurnal_wind_decay_kn
        evening = datetime(2025, 6, 21, 20, 0, 0)
        effective = diurnal_wind_decay_kn(evening, 10.0, cloud_cover_pct=80.0)
        assert effective == 10.0

    def test_outside_window_no_decay(self):
        """Middag (ver vóór sunset) → geen effect."""
        from src.scoring.hourly import diurnal_wind_decay_kn
        midday = datetime(2025, 6, 21, 14, 0, 0)
        effective = diurnal_wind_decay_kn(midday, 10.0, cloud_cover_pct=20.0)
        assert effective == 10.0


class TestSprint2TideFlankBonus:
    """Sprint 2 #11 — mid-tide flank bonus."""

    def test_mid_rising_gets_full_bonus(self):
        from src.scoring.hourly import tide_flank_bonus
        assert tide_flank_bonus(0.5, is_rising=True) == 2.0

    def test_mid_falling_gets_half_bonus(self):
        from src.scoring.hourly import tide_flank_bonus
        assert tide_flank_bonus(0.5, is_rising=False) == 1.0

    def test_outside_mid_no_bonus(self):
        from src.scoring.hourly import tide_flank_bonus
        assert tide_flank_bonus(0.1, is_rising=True) == 0.0
        assert tide_flank_bonus(0.9, is_rising=True) == 0.0


class TestSprint2SizeCap:
    """Sprint 2 #13 — hard size-cap via multiplicatieve aggregation."""

    def test_marginal_wave_cannot_reach_surfable_via_environment(self):
        """0.4m golf (~3-5pt) + perfect environment mag GEEN 60+ score halen."""
        from src.data.models import ScoreBreakdown
        from datetime import datetime
        sb = ScoreBreakdown(
            timestamp=datetime(2025, 8, 6, 12, 0),
            golf_score=5.0,
            wind_score=32.0,  # max
            tide_score=20.0,  # max
            swell_dir_bonus=10.0,  # max
        )
        # Additief: 67. Multiplicatief: 5 × (1 + 2.5 × 1.0) = 17.5.
        # Min = 17.5. Onder surfable=60 ✓
        assert sb.total_score < 60.0
        assert sb.total_score < 20.0

    def test_big_wave_with_modest_environment_uses_additive(self):
        """30pt golf + matige environment → additieve uitkomst, niet multiplicatief gecapt."""
        from src.data.models import ScoreBreakdown
        sb = ScoreBreakdown(
            timestamp=_FIXED_TS,
            golf_score=30.0,
            wind_score=10.0,
            tide_score=10.0,
            swell_dir_bonus=5.0,
        )
        # Additief: 55. Multiplicatief: 30 × (1 + 2.5 × 25/62) = 30 × 2.008 = 60.2.
        # Min = 55 (additief). De multiplicative cap is NIET de bottleneck hier.
        assert sb.total_score == 55.0


class TestSprint2WindSpreadInScoring:
    """Sprint 2 #8 — wind-spread doorgegeven via context werkt in score_hour."""

    def _make_state(self):
        peak = SpectralPeak(
            frequency_mhz=100, period_s=10.0, height_m=1.2,
            direction_deg=300, type=SwellType.GROUND_SWELL
        )
        return HourState(
            timestamp=_FIXED_TS,
            location_name="Noordwijk",
            wave_spectrum=WaveSpectrum(
                timestamp=_FIXED_TS, significant_height_total=1.2,
                mean_period=10, mean_direction=300, peaks=[peak]
            ),
            wind=WindState(speed_kn=8, direction_deg=180),
            tide=TideState(level_m=0.5, phase="opgaand",
                           next_low=_FIXED_TS, next_high=_FIXED_TS),
        )

    def test_high_spread_reduces_score(self):
        """Hoge spread (12kn std) → golf_score multiplier ~0.85."""
        from src.scoring.hourly import score_hour
        baseline = score_hour(self._make_state())
        with_spread = score_hour(
            self._make_state(),
            context={'wind_spread': {'speed_std_kn': 12.0, 'direction_spread_deg': 0}}
        )
        assert with_spread.golf_score < baseline.golf_score
        # Multiplier minimum 0.85; bij golf_score ~38: aftrek ~5.7pt
        ratio = with_spread.golf_score / baseline.golf_score
        assert 0.83 <= ratio <= 0.87

    def test_zero_spread_no_effect(self):
        """Lage spread → geen effect op golf_score."""
        from src.scoring.hourly import score_hour
        baseline = score_hour(self._make_state())
        with_spread = score_hour(
            self._make_state(),
            context={'wind_spread': {'speed_std_kn': 1.0, 'direction_spread_deg': 5.0}}
        )
        assert with_spread.golf_score == baseline.golf_score


if __name__ == "__main__":
    pytest.main([__file__, "-v"])