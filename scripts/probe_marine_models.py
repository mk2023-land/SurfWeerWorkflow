"""Test various Open-Meteo marine wave models for forecast horizon coverage.

Throwaway research script — niet ingelijfd in productie.
"""
import json
import urllib.request
import urllib.parse
import urllib.error
from collections import defaultdict

LAT = 52.241
LON = 4.428
URL = "https://marine-api.open-meteo.com/v1/marine"

MODELS = [
    None,  # default
    "ewam",
    "gwam",
    "ecmwf_wam025",
    "ecmwf_wam025_ensemble",
    "gfs_wave025",
    "gfs_wave016",
    "meteofrance_wave",
    "ncep_gfs_wave",
    "best_match",
]

FIELDS = "wave_height,wave_period,wave_direction,swell_wave_height,swell_wave_period,swell_wave_direction,wind_wave_height,wind_wave_period"


def fetch(model):
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
    url = f"{URL}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"_http_error": e.code, "_body": body[:300]}
    except Exception as e:
        return {"_error": str(e)}


def summarize(model):
    print(f"\n=== model={model or 'DEFAULT'} ===")
    d = fetch(model)
    if "_http_error" in d:
        print(f"HTTP {d['_http_error']}: {d['_body']}")
        return
    if "_error" in d:
        print(f"ERROR: {d['_error']}")
        return
    if "error" in d and d.get("error"):
        print(f"API ERROR: {d.get('reason')}")
        return
    h = d.get("hourly", {}) or {}
    times = h.get("time", []) or []
    keys = list(h.keys())
    print(f"keys ({len(keys)}): {keys[:12]}{'...' if len(keys)>12 else ''}")
    for primary_field in ("wave_height", "swell_wave_height", "wind_wave_height"):
        col = h.get(primary_field)
        suffix_col = None
        if not col and model:
            suffix_col = h.get(f"{primary_field}_{model}")
        use_col = col or suffix_col
        if use_col:
            by_day = defaultdict(lambda: [0, 0])
            for t, v in zip(times, use_col):
                day = t[:10]
                by_day[day][0] += 1
                if v is not None:
                    by_day[day][1] += 1
            print(f"--- {primary_field} (column: {'bare' if col else 'suffixed'}) ---")
            for day in sorted(by_day):
                ok, total = by_day[day][1], by_day[day][0]
                marker = "FULL" if ok == total else ("PART" if ok else "ZERO")
                print(f"  {day}: {ok:2d}/{total:2d}  [{marker}]")
            break
    else:
        print("No wave_height-like column found")
    if times:
        print(f"first/last time: {times[0]} / {times[-1]}")


if __name__ == "__main__":
    for m in MODELS:
        summarize(m)
