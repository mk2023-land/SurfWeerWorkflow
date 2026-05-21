"""
Swell-richting scoring + pier-refractie.

Continue cosine-curve t.o.v. beach_normal (315° NW), gemoduleerd door
Gaussian pier-transmission rond shadow center (10° NNO).
"""
import math

from src.config import PIER_REFRACTION


def pier_transmission_factor(swell_direction_deg: int, period_s: float = 7.0) -> float:
    """
    Continue refractie-factor voor pier-shadow (vervangt binaire knip).

    Gaussian curve rond shadow center (10° NNO). Lange-periode swell (Tp ≥ 10s)
    krijgt +15% transmissie bonus (betere refractie).
    """
    d = swell_direction_deg % 360
    center = PIER_REFRACTION['shadow_center_deg']
    sigma = PIER_REFRACTION['shadow_half_width_deg']
    t_min = PIER_REFRACTION['min_transmission']
    t_max = PIER_REFRACTION['max_transmission']

    raw = (d - center) % 360
    delta = min(raw, 360 - raw)

    gaussian = math.exp(-(delta / sigma) ** 2)
    transmission = t_max - (t_max - t_min) * gaussian

    if period_s >= 10.0:
        bonus = PIER_REFRACTION['long_period_bonus']
        transmission = min(1.0, transmission + bonus * (1.0 - transmission))

    return max(t_min, min(1.0, transmission))


def _cos_to_beach(direction: float, beach_normal: float = 315.0) -> float:
    """
    Cosine van hoek tussen swell-richting en beach-normal (FROM).

    +1.0 = perfect aan-strand. Continue, geen sprongen op bucket-grenzen.
    """
    diff_rad = math.radians(direction - beach_normal)
    return math.cos(diff_rad)


def score_swell_direction_bonus(swell_direction_deg: int, period_s: float = 7.0) -> float:
    """
    Swell-richting bonus voor Noordwijk (max 10 punten).

    Continue cosine-curve t.o.v. beach_normal (315° NW), multiplicatief met
    pier-transmission. Bonus min=5 (recht uit land), max=10 (perfect NW).
    """
    direction = swell_direction_deg % 360
    transmission = pier_transmission_factor(direction, period_s)
    cos = _cos_to_beach(float(direction), beach_normal=315.0)
    raw = 5.0 + 5.0 * max(0.0, cos)
    return raw * transmission
