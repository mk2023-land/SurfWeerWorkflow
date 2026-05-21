"""
Seizoensbaseline builder module.
Bouwt baseline van historische surfcondities voor rarity percentiles.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from src.config import NOORDWIJK
from src.data.sources.open_meteo import _get_openmeteo_client
from src.scoring.hourly import score_hour

logger = logging.getLogger(__name__)


class SeasonalBaselineBuilder:
    """Bouwt seizoensbaseline van historische data."""

    def __init__(self, years_back: int = 5):
        self.years_back = years_back
        self.openmeteo_client = _get_openmeteo_client()

    async def build_baseline(self) -> dict[str, dict]:
        """
        Bouw baseline voor alle weken van het jaar.

        Returns:
            Dictionary met week_number → {p50, p70, p90} percentiles
        """
        logger.info(f"Building seasonal baseline from {self.years_back} years of historical data")

        # Haal historische data op
        # Open-Meteo archive heeft latente data — end_date 5 dagen in het verleden
        # voorkomt rows met None-velden voor uren die nog niet zijn ge-archiveerd.
        start_date = (datetime.now() - timedelta(days=self.years_back * 365)).strftime("%Y-%m-%d")
        end_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

        logger.info(f"Fetching archive data from {start_date} to {end_date}")

        # Fetch data in batches (API limits)
        all_scores = []

        # Verdeel in maandelijkse batches
        current_date = datetime.strptime(start_date, "%Y-%m-%d")
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")

        while current_date < end_date_obj:
            batch_end = min(current_date + timedelta(days=30), end_date_obj)

            batch_start_str = current_date.strftime("%Y-%m-%d")
            batch_end_str = batch_end.strftime("%Y-%m-%d")

            logger.info(f"Fetching batch: {batch_start_str} to {batch_end_str}")

            try:
                archive_data = await self.openmeteo_client.fetch_archive_data(
                    batch_start_str,
                    batch_end_str,
                    NOORDWIJK.lat,
                    NOORDWIJK.lon
                )

                # Process data
                batch_scores = self._process_archive_data(archive_data)
                all_scores.extend(batch_scores)

                logger.info(f"Processed {len(batch_scores)} hour scores from this batch")

            except Exception as e:
                logger.error(f"Error processing batch {batch_start_str}: {e}")

            current_date = batch_end + timedelta(days=1)

            # Rate limiting
            await asyncio.sleep(1)

        logger.info(f"Total hour scores collected: {len(all_scores)}")

        # Bereken percentiles per week
        baseline = self._calculate_weekly_percentiles(all_scores)

        # Save baseline
        self._save_baseline(baseline)

        logger.info(f"Baseline built successfully: {len(baseline)} weeks")

        return baseline

    def _process_archive_data(self, archive_data: dict) -> list[tuple]:
        """
        Process archief data naar lijst van (week_number, score) tuples.

        Args:
            archive_data: Archive data van Open-Meteo

        Returns:
            Lijst van (week_number, score) tuples
        """
        weather_data = archive_data.get('weather', [])
        marine_data = archive_data.get('marine', [])

        if not weather_data or not marine_data:
            return []

        scores = []

        # Merge weather en marine data
        for i in range(min(len(weather_data), len(marine_data))):
            weather = weather_data[i]
            marine = marine_data[i]

            # Skip als timestamps niet matchen
            if abs((weather['timestamp'] - marine['timestamp']).total_seconds()) > 3600:
                continue

            try:
                # Maak HourState
                from src.data.models import (
                    HourState,
                    TideState,
                    WaveSpectrum,
                    WindState,
                )

                # Skip rows met essentiële None-velden (archive heeft soms gaten).
                if (weather.get('wind_speed') is None
                        or weather.get('wind_direction') is None
                        or marine.get('wave_height') is None
                        or marine.get('wave_period') is None
                        or marine.get('wave_direction') is None):
                    continue

                # Wave spectrum (simplificeerd)
                wave_spectrum = WaveSpectrum(
                    timestamp=weather['timestamp'],
                    significant_height_total=float(marine['wave_height']),
                    mean_period=float(marine['wave_period']),
                    mean_direction=int(marine['wave_direction']),
                    peaks=[]
                )

                # Wind state
                wind_state = WindState(
                    speed_kn=float(weather['wind_speed']),
                    direction_deg=int(weather['wind_direction']),
                    gusts_kn=None
                )

                # Tide state (placeholder)
                tide_state = TideState(
                    level_m=0.0,
                    phase="onbekend",
                    next_low=datetime.now() + timedelta(hours=6),
                    next_high=datetime.now() + timedelta(hours=12)
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

                # Score uur
                score = score_hour(hour_state)

                # Bepaal week nummer
                week_number = weather['timestamp'].isocalendar()[1]

                scores.append((week_number, score.total_score))

            except Exception as e:
                logger.warning(f"Error processing hour {weather['timestamp']}: {e}")
                continue

        return scores

    def _calculate_weekly_percentiles(self, scores: list[tuple]) -> dict[str, dict]:
        """
        Bereken percentiles per week van het jaar.

        Args:
            scores: Lijst van (week_number, score) tuples

        Returns:
            Dictionary met week_number → {p50, p70, p90}
        """
        # Groepeer per week
        weekly_scores = {}

        for week_number, score in scores:
            week_key = f"week_{week_number}"
            if week_key not in weekly_scores:
                weekly_scores[week_key] = []
            weekly_scores[week_key].append(score)

        # Bereken percentiles per week
        baseline = {}

        for week_key, week_scores in weekly_scores.items():
            if not week_scores:
                continue

            # Sorteer scores
            sorted_scores = sorted(week_scores)

            # Bereken percentiles
            n = len(sorted_scores)

            # P50 (mediaan)
            p50_idx = int(n * 0.5)
            p50 = sorted_scores[min(p50_idx, n - 1)]

            # P70
            p70_idx = int(n * 0.7)
            p70 = sorted_scores[min(p70_idx, n - 1)]

            # P90
            p90_idx = int(n * 0.9)
            p90 = sorted_scores[min(p90_idx, n - 1)]

            baseline[week_key] = {
                'p50': round(p50, 1),
                'p70': round(p70, 1),
                'p90': round(p90, 1),
                'sample_size': n
            }

        return baseline

    def _save_baseline(self, baseline: dict):
        """Sla baseline op naar JSON bestand.

        Veiligheidscheck: een lege baseline (rebuild faalde maar workflow
        exit=0) zou de bestaande werkende baseline overschrijven en alerts
        onmogelijk maken. We weigeren in dat geval te schrijven.
        """
        baseline_file = Path('data/seasonal_baseline.json')
        baseline_file.parent.mkdir(parents=True, exist_ok=True)

        if not baseline:
            logger.error(
                "Refuse to write empty baseline — would overwrite working file. "
                "Check archive fetch errors above. Existing file (if any) preserved."
            )
            return

        with open(baseline_file, 'w') as f:
            json.dump(baseline, f, indent=2)

        logger.info(f"Baseline saved to {baseline_file}")

    def load_baseline(self) -> dict:
        """Laad baseline van bestand."""
        baseline_file = Path('data/seasonal_baseline.json')

        if not baseline_file.exists():
            logger.warning("No baseline file found")
            return {}

        with open(baseline_file) as f:
            return json.load(f)


async def main():
    """Hoofd entry point voor baseline builder."""
    builder = SeasonalBaselineBuilder(years_back=5)
    baseline = await builder.build_baseline()

    # Print summary
    print("\nBaseline Summary:")
    print(f"Weeks covered: {len(baseline)}")
    if baseline:
        avg = sum(b['sample_size'] for b in baseline.values()) / len(baseline)
        print(f"Average sample size: {avg:.0f}")
    else:
        print("Average sample size: N/A (geen weken — check archive errors hierboven)")

    # Print some examples
    print("\nExample weeks:")
    for week_key in ['week_1', 'week_13', 'week_26', 'week_39', 'week_52']:
        if week_key in baseline:
            data = baseline[week_key]
            print(f"  {week_key}: P50={data['p50']}, P70={data['p70']}, P90={data['p90']} (n={data['sample_size']})")


if __name__ == "__main__":
    asyncio.run(main())
