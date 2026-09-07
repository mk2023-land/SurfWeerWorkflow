"""
Unit tests voor `SeasonalBaselineBuilder._process_archive_data`.

Golfdata komt sinds sept 2026 van de RWS IJG1-boei i.p.v. Open-Meteo's
marine-archief (bevestigd structureel kapot — levert overal 0 golf-uren).
Deze tests dekken de nieuwe nearest-timestamp merge tussen Open-Meteo
weer-uren en RWS-boeipunten:
  - Een boei-punt binnen ±30 min van een weer-uur wordt gematcht.
  - Een boei-punt buiten die tolerantie wordt genegeerd (uur overgeslagen).
  - Weer-uren zonder wind_speed/wind_direction worden overgeslagen.
  - Lege buoy_data levert een lege score-lijst (geen crash).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.baseline.seasonal import SeasonalBaselineBuilder


def _builder() -> SeasonalBaselineBuilder:
    # __init__ instantieert alleen clients (geen netwerk-IO), dus veilig
    # zonder mocking te construeren voor deze pure-logica tests.
    return SeasonalBaselineBuilder(years_back=1)


def _weather_row(dt: datetime, speed=15.0, direction=250) -> dict:
    return {
        'timestamp': dt,
        'wind_speed': speed,
        'wind_direction': direction,
        'temperature': 12.0,
    }


def _buoy_point(dt: datetime, height_m=1.5, period_s=7.0) -> dict:
    return {
        'timestamp': dt,
        'station': 'IJG1',
        'height_m': height_m,
        'period_s': period_s,
        'direction_deg': 0.0,
    }


class TestProcessArchiveData:
    def test_matches_buoy_point_within_tolerance(self):
        builder = _builder()
        weather_ts = datetime(2021, 3, 10, 12, 0, tzinfo=timezone.utc)
        buoy_ts = weather_ts + timedelta(minutes=20)  # binnen ±30 min

        archive_data = {'weather': [_weather_row(weather_ts)]}
        buoy_data = [_buoy_point(buoy_ts)]

        scores = builder._process_archive_data(archive_data, buoy_data)

        assert len(scores) == 1
        week_number, score = scores[0]
        assert week_number == weather_ts.isocalendar()[1]
        assert isinstance(score, (int, float))

    def test_skips_hour_without_nearby_buoy_point(self):
        builder = _builder()
        weather_ts = datetime(2021, 3, 10, 12, 0, tzinfo=timezone.utc)
        buoy_ts = weather_ts + timedelta(hours=2)  # ver buiten tolerantie

        archive_data = {'weather': [_weather_row(weather_ts)]}
        buoy_data = [_buoy_point(buoy_ts)]

        scores = builder._process_archive_data(archive_data, buoy_data)

        assert scores == []

    def test_skips_weather_row_with_missing_wind(self):
        builder = _builder()
        weather_ts = datetime(2021, 3, 10, 12, 0, tzinfo=timezone.utc)
        row = _weather_row(weather_ts)
        row['wind_speed'] = None

        archive_data = {'weather': [row]}
        buoy_data = [_buoy_point(weather_ts)]

        scores = builder._process_archive_data(archive_data, buoy_data)

        assert scores == []

    def test_empty_buoy_data_returns_no_scores(self):
        builder = _builder()
        weather_ts = datetime(2021, 3, 10, 12, 0, tzinfo=timezone.utc)
        archive_data = {'weather': [_weather_row(weather_ts)]}

        scores = builder._process_archive_data(archive_data, [])

        assert scores == []

    def test_skips_night_hour_even_with_good_buoy_match(self):
        """
        Nacht-uren mogen NIET meetellen in de baseline-percentiel-populatie,
        ook al is er een prima boei-match. `score_hour` zou toch 0 teruggeven
        (eigen daglicht-gate), en zonder deze filter zou die kunstmatige nul
        de percentielen omlaag trekken (gevonden bij de eerste echte rebuild,
        sept 2026 — zie module-docstring bij _process_archive_data).
        """
        builder = _builder()
        # 03:00 UTC = 04:00 Amsterdam-wintertijd — diep in de nacht, ongeacht
        # jaargetijde.
        night_ts = datetime(2021, 1, 10, 3, 0, tzinfo=timezone.utc)

        archive_data = {'weather': [_weather_row(night_ts)]}
        buoy_data = [_buoy_point(night_ts)]

        scores = builder._process_archive_data(archive_data, buoy_data)

        assert scores == []

    def test_picks_nearest_of_multiple_candidates(self):
        """Bij meerdere boei-punten binnen bereik moet het dichtstbijzijnde
        (niet het eerste of laatste) gekozen worden."""
        builder = _builder()
        weather_ts = datetime(2021, 3, 10, 12, 0, tzinfo=timezone.utc)
        far = _buoy_point(weather_ts - timedelta(minutes=25), height_m=9.0)
        near = _buoy_point(weather_ts + timedelta(minutes=5), height_m=1.2)

        archive_data = {'weather': [_weather_row(weather_ts)]}
        buoy_data = [far, near]

        scores = builder._process_archive_data(archive_data, buoy_data)

        assert len(scores) == 1
        # Impliciete check via score: 1.2m geeft een andere (lagere) score
        # dan 9.0m — we verifiëren indirect dat `near` is gebruikt door te
        # vergelijken met een run die alleen `far` bevat.
        only_far_scores = builder._process_archive_data(
            {'weather': [_weather_row(weather_ts)]}, [far],
        )
        assert scores[0][1] != only_far_scores[0][1]
