"""
One-shot test: stuur een echte digest-notificatie (ntfy / email / SMS) met de
huidige Noordwijk-forecast. Loopt buiten het morning-digest-venster om.

Backend wordt bepaald door $NOTIFIER (default 'ntfy'):
  - 'ntfy'   → NTFY_TOPIC moet gezet zijn
  - 'email'  → SMTP_USER + SMTP_PASSWORD + RECIPIENT_EMAIL
  - 'twilio' → TWILIO_* + RECIPIENT_PHONE_NUMBER

Gebruik:   python scripts/send_test_notification.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import NOORDWIJK
from src.data.sources.open_meteo import fetch_all_openmeteo_data, OpenMeteoClient
from src.data.sources.rws import fetch_all_rws_data, tide_state_at
from src.data.models import HourState, WindState
from src.scoring.hourly import score_hour
from src.scoring.windows import analyze_windows
from src.llm.generator import SMSGenerator
from src.llm.validator import SMSValidator
from src.notify import get_notifier


async def main() -> int:
    print("→ Open-Meteo + RWS ophalen...")
    openmeteo = await fetch_all_openmeteo_data(NOORDWIJK.lat, NOORDWIJK.lon)
    try:
        rws = await fetch_all_rws_data() or {}
    except Exception as e:
        print(f"  (RWS onbereikbaar, ga door zonder tij: {e})")
        rws = {}

    marine = openmeteo.get('marine') or []
    forecast = (openmeteo.get('forecast') or {}).get('knmi_seamless') or []
    if not marine or not forecast:
        print("✗ Geen forecast-data — kan niets bouwen.")
        return 1
    print(f"→ {len(marine)} uur marine-data, {len(forecast)} uur wind-data.")

    om = OpenMeteoClient()
    tide = rws.get('tide') or {}
    states = []
    for i in range(min(len(marine), len(forecast))):
        m, w = marine[i], forecast[i]
        if abs((m['timestamp'] - w['timestamp']).total_seconds()) > 3600:
            continue
        states.append(HourState(
            timestamp=m['timestamp'],
            location_name=NOORDWIJK.name,
            wave_spectrum=om.marine_data_to_wave_spectrum(m),
            wind=WindState(
                speed_kn=w['wind_speed'],
                direction_deg=int(w['wind_direction']),
                gusts_kn=w['wind_gusts'],
            ),
            tide=tide_state_at(tide, m['timestamp']),
            forecast_source='open-meteo',
            confidence=1.0,
        ))

    scores = [score_hour(s) for s in states]
    windows = analyze_windows(scores)
    peak_today = max((s.total_score for s in scores[:24]), default=0)
    peak_tomorrow = max((s.total_score for s in scores[24:48]), default=0)
    print(f"→ Peak vandaag: {peak_today}, peak morgen: {peak_tomorrow}, windows: {len(windows)}")

    print("→ Tekst genereren via Claude Haiku...")
    gen = SMSGenerator()
    val = SMSValidator()
    summary = {'total_hours': len(scores),
               'surfable_hours': sum(1 for s in scores if s.is_surfable())}
    text = gen.generate_digest_sms(states, scores, windows, summary)

    if not val.validate_digest_format(text):
        print("⚠ Format-validatie faalde, fallback template gebruikt.")
        text = gen._fallback_digest_template(states, scores, windows)

    print(f"→ Bericht ({len(text)} tekens):")
    print("  " + text.replace("\n", "\n  "))

    notifier = get_notifier()
    print(f"→ Versturen via {notifier.channel}...")
    result = notifier.send_digest(text)
    if result.get('success'):
        ident = result.get('message_id') or result.get('recipient')
        print(f"✓ Verstuurd via {result.get('channel')}: {ident}")
        return 0
    print(f"✗ Verzending mislukt: {result.get('error')}")
    return 2


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
