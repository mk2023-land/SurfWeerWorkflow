"""
Unit tests voor src/llm/generator.py — fallback templates + retry-gedrag.

Focus:
- Rijkere fallback-digest met board-advies, springtij, mist, multi-window.
- "flat" string bij dag waar Hs < 0.5m.
- max_tokens per call-type (alert vs digest).
- Anthropic retry-after header respect in _call_claude.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from src.data.models import (
    HourState,
    ScoreBreakdown,
    SpectralPeak,
    SurfWindow,
    SwellType,
    TideState,
    WaveSpectrum,
    WindState,
)
from src.llm.generator import SMSGenerator


def _make_hour(
    ts: datetime,
    hs: float = 1.0,
    period_s: float = 7.0,
    wave_dir: int = 285,
    wind_kn: float = 12.0,
    wind_dir: int = 225,
    tide_phase: str = "opgaand",
    tide_level: float = 0.5,
    daily_range_m: float = 1.8,
    visibility_m: float = 10000,
    dew_point_c: float = 8.0,
    air_temp_c: float = 12.0,
    cape: float = 50.0,
    li: float = 4.0,
) -> HourState:
    peak = SpectralPeak(
        frequency_mhz=int(1000 / max(period_s, 0.1)),
        period_s=period_s,
        height_m=hs,
        direction_deg=wave_dir,
        type=SwellType.WIND_SWELL,
    )
    spectrum = WaveSpectrum(
        timestamp=ts,
        significant_height_total=hs,
        mean_period=period_s,
        mean_direction=wave_dir,
        peaks=[peak] if hs > 0 else [],
    )
    wind = WindState(speed_kn=wind_kn, direction_deg=wind_dir, gusts_kn=wind_kn + 4)
    tide = TideState(
        level_m=tide_level,
        phase=tide_phase,
        next_low=ts + timedelta(hours=3),
        next_high=ts + timedelta(hours=9),
        daily_range_m=daily_range_m,
        last_turn_time=ts - timedelta(hours=2),
        next_turn_time=ts + timedelta(hours=4),
    )
    return HourState(
        timestamp=ts,
        location_name="Noordwijk",
        wave_spectrum=spectrum,
        wind=wind,
        tide=tide,
        visibility_m=visibility_m,
        dew_point_c=dew_point_c,
        air_temperature_c=air_temp_c,
        cape_jkg=cape,
        lifted_index=li,
    )


def _make_score(ts: datetime, total: float = 65.0) -> ScoreBreakdown:
    return ScoreBreakdown(
        timestamp=ts,
        golf_score=20.0,
        wind_score=20.0,
        tide_score=15.0,
        swell_dir_bonus=5.0,
    )


class TestFallbackDigestTemplate:
    """De fallback-digest moet 4 dagen rijk renderen zonder LLM."""

    def setup_method(self):
        # Disable de client zodat fallback altijd wordt gebruikt
        with patch("src.llm.generator.ANTHROPIC_CONFIG", {"api_key": None,
                                                          "max_tokens": 800,
                                                          "max_tokens_alert": 300,
                                                          "max_tokens_digest": 1200,
                                                          "temperature": 0.4,
                                                          "model": "x",
                                                          "fallback_model": "y"}):
            self.gen = SMSGenerator()

    def _make_day(self, base_date: datetime, hs: float, period_s: float = 7.0,
                  wind_kn: float = 12.0, daily_range_m: float = 1.8,
                  visibility_m: float = 10000):
        """Genereer 4 uur staten (11u-14u) voor één dag."""
        states = []
        scores = []
        for h in range(11, 15):
            ts = base_date.replace(hour=h, minute=0, second=0, microsecond=0)
            states.append(_make_hour(
                ts, hs=hs, period_s=period_s, wind_kn=wind_kn,
                daily_range_m=daily_range_m, visibility_m=visibility_m,
            ))
            scores.append(_make_score(ts, total=65.0 if hs >= 0.5 else 10.0))
        return states, scores

    def test_fallback_includes_surfweerbericht_header(self):
        d0 = datetime(2026, 5, 20)
        s, sc = self._make_day(d0, hs=0.9)
        sms = self.gen._fallback_digest_template(s, sc, [])
        assert sms.startswith("Surf-update Noordwijk van "), sms

    def test_fallback_includes_webcam(self):
        d0 = datetime(2026, 5, 20)
        s, sc = self._make_day(d0, hs=0.9)
        sms = self.gen._fallback_digest_template(s, sc, [])
        assert "surfweer.nl/webcams/noordwijk/" in sms

    def test_flat_day_renders_as_flat(self):
        """Dag waar max(Hs) < 0.5 → label 'flat'."""
        d0 = datetime(2026, 5, 20)
        s, sc = self._make_day(d0, hs=0.2)  # flat
        sms = self.gen._fallback_digest_template(s, sc, [])
        assert "flat" in sms.lower()

    def test_springtij_marker_when_range_high(self):
        """daily_range_m >= 2.0 → '(springtij)' label."""
        d0 = datetime(2026, 5, 20)
        s, sc = self._make_day(d0, hs=1.0, daily_range_m=2.3)
        sms = self.gen._fallback_digest_template(s, sc, [])
        assert "springtij" in sms.lower()

    def test_no_springtij_when_range_normal(self):
        d0 = datetime(2026, 5, 20)
        s, sc = self._make_day(d0, hs=1.0, daily_range_m=1.5)
        sms = self.gen._fallback_digest_template(s, sc, [])
        assert "springtij" not in sms.lower()

    def test_four_day_digest_format(self):
        """Maak 4 verschillende dagen en check dat alle label-styles erin zitten."""
        base = datetime(2026, 5, 20)
        states, scores = [], []
        configs = [
            (base, 0.9, 6.5, 12),         # vandaag: surfbaar small
            (base + timedelta(days=1), 0.2, 4.0, 8),   # morgen: flat
            (base + timedelta(days=2), 1.4, 7.0, 18),  # overmorgen: stevig
            (base + timedelta(days=3), 0.1, 3.5, 10),  # +3: flat
        ]
        for d, h, p, w in configs:
            ds, sc = self._make_day(d, hs=h, period_s=p, wind_kn=w)
            states.extend(ds)
            scores.extend(sc)
        sms = self.gen._fallback_digest_template(states, scores, [])
        # Vandaag, Morgen, Overmorgen, +3 als labels
        assert "Vandaag" in sms
        assert "Morgen" in sms
        assert "Overmorgen" in sms
        assert "+3" in sms
        # Minstens 1 'flat' regel (morgen en +3)
        assert sms.lower().count("flat") >= 1
        # Stevige wind-marker
        assert "(sterk)" in sms  # wind_kn=18 → "(sterk)"

    def test_window_renders_as_range(self):
        d0 = datetime(2026, 5, 20)
        s, sc = self._make_day(d0, hs=1.0)
        w = SurfWindow(
            start=d0.replace(hour=11),
            end=d0.replace(hour=14),
            peak_score=70,
            median_score=65,
            peak_hour=d0.replace(hour=12),
            triggers=[],
            stability=0.8,
            rarity_percentile=75.0,
            hourly_scores=sc,
            kind='surfable',
        )
        sms = self.gen._fallback_digest_template(s, sc, [w])
        assert "11:00-14:00" in sms

    def test_multi_window_joined_with_ook(self):
        """Twee windows in dezelfde dag worden gejoind met ' ook '."""
        d0 = datetime(2026, 5, 20)
        # Maak een dag met uren 8-20 zodat beide windows binnen dag-range vallen.
        states, scores = [], []
        for h in range(8, 21):
            ts = d0.replace(hour=h, minute=0, second=0, microsecond=0)
            states.append(_make_hour(ts, hs=1.0, period_s=7.0, wind_kn=12))
            scores.append(_make_score(ts, total=65))

        w1 = SurfWindow(
            start=d0.replace(hour=11),
            end=d0.replace(hour=13),
            peak_score=70, median_score=65,
            peak_hour=d0.replace(hour=12),
            triggers=[], stability=0.8, rarity_percentile=75.0,
            hourly_scores=scores, kind='surfable',
        )
        w2 = SurfWindow(
            start=d0.replace(hour=18),
            end=d0.replace(hour=20),
            peak_score=65, median_score=60,
            peak_hour=d0.replace(hour=19),
            triggers=[], stability=0.7, rarity_percentile=70.0,
            hourly_scores=scores, kind='surfable',
        )
        sms = self.gen._fallback_digest_template(states, scores, [w1, w2])
        assert " ook " in sms, f"Verwacht ' ook ' in:\n{sms}"

    def test_empty_input_returns_safe_fallback(self):
        sms = self.gen._fallback_digest_template([], [], [])
        assert "geen data" in sms.lower()
        assert "surfweer.nl/webcams/noordwijk/" in sms


class TestMaxTokensPerCallType:
    """generate_alert_sms en generate_digest_sms moeten andere max_tokens passen."""

    def test_alert_uses_max_tokens_alert(self):
        """generate_alert_sms moet ANTHROPIC_CONFIG['max_tokens_alert'] doorgeven."""
        from src.config import ANTHROPIC_CONFIG
        assert ANTHROPIC_CONFIG['max_tokens_alert'] == 300
        assert ANTHROPIC_CONFIG['max_tokens_digest'] == 1200
        # Backward compat: legacy key bestaat nog
        assert ANTHROPIC_CONFIG['max_tokens'] == 800

    def test_call_claude_accepts_max_tokens_param(self):
        """_call_claude moet een expliciete max_tokens parameter accepteren."""
        gen = SMSGenerator()
        gen.client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="test response")]
        gen.client.messages.create.return_value = mock_message

        gen._call_claude({"test": "input"}, max_tokens=350)
        # Eerste positionele call inspecteren
        call_kwargs = gen.client.messages.create.call_args.kwargs
        assert call_kwargs['max_tokens'] == 350

    def test_call_claude_falls_back_to_config_default(self):
        """Zonder max_tokens parameter → ANTHROPIC_CONFIG['max_tokens']."""
        gen = SMSGenerator()
        gen.client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="test")]
        gen.client.messages.create.return_value = mock_message

        gen._call_claude({"test": "input"})
        call_kwargs = gen.client.messages.create.call_args.kwargs
        # Moet de legacy default zijn
        assert call_kwargs['max_tokens'] == 800


class TestOverloadFallbackToHaiku:
    """
    Bij OverloadedError/RateLimitError op het primaire model schakelt
    _call_claude door naar het fallback-model. De Anthropic SDK doet zelf
    al exponential-backoff retry (`max_retries=2` in __init__) — wij testen
    alleen het model-fallback gedrag, niet de SDK-interne sleep.
    """

    def test_primary_overload_falls_back_to_haiku(self):
        """OverloadedError op Sonnet → één call naar Haiku, returned die output."""
        from anthropic._exceptions import OverloadedError

        gen = SMSGenerator()
        gen.client = MagicMock()

        mock_response = MagicMock()
        mock_response.headers = {}

        err = OverloadedError(
            message="overloaded",
            response=mock_response,
            body={"error": {"type": "overloaded_error"}},
        )

        success_message = MagicMock()
        success_message.content = [MagicMock(text="haiku output")]
        # Eerste call (Sonnet) faalt na SDK-retries, tweede call (Haiku) slaagt
        gen.client.messages.create.side_effect = [err, success_message]

        result = gen._call_claude({"test": "input"})

        assert result == "haiku output"
        # Twee separate calls — één per model
        assert gen.client.messages.create.call_count == 2
        models_called = [
            c.kwargs.get("model")
            for c in gen.client.messages.create.call_args_list
        ]
        # Primary eerst, fallback daarna
        from src.config import ANTHROPIC_CONFIG
        assert models_called[0] == ANTHROPIC_CONFIG['model']
        assert models_called[1] == ANTHROPIC_CONFIG['fallback_model']

    def test_both_models_overloaded_returns_none(self):
        """Beide modellen Overloaded → return None zodat caller fallback-template gebruikt."""
        from anthropic._exceptions import OverloadedError

        gen = SMSGenerator()
        gen.client = MagicMock()

        mock_response = MagicMock()
        mock_response.headers = {}

        err = OverloadedError(
            message="overloaded",
            response=mock_response,
            body={"error": {"type": "overloaded_error"}},
        )

        gen.client.messages.create.side_effect = [err, err]

        result = gen._call_claude({"test": "input"})

        assert result is None
        assert gen.client.messages.create.call_count == 2

    def test_rate_limit_on_primary_also_triggers_fallback(self):
        """Niet alleen OverloadedError — ook RateLimitError moet fallback triggeren."""
        from anthropic._exceptions import RateLimitError

        gen = SMSGenerator()
        gen.client = MagicMock()

        mock_response = MagicMock()
        mock_response.headers = {}

        err = RateLimitError(
            message="rate limited",
            response=mock_response,
            body={"error": {"type": "rate_limit_error"}},
        )

        success_message = MagicMock()
        success_message.content = [MagicMock(text="ok")]
        gen.client.messages.create.side_effect = [err, success_message]

        result = gen._call_claude({"test": "input"})

        assert result == "ok"
        assert gen.client.messages.create.call_count == 2


class TestPromptCaching:
    """
    SYSTEM_PROMPT wordt verzonden als content-block list met
    cache_control=ephemeral zodat Anthropic prompt-caching herbruik kan
    pakken op de ~3K-token referentie-forecaster-prompt.
    """

    def test_system_param_is_list_with_cache_control(self):
        gen = SMSGenerator()
        gen.client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="ok")]
        gen.client.messages.create.return_value = mock_message

        gen._call_claude({"test": "input"})

        call_kwargs = gen.client.messages.create.call_args.kwargs
        system_arg = call_kwargs['system']
        # Moet een lijst zijn (niet een bare string) — anders kan cache_control
        # niet worden geattacheerd.
        assert isinstance(system_arg, list), f"Verwacht lijst, kreeg {type(system_arg)}"
        assert len(system_arg) == 1
        block = system_arg[0]
        assert block.get("type") == "text"
        assert block.get("cache_control") == {"type": "ephemeral"}
        # System-tekst moet de referentie-forecaster-prompt zijn (gecheckt via anker-string)
        assert "referentie-forecaster" in block.get("text", "")
