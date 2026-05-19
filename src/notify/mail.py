"""
SMTP-mail-notifier. Gratis alternatief voor Twilio (en backup voor ntfy als je
liever een echte e-mail in je inbox hebt dan een push-melding).

Standaard config (overschrijfbaar via env):
    SMTP_HOST     = smtp.gmail.com
    SMTP_PORT     = 587   (STARTTLS)
    SMTP_USER     = ...   (volledig e-mailadres)
    SMTP_PASSWORD = ...   (voor Gmail: 16-tekens app-password, niet je gewone wachtwoord)
    RECIPIENT_EMAIL = ... (default = SMTP_USER, dus naar jezelf)
    SMTP_FROM_NAME = "Noordwijk Surf Alert"

Gmail-setup:
    1. Account Security → 2-Step Verification AAN.
    2. App passwords → "Mail" → kopieer 16-tekens.
    3. Zet die in SMTP_PASSWORD.
"""
import logging
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


class EmailNotifier:
    channel = 'email'

    def __init__(self):
        self.host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
        self.port = int(os.getenv('SMTP_PORT', '587'))
        self.user = os.getenv('SMTP_USER')
        self.password = os.getenv('SMTP_PASSWORD')
        self.recipient = os.getenv('RECIPIENT_EMAIL') or self.user
        self.from_name = os.getenv('SMTP_FROM_NAME', 'Noordwijk Surf Alert')

        if not (self.user and self.password):
            logger.warning(
                "SMTP credentials niet geconfigureerd (SMTP_USER / SMTP_PASSWORD). "
                "Mail wordt niet verzonden."
            )
        else:
            logger.info(f"EmailNotifier klaar: van={self.user} naar={self.recipient} via {self.host}:{self.port}")

    def send_alert(self, message: str) -> dict:
        subject = f"NWIJK ALERT {datetime.now().strftime('%d-%m %H:%M')}"
        return self._send(subject, message)

    def send_digest(self, message: str) -> dict:
        subject = f"Nwijk surfdigest {datetime.now().strftime('%a %d-%m')}"
        return self._send(subject, message)

    def _send(self, subject: str, body: str) -> dict:
        if not (self.user and self.password and self.recipient):
            return {
                'success': False,
                'channel': self.channel,
                'error': 'SMTP credentials of recipient ontbreken',
                'message': body,
            }
        msg = MIMEText(body, _charset='utf-8')
        msg['Subject'] = subject
        msg['From'] = f"{self.from_name} <{self.user}>"
        msg['To'] = self.recipient
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(self.user, self.password)
                server.send_message(msg)
            logger.info(f"Mail verstuurd: {subject!r} → {self.recipient}")
            return {
                'success': True,
                'channel': self.channel,
                'recipient': self.recipient,
                'subject': subject,
                'message': body,
            }
        except Exception as e:
            logger.error(f"SMTP-verzending mislukt ({type(e).__name__}): {e}")
            return {
                'success': False,
                'channel': self.channel,
                'error': str(e),
                'message': body,
            }
