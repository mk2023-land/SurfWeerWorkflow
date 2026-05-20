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
