"""Vergelijk DEFAULT vs gwam vs ecmwf_wam025 voor Noordwijk.

Doel: bevestigen dat een fallback-model:
- vergelijkbare values geeft op overlap (eerste 3 dagen)
- T+3..T+6 daadwerkelijk plausibele waarden geeft (geen 0.0, geen None)
- alle subfields (swell, wind_wave) heeft
"""
import json
import urllib.request
import urllib.parse

LAT = 52.241
LON = 4.428
URL = "https://marine-api.open-meteo.com/v1/marine"
FIELDS = (
    "wave_height,wave_period,wave_direction,"
    "swell_wave_height,swell_wave_period,swell_wave_direction,"
    "wind_wave_height,wind_wave_period,wind_wave_peak_period,wind_wave_direction"
)


def fetch(model=None):
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": FIELDS,
        "forecast_days": 7,
        "timezone": "Europe/Amsterdam",
    }
    if model:
        params["models"] = model
    qs = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{URL}?{qs}", timeout=30) as r:
        return json.loads(r.read())


def main():
    default = fetch(None)
    gwam = fetch("gwam")
    ecwam = fetch("ecmwf_wam025")

    times = default["hourly"]["time"]
    print(f"{'time':<18}  {'default':>10}  {'gwam':>10}  {'ecwam':>10}")
    # Sample every 6h
    for i in range(0, len(times), 6):
        t = times[i]
        d = default["hourly"]["wave_height"][i]
        g = gwam["hourly"]["wave_height"][i]
        e = ecwam["hourly"]["wave_height"][i]
        fmt = lambda v: f"{v:>10.2f}" if v is not None else f"{'None':>10}"
        print(f"{t:<18}  {fmt(d)}  {fmt(g)}  {fmt(e)}")

    print("\n--- T+4..T+6 details voor ecmwf_wam025 ---")
    for field in (
        "wave_height", "wave_period", "wave_direction",
        "swell_wave_height", "swell_wave_period", "swell_wave_direction",
        "wind_wave_height", "wind_wave_period", "wind_wave_peak_period",
        "wind_wave_direction",
    ):
        col = ecwam["hourly"].get(field, [])
        # Look at a t+4 hour (e.g., index ~96)
        t96 = times[96] if len(times) > 96 else None
        v96 = col[96] if col and len(col) > 96 else None
        non_none_in_range = sum(1 for v in col[72:] if v is not None) if col else 0
        total_in_range = len(col[72:]) if col else 0
        print(f"  {field:<32}  t+4 sample ({t96})={v96}, non-None T+3..T+6: {non_none_in_range}/{total_in_range}")


if __name__ == "__main__":
    main()
