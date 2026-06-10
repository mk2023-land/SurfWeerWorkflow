"""Tests voor de component-fit in scripts/calibrate.py — her-scoort de golf-keten
onder geleerde parameters (WIND_FACE_PENALTY strength + PARTITION wind_sea_mult).

NB: calibrate.py leeft lokaal (privé-pad-referenties) en wordt niet meegepusht;
deze test draait lokaal naast de suite.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import calibrate
from src.scoring.context import period_factor
from src.scoring.hourly import golf_height_curve


def _basis(golf, face_q=0.673, strength=0.5, swell_h=0.0, windsea_h=1.2,
           groom=0.0, eff=None, tp=4.8, wind=8.0, tide=18.0, dir_b=8.0, conf=1.0):
    import math
    if eff is None:
        eff = math.sqrt(swell_h ** 2 * 1.0 + windsea_h ** 2 * 0.65)
    return {
        'golf_score': golf, 'wind_score': wind, 'tide_score': tide,
        'swell_dir_bonus': dir_b, 'confidence': conf, 'face_q': face_q,
        'wfp_strength': strength, 'swell_h_m': swell_h, 'windsea_h_m': windsea_h,
        'eff_height_m': round(eff, 3), 'groom': groom, 'dominant_tp_s': tp,
    }


def test_rescore_identity_at_seed():
    """Bij (strength=gelogd, mult=0.65) geeft de her-score golf_old terug, op de
    snapshot-afronding na (eff_height_m wordt op 3 decimalen gelogd → ~0,05 ruis
    op golf; verwaarloosbaar)."""
    sb = _basis(golf=14.5)
    out = calibrate._rescore_golf(
        sb, strength=0.5, wind_sea_mult=0.65, golf_max=38.0, swell_mult=1.0,
        wfp_min=0.40, _curve=golf_height_curve, _pf=period_factor,
    )
    assert abs(out - 14.5) < 0.05


def test_rescore_softer_penalty_raises_golf():
    """Lagere face-penalty-strength en hogere wind-zee-weging → hogere golf-score."""
    sb = _basis(golf=14.5)
    softer = calibrate._rescore_golf(
        sb, strength=0.0, wind_sea_mult=1.0, golf_max=38.0, swell_mult=1.0,
        wfp_min=0.40, _curve=golf_height_curve, _pf=period_factor,
    )
    assert softer > 14.5


def test_fit_components_recovers_separation():
    """Met paren waar de referentie hoger callt dan onze seed-score, moet de
    component-fit zachtere parameters kiezen die de overeenkomst verbeteren."""
    pairs = []
    # 4 onshore windswell-dagen: ref zegt surfable/longboard, seed-golf laag.
    for golf, ref in [(14.5, 'surfable'), (13.0, 'surfable'),
                      (12.0, 'longboard'), (16.0, 'surfable')]:
        pairs.append({'date': f'd{golf}', 'ref': ref, 'our_verdict': 'flat',
                      'our_peak_score': golf + 28.0,
                      'features': {'score_basis': _basis(golf=golf)}})
    # 2 echte flat-dagen (mini swell): moeten flat blijven.
    for golf in (2.0, 1.0):
        pairs.append({'date': f'f{golf}', 'ref': 'flat', 'our_verdict': 'flat',
                      'our_peak_score': golf + 10.0,
                      'features': {'score_basis': _basis(
                          golf=golf, windsea_h=0.4, tide=5.0, dir_b=1.0)}})
    res = calibrate.fit_components(pairs)
    assert res is not None
    assert res['n'] == 6
    assert 0.0 <= res['wind_face_strength'] <= 0.7
    assert 0.65 <= res['wind_sea_multiplier'] <= 1.0
    # Moet beter zijn dan de huidige 0% (alles flat vs 4 niet-flat refs).
    assert res['agreement'] > 0.0


def test_fit_components_too_few_basis():
    """Onder 6 paren-met-score_basis: geen fit (None)."""
    pairs = [{'date': 'x', 'ref': 'flat', 'our_verdict': 'flat',
              'our_peak_score': 10, 'features': {}}]
    assert calibrate.fit_components(pairs) is None
