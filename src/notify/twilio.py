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

logger = logging.getLogger(__name__)


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
                self.client = Client(self.account_sid, self.auth_token)
                logger.info(f"Twilio klaar: van={self.from_number} naar={self.to_number}")
            except Exception as e:
                logger.error(f"Twilio init mislukt: {e}")
                self.client = None

    def send_alert(self, message: str) -> dict:
        return self._send(message)

    def send_digest(self, message: str) -> dict:
        return self._send(message)

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
