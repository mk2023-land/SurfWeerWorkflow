"""
Alert detection module.
Bevat 5 alert detectors die verschillende meteorologische patronen detecteren.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from src.data.models import AlertCandidate, AlertType, HourState, SurfWindow

logger = logging.getLogger(__name__)


class SwellArrivalDetector:
    """
    Type 1: Swell-arrival alert.

    Detecteert wanneer een nieuwe swell aankomt op basis van
    frequentie-verschuiving in A12 spectrum.
    """

    def __init__(self):
        self.name = "Swell Arrival Detector"
        self.alert_type = AlertType.SWELL_ARRIVAL

    def detect(
        self,
        history: list[HourState],
        current: HourState,
        buoy_history: dict[str, list] = None
    ) -> Optional[AlertCandidate]:
        """
        Detecteer swell arrival.

        Criteria:
        - A12 spectrum piek-frequentie verschoven naar lager (langere periode)
        - Piek-amplitude gestegen met >= 30%
        - Lead time: 6-12 uur voor kustaankomst

        Args:
            history: Historische HourStates (laatste 12 uren)
            current: Huidige HourState
            buoy_history: Boei data history (per station)

        Returns:
            AlertCandidate of None
        """
        if not buoy_history or 'A12' not in buoy_history:
            return None

        a12_data = buoy_history['A12']
        if len(a12_data) < 2:
            return None

        # B2 fix: voorheen `previous = a12_data[0]` — bij hours_back=48 met
        # 10-min raster betekent dat 48u terug, niet 12u, → chronische
        # false-positives bij voor-/najaarstransities. Nu: zoek de spectrum
        # die het dichtst bij `current - 12h` ligt, binnen ±2u tolerantie.
        current_spectrum = a12_data[-1]
        target_ts = current_spectrum.timestamp - timedelta(hours=12)

        def _strip_tz(d):
            return d.replace(tzinfo=None) if getattr(d, 'tzinfo', None) else d

        target_n = _strip_tz(target_ts)
        previous_spectrum = None
        best_delta = timedelta(hours=2)
        for spec in a12_data[:-1]:
            spec_n = _strip_tz(spec.timestamp)
            delta = abs(spec_n - target_n)
            if delta <= best_delta:
                best_delta = delta
                previous_spectrum = spec

        if previous_spectrum is None:
            logger.debug(
                "T1: geen A12 spectrum binnen 12h±2u tolerantie (have %d entries)",
                len(a12_data),
            )
            return None

        # Extraheer pieken
        current_peaks = current_spectrum.peaks
        previous_peaks = previous_spectrum.peaks

        if not current_peaks or not previous_peaks:
            return None

        # Vind dominant piek
        current_peak = max(current_peaks, key=lambda p: p.height_m)
        previous_peak = max(previous_peaks, key=lambda p: p.height_m)

        # Check criteria
        period_increase = current_peak.period_s - previous_peak.period_s
        height_increase = (current_peak.height_m - previous_peak.height_m) / max(previous_peak.height_m, 0.01)

        if period_increase >= 1.5 and height_increase >= 0.3:
            logger.info(f"Swell arrival detected: period {previous_peak.period_s}s → {current_peak.period_s}s, "
                       f"height {previous_peak.height_m}m → {current_peak.height_m}m")

            return AlertCandidate(
                alert_type=self.alert_type,
                window=None,  # Wordt later ingesteld
                detection_time=datetime.now(),
                explanation=f"New swell arriving: {current_peak.period_s}s period at {current_peak.height_m}m "
                          f"(was {previous_peak.period_s}s at {previous_peak.height_m}m 12h ago)",
                confidence=0.8
            )

        return None


class WindShiftDetector:
    """
    Type 2: Wind-shift alert.

    Detecteert koufront/trog passage met significante windrichting verandering.
    """

    def __init__(self):
        self.name = "Wind Shift Detector"
        self.alert_type = AlertType.WIND_SHIFT

    def detect(
        self,
        forecast: list[HourState],
        history: list[HourState] = None
    ) -> Optional[AlertCandidate]:
        """
        Detecteer wind shift.

        Criteria:
        - Wind richting verandert >= 45° binnen 6 uur
        - Na de shift is wind offshore (75°-135°) of side-offshore (135°-225°)
        - Wind snelheid na shift <= 12kn
        - Swell aanwezig (uit forecast of live boei)

        Args:
            forecast: Forecast HourStates (komende 48 uur)
            history: Historische HourStates (optioneel)

        Returns:
            AlertCandidate of None
        """
        if len(forecast) < 6:
            return None

        # Loop over forecast op zoek naar shift
        for i in range(5, len(forecast) - 6):
            hour_before = forecast[i - 5]
            forecast[i]
            hour_after = forecast[i + 6]

            # Bereken richting verschil (account voor 0/360 wraparound)
            dir_before = hour_before.wind.direction_deg
            dir_after = hour_after.wind.direction_deg

            diff = abs(dir_after - dir_before)
            if diff > 180:
                diff = 360 - diff

            # Check criteria
            if diff >= 45:
                # Check of nieuwe richting offshore/side-offshore is
                new_dir = hour_after.wind.direction_deg
                is_offshore = 75 <= new_dir <= 135
                is_side_offshore = 135 <= new_dir <= 225

                if (is_offshore or is_side_offshore) and hour_after.wind.speed_kn <= 12:
                    # Check swell aanwezigheid
                    has_swell = hour_after.wave_spectrum.significant_height_total >= 0.7

                    if has_swell:
                        logger.info(f"Wind shift detected: {dir_before}° → {dir_after}° "
                                   f"({diff}° change) at {hour_after.timestamp}")

                        return AlertCandidate(
                            alert_type=self.alert_type,
                            window=None,
                            detection_time=datetime.now(),
                            explanation=f"Wind shifting from {dir_before}° to {dir_after}° "
                                      f"(offshore), {hour_after.wind.speed_kn}kn wind",
                            confidence=0.7
                        )

        return None


class WindDipDetector:
    """
    Type 3: Wind-dip alert.

    Detecteert locale windstilte door synoptische storing.
    """

    def __init__(self):
        self.name = "Wind Dip Detector"
        self.alert_type = AlertType.WIND_DIP

    def detect(
        self,
        forecast: list[HourState],
        history: list[HourState] = None
    ) -> Optional[AlertCandidate]:
        """
        Detecteer wind dip.

        Criteria:
        - Locale minimum in wind speed (≥4kn drop onder omliggende 4u)
        - Minimum duurt >= 1 uur
        - Swell aanwezig (>= 0.7m)

        Args:
            forecast: Forecast HourStates (komende 48 uur)
            history: Historische HourStates (optioneel)

        Returns:
            AlertCandidate of None
        """
        if len(forecast) < 9:
            return None

        # Loop over forecast op zoek naar locale minima
        for i in range(4, len(forecast) - 4):
            current = forecast[i]
            surrounding = forecast[i-4:i+5]

            # Bereken gemiddelde van omliggende uren (exclusief current)
            surrounding_speeds = [h.wind.speed_kn for j, h in enumerate(surrounding) if j != 4]
            avg_surrounding = sum(surrounding_speeds) / len(surrounding_speeds)

            # Check criteria
            if current.wind.speed_kn <= (avg_surrounding - 4) and current.wind.speed_kn <= 12:
                # Check of dit minimum lang genoeg duurt
                dip_duration = 0
                for j in range(i, min(i + 4, len(forecast))):
                    if forecast[j].wind.speed_kn <= (avg_surrounding - 4):
                        dip_duration += 1
                    else:
                        break

                if dip_duration >= 1:
                    # Check swell aanwezigheid
                    has_swell = current.wave_spectrum.significant_height_total >= 0.7

                    if has_swell:
                        logger.info(f"Wind dip detected: {current.wind.speed_kn}kn vs "
                                   f"{avg_surrounding:.1f}kn average at {current.timestamp}")

                        return AlertCandidate(
                            alert_type=self.alert_type,
                            window=None,
                            detection_time=datetime.now(),
                            explanation=f"Wind dip: {current.wind.speed_kn}kn for {dip_duration}h "
                                      f"(vs {avg_surrounding:.1f}kn surrounding)",
                            confidence=0.6
                        )

        return None


class SustainedGroundswellDetector:
    """
    Type 4: Sustained groundswell alert.

    Detecteert aanhoudende groundswell op live boei.
    """

    def __init__(self):
        self.name = "Sustained Groundswell Detector"
        self.alert_type = AlertType.SUSTAINED_GROUNDSWELL

    def detect(
        self,
        buoy_history: dict[str, list],
        min_duration_hours: int = 3
    ) -> Optional[AlertCandidate]:
        """
        Detecteer sustained groundswell.

        Criteria:
        - IJG1 boei meet >= 9s periode én >= 0.7m swell
        - Aanhoudend >= 3 metingen achter elkaar
        - Real-time detection (geen forecast)

        Args:
            buoy_history: Boei data history (per station)
            min_duration_hours: Minimale duur in uren

        Returns:
            AlertCandidate of None
        """
        if 'IJG1' not in buoy_history:
            return None

        ijg1_data = buoy_history['IJG1']
        if len(ijg1_data) < min_duration_hours:
            return None

        # Check laatste N metingen
        recent_data = ijg1_data[-min_duration_hours:]

        consecutive_count = 0
        max_period = 0
        max_height = 0

        for spectrum in reversed(recent_data):
            # Vind groundswell component
            from src.scoring.deconstruct import decompose_spectrum
            decomposition = decompose_spectrum(spectrum)

            groundswell = decomposition['ground_swell']

            if groundswell and groundswell.period_s >= 9 and groundswell.height_m >= 0.7:
                consecutive_count += 1
                max_period = max(max_period, groundswell.period_s)
                max_height = max(max_height, groundswell.height_m)
            else:
                break

        if consecutive_count >= min_duration_hours:
            logger.info(f"Sustained groundswell detected: {max_period}s at {max_height}m "
                       f"for {consecutive_count} hours")

            return AlertCandidate(
                alert_type=self.alert_type,
                window=None,
                detection_time=datetime.now(),
                explanation=f"Sustained groundswell: {max_period}s period at {max_height}m "
                          f"for {consecutive_count} hours",
                confidence=0.9
            )

        return None


class TideGatedWindowDetector:
    """
    Type 5: Tide-gated window alert.

    Detecteert combinatie windows met gunstig tij.
    """

    def __init__(self):
        self.name = "Tide Gated Window Detector"
        self.alert_type = AlertType.TIDE_GATED

    def detect(
        self,
        windows: list[SurfWindow],
        forecast: Optional[list[HourState]] = None,
    ) -> Optional[AlertCandidate]:
        """
        Detecteer tide-gated windows.

        Criteria:
        - Window met score >= 75
        - Tijdens window: tide_norm in [0.3, 0.8] (gunstig tij)
        - Wind speed < 12kn tijdens window
        - Duur >= 1 uur

        Args:
            windows: Lijst van SurfWindow objecten
            forecast: Forecast HourStates — gebruikt om per uur tij/wind te checken.
                Zonder forecast kan de T5-conditie niet geverifieerd worden en
                wordt None geretourneerd.

        Returns:
            AlertCandidate of None
        """
        if not forecast:
            return None

        # Index forecast op timestamp zodat we per ScoreBreakdown de bijbehorende
        # HourState (met tij/wind) kunnen vinden.
        state_by_ts: dict[datetime, HourState] = {h.timestamp: h for h in forecast}

        for window in windows:
            if window.peak_score < 75:
                continue

            # Check tide en wind condities tijdens window
            all_good = True

            for score in window.hourly_scores:
                state = state_by_ts.get(score.timestamp)
                if state is None:
                    # Zonder onderliggende state kunnen we het niet verifiëren.
                    all_good = False
                    break

                tide_ok = 0.3 <= state.tide.normalized_level <= 0.8
                wind_ok = state.wind.speed_kn < 12

                if not (tide_ok and wind_ok):
                    all_good = False
                    break

            if all_good:
                logger.info(f"Tide-gated window detected: {window.peak_score} peak, "
                           f"{window.duration_hours:.1f}h duration")

                return AlertCandidate(
                    alert_type=self.alert_type,
                    window=window,
                    detection_time=datetime.now(),
                    explanation=f"Tide-gated window: {window.peak_score} peak score, "
                              f"{window.duration_hours:.1f}h duration with favorable tide",
                    confidence=0.75
                )

        return None


class AlertDetectorEngine:
    """
    Hoofd engine die alle detectors parallel uitvoert.
    """

    def __init__(self):
        self.detectors = {
            AlertType.SWELL_ARRIVAL: SwellArrivalDetector(),
            AlertType.WIND_SHIFT: WindShiftDetector(),
            AlertType.WIND_DIP: WindDipDetector(),
            AlertType.SUSTAINED_GROUNDSWELL: SustainedGroundswellDetector(),
            AlertType.TIDE_GATED: TideGatedWindowDetector()
        }

    def detect_all_with_candidates(
        self,
        forecast: list[HourState],
        history: list[HourState],
        buoy_history: dict[str, list] = None,
        windows: list[SurfWindow] = None
    ) -> tuple[set[AlertType], dict[AlertType, AlertCandidate]]:
        """
        Voer alle detectors uit en return (Set, Dict) zodat callers naar keuze
        de set van getriggerd-types kunnen gebruiken, of per type de bijbehorende
        `AlertCandidate` met zijn rijke `explanation` (voor LLM-prompt of titel-
        body fallback).

        De Set bevat alle AlertType-waarden waarvoor minimaal één route triggerde.
        De Dict mapt AlertType → AlertCandidate (de eerste candidate die we
        kregen voor dat type — in praktijk is dat steeds dezelfde detector).
        Voor SWELL_ARRIVAL geldt: de live-boei-route levert de candidate;
        de persisted-history-route triggert hooguit alleen de Set-entry.

        Returns:
            (triggered_alerts, candidates_by_type)
        """
        triggered_alerts: set[AlertType] = set()
        candidates: dict[AlertType, AlertCandidate] = {}

        # Type 1: Swell arrival (buoy history)
        if buoy_history:
            candidate = self.detectors[AlertType.SWELL_ARRIVAL].detect(history, forecast[0] if forecast else None, buoy_history)
            if candidate:
                triggered_alerts.add(AlertType.SWELL_ARRIVAL)
                candidates[AlertType.SWELL_ARRIVAL] = candidate

        # Type 1 (Sprint 3 #15): tweede route via persisted boei-spectrum history
        # (A12/K13 6u-trend). Volledig stil bij ontbrekende history-file.
        from src.scoring.trigger_T1 import detect_swell_arrival, load_history
        if detect_swell_arrival(load_history()):
            triggered_alerts.add(AlertType.SWELL_ARRIVAL)
            # Geen rijke candidate vanaf deze route — alleen de set-entry. De
            # eerste route boven heeft mogelijk al de Dict gevuld.

        # Type 2: Wind shift (forecast)
        candidate = self.detectors[AlertType.WIND_SHIFT].detect(forecast, history)
        if candidate:
            triggered_alerts.add(AlertType.WIND_SHIFT)
            candidates[AlertType.WIND_SHIFT] = candidate

        # Type 3: Wind dip (forecast)
        candidate = self.detectors[AlertType.WIND_DIP].detect(forecast, history)
        if candidate:
            triggered_alerts.add(AlertType.WIND_DIP)
            candidates[AlertType.WIND_DIP] = candidate

        # Type 4: Sustained groundswell (buoy history)
        if buoy_history:
            candidate = self.detectors[AlertType.SUSTAINED_GROUNDSWELL].detect(buoy_history)
            if candidate:
                triggered_alerts.add(AlertType.SUSTAINED_GROUNDSWELL)
                candidates[AlertType.SUSTAINED_GROUNDSWELL] = candidate

        # Type 5: Tide gated window (windows) — heeft forecast nodig om per uur
        # de tij/wind condities te verifiëren.
        if windows:
            candidate = self.detectors[AlertType.TIDE_GATED].detect(windows, forecast)
            if candidate:
                triggered_alerts.add(AlertType.TIDE_GATED)
                candidates[AlertType.TIDE_GATED] = candidate

        logger.info(f"Detected {len(triggered_alerts)} alert types: {[t.value for t in triggered_alerts]}")
        return triggered_alerts, candidates

    def detect_all(
        self,
        forecast: list[HourState],
        history: list[HourState],
        buoy_history: dict[str, list] = None,
        windows: list[SurfWindow] = None
    ) -> set[AlertType]:
        """
        Backwards-compat shim: voert detectie uit en retourneert alleen de Set
        van triggered types. Bestaande callers (main.py:269, tests die
        `detect_all` mocken met een Set-return) blijven werken zonder wijziging.

        Nieuwe code die de rijke per-detector `AlertCandidate.explanation`
        nodig heeft kan `detect_all_with_candidates` direct aanroepen.
        """
        triggered, _ = self.detect_all_with_candidates(
            forecast, history, buoy_history, windows
        )
        return triggered
