"""
SMS generator module met Claude Haiku 4.5.
Genereert natuurlijke SMS berichten in stijl van referentie-forecaster van de referentie-forecaster.
"""
import json
import logging
from datetime import datetime
from typing import Dict, Optional
import anthropic

from src.config import ANTHROPIC_CONFIG
from src.data.models import AlertCandidate, Decision, ScoreBreakdown

logger = logging.getLogger(__name__)

# System prompt in het Nederlands, referentie-forecaster stijl
SYSTEM_PROMPT = """Je schrijft korte surf-SMS'jes voor Noordwijk in de stijl van referentie-forecaster
van de referentie-forecaster. Bondig, surferslang oké, geen overdrijving.

STRIKTE REGELS:
1. Gebruik ALLEEN getallen die in de structured_input staan.
2. Verzin GEEN windrichtingen, golfhoogtes, periodes of tijden.
3. Houd berichten <320 tekens (= 2 SMS) waar mogelijk.
4. Bij type "alert": begin met "NWIJK ALERT [datum]".
5. Bij type "digest": begin met "Nwijk [dag]:".
6. Vermeld altijd: tijdvenster, golfhoogte, periode, windrichting+kracht.
7. Bij alert vermeld kort de REDEN:
   T1=swell aankomst, T2=wind draait aflandig,
   T3=windstilte-window, T4=groundswell door, T5=goede combo
8. Eindig met "Cam: surfweer.nl/webcams/noordwijk/"
9. Geen speculatie, geen "denk ik", geen voorbehouden anders dan al in input"""


class SMSGenerator:
    """Genereert SMS berichten met Claude Haiku 4.5."""

    def __init__(self):
        if not ANTHROPIC_CONFIG['api_key']:
            logger.warning("No Anthropic API key configured, using fallback templates only")
            self.client = None
        else:
            self.client = anthropic.Anthropic(api_key=ANTHROPIC_CONFIG['api_key'])

    def generate_alert_sms(self, alert: AlertCandidate) -> str:
        """
        Genereer SMS voor alert.

        Args:
            alert: AlertCandidate met alle details

        Returns:
            SMS tekst string
        """
        if not self.client:
            return self._fallback_alert_template(alert)

        try:
            # Bereid input voor
            structured_input = self._prepare_alert_input(alert)

            # Roep Claude Haiku aan
            message = self.client.messages.create(
                model=ANTHROPIC_CONFIG['model'],
                max_tokens=ANTHROPIC_CONFIG['max_tokens'],
                temperature=ANTHROPIC_CONFIG['temperature'],
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(structured_input, indent=2, default=str)
                    }
                ]
            )

            sms_text = message.content[0].text.strip()

            logger.info(f"Generated SMS using Claude Haiku: {sms_text[:50]}...")
            return sms_text

        except Exception as e:
            logger.error(f"Failed to generate SMS with Claude Haiku: {e}")
            return self._fallback_alert_template(alert)

    def generate_digest_sms(self, scores: list, forecast_summary: dict) -> str:
        """
        Genereer SMS voor daily digest.

        Args:
            scores: Lijst van scores voor komende dagen
            forecast_summary: Samenvatting van forecast

        Returns:
            SMS tekst string
        """
        if not self.client:
            return self._fallback_digest_template(scores, forecast_summary)

        try:
            # Bereid input voor
            structured_input = self._prepare_digest_input(scores, forecast_summary)

            # Roep Claude Haiku aan
            message = self.client.messages.create(
                model=ANTHROPIC_CONFIG['model'],
                max_tokens=ANTHROPIC_CONFIG['max_tokens'],
                temperature=ANTHROPIC_CONFIG['temperature'],
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(structured_input, indent=2, default=str)
                    }
                ]
            )

            sms_text = message.content[0].text.strip()

            logger.info(f"Generated digest SMS using Claude Haiku: {sms_text[:50]}...")
            return sms_text

        except Exception as e:
            logger.error(f"Failed to generate digest SMS with Claude Haiku: {e}")
            return self._fallback_digest_template(scores, forecast_summary)

    def _prepare_alert_input(self, alert: AlertCandidate) -> Dict:
        """Bereid structured input voor alert SMS."""
        input_data = {
            "type": "alert",
            "date": alert.detection_time.strftime("%Y-%m-%d"),
            "trigger_types": [t.value for t in alert.window.triggers] if alert.window else [],
            "trigger_explanation": alert.explanation,
            "rarity": f"{alert.window.rarity_percentile:.0f}e percentile" if alert.window else "",
            "webcam_url": "https://surfweer.nl/webcams/noordwijk/"
        }

        if alert.window:
            input_data["window"] = {
                "start": alert.window.start.strftime("%H:%M"),
                "end": alert.window.end.strftime("%H:%M"),
                "peak_score": alert.window.peak_score,
                "duration_hours": f"{alert.window.duration_hours:.1f}"
            }

            # Voeg peak hour conditions toe
            peak_hour = max(alert.window.hourly_scores, key=lambda s: s.total_score)
            input_data["conditions"] = self._extract_hour_conditions(peak_hour)

        return input_data

    def _prepare_digest_input(self, scores: list, forecast_summary: dict) -> Dict:
        """Bereid structured input voor digest SMS."""
        today_peak = max(scores[:24], key=lambda s: s.total_score) if len(scores) >= 24 else None
        tomorrow_peak = max(scores[24:48], key=lambda s: s.total_score) if len(scores) >= 48 else None

        input_data = {
            "type": "digest",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "webcam_url": "https://surfweer.nl/webcams/noordwijk/"
        }

        if today_peak:
            input_data["today"] = {
                "peak_score": today_peak.total_score,
                "conditions": self._extract_hour_conditions(today_peak)
            }

        if tomorrow_peak:
            input_data["tomorrow"] = {
                "peak_score": tomorrow_peak.total_score,
                "conditions": self._extract_hour_conditions(tomorrow_peak)
            }

        # Voeg forecast summary toe
        input_data["forecast_summary"] = forecast_summary

        return input_data

    def _extract_hour_conditions(self, score: ScoreBreakdown) -> Dict:
        """Extraheer condities uit een uur."""
        return {
            "wave_total_m": score.total_score,  # Placeholder
            "wind_kn": score.wind_score,  # Placeholder
            "wind_label": "offshore" if score.wind_score > 25 else "side-offshore" if score.wind_score > 15 else "onshore"
        }

    def _fallback_alert_template(self, alert: AlertCandidate) -> str:
        """Fallback template voor alerts."""
        if not alert.window:
            return f"NWIJK ALERT: {alert.explanation}. Cam: surfweer.nl/webcams/noordwijk/"

        time_str = f"{alert.window.start.strftime('%H:%M')}-{alert.window.end.strftime('%H:%M')}u"
        trigger_str = ", ".join([t.value for t in alert.window.triggers])

        return (f"NWIJK ALERT {alert.detection_time.strftime('%d-%m')} {time_str}: "
                f"{alert.window.peak_score}/100, {trigger_str}. "
                f"Cam: surfweer.nl/webcams/noordwijk/")

    def _fallback_digest_template(self, scores: list, forecast_summary: dict) -> str:
        """Fallback template voor digest."""
        today_peak = max(scores[:24], key=lambda s: s.total_score) if len(scores) >= 24 else None
        tomorrow_peak = max(scores[24:48], key=lambda s: s.total_score) if len(scores) >= 48 else None

        date_str = datetime.now().strftime("%a %d-%m")
        today_str = f"vandaag {today_peak.total_score}" if today_peak else "vandaag onbekend"
        tomorrow_str = f"morgen {tomorrow_peak.total_score}" if tomorrow_peak else "morgen onbekend"

        return f"Nwijk {date_str}: {today_str}, {tomorrow_str}. Cam: surfweer.nl/webcams/noordwijk/"