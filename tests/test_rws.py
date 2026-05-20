"""
Unit tests voor de RWS DDAPI20-uitbreiding (extra Aquo-grootheden + surge).

Mocked POST-responses op het `_post`-niveau, zodat alle code-paden door
de echte `_fetch_series`-parser heen lopen. Dat is belangrijk: een bug
in de parser (verkeerde Aquo-code mapping, lege MetingenLijst, etc.) wordt
zo gevangen.

Dekt:
  - Nieuwe grootheden komen binnen op IJG1 (Tp, SObh, Hmax, LUCHTDK, LUCHTTPR).
  - Tp/Tp001 fallback wanneer Tp leeg is.
  - Graceful degradation: één grootheid 503 breekt de boei niet.
  - LUCHTDK/LUCHTTPR alleen voor IJG1, niet voor A12/K13.
  - Surge residual = measured - astronomical, en degradeert gracieus.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.sources.rws import (  # noqa: E402
    GROOTHEID_HM0,
    GROOTHEID_HMAX,
    GROOTHEID_LUCHTDK,
    GROOTHEID_LUCHTTPR,
    GROOTHEID_SOBH,
    GROOTHEID_TH0,
    GROOTHEID_TM02,
    GROOTHEID_TP,
    GROOTHEID_TP_FALLBACK,
    GROOTHEID_WATHTBRKD,
    GROOTHEID_WATHTE,
    RWSClient,
)
import src.data.sources.rws as rws_mod  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _ts(offset_min: int) -> str:
    """RWS-style ISO timestamp, basis 2026-05-20T12:00+00:00."""
    base = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    return (base + timedelta(minutes=offset_min)).isoformat(timespec='milliseconds')


def _series(grootheid: str, eenheid: str, values: List[float]) -> Dict[str, Any]:
    """Bouw een RWS DDAPI20-response voor één grootheid."""
    return {
        'Succesvol': True,
        'WaarnemingenLijst': [{
            'AquoMetadata': {
                'Grootheid': {'Code': grootheid},
                'Eenheid': {'Code': eenheid},
            },
            'MetingenLijst': [
                {
                    'Tijdstip': _ts(i * 10),
                    'Meetwaarde': {'Waarde_Numeriek': v},
                }
                for i, v in enumerate(values)
            ],
        }],
    }


def _empty_response() -> Dict[str, Any]:
    return {'Succesvol': True, 'WaarnemingenLijst': []}


class _FakeRouter:
    """
    Router die op basis van de Aquo-code in de POST body
    een gemockte response retourneert. Aquo-codes zonder mapping
    krijgen optioneel een httpx.HTTPError (om 503/404 te simuleren).
    """

    def __init__(self):
        self.responses: Dict[str, Any] = {}
        self.errors: Dict[str, Exception] = {}
        self.calls: List[str] = []

    def add(self, grootheid: str, response: Dict[str, Any]):
        self.responses[grootheid] = response

    def fail(self, grootheid: str, exc: Exception):
        self.errors[grootheid] = exc

    def __call__(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        aquo = body['AquoPlusWaarnemingMetadata']['AquoMetadata']
        code = aquo['Grootheid']['Code']
        self.calls.append(code)
        if code in self.errors:
            raise self.errors[code]
        return self.responses.get(code, _empty_response())


def _patch_client(monkeypatch, router: _FakeRouter) -> RWSClient:
    client = RWSClient()

    async def fake_post(url, body):
        return router(url, body)

    monkeypatch.setattr(client, '_post', fake_post)
    return client


def _ijg1_full_router() -> _FakeRouter:
    """Router met realistische data voor alle IJG1-grootheden."""
    r = _FakeRouter()
    r.add(GROOTHEID_HM0, _series(GROOTHEID_HM0, 'cm', [120.0, 130.0, 140.0]))
    r.add(GROOTHEID_TM02, _series(GROOTHEID_TM02, 's', [5.8, 6.0, 6.2]))
    r.add(GROOTHEID_TH0, _series(GROOTHEID_TH0, 'graad', [280.0, 282.0, 285.0]))
    r.add(GROOTHEID_TP, _series(GROOTHEID_TP, 's', [6.4, 6.6, 6.8]))
    r.add(GROOTHEID_SOBH, _series(GROOTHEID_SOBH, 'graad', [22.0, 24.0, 26.0]))
    r.add(GROOTHEID_HMAX, _series(GROOTHEID_HMAX, 'cm', [190.0, 200.0, 220.0]))
    r.add(GROOTHEID_LUCHTDK, _series(GROOTHEID_LUCHTDK, 'hPa', [1015.0, 1015.2, 1015.4]))
    r.add(GROOTHEID_LUCHTTPR, _series(GROOTHEID_LUCHTTPR, 'oC', [12.0, 12.2, 12.4]))
    return r


class TestExtendedBuoyData:
    def test_ijg1_returns_all_new_grootheden(self, monkeypatch):
        """Alle 5 extra grootheden moeten als dict-keys verschijnen op elk punt."""
        client = _patch_client(monkeypatch, _ijg1_full_router())
        data = _run(client.fetch_buoy_data('IJG1', hours_back=1))

        assert len(data) == 3
        first = data[0]
        # Bestaande backward-compatible velden.
        assert 'timestamp' in first
        assert first['height_m'] == pytest.approx(1.20)
        assert first['period_s'] == pytest.approx(5.8)
        assert first['direction_deg'] == pytest.approx(280.0)
        # Nieuwe velden.
        assert first['tp_s'] == pytest.approx(6.4)
        assert first['sobh_deg'] == pytest.approx(22.0)
        assert first['hmax_m'] == pytest.approx(1.90)  # 190 cm → 1.9 m
        assert first['pressure_hpa'] == pytest.approx(1015.0)
        assert first['air_temp_c'] == pytest.approx(12.0)

    def test_tp_fallback_to_tp001(self, monkeypatch):
        """Als Tp leeg is, moet de client Tp001 als fallback proberen."""
        r = _ijg1_full_router()
        # Tp niets, Tp001 wel.
        r.add(GROOTHEID_TP, _empty_response())
        r.add(GROOTHEID_TP_FALLBACK, _series(GROOTHEID_TP_FALLBACK, 's',
                                             [7.4, 7.6, 7.8]))
        client = _patch_client(monkeypatch, r)
        data = _run(client.fetch_buoy_data('IJG1', hours_back=1))

        assert all('tp_s' in p for p in data)
        assert data[0]['tp_s'] == pytest.approx(7.4)
        assert GROOTHEID_TP_FALLBACK in r.calls

    def test_graceful_degradation_one_grootheid_503(self, monkeypatch, caplog):
        """503 op SObh mag de rest niet kapotmaken; SObh-key ontbreekt simpelweg."""
        r = _ijg1_full_router()
        r.fail(GROOTHEID_SOBH, httpx.HTTPError("503 Service Unavailable"))
        client = _patch_client(monkeypatch, r)

        with caplog.at_level(logging.WARNING):
            data = _run(client.fetch_buoy_data('IJG1', hours_back=1))

        assert len(data) == 3
        for p in data:
            assert 'sobh_deg' not in p
            # andere extras blijven aanwezig
            assert 'tp_s' in p
            assert 'hmax_m' in p
            assert 'pressure_hpa' in p
        # Per-grootheid warning gelogd.
        assert any(GROOTHEID_SOBH in rec.message for rec in caplog.records)

    def test_a12_no_air_pressure(self, monkeypatch):
        """LUCHTDK/LUCHTTPR mogen niet bevraagd worden voor A12."""
        r = _FakeRouter()
        r.add(GROOTHEID_HM0, _series(GROOTHEID_HM0, 'cm', [180.0, 190.0]))
        r.add(GROOTHEID_TM02, _series(GROOTHEID_TM02, 's', [7.0, 7.2]))
        r.add(GROOTHEID_TH0, _series(GROOTHEID_TH0, 'graad', [290.0, 295.0]))
        r.add(GROOTHEID_TP, _series(GROOTHEID_TP, 's', [8.0, 8.2]))
        r.add(GROOTHEID_SOBH, _series(GROOTHEID_SOBH, 'graad', [30.0, 32.0]))
        r.add(GROOTHEID_HMAX, _series(GROOTHEID_HMAX, 'cm', [260.0, 270.0]))
        client = _patch_client(monkeypatch, r)

        data = _run(client.fetch_buoy_data('A12', hours_back=1))

        assert len(data) == 2
        assert GROOTHEID_LUCHTDK not in r.calls
        assert GROOTHEID_LUCHTTPR not in r.calls
        for p in data:
            assert 'pressure_hpa' not in p
            assert 'air_temp_c' not in p
            assert 'tp_s' in p
            assert 'sobh_deg' in p

    def test_backward_compat_with_include_extras_false(self, monkeypatch):
        """`include_extras=False` moet alleen de basis-3 grootheden bevragen."""
        r = _ijg1_full_router()
        client = _patch_client(monkeypatch, r)
        data = _run(client.fetch_buoy_data(
            'IJG1', hours_back=1, include_extras=False))

        assert len(data) == 3
        # Geen van de extra velden mag worden gezet.
        for p in data:
            assert 'tp_s' not in p
            assert 'sobh_deg' not in p
            assert 'hmax_m' not in p
            assert 'pressure_hpa' not in p
            assert 'air_temp_c' not in p
        # En geen extra-grootheid moet daadwerkelijk zijn aangevraagd.
        assert GROOTHEID_TP not in r.calls
        assert GROOTHEID_SOBH not in r.calls
        assert GROOTHEID_HMAX not in r.calls
        assert GROOTHEID_LUCHTDK not in r.calls
        assert GROOTHEID_LUCHTTPR not in r.calls


class TestSurgeResidual:
    def _tide_router(
        self,
        astro_cm: List[float],
        measured_cm: List[float],
        brkd_cm: List[float] = None,
    ) -> _FakeRouter:
        r = _FakeRouter()
        # Astronomisch via WATHTE (de fetch_tide_predictions-call).
        r.add(GROOTHEID_WATHTE, _series(GROOTHEID_WATHTE, 'cm', astro_cm))
        # WATHTBRKD: optioneel "berekend" tij. Bij None laten we de
        # fallback (gebruik astronomical_events) inschakelen.
        if brkd_cm is not None:
            r.add(GROOTHEID_WATHTBRKD,
                  _series(GROOTHEID_WATHTBRKD, 'cm', brkd_cm))
        return r

    def _patch_tide_client(
        self,
        monkeypatch,
        router: _FakeRouter,
        measured_cm: List[float],
    ) -> RWSClient:
        """
        WATHTE wordt twee keer aangevraagd: één keer met proces_type=astronomisch
        en één keer met proces_type=metingen. We routeren op basis van de
        ProcesType in de body, niet alleen op grootheid-code.
        """
        client = RWSClient()
        measured_response = _series(GROOTHEID_WATHTE, 'cm', measured_cm)

        async def fake_post(url, body):
            aquo = body['AquoPlusWaarnemingMetadata']['AquoMetadata']
            code = aquo['Grootheid']['Code']
            proces = aquo.get('ProcesType')
            router.calls.append(f"{code}:{proces}")
            if code == GROOTHEID_WATHTE and proces == 'metingen':
                return measured_response
            if code in router.errors:
                raise router.errors[code]
            return router.responses.get(code, _empty_response())

        monkeypatch.setattr(client, '_post', fake_post)
        return client

    def test_surge_residual_simple(self, monkeypatch):
        """surge = measured - astronomical, on each matched timestamp."""
        # Astronomisch raster én WATHTBRKD identiek (consistent berekend tij).
        astro = [50.0, 60.0, 70.0]
        brkd = [50.0, 60.0, 70.0]
        # Gemeten ligt 15 cm hoger → surge = +15 cm.
        measured = [65.0, 75.0, 85.0]

        router = self._tide_router(astro, measured, brkd_cm=brkd)
        client = self._patch_tide_client(monkeypatch, router, measured)

        result = _run(client.fetch_tide_predictions('ijmuiden', days_ahead=1))

        residuals = result['surge_residual_cm']
        assert len(residuals) == 3
        for r in residuals:
            assert r['surge_cm'] == pytest.approx(15.0)
        assert result['latest_surge_cm'] == pytest.approx(15.0)

    def test_surge_uses_wathte_astronomical_when_brkd_missing(self, monkeypatch):
        """
        Wanneer WATHTBRKD leeg is, moet de surge-berekening terugvallen op
        de astronomische WATHTE-events (level_m * 100 → cm).
        """
        astro = [40.0, 50.0, 60.0]
        # measured 10 cm hoger → surge = +10 cm.
        measured = [50.0, 60.0, 70.0]

        router = self._tide_router(astro, measured, brkd_cm=None)
        client = self._patch_tide_client(monkeypatch, router, measured)

        result = _run(client.fetch_tide_predictions('ijmuiden', days_ahead=1))
        residuals = result['surge_residual_cm']
        assert len(residuals) == 3
        for r in residuals:
            assert r['surge_cm'] == pytest.approx(10.0)

    def test_surge_empty_when_no_measured(self, monkeypatch):
        """Zonder gemeten WATHTE moet residual leeg en latest None zijn."""
        astro = [50.0, 60.0, 70.0]
        router = self._tide_router(astro, measured_cm=[])
        client = self._patch_tide_client(monkeypatch, router, measured_cm=[])

        result = _run(client.fetch_tide_predictions('ijmuiden', days_ahead=1))
        assert result['surge_residual_cm'] == []
        assert result['latest_surge_cm'] is None
        # tide_events moet wel gevuld zijn — surge degradeert apart.
        assert len(result['tide_events']) == 3


# ---------------------------------------------------------------------------
# Tests voor DATA-RESILIENCE fixes:
# - Concurrency throttle (semaphore)
# - Empty-body retry (DDAPI20 load-symptoom)
# - IJG1 → A12 failover
# - RWS unit-check op Hm0
# - Sanity checks (negative / out-of-range)
# ---------------------------------------------------------------------------


class TestRWSConcurrencyThrottle:
    def test_concurrency_does_not_exceed_limit(self, monkeypatch):
        """
        24 simultane _fetch_series_safe calls mogen niet meer dan
        RWS_CONCURRENCY_LIMIT (default 3) tegelijk in-flight zijn.
        Bewijs via een counter + asyncio.sleep zodat parallellisme
        observeerbaar wordt.
        """
        # Forceer de module-semaphore op een bekende limiet voor de test.
        limit = 3
        monkeypatch.setattr(rws_mod, '_rws_semaphore', asyncio.Semaphore(limit))

        in_flight = 0
        max_in_flight = 0
        lock = asyncio.Lock()

        async def fake_post(url, body):
            nonlocal in_flight, max_in_flight
            async with lock:
                in_flight += 1
                if in_flight > max_in_flight:
                    max_in_flight = in_flight
            await asyncio.sleep(0.02)  # genoeg om parallellisme zichtbaar te maken
            async with lock:
                in_flight -= 1
            return _series(GROOTHEID_HM0, 'cm', [100.0])

        client = RWSClient()
        monkeypatch.setattr(client, '_post', fake_post)

        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=1)
        tasks = [
            client._fetch_series_safe('ijgeul.1', GROOTHEID_HM0, start, now)
            for _ in range(24)
        ]
        _run(asyncio.gather(*tasks))

        assert max_in_flight <= limit, (
            f"Max concurrency {max_in_flight} > limit {limit} — semaphore werkt niet"
        )


class TestRWSEmptyBodyRetry:
    def test_two_empty_bodies_then_success(self, monkeypatch):
        """
        Twee empty-body responses gevolgd door een succes moet alsnog
        de data binnenkrijgen (max retries = 2 = 3 totale pogingen).
        """
        monkeypatch.setattr(rws_mod, 'RWS_EMPTY_BODY_RETRY_DELAY_S', 0.0)
        monkeypatch.setattr(rws_mod, '_rws_semaphore', asyncio.Semaphore(8))

        calls = {'n': 0}

        async def fake_post(url, body):
            calls['n'] += 1
            if calls['n'] <= 2:
                # Simuleer empty-body: json.JSONDecodeError
                raise json.JSONDecodeError("Expecting value", "", 0)
            return _series(GROOTHEID_HM0, 'cm', [150.0])

        client = RWSClient()
        monkeypatch.setattr(client, '_post', fake_post)

        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=1)
        rows = _run(client._fetch_series_safe('ijgeul.1', GROOTHEID_HM0, start, now))
        assert len(rows) == 1
        assert rows[0]['value'] == 150.0
        assert calls['n'] == 3

    def test_three_empty_bodies_returns_empty_list(self, monkeypatch, caplog):
        """3 empty-body responses op rij → graceful empty list + WARNING."""
        monkeypatch.setattr(rws_mod, 'RWS_EMPTY_BODY_RETRY_DELAY_S', 0.0)
        monkeypatch.setattr(rws_mod, '_rws_semaphore', asyncio.Semaphore(8))

        async def fake_post(url, body):
            raise json.JSONDecodeError("Expecting value", "", 0)

        client = RWSClient()
        monkeypatch.setattr(client, '_post', fake_post)

        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=1)
        with caplog.at_level(logging.WARNING):
            rows = _run(client._fetch_series_safe('ijgeul.1', GROOTHEID_HM0, start, now))
        assert rows == []
        assert any('empty-body' in rec.message.lower() for rec in caplog.records)


class TestRWSUnitCheck:
    def test_hm0_in_meters_no_extra_conversion(self, monkeypatch):
        """Bij unit='m' moet de Hm0 NIET door 100 gedeeld worden."""
        r = _ijg1_full_router()
        # Override Hm0 met unit='m' en kleine waarden.
        r.responses[GROOTHEID_HM0] = _series(GROOTHEID_HM0, 'm', [1.2, 1.3, 1.4])
        client = _patch_client(monkeypatch, r)

        data = _run(client.fetch_buoy_data('IJG1', hours_back=1))
        assert len(data) == 3
        # 1.2 m blijft 1.2 m (niet 0.012).
        assert data[0]['height_m'] == pytest.approx(1.20)
        assert data[2]['height_m'] == pytest.approx(1.40)

    def test_unknown_unit_assumes_cm(self, monkeypatch, caplog):
        """Bij onbekende unit moet een WARNING gelogd worden en cm-fallback."""
        r = _ijg1_full_router()
        r.responses[GROOTHEID_HM0] = _series(GROOTHEID_HM0, 'foobar', [120.0, 130.0])
        client = _patch_client(monkeypatch, r)
        with caplog.at_level(logging.WARNING):
            data = _run(client.fetch_buoy_data('IJG1', hours_back=1))
        assert data[0]['height_m'] == pytest.approx(1.20)
        assert any('foobar' in rec.message for rec in caplog.records)


class TestRWSSanityCheck:
    def test_negative_hm0_dropped(self, monkeypatch, caplog):
        """Hm0 < 0 cm → row gedropt + WARNING gelogd."""
        r = _ijg1_full_router()
        # Eerste twee waarden negatief / out-of-range; derde is geldig.
        r.responses[GROOTHEID_HM0] = _series(GROOTHEID_HM0, 'cm', [-50.0, 1600.0, 130.0])
        client = _patch_client(monkeypatch, r)
        with caplog.at_level(logging.WARNING):
            data = _run(client.fetch_buoy_data('IJG1', hours_back=1))
        # Maar 1 valide row (130 cm = 1.3 m).
        assert len(data) == 1
        assert data[0]['height_m'] == pytest.approx(1.30)
        assert any('Hm0' in rec.message for rec in caplog.records)


class TestIJG1Failover:
    def test_ijg1_empty_uses_a12_as_primary(self, monkeypatch):
        """Als IJG1 leeg blijft, moet A12 als primary_buoy ingezet worden."""
        from src.data.sources import rws as rws_mod_local

        async def fake_primary():
            return {'station': 'IJG1', 'station_name': 'IJgeul',
                    'spectra': [], 'raw_data': []}

        async def fake_ew():
            return {
                'A12': {
                    'station_name': 'A12 platform',
                    'spectra': ['spec1', 'spec2'],
                    'raw_data': [{'timestamp': 'x', 'height_m': 1.0,
                                  'period_s': 7, 'direction_deg': 290}],
                },
                'K13': {'station_name': 'K13 platform',
                        'spectra': [], 'raw_data': []},
            }

        class _FakeTideClient:
            async def fetch_tide_predictions(self, *args, **kwargs):
                return {'tide_events': [{'timestamp': 't', 'level_m': 0.5,
                                         'phase': 'opgaand'}],
                        'high_tides': [], 'low_tides': [],
                        'location': 'ijmuiden',
                        'surge_residual_cm': [], 'latest_surge_cm': None}

        monkeypatch.setattr(rws_mod_local, 'fetch_primary_buoy_data', fake_primary)
        monkeypatch.setattr(rws_mod_local, 'fetch_early_warning_buoys', fake_ew)
        monkeypatch.setattr(rws_mod_local, 'RWSClient', lambda: _FakeTideClient())

        result = _run(rws_mod_local.fetch_all_rws_data())
        assert result['primary_buoy']['station'] == 'A12'
        assert result['primary_buoy_fallback'] == 'A12'
        assert len(result['primary_buoy']['raw_data']) == 1

    def test_ijg1_with_data_no_fallback_field(self, monkeypatch):
        """Als IJG1 wel data heeft, geen fallback-veld + IJG1 blijft primary."""
        from src.data.sources import rws as rws_mod_local

        async def fake_primary():
            return {'station': 'IJG1', 'station_name': 'IJgeul',
                    'spectra': ['s1'],
                    'raw_data': [{'timestamp': 't', 'height_m': 1.0}]}

        async def fake_ew():
            return {'A12': {'station_name': 'A12 platform',
                            'spectra': [], 'raw_data': []},
                    'K13': {'station_name': 'K13 platform',
                            'spectra': [], 'raw_data': []}}

        class _FakeTideClient:
            async def fetch_tide_predictions(self, *args, **kwargs):
                return {'tide_events': [{'timestamp': 't', 'level_m': 0.5,
                                         'phase': 'opgaand'}],
                        'high_tides': [], 'low_tides': [],
                        'location': 'ijmuiden',
                        'surge_residual_cm': [], 'latest_surge_cm': None}

        monkeypatch.setattr(rws_mod_local, 'fetch_primary_buoy_data', fake_primary)
        monkeypatch.setattr(rws_mod_local, 'fetch_early_warning_buoys', fake_ew)
        monkeypatch.setattr(rws_mod_local, 'RWSClient', lambda: _FakeTideClient())

        result = _run(rws_mod_local.fetch_all_rws_data())
        assert result['primary_buoy']['station'] == 'IJG1'
        assert 'primary_buoy_fallback' not in result
