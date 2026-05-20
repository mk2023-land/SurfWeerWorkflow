"""
Alert engine module.
Coördineert detectie, besluitvorming, en state management.
"""
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Set
from zoneinfo import ZoneInfo
import json
from pathlib import Path

_NL = ZoneInfo('Europe/Amsterdam')

from src.data.models import (
    HourState,
    AlertCandidate,
    AlertType,
    Decision,
    SystemState,
    SurfWindow,
    ScoreBreakdown
)

from src.config import ALERT_CONFIG

from .detectors import AlertDetectorEngine

logger = logging.getLogger(__name__)


# B9 — Primary alert-type priority. Bij meerdere triggered alerts wordt
# de hoogst gerankte gekozen als primary (titel + alert_type). Voorheen
# pakte `set.pop()` willekeurig waardoor identieke condities verschillende
# alerts gaven op verschillende dagen.
#
# Rationale: T1 (nieuwe swell) is zeldzaam en meest informatief; T4
# (sustained groundswell) is een kwaliteits-event; T2 (front-passage)
# is een ingrijpende verandering; T5 (tide window) is precise maar
# minder uniek; T3 (windstilte) is kortdurend en het minst urgent.
PRIMARY_ALERT_PRIORITY = [
    AlertType.SWELL_ARRIVAL,
    AlertType.SUSTAINED_GROUNDSWELL,
    AlertType.WIND_SHIFT,
    AlertType.TIDE_GATED,
    AlertType.WIND_DIP,
]


def select_primary_alert_type(triggered: Set[AlertType]) -> Optional[AlertType]:
    """
    Kies deterministisch de primary alert-type uit een set triggered types.

    Returns None bij lege set, anders het hoogst geprioriteerde type
    (PRIMARY_ALERT_PRIORITY volgorde). Onbekende types (die niet in de
    priority-lijst staan) zijn vangnet — alleen geselecteerd als geen
    bekende type voorkomt.
    """
    if not triggered:
        return None
    for t in PRIMARY_ALERT_PRIORITY:
        if t in triggered:
            return t
    return next(iter(triggered))


class AlertEngine:
    """
    Hoofd alert engine die detectie coördineert en beslissingen neemt.
    """

    def __init__(self, state_file: str = "data/state.json"):
        self.detector_engine = AlertDetectorEngine()
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> SystemState:
        """Laad state uit bestand.

        B5: normaliseert alle datetimes naar tz-aware UTC zodat cooldown-
        en digest-checks geen naive/aware TypeError kunnen gooien tussen
        verschillende state.json generaties.

        B10: bij een corrupt state.json (JSONDecodeError) wordt NIET stil
        teruggevallen op een lege SystemState — dat zou de weekly cap
        bypassen en de gebruiker een burst aan alerts kunnen geven na een
        cache-corruptie. In plaats daarvan loggen we ERROR en raisen we,
        zodat de GH Actions run faalt en de operator een CI-failure mail
        krijgt (recoverable). De fallback-op-lege-SystemState blijft alleen
        gelden voor de echte first-run (state.json bestaat niet).
        """
        def _aware_utc(s: Optional[str]) -> Optional[datetime]:
            if not s:
                return None
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        state_path = Path(self.state_file)
        if not state_path.exists():
            # First run: geen state.json → lege SystemState is correct.
            return SystemState()

        try:
            with open(state_path, 'r') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            # B10: corrupt file — raise zodat de run faalt ipv stilletjes
            # de weekly cap te resetten. Operator krijgt CI-failure mail
            # en kan handmatig de state cache reset doen.
            logger.error(
                f"Corrupt state file at {state_path}: {e}. "
                f"Refusing to start with empty state to avoid bypassing weekly cap. "
                f"Delete the file (or the GH Actions cache entry) to force a clean first-run."
            )
            raise

        return SystemState(
            last_alert_time=_aware_utc(data.get('last_alert_time')),
            alerts_sent_this_week=data.get('alerts_sent_this_week', 0),
            week_number=data.get('week_number', datetime.now(timezone.utc).isocalendar()[1]),
            last_digest_time=_aware_utc(data.get('last_digest_time')),
            cooldown_until=_aware_utc(data.get('cooldown_until')),
        )

    def _save_state(self):
        """Sla state atomisch op naar bestand.

        B10: voorheen schreef `json.dump` direct naar `state.json`. Als de
        runner mid-write gekilled werd (zeldzaam maar gedocumenteerd voor
        GH Actions) bleef een truncated/corrupt file achter. De daaropvolgende
        run viel terug op een lege SystemState → weekly cap bypassed.

        Fix: schrijf naar `state.json.tmp.<pid>.<uuid>` in dezelfde directory
        (zelfde filesystem = `os.replace` is POSIX-atomair) en rename pas
        ná een succesvolle write+flush+fsync. PID+UUID in de tmp-naam
        voorkomt collisions als er ooit parallelle runs zouden zijn.
        """
        state_path = Path(self.state_file)
        state_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            'last_alert_time': self.state.last_alert_time.isoformat() if self.state.last_alert_time else None,
            'alerts_sent_this_week': self.state.alerts_sent_this_week,
            'week_number': self.state.week_number,
            'last_digest_time': self.state.last_digest_time.isoformat() if self.state.last_digest_time else None,
            'cooldown_until': self.state.cooldown_until.isoformat() if self.state.cooldown_until else None
        }

        tmp_path = state_path.with_name(
            f"{state_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        )
        try:
            with open(tmp_path, 'w') as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, state_path)
        except Exception:
            # Best-effort cleanup: laat geen tmp-files achter bij een crash.
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise

    def evaluate_forecast(
        self,
        forecast: List[HourState],
        history: List[HourState],
        buoy_history: Dict[str, List] = None,
        windows: List[SurfWindow] = None,
        is_digest_time: bool = False
    ) -> Decision:
        """
        Evalueer forecast en neem beslissing.

        Args:
            forecast: Forecast HourStates (komende 48 uur)
            history: Historische HourStates (laatste 12 uren)
            buoy_history: Boei data history
            windows: SurfWindow objecten
            is_digest_time: Of dit het vast tijdstip is voor daily digest

        Returns:
            Decision object
        """
        # Kostenbeperking check
        if not self._check_monthly_budget():
            logger.warning("Monthly budget reached, skipping alerts")
            return Decision(
                send_digest=is_digest_time,
                send_alerts=[],
                skip_reason="Maandelijks budget bereikt (notificatie + Anthropic gecombineerde cap)"
            )

        # Voer detectors uit
        triggered_alerts = self.detector_engine.detect_all(
            forecast, history, buoy_history, windows
        )

        # Filter alert-worthy windows
        alertworthy_windows = []
        if windows:
            alertworthy_windows = [w for w in windows if w.is_alertworthy]

        # Check of alerts enabled zijn
        if not ALERT_CONFIG['alerts_enabled']:
            logger.info("Alerts disabled, only sending digest if scheduled")
            return Decision(
                send_digest=is_digest_time,
                send_alerts=[],
                skip_reason="Alerts disabled in configuration"
            )

        # Check cooldown (B5: tz-aware UTC overal om naive/aware mix te vermijden)
        cooldown = self.state._ensure_utc(self.state.cooldown_until)
        if cooldown and datetime.now(timezone.utc) < cooldown:
            logger.info(f"In cooldown until {cooldown}")
            return Decision(
                send_digest=is_digest_time,
                send_alerts=[],
                skip_reason=f"In cooldown until {cooldown}"
            )

        # Check weekly cap
        if not self.state.should_send_alert(
            ALERT_CONFIG['cooldown_hours_between_alerts'],
            ALERT_CONFIG['max_alerts_per_week']
        ):
            logger.info(f"Weekly cap reached: {self.state.alerts_sent_this_week}/{ALERT_CONFIG['max_alerts_per_week']}")
            return Decision(
                send_digest=is_digest_time,
                send_alerts=[],
                skip_reason=f"Weekly cap reached ({self.state.alerts_sent_this_week}/{ALERT_CONFIG['max_alerts_per_week']})"
            )

        # Kies beste alert-waardige window
        if alertworthy_windows and triggered_alerts:
            # Sorteer op score en kies beste
            best_window = max(alertworthy_windows, key=lambda w: w.peak_score)

            # B9 fix: deterministische primary alert-type selectie via
            # priority-ordering ipv set.pop() (= arbitrair). Volledige set
            # gaat naar _generate_explanation zodat titel en body niet meer
            # uit elkaar kunnen lopen.
            primary_type = select_primary_alert_type(triggered_alerts)

            alert_candidate = AlertCandidate(
                alert_type=primary_type,
                window=best_window,
                detection_time=datetime.now(),
                explanation=self._generate_explanation(best_window, triggered_alerts),
                confidence=best_window.stability
            )

            logger.info(f"Sending alert: {best_window.peak_score} peak score, {best_window.duration_hours:.1f}h duration")

            # Update state
            self.state.record_alert(ALERT_CONFIG['cooldown_hours_between_alerts'])
            self._save_state()

            return Decision(
                send_digest=False,
                send_alerts=[alert_candidate]
            )

        # Geen alert, maar wel digest?
        return Decision(
            send_digest=is_digest_time,
            send_alerts=[],
            skip_reason="No alert-worthy conditions found"
        )

    def _generate_explanation(self, window: SurfWindow, alert_types: Set[AlertType]) -> str:
        """Genereer uitleg voor alert."""
        type_names = {
            AlertType.SWELL_ARRIVAL: "Nieuwe swell aankomst",
            AlertType.WIND_SHIFT: "Wind draait aflandig",
            AlertType.WIND_DIP: "Windstilte window",
            AlertType.SUSTAINED_GROUNDSWELL: "Aanhoudende groundswell",
            AlertType.TIDE_GATED: "Gunstige tij combinatie"
        }

        type_explanations = [type_names[t] for t in alert_types if t in type_names]

        if type_explanations:
            explanation = ", ".join(type_explanations)
        else:
            explanation = "Goede surfcondities"

        explanation += f" ({window.peak_score} score, {window.duration_hours:.1f}h duration)"

        return explanation

    def _check_monthly_budget(self) -> bool:
        """
        Controleer maandelijks budget. Kosten per notificatie hangen af van het
        kanaal: ntfy.sh en SMTP-mail zijn €0, alleen Twilio-SMS kost ~€0.08.
        """
        try:
            log_file = Path('data/forecasts_log.jsonl')
            if not log_file.exists():
                return True

            import os
            channel = (os.getenv('NOTIFIER') or 'ntfy').lower()
            cost_per_send = 0.08 if channel == 'twilio' else 0.0

            notify_count = 0
            llm_count = 0
            current_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            with open(log_file, 'r') as f:
                for line in f:
                    try:
                        log_entry = json.loads(line)
                        timestamp = datetime.fromisoformat(log_entry['timestamp'])
                        if timestamp >= current_month:
                            if log_entry.get('sms_sent'):
                                notify_count += 1
                            if log_entry.get('llm_used'):
                                llm_count += 1
                    except (json.JSONDecodeError, KeyError):
                        continue

            notify_cost = notify_count * cost_per_send
            llm_cost = llm_count * 0.001  # ~€0.001 per Claude Haiku call

            max_notify_cost = ALERT_CONFIG.get('max_sms_cost_per_month_eur', 5.0)
            max_llm_cost = ALERT_CONFIG.get('max_anthropic_cost_per_month_eur', 3.0)

            if cost_per_send > 0 and notify_cost >= max_notify_cost:
                logger.warning(f"Notify budget reached: €{notify_cost:.2f}/€{max_notify_cost}")
                return False
            if llm_cost >= max_llm_cost:
                logger.warning(f"LLM budget reached: €{llm_cost:.2f}/€{max_llm_cost}")
                return False

            logger.info(
                f"Budget status ({channel}): notify €{notify_cost:.2f}/€{max_notify_cost}, "
                f"LLM €{llm_cost:.2f}/€{max_llm_cost}"
            )
            return True

        except Exception as e:
            logger.error(f"Error checking budget: {e}")
            return True  # Bij error door laten gaan

    def is_morning_first_run(self) -> bool:
        """
        True als dit de eerste run van vandaag is in het ochtend-venster (05-13 NL tijd).

        Gebruikt Europe/Amsterdam expliciet — GitHub Actions runners zijn UTC, dus
        een naive `datetime.now().hour` geeft daar UTC-uren en mist het venster.

        Het venster is bewust ruim: GitHub Actions cron heeft géén SLA en kan
        regelmatig 30+ minuten, soms enkele uren delay hebben. De morning cron
        is geconfigureerd voor 05:15 UTC = 07:15 NL CEST, maar in praktijk kan
        die pas om 10:39 NL of later vuren. Het 5-13 venster vangt vrijwel alle
        ochtend-jitter op. De `last_digest_time`-check garandeert dat we maar
        één keer per dag een ochtend-digest sturen, ongeacht hoeveel runs er
        in dit venster vallen.
        """
        now_nl = datetime.now(_NL)
        if not (5 <= now_nl.hour <= 13):
            return False

        if self.state.last_digest_time:
            last = self.state.last_digest_time
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last.astimezone(_NL).date() == now_nl.date():
                return False  # vandaag al verstuurd

        return True

    def record_digest_sent(self):
        """Registreer dat een digest is verstuurd (tz-aware, UTC)."""
        self.state.last_digest_time = datetime.now(timezone.utc)
        self._save_state()