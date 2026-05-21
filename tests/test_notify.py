"""
Unit tests voor de NOTIFIER-laag.

Dekt:
  - ntfy.sh retry-loop (5xx, network, 4xx no-retry, 429 Retry-After, success)
  - SMTP mail retry (SMTPServerDisconnected → retry; 5xx → no retry)
  - Twilio length truncation (alert > 320, digest > 1600)
  - format_nl_date timezone-bewustzijn (UTC 23:30 vrijdag → "za" in NL CEST)
"""
from __future__ import annotations

import logging
import os
import smtplib
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# format_nl_date — timezone-awareness
# ---------------------------------------------------------------------------

class TestFormatNLDate:
    def test_naive_treated_as_local(self):
        from src.notify import format_nl_date
        # Maandag 19 mei 2025
        dt = datetime(2025, 5, 19, 12, 0)
        assert format_nl_date(dt) == "ma 19 mei"

    def test_utc_fri_2330_becomes_amsterdam_sat(self):
        """UTC 23:30 vrijdag 23-mei-2025 = CEST 01:30 zaterdag 24-mei.

        Dit is de exacte GitHub-Actions-bug: runner in UTC zou "vr 23 mei"
        loggen terwijl het in NL al "za 24 mei" is.
        """
        from src.notify import format_nl_date
        dt_utc = datetime(2025, 5, 23, 23, 30, tzinfo=timezone.utc)
        # Niet vr 23 mei, maar za 24 mei
        assert format_nl_date(dt_utc) == "za 24 mei"

    def test_amsterdam_aware_passes_through(self):
        from src.notify import format_nl_date
        dt = datetime(2025, 5, 19, 12, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
        assert format_nl_date(dt) == "ma 19 mei"

    def test_none_uses_now_amsterdam(self):
        """Mag niet crashen en moet een geldige NL-string opleveren."""
        from src.notify import format_nl_date
        result = format_nl_date()
        # Format: '<dd> <day> <month>' — 3 tokens minimaal
        parts = result.split()
        assert len(parts) == 3
        assert parts[0] in ('ma', 'di', 'wo', 'do', 'vr', 'za', 'zo')


# ---------------------------------------------------------------------------
# ntfy.sh retry
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code: int, text: str = '', headers: dict | None = None,
                 json_data: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self  # type: ignore[arg-type]
            )


@pytest.fixture
def ntfy_no_sleep(monkeypatch):
    """Patch time.sleep zodat retries instant zijn."""
    import src.notify.ntfy as ntfy_mod
    monkeypatch.setattr(ntfy_mod.time, 'sleep', lambda *_: None)
    return ntfy_mod


@pytest.fixture
def ntfy_notifier(monkeypatch, ntfy_no_sleep):
    monkeypatch.setenv('NTFY_TOPIC', 'test-topic-xyz')
    from src.notify.ntfy import NtfyNotifier
    return NtfyNotifier()


class TestNtfyRetry:
    def test_success_first_try(self, monkeypatch, ntfy_notifier):
        from src.notify import ntfy as ntfy_mod
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return _FakeResponse(200, text='{"id":"abc"}', json_data={'id': 'abc'})

        monkeypatch.setattr(ntfy_mod.httpx, 'post', fake_post)
        result = ntfy_notifier.send_alert("test message")
        assert result['success'] is True
        assert result['message_id'] == 'abc'
        assert len(calls) == 1

    def test_retry_on_5xx_then_success(self, monkeypatch, ntfy_notifier, caplog):
        from src.notify import ntfy as ntfy_mod
        sequence = [
            _FakeResponse(503),
            _FakeResponse(502),
            _FakeResponse(200, text='{"id":"ok"}', json_data={'id': 'ok'}),
        ]
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return sequence.pop(0)

        monkeypatch.setattr(ntfy_mod.httpx, 'post', fake_post)
        with caplog.at_level(logging.INFO, logger='src.notify.ntfy'):
            result = ntfy_notifier.send_alert("body")
        assert result['success'] is True
        assert len(calls) == 3
        # INFO-log over retry moet verschijnen
        assert any('retry over' in r.message for r in caplog.records)

    def test_no_retry_on_4xx(self, monkeypatch, ntfy_notifier):
        from src.notify import ntfy as ntfy_mod
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return _FakeResponse(403, text='Forbidden')

        monkeypatch.setattr(ntfy_mod.httpx, 'post', fake_post)
        result = ntfy_notifier.send_alert("body")
        assert result['success'] is False
        assert '403' in result['error']
        # Géén retry — exact 1 call
        assert len(calls) == 1

    def test_retry_after_header_respected_on_429(self, monkeypatch, ntfy_notifier):
        from src.notify import ntfy as ntfy_mod
        sleeps: list[float] = []
        monkeypatch.setattr(ntfy_mod.time, 'sleep', lambda s: sleeps.append(s))

        sequence = [
            _FakeResponse(429, headers={'Retry-After': '7'}),
            _FakeResponse(200, text='{"id":"x"}', json_data={'id': 'x'}),
        ]

        def fake_post(url, **kwargs):
            return sequence.pop(0)

        monkeypatch.setattr(ntfy_mod.httpx, 'post', fake_post)
        result = ntfy_notifier.send_alert("body")
        assert result['success'] is True
        # Eerste sleep moet 7s zijn (Retry-After), niet de default 3s
        assert sleeps and sleeps[0] == 7

    def test_retry_on_network_error(self, monkeypatch, ntfy_notifier):
        from src.notify import ntfy as ntfy_mod
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if len(calls) < 2:
                raise httpx.ConnectError("conn refused")
            return _FakeResponse(200, text='{"id":"ok"}', json_data={'id': 'ok'})

        monkeypatch.setattr(ntfy_mod.httpx, 'post', fake_post)
        result = ntfy_notifier.send_alert("body")
        assert result['success'] is True
        assert len(calls) == 2

    def test_all_retries_fail_returns_failure(self, monkeypatch, ntfy_notifier, caplog):
        from src.notify import ntfy as ntfy_mod

        def fake_post(url, **kwargs):
            return _FakeResponse(500)

        monkeypatch.setattr(ntfy_mod.httpx, 'post', fake_post)
        with caplog.at_level(logging.WARNING, logger='src.notify.ntfy'):
            result = ntfy_notifier.send_alert("body")
        assert result['success'] is False
        assert any('mislukt na' in r.message for r in caplog.records
                   if r.levelno >= logging.WARNING)


# ---------------------------------------------------------------------------
# Mail SMTP retry
# ---------------------------------------------------------------------------

class _FakeSMTP:
    """Minimal smtplib.SMTP stand-in. `behavior` is a list of None | Exception
    consumed left-to-right; None = send succeeds."""

    instances: list = []

    def __init__(self, behavior_queue):
        self.behavior_queue = behavior_queue
        self.sent = False
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        # Pop behavior on entry — if it's an exception that triggers on connect,
        # raise here. Otherwise hold for send_message.
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self): pass
    def starttls(self): pass
    def login(self, u, p): pass

    def send_message(self, msg):
        outcome = self.behavior_queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        self.sent = True


@pytest.fixture
def mail_no_sleep(monkeypatch):
    import src.notify.mail as mail_mod
    monkeypatch.setattr(mail_mod.time, 'sleep', lambda *_: None)
    return mail_mod


@pytest.fixture
def mail_notifier(monkeypatch, mail_no_sleep):
    monkeypatch.setenv('SMTP_USER', 'tester@example.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'app-pw-1234')
    monkeypatch.setenv('RECIPIENT_EMAIL', 'tester@example.com')
    from src.notify.mail import EmailNotifier
    return EmailNotifier()


class TestMailRetry:
    def test_retry_on_server_disconnected_then_success(
        self, monkeypatch, mail_notifier, caplog
    ):
        import src.notify.mail as mail_mod
        _FakeSMTP.instances = []
        queue = [smtplib.SMTPServerDisconnected("nope"), None]

        def fake_smtp_ctor(host, port, timeout=30):
            return _FakeSMTP(queue)

        monkeypatch.setattr(mail_mod.smtplib, 'SMTP', fake_smtp_ctor)
        with caplog.at_level(logging.INFO, logger='src.notify.mail'):
            result = mail_notifier.send_alert("body")
        assert result['success'] is True
        # Twee SMTP-instanties = retry gebeurde
        assert len(_FakeSMTP.instances) == 2
        assert any('retry over' in r.message for r in caplog.records)

    def test_no_retry_on_5xx_response(self, monkeypatch, mail_notifier):
        import src.notify.mail as mail_mod
        _FakeSMTP.instances = []
        # 550 = Mailbox unavailable, permanent
        err = smtplib.SMTPResponseException(550, b'5.1.1 No such user')
        queue = [err, None]

        def fake_smtp_ctor(host, port, timeout=30):
            return _FakeSMTP(queue)

        monkeypatch.setattr(mail_mod.smtplib, 'SMTP', fake_smtp_ctor)
        result = mail_notifier.send_alert("body")
        assert result['success'] is False
        # Geen retry: alleen 1 instance
        assert len(_FakeSMTP.instances) == 1

    def test_retry_on_4xx_smtp_response(self, monkeypatch, mail_notifier):
        import src.notify.mail as mail_mod
        _FakeSMTP.instances = []
        # 451 = Try again later, transient
        err = smtplib.SMTPResponseException(451, b'4.7.1 try later')
        queue = [err, None]

        def fake_smtp_ctor(host, port, timeout=30):
            return _FakeSMTP(queue)

        monkeypatch.setattr(mail_mod.smtplib, 'SMTP', fake_smtp_ctor)
        result = mail_notifier.send_alert("body")
        assert result['success'] is True
        assert len(_FakeSMTP.instances) == 2

    def test_all_retries_fail_warning(self, monkeypatch, mail_notifier, caplog):
        import src.notify.mail as mail_mod
        _FakeSMTP.instances = []
        # 4 keer disconnected = 1 + 3 retries → all fail
        queue = [smtplib.SMTPServerDisconnected("x")] * 4

        def fake_smtp_ctor(host, port, timeout=30):
            return _FakeSMTP(queue)

        monkeypatch.setattr(mail_mod.smtplib, 'SMTP', fake_smtp_ctor)
        with caplog.at_level(logging.WARNING, logger='src.notify.mail'):
            result = mail_notifier.send_alert("body")
        assert result['success'] is False
        assert len(_FakeSMTP.instances) == 4
        assert any('mislukt na' in r.message for r in caplog.records
                   if r.levelno >= logging.WARNING)


# ---------------------------------------------------------------------------
# Twilio length truncation
# ---------------------------------------------------------------------------

@pytest.fixture
def twilio_notifier(monkeypatch):
    monkeypatch.setenv('TWILIO_ACCOUNT_SID', 'sid')
    monkeypatch.setenv('TWILIO_AUTH_TOKEN', 'tok')
    monkeypatch.setenv('TWILIO_PHONE_NUMBER', '+10000000000')
    monkeypatch.setenv('RECIPIENT_PHONE_NUMBER', '+31000000000')

    import src.notify.twilio as tw_mod

    # Vervang Client met een dummy die messages.create captured
    captured = {}

    class _FakeMessages:
        def create(self, body, from_, to):
            captured['body'] = body
            captured['to'] = to
            m = MagicMock()
            m.sid = 'SM-xxx'
            m.status = 'queued'
            return m

    class _FakeClient:
        def __init__(self, *a, **k):
            self.messages = _FakeMessages()

    monkeypatch.setattr(tw_mod, 'Client', _FakeClient)
    notifier = tw_mod.TwilioNotifier()
    notifier._captured = captured  # type: ignore[attr-defined]
    return notifier


class TestTwilioTruncation:
    def test_alert_truncated_above_320(self, twilio_notifier, caplog):
        long_msg = "A" * 500
        with caplog.at_level(logging.WARNING, logger='src.notify.twilio'):
            result = twilio_notifier.send_alert(long_msg)
        assert result['success'] is True
        sent = twilio_notifier._captured['body']
        assert len(sent) == 320
        assert sent.endswith("...")
        assert any('truncated' in r.message for r in caplog.records)

    def test_alert_short_not_truncated(self, twilio_notifier, caplog):
        msg = "kort alert bericht"
        with caplog.at_level(logging.WARNING, logger='src.notify.twilio'):
            result = twilio_notifier.send_alert(msg)
        assert result['success'] is True
        assert twilio_notifier._captured['body'] == msg
        assert not any('truncated' in r.message for r in caplog.records)

    def test_digest_truncated_above_1600(self, twilio_notifier, caplog):
        long_msg = "B" * 2500
        with caplog.at_level(logging.WARNING, logger='src.notify.twilio'):
            result = twilio_notifier.send_digest(long_msg)
        assert result['success'] is True
        sent = twilio_notifier._captured['body']
        assert len(sent) == 1600
        assert sent.endswith("...")
        assert any('truncated' in r.message for r in caplog.records)

    def test_digest_below_1600_unchanged(self, twilio_notifier):
        msg = "C" * 1500
        result = twilio_notifier.send_digest(msg)
        assert result['success'] is True
        assert twilio_notifier._captured['body'] == msg

    def test_digest_can_be_longer_than_alert_limit(self, twilio_notifier):
        # 1000 chars: te lang voor alert (320), maar prima voor digest (1600).
        msg = "D" * 1000
        result = twilio_notifier.send_digest(msg)
        assert result['success'] is True
        assert twilio_notifier._captured['body'] == msg  # niet getrunceerd
