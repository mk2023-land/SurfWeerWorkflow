"""
Validation script voor backtesting tegen historische SMS dataset.
Vergelijkt algoritme output met verwachte resultaten uit validatieset.
"""
import asyncio
import json
from datetime import datetime
from typing import Dict, List, Tuple
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.sources.open_meteo import OpenMeteoClient
from src.data.models import HourState, ScoreBreakdown
from src.scoring.hourly import score_hour
from src.config import NOORDWIJK


class ValidationRunner:
    """Voert backtest validatie uit."""

    def __init__(self):
        self.openmeteo_client = OpenMeteoClient()
        self.results = []

    async def validate_against_historical_set(self, validation_set: List[Dict]) -> Dict:
        """
        Voer validatie uit tegen historische SMS dataset.

        Args:
            validation_set: Lijst van historische SMS cases

        Returns:
            Dictionary met validatie resultaten
        """
        logger.info(f"Validating against {len(validation_set)} historical cases")

        passed_cases = 0
        failed_cases = 0

        for case in validation_set:
            result = await self._validate_case(case)
            self.results.append(result)

            if result['passed']:
                passed_cases += 1
            else:
                failed_cases += 1

        accuracy = passed_cases / len(validation_set) if validation_set else 0

        summary = {
            'total_cases': len(validation_set),
            'passed_cases': passed_cases,
            'failed_cases': failed_cases,
            'accuracy': accuracy,
            'results': self.results
        }

        self._print_summary(summary)

        return summary

    async def _validate_case(self, case: Dict) -> Dict:
        """
        Valideer één historische case.

        Args:
            case: Historische case data

        Returns:
            Dictionary met validatie resultaat
        """
        date_str = case['date']
        expected_output = case['expected_algorithm_output']
        expected_min_score = expected_output.get('score_range', [0, 100])[0]
        expected_max_score = expected_output.get('score_range', [0, 100])[1]
        expected_alert = expected_output.get('alert', False)

        try:
            # Haal historische data op voor deze datum
            date_obj = datetime.strptime(date_str, "%d-%m-%Y")
            start_date = date_obj.strftime("%Y-%m-%d")
            end_date = (date_obj + timedelta(days=1)).strftime("%Y-%m-%d")

            archive_data = await self.openmeteo_client.fetch_archive_data(
                start_date,
                end_date,
                NOORDWIJK.lat,
                NOORDWIJK.lon
            )

            # Process data
            hour_states = self._process_archive_data(archive_data)

            if not hour_states:
                return {
                    'date': date_str,
                    'passed': False,
                    'error': 'No data available',
                    'expected': expected_output,
                    'actual': None
                }

            # Score alle uren
            hourly_scores = [score_hour(state) for state in hour_states]

            # Vind peak score
            peak_score = max(s.total_score for s in hourly_scores)

            # Bepaal of alert zou moeten zijn
            alert_threshold = 75
            would_alert = peak_score >= alert_threshold

            # Vergelijk met verwacht
            score_match = expected_min_score <= peak_score <= expected_max_score
            alert_match = would_alert == expected_alert

            passed = score_match and alert_match

            return {
                'date': date_str,
                'passed': passed,
                'expected': expected_output,
                'actual': {
                    'peak_score': peak_score,
                    'would_alert': would_alert,
                    'score_match': score_match,
                    'alert_match': alert_match
                }
            }

        except Exception as e:
            return {
                'date': date_str,
                'passed': False,
                'error': str(e),
                'expected': expected_output,
                'actual': None
            }

    def _process_archive_data(self, archive_data: Dict) -> List:
        """Process archief data naar HourStates (zelfde als baseline builder)."""
        weather_data = archive_data.get('weather', [])
        marine_data = archive_data.get('marine', [])

        if not weather_data or not marine_data:
            return []

        hour_states = []

        for i in range(min(len(weather_data), len(marine_data))):
            weather = weather_data[i]
            marine = marine_data[i]

            if abs((weather['timestamp'] - marine['timestamp']).total_seconds()) > 3600:
                continue

            try:
                from src.data.models import WaveSpectrum, WindState, TideState, HourState

                wave_spectrum = WaveSpectrum(
                    timestamp=weather['timestamp'],
                    significant_height_total=marine.get('wave_height', 0.0),
                    mean_period=marine.get('wave_period', 0.0),
                    mean_direction=int(marine.get('wave_direction', 0.0)),
                    peaks=[]
                )

                wind_state = WindState(
                    speed_kn=weather['wind_speed'],
                    direction_deg=int(weather['wind_direction']),
                    gusts_kn=None
                )

                tide_state = TideState(
                    level_m=0.0,
                    phase="onbekend",
                    next_low=datetime.now(),
                    next_high=datetime.now()
                )

                hour_state = HourState(
                    timestamp=weather['timestamp'],
                    location_name=NOORDWIJK.name,
                    wave_spectrum=wave_spectrum,
                    wind=wind_state,
                    tide=tide_state,
                    forecast_source="archive",
                    confidence=1.0
                )

                hour_states.append(hour_state)

            except Exception as e:
                continue

        return hour_states

    def _print_summary(self, summary: Dict):
        """Print samenvatting van validatie resultaten."""
        print("\n" + "="*80)
        print("VALIDATIE SAMENVATTING")
        print("="*80)
        print(f"Totaal cases: {summary['total_cases']}")
        print(f"Geslaagd: {summary['passed_cases']} ({summary['accuracy']*100:.1f}%)")
        print(f"Gefaald: {summary['failed_cases']} ({(1-summary['accuracy'])*100:.1f}%)")

        # Check of accuracy threshold gehaald is
        min_accuracy = 0.70
        if summary['accuracy'] >= min_accuracy:
            print(f"\n✓ VALIDATIE GESLAAGD (≥{min_accuracy*100:.0f}% accuracy)")
        else:
            print(f"\n✗ VALIDATIE GEFAALD (<{min_accuracy*100:.0f}% accuracy)")

        # Print gefaalde cases
        failed_results = [r for r in summary['results'] if not r['passed']]
        if failed_results:
            print("\nGefaalde cases:")
            for result in failed_results:
                print(f"  {result['date']}: {result.get('error', 'Score/alert mismatch')}")
                if result.get('actual'):
                    print(f"    Verwacht: {result['expected']}")
                    print(f"    Actueel: peak={result['actual']['peak_score']}, alert={result['actual']['would_alert']}")

        print("="*80 + "\n")


# Voorbeeld validatieset (gebaseerd op plan document)
VALIDATION_SET = [
    {
        "date": "06-08-2025",
        "tobias_alert_explicit": True,
        "tobias_noordwijk_assessment": "1,4m swell op 100mhz (10s) groundswell door windgolven heen",
        "tobias_alert_type": "T4",
        "expected_algorithm_output": {
            "score_range": [75, 85],
            "alert": True
        }
    },
    {
        "date": "16-05-2026",
        "tobias_alert_explicit": True,
        "tobias_noordwijk_assessment": "Zvoort/Nwijk heel even 11-12u zonder wind",
        "tobias_alert_type": "T3+T5",
        "expected_algorithm_output": {
            "score_range": [70, 80],
            "alert": True
        }
    },
    {
        "date": "09-09-2025",
        "tobias_alert_explicit": False,
        "tobias_noordwijk_assessment": "Nauwelijks wind → geen golfgeneratie",
        "tobias_alert_type": None,
        "expected_algorithm_output": {
            "score_range": [0, 15],
            "alert": False
        }
    },
    {
        "date": "05-08-2025",
        "tobias_alert_explicit": True,
        "tobias_noordwijk_assessment": "1.5m swell uit N op 10sec",
        "tobias_alert_type": "T1+T4+T5",
        "expected_algorithm_output": {
            "score_range": [75, 90],
            "alert": True
        }
    }
]


async def main():
    """Hoofd entry point."""
    import logging
    logging.basicConfig(level=logging.INFO)

    validator = ValidationRunner()
    summary = await validator.validate_against_historical_set(VALIDATION_SET)

    # Exit met passende status code
    if summary['accuracy'] >= 0.70:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    from datetime import timedelta
    asyncio.run(main())