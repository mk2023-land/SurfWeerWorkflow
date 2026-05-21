"""
One-shot test: bouw een digest-bericht voor Noordwijk en (optioneel) verzend
het. STANDAARD wordt het bericht alleen geprint — gebruik --send om écht
naar de notifier te pushen, zodat je eerst kunt benchmarken.

Backend wordt bepaald door $NOTIFIER (default 'ntfy'):
  - 'ntfy'   → NTFY_TOPIC moet gezet zijn
  - 'email'  → SMTP_USER + SMTP_PASSWORD + RECIPIENT_EMAIL
  - 'twilio' → TWILIO_* + RECIPIENT_PHONE_NUMBER

Gebruik:
    python scripts/send_test_notification.py            # dry-run, print alleen
    python scripts/send_test_notification.py --send     # daadwerkelijk verzenden
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import NOORDWIJK
from src.data.models import HourState, WindState
from src.data.sources.open_meteo import OpenMeteoClient, fetch_all_openmeteo_data
from src.data.sources.rws import fetch_all_rws_data, tide_state_at
from src.llm.generator import SMSGenerator
from src.notify import get_notifier
from src.scoring.hourly import compute_wind_spread_per_hour, score_hour_series
from src.scoring.windows import analyze_windows


async def main() -> int:
    print("→ Open-Meteo + RWS ophalen...")
    openmeteo = await fetch_all_openmeteo_data(NOORDWIJK.lat, NOORDWIJK.lon)
    try:
        rws = await fetch_all_rws_data() or {}
    except Exception as e:
        print(f"  (RWS onbereikbaar, ga door zonder tij: {e})")
        rws = {}

    marine = openmeteo.get('marine') or []
    forecast_by_model = openmeteo.get('forecast') or {}
    forecast = forecast_by_model.get('knmi_seamless') or []
    if not marine or not forecast:
        print("✗ Geen forecast-data — kan niets bouwen.")
        return 1
    print(
        f"→ {len(marine)} uur marine-data, {len(forecast)} uur wind-data "
        f"({len(forecast_by_model)} model(en): {list(forecast_by_model.keys())})."
    )

    # Sprint 2 #8 — per-uur wind-spread tussen modellen
    wind_spread_full = compute_wind_spread_per_hour(forecast_by_model)
    spread_by_ts = {entry['timestamp']: entry for entry in wind_spread_full}

    om = OpenMeteoClient()
    tide = rws.get('tide') or {}
    states = []
    pressure_series = []
    cloud_series = []
    wind_spread_series = []
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
        # Parallelle series voor Sprint 1+2 features:
        # - pressure_series: druk-gradient detector (Sprint 1)
        # - cloud_series: diurnal wind-decay (Sprint 2 #12)
        # - wind_spread_series: multi-model confidence (Sprint 2 #8)
        pressure_series.append(w.get('pressure') or 1013.0)
        cloud_series.append(w.get('cloud_cover'))
        wind_spread_series.append(spread_by_ts.get(m['timestamp']) or {})

    scores = score_hour_series(
        states,
        pressure_series=pressure_series,
        cloud_cover_series=cloud_series,
        wind_spread_series=wind_spread_series,
    )
    windows = analyze_windows(scores)
    peak_today = max((s.total_score for s in scores[:24]), default=0)
    peak_tomorrow = max((s.total_score for s in scores[24:48]), default=0)
    print(f"→ Peak vandaag: {peak_today}, peak morgen: {peak_tomorrow}, windows: {len(windows)}")

    # Sprint 2 #8 — print samenvatting van model-spread (debug-info)
    if wind_spread_full:
        avg_speed_std = sum(s['speed_std_kn'] for s in wind_spread_full[:48]) / max(48, len(wind_spread_full[:48]))
        max_speed_std = max((s['speed_std_kn'] for s in wind_spread_full[:48]), default=0)
        avg_dir_spread = sum(s['direction_spread_deg'] for s in wind_spread_full[:48]) / max(48, len(wind_spread_full[:48]))
        print(
            f"→ Wind-model spread 0-48u: speed avg/max {avg_speed_std:.1f}/{max_speed_std:.1f} kn, "
            f"dir avg {avg_dir_spread:.1f}°"
        )

    print("→ Tekst genereren via Claude (retry-loop met validator-feedback)...")
    gen = SMSGenerator()
    summary = {'total_hours': len(scores),
               'surfable_hours': sum(1 for s in scores if s.is_surfable())}

    # Productie-pad: generate_digest_sms doet intern 3× retry met
    # validator-feedback bij hallucinaties. Pas bij alle 3 falen → fallback.
    text = gen.generate_digest_sms(
        states, scores, windows, summary,
        wind_spread_series=wind_spread_full,
    )

    used_fallback = text.startswith("Surf-update Noordwijk")
    if used_fallback:
        print("⚠ Alle 3 LLM-pogingen faalden — fallback template gebruikt.")
    else:
        print("✓ Claude-tekst gegenereerd en gevalideerd (anti-hallucinatie OK).")

    print(f"→ Bericht ({len(text)} tekens):")
    print("  " + text.replace("\n", "\n  "))

    send_flag = '--send' in sys.argv
    if not send_flag:
        print()
        print("→ DRY-RUN — bericht NIET verzonden. Gebruik --send om te pushen.")
        return 0

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
