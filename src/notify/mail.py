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
import socket
import time
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from src.notify import format_nl_date

logger = logging.getLogger(__name__)

# Exponential backoff (seconds) — 3 retries: 2, 4, 8.
_RETRY_BACKOFF_SECONDS = (2, 4, 8)


def _is_transient_smtp_response(exc: smtplib.SMTPResponseException) -> bool:
    """SMTP RFC 5321: 4xx = transient (retry), 5xx = permanent (no retry)."""
    code = getattr(exc, 'smtp_code', None)
    return isinstance(code, int) and 400 <= code < 500


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
        subject = f"NWIJK ALERT {datetime.now(ZoneInfo('Europe/Amsterdam')).strftime('%d-%m %H:%M')}"
        return self._send(subject, message)

    def send_digest(self, message: str) -> dict:
        subject = f"Surf-update Noordwijk van {format_nl_date(datetime.now(ZoneInfo('Europe/Amsterdam')))}"
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

        last_error = 'unknown'
        last_error_type = 'Unknown'
        for attempt in range(len(_RETRY_BACKOFF_SECONDS) + 1):
            is_last = attempt == len(_RETRY_BACKOFF_SECONDS)
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
            except smtplib.SMTPResponseException as e:
                # 4xx → retryable transient; 5xx → permanent
                if not _is_transient_smtp_response(e):
                    logger.error(
                        f"SMTP permanente fout (5xx): code={e.smtp_code} {e.smtp_error!r}"
                    )
                    return {
                        'success': False, 'channel': self.channel,
                        'error': f"SMTP {e.smtp_code}: {e.smtp_error!r}",
                        'message': body,
                    }
                last_error = f"SMTP {e.smtp_code}: {e.smtp_error!r}"
                last_error_type = type(e).__name__
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                    socket.timeout, OSError) as e:
                last_error = str(e)
                last_error_type = type(e).__name__
            except Exception as e:
                # Onverwachte fouten (bv. SMTPAuthenticationError) — geen retry.
                logger.error(f"SMTP-verzending mislukt ({type(e).__name__}): {e}")
                return {
                    'success': False, 'channel': self.channel,
                    'error': str(e), 'message': body,
                }

            if is_last:
                logger.warning(
                    f"SMTP send mislukt na {attempt + 1} pogingen "
                    f"({last_error_type}): {last_error}"
                )
                break
            wait = _RETRY_BACKOFF_SECONDS[attempt]
            logger.info(
                f"SMTP send poging {attempt + 1} faalde ({last_error_type}: {last_error}); "
                f"retry over {wait}s"
            )
            time.sleep(wait)

        return {
            'success': False, 'channel': self.channel,
            'error': last_error, 'message': body,
        }
