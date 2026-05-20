"""
Unit tests voor Open-Meteo multi-model parsing.

Regressie voor B3: bij multi-model requests mag _key niet terugvallen
op de bare kolom-naam — anders resolven alle modellen naar dezelfde
data en wordt Sprint 2 #8 (wind-spread confidence) silently dood.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.sources.open_meteo import OpenMeteoClient


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _fake_multi_model_response() -> dict:
    """Open-Meteo response met drie modellen, allemaal suffixed."""
    times = [f"2026-05-20T{h:02d}:00" for h in range(0, 6)]
    return {
        "hourly": {
            "time": times,
            "wind_speed_10m_knmi_seamless":   [10, 11, 12, 13, 14, 15],
            "wind_direction_10m_knmi_seamless": [270, 270, 275, 280, 285, 290],
            "wind_gusts_10m_knmi_seamless":   [15, 16, 17, 18, 19, 20],
            "wind_speed_10m_ecmwf_ifs025":    [9,  10, 11, 13, 15, 16],
            "wind_direction_10m_ecmwf_ifs025": [260, 265, 270, 280, 290, 295],
            "wind_gusts_10m_ecmwf_ifs025":    [14, 15, 16, 18, 20, 21],
            "wind_speed_10m_gfs_seamless":    [11, 12, 13, 14, 14, 15],
            "wind_direction_10m_gfs_seamless": [280, 275, 270, 285, 280, 290],
            "wind_gusts_10m_gfs_seamless":    [16, 17, 18, 19, 19, 20],
        }
    }


def _fake_collapsed_response() -> dict:
    """
    Pathological response: één model (knmi) heeft suffixed keys, de andere
    twee niet. Voor de fix-pre regression: oude code zou alle drie naar
    de bare 'wind_speed_10m' laten resolven → identieke data.
    """
    times = [f"2026-05-20T{h:02d}:00" for h in range(0, 4)]
    return {
        "hourly": {
            "time": times,
            "wind_speed_10m":                 [10, 11, 12, 13],
            "wind_direction_10m":             [270, 275, 280, 285],
            "wind_gusts_10m":                 [15, 16, 17, 18],
            "wind_speed_10m_knmi_seamless":   [10, 11, 12, 13],
            "wind_direction_10m_knmi_seamless": [270, 275, 280, 285],
            "wind_gusts_10m_knmi_seamless":   [15, 16, 17, 18],
        }
    }


def _patch_client(monkeypatch, response: dict) -> OpenMeteoClient:
    oc = OpenMeteoClient()

    async def fake_get(url, params):
        return response

    monkeypatch.setattr(oc, '_request_with_retry', fake_get)
    return oc


class TestMultiModelParsing:
    def test_three_models_parsed_independently(self, monkeypatch):
        client = _patch_client(monkeypatch, _fake_multi_model_response())
        result = _run(client.fetch_forecast_data(
            lat=52.24, lon=4.42,
            models=['knmi_seamless', 'ecmwf_ifs025', 'gfs_seamless'],
        ))
        assert set(result.keys()) >= {'knmi_seamless', 'ecmwf_ifs025', 'gfs_seamless'}

        knmi_speeds = [r['wind_speed'] for r in result['knmi_seamless']]
        ecmwf_speeds = [r['wind_speed'] for r in result['ecmwf_ifs025']]
        gfs_speeds = [r['wind_speed'] for r in result['gfs_seamless']]

        # Drie series moeten ECHT verschillen — anders is _key alsnog
        # naar dezelfde kolom geresolved.
        assert knmi_speeds != ecmwf_speeds
        assert knmi_speeds != gfs_speeds
        assert ecmwf_speeds != gfs_speeds

    def test_collapsed_response_skips_unsuffixed_models(self, monkeypatch, caplog):
        """
        Als Open-Meteo voor ecmwf/gfs alleen bare keys retourneert, moeten
        die modellen NIET in result staan (geen silent collapse via bare-key
        fallback). Een warning moet gelogd worden.
        """
        client = _patch_client(monkeypatch, _fake_collapsed_response())
        with caplog.at_level(logging.WARNING):
            result = _run(client.fetch_forecast_data(
                lat=52.24, lon=4.42,
                models=['knmi_seamless', 'ecmwf_ifs025', 'gfs_seamless'],
            ))

        assert 'knmi_seamless' in result
        # ecmwf en gfs missen suffixed wind keys → moeten ge-skipt zijn.
        assert 'ecmwf_ifs025' not in result
        assert 'gfs_seamless' not in result

        msgs = ' '.join(rec.message for rec in caplog.records)
        assert 'ecmwf_ifs025' in msgs or 'gfs_seamless' in msgs

    def test_single_model_uses_bare_keys(self, monkeypatch):
        """Single-model request: bare keys zijn correct (geen suffix)."""
        client = _patch_client(monkeypatch, _fake_collapsed_response())
        result = _run(client.fetch_forecast_data(
            lat=52.24, lon=4.42,
            models=['knmi_seamless'],
        ))
        assert 'knmi_seamless' in result
        speeds = [r['wind_speed'] for r in result['knmi_seamless']]
        assert speeds == [10, 11, 12, 13]


# ---------------------------------------------------------------------------
# Tests voor uitgebreide forecast-velden (atmosferisch + stability)
# ---------------------------------------------------------------------------


def _fake_full_forecast_response() -> dict:
    """
    Response met ALLE nieuwe forecast-velden (per-model + stability).
    Twee modellen: knmi_seamless en ecmwf_ifs025.
    """
    times = [f"2026-05-20T{h:02d}:00" for h in range(0, 4)]
    return {
        "hourly": {
            "time": times,
            # Wind per model
            "wind_speed_10m_knmi_seamless":   [10, 11, 12, 13],
            "wind_direction_10m_knmi_seamless": [270, 275, 280, 285],
            "wind_gusts_10m_knmi_seamless":   [15, 16, 17, 18],
            "wind_speed_10m_ecmwf_ifs025":    [9, 10, 11, 12],
            "wind_direction_10m_ecmwf_ifs025": [265, 270, 275, 280],
            "wind_gusts_10m_ecmwf_ifs025":    [14, 15, 16, 17],
            # Bestaande overige meteo (per-model suffixed)
            "temperature_2m_knmi_seamless":   [15.0, 15.5, 16.0, 16.5],
            "temperature_2m_ecmwf_ifs025":    [14.5, 15.0, 15.5, 16.0],
            "precipitation_knmi_seamless":    [0.0, 0.0, 0.2, 0.5],
            "precipitation_ecmwf_ifs025":     [0.0, 0.1, 0.3, 0.6],
            "pressure_msl_knmi_seamless":     [1015, 1014, 1013, 1012],
            "pressure_msl_ecmwf_ifs025":      [1016, 1015, 1014, 1013],
            "cloud_cover_knmi_seamless":      [10, 20, 30, 40],
            "cloud_cover_ecmwf_ifs025":       [15, 25, 35, 45],
            # NIEUW: per-model atmospheric/display fields
            "apparent_temperature_knmi_seamless": [14.0, 14.5, 15.0, 15.5],
            "apparent_temperature_ecmwf_ifs025":  [13.5, 14.0, 14.5, 15.0],
            "relative_humidity_2m_knmi_seamless": [80, 78, 76, 74],
            "relative_humidity_2m_ecmwf_ifs025":  [82, 80, 78, 76],
            "dew_point_2m_knmi_seamless":     [11.0, 11.5, 11.8, 12.0],
            "dew_point_2m_ecmwf_ifs025":      [11.2, 11.6, 12.0, 12.3],
            "visibility_knmi_seamless":       [24140, 24140, 20000, 15000],
            "visibility_ecmwf_ifs025":        [24140, 22000, 18000, 13000],
            "weather_code_knmi_seamless":     [0, 1, 2, 3],
            "weather_code_ecmwf_ifs025":      [0, 1, 3, 45],
            "is_day_knmi_seamless":           [0, 0, 1, 1],
            "is_day_ecmwf_ifs025":            [0, 0, 1, 1],
            "uv_index_knmi_seamless":         [0.0, 0.0, 0.5, 1.2],
            "uv_index_ecmwf_ifs025":          [0.0, 0.0, 0.4, 1.0],
            "sunshine_duration_knmi_seamless": [0, 0, 1800, 3600],
            "sunshine_duration_ecmwf_ifs025":  [0, 0, 1500, 3000],
            # Stability fields (suffixed per primary model; we pakken eerste beschikbaar)
            "cape_knmi_seamless":             [100, 150, 200, 250],
            "lifted_index_knmi_seamless":     [2.0, 1.5, 1.0, 0.5],
            "convective_inhibition_knmi_seamless": [-50, -40, -30, -20],
            "boundary_layer_height_knmi_seamless": [800, 900, 1000, 1100],
        }
    }


def _fake_minimal_forecast_response() -> dict:
    """
    Response met alleen wind (essentieel) — alle NIEUWE velden ontbreken.
    Test dat fetch_forecast_data niet crasht en None retourneert voor missing.
    """
    times = [f"2026-05-20T{h:02d}:00" for h in range(0, 3)]
    return {
        "hourly": {
            "time": times,
            "wind_speed_10m_knmi_seamless":   [10, 11, 12],
            "wind_direction_10m_knmi_seamless": [270, 275, 280],
        }
    }


class TestForecastExtendedFields:
    def test_all_new_forecast_fields_parsed(self, monkeypatch):
        """Alle nieuwe per-model + stability velden komen door."""
        client = _patch_client(monkeypatch, _fake_full_forecast_response())
        result = _run(client.fetch_forecast_data(
            lat=52.24, lon=4.42,
            models=['knmi_seamless', 'ecmwf_ifs025'],
        ))
        assert 'knmi_seamless' in result
        assert 'ecmwf_ifs025' in result

        row = result['knmi_seamless'][2]
        # Bestaande velden blijven werken
        assert row['wind_speed'] == 12
        assert row['temperature'] == 16.0
        # Nieuwe per-model display/atmospheric fields
        assert row['apparent_temperature'] == 15.0
        assert row['relative_humidity'] == 76
        assert row['dew_point'] == 11.8
        assert row['visibility'] == 20000
        assert row['weather_code'] == 2
        assert row['is_day'] == 1
        assert row['uv_index'] == 0.5
        assert row['sunshine_duration'] == 1800
        # Stability fields (gedeeld over modellen)
        assert row['cape'] == 200
        assert row['lifted_index'] == 1.0
        assert row['convective_inhibition'] == -30
        assert row['boundary_layer_height'] == 1000

        # ECMWF row krijgt eigen per-model fields, maar GEDEELDE stability
        row2 = result['ecmwf_ifs025'][2]
        assert row2['apparent_temperature'] == 14.5
        assert row2['relative_humidity'] == 78
        # Stability is dezelfde bron (knmi_seamless suffix is enige beschikbaar)
        assert row2['cape'] == 200
        assert row2['lifted_index'] == 1.0

    def test_missing_new_fields_return_none(self, monkeypatch):
        """Ontbrekende nieuwe velden retourneren None, geen crash."""
        client = _patch_client(monkeypatch, _fake_minimal_forecast_response())
        result = _run(client.fetch_forecast_data(
            lat=52.24, lon=4.42,
            models=['knmi_seamless'],
        ))
        assert 'knmi_seamless' in result
        row = result['knmi_seamless'][0]
        # Wind moet werken
        assert row['wind_speed'] == 10
        # Alle nieuwe velden moeten None zijn (niet crashen, niet KeyError)
        for new_field in (
            'apparent_temperature', 'relative_humidity', 'dew_point',
            'visibility', 'weather_code', 'is_day', 'uv_index',
            'sunshine_duration', 'cape', 'lifted_index',
            'convective_inhibition', 'boundary_layer_height',
        ):
            assert new_field in row, f"Missing key in row: {new_field}"
            assert row[new_field] is None


# ---------------------------------------------------------------------------
# Tests voor uitgebreide marine-velden + DWD EWAM
# ---------------------------------------------------------------------------


def _fake_marine_response_full() -> dict:
    times = [f"2026-05-20T{h:02d}:00" for h in range(0, 3)]
    return {
        "hourly": {
            "time": times,
            "wave_height":             [1.0, 1.1, 1.2],
            "wave_direction":          [270, 275, 280],
            "wave_period":             [6.0, 6.5, 7.0],
            "wind_wave_height":        [0.5, 0.6, 0.7],
            "wind_wave_direction":     [270, 275, 280],
            "wind_wave_period":        [4.0, 4.2, 4.5],
            "wind_wave_peak_period":   [4.5, 4.7, 5.0],
            "swell_wave_height":       [0.8, 0.9, 1.0],
            "swell_wave_direction":    [280, 285, 290],
            "swell_wave_period":       [9.0, 9.5, 10.0],
            # NIEUW: ocean + sea-level fields
            "sea_surface_temperature": [12.5, 12.6, 12.7],
            "ocean_current_velocity":  [0.2, 0.25, 0.3],
            "ocean_current_direction": [45, 50, 55],
            "sea_level_height_msl":    [0.1, 0.3, 0.5],
            "invert_barometer_height": [0.0, 0.02, 0.04],
        }
    }


def _fake_marine_response_ewam() -> dict:
    """Marine multi-model: standaard ECMWAM (bare) + ewam (suffixed)."""
    times = [f"2026-05-20T{h:02d}:00" for h in range(0, 3)]
    return {
        "hourly": {
            "time": times,
            # Default ECMWAM (bare keys)
            "wave_height":             [1.0, 1.1, 1.2],
            "wave_direction":          [270, 275, 280],
            "wave_period":             [6.0, 6.5, 7.0],
            "wind_wave_height":        [0.5, 0.6, 0.7],
            "wind_wave_direction":     [270, 275, 280],
            "wind_wave_period":        [4.0, 4.2, 4.5],
            "wind_wave_peak_period":   [4.5, 4.7, 5.0],
            "swell_wave_height":       [0.8, 0.9, 1.0],
            "swell_wave_direction":    [280, 285, 290],
            "swell_wave_period":       [9.0, 9.5, 10.0],
            # DWD EWAM (suffixed keys, 5km Europese kust-resolutie)
            "wave_height_ewam":        [1.2, 1.3, 1.4],
            "wave_direction_ewam":     [275, 280, 285],
            "wave_period_ewam":        [6.5, 7.0, 7.5],
            "wind_wave_height_ewam":   [0.6, 0.7, 0.8],
            "wind_wave_direction_ewam": [272, 277, 282],
            "wind_wave_period_ewam":   [4.2, 4.5, 4.8],
            "wind_wave_peak_period_ewam": [4.7, 5.0, 5.3],
            "swell_wave_height_ewam":  [0.9, 1.0, 1.1],
            "swell_wave_direction_ewam": [282, 287, 292],
            "swell_wave_period_ewam":  [9.2, 9.8, 10.2],
            # Sea-surface temp (deelt model met default als bare)
            "sea_surface_temperature": [12.5, 12.6, 12.7],
        }
    }


class TestMarineExtendedFields:
    def test_all_new_marine_fields_parsed(self, monkeypatch):
        """SST, ocean current, sea-level fields komen door."""
        client = _patch_client(monkeypatch, _fake_marine_response_full())
        result = _run(client.fetch_marine_data(lat=52.24, lon=4.42))

        assert len(result) == 3
        row = result[1]
        # Bestaande velden
        assert row['wave_height'] == 1.1
        assert row['swell_wave_period'] == 9.5
        # Nieuwe velden
        assert row['sea_surface_temperature'] == 12.6
        assert row['ocean_current_velocity'] == 0.25
        assert row['ocean_current_direction'] == 50
        assert row['sea_level_height_msl'] == 0.3
        assert row['invert_barometer_height'] == 0.02

    def test_missing_marine_extra_fields_return_none(self, monkeypatch):
        """
        Wanneer Open-Meteo voor een locatie/tijd de extra velden NIET levert,
        moeten de keys aanwezig zijn met None — niet crashen.
        """
        minimal = {
            "hourly": {
                "time": ["2026-05-20T00:00", "2026-05-20T01:00"],
                "wave_height":      [1.0, 1.1],
                "wave_direction":   [270, 275],
                "wave_period":      [6.0, 6.5],
                "wind_wave_height": [0.5, 0.6],
                "wind_wave_direction": [270, 275],
                "wind_wave_period": [4.0, 4.2],
                "wind_wave_peak_period": [4.5, 4.7],
                "swell_wave_height": [0.8, 0.9],
                "swell_wave_direction": [280, 285],
                "swell_wave_period": [9.0, 9.5],
            }
        }
        client = _patch_client(monkeypatch, minimal)
        result = _run(client.fetch_marine_data(lat=52.24, lon=4.42))
        row = result[0]
        for new_field in (
            'sea_surface_temperature', 'ocean_current_velocity',
            'ocean_current_direction', 'sea_level_height_msl',
            'invert_barometer_height',
        ):
            assert new_field in row
            assert row[new_field] is None

    def test_dwd_ewam_multi_model(self, monkeypatch):
        """DWD EWAM model levert suffixed keys naast default ECMWAM."""
        client = _patch_client(monkeypatch, _fake_marine_response_ewam())
        result = _run(client.fetch_marine_data(
            lat=52.24, lon=4.42, models=['ewam']
        ))
        assert len(result) == 3
        row = result[1]
        # Default ECMWAM blijft op de bare keys staan
        assert row['wave_height'] == 1.1
        assert row['wave_period'] == 6.5
        # EWAM suffixed keys zijn ook aanwezig
        assert row['wave_height_ewam'] == 1.3
        assert row['wave_period_ewam'] == 7.0
        assert row['swell_wave_height_ewam'] == 1.0
        # Spread tussen modellen moet non-trivial zijn
        assert row['wave_height_ewam'] != row['wave_height']

    def test_fetch_marine_data_ewam_helper(self, monkeypatch):
        """Helper-functie levert dezelfde shape, met EWAM-suffixed keys."""
        client = _patch_client(monkeypatch, _fake_marine_response_ewam())
        result = _run(client.fetch_marine_data_ewam(lat=52.24, lon=4.42))
        assert len(result) == 3
        row = result[0]
        assert 'wave_height_ewam' in row
        assert row['wave_height_ewam'] == 1.2
