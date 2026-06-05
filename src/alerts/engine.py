"""
Alert engine module.
Coördineert detectie, besluitvorming, en state management.
"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from src.config import ALERT_CONFIG
from src.data.models import AlertCandidate, AlertType, Decision, HourState, SurfWindow, SystemState
from src.util import to_utc

from .detectors import AlertDetectorEngine

_NL = ZoneInfo('Europe/Amsterdam')

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


def select_primary_alert_type(triggered: set[AlertType]) -> Optional[AlertType]:
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

    def __init__(
        self,
        state_file: str = "data/state.json",
        budget_file: str = "data/monthly_budget.json",
    ):
        self.detector_engine = AlertDetectorEngine()
        self.state_file = state_file
        self.budget_file = budget_file
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
            with open(state_path) as f:
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
        forecast: list[HourState],
        history: list[HourState],
        buoy_history: dict[str, list] = None,
        windows: list[SurfWindow] = None,
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

        # Voer detectors uit. We trekken zowel de Set (voor priority-selectie)
        # als de Dict[AlertType, AlertCandidate] (voor rijke explanations) op
        # — zie AlertDetectorEngine.detect_all_with_candidates.
        triggered_alerts, candidates_by_type = (
            self.detector_engine.detect_all_with_candidates(
                forecast, history, buoy_history, windows
            )
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
                explanation=self._generate_explanation(
                    best_window, triggered_alerts, candidates_by_type
                ),
                confidence=best_window.stability
            )

            logger.info(f"Sending alert: {best_window.peak_score} peak score, {best_window.duration_hours:.1f}h duration")

            # Fix #3: NIET hier state.record_alert + save callen. Voorheen werd
            # state direct na de Decision bijgewerkt; bij een notifier-5xx,
            # validator-fail of crash in main.py kreeg de gebruiker een
            # ghost-cooldown (4u) en ++ weekly counter zónder dat er een alert
            # daadwerkelijk verzonden was. Bij een 5xx-spike kon dat de hele
            # week aan alert-budget kosten zonder één verzonden alert.
            # Caller (main.py:_handle_alert) doet record_alert PAS NA bevestigd
            # success van notifier.send_alert().
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

    def _generate_explanation(
        self,
        window: SurfWindow,
        alert_types: set[AlertType],
        candidates_by_type: Optional[dict[AlertType, AlertCandidate]] = None,
    ) -> str:
        """Genereer uitleg voor alert.

        Als `candidates_by_type` is meegegeven, gebruik dan de rijke
        per-detector `AlertCandidate.explanation` strings — die bevatten
        concrete getallen (periode, hoogte, windrichting/snelheid, duur)
        waar de generieke labels alleen het categorie-name geven. We
        deduppen geordend zodat de volgorde stabiel is met
        PRIMARY_ALERT_PRIORITY zoals elders in deze module.

        Voor types waarvoor géén candidate bekend is (bijv. SWELL_ARRIVAL
        die alleen via de persisted-history-route triggerde) valt het
        per-type stukje terug op het generieke label hieronder, zodat
        de uitleg nooit een type stilletjes weglaat.
        """
        type_names = {
            AlertType.SWELL_ARRIVAL: "Nieuwe swell aankomst",
            AlertType.WIND_SHIFT: "Wind draait aflandig",
            AlertType.WIND_DIP: "Windstilte window",
            AlertType.SUSTAINED_GROUNDSWELL: "Aanhoudende groundswell",
            AlertType.TIDE_GATED: "Gunstige tij combinatie"
        }

        type_explanations: list[str] = []

        if candidates_by_type:
            # Geordend per PRIMARY_ALERT_PRIORITY zodat de volgorde
            # deterministisch is. Onbekende (out-of-priority) types
            # daarna in iteratie-volgorde van de set.
            ordered_types = [t for t in PRIMARY_ALERT_PRIORITY if t in alert_types]
            ordered_types += [t for t in alert_types if t not in PRIMARY_ALERT_PRIORITY]
            for t in ordered_types:
                cand = candidates_by_type.get(t)
                if cand and cand.explanation:
                    type_explanations.append(cand.explanation)
                elif t in type_names:
                    type_explanations.append(type_names[t])
        else:
            type_explanations = [type_names[t] for t in alert_types if t in type_names]

        if type_explanations:
            explanation = "; ".join(type_explanations) if candidates_by_type else ", ".join(type_explanations)
        else:
            explanation = "Goede surfcondities"

        explanation += f" ({window.peak_score} score, {window.duration_hours:.1f}h duration)"

        return explanation

    # ---- Monthly budget: O(1) cache ipv O(n) jsonl-scan -------------------
    #
    # Voorheen las `_check_monthly_budget` per run het volledige
    # `data/forecasts_log.jsonl` om notify- en LLM-calls van deze maand op te
    # tellen. Dat is O(n) over alle historische runs en blijft groeien zolang
    # het bestand niet roteert — bij 4 runs/dag tikt dat op aan ~120 regels per
    # maand × jaren history. Bovendien werd de timestamp naive geparset
    # (Europe/Amsterdam aanname zonder tz-info, vergeleken met `datetime.now()`
    # ook naive) → in de praktijk werkbaar maar inconsistent met de rest van
    # de pipeline die overal `src.util.to_utc` gebruikt.
    #
    # Nu houden we de tellers bij in `data/monthly_budget.json` (zelfde
    # atomic-write patroon als state.json). Bij maand-rollover wordt het
    # bestand reset. Bij ontbrekend bestand vallen we exact één keer terug op
    # de legacy log-scan (first-run / cache-loss recovery) zodat we geen
    # historische teller verliezen. Caller (main.py) roept `record_send()`
    # aan na bevestigde send-success — analoog aan de Fix #3 record_alert-
    # ordering — zodat een mislukte notifier-5xx geen budget verbruikt.

    def _current_month_str(self) -> str:
        """YYYY-MM voor de huidige maand (UTC). Eén canonieke tijd-zone houdt
        rollover deterministisch ongeacht runner-tz."""
        return datetime.now(timezone.utc).strftime('%Y-%m')

    def _load_monthly_budget(self) -> dict:
        """Lees `data/monthly_budget.json` of return een verse maand-struct.

        Bij maand-rollover (stored month != huidige maand): reset counters.
        Bij ontbrekend bestand: fallback naar legacy jsonl-scan (one-shot
        zodat we niet stilletjes een lopende maand op 0 zetten na een cache-
        wipe). Bij corrupte JSON: log + reset (het is een teller, geen
        veiligheids-kritische state zoals state.json).
        """
        current_month = self._current_month_str()
        budget_path = Path(self.budget_file)

        if not budget_path.exists():
            # Legacy fallback — telt deze maand op uit forecasts_log.jsonl.
            # Gebeurt maximaal één keer per cache-cycle; daarna wordt de
            # waarde gepersisteerd door _update_monthly_budget na de
            # eerstvolgende send.
            return self._scan_legacy_log_for_month(current_month)

        try:
            with open(budget_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                f"Corrupt monthly_budget at {budget_path}: {e}. Resetting counters."
            )
            return {
                'month': current_month,
                'notify_count': 0,
                'llm_count': 0,
                'last_updated': datetime.now(timezone.utc).isoformat(),
            }

        if data.get('month') != current_month:
            logger.info(
                f"Monthly budget rollover {data.get('month')} → {current_month}"
            )
            return {
                'month': current_month,
                'notify_count': 0,
                'llm_count': 0,
                'last_updated': datetime.now(timezone.utc).isoformat(),
            }

        # Coerce velden voor robustheid tegen handmatige edits.
        return {
            'month': current_month,
            'notify_count': int(data.get('notify_count', 0)),
            'llm_count': int(data.get('llm_count', 0)),
            'last_updated': data.get('last_updated') or datetime.now(timezone.utc).isoformat(),
        }

    def _scan_legacy_log_for_month(self, current_month: str) -> dict:
        """Tel notify/LLM-calls van de huidige maand uit forecasts_log.jsonl.

        Eenmalige fallback voor first-run of cache-loss. Naive timestamps
        worden geïnterpreteerd als Europe/Amsterdam (consistent met de rest
        van de pipeline via `src.util.to_utc`) en in UTC vergeleken.
        """
        log_file = Path('data/forecasts_log.jsonl')
        notify_count = 0
        llm_count = 0
        if log_file.exists():
            try:
                # Maand-grens in UTC: eerste van de maand 00:00 UTC. We
                # vergelijken UTC met UTC zodat tz-aware en geconverteerde
                # naive timestamps consistent gebufferd worden.
                month_start = datetime.strptime(current_month, '%Y-%m').replace(tzinfo=timezone.utc)
                with open(log_file) as f:
                    for line in f:
                        try:
                            log_entry = json.loads(line)
                            raw_ts = log_entry.get('timestamp')
                            if not raw_ts:
                                continue
                            ts = datetime.fromisoformat(raw_ts)
                            ts_utc = to_utc(ts)
                            if ts_utc >= month_start:
                                if log_entry.get('sms_sent'):
                                    notify_count += 1
                                if log_entry.get('llm_used'):
                                    llm_count += 1
                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue
            except OSError as e:
                logger.warning(f"Legacy budget scan failed: {e}")

        return {
            'month': current_month,
            'notify_count': notify_count,
            'llm_count': llm_count,
            'last_updated': datetime.now(timezone.utc).isoformat(),
        }

    def _save_monthly_budget(self, budget: dict) -> None:
        """Atomic write — zelfde tmp+rename patroon als `_save_state` zodat een
        mid-write crash de bestaande teller niet corrumpeert."""
        budget_path = Path(self.budget_file)
        budget_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = budget_path.with_name(
            f"{budget_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        )
        try:
            with open(tmp_path, 'w') as f:
                json.dump(budget, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, budget_path)
        except Exception:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise

    def _update_monthly_budget(self, notify_increment: int, llm_increment: int) -> dict:
        """Increment de monthly-budget tellers en persist atomisch.

        Returns de geüpdatete struct (handig voor tests / logging).
        """
        budget = self._load_monthly_budget()
        budget['notify_count'] = int(budget.get('notify_count', 0)) + int(notify_increment)
        budget['llm_count'] = int(budget.get('llm_count', 0)) + int(llm_increment)
        budget['last_updated'] = datetime.now(timezone.utc).isoformat()
        self._save_monthly_budget(budget)
        return budget

    def record_send(self, notify: bool, llm: bool) -> None:
        """Hook voor main.py — call na een bevestigd-successfulle send.

        `notify=True`  → +1 op `notify_count` (1 SMS/mail/push verzonden)
        `llm=True`     → +1 op `llm_count` (1 LLM-call verbruikt voor deze run)

        Caller-discipline: roep dit ALLEEN aan na `result.get('success')`
        — analoog aan de Fix #3 ordering voor `state.record_alert`. Een
        mislukte notifier mag het budget niet aantasten.
        """
        notify_inc = 1 if notify else 0
        llm_inc = 1 if llm else 0
        if notify_inc == 0 and llm_inc == 0:
            return
        try:
            self._update_monthly_budget(notify_inc, llm_inc)
        except Exception as e:
            # Budget-bookkeeping mag de send-pipeline nooit laten falen.
            logger.error(f"record_send: monthly budget update faalde: {e}")

    def _check_monthly_budget(self) -> bool:
        """
        Controleer maandelijks budget. Kosten per notificatie hangen af van het
        kanaal: ntfy.sh en SMTP-mail zijn €0, alleen Twilio-SMS kost ~€0.08.

        O(1) — leest enkel `data/monthly_budget.json`. Bij missing file valt
        `_load_monthly_budget` éénmalig terug op een log-scan zodat een cache-
        wipe halverwege de maand geen budget-reset triggert.
        """
        try:
            channel = (os.getenv('NOTIFIER') or 'ntfy').lower()
            cost_per_send = 0.08 if channel == 'twilio' else 0.0

            budget = self._load_monthly_budget()
            notify_count = int(budget.get('notify_count', 0))
            llm_count = int(budget.get('llm_count', 0))

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
        # Test-knop: FORCE_DIGEST=true (handmatige workflow_dispatch) forceert
        # digest-generatie, ook buiten het ochtend-venster en ook als er vandaag
        # al een digest verstuurd is. Bedoeld om Claude end-to-end te testen
        # zonder op de ochtend-cron te wachten.
        if os.getenv('FORCE_DIGEST', '').lower() in ('true', '1', 'yes'):
            logger.info("FORCE_DIGEST actief — digest geforceerd")
            return True

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
