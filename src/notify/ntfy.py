"""
ntfy.sh push-melding notifier. Gratis, geen account, geen API key.

Werking: HTTP POST naar https://ntfy.sh/<topic> met de SMS-tekst als body.
Topic-naam is je geheim — wie de naam kent kan je notificaties ZIEN
(en wie er heen POST kan je notificaties STUREN). Kies dus iets
unguessable, geen `surfalert` maar bv. `nwijk-mk-h4pdq7nl`.

ENV:
    NTFY_TOPIC    — verplicht, unieke topic-naam
    NTFY_SERVER   — optioneel, default https://ntfy.sh
"""
import logging
import os
from datetime import datetime

import httpx

from src.notify import format_nl_date

logger = logging.getLogger(__name__)


class NtfyNotifier:
    channel = 'ntfy'

    def __init__(self):
        self.topic = os.getenv('NTFY_TOPIC')
        self.server = os.getenv('NTFY_SERVER', 'https://ntfy.sh').rstrip('/')
        if not self.topic:
            logger.warning("NTFY_TOPIC niet gezet; push-meldingen worden niet verzonden")
        else:
            logger.info(f"NtfyNotifier klaar: {self.server}/{self.topic}")

    def send_alert(self, message: str) -> dict:
        return self._post(
            title=f"NWIJK ALERT {datetime.now().strftime('%d-%m %H:%M')}",
            body=message,
            priority='4',  # high — alert: laat doorpiepen ook bij stille modus
        )

    def send_digest(self, message: str) -> dict:
        return self._post(
            title=f"Surf-update Noordwijk van {format_nl_date(datetime.now())}",
            body=message,
            priority='3',  # normal
        )

    def _post(self, title: str, body: str, priority: str) -> dict:
        if not self.topic:
            return {'success': False, 'channel': self.channel,
                    'error': 'NTFY_TOPIC niet gezet', 'message': body}
        url = f"{self.server}/{self.topic}"
        try:
            response = httpx.post(
                url,
                content=body.encode('utf-8'),
                headers={
                    'Title': title,
                    'Priority': priority,
                    'Content-Type': 'text/plain; charset=utf-8',
                },
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json() if response.text else {}
            logger.info(f"ntfy push verstuurd naar {self.topic} (id={data.get('id')})")
            return {
                'success': True,
                'channel': self.channel,
                'recipient': self.topic,
                'message_id': data.get('id'),
                'message': body,
            }
        except Exception as e:
            logger.error(f"ntfy POST mislukt ({type(e).__name__}): {e}")
            return {'success': False, 'channel': self.channel,
                    'error': str(e), 'message': body}
