"""Genereer een live digest met de huidige forecast — window-fix + nieuw
verdict+venster-format + volledige anti-hallucinatie-validator. Verstuurt NIETS.
"""
import asyncio

from src.config import NOORDWIJK
from src.data.sources.open_meteo import fetch_all_openmeteo_data
from src.main import SurfAlertSystem
from src.scoring.hourly import compute_wind_spread_per_hour, score_hour_series
from src.scoring.windows import analyze_windows


async def main():
    sys = SurfAlertSystem(dry_run=True)
    try:
        from src.baseline.seasonal import SeasonalBaselineBuilder
        sys.seasonal_baseline = SeasonalBaselineBuilder().load_baseline()
    except Exception:
        sys.seasonal_baseline = None

    om = await fetch_all_openmeteo_data(NOORDWIJK.lat, NOORDWIJK.lon)
    rws = {}
    try:
        from src.data.sources.rws import fetch_all_rws_data
        rws = await fetch_all_rws_data() or {}
    except Exception:
        pass

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
    sys._last_wind_spread_full = wind_spread
    windows = analyze_windows(scores, {}, seasonal_baseline=sys.seasonal_baseline)

    result = sys._handle_digest(hour_states, scores, windows)
    msg = result.get("message") if isinstance(result, dict) else result
    print("\n" + "=" * 70)
    print(msg)
    print("=" * 70)
    print(f"\n[{len(msg)} tekens]")


if __name__ == "__main__":
    asyncio.run(main())
