"""Verifieer dat fetch_marine_data nu T+0..T+6 dekt via extended_fallback."""
import asyncio
import logging
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')

from src.data.sources.open_meteo import _get_openmeteo_client


async def main():
    client = _get_openmeteo_client()
    rows = await client.fetch_marine_data()
    print(f"\nTotal rows: {len(rows)}")
    by_day = defaultdict(lambda: {'total': 0, 'wh_ok': 0, 'sources': defaultdict(int)})
    for row in rows:
        day = row['timestamp'].strftime('%Y-%m-%d')
        by_day[day]['total'] += 1
        if row.get('wave_height') is not None:
            by_day[day]['wh_ok'] += 1
        by_day[day]['sources'][row.get('wave_source', '?')] += 1

    print(f"\n{'day':<12} {'wh_ok/total':<12} {'sources':<40}")
    for day in sorted(by_day):
        d = by_day[day]
        srcs = ', '.join(f"{s}={n}" for s, n in d['sources'].items())
        print(f"{day:<12} {d['wh_ok']}/{d['total']:<10} {srcs}")

    # Specifieke check: T+4 noon
    from datetime import datetime, timedelta
    today = datetime.now().date()
    t_plus_4 = today + timedelta(days=4)
    print(f"\n--- Sample rows op T+4 ({t_plus_4}) ---")
    for row in rows:
        if row['timestamp'].date() == t_plus_4 and row['timestamp'].hour in (0, 6, 12, 18):
            print(
                f"  {row['timestamp']} src={row.get('wave_source'):<20} "
                f"wh={row.get('wave_height')} period={row.get('wave_period')} "
                f"dir={row.get('wave_direction')} "
                f"swell_h={row.get('swell_wave_height')} swell_p={row.get('swell_wave_period')} "
                f"ww_h={row.get('wind_wave_height')}"
            )


if __name__ == '__main__':
    asyncio.run(main())
