"""
Wave-gerelateerde modifiers en factoren voor scoring.

Energy flux, wave-age, Iribarren breaker-type, mixed-sea, directionele spread,
en partition-aware decompositie.
"""
import math
import os
from typing import Optional

from src.config import PARTITION_WEIGHTS
from src.data.models import WaveSpectrum
from src.scoring.deconstruct import decompose_spectrum


def _combine_golf_modifiers(factors: dict) -> float:
    """
    Weighted-sum aggregation van golf-modifiers (anti-collapse).

    Vervangt multiplicatieve stacking van 6 modifiers (we_factor, age_factor,
    iri_factor, face_quality, wind_trend, wind_spread). Bij borderline
    conditions worden alle factoren <1 → 0.85⁶ ≈ 0.38 collapse onder
    surfable-drempel. Met weighted-sum: 1 + 6 * w_avg * dev → milde, subtiele
    penalty in plaats van compounding decay.
    """
    # face_quality is hier verwijderd: te verdund (0,20 → max ~12% effect),
    # waardoor een uitgeblazen golf alsnog hoog scoorde. Wordt nu als EIGEN,
    # fit-bare multiplier op de golf-score toegepast (zie score_hour +
    # config.WIND_FACE_PENALTY). De overige vijf modifiers blijven de subtiele
    # weighted-sum.
    weights = {
        'wave_energy': 0.25,
        'wave_age': 0.25,
        'iribarren': 0.15,
        'wind_trend': 0.05,
        'wind_spread': 0.10,
    }
    combined = 1.0
    for name, w in weights.items():
        factor = factors.get(name, 1.0)
        dev = factor - 1.0
        combined += w * dev
    return max(0.60, min(1.25, combined))


def wave_energy_flux(hs_m: float, te_s: float) -> float:
    """
    Wave energy flux per meter golfkam, in kW/m.

    Standaard fysica-formule: P = (ρg²/64π) · Hs² · Te ≈ 0.49 · Hs² · Te.
    """
    return 0.49 * hs_m * hs_m * te_s


def wave_energy_factor(hs_m: float, te_s: float, reference_kw_m: Optional[float] = None) -> float:
    """Multiplier op golf_score gebaseerd op wave energy flux relatief aan referentie."""
    if hs_m <= 0 or te_s <= 0:
        return 1.0
    if reference_kw_m is None:
        env_ref = os.environ.get('WAVE_ENERGY_REF_KWM')
        if env_ref:
            try:
                reference_kw_m = float(env_ref)
            except ValueError:
                reference_kw_m = 3.43
        else:
            reference_kw_m = 3.43
    p = wave_energy_flux(hs_m, te_s)
    ratio = p / reference_kw_m
    factor = 1.0 + 0.15 * math.tanh((ratio - 1.0) / 1.5)
    return max(0.75, min(1.20, factor))


def wave_age(tp_s: float, wind_speed_kn: float) -> float:
    """Wave-age: c_p / U10. Maat voor 'rijpheid' van wind-zee."""
    if wind_speed_kn <= 0 or tp_s <= 0:
        return 999.0
    cp = 1.56 * tp_s
    u10_ms = wind_speed_kn / 1.944
    return cp / u10_ms


def wave_age_factor(tp_s: float, wind_speed_kn: float) -> float:
    """Soft penalty op golf_score gebaseerd op wave-age (cp/U10)."""
    age = wave_age(tp_s, wind_speed_kn)
    if age < 0.5:
        return 0.55
    if age < 0.83:
        return 0.55 + (age - 0.5) * (0.25 / 0.33)
    if age < 1.0:
        return 0.80 + (age - 0.83) * (0.15 / 0.17)
    if age <= 1.2:
        return 1.0
    if age <= 2.0:
        return min(1.05, 1.0 + 0.025 * (age - 1.2))
    return 1.05


def iribarren_factor(
    hs_m: float,
    tp_s: float,
    beach_slope: float = 0.02,
    tide_normalized: Optional[float] = None,
) -> float:
    """
    Iribarren-getal ξ = tan(β) / √(H/L₀) — voorspelt breaker-type.

    Tide-dependent slope: outer-bar (~0.015 bij LW) en inner-bar (~0.030 bij HW).
    """
    if hs_m <= 0 or tp_s <= 0:
        return 1.00
    L0 = 1.56 * tp_s * tp_s
    if L0 <= 0:
        return 1.00
    if tide_normalized is not None:
        t = max(0.0, min(1.0, tide_normalized))
        beach_slope = 0.015 + (0.030 - 0.015) * t
    xi = beach_slope / math.sqrt(hs_m / L0)
    if xi < 0.10:
        return 0.93
    if xi < 0.18:
        return 0.98
    if xi < 0.45:
        return 1.00 + (xi - 0.18) * (0.10 / 0.27)
    if xi < 0.80:
        return 1.10
    return 1.00


def _offshore_groom(cos_offshore: Optional[float], wind_speed_kn: Optional[float]) -> float:
    """
    Grooming-fractie (0.0-1.0) van aflandige wind die een zee opschoont.

    Aflandige/zij-aflandige wind strijkt het oppervlak glad: windzee + swell
    naast elkaar wordt dan een opschoning ("clean windlijn", de referentie-forecaster) i.p.v.
    chaotische chop. 0.0 = geen grooming (aanlandig, glassy <3kn, of >22kn die
    de boel juist afblaast); 1.0 = volledige grooming bij volle side-offshore.
    cos_offshore: +1 pure offshore, 0 cross-shore, −1 onshore (zie wind.py).
    """
    if (cos_offshore is None or wind_speed_kn is None
            or cos_offshore <= 0.0 or wind_speed_kn < 3.0 or wind_speed_kn > 22.0):
        return 0.0
    return min(1.0, cos_offshore / 0.30)  # vol effect vanaf side-offshore (cos≥0.30)


def mixed_sea_penalty(
    spectrum: WaveSpectrum,
    angle_threshold_deg: float = 30.0,
    min_height_m: float = 0.4,
    cos_offshore: Optional[float] = None,
    wind_speed_kn: Optional[float] = None,
) -> tuple:
    """
    Detecteer 'mixed sea' — twee swell-componenten uit duidelijk verschillende
    richtingen. Resultaat is rommelig wave-veld zonder dominante set-richting.

    Aflandige wind heft de penalty (deels) op: de wind groomt de coëxistentie
    van windzee + swell tot een clean windlijn i.p.v. chop. Zonder wind-context
    (cos_offshore/wind_speed_kn None) is het gedrag ongewijzigd t.o.v. vroeger.

    Returns: (is_mixed: bool, penalty: float in pt).
    """
    if not spectrum.peaks or len(spectrum.peaks) < 2:
        return (False, 0.0)
    sorted_peaks = sorted(
        spectrum.peaks,
        key=lambda p: (p.height_m ** 2) * p.period_s,
        reverse=True,
    )
    p1, p2 = sorted_peaks[0], sorted_peaks[1]
    if p1.height_m < min_height_m or p2.height_m < min_height_m:
        return (False, 0.0)
    raw = abs(p1.direction_deg - p2.direction_deg) % 360
    angle = min(raw, 360 - raw)
    if angle >= angle_threshold_deg:
        penalty = -3.0 * (1.0 - _offshore_groom(cos_offshore, wind_speed_kn))
        if penalty < -0.05:
            return (True, penalty)
        return (False, 0.0)
    return (False, 0.0)


def wave_quality_spread_factor(directional_spread_deg: Optional[float]) -> float:
    """
    Multiplier op golf-score op basis van directionele spreiding (SObh).

    Lage spreiding = clean groomed swell. Hoge spreiding = rommelig.
    Toepasbaar alleen met echte boei-observatie (nowcast t=0..3u).
    """
    if directional_spread_deg is None:
        return 1.0
    s = directional_spread_deg
    if s < 20.0:
        return 1.05
    if s < 30.0:
        return 1.00
    if s < 45.0:
        return 0.95
    return 0.88


def partition_energy_components(
    wave_spectrum: WaveSpectrum,
    cos_offshore: Optional[float] = None,
    wind_speed_kn: Optional[float] = None,
) -> dict:
    """
    Per-partition wave-energy decompositie.

    Returns dict met swell_energy_kwm, wind_sea_energy_kwm, swell_height_m,
    wind_sea_height_m, dominant_period_s, effective_height_m.

    Bij aflandige, gematigde wind wordt de windzee-downweging (0.65×) verzacht
    richting 1.0: de wind strijkt de windzee tot rideable face i.p.v. chop, dus
    die hoort dan vollediger mee te tellen in de effectieve hoogte. Zonder
    wind-context blijft de oude 0.65×-weging gelden.
    """
    decomposition = decompose_spectrum(wave_spectrum)

    swell_h = 0.0
    swell_T = 0.0
    if decomposition['ground_swell']:
        gs = decomposition['ground_swell']
        if decomposition['wind_swell']:
            ws_swell = decomposition['wind_swell']
            swell_h = math.sqrt(gs.height_m ** 2 + ws_swell.height_m ** 2)
            e_gs = gs.height_m ** 2 * gs.period_s
            e_ws = ws_swell.height_m ** 2 * ws_swell.period_s
            swell_T = (
                (e_gs + e_ws) / (gs.height_m ** 2 + ws_swell.height_m ** 2)
                if (gs.height_m + ws_swell.height_m) > 0 else 0
            )
        else:
            swell_h = gs.height_m
            swell_T = gs.period_s
    elif decomposition['wind_swell']:
        ws = decomposition['wind_swell']
        swell_h = ws.height_m
        swell_T = ws.period_s

    wind_h = 0.0
    wind_T = 0.0
    if decomposition['wind_sea']:
        wsea = decomposition['wind_sea']
        wind_h = wsea.height_m
        wind_T = wsea.period_s

    swell_energy = wave_energy_flux(swell_h, swell_T) if swell_h > 0 and swell_T > 0 else 0.0
    wind_energy = wave_energy_flux(wind_h, wind_T) if wind_h > 0 and wind_T > 0 else 0.0

    wind_sea_mult = PARTITION_WEIGHTS['wind_sea_multiplier']
    groom = _offshore_groom(cos_offshore, wind_speed_kn)
    if groom > 0.0:
        # Verzacht de downweging richting volledige weging bij offshore grooming.
        wind_sea_mult = wind_sea_mult + (PARTITION_WEIGHTS['swell_multiplier'] - wind_sea_mult) * groom
    swell_e_h_sq = swell_h ** 2 * PARTITION_WEIGHTS['swell_multiplier']
    wind_e_h_sq = wind_h ** 2 * wind_sea_mult
    eff_height = math.sqrt(swell_e_h_sq + wind_e_h_sq)

    # Fallback bij geen partities: gebruik Hs met multiplier 0.90.
    if eff_height < 0.01 and wave_spectrum.significant_height_total > 0:
        eff_height = wave_spectrum.significant_height_total * 0.90

    if swell_energy >= wind_energy and swell_T > 0:
        dominant_T = swell_T
    elif wind_T > 0:
        dominant_T = wind_T
    else:
        dominant_T = wave_spectrum.mean_period or 8.0

    return {
        'swell_energy_kwm': swell_energy,
        'wind_sea_energy_kwm': wind_energy,
        'swell_height_m': swell_h,
        'swell_period_s': swell_T,
        'wind_sea_height_m': wind_h,
        'wind_sea_period_s': wind_T,
        'dominant_period_s': dominant_T,
        'effective_height_m': eff_height,
    }
