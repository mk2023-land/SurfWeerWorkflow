"""
One-shot test-run die een ECHTE SMS stuurt met de huidige Noordwijk-forecast.

Loopt de standaard pipeline buiten het morning-digest-venster om en zonder
ALERTS_ENABLED=true te vereisen. Voor handmatige verificatie van end-to-end
(Open-Meteo → scoring → Claude Haiku tekst → Twilio).

Gebruik:  python scripts/send_real_test_sms.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Zorg dat src importeerbaar is (zelfde patroon als main.py).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import NOORDWIJK
from src.data.sources.open_meteo import fetch_all_openmeteo_data, OpenMeteoClient
from src.data.sources.rws import fetch_all_rws_data, tide_state_at
from src.data.models import HourState, WindState
from src.scoring.hourly import score_hour
from src.llm.generator import SMSGenerator
from src.sms.twilio import TwilioClient


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
        print("✗ Geen forecast-data — kan geen SMS bouwen.")
        return 1

    print(f"→ {len(marine)} uur marine-data, {len(forecast)} uur wind-data.")

    om_client = OpenMeteoClient()
    tide_data = rws.get('tide') or {}
    hour_states = []
    for i in range(min(len(marine), len(forecast))):
        m, w = marine[i], forecast[i]
        if abs((m['timestamp'] - w['timestamp']).total_seconds()) > 3600:
            continue
        hour_states.append(HourState(
            timestamp=m['timestamp'],
            location_name=NOORDWIJK.name,
            wave_spectrum=om_client.marine_data_to_wave_spectrum(m),
            wind=WindState(
                speed_kn=w['wind_speed'],
                direction_deg=int(w['wind_direction']),
                gusts_kn=w['wind_gusts'],
            ),
            tide=tide_state_at(tide_data, m['timestamp']),
            forecast_source='open-meteo',
            confidence=1.0,
        ))

    scores = [score_hour(s) for s in hour_states]
    peak_today = max((s.total_score for s in scores[:24]), default=0)
    peak_tomorrow = max((s.total_score for s in scores[24:48]), default=0)
    print(f"→ Peak vandaag: {peak_today}, peak morgen: {peak_tomorrow}")

    print("→ Tekst genereren via Claude Haiku...")
    generator = SMSGenerator()
    summary = {
        'total_hours': len(scores),
        'surfable_hours': sum(1 for s in scores if s.is_surfable()),
    }
    sms_text = generator.generate_digest_sms(scores, summary)
    print(f"→ Bericht ({len(sms_text)} tekens):")
    print("  " + sms_text.replace("\n", "\n  "))

    print("→ Versturen via Twilio...")
    client = TwilioClient()
    result = client.send_sms(sms_text)
    if result.get('success'):
        print(f"✓ Verstuurd. SID={result.get('message_id')}, status={result.get('status')}")
        return 0
    print(f"✗ Verzending mislukt: {result.get('error')}")
    return 2


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
