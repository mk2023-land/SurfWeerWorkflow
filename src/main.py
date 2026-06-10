"""
Hoofdscript voor Noordwijk Surf Alert Systeem.
Orkestreert data ophaling, scoring, alert detectie, en SMS verzending.
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from src.alerts.detectors import AlertDetectorEngine
from src.alerts.engine import AlertEngine
from src.baseline.seasonal import SeasonalBaselineBuilder
from src.config import ALERT_CONFIG, DEBUG, NOORDWIJK
from src.data.models import HourState, RunLog, ScoreBreakdown, SurfWindow, WindState
from src.data.sources.open_meteo import (
    _get_openmeteo_client,
    fetch_all_openmeteo_data,
)
from src.data.sources.rws import fetch_all_rws_data, tide_state_at
from src.llm.generator import SMSGenerator, fallback_reason_label
from src.llm.validator import SMSValidator
from src.notify import format_send_result_for_logging, get_notifier
from src.scoring.hourly import (
    compute_wind_spread_per_hour,
    score_hour_series,
)
from src.scoring.windows import analyze_windows, filter_alertworthy_windows

# Setup logging. Fix #6: RotatingFileHandler — surf_alert.log groeit anders
# unbounded (multi-MB per jaar) en blaast de GH Actions cache op (cache thrash
# + 10GB repo-limit risk). 2MB × 3 backups = harde 8MB cap voor logs.
_log_file_handler = RotatingFileHandler(
    'data/surf_alert.log',
    maxBytes=2 * 1024 * 1024,  # 2 MB per file
    backupCount=3,
)
logging.basicConfig(
    level=logging.INFO if DEBUG else logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        _log_file_handler,
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
        self._last_wind_spread_full: Optional[list[dict]] = None
        # Reden waarom de alert-detectie deze run gedegradeerd was (lege
        # forecast / geen boei-data), of None. Gezet in run(), gelezen door
        # _handle_digest om er een waarschuwingsregel op de digest te zetten.
        self._alert_search_degraded_reason: Optional[str] = None
        # Fix #1: seasonal baseline wordt in run() geladen — placeholder hier
        # zodat een vroege error niet op een ontbrekend attribuut faalt.
        self.seasonal_baseline: Optional[dict] = None
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

        # Fix #1: laad seasonal baseline VROEG in de run. Zonder baseline geeft
        # `calculate_rarity_percentile` altijd 50 → `rarity_percentile >= 70`
        # faalt altijd → geen enkele alert kan ooit firen. Zonder file laten
        # we het systeem doordraaien, maar met expliciete WARNING dat rarity
        # check effectief uit staat.
        try:
            baseline_builder = SeasonalBaselineBuilder()
            baseline = baseline_builder.load_baseline()
            if baseline:
                self.seasonal_baseline = baseline
                run_log.seasonal_baseline_loaded = True
                logger.info(
                    f"Seasonal baseline geladen: {len(baseline)} weken. "
                    f"Rarity-check actief."
                )
            else:
                self.seasonal_baseline = None
                logger.warning(
                    "Seasonal baseline ontbreekt → rarity_percentile valt terug "
                    "op default 50.0 voor alle windows. is_alertworthy.rarity-"
                    "check (>=70) zal NOOIT passen. Run "
                    "`python -m src.baseline.seasonal` om de baseline te bouwen."
                )
        except Exception as e:
            logger.warning(f"Baseline laden faalde, fallback naar None: {e}")
            self.seasonal_baseline = None

        try:
            # Stap 1: Haal data op (parallel)
            logger.info("Fetching data from all sources...")

            # Open-Meteo data opslaan
            try:
                openmeteo_data = await fetch_all_openmeteo_data(NOORDWIJK.lat, NOORDWIJK.lon)
                run_log.openmeteo_status = 'ok' if openmeteo_data else 'partial'
            except Exception as e:
                logger.error(f"Failed to fetch Open-Meteo data: {e}")
                openmeteo_data = None
                run_log.openmeteo_status = 'failed'

            # RWS data proberen (tijdelijk optioneel — API is verhuisd)
            rws_data = {}
            try:
                rws_data = await fetch_all_rws_data() or {}
                # 'ok' = primary boei aanwezig, 'partial' = alleen tide of EW-boeien,
                # 'failed' = niets bruikbaars.
                if rws_data.get('primary_buoy'):
                    run_log.rws_status = 'ok'
                elif rws_data.get('tide') or rws_data.get('early_warning_buoys'):
                    run_log.rws_status = 'partial'
                else:
                    run_log.rws_status = 'failed'
            except Exception as e:
                logger.warning(f"RWS data unavailable (API transition): {e}")
                run_log.rws_status = 'failed'

            # Fix #2: BIAS-CORRECTIE WIRING. Vergelijk laatste 3-6u boei vs model,
            # pas decay-correctie toe op de eerstvolgende 12-24u forecast. Voorheen
            # was alleen `log_bias_observation` gewired — de feature die "~22% RMSE
            # reductie" geeft was dood (in productie nooit gerund).
            try:
                from src.scoring.bias_correction import apply_bias_to_forecast, compute_buoy_bias
                boei_obs = (rws_data.get('primary_buoy') or {}).get('raw_data') or []
                marine_rows = (openmeteo_data or {}).get('marine') or []
                if boei_obs and marine_rows:
                    bias = compute_buoy_bias(boei_obs, marine_rows, when=datetime.now())
                    if bias:
                        corrected_marine = apply_bias_to_forecast(
                            marine_rows, bias, when=datetime.now()
                        )
                        openmeteo_data['marine'] = corrected_marine
                        run_log.bias_correction_applied = True
                        logger.info(
                            f"Bias-correctie toegepast: "
                            f"hs_factor={bias['hs_bias_factor']:.3f}, "
                            f"period_factor={bias['period_bias_factor']:.3f}, "
                            f"n={bias['n_samples']}"
                        )
                    else:
                        logger.info(
                            "Bias-correctie: onvoldoende samples, forecast ongecorrigeerd"
                        )
            except Exception as e:
                logger.warning(f"Bias-correctie wiring failed: {e}")

            # Stap 2: Bouw HourStates
            logger.info("Building hour states...")
            hour_states = self._build_hour_states(openmeteo_data, rws_data)

            if not hour_states:
                logger.error("No hour states created from data")
                run_log.error = "No data available"
                return run_log

            # Stap 3: Score elk uur — Sprint 2 stack via score_hour_series
            # met pressure-, cloud- en wind-spread-series voor context.
            logger.info("Scoring hours...")
            forecast_by_model = (openmeteo_data or {}).get('forecast') or {}
            primary_model = forecast_by_model.get('knmi_seamless') or []

            # Sprint 2 #8 — bereken per-uur wind-spread tussen modellen
            wind_spread_full = compute_wind_spread_per_hour(forecast_by_model)
            spread_by_ts = {entry['timestamp']: entry for entry in wind_spread_full}

            # Bouw parallel series (alleen voor uren die ook in hour_states zitten)
            pressure_series = []
            cloud_series = []
            wind_spread_series = []
            primary_by_ts = {row['timestamp']: row for row in primary_model}
            for st in hour_states:
                row = primary_by_ts.get(st.timestamp) or {}
                pressure_series.append(row.get('pressure') or 1013.0)
                cloud_series.append(row.get('cloud_cover'))
                wind_spread_series.append(spread_by_ts.get(st.timestamp) or {})

            hourly_scores = score_hour_series(
                hour_states,
                pressure_series=pressure_series,
                cloud_cover_series=cloud_series,
                wind_spread_series=wind_spread_series,
            )

            # Bewaar voor _handle_digest (Sprint 2 #8 — model spread → LLM)
            self._last_wind_spread_full = wind_spread_full

            # Stap 4: Analyseer windows
            # Fix #1: geef de seasonal baseline door zodat
            # `calculate_rarity_percentile` ECHTE percentiles berekent ipv
            # default 50. Zonder dit kon `is_alertworthy.rarity_percentile>=70`
            # NOOIT True worden → geen enkele alert kon firen.
            logger.info("Analyzing surf windows...")
            windows = analyze_windows(
                hourly_scores,
                seasonal_baseline=self.seasonal_baseline,
            )
            # alertworthy-filtering gebeurt PAS NA de alert-detectie hieronder:
            # de detectors leveren de triggers die elk window nodig heeft om
            # `is_alertworthy` (eist >=1 trigger) te kunnen halen. Filteren we
            # hier al, dan is het altijd leeg (windows hebben nog triggers=[]).

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

            # Sprint 3 #15 — append A12/K13 spectrum-snapshots naar history-jsonl
            # voor T1 swell-arrival detectie. Stille fail bij missing data.
            try:
                from src.scoring.trigger_T1 import append_buoy_snapshot
                append_buoy_snapshot({
                    'A12': buoy_history.get('A12') or [],
                    'K13': buoy_history.get('K13') or [],
                })
            except Exception as e:
                logger.warning(f"Buoy spectra history append failed: {e}")

            # Sprint 3 #16 — bias-log voor lange-termijn learning. Pakt model
            # marine-data + actuele boei-rows.
            try:
                from src.scoring.bias_correction import log_bias_observation
                marine_rows = (openmeteo_data or {}).get('marine') or []
                actual_obs = {
                    'IJG1': (rws_data.get('primary_buoy') or {}).get('raw_data') or [],
                    'A12':  ((rws_data.get('early_warning_buoys') or {}).get('A12') or {}).get('raw_data') or [],
                    'K13':  ((rws_data.get('early_warning_buoys') or {}).get('K13') or {}).get('raw_data') or [],
                }
                log_bias_observation(datetime.now(), marine_rows, actual_obs)
            except Exception as e:
                logger.warning(f"Bias log write failed: {e}")

            # Degradatie-signaal: de detectie KAN draaien maar mist kritieke
            # input. Dan is "geen alert" niet hetzelfde als "geen surf" — de
            # gebruiker wil expliciet horen dat de alert-zoektocht beperkt was.
            # We zetten alleen de vlag; surfacing gebeurt via een digest-prefix
            # (zie _handle_digest), zodat de niet-fatale degradatie niet de
            # harde failure-push misbruikt.
            if not forecast:
                run_log.alert_search_degraded = True
                run_log.alert_search_degraded_reason = 'lege forecast (geen modeldata vooruit)'
            elif not any(buoy_history.values()):
                run_log.alert_search_degraded = True
                run_log.alert_search_degraded_reason = 'geen boei-data (RWS) — swell-detectors uit'
            self._alert_search_degraded_reason = run_log.alert_search_degraded_reason
            if run_log.alert_search_degraded:
                logger.warning(
                    f"Alert-detectie gedegradeerd: {run_log.alert_search_degraded_reason}"
                )

            triggered_alerts = detector_engine.detect_all(
                forecast, history, buoy_history, windows
            )

            # KOPPEL de getriggerde alert-types aan de windows in de forecast-
            # regio. Zonder deze stap droeg elk window `triggers=[]` (windows
            # werden met een lege triggers_dict gebouwd), waardoor
            # `SurfWindow.is_alertworthy` — die >=1 trigger eist — ALTIJD False
            # was en er NOOIT een alert kon firen (state.json hield
            # last_alert_time=null). De oude post-hoc loop schreef alleen naar
            # een losse triggers_dict en raakte de window-objecten nooit aan.
            # We koppelen alleen aan forecast-windows (start >= forecast[0])
            # zodat historische condities geen alert kunnen veroorzaken.
            if triggered_alerts and forecast:
                forecast_start = forecast[0].timestamp
                trigger_list = list(triggered_alerts)
                for window in windows:
                    if window.start >= forecast_start:
                        window.triggers = list(trigger_list)

            # Filter PAS NU — na trigger-koppeling kan dit niet-leeg zijn.
            alertworthy_windows = filter_alertworthy_windows(windows)

            # Stap 6: Neem beslissing
            logger.info("Making decision...")
            is_digest_time = self.alert_engine.is_morning_first_run()

            decision = self.alert_engine.evaluate_forecast(
                forecast, history, buoy_history, windows, is_digest_time
            )

            # Fix #4: capture welke alert types triggered zijn vóór decision-
            # specifieke logica. Lijst van enum-values als strings.
            run_log.alert_types_detected = [t.value for t in triggered_alerts]

            # Decision-veld + boei-snapshot velden VROEG op run_log zetten
            # zodat _archive_sent_sms (in de blokken hieronder) zowel het
            # juiste type ("digest"/"alert") ALS de actuele IJG1/A12 buoy-
            # snapshot in het archief vastlegt. Voorheen werd dit pas in
            # _update_run_log na de notificatie-blokken gezet — resultaat:
            # alle archief-entries met decision="skip" en buoy_*=None,
            # ondanks dat de boei-data wel in rws_data zat. Issue mei 2026:
            # bias_log + forecasts_log toonden IJG1-records, maar sms_archive
            # bleef null. Daarom _update_run_log NU hier — daarna in stap 8
            # geen tweede call meer nodig (idempotent want zelfde input).
            self._update_run_log(run_log, hourly_scores, alertworthy_windows, decision, rws_data)

            # Leer-loop dataset-foundation: leg per forecast-dag onze features +
            # ons verdict voor Noordwijk vast, zodat een later binnenkomend
            # referentie-bericht op dezelfde datum gepaird kan worden (scripts/
            # calibrate.py). Best-effort; faalt nooit de run.
            try:
                self._log_forecast_features(hour_states, hourly_scores, windows)
            except Exception as e:
                logger.warning(f"Forecast-feature logging faalde: {e}")

            # Stap 7: Genereer en verstuur notificatie (mail of SMS)
            if decision.has_alert:
                logger.info("Generating and sending alert notification...")
                result = self._handle_alert(decision.send_alerts[0])
                run_log.sms_sent = format_send_result_for_logging(result)
                run_log.sms_text_full = result.get('message') if isinstance(result, dict) else None
                run_log.llm_used = True
                # Fix #4: validation status uit handle_alert result.
                if 'validation_passed' in result:
                    run_log.llm_validation_passed = bool(result.get('validation_passed'))
                if result.get('validation_issues'):
                    run_log.llm_validation_issues = list(result['validation_issues'])

                # Fix #3: record_alert PAS NA bevestigde send-success. Eerder
                # werd state al in `evaluate_forecast` bijgewerkt → bij notifier-
                # 5xx of validator-fail kreeg de gebruiker een ghost-cooldown
                # van 4u + ++ weekly counter terwijl er niets verzonden was.
                # Bij 5xx-spike kon dat de hele week aan alert-budget kosten.
                if result.get('success'):
                    self.alert_engine.state.record_alert(
                        ALERT_CONFIG['cooldown_hours_between_alerts']
                    )
                    self.alert_engine._save_state()
                    self.alert_engine.record_send(notify=True, llm=True)
                    if run_log.sms_text_full:
                        self._archive_sent_sms(run_log, run_log.sms_text_full)
                else:
                    logger.warning(
                        "Alert send failed — state NIET bijgewerkt om ghost-"
                        "cooldown te voorkomen. Result: %s",
                        result.get('error'),
                    )
                    # Echte send-fail op een live run → markeer als run-error zodat
                    # main() exit-1 geeft en de workflow `if: failure()`-push de
                    # gebruiker waarschuwt ("de alert-run werkte niet").
                    if not self.dry_run:
                        run_log.error = f"alert_send_failed: {result.get('error')}"

            elif decision.send_digest:
                logger.info("Generating and sending digest notification...")
                result = self._handle_digest(hour_states, hourly_scores, windows)
                run_log.sms_sent = format_send_result_for_logging(result)
                run_log.sms_text_full = result.get('message') if isinstance(result, dict) else None
                run_log.llm_used = True
                if 'validation_passed' in result:
                    run_log.llm_validation_passed = bool(result.get('validation_passed'))
                if result.get('validation_issues'):
                    run_log.llm_validation_issues = list(result['validation_issues'])

                # Fix #3: record_digest_sent ALLEEN na success. Anders raakt
                # `last_digest_time` gezet op een dag waarop niets verstuurd is
                # → volgende ochtend wordt de digest geblokkeerd door
                # is_morning_first_run's "vandaag al verstuurd"-check.
                if not result.get('success'):
                    logger.warning(
                        "Digest send failed — last_digest_time NIET bijgewerkt. "
                        "Result: %s", result.get('error'),
                    )
                    # Live send-fail → run-error zodat de workflow `if: failure()`-
                    # push afgaat. Op MANUAL_RUN niet (test, geen alarm nodig).
                    if not self.dry_run and os.getenv('MANUAL_RUN', '').lower() \
                            not in ('true', '1', 'yes'):
                        run_log.error = f"digest_send_failed: {result.get('error')}"
                elif os.getenv('MANUAL_RUN', '').lower() in ('true', '1', 'yes'):
                    # MANUAL_RUN=true (workflow_dispatch / lokale tests):
                    # verstuur wel maar pollueer last_digest_time NIET — anders
                    # blokkeert die de eerstvolgende scheduled cron-run. Ook
                    # NIET archiveren — handmatige tests horen niet in de
                    # trainings-set, alleen productie-digests.
                    logger.info("MANUAL_RUN=true → state.last_digest_time NIET geüpdatet, geen archief-entry")
                    self.alert_engine.record_send(notify=True, llm=True)
                else:
                    self.alert_engine.record_digest_sent()
                    self.alert_engine.record_send(notify=True, llm=True)
                    if run_log.sms_text_full:
                        self._archive_sent_sms(run_log, run_log.sms_text_full)

            else:
                logger.info(f"No action: {decision.skip_reason}")
                run_log.sms_sent = None

            # Stap 8: run_log al gepopuleerd vóór de notify-blokken
            # (zie commentaar boven _update_run_log call). Geen tweede call
            # nodig — alle velden zijn final op het moment dat we hier
            # aankomen. Logging van completion staat hier voor consistentie
            # met eerdere release-versies.

            logger.info(f"Run completed successfully in {(datetime.now() - start_time).total_seconds():.1f}s")

        except Exception as e:
            logger.exception("Run failed")
            run_log.error = str(e)

        finally:
            # Stap 9: Log run
            self._log_run(run_log)

        return run_log

    def _build_hour_states(self, openmeteo_data: dict, rws_data: dict) -> list[HourState]:
        """Bouw HourStates uit ruwe data.

        Pakt naast de basis-velden (wave/wind/tide) ook de nieuwe atmospheric-
        en ocean-context velden uit Open-Meteo Marine/Forecast en de storm-surge
        residual uit RWS. Latest IJG1 boei-sample overschrijft `peak_period_observed_s`
        en `directional_spread_deg` voor de eerste 3 nowcast-uren (t=0..2).
        """
        hour_states = []

        # Fix #7: catch-all `except Exception` swallowt programming-errors
        # (AttributeError/ValueError) → onmogelijk te debuggen. Hier vangen we
        # alleen data-shape-mismatches per-row (KeyError/IndexError/TypeError)
        # met DEBUG-log; programmatic errors propaganderen we naar boven zodat
        # ze in run() opgevangen worden en in de RunLog.error landen.
        if not openmeteo_data:
            logger.warning("No openmeteo_data — kan geen hour states bouwen")
            return []

        marine_data = openmeteo_data.get('marine', [])
        forecast_data = openmeteo_data.get('forecast', {})

        # Gebruik KNMI model als primary
        primary_model = forecast_data.get('knmi_seamless', [])

        if not marine_data or not primary_model:
            logger.warning("Missing marine or forecast data")
            return []

        tide_data = (rws_data or {}).get('tide') or {}
        openmeteo_client = _get_openmeteo_client()

        # Storm-surge scalar uit RWS — zelfde waarde voor alle uren in deze
        # run (simpele distributie; kan later granulair per uur).
        latest_surge_cm = None
        if tide_data:
            latest_surge_cm = tide_data.get('latest_surge_cm')

        # Recente IJG1 boei-observatie voor Tp + spread (nowcast-overlay).
        ijg1_raw_latest = None
        try:
            ijg1_raw = ((rws_data or {}).get('primary_buoy') or {}).get('raw_data') or []
            if ijg1_raw:
                ijg1_raw_latest = ijg1_raw[-1]
        except (KeyError, TypeError, IndexError) as e:
            logger.debug(f"IJG1 raw_data lookup failed (data-shape mismatch): {e}")
            ijg1_raw_latest = None
        if ijg1_raw_latest:
            logger.info(
                f"IJG1 latest sample: tp_s={ijg1_raw_latest.get('tp_s')}, "
                f"hmax_m={ijg1_raw_latest.get('hmax_m')}"
            )

        # Merge marine en forecast data per uur. Per-row: data-shape problemen
        # (KeyError/TypeError/IndexError) → DEBUG-log + skip; programmatic
        # errors (AttributeError, ValueError) bubbelen naar boven.
        for i in range(min(len(marine_data), len(primary_model))):
            try:
                marine = marine_data[i]
                weather = primary_model[i]

                # Skip als timestamps niet matchen
                if abs((marine['timestamp'] - weather['timestamp']).total_seconds()) > 3600:
                    continue

                wave_spectrum = openmeteo_client.marine_data_to_wave_spectrum(marine)

                # Boei-observatie overlay voor nowcast-uren (eerste 3): geef de
                # latest IJG1 Tp mee als "observed" override. Daarna blijft het
                # None — alleen forecast-data telt.
                # NB: S0BH (directional spread) wordt door RWS DDAPI20 voor onze
                # stations niet meer gepubliceerd; de overlay daarvoor is
                # verwijderd omdat `fetch_buoy_data` nooit `sobh_deg` in z'n
                # output dict zet.
                if ijg1_raw_latest and i < 3:
                    tp_obs = ijg1_raw_latest.get('tp_s')
                    if tp_obs is not None:
                        wave_spectrum.peak_period_observed_s = float(tp_obs)

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
                    confidence=1.0,
                    # Atmospheric context (uit primary forecast model)
                    air_temperature_c=weather.get('temperature'),
                    precipitation_mm=weather.get('precipitation'),
                    visibility_m=weather.get('visibility'),
                    weather_code=weather.get('weather_code'),
                    relative_humidity_pct=weather.get('relative_humidity'),
                    dew_point_c=weather.get('dew_point'),
                    uv_index=weather.get('uv_index'),
                    sunshine_duration_s=weather.get('sunshine_duration'),
                    # Atmospheric instability (gedeeld primary)
                    cape_jkg=weather.get('cape'),
                    lifted_index=weather.get('lifted_index'),
                    convective_inhibition_jkg=weather.get('convective_inhibition'),
                    boundary_layer_height_m=weather.get('boundary_layer_height'),
                    # Ocean context (marine + RWS surge scalar)
                    sea_surface_temperature_c=marine.get('sea_surface_temperature'),
                    ocean_current_velocity_ms=marine.get('ocean_current_velocity'),
                    ocean_current_direction_deg=marine.get('ocean_current_direction'),
                    sea_level_height_msl_m=marine.get('sea_level_height_msl'),
                    storm_surge_cm=latest_surge_cm,
                    wave_source=marine.get('wave_source', 'primary'),
                )

                hour_states.append(hour_state)
            except (KeyError, TypeError, IndexError) as e:
                # Data-shape mismatch in deze rij — skip en log op DEBUG zodat
                # we niet de logs vervuilen, maar wel kunnen tracen.
                logger.debug(
                    f"_build_hour_states: skip rij i={i} door data-mismatch "
                    f"({type(e).__name__}: {e})"
                )
                continue

        return hour_states

    def _handle_alert(self, alert) -> dict:
        """
        Genereer en verstuur alert-notificatie.

        B7: alerts gaan 4×/dag mogelijk uit en zijn vertrouwens-kritiek.
        Twee validatie-lagen:
          1. `validate_sms` (anti-hallucinatie): bij faal → ABORT, geen
             alert sturen. Een verkeerde alert erodeert gebruikersvertrouwen
             sneller dan een gemiste alert.
          2. `validate_alert_format` (prefix + datum-pattern): bij faal →
             log warning, alert wordt nog wel verstuurd (kosmetisch issue).
        """
        sms_text = self.sms_generator.generate_alert_sms(alert)

        anti_hallucination = self.sms_validator.validate_sms(
            sms_text,
            self.sms_generator._prepare_alert_input(alert)
        )
        if not anti_hallucination:
            logger.error(
                "Alert anti-hallucinatie validatie FAILED — alert wordt "
                "NIET verzonden. Issues: %s. Tekst was: %r",
                anti_hallucination.issues, sms_text,
            )
            return {
                'success': False,
                'channel': self.notifier.channel,
                'error': 'validation_failed',
                'validation_passed': False,
                'validation_issues': anti_hallucination.issues,
                'message': sms_text,
            }

        format_ok = self.sms_validator.validate_alert_format(sms_text)
        if not format_ok:
            logger.warning(
                "Alert format-check faalde (%s) — toch verstuurd want "
                "anti-hallucinatie passed.", format_ok.issues,
            )

        # Nood-template-waarschuwing — spiegelt _handle_digest. generate_alert_sms
        # zet last_fallback_reason zodra Claude niet de tekst leverde (geen key,
        # API-fout, credits op, of validatie 3× afgekeurd). De gebruiker wil dit
        # expliciet horen. Prefix NA de format-validatie hierboven, zodat de
        # waarschuwingsregel de prefix/datum-check van het echte bericht niet
        # verstoort.
        fallback_reason = getattr(self.sms_generator, 'last_fallback_reason', None)
        extra_issues: list[str] = []
        if fallback_reason:
            reason_txt = fallback_reason_label(fallback_reason)
            logger.error(f"ALERT OP NOOD-TEMPLATE — reden: {fallback_reason}")
            sms_text = f"LET OP nood-template ({reason_txt}), geen Claude:\n{sms_text}"
            extra_issues = [f"llm_fallback:{fallback_reason}"]

        if not self.dry_run:
            result = self.notifier.send_alert(sms_text)
            # Fix #4: voeg validation-status toe aan result voor RunLog audit.
            result.setdefault('validation_passed', not fallback_reason)
            result.setdefault('validation_issues', extra_issues)
            if fallback_reason:
                result.setdefault('llm_fallback_reason', fallback_reason)
            return result
        return {
            'success': True,
            'debug_mode': True,
            'channel': self.notifier.channel,
            'message': sms_text,
            'validation_passed': not fallback_reason,
            'validation_issues': extra_issues,
            'llm_fallback_reason': fallback_reason,
        }

    def _handle_digest(
        self,
        hour_states: list[HourState],
        hourly_scores: list[ScoreBreakdown],
        windows: list[SurfWindow],
    ) -> dict:
        """
        Genereer en verstuur digest SMS.

        B7: ook digest moet de anti-hallucinatie validator passeren.
        Bij faal → fallback-template (deterministische digest zonder LLM).
        """
        forecast_summary = {
            'total_hours': len(hourly_scores),
            'surfable_hours': len([s for s in hourly_scores if s.is_surfable()])
        }

        sms_text = self.sms_generator.generate_digest_sms(
            hour_states, hourly_scores, windows, forecast_summary,
            wind_spread_series=getattr(self, '_last_wind_spread_full', None),
        )

        # Fix #4: track validation-uitkomst zodat RunLog deze kan loggen.
        validation_passed = True
        validation_issues: list[str] = []

        # Anti-hallucinatie checks zijn nu inside `generate_digest_sms` via
        # `_generate_with_retry` (3× retry met validator-feedback). Hier nog
        # alleen format-sanity (Nwijk/Surf-update Noordwijk prefix + dag-afkorting).
        format_ok = self.sms_validator.validate_digest_format(sms_text)
        if not format_ok:
            logger.warning(
                f"Digest format validation failed: {format_ok.issues}, fallback gebruikt"
            )
            sms_text = self.sms_generator._fallback_digest_template(
                hour_states, hourly_scores, windows
            )
            validation_passed = False
            validation_issues = list(format_ok.issues)

            # Her-valideer de fallback zelf: bij een lege/degenererende digest
            # (bv. geen rijdbare dagen → "Nwijk: geen data.") faalt óók de
            # fallback de format-check. Dan NIETS verzenden i.p.v. een door de
            # eigen validator afgekeurd bericht de deur uit te sturen.
            fallback_ok = self.sms_validator.validate_digest_format(sms_text)
            if not fallback_ok:
                logger.error(
                    "Digest-fallback faalt óók format-validatie %s — niet "
                    "verzonden. Tekst: %r", fallback_ok.issues, sms_text,
                )
                return {
                    'success': False,
                    'channel': self.notifier.channel,
                    'error': 'digest_format_invalid_after_fallback',
                    'validation_passed': False,
                    'validation_issues': list(fallback_ok.issues),
                    'message': sms_text,
                }

        # Nood-template-waarschuwing. generate_digest_sms zet last_fallback_reason
        # zodra Claude niet de tekst leverde (geen key, API-fout, credits op, of
        # 3× door de validator afgekeurd). De gebruiker wil dit expliciet horen —
        # we prefixen een regel ZODAT het via het normale SMS-kanaal binnenkomt.
        # Ná de format-validatie hierboven, want die eist een 'Nwijk'/'Surfweer'-start.
        fallback_reason = getattr(self.sms_generator, 'last_fallback_reason', None)
        if fallback_reason:
            reason_txt = fallback_reason_label(fallback_reason)
            logger.error(f"DIGEST OP NOOD-TEMPLATE — reden: {fallback_reason}")
            sms_text = f"LET OP nood-template ({reason_txt}), geen Claude:\n{sms_text}"
            validation_passed = False
            validation_issues = (validation_issues or []) + [f"llm_fallback:{fallback_reason}"]
            run_log_note = f"llm_fallback:{fallback_reason}"
        else:
            run_log_note = None

        # Degradatie-waarschuwing: de alert-detectie miste deze run kritieke
        # input (lege forecast / geen boei-data). Net als de nood-template-prefix
        # NA de format-validatie toegevoegd. Zo weet de gebruiker dat een rustige
        # digest deels komt doordat de alert-zoektocht beperkt was — niet puur
        # doordat er geen surf is.
        degraded_reason = getattr(self, '_alert_search_degraded_reason', None)
        if degraded_reason:
            sms_text = (
                f"LET OP: alert-detectie beperkt ({degraded_reason}) — "
                f"alerts mogelijk gemist.\n{sms_text}"
            )
            validation_issues = (validation_issues or []) + [
                f"alert_search_degraded:{degraded_reason}"
            ]

        if not self.dry_run:
            result = self.notifier.send_digest(sms_text)
            result.setdefault('validation_passed', validation_passed)
            result.setdefault('validation_issues', validation_issues)
            if run_log_note:
                result.setdefault('llm_fallback_reason', fallback_reason)
            return result
        return {
            'success': True,
            'debug_mode': True,
            'channel': self.notifier.channel,
            'message': sms_text,
            'validation_passed': validation_passed,
            'validation_issues': validation_issues,
            'llm_fallback_reason': fallback_reason,
        }

    def _update_run_log(
        self,
        run_log: RunLog,
        hourly_scores: list[ScoreBreakdown],
        windows: list[SurfWindow],
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
        """Log run naar JSONL bestand met line-count rotatie."""
        from src.util_files import append_jsonl_with_rotation
        append_jsonl_with_rotation(
            Path('data/forecasts_log.jsonl'),
            run_log.to_dict(),
            max_lines=10000,
            keep_archives=3,
        )

    def _log_forecast_features(self, hour_states, hourly_scores, windows):
        """Append per-dag feature-snapshot voor Noordwijk → git-persistente jsonl.

        DATASET-FOUNDATION voor de leer-loop: elke run legt ONZE features (de
        fysische condities op het piek-uur) + ONS verdict per forecast-dag vast.
        De referentie-LABELS komen los uit het archief (privé referentie-archief); scripts/
        calibrate.py joint beide op datum en fit de scoring-parameters daarop.
        Bewust géén referentie-label hier — dit is puur onze kant, deterministisch
        reproduceerbaar, en groeit met elke run mee.
        """
        from collections import defaultdict
        from src.config import WIND_FACE_PENALTY
        from src.scoring.wave_modifiers import (
            _offshore_groom,
            partition_energy_components,
        )
        from src.scoring.wind import _wind_direction_cosine, wave_face_quality
        from src.util_files import append_jsonl_with_rotation
        if not hour_states or not hourly_scores:
            return

        by_day: dict = defaultdict(list)
        for st, sc in zip(hour_states, hourly_scores):
            by_day[st.timestamp.date()].append((st, sc))

        run_ts = datetime.now()
        path = Path('data/forecast_features.jsonl')
        for day, pairs in sorted(by_day.items()):
            st_peak, sc_peak = max(pairs, key=lambda p: p[1].total_score)
            ws = st_peak.wave_spectrum
            hs = getattr(ws, 'significant_height_total', None)
            tp = getattr(ws, 'peak_period_observed_s', None) or getattr(ws, 'mean_period', None)
            tide = st_peak.tide
            day_windows = [
                w for w in windows
                if w.start.date() == day or w.end.date() == day
            ]
            kinds = {w.kind for w in day_windows}
            verdict = (
                'surfable' if 'surfable' in kinds
                else 'longboard' if 'longboard' in kinds
                else 'flat'
            )
            # Re-score-basis: genoeg om de golf-score van dit piek-uur EXACT te
            # herberekenen onder geleerde parameters (WIND_FACE_PENALTY strength,
            # PARTITION wind_sea_multiplier), zodat scripts/calibrate.py de
            # score-componenten kan fitten i.p.v. alleen de drempels. De wind/tij/
            # richting-delen blijven vast; alleen de golf-keten wordt her-gescoord.
            cos_off = _wind_direction_cosine(
                st_peak.wind.direction_deg, NOORDWIJK.beach_normal_deg
            )
            parts = partition_energy_components(ws, cos_off, st_peak.wind.speed_kn)
            score_basis = {
                'golf_score': round(sc_peak.golf_score, 2),
                'wind_score': round(sc_peak.wind_score, 2),
                'tide_score': round(sc_peak.tide_score, 2),
                'swell_dir_bonus': round(sc_peak.swell_dir_bonus, 2),
                'confidence': round(getattr(sc_peak, 'confidence', 1.0), 4),
                'face_q': round(wave_face_quality(st_peak.wind.speed_kn, cos_off), 4),
                'wfp_strength': WIND_FACE_PENALTY['strength'],
                'swell_h_m': round(parts['swell_height_m'], 3),
                'windsea_h_m': round(parts['wind_sea_height_m'], 3),
                'eff_height_m': round(parts['effective_height_m'], 3),
                'groom': round(_offshore_groom(cos_off, st_peak.wind.speed_kn), 4),
                'dominant_tp_s': round(parts['dominant_period_s'], 2),
            }
            record = {
                'run_timestamp': run_ts.isoformat(),
                'forecast_date': day.isoformat(),
                'day_offset': (day - run_ts.date()).days,
                'spot': 'noordwijk',
                # Features op het piek-score-uur (model-inputs voor de leer-loop)
                'peak_hour': st_peak.timestamp.strftime('%H:%M'),
                'hs_m': round(hs, 2) if hs is not None else None,
                'tp_s': round(tp, 1) if tp is not None else None,
                'wind_speed_kn': round(st_peak.wind.speed_kn, 1),
                'wind_dir_deg': int(st_peak.wind.direction_deg),
                'offshore_cos': round(
                    _wind_direction_cosine(
                        st_peak.wind.direction_deg, NOORDWIJK.beach_normal_deg
                    ), 3,
                ),
                'tide_level_norm': (
                    round(tide.normalized_level, 2)
                    if getattr(tide, 'normalized_level', None) is not None else None
                ),
                'tide_phase': str(getattr(tide, 'phase', None)),
                # Onze uitkomst (te vergelijken met het referentie-label)
                'our_peak_score': sc_peak.total_score,
                'our_verdict': verdict,
                'our_windows': [
                    f"{w.start.strftime('%H')}-{w.end.strftime('%H')}u"
                    for w in day_windows
                ],
                # Her-scoorbare golf-keten voor de leer-loop (zie boven).
                'score_basis': score_basis,
            }
            append_jsonl_with_rotation(
                path, record, max_lines=20000, keep_archives=2,
            )

    def _archive_sent_sms(self, run_log: RunLog, sms_text: str):
        """
        Persisteer een succesvol verzonden SMS naar het maand-archief in git.

        Doel: trainings-set opbouwen voor latere model-fine-tuning. Naast
        forecasts_log.jsonl (die door cache afhankelijk is) committeren we
        de SMS-tekst + meetbare condities permanent naar git. Eén bestand
        per kalendermaand (YYYY-MM.jsonl) om commits beheersbaar te houden.

        Wordt alleen aangeroepen na .get('success') op een digest of alert.
        """
        from src.util_files import append_jsonl_with_rotation
        ts = datetime.now()
        month_file = Path('data/sms_archive') / f"{ts.strftime('%Y-%m')}.jsonl"
        entry = {
            'timestamp': ts.isoformat(),
            'decision': run_log.decision,
            'alert_types': run_log.alert_types_detected or [],
            'sms_text': sms_text,
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
        # Geen rotatie nodig — één file per maand stops vanzelf bij ~120
        # entries (4 runs/dag × 30 dagen). max_lines=10000 puur defensief.
        append_jsonl_with_rotation(
            month_file, entry,
            max_lines=10000, keep_archives=1,
        )


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
