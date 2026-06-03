"""Test max forecast horizon (forecast_days param) per model."""
import json
import urllib.parse
import urllib.request

LAT = 52.241
LON = 4.428
URL = "https://marine-api.open-meteo.com/v1/marine"


def fetch(model, days):
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "wave_height",
        "forecast_days": days,
        "timezone": "Europe/Amsterdam",
    }
    if model:
        params["models"] = model
    qs = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"{URL}?{qs}", timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"_http_error": e.code, "_body": body[:200]}


for model in [None, "gwam", "ecmwf_wam025"]:
    print(f"\n=== model={model or 'DEFAULT'} ===")
    for days in [7, 10, 14, 16]:
        d = fetch(model, days)
        if "_http_error" in d:
            print(f"  forecast_days={days}: HTTP {d['_http_error']}: {d['_body']}")
            continue
        h = d.get("hourly", {})
        times = h.get("time", [])
        wh = h.get("wave_height", [])
        non_none = sum(1 for v in wh if v is not None)
        last_time = times[-1] if times else None
        # Find last non-None index
        last_data = None
        for i in range(len(wh) - 1, -1, -1):
            if wh[i] is not None:
                last_data = times[i]
                break
        print(f"  forecast_days={days}: total={len(times)}, non-None={non_none}, last_time={last_time}, last_data={last_data}")
