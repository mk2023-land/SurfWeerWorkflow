"""
Diagnose: dump per dag/uur de golfhoogte, periode, wind en surf-score voor de
komende dagen, plus welke window-drempel (longboard=42 / surfable=60) elk uur
haalt. Doel: zien of er uren met echte golven zijn die onder de drempel
wegvallen (Windfinder-vraag: "zouden er vensters moeten zijn").

Draait alleen data-fetch + scoring + window-analyse — GEEN LLM, GEEN notify.
"""
import asyncio
from collections import defaultdict

from src.config import SURF_MINIMUMS, SURF_THRESHOLDS
from src.main import SurfAlertSystem
from src.scoring.daylight import is_daylight_noordwijk
from src.scoring.hourly import compute_wind_spread_per_hour, score_hour_series
from src.scoring.windows import analyze_windows

_COMPASS = ["N","NNO","NO","ONO","O","OZO","ZO","ZZO","Z","ZZW","ZW","WZW","W","WNW","NW","NNW"]
def comp(deg):
    return _COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]

DAYS_NL = ["ma","di","wo","do","vr","za","zo"]


async def main():
    sys = SurfAlertSystem(dry_run=True)

    om = await sys._fetch_openmeteo() if hasattr(sys, "_fetch_openmeteo") else None
    if om is None:
        from src.config import NOORDWIJK
        from src.data.sources.open_meteo import fetch_all_openmeteo_data
        om = await fetch_all_openmeteo_data(NOORDWIJK.lat, NOORDWIJK.lon)

    rws = {}
    try:
        from src.data.sources.rws import fetch_all_rws_data
        rws = await fetch_all_rws_data() or {}
    except Exception as e:
        print(f"(RWS niet beschikbaar: {e})")

    hour_states = sys._build_hour_states(om, rws)

    forecast_by_model = (om or {}).get("forecast") or {}
    primary = forecast_by_model.get("knmi_seamless") or []
    wind_spread = compute_wind_spread_per_hour(forecast_by_model)
    spread_by_ts = {e["timestamp"]: e for e in wind_spread}
    primary_by_ts = {r["timestamp"]: r for r in primary}

    pressure, cloud, wss = [], [], []
    for st in hour_states:
        row = primary_by_ts.get(st.timestamp) or {}
        pressure.append(row.get("pressure") or 1013.0)
        cloud.append(row.get("cloud_cover"))
        wss.append(spread_by_ts.get(st.timestamp) or {})

    scores = score_hour_series(hour_states, pressure_series=pressure,
                               cloud_cover_series=cloud, wind_spread_series=wss)
    windows = analyze_windows(scores, {}, seasonal_baseline=sys.seasonal_baseline if hasattr(sys, "seasonal_baseline") else None)

    score_by_ts = {s.timestamp: s for s in scores}
    LB = SURF_THRESHOLDS["longboard"]
    SF = SURF_THRESHOLDS["surfable"]
    by_day = defaultdict(list)
    for st in hour_states:
        by_day[st.timestamp.date()].append(st)

    print(f"\nDrempels: longboard>={LB}  surfable>={SF}  | min_hs={SURF_MINIMUMS['min_hs_m']}m min_period={SURF_MINIMUMS['min_period_s']}s")
    print(f"min_golf longboard>={SURF_THRESHOLDS['min_golf_longboard']}  surfable>={SURF_THRESHOLDS['min_golf_surfable']}\n")

    for day in sorted(by_day):
        sts = sorted(by_day[day], key=lambda s: s.timestamp)
        label = DAYS_NL[day.weekday()]
        print(f"=== {label} {day} ===")
        for st in sts:
            h = st.timestamp.hour
            if not is_daylight_noordwijk(st.timestamp):
                continue
            sc = score_by_ts.get(st.timestamp)
            if sc is None:
                continue
            ws = st.wave_spectrum
            hs = ws.significant_height_total
            per = ws.mean_period
            wdir = comp(ws.mean_direction)
            wind = st.wind
            flag = ""
            if sc.total_score >= SF:
                flag = "  <<SURFABLE"
            elif sc.total_score >= LB:
                flag = "  <longboard"
            src = "" if st.wave_source == "primary" else f" [{st.wave_source}]"
            print(f"  {h:02d}u  {hs:0.2f}m {wdir:>3} {per:0.1f}s | "
                  f"wind {wind.speed_kn:0.1f}kn {comp(wind.direction_deg):>3} | "
                  f"score {sc.total_score:5.1f} (golf {sc.golf_score:0.1f}){src}{flag}")
        print()

    print(f"\nWINDOWS gevonden door analyze_windows: {len(windows)}")
    for w in windows:
        print(f"  {DAYS_NL[w.start.weekday()]} {w.start.strftime('%H:%M')}-{w.end.strftime('%H:%M')} "
              f"kind={w.kind} peak={w.peak_score} median={w.median_score}")


if __name__ == "__main__":
    asyncio.run(main())
