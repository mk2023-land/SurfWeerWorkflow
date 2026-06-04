"""
Twilio-SMS-notifier (betaalde fallback). Behoudt de oude Twilio-integratie achter
de nieuwe Notifier-interface zodat je via NOTIFIER=twilio kunt terugschakelen.

ENV:
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_PHONE_NUMBER     (afzender, +1...)
    RECIPIENT_PHONE_NUMBER  (+31...)
"""
import logging
import os
from typing import Optional

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from src.config import TWILIO_ALERT_MAX_LEN, TWILIO_DIGEST_MAX_LEN

logger = logging.getLogger(__name__)

# Backward-compatibele aliases — limieten leven nu in src.config zodat
# validator en notifier dezelfde getallen delen.
# (GSM-7: 160 chars/segment; UCS-2: 70 chars/segment. Wij rekenen pessimistisch
# in GSM-7 segments × 160. Bij ~€0.07 per segment is een 10-segment digest
# €0.70 per push — onaanvaardbaar bij meerdere/dag.)
HARD_SMS_LIMIT = TWILIO_DIGEST_MAX_LEN
ALERT_SMS_LIMIT = TWILIO_ALERT_MAX_LEN
_TRUNCATE_SUFFIX = "..."


def _truncate(message: str, limit: int, kind: str) -> str:
    """Truncate to `limit` chars (suffix included). No-op als al binnen limiet."""
    if len(message) <= limit:
        return message
    cut = max(0, limit - len(_TRUNCATE_SUFFIX))
    truncated = message[:cut] + _TRUNCATE_SUFFIX
    logger.warning(
        f"Twilio {kind} truncated: {len(message)} → {len(truncated)} chars (limit={limit})"
    )
    return truncated


class TwilioNotifier:
    channel = 'sms'

    def __init__(self):
        self.account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        self.auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        self.from_number = os.getenv('TWILIO_PHONE_NUMBER')
        self.to_number = os.getenv('RECIPIENT_PHONE_NUMBER')

        if not (self.account_sid and self.auth_token):
            logger.warning("Twilio credentials niet geconfigureerd; SMS wordt niet verstuurd.")
            self.client: Optional[Client] = None
        else:
            try:
                # Expliciete HTTP-timeout: zonder dit kan een hangende
                # Twilio-API de hele cron-run laten vastlopen (ntfy/mail
                # hebben al een timeout).
                from twilio.http.http_client import TwilioHttpClient
                http_client = TwilioHttpClient(timeout=15)
                self.client = Client(
                    self.account_sid, self.auth_token, http_client=http_client
                )
                logger.info(f"Twilio klaar: van={self.from_number} naar={self.to_number}")
            except Exception as e:
                logger.error(f"Twilio init mislukt: {e}")
                self.client = None

    def send_alert(self, message: str) -> dict:
        # Alert moet kort zijn — 2 segments max.
        return self._send(_truncate(message, ALERT_SMS_LIMIT, 'alert'))

    def send_digest(self, message: str) -> dict:
        # Digest mag tot 10 segments (kosten-cap).
        return self._send(_truncate(message, HARD_SMS_LIMIT, 'digest'))

    def _send(self, message: str, recipient: Optional[str] = None) -> dict:
        if not self.client:
            return {'success': False, 'channel': self.channel,
                    'error': 'Twilio client niet geconfigureerd', 'message': message}
        to = recipient or self.to_number
        if not to:
            return {'success': False, 'channel': self.channel,
                    'error': 'Geen ontvanger-nummer', 'message': message}
        try:
            m = self.client.messages.create(body=message, from_=self.from_number, to=to)
            logger.info(f"SMS verstuurd: SID={m.sid}, status={m.status}")
            return {
                'success': True,
                'channel': self.channel,
                'message_id': m.sid,
                'recipient': to,
                'status': m.status,
                'message': message,
            }
        except TwilioRestException as e:
            logger.error(f"Twilio API-fout: {e}")
            return {'success': False, 'channel': self.channel,
                    'error': str(e), 'code': e.code, 'message': message}
        except Exception as e:
            logger.error(f"Onverwachte fout bij SMS: {e}")
            return {'success': False, 'channel': self.channel,
                    'error': str(e), 'message': message}
