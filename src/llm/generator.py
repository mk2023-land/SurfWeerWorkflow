"""
SMS generator module met Claude Haiku.

Bouwt structured-input voor Claude in fysische eenheden (meters, knopen, graden) —
NOOIT scores als golfhoogte/wind doorgeven, dat heeft eerder hallucinaties veroorzaakt
(score 51 werd "51m golfhoogte"). Stijl-template: Tobias van surfweer.nl.

Digest is multi-day (vandaag + 3 dagen vooruit) en bevat per dag de beste window,
piek-condities, tij-richting (opkomend/afgaand) en eerstvolgende hoog/laag, plus
een lokale spring/dood-tij notitie op basis van maan-fase.
"""
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import anthropic

from src.config import ANTHROPIC_CONFIG, NOORDWIJK
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

_DAY_NL_SHORT = ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo']


def degrees_to_compass(deg: float) -> str:
    """Vertaal hoek (graden) naar 16-punts kompasrichting (NL)."""
    idx = int(((deg % 360) + 11.25) / 22.5) % 16
    return _COMPASS_16[idx]


def wind_label_for_noordwijk(wind_dir_deg: int) -> str:
    """Wind-categorie voor Noordwijk: aflandig / zijaflandig / aanlandig / zij-aanlandig."""
    from src.config import WIND_DIRECTIONS
    d = wind_dir_deg % 360
    if WIND_DIRECTIONS['offshore'][0] <= d <= WIND_DIRECTIONS['offshore'][1]:
        return 'aflandig'
    if WIND_DIRECTIONS['side_offshore'][0] <= d <= WIND_DIRECTIONS['side_offshore'][1]:
        return 'zijaflandig'
    if WIND_DIRECTIONS['onshore'][0] <= d <= WIND_DIRECTIONS['onshore'][1]:
        return 'aanlandig'
    return 'zij-aanlandig'


def is_blocked_by_ijmuiden_pier(swell_dir_deg: int) -> bool:
    """True als swell-richting binnen de NNO-sector valt die door IJmuiden-pier wordt afgeschermd."""
    blocked_min = NOORDWIJK.blocked_swell_dir_min
    blocked_max = NOORDWIJK.blocked_swell_dir_max
    if blocked_min == 0 and blocked_max == 0:
        return False
    d = swell_dir_deg % 360
    if blocked_min <= blocked_max:
        return blocked_min <= d <= blocked_max
    return d >= blocked_min or d <= blocked_max


def moon_phase_info(when: datetime) -> Tuple[float, str, bool]:
    """
    Simpele maan-fase berekening (synodische maand 29.53 dagen, referentie nieuwe maan
    2000-01-06 18:14 UTC). Goed genoeg voor "springtij of niet".

    Returns:
        (phase_age_days, label_nl, is_spring_tide).
        is_spring_tide = binnen 2 dagen van nieuwe of volle maan.
    """
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    when_utc = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)
    days = (when_utc - ref).total_seconds() / 86400.0
    age = days % 29.530588
    # Labels per ~3.7-dagen kwart.
    if age < 1.85 or age >= 27.68:
        label = 'nieuwe maan'
    elif age < 5.54:
        label = 'wassende sikkel'
    elif age < 9.23:
        label = 'eerste kwartier'
    elif age < 12.92:
        label = 'wassende maan'
    elif age < 16.61:
        label = 'volle maan'
    elif age < 20.30:
        label = 'afnemende maan'
    elif age < 23.99:
        label = 'laatste kwartier'
    else:
        label = 'afnemende sikkel'
    # Springtij-venster: <2 dagen rond nieuwe maan (0/29.53) of volle maan (14.77).
    distance_new = min(age, 29.530588 - age)
    distance_full = abs(age - 14.765)
    is_spring = distance_new < 2.0 or distance_full < 2.0
    return age, label, is_spring


SYSTEM_PROMPT = """Je schrijft surf-SMS'jes voor Noordwijk in de stijl van Tobias van
surfweer.nl. Lopende zinnen, surfers-jargon mag, géén overdrijving, géén voorbehouden.

STIJL & LENGTE:
- Schrijf SPREEKTAAL, geen telegram-stijl. Volle zinnen, mag een grapje, mag een korte
  duiding ("wind blijft te hard", "swell loopt af", "mss zaterdag wat nieuws").
- Begin met "Nwijk [day_label_today]: " (of "NWIJK ALERT [datum]" bij alerts).
- Max ~480 tekens (= 3 SMS-segmenten). Liever rond de 300-450, niet te kort.

PER DAG IN `days` (vandaag → +3, dus 4 dagen) schrijf je ÉÉN VOLLE ZIN met:
1. Een tijdsaanduiding:
   - Surfable (best_window.is_surfable=true): noem het tijdblok "start_time-end_time".
     ALS best_window.duration_hours > 3 noem je ALTIJD ook best_window.peak_time als
     het top-moment binnen het venster ("top rond 10u", "piek 14:00", o.i.d.).
   - Anders: gebruik peak_hour.time als anker (bv. "rond 14u") of zeg "flat".
2. Golfhoogte (m) + periode (s) + golfrichting (wave_direction_compass).
3. Wind: speed (kn) + wind_direction_compass + wind_label (aflandig / zijaflandig /
   aanlandig).
4. Tij: phase_at_peak (opgaand of afgaand). Mag aangevuld met next_high_time of
   next_low_time als die op die dag valt.

EXTRA SIGNALEN (kort vermelden wanneer relevant):
- tide_context.spring_tide=true → "springtij" (sterker stroming, korter window).
- peak_hour.swell_refracts_around_ijmuiden=true → noem dat de pier van IJmuiden
  hindert / afschermt (klassieke NNO-refractie).
- peak_hour.swell_type="groundswell" → benoem groundswell + periode ("8s groundswell").

STRIKTE REGELS:
1. Gebruik UITSLUITEND getallen die in de JSON-input staan. Niet interpoleren, niet
   afronden. Eenheden zijn EXPLICIET in de veldnaam (m/s/kn/deg). Score-getallen
   (0-100) vermeld je NIET in de SMS.
2. Eindig met " Cam: surfweer.nl/webcams/noordwijk/"
3. Geen "denk ik" / "waarschijnlijk" / "misschien" / emoji / disclaimers.
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
        logger.info(f"Generated SMS via Claude Haiku: {sms_text[:80]}...")
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
            peak_hour_score = max(alert.window.hourly_scores, key=lambda s: s.total_score)
            input_data["window"] = {
                "start": alert.window.start.strftime("%H:%M"),
                "end": alert.window.end.strftime("%H:%M"),
                "duration_hours": round(alert.window.duration_hours, 1),
                "peak_time": peak_hour_score.timestamp.strftime("%H:%M"),
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
        Multi-day digest: vandaag + 3 dagen vooruit. Per dag: peak_hour-condities,
        beste window (indien surfable), tij-richting + eerstvolgende hoog/laag,
        en springtij-context.
        """
        days = self._group_by_day(hour_states, scores)
        day_blocks: List[Dict] = []
        labels = ["vandaag", "morgen", "overmorgen", "+3"]

        for i, (date_obj, day_states, day_scores) in enumerate(days[:4]):
            if not day_states or not day_scores:
                continue
            label = labels[i] if i < len(labels) else date_obj.strftime("%a %d/%m")
            day_blocks.append(self._summarize_day(
                day_states, day_scores, windows,
                date_obj=date_obj, label_nl=label
            ))

        now = datetime.now()
        _, moon_label, is_spring = moon_phase_info(now)

        return {
            "type": "digest",
            "date_today": now.strftime("%Y-%m-%d"),
            "day_label_today": _DAY_NL_SHORT[now.weekday()],
            "days": day_blocks,
            "tide_context": {
                "moon_phase_nl": moon_label,
                "spring_tide": is_spring,
                "spring_tide_label": "springtij" if is_spring else None,
            },
            "forecast_summary": forecast_summary,
            "webcam_url": "https://surfweer.nl/webcams/noordwijk/",
        }

    def _group_by_day(
        self,
        hour_states: List[HourState],
        scores: List[ScoreBreakdown],
    ) -> List[Tuple]:
        """Groepeer (state, score) op kalenderdag in chronologische volgorde."""
        groups: Dict = {}
        for s, sc in zip(hour_states, scores):
            d = s.timestamp.date()
            groups.setdefault(d, ([], []))
            groups[d][0].append(s)
            groups[d][1].append(sc)
        return [(d, *groups[d]) for d in sorted(groups.keys())]

    def _summarize_day(
        self,
        day_states: List[HourState],
        day_scores: List[ScoreBreakdown],
        all_windows: List[SurfWindow],
        date_obj,
        label_nl: str,
    ) -> Dict:
        peak_idx = max(range(len(day_scores)), key=lambda i: day_scores[i].total_score)
        peak_state = day_states[peak_idx]
        peak_score = day_scores[peak_idx]

        day_windows = [w for w in all_windows
                       if day_states[0].timestamp <= w.peak_hour <= day_states[-1].timestamp]
        best_window = max(day_windows, key=lambda w: w.peak_score) if day_windows else None

        peak_conditions = self._hour_state_to_conditions(peak_state)

        result: Dict = {
            "label_nl": label_nl,
            "date": date_obj.strftime("%Y-%m-%d"),
            "day_short": _DAY_NL_SHORT[date_obj.weekday()],
            "is_surfable": peak_score.total_score >= 60,
            "peak_score_0_100": round(peak_score.total_score, 1),
            "peak_hour": peak_conditions,
            "tide_summary": self._tide_summary_for_day(day_states, peak_state),
        }
        if best_window:
            result["best_window"] = {
                "is_surfable": True,
                "start_time": best_window.start.strftime("%H:%M"),
                "end_time": best_window.end.strftime("%H:%M"),
                "duration_hours": round(best_window.duration_hours, 1),
                "peak_time": best_window.peak_hour.strftime("%H:%M"),
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

        wave_dir_deg = dominant.direction_deg if dominant else int(spectrum.mean_direction)
        return {
            "time": state.timestamp.strftime("%H:%M"),
            "wave_height_m": round(spectrum.significant_height_total, 1),
            "wave_period_s": round(dominant.period_s if dominant else spectrum.mean_period, 1),
            "wave_direction_deg": int(wave_dir_deg),
            "wave_direction_compass": degrees_to_compass(wave_dir_deg),
            "swell_type": swell_type_label or "onbekend",
            "swell_refracts_around_ijmuiden": is_blocked_by_ijmuiden_pier(int(wave_dir_deg)),
            "wind_speed_kn": round(state.wind.speed_kn, 1),
            "wind_gust_kn": round(state.wind.gusts_kn, 1) if state.wind.gusts_kn else None,
            "wind_direction_deg": int(state.wind.direction_deg),
            "wind_direction_compass": degrees_to_compass(state.wind.direction_deg),
            "wind_label": wind_label_for_noordwijk(state.wind.direction_deg),
        }

    def _tide_summary_for_day(self, day_states: List[HourState], peak_state: HourState) -> Dict:
        """Eerstvolgende hoog- en laagtij + huidige tij-richting op piek-moment."""
        tide = peak_state.tide
        # next_high/next_low zijn al berekend per HourState; pak de eerste van deze dag.
        next_high = peak_state.tide.next_high
        next_low = peak_state.tide.next_low
        return {
            "phase_at_peak": tide.phase,                       # opgaand/afgaand/onbekend
            "level_m_at_peak": round(tide.level_m, 2),
            "next_high_time": next_high.strftime("%H:%M") if next_high else None,
            "next_low_time": next_low.strftime("%H:%M") if next_low else None,
        }

    # ---------- fallback templates ----------

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
        """Deterministische 4-daagse digest in surfweer-stijl."""
        if not hour_states or not scores:
            return "Nwijk: geen data beschikbaar. Cam: surfweer.nl/webcams/noordwijk/"

        days = self._group_by_day(hour_states, scores)
        now = datetime.now()
        day_label = _DAY_NL_SHORT[now.weekday()]
        _, _, is_spring = moon_phase_info(now)

        labels = ["vandaag", "morgen", "overmorgen", None]
        parts: List[str] = []
        for i, (date_obj, day_states, day_scores) in enumerate(days[:4]):
            if not day_states:
                continue
            label = labels[i] or date_obj.strftime("%a %d/%m")
            peak_idx = max(range(len(day_scores)), key=lambda i: day_scores[i].total_score)
            ps = day_states[peak_idx]
            spectrum = ps.wave_spectrum
            dom = max(spectrum.peaks, key=lambda p: p.height_m) if spectrum.peaks else None
            h = round(spectrum.significant_height_total, 1)
            p = round(dom.period_s if dom else spectrum.mean_period, 1)
            wave_dir = degrees_to_compass(dom.direction_deg if dom else spectrum.mean_direction)
            wind_dir = degrees_to_compass(ps.wind.direction_deg)
            wind_kn = round(ps.wind.speed_kn)
            tide_dir = ps.tide.phase if ps.tide.phase in ('opgaand', 'afgaand') else '–'

            day_windows = [w for w in windows
                           if day_states[0].timestamp <= w.peak_hour <= day_states[-1].timestamp]
            if day_windows:
                w = max(day_windows, key=lambda w: w.peak_score)
                window_str = f" {w.start.strftime('%H:%M')}-{w.end.strftime('%H:%M')}"
            else:
                window_str = " (geen venster)"

            parts.append(
                f"{label}: {h}m {p}s {wave_dir}, {wind_dir}{wind_kn}kn, "
                f"tij {tide_dir}{window_str}"
            )

        body = "; ".join(parts)
        spring_note = " Springtij." if is_spring else ""
        return f"Nwijk {day_label}: {body}.{spring_note} Cam: surfweer.nl/webcams/noordwijk/"
