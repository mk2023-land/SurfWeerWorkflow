"""
Swell deconstructie module.
Scheidt golf data in wind sea, wind swell, en groundswell componenten.
"""
import logging
from typing import List, Optional
import numpy as np

from src.data.models import (
    WaveSpectrum,
    SpectralPeak,
    SwellType,
    HourState
)

from src.config import SWELL_TYPES, FREQUENCY_RANGES

logger = logging.getLogger(__name__)


def decompose_spectrum(spectrum: WaveSpectrum) -> dict:
    """
    Deconstrueer een golf spectrum in componenten.

    Returns:
        Dictionary met wind_sea, wind_swell, ground_swell componenten
    """
    peaks = spectrum.peaks

    # Als geen pieken, maak dummy op basis van totaal
    if not peaks:
        logger.warning(f"Geen spectrale pieken gevonden voor {spectrum.timestamp}")
        return {
            'wind_sea': None,
            'wind_swell': None,
            'ground_swell': None,
            'dominant_type': None,
            'total_height': spectrum.significant_height_total
        }

    # Classificeer pieken
    wind_sea_peaks = [p for p in peaks if p.type == SwellType.WIND_SEA]
    wind_swell_peaks = [p for p in peaks if p.type == SwellType.WIND_SWELL]
    ground_swell_peaks = [p for p in peaks if p.type == SwellType.GROUND_SWELL]

    # Haal dominante piek per type
    wind_sea = max(wind_sea_peaks, key=lambda p: p.height_m) if wind_sea_peaks else None
    wind_swell = max(wind_swell_peaks, key=lambda p: p.height_m) if wind_swell_peaks else None
    ground_swell = max(ground_swell_peaks, key=lambda p: p.height_m) if ground_swell_peaks else None

    # Bepaal dominant type (hoogste piek)
    all_peaks_sorted = sorted(peaks, key=lambda p: p.height_m, reverse=True)
    dominant_type = all_peaks_sorted[0].type if all_peaks_sorted else None

    return {
        'wind_sea': wind_sea,
        'wind_swell': wind_swell,
        'ground_swell': ground_swell,
        'dominant_type': dominant_type,
        'total_height': spectrum.significant_height_total
    }


def calculate_period_from_frequency(frequency_mhz: float) -> float:
    """
    Bereken periode in seconden uit frequentie in mHz.

    Formule: periode_seconden = 1000 / frequentie_mhz
    """
    if frequency_mhz <= 0:
        return 0.0
    return 1000.0 / frequency_mhz


def classify_swell_by_period(period_s: float) -> SwellType:
    """
    Classificeer swell type op basis van periode.

    Args:
        period_s: Periode in seconden

    Returns:
        SwellType enum
    """
    if period_s < 7:
        return SwellType.WIND_SEA
    elif period_s < 9:
        return SwellType.WIND_SWELL
    else:
        return SwellType.GROUND_SWELL


def classify_swell_by_frequency(frequency_mhz: float) -> SwellType:
    """
    Classificeer swell type op basis van frequentie.

    Args:
        frequency_mhz: Frequentie in mHz

    Returns:
        SwellType enum
    """
    if frequency_mhz >= 200:
        return SwellType.WIND_SEA
    elif frequency_mhz >= 111:
        return SwellType.WIND_SWELL
    else:
        return SwellType.GROUND_SWELL


def extract_direction_info(spectrum: WaveSpectrum) -> dict:
    """
    Extraheer richting informatie uit spectrum.

    Returns:
        Dictionary met mean direction en directions per type
    """
    mean_direction = spectrum.mean_direction

    decomposition = decompose_spectrum(spectrum)

    return {
        'mean_direction': mean_direction,
        'wind_sea_direction': decomposition['wind_sea'].direction_deg if decomposition['wind_sea'] else None,
        'wind_swell_direction': decomposition['wind_swell'].direction_deg if decomposition['wind_swell'] else None,
        'ground_swell_direction': decomposition['ground_swell'].direction_deg if decomposition['ground_swell'] else None,
    }


def has_groundswell_through_windsea(spectrum: WaveSpectrum) -> bool:
    """
    Controleer of er een groundswell door windgolven heen komt.

    Dit is het "perfecte voorspellingsmoment" dat Tobias noemt:
    groundswell op 10s (100mhz) + wind sea op 5s (200mhz) zijn
    twee aparte energiepieken.

    Returns:
        True als groundswell aanwezig is EN wind sea aanwezig is
    """
    decomposition = decompose_spectrum(spectrum)

    return (
        decomposition['ground_swell'] is not None and
        decomposition['wind_sea'] is not None
    )


def is_clean_swell(spectrum: WaveSpectrum) -> bool:
    """
    Controleer of de swell "clean" is (één dominante piek).

    Returns:
        True als één piek dominant is (≥70% van totale energie)
    """
    if not spectrum.peaks or len(spectrum.peaks) == 1:
        return True

    # Bereken energie per piek (E ~ H²)
    energies = [(p.height_m ** 2) for p in spectrum.peaks]
    total_energy = sum(energies)

    if total_energy == 0:
        return True

    max_energy = max(energies)
    return (max_energy / total_energy) >= 0.7


def get_swell_quality_score(spectrum: WaveSpectrum) -> float:
    """
    Bereken swell kwaliteit score (0.0-1.0).

    Factoren:
    - Clean swell vs messy (meerdere pieken)
    - Groundswell aanwezigheid
    - Consistentie van richting

    Returns:
        Kwaliteit score 0.0-1.0
    """
    decomposition = decompose_spectrum(spectrum)

    score = 0.0

    # Groundswell bonus (max 0.4)
    if decomposition['ground_swell']:
        score += 0.4
        # Extra bonus voor lange periode groundswell
        if decomposition['ground_swell'].period_s >= 10:
            score += 0.1

    # Clean swell bonus (max 0.3)
    if is_clean_swell(spectrum):
        score += 0.3
    elif has_groundswell_through_windsea(spectrum):
        score += 0.2  # Ook goed, maar minder dan pure clean

    # Wind swell bonus (max 0.2)
    if decomposition['wind_swell']:
        score += 0.2

    return min(1.0, score)