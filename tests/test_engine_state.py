"""
Regression tests voor B10 — atomic state.json write + corrupt-file safety.

Achtergrond: de SurfWeerWorkflow draait 4x/dag op GitHub Actions en
persisteert alert-state (cooldown, weekly counter, last_digest_time) via de
GH Actions cache. Bij een mid-write crash kon `state.json` truncated raken,
waarna de volgende run silently terugviel op een lege SystemState → weekly
cap bypassed → user kon 10+ alerts op één Sunday krijgen.

Deze tests dekken:
  - Round-trip save→load preserveert alle velden (incl. tz-aware UTC).
  - Atomic-rename: garbage in `state.json.tmp` raakt de echte
    `state.json` niet.
  - Corrupt `state.json` → AlertEngine.__init__ propageert JSONDecodeError
    in plaats van stil lege state te leveren.
  - First-run (geen state.json) → lege SystemState, geen crash.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.alerts.engine import AlertEngine
from src.data.models import SystemState


def _make_engine(state_file: Path) -> AlertEngine:
    """Helper: maak AlertEngine met custom state_file path."""
    return AlertEngine(state_file=str(state_file))


class TestRoundTrip:
    def test_save_then_load_preserves_all_fields(self, tmp_path):
        state_file = tmp_path / "state.json"
        engine = _make_engine(state_file)

        # Populeer alle velden met realistische tz-aware UTC waarden.
        last_alert = datetime(2026, 5, 19, 8, 30, 0, tzinfo=timezone.utc)
        last_digest = datetime(2026, 5, 20, 7, 15, 0, tzinfo=timezone.utc)
        cooldown = last_alert + timedelta(hours=12)
        engine.state.last_alert_time = last_alert
        engine.state.alerts_sent_this_week = 3
        engine.state.week_number = 21
        engine.state.last_digest_time = last_digest
        engine.state.cooldown_until = cooldown

        engine._save_state()

        # Een tweede engine leest dezelfde file en moet alles terugkrijgen.
        engine2 = _make_engine(state_file)
        assert engine2.state.last_alert_time == last_alert
        assert engine2.state.alerts_sent_this_week == 3
        assert engine2.state.week_number == 21
        assert engine2.state.last_digest_time == last_digest
        assert engine2.state.cooldown_until == cooldown

    def test_save_creates_parent_dir(self, tmp_path):
        """state_file in een nog niet bestaande subdir moet werken."""
        state_file = tmp_path / "nested" / "subdir" / "state.json"
        engine = _make_engine(state_file)
        engine.state.alerts_sent_this_week = 2
        engine._save_state()
        assert state_file.exists()


class TestAtomicWrite:
    def test_garbage_in_tmp_does_not_touch_real_file(self, tmp_path):
        """
        Simuleer een mid-write crash: er ligt een corrupte tmp-file naast
        de echte state.json. De echte state.json moet onaangetast blijven
        en gewoon laadbaar zijn.
        """
        state_file = tmp_path / "state.json"

        # Schrijf een geldige state.json
        engine = _make_engine(state_file)
        engine.state.alerts_sent_this_week = 5
        engine.state.week_number = 21
        engine._save_state()
        original_content = state_file.read_text()

        # Simuleer crash mid-write: garbage in een tmp-bestand naast de
        # echte file. (Echte tmp-naam bevat pid+uuid, maar elke `.tmp.*`
        # buurman mag de echte file niet beïnvloeden.)
        garbage_tmp = state_file.with_name("state.json.tmp.99999.deadbeef")
        garbage_tmp.write_text("{not valid json")

        # De echte file is bit-identiek na de "crash".
        assert state_file.read_text() == original_content

        # En laden werkt nog steeds.
        engine2 = _make_engine(state_file)
        assert engine2.state.alerts_sent_this_week == 5
        assert engine2.state.week_number == 21

    def test_no_tmp_file_remains_after_successful_save(self, tmp_path):
        """Na een succesvolle save mogen er geen .tmp.* files overblijven."""
        state_file = tmp_path / "state.json"
        engine = _make_engine(state_file)
        engine.state.alerts_sent_this_week = 1
        engine._save_state()

        leftover = list(tmp_path.glob("state.json.tmp.*"))
        assert leftover == [], f"Onverwachte tmp-files: {leftover}"

    def test_save_overwrites_existing_state(self, tmp_path):
        """os.replace moet bestaande state.json overschrijven."""
        state_file = tmp_path / "state.json"
        engine = _make_engine(state_file)
        engine.state.alerts_sent_this_week = 1
        engine._save_state()

        engine.state.alerts_sent_this_week = 7
        engine._save_state()

        with open(state_file) as f:
            data = json.load(f)
        assert data['alerts_sent_this_week'] == 7


class TestCorruptStateRaises:
    def test_corrupt_state_raises_on_engine_init(self, tmp_path):
        """
        B10 kern: corrupt state.json moet de run laten falen, NIET stil
        terugvallen op een lege SystemState (= weekly cap bypassed).
        """
        state_file = tmp_path / "state.json"
        state_file.write_text("{this is not valid json")

        with pytest.raises(json.JSONDecodeError):
            _make_engine(state_file)

    def test_corrupt_state_truncated_mid_write_raises(self, tmp_path):
        """
        Realistisch crash-scenario: een truncated state.json (proces
        gekilled tijdens json.dump, zonder de B10 atomic-write fix).
        """
        state_file = tmp_path / "state.json"
        state_file.write_text('{\n  "last_alert_time": "2026-05')

        with pytest.raises(json.JSONDecodeError):
            _make_engine(state_file)

    def test_empty_state_file_raises(self, tmp_path):
        """
        Zero-byte state.json (geheel truncated) is ook corrupt — raise.
        json.JSONDecodeError wordt netjes door json.load gegooid op "".
        """
        state_file = tmp_path / "state.json"
        state_file.write_text("")

        with pytest.raises(json.JSONDecodeError):
            _make_engine(state_file)


class TestFirstRun:
    def test_missing_state_file_returns_fresh_state(self, tmp_path):
        """
        First run: geen state.json → lege SystemState, geen exception.
        Dit is het enige scenario waarin we stil terugvallen op een lege
        state — een echte first-run is niet hetzelfde als een corruptie.
        """
        state_file = tmp_path / "state.json"
        assert not state_file.exists()

        engine = _make_engine(state_file)
        assert isinstance(engine.state, SystemState)
        assert engine.state.last_alert_time is None
        assert engine.state.alerts_sent_this_week == 0
        assert engine.state.last_digest_time is None
        assert engine.state.cooldown_until is None

    def test_first_run_then_save_creates_file(self, tmp_path):
        """First run → save → file exists en is valid JSON."""
        state_file = tmp_path / "state.json"
        engine = _make_engine(state_file)
        engine._save_state()

        assert state_file.exists()
        with open(state_file) as f:
            data = json.load(f)  # Mag niet crashen.
        assert data['alerts_sent_this_week'] == 0
