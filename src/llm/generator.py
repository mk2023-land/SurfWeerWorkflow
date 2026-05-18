"""
SMS generator module met Claude Haiku.

Bouwt structured-input voor Claude in fysische eenheden (meters, knopen, graden) —
NOOIT scores als golfhoogte/wind doorgeven, dat heeft eerder hallucinaties veroorzaakt
(score 51 werd "51m golfhoogte"). Stijl-template: Tobias van surfweer.nl.
"""
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
import anthropic

from src.config import ANTHROPIC_CONFIG
from src.data.models import (
    AlertCandidate,
    HourState,
    ScoreBreakdown,
    SurfWindow,
    SwellType,
)

logger = logging.getLogger(__name__)


_COMPASS_16 = ['N', 'NNO', 'NO', 'ONO', 'O', 'OZO', 'ZO', 'ZZO',
               'Z', 'ZZW', 'ZW', 'WZW', 'W', 'WNW', 'NW', 'NNW']

_DAY_NL = ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo']


def degrees_to_compass(deg: float) -> str:
    """Vertaal hoek (graden) naar 16-punts kompasrichting (NL)."""
    idx = int(((deg % 360) + 11.25) / 22.5) % 16
    return _COMPASS_16[idx]


def wind_label_for_noordwijk(wind_dir_deg: int) -> str:
    """Wind-categorie voor Noordwijk: offshore / side-offshore / onshore / side-onshore."""
    from src.config import WIND_DIRECTIONS
    d = wind_dir_deg % 360
    if WIND_DIRECTIONS['offshore'][0] <= d <= WIND_DIRECTIONS['offshore'][1]:
        return 'aflandig'
    if WIND_DIRECTIONS['side_offshore'][0] <= d <= WIND_DIRECTIONS['side_offshore'][1]:
        return 'zijaflandig'
    if WIND_DIRECTIONS['onshore'][0] <= d <= WIND_DIRECTIONS['onshore'][1]:
        return 'aanlandig'
    return 'zij-aanlandig'


SYSTEM_PROMPT = """Je schrijft korte surf-SMS'jes voor Noordwijk in de stijl van Tobias
van surfweer.nl. Bondig, surfers-jargon mag, geen overdrijving, geen voorbehouden.

STRIKTE REGELS:
1. Gebruik UITSLUITEND getallen die in de JSON-input staan. NIET interpoleren, NIET afronden.
2. Eenheden zijn EXPLICIET in de veldnaam:
     wave_height_m   → meters
     wave_period_s   → seconden
     wind_speed_kn   → knopen
     *_deg           → graden (kompas-label staat al voorgekookt in *_compass)
   Verzin NOOIT andere eenheden. Score-getallen (0-100) vermeld je niet in de SMS.
3. Bij type "digest" begin met "Nwijk [dag]:". Bij "alert" met "NWIJK ALERT [datum]".
4. Vermeld voor vandaag (en kort morgen): golfhoogte (m), periode (s), wind (kn + kompas).
5. Als best_window.is_surfable=false zeg dan "geen venster" of "flat" — geen tijdblok verzinnen.
6. Als is_surfable=true, gebruik EXACT start_time en end_time uit de input voor het tijdblok.
7. Houd onder 320 tekens (= 2 SMS).
8. Eindig met "Cam: surfweer.nl/webcams/noordwijk/"
9. Geen "denk ik", geen "waarschijnlijk", geen emoji.
"""


class SMSGenerator:
    """Genereert SMS berichten met Claude Haiku."""

    def __init__(self):
        if not ANTHROPIC_CONFIG['api_key']:
            logger.warning("No Anthropic API key configured, using fallback templates only")
            self.client = None
        else:
            self.client = anthropic.Anthropic(api_key=ANTHROPIC_CONFIG['api_key'])

    # ---------- public API ----------

    def generate_alert_sms(self, alert: AlertCandidate) -> str:
        if not self.client:
            return self._fallback_alert_template(alert)
        try:
            structured_input = self._prepare_alert_input(alert)
            return self._call_claude(structured_input) or self._fallback_alert_template(alert)
        except Exception as e:
            logger.error(f"Failed to generate alert SMS with Claude Haiku: {e}")
            return self._fallback_alert_template(alert)

    def generate_digest_sms(
        self,
        hour_states: List[HourState],
        scores: List[ScoreBreakdown],
        windows: List[SurfWindow],
        forecast_summary: Optional[Dict] = None,
    ) -> str:
        """
        Genereer digest-SMS op basis van uurstaten + scores + windows.

        Args:
            hour_states: Alle HourStates in chronologische volgorde (rij[0]=nu).
            scores:      Score-breakdowns, één-op-één met hour_states.
            windows:     Door analyze_windows() gedetecteerde surf-windows.
            forecast_summary: Optionele metadata (total/surfable hours).
        """
        if not self.client:
            return self._fallback_digest_template(hour_states, scores, windows)
        try:
            structured_input = self._prepare_digest_input(hour_states, scores, windows, forecast_summary or {})
            return self._call_claude(structured_input) or self._fallback_digest_template(hour_states, scores, windows)
        except Exception as e:
            logger.error(f"Failed to generate digest SMS with Claude Haiku: {e}")
            return self._fallback_digest_template(hour_states, scores, windows)

    # ---------- LLM call ----------

    def _call_claude(self, structured_input: Dict) -> Optional[str]:
        message = self.client.messages.create(
            model=ANTHROPIC_CONFIG['model'],
            max_tokens=ANTHROPIC_CONFIG['max_tokens'],
            temperature=ANTHROPIC_CONFIG['temperature'],
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": json.dumps(structured_input, indent=2, default=str),
            }],
        )
        sms_text = message.content[0].text.strip()
        logger.info(f"Generated SMS via Claude Haiku: {sms_text[:60]}...")
        return sms_text

    # ---------- input shaping ----------

    def _prepare_alert_input(self, alert: AlertCandidate) -> Dict:
        input_data: Dict = {
            "type": "alert",
            "date": alert.detection_time.strftime("%Y-%m-%d"),
            "trigger_types": [t.value for t in alert.window.triggers] if alert.window else [],
            "trigger_explanation": alert.explanation,
            "rarity": f"{alert.window.rarity_percentile:.0f}e percentile" if alert.window else "",
            "webcam_url": "https://surfweer.nl/webcams/noordwijk/",
        }
        if alert.window:
            input_data["window"] = {
                "start": alert.window.start.strftime("%H:%M"),
                "end": alert.window.end.strftime("%H:%M"),
                "duration_hours": round(alert.window.duration_hours, 1),
            }
        return input_data

    def _prepare_digest_input(
        self,
        hour_states: List[HourState],
        scores: List[ScoreBreakdown],
        windows: List[SurfWindow],
        forecast_summary: Dict,
    ) -> Dict:
        """
        Bouw structured input voor digest. Pakt voor vandaag (h0-h24) en morgen (h24-h48)
        respectievelijk de beste-window (indien surfable, score≥60) plus de piek-uur condities.
        """
        today = self._summarize_day(hour_states[:24], scores[:24], windows, day_offset=0)
        tomorrow = self._summarize_day(hour_states[24:48], scores[24:48], windows, day_offset=1)

        now = datetime.now()
        return {
            "type": "digest",
            "date": now.strftime("%Y-%m-%d"),
            "day_label_nl": _DAY_NL[now.weekday()],
            "today": today,
            "tomorrow": tomorrow,
            "forecast_summary": forecast_summary,
            "webcam_url": "https://surfweer.nl/webcams/noordwijk/",
        }

    def _summarize_day(
        self,
        day_states: List[HourState],
        day_scores: List[ScoreBreakdown],
        all_windows: List[SurfWindow],
        day_offset: int,
    ) -> Optional[Dict]:
        if not day_states or not day_scores:
            return None

        # Piek-uur op basis van score.
        peak_idx = max(range(len(day_scores)), key=lambda i: day_scores[i].total_score)
        peak_state = day_states[peak_idx]
        peak_score = day_scores[peak_idx]

        # Beste window in deze dag (binnen [day_start, day_end)).
        day_start = day_states[0].timestamp
        day_end = day_states[-1].timestamp
        day_windows = [
            w for w in all_windows
            if day_start <= w.peak_hour <= day_end
        ]
        best_window = max(day_windows, key=lambda w: w.peak_score) if day_windows else None

        result: Dict = {
            "peak_score_0_100": round(peak_score.total_score, 1),
            "is_surfable": peak_score.total_score >= 60,
            "peak_hour": self._hour_state_to_conditions(peak_state),
        }
        if best_window:
            result["best_window"] = {
                "is_surfable": True,
                "start_time": best_window.start.strftime("%H:%M"),
                "end_time": best_window.end.strftime("%H:%M"),
                "duration_hours": round(best_window.duration_hours, 1),
                "peak_score_0_100": int(best_window.peak_score),
            }
        else:
            result["best_window"] = {"is_surfable": False}
        return result

    def _hour_state_to_conditions(self, state: HourState) -> Dict:
        """Pak fysische condities uit HourState. Alles in expliciete eenheden."""
        spectrum = state.wave_spectrum
        dominant = max(spectrum.peaks, key=lambda p: p.height_m) if spectrum.peaks else None

        swell_type_label = None
        if dominant:
            swell_type_label = {
                SwellType.GROUND_SWELL: "groundswell",
                SwellType.WIND_SWELL:   "wind-swell",
                SwellType.WIND_SEA:     "wind-sea",
            }.get(dominant.type, "onbekend")

        wave_dir_deg = dominant.direction_deg if dominant else spectrum.mean_direction
        return {
            "time": state.timestamp.strftime("%H:%M"),
            "wave_height_m": round(spectrum.significant_height_total, 1),
            "wave_period_s": round(dominant.period_s if dominant else spectrum.mean_period, 1),
            "wave_direction_deg": int(wave_dir_deg),
            "wave_direction_compass": degrees_to_compass(wave_dir_deg),
            "swell_type": swell_type_label or "onbekend",
            "wind_speed_kn": round(state.wind.speed_kn, 1),
            "wind_direction_deg": int(state.wind.direction_deg),
            "wind_direction_compass": degrees_to_compass(state.wind.direction_deg),
            "wind_label": wind_label_for_noordwijk(state.wind.direction_deg),
            "tide_phase": state.tide.phase,
            "tide_level_m": round(state.tide.level_m, 2),
        }

    # ---------- fallback templates (gebruikt bij API-fout of validatie-falen) ----------

    def _fallback_alert_template(self, alert: AlertCandidate) -> str:
        if not alert.window:
            return f"NWIJK ALERT: {alert.explanation}. Cam: surfweer.nl/webcams/noordwijk/"
        time_str = f"{alert.window.start.strftime('%H:%M')}-{alert.window.end.strftime('%H:%M')}u"
        trigger_str = ", ".join([t.value for t in alert.window.triggers]) or "goede condities"
        return (f"NWIJK ALERT {alert.detection_time.strftime('%d-%m')} {time_str}: "
                f"{alert.window.peak_score}/100, {trigger_str}. "
                f"Cam: surfweer.nl/webcams/noordwijk/")

    def _fallback_digest_template(
        self,
        hour_states: List[HourState],
        scores: List[ScoreBreakdown],
        windows: List[SurfWindow],
    ) -> str:
        """Deterministische fallback in surfweer-stijl met echte fysische waardes."""
        if not hour_states or not scores:
            return ("Nwijk: geen data beschikbaar. "
                    "Cam: surfweer.nl/webcams/noordwijk/")

        now = datetime.now()
        day_label = _DAY_NL[now.weekday()]

        def fmt_day(states: List[HourState], day_scores: List[ScoreBreakdown], label: str) -> str:
            if not states or not day_scores:
                return f"{label} geen data"
            peak_idx = max(range(len(day_scores)), key=lambda i: day_scores[i].total_score)
            ps = states[peak_idx]
            spectrum = ps.wave_spectrum
            dom = max(spectrum.peaks, key=lambda p: p.height_m) if spectrum.peaks else None
            h = round(spectrum.significant_height_total, 1)
            p = round(dom.period_s if dom else spectrum.mean_period, 1)
            wind_dir = degrees_to_compass(ps.wind.direction_deg)
            wind_kn = round(ps.wind.speed_kn)

            day_windows = [w for w in windows
                           if states[0].timestamp <= w.peak_hour <= states[-1].timestamp]
            if day_windows:
                w = max(day_windows, key=lambda w: w.peak_score)
                window_str = f"{w.start.strftime('%H:%M')}-{w.end.strftime('%H:%M')}"
            else:
                window_str = "geen venster"
            return f"{label} {h}m {p}s {wind_dir}{wind_kn}kn, {window_str}"

        today = fmt_day(hour_states[:24], scores[:24], "vandaag")
        tomorrow = fmt_day(hour_states[24:48], scores[24:48], "morgen")

        return (f"Nwijk {day_label}: {today}. {tomorrow}. "
                f"Cam: surfweer.nl/webcams/noordwijk/")
