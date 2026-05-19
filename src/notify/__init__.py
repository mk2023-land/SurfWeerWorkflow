"""
Notifier-laag: één interface (`Notifier.send_alert` / `Notifier.send_digest`),
twee backends: SMTP-mail (gratis) en Twilio-SMS (betaald, fallback).

Selectie via env var `NOTIFIER`:
  - `NOTIFIER=email`  → SMTP via Gmail/andere provider (gratis)
  - `NOTIFIER=twilio` → Twilio SMS

Default is email.
"""
import logging
import os
from datetime import datetime
from typing import Protocol

logger = logging.getLogger(__name__)


_NL_DAYS_SHORT = ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo']
_NL_MONTHS = ['januari', 'februari', 'maart', 'april', 'mei', 'juni',
              'juli', 'augustus', 'september', 'oktober', 'november', 'december']


def format_nl_date(dt: datetime) -> str:
    """Format datum als 'ma 19 mei' — voor titels in notificaties."""
    return f"{_NL_DAYS_SHORT[dt.weekday()]} {dt.day} {_NL_MONTHS[dt.month - 1]}"


class Notifier(Protocol):
    """Minimale interface waar main.py mee praat."""

    def send_alert(self, message: str) -> dict: ...
    def send_digest(self, message: str) -> dict: ...
    @property
    def channel(self) -> str: ...


def get_notifier() -> Notifier:
    """Bouw de juiste notifier op basis van $NOTIFIER (default 'ntfy')."""
    kind = (os.getenv('NOTIFIER') or 'ntfy').lower()
    if kind == 'ntfy':
        from src.notify.ntfy import NtfyNotifier
        return NtfyNotifier()
    if kind == 'email':
        from src.notify.mail import EmailNotifier
        return EmailNotifier()
    if kind == 'twilio':
        from src.notify.twilio import TwilioNotifier
        return TwilioNotifier()
    raise ValueError(f"Onbekende NOTIFIER waarde: {kind!r} (verwacht 'ntfy', 'email' of 'twilio')")


def format_send_result_for_logging(result: dict) -> str:
    """Eén log-string voor zowel email- als Twilio-resultaten."""
    if not isinstance(result, dict):
        return f"UNKNOWN: {result!r}"
    msg_preview = (result.get('message') or '')[:100]
    if result.get('success'):
        if result.get('debug_mode'):
            return f"DEBUG: {msg_preview}..."
        ident = result.get('message_id') or result.get('recipient') or 'sent'
        return f"SUCCESS ({result.get('channel','?')}): id={ident}, msg={msg_preview}..."
    return f"FAILED ({result.get('channel','?')}): {result.get('error')}, msg={msg_preview}..."
