"""Verifieer dat marine_data_to_wave_spectrum bruikbare WaveSpectrum geeft
voor extended_fallback rows (gemixt totals=ecmwf + split=gwam).
"""
import asyncio

from src.data.sources.open_meteo import _get_openmeteo_client


async def main():
    client = _get_openmeteo_client()
    rows = await client.fetch_marine_data()
    fb_rows = [r for r in rows if r.get('wave_source') == 'extended_fallback']
    print(f"Fallback rows: {len(fb_rows)}")
    for r in fb_rows[::12][:6]:  # sample every ~12h
        spec = client.marine_data_to_wave_spectrum(r)
        peaks_summary = ', '.join(
            f"{p.type.value} h={p.height_m:.2f} T={p.period_s:.1f} dir={p.direction_deg}"
            for p in spec.peaks
        )
        print(
            f"  {r['timestamp']} Hs={spec.significant_height_total:.2f} "
            f"Tm={spec.mean_period:.1f} peaks=[{peaks_summary}]"
        )


asyncio.run(main())
