"""
Alert engine module.
Coördineert detectie, besluitvorming, en state management.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
import json
from pathlib import Path

from src.data.models import (
    HourState,
    AlertCandidate,
    AlertType,
    Decision,
    SystemState,
    SurfWindow,
    ScoreBreakdown
)

from src.config import (
    ALERT_CONFIG,
    DEBUG
)

from .detectors import AlertDetectorEngine

logger = logging.getLogger(__name__)


class AlertEngine:
    """
    Hoofd alert engine die detectie coördineert en beslissingen neemt.
    """

    def __init__(self, state_file: str = "data/state.json"):
        self.detector_engine = AlertDetectorEngine()
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> SystemState:
        """Laad state uit bestand."""
        try:
            state_path = Path(self.state_file)
            if state_path.exists():
                with open(state_path, 'r') as f:
                    data = json.load(f)
                    return SystemState(
                        last_alert_time=datetime.fromisoformat(data['last_alert_time']) if data.get('last_alert_time') else None,
                        alerts_sent_this_week=data.get('alerts_sent_this_week', 0),
                        week_number=data.get('week_number', datetime.now().isocalendar()[1]),
                        last_digest_time=datetime.fromisoformat(data['last_digest_time']) if data.get('last_digest_time') else None,
                        cooldown_until=datetime.fromisoformat(data['cooldown_until']) if data.get('cooldown_until') else None
                    )
        except Exception as e:
            logger.warning(f"Failed to load state: {e}, creating new state")

        return SystemState()

    def _save_state(self):
        """Sla state op naar bestand."""
        state_path = Path(self.state_file)
        state_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            'last_alert_time': self.state.last_alert_time.isoformat() if self.state.last_alert_time else None,
            'alerts_sent_this_week': self.state.alerts_sent_this_week,
            'week_number': self.state.week_number,
            'last_digest_time': self.state.last_digest_time.isoformat() if self.state.last_digest_time else None,
            'cooldown_until': self.state.cooldown_until.isoformat() if self.state.cooldown_until else None
        }

        with open(state_path, 'w') as f:
            json.dump(data, f, indent=2)

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

        # Check cooldown
        if self.state.cooldown_until and datetime.now() < self.state.cooldown_until:
            logger.info(f"In cooldown until {self.state.cooldown_until}")
            return Decision(
                send_digest=is_digest_time,
                send_alerts=[],
                skip_reason=f"In cooldown until {self.state.cooldown_until}"
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

            # Maak alert candidate
            alert_candidate = AlertCandidate(
                alert_type=triggered_alerts.pop(),  # Gebruik één type als primary
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

    def is_morning_first_run(self) -> bool:
        """
        Controleer of dit de eerste run van de ochtend is (07:00 NL tijd).

        Returns:
            True als dit de daily digest time is
        """
        now = datetime.now()

        # Check of dit de eerste run van vandaag is
        if self.state.last_digest_time:
            last_digest_date = self.state.last_digest_time.date()
            if last_digest_date == now.date():
                return False  # Alleen digest gestuurd vandaag

        # Check tijd (07:00-08:00 NL tijd)
        hour = now.hour
        return hour == 7  # 07:00-08:00

    def record_digest_sent(self):
        """Registreer dat een digest is verstuurd."""
        self.state.last_digest_time = datetime.now()
        self._save_state()