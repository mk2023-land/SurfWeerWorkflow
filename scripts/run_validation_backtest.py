"""
Validation-backtest tegen canonieke historische scenario's.

Scoort een set gedocumenteerde referentie-forecaster-referentiedagen (condities als
deterministische fixtures) met de live scoring-engine en vergelijkt de
piek-score + alert-beslissing met de verwachte uitkomst.

Waarom fixtures i.p.v. de Open-Meteo archive-API (oude opzet): de marine-
archive levert voor deze historische datums geen golfhoogte (wave_height=
None -> Hs=0 -> score 0), waardoor de backtest structureel faalde op
ontbrekende bron-data i.p.v. op echte scoring-regressies. Met vastgelegde
condities is dit een reproduceerbare regressie-guard op de scoring-engine.

De `expected`-ranges zijn gekalibreerd op de huidige engine (incl. de
offshore-grooming uit 2026-06). Schuift een toekomstige scoring-wijziging
een case buiten z'n band, dan is dat bewust te beoordelen (band bijstellen
of regressie fixen) — precies waar deze guard voor is.

Runnable script (geen pytest): `uv run python scripts/run_validation_backtest.py`.
De fixture-gelijkwaardige unit-tests staan in tests/test_scoring.py.
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from src.data.models import (
    HourState,
    SpectralPeak,
    SwellType,
    TideState,
    WaveSpectrum,
    WindState,
)
from src.scoring.hourly import score_hour

logger = logging.getLogger(__name__)

# Vast midden-op-de-dag timestamp (zomertijd 11:00 NL) — voorkomt dat de
# daglicht-filter in score_hour een nul-score geeft.
_TS = datetime(2025, 8, 6, 9, 0, 0)
_ALERT_THRESHOLD = 75


def _build_hour_state(cond: dict) -> HourState:
    """Bouw een HourState uit een fixture-conditie-dict."""
    peaks = [
        SpectralPeak(
            frequency_mhz=p["frequency_mhz"],
            period_s=p["period_s"],
            height_m=p["height_m"],
            direction_deg=p["direction_deg"],
            type=p["type"],
        )
        for p in cond.get("peaks", [])
    ]
    spectrum = WaveSpectrum(
        timestamp=_TS,
        significant_height_total=cond["hs_m"],
        mean_period=cond["mean_period_s"],
        mean_direction=cond["mean_direction_deg"],
        peaks=peaks,
    )
    wind = WindState(
        speed_kn=cond["wind_speed_kn"],
        direction_deg=cond["wind_direction_deg"],
        gusts_kn=cond.get("wind_gusts_kn"),
    )
    tide = TideState(
        level_m=cond["tide_level_m"],
        phase=cond["tide_phase"],
        next_low=_TS,
        next_high=_TS,
    )
    return HourState(
        timestamp=_TS,
        location_name="Noordwijk",
        wave_spectrum=spectrum,
        wind=wind,
        tide=tide,
    )


class ValidationRunner:
    """Voert de fixture-backtest uit."""

    def __init__(self):
        self.results = []

    def validate_against_set(self, validation_set: list[dict]) -> dict:
        logger.info(f"Validating against {len(validation_set)} canonieke cases")
        passed = 0
        for case in validation_set:
            result = self._validate_case(case)
            self.results.append(result)
            if result["passed"]:
                passed += 1

        accuracy = passed / len(validation_set) if validation_set else 0.0
        summary = {
            "total_cases": len(validation_set),
            "passed_cases": passed,
            "failed_cases": len(validation_set) - passed,
            "accuracy": accuracy,
            "results": self.results,
        }
        self._print_summary(summary)
        return summary

    def _validate_case(self, case: dict) -> dict:
        exp = case["expected_algorithm_output"]
        lo, hi = exp["score_range"]
        expect_alert = exp["alert"]
        try:
            state = _build_hour_state(case["conditions"])
            peak = round(score_hour(state).total_score, 1)
            would_alert = peak >= _ALERT_THRESHOLD
            passed = (lo <= peak <= hi) and (would_alert == expect_alert)
            return {
                "date": case["date"],
                "passed": passed,
                "expected": exp,
                "actual": {
                    "peak_score": peak,
                    "would_alert": would_alert,
                    "score_match": lo <= peak <= hi,
                    "alert_match": would_alert == expect_alert,
                },
            }
        except Exception as e:
            return {
                "date": case["date"],
                "passed": False,
                "error": str(e),
                "expected": exp,
                "actual": None,
            }

    def _print_summary(self, summary: dict):
        print("\n" + "=" * 80)
        print("VALIDATIE SAMENVATTING")
        print("=" * 80)
        print(f"Totaal cases: {summary['total_cases']}")
        print(f"Geslaagd: {summary['passed_cases']} ({summary['accuracy']*100:.1f}%)")
        print(f"Gefaald: {summary['failed_cases']} ({(1-summary['accuracy'])*100:.1f}%)")

        min_accuracy = 0.70
        if summary["accuracy"] >= min_accuracy:
            print(f"\nVALIDATIE GESLAAGD (>= {min_accuracy*100:.0f}% accuracy)")
        else:
            print(f"\nVALIDATIE GEFAALD (< {min_accuracy*100:.0f}% accuracy)")

        for r in summary["results"]:
            mark = "OK " if r["passed"] else "XX "
            if r.get("actual"):
                a = r["actual"]
                print(f"  {mark}{r['date']}: peak={a['peak_score']} alert={a['would_alert']} "
                      f"(verwacht {r['expected']['score_range']}, alert={r['expected']['alert']})")
            else:
                print(f"  {mark}{r['date']}: ERROR {r.get('error')}")
        print("=" * 80 + "\n")


# Canonieke validatieset — condities uit de referentie-forecaster referentie-assessments
# (gedocumenteerd), expected-ranges gekalibreerd op de huidige scoring-engine.
# Spiegelt de fixture-cases in tests/test_scoring.py.
VALIDATION_SET = [
    {
        "date": "06-08-2025",
        "ref_noordwijk_assessment": "1,4m groundswell op 10s door windgolven heen (T4)",
        "conditions": {
            "hs_m": 1.4, "mean_period_s": 8.0, "mean_direction_deg": 315,
            "peaks": [
                {"frequency_mhz": 100, "period_s": 10.0, "height_m": 1.2,
                 "direction_deg": 330, "type": SwellType.GROUND_SWELL},
                {"frequency_mhz": 200, "period_s": 5.0, "height_m": 0.4,
                 "direction_deg": 270, "type": SwellType.WIND_SEA},
            ],
            "wind_speed_kn": 4, "wind_direction_deg": 180,
            "tide_level_m": 0.5, "tide_phase": "opgaand",
        },
        "expected_algorithm_output": {"score_range": [88, 100], "alert": True},
    },
    {
        "date": "16-05-2026",
        "ref_noordwijk_assessment": "0,9m groundswell, windstilte-window 11-12u (T3+T5)",
        "conditions": {
            "hs_m": 0.9, "mean_period_s": 9.0, "mean_direction_deg": 340,
            "peaks": [
                {"frequency_mhz": 111, "period_s": 9.0, "height_m": 0.9,
                 "direction_deg": 340, "type": SwellType.GROUND_SWELL},
            ],
            "wind_speed_kn": 2, "wind_direction_deg": 180,
            "tide_level_m": 0.6, "tide_phase": "afgaand",
        },
        "expected_algorithm_output": {"score_range": [62, 75], "alert": False},
    },
    {
        "date": "09-09-2025",
        "ref_noordwijk_assessment": "Nauwelijks wind -> geen golfgeneratie, flat",
        "conditions": {
            "hs_m": 0.3, "mean_period_s": 4.0, "mean_direction_deg": 270,
            "peaks": [],
            "wind_speed_kn": 6, "wind_direction_deg": 90,
            "tide_level_m": 0.2, "tide_phase": "afgaand",
        },
        "expected_algorithm_output": {"score_range": [0, 15], "alert": False},
    },
    {
        "date": "05-08-2025",
        "ref_noordwijk_assessment": "1,5m groundswell uit NNW op 10s (T1+T4+T5)",
        "conditions": {
            "hs_m": 1.5, "mean_period_s": 10.0, "mean_direction_deg": 335,
            "peaks": [
                {"frequency_mhz": 100, "period_s": 10.0, "height_m": 1.5,
                 "direction_deg": 335, "type": SwellType.GROUND_SWELL},
            ],
            "wind_speed_kn": 6, "wind_direction_deg": 120,
            "tide_level_m": 0.5, "tide_phase": "opgaand",
        },
        "expected_algorithm_output": {"score_range": [90, 100], "alert": True},
    },
]


def main():
    logging.basicConfig(level=logging.INFO)
    runner = ValidationRunner()
    summary = runner.validate_against_set(VALIDATION_SET)

    # Schrijf JSON-resultaat (gelezen door de PR-comment-stap in CI).
    out = Path("tests/validation_output.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    sys.exit(0 if summary["accuracy"] >= 0.70 else 1)


if __name__ == "__main__":
    main()
