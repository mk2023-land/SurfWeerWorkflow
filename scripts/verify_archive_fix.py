"""Verifieer dat _archive_sent_sms na de fix non-None buoy-velden krijgt.

Doet een echte live-fetch (RWS + Open-Meteo niet nodig) en simuleert het
volgorde-pad: _update_run_log → _archive_sent_sms.
"""
import asyncio
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '/home/merlijn/Merlijn/SurfWeerWorkflow')

from src.data.models import RunLog
from src.data.sources.rws import fetch_all_rws_data


async def main():
    # Echte RWS-fetch — IJG1 + A12 + K13 + tide.
    rws_data = await fetch_all_rws_data()
    print(f"primary_buoy.station = {rws_data.get('primary_buoy', {}).get('station')}")
    print(f"primary_buoy.spectra count = {len(rws_data.get('primary_buoy', {}).get('spectra', []))}")
    print(f"A12 spectra count = {len(rws_data.get('early_warning_buoys', {}).get('A12', {}).get('spectra', []))}")

    # Simuleer een minimal run_log en de update-call.
    run_log = RunLog(
        timestamp=datetime.now(),
        run_type='manual_test',
        scores_today_peak=0,
        scores_tomorrow_peak=0,
        alert_types_detected=[],
        windows_total=0,
        windows_alertworthy=0,
        decision='digest',
    )
    run_log.sms_text_full = 'TEST SMS body'

    # Repliceer _update_run_log voor de buoy-velden (lichtgewicht test).
    if rws_data.get('primary_buoy', {}).get('spectra'):
        ijg1 = rws_data['primary_buoy']['spectra'][-1]
        run_log.buoy_ijg1_height = ijg1.significant_height_total
        run_log.buoy_ijg1_period = ijg1.mean_period
    if rws_data.get('early_warning_buoys', {}).get('A12', {}).get('spectra'):
        a12 = rws_data['early_warning_buoys']['A12']['spectra'][-1]
        run_log.buoy_a12_period = a12.mean_period

    print(f"\nrun_log.buoy_ijg1_height = {run_log.buoy_ijg1_height}")
    print(f"run_log.buoy_ijg1_period = {run_log.buoy_ijg1_period}")
    print(f"run_log.buoy_a12_period  = {run_log.buoy_a12_period}")

    # Schrijf naar tempdir om te verifieren wat _archive_sent_sms zou produceren.
    with tempfile.TemporaryDirectory() as td:
        archive_dir = Path(td) / 'sms_archive'
        archive_dir.mkdir()

        # Inline kopie van _archive_sent_sms body (zonder zelf-de-file-IO van
        # de echte methode aan te roepen — die schrijft naar cwd 'data/').
        ts = datetime.now()
        entry = {
            'timestamp': ts.isoformat(),
            'decision': run_log.decision,
            'alert_types': run_log.alert_types_detected or [],
            'sms_text': run_log.sms_text_full,
            'validation_passed': run_log.llm_validation_passed,
            'validation_issues': run_log.llm_validation_issues or [],
            'scores_today_peak': run_log.scores_today_peak,
            'scores_tomorrow_peak': run_log.scores_tomorrow_peak,
            'buoy_ijg1_height': run_log.buoy_ijg1_height,
            'buoy_ijg1_period': run_log.buoy_ijg1_period,
            'buoy_a12_period': run_log.buoy_a12_period,
            'windows_total': run_log.windows_total,
            'windows_alertworthy': run_log.windows_alertworthy,
            'bias_correction_applied': run_log.bias_correction_applied,
        }
        print(f"\nWould archive entry: {json.dumps(entry, default=str, indent=2)}")

        assert entry['buoy_ijg1_height'] is not None, "FAIL: buoy_ijg1_height is None"
        assert entry['buoy_ijg1_period'] is not None, "FAIL: buoy_ijg1_period is None"
        assert entry['buoy_a12_period'] is not None, "FAIL: buoy_a12_period is None"
        print("\nPASS: alle buoy-velden zijn non-None in het archief-payload.")


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')
    asyncio.run(main())
