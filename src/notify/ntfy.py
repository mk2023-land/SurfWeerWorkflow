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
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from src.notify import format_nl_date

logger = logging.getLogger(__name__)

# Exponential backoff schedule (seconds) — 3 retries totaal:
# attempt 1 fail → wait 3s → attempt 2 fail → wait 6s → attempt 3 fail → wait 12s → attempt 4 (laatste)
_RETRY_BACKOFF_SECONDS = (3, 6, 12)


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
            title=f"NWIJK ALERT {datetime.now(ZoneInfo('Europe/Amsterdam')).strftime('%d-%m %H:%M')}",
            body=message,
            priority='4',  # high — alert: laat doorpiepen ook bij stille modus
        )

    def send_digest(self, message: str) -> dict:
        return self._post(
            title=f"Surf-update Noordwijk {format_nl_date(datetime.now(ZoneInfo('Europe/Amsterdam')))}",
            body=message,
            priority='3',  # normal
        )

    def _post(self, title: str, body: str, priority: str) -> dict:
        if not self.topic:
            return {'success': False, 'channel': self.channel,
                    'error': 'NTFY_TOPIC niet gezet', 'message': body}
        url = f"{self.server}/{self.topic}"
        headers = {
            'Title': title,
            'Priority': priority,
            'Content-Type': 'text/plain; charset=utf-8',
        }
        # POST is idempotent voor ntfy publish — retry op transient errors veilig.
        # Total pogingen = len(backoff) + 1 = 4.
        last_error: str = 'unknown'
        last_error_type: str = 'Unknown'
        for attempt in range(len(_RETRY_BACKOFF_SECONDS) + 1):
            is_last = attempt == len(_RETRY_BACKOFF_SECONDS)
            try:
                response = httpx.post(
                    url,
                    content=body.encode('utf-8'),
                    headers=headers,
                    timeout=10.0,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_error = str(e)
                last_error_type = type(e).__name__
                if is_last:
                    logger.warning(
                        f"ntfy POST mislukt na {attempt + 1} pogingen ({last_error_type}): {last_error}"
                    )
                    break
                wait = _RETRY_BACKOFF_SECONDS[attempt]
                logger.info(
                    f"ntfy POST poging {attempt + 1} faalde ({last_error_type}: {last_error}); "
                    f"retry over {wait}s"
                )
                time.sleep(wait)
                continue
            except Exception as e:  # niet-retryable client-side fout
                logger.error(f"ntfy POST onverwachte fout ({type(e).__name__}): {e}")
                return {'success': False, 'channel': self.channel,
                        'error': str(e), 'message': body}

            status = response.status_code
            if status < 400:
                try:
                    data = response.json() if response.text else {}
                except Exception:
                    data = {}
                logger.info(f"ntfy push verstuurd naar {self.topic} (id={data.get('id')})")
                return {
                    'success': True,
                    'channel': self.channel,
                    'recipient': self.topic,
                    'message_id': data.get('id'),
                    'message': body,
                }

            # 429 Too Many Requests: respect Retry-After indien aanwezig
            if status == 429 and not is_last:
                retry_after = response.headers.get('Retry-After')
                wait = _RETRY_BACKOFF_SECONDS[attempt]
                if retry_after:
                    try:
                        wait = max(1, int(retry_after))
                    except ValueError:
                        pass  # ignoreer date-format Retry-After, gebruik backoff
                logger.info(
                    f"ntfy POST poging {attempt + 1}: HTTP 429; retry over {wait}s"
                )
                time.sleep(wait)
                continue

            # 5xx: transient server error → retry
            if 500 <= status < 600:
                last_error = f"HTTP {status}"
                last_error_type = 'HTTPStatusError'
                if is_last:
                    logger.warning(
                        f"ntfy POST mislukt na {attempt + 1} pogingen (HTTP {status})"
                    )
                    break
                wait = _RETRY_BACKOFF_SECONDS[attempt]
                logger.info(
                    f"ntfy POST poging {attempt + 1} faalde (HTTP {status}); retry over {wait}s"
                )
                time.sleep(wait)
                continue

            # 4xx (anders dan 429): client error — retry zinloos
            logger.error(f"ntfy POST client-fout HTTP {status}: {response.text[:200]}")
            return {'success': False, 'channel': self.channel,
                    'error': f"HTTP {status}", 'message': body}

        return {'success': False, 'channel': self.channel,
                'error': last_error, 'message': body}
