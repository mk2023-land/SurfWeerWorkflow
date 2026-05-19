"""
Hoofdscript voor Noordwijk Surf Alert Systeem.
Orkestreert data ophaling, scoring, alert detectie, en SMS verzending.
"""
import asyncio
import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import argparse
import sys
import os

# Add src to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    NOORDWIJK,
    ALERT_CONFIG,
    DEBUG,
    TIMEZONE
)

from data.models import (
    HourState,
    WaveSpectrum,
    WindState,
    TideState,
    ScoreBreakdown,
    SurfWindow,
    RunLog
)

from data.sources.open_meteo import fetch_all_openmeteo_data, OpenMeteoClient
from data.sources.rws import fetch_all_rws_data, RWSClient, tide_state_at

from scoring.deconstruct import decompose_spectrum
from scoring.hourly import score_hour, calculate_confidence
from scoring.windows import analyze_windows, filter_alertworthy_windows

from alerts.engine import AlertEngine
from alerts.detectors import AlertDetectorEngine

from llm.generator import SMSGenerator
from llm.validator import SMSValidator

from notify import get_notifier, format_send_result_for_logging

# Setup logging
logging.basicConfig(
    level=logging.INFO if DEBUG else logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data/surf_alert.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


class SurfAlertSystem:
    """Hoofd systeem klasse."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.alert_engine = AlertEngine()
        self.sms_generator = SMSGenerator()
        self.sms_validator = SMSValidator()
        self.notifier = get_notifier()
        logger.info(f"Notifier kanaal: {self.notifier.channel}")

        # Zorg dat data directory bestaat
        Path('data').mkdir(parents=True, exist_ok=True)

    async def run(self) -> RunLog:
        """
        Voer complete run uit.

        Returns:
            RunLog met resultaten
        """
        start_time = datetime.now()
        logger.info(f"Starting surf alert system run at {start_time}")

        run_log = RunLog(
            timestamp=start_time,
            run_type="manual" if self.dry_run else "scheduled",
            scores_today_peak=0,
            scores_tomorrow_peak=0,
            alert_types_detected=[],
            windows_total=0,
            windows_alertworthy=0,
            decision="skip"
        )

        try:
            # Stap 1: Haal data op (parallel)
            logger.info("Fetching data from all sources...")

            # Open-Meteo data opslaan
            try:
                openmeteo_data = await fetch_all_openmeteo_data(NOORDWIJK.lat, NOORDWIJK.lon)
            except Exception as e:
                logger.error(f"Failed to fetch Open-Meteo data: {e}")
                openmeteo_data = None

            # RWS data proberen (tijdelijk optioneel — API is verhuisd)
            rws_data = {}
            try:
                rws_data = await fetch_all_rws_data() or {}
            except Exception as e:
                logger.warning(f"RWS data unavailable (API transition): {e}")

            # Stap 2: Bouw HourStates
            logger.info("Building hour states...")
            hour_states = self._build_hour_states(openmeteo_data, rws_data)

            if not hour_states:
                logger.error("No hour states created from data")
                run_log.error = "No data available"
                return run_log

            # Stap 3: Score elk uur
            logger.info("Scoring hours...")
            hourly_scores = [score_hour(state) for state in hour_states]

            # Stap 4: Analyseer windows
            logger.info("Analyzing surf windows...")
            triggers_dict = {}  # timestamp → AlertType lijst
            windows = analyze_windows(hourly_scores, triggers_dict)
            alertworthy_windows = filter_alertworthy_windows(windows)

            # Stap 5: Voer alert detectie uit
            logger.info("Running alert detection...")
            detector_engine = AlertDetectorEngine()

            # Split history en forecast
            history = hour_states[:12]  # Laatste 12 uren
            forecast = hour_states[12:]  # Komende uren

            # Bereid buoy history voor
            buoy_history = {
                'IJG1': rws_data['primary_buoy']['spectra'] if rws_data.get('primary_buoy') else [],
                'A12': rws_data['early_warning_buoys']['A12']['spectra'] if rws_data.get('early_warning_buoys') else [],
                'K13': rws_data['early_warning_buoys']['K13']['spectra'] if rws_data.get('early_warning_buoys') else []
            }

            triggered_alerts = detector_engine.detect_all(
                forecast, history, buoy_history, windows
            )

            # Stap 6: Neem beslissing
            logger.info("Making decision...")
            is_digest_time = self.alert_engine.is_morning_first_run()

            # Voeg triggers toe aan windows
            for window in windows:
                for hour_score in window.hourly_scores:
                    if triggered_alerts:
                        triggers_dict[hour_score.timestamp] = list(triggered_alerts)

            decision = self.alert_engine.evaluate_forecast(
                forecast, history, buoy_history, windows, is_digest_time
            )

            # Stap 7: Genereer en verstuur notificatie (mail of SMS)
            if decision.has_alert:
                logger.info("Generating and sending alert notification...")
                result = self._handle_alert(decision.send_alerts[0])
                run_log.sms_sent = format_send_result_for_logging(result)
                run_log.llm_used = True

            elif decision.send_digest:
                logger.info("Generating and sending digest notification...")
                result = self._handle_digest(hour_states, hourly_scores, windows)
                run_log.sms_sent = format_send_result_for_logging(result)
                run_log.llm_used = True
                self.alert_engine.record_digest_sent()

            else:
                logger.info(f"No action: {decision.skip_reason}")
                run_log.sms_sent = None

            # Stap 8: Update run log
            self._update_run_log(run_log, hourly_scores, alertworthy_windows, decision, rws_data)

            logger.info(f"Run completed successfully in {(datetime.now() - start_time).total_seconds():.1f}s")

        except Exception as e:
            logger.error(f"Run failed with error: {e}")
            run_log.error = str(e)
            import traceback
            traceback.print_exc()

        finally:
            # Stap 9: Log run
            self._log_run(run_log)

        return run_log

    def _build_hour_states(self, openmeteo_data: dict, rws_data: dict) -> List[HourState]:
        """Bouw HourStates uit ruwe data."""
        hour_states = []

        try:
            marine_data = openmeteo_data.get('marine', [])
            forecast_data = openmeteo_data.get('forecast', {})

            # Gebruik KNMI model als primary
            primary_model = forecast_data.get('knmi_seamless', [])

            if not marine_data or not primary_model:
                logger.warning("Missing marine or forecast data")
                return []

            tide_data = (rws_data or {}).get('tide') or {}
            openmeteo_client = OpenMeteoClient()

            # Merge marine en forecast data per uur
            for i in range(min(len(marine_data), len(primary_model))):
                marine = marine_data[i]
                weather = primary_model[i]

                # Skip als timestamps niet matchen
                if abs((marine['timestamp'] - weather['timestamp']).total_seconds()) > 3600:
                    continue

                wave_spectrum = openmeteo_client.marine_data_to_wave_spectrum(marine)

                wind_state = WindState(
                    speed_kn=weather['wind_speed'],
                    direction_deg=int(weather['wind_direction']),
                    gusts_kn=weather['wind_gusts']
                )

                tide_state = tide_state_at(tide_data, marine['timestamp'])

                hour_state = HourState(
                    timestamp=marine['timestamp'],
                    location_name=NOORDWIJK.name,
                    wave_spectrum=wave_spectrum,
                    wind=wind_state,
                    tide=tide_state,
                    forecast_source="open-meteo",
                    confidence=1.0
                )

                hour_states.append(hour_state)

        except Exception as e:
            logger.error(f"Error building hour states: {e}")
            return []

        return hour_states

    def _handle_alert(self, alert) -> dict:
        """Genereer en verstuur alert-notificatie."""
        sms_text = self.sms_generator.generate_alert_sms(alert)

        validation_result = self.sms_validator.validate_sms(
            sms_text,
            self.sms_generator._prepare_alert_input(alert)
        )
        if not validation_result:
            logger.warning(f"Alert validation failed: {validation_result.issues}, fallback gebruikt")
            sms_text = self.sms_generator._fallback_alert_template(alert)

        if not self.dry_run:
            return self.notifier.send_alert(sms_text)
        return {'success': True, 'debug_mode': True, 'channel': self.notifier.channel, 'message': sms_text}

    def _handle_digest(
        self,
        hour_states: List[HourState],
        hourly_scores: List[ScoreBreakdown],
        windows: List[SurfWindow],
    ) -> dict:
        """Genereer en verstuur digest SMS."""
        forecast_summary = {
            'total_hours': len(hourly_scores),
            'surfable_hours': len([s for s in hourly_scores if s.is_surfable()])
        }

        sms_text = self.sms_generator.generate_digest_sms(
            hour_states, hourly_scores, windows, forecast_summary
        )

        format_ok = self.sms_validator.validate_digest_format(sms_text)
        if not format_ok:
            logger.warning(f"Digest format validation failed: {format_ok.issues}, fallback gebruikt")
            sms_text = self.sms_generator._fallback_digest_template(hour_states, hourly_scores, windows)

        if not self.dry_run:
            return self.notifier.send_digest(sms_text)
        return {'success': True, 'debug_mode': True, 'channel': self.notifier.channel, 'message': sms_text}

    def _update_run_log(
        self,
        run_log: RunLog,
        hourly_scores: List[ScoreBreakdown],
        windows: List[SurfWindow],
        decision,
        rws_data: dict
    ):
        """Update run log met resultaten."""
        # Peak scores
        if len(hourly_scores) >= 24:
            run_log.scores_today_peak = max(s.total_score for s in hourly_scores[:24])
        if len(hourly_scores) >= 48:
            run_log.scores_tomorrow_peak = max(s.total_score for s in hourly_scores[24:48])

        # Windows
        run_log.windows_total = len(windows)
        run_log.windows_alertworthy = len([w for w in windows if w.is_alertworthy])

        # Decision
        run_log.decision = decision.action

        # Buoi data
        if rws_data.get('primary_buoy', {}).get('spectra'):
            ijg1 = rws_data['primary_buoy']['spectra'][-1]
            run_log.buoy_ijg1_height = ijg1.significant_height_total
            run_log.buoy_ijg1_period = ijg1.mean_period

        if rws_data.get('early_warning_buoys', {}).get('A12', {}).get('spectra'):
            a12 = rws_data['early_warning_buoys']['A12']['spectra'][-1]
            run_log.buoy_a12_period = a12.mean_period

    def _log_run(self, run_log: RunLog):
        """Log run naar JSONL bestand."""
        log_file = Path('data/forecasts_log.jsonl')

        with open(log_file, 'a') as f:
            f.write(json.dumps(run_log.to_dict(), default=str) + '\n')


async def main():
    """Hoofd entry point."""
    parser = argparse.ArgumentParser(description='Noordwijk Surf Alert System')
    parser.add_argument('--dry-run', action='store_true', help='Run without sending SMS')
    args = parser.parse_args()

    system = SurfAlertSystem(dry_run=args.dry_run)
    run_log = await system.run()

    # Exit met status code
    if run_log.error:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())