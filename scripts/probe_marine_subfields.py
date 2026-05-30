"""Check welke subfields beschikbaar zijn per model voor de volle 7 dagen."""
import json
import urllib.request
import urllib.parse
from collections import defaultdict

LAT = 52.241
LON = 4.428
URL = "https://marine-api.open-meteo.com/v1/marine"
FIELDS = (
    "wave_height,wave_period,wave_direction,"
    "swell_wave_height,swell_wave_period,swell_wave_direction,"
    "wind_wave_height,wind_wave_period,wind_wave_peak_period,wind_wave_direction"
).split(",")


def fetch(model):
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": ",".join(FIELDS),
        "forecast_days": 7,
        "timezone": "Europe/Amsterdam",
    }
    if model:
        params["models"] = model
    qs = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{URL}?{qs}", timeout=30) as r:
        return json.loads(r.read())


for model in ["gwam", "ecmwf_wam025"]:
    print(f"\n=== model={model} ===")
    d = fetch(model)
    h = d["hourly"]
    times = h["time"]
    for field in FIELDS:
        col = h.get(field, [])
        non_none = sum(1 for v in col if v is not None)
        print(f"  {field:<32} {non_none:>3}/{len(times)} non-None")
