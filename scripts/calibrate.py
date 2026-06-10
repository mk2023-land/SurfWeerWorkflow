"""
Leer-loop: fit de scoring-parameters op de gepairde referentie-data
(data-driven, geen hand-gekozen verdict-drempels).

Idee (referentie-pariteit, toekomstbestendig):
  - ONZE kant — `data/forecast_features.jsonl` (door main.py per run gevuld):
    per forecast-dag de fysische features op het piek-uur + ons verdict + onze
    piek-score voor Noordwijk.
  - REFERENTIE-LABELS — uit het verstuurde referentie-bericht (geparst en in een
    privé-archief opgeslagen); de ingest-stap joint die met onze snapshots tot trainingsparen in `data/training/ref_pairs.jsonl`.
  - EVALUEER: hoe vaak komt ons verdict overeen met de referentie? (confusion)
  - FIT: zoek de longboard/surfable-drempels op onze piek-score die de
    overeenkomst maximaliseren → schrijf naar `data/learned_params.json`
    (config.py laadt dat over de seed-waarden).
  - MODEL ERNAAST: train een lichte numpy-classifier (features → verdict) en
    rapporteer zijn leave-one-out-overeenkomst NAAST de drempel-fit — "beide
    naast elkaar", zodat we per datavolume kunnen kiezen.

Bewust GEEN hardcoded verdict-regels: de drempels worden gefit, niet geraden.
Bij te weinig data rapporteert het script dat eerlijk en raakt het de
learned_params NIET aan (de fysica-seed blijft staan).

Run:  uv run python scripts/calibrate.py [--write] [--min-pairs N]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Script leeft in <repo>/scripts/ — zorg dat src/ importeerbaar is voor de
# component-fit (her-scoort de golf-keten met de ÉCHTE scoring-helpers, geen
# her-implementatie → geen drift met de live scoring).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

VERDICTS = ['flat', 'longboard', 'surfable']  # ordinaal: flat < longboard < surfable
_VRANK = {v: i for i, v in enumerate(VERDICTS)}

# Training-paren leven in de private archive-repo (~/Merlijn/referentie-archief), durable +
# privé — NIET meer in de publieke repo onder data/training/ (gitignored,
# vluchtig). Spiegelt de locatie die de ingest gebruikt. Override: REF_PAIRS_PATH.
_DEFAULT_PAIRS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / 'referentie-archief' / 'data' / 'training' / 'ref_pairs.jsonl'
)
PAIRS_PATH = Path(os.getenv('REF_PAIRS_PATH', _DEFAULT_PAIRS_PATH))
LEARNED_PATH = Path(os.getenv('LEARNED_PARAMS_PATH', 'data/learned_params.json'))


# ---------------------------------------------------------------------------
# Gepairde data laden (label + onze snapshot), geschreven door de ingest.
# ---------------------------------------------------------------------------
def load_pairs() -> list[dict]:
    if not PAIRS_PATH.exists():
        return []
    out = []
    for line in PAIRS_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            p = json.loads(line)
        except json.JSONDecodeError:
            continue
        if p.get('paired') and p.get('our_verdict') and p.get('ref_verdict') in VERDICTS:
            out.append({
                'date': p['date'],
                'ref': p['ref_verdict'],
                'our_verdict': p['our_verdict'],
                'our_peak_score': p.get('our_peak_score'),
                'features': p.get('features') or {},
            })
    return out


# ---------------------------------------------------------------------------
# Evaluatie
# ---------------------------------------------------------------------------
def confusion(pairs: list[dict], pred_key: str) -> tuple[dict, float]:
    cm = defaultdict(lambda: defaultdict(int))
    correct = 0
    for p in pairs:
        t, o = p['ref'], p[pred_key]
        cm[t][o] += 1
        if t == o:
            correct += 1
    acc = correct / len(pairs) if pairs else 0.0
    return cm, acc


def print_confusion(cm: dict, title: str) -> None:
    print(f"\n  {title} (rij=referentie, kolom=onze):")
    print("    " + "".join(f"{v:>11}" for v in VERDICTS))
    for t in VERDICTS:
        row = "".join(f"{cm.get(t, {}).get(o, 0):>11}" for o in VERDICTS)
        print(f"    {t:>9}{row}")


# ---------------------------------------------------------------------------
# Drempel-fit (data-driven i.p.v. hardcoded 42/60)
# ---------------------------------------------------------------------------
def verdict_from_score(score: float, lb: float, sb: float) -> str:
    if score >= sb:
        return 'surfable'
    if score >= lb:
        return 'longboard'
    return 'flat'


def fit_thresholds(pairs: list[dict]):
    """Zoek (longboard_thr, surfable_thr) op onze piek-score die de overeenkomst
    met de referentie maximaliseert. Returnt (lb, sb, agreement) of None."""
    scored = [p for p in pairs if isinstance(p.get('our_peak_score'), (int, float))]
    if not scored:
        return None
    best = (None, None, -1.0)
    grid = list(range(10, 91, 2))
    for lb in grid:
        for sb in grid:
            if sb <= lb:
                continue
            ok = sum(
                1 for p in scored
                if verdict_from_score(p['our_peak_score'], lb, sb) == p['ref']
            )
            acc = ok / len(scored)
            if acc > best[2]:
                best = (float(lb), float(sb), acc)
    return best


# ---------------------------------------------------------------------------
# Component-fit: her-scoor de GOLF-keten onder kandidaat-parameters
# (WIND_FACE_PENALTY strength + PARTITION wind_sea_multiplier) en zoek samen met
# de drempels de set die de overeenkomst maximaliseert. Anders dan fit_thresholds
# (die alleen de knip-punten op een vaste score verschuift) raakt dit de
# score-componenten die het structurele onder-callen van onshore windswell
# veroorzaken. Vereist de `score_basis` in elk paar (main.py schrijft die sinds
# 2026-06-10); paren zonder basis worden overgeslagen.
#
# Her-score is EXACT in het gangbare windswell-regime: de golf-keten is daar
# golf = curve(eff_height) · period_factor(T) · residual · face_pen, waarbij
# `residual` (combined-modifiers, mixed-sea, spread, bonussen, cap) constant is
# t.o.v. de twee knoppen. We meten residual uit de gelogde golf_score en passen
# alleen curve(eff) en face_pen opnieuw toe; het totaal komt via de ÉCHTE
# ScoreBreakdown.total_score (env-blend + confidence) — geen drift.
# ---------------------------------------------------------------------------
def _face_pen(strength: float, face_q: float, min_factor: float) -> float:
    return max(min_factor, 1.0 - strength * (1.0 - face_q))


def _rescore_golf(sb: dict, strength: float, wind_sea_mult: float,
                  golf_max: float, swell_mult: float, wfp_min: float,
                  _curve, _pf) -> float:
    """Her-scoor golf_score van één piek-uur onder kandidaat (strength,
    wind_sea_mult). Gebruikt de gedeelde curve/period_factor uit src."""
    swell_h = sb.get('swell_h_m') or 0.0
    windsea_h = sb.get('windsea_h_m') or 0.0
    groom = sb.get('groom') or 0.0
    eff_old = sb.get('eff_height_m') or 0.0
    T = sb.get('dominant_tp_s') or 0.0
    face_q = sb.get('face_q')
    strength_old = sb.get('wfp_strength')
    golf_old = sb.get('golf_score')
    if None in (face_q, strength_old, golf_old) or eff_old <= 0:
        return golf_old if golf_old is not None else 0.0

    # Nieuwe effectieve hoogte met gegroomde wind-zee-multiplier (spiegelt
    # partition_energy_components). Geen partities → multiplier-knop is no-op.
    if swell_h < 0.01 and windsea_h < 0.01:
        eff_new = eff_old
    else:
        mult = wind_sea_mult + (swell_mult - wind_sea_mult) * groom if groom > 0 else wind_sea_mult
        eff_new = math.sqrt(swell_h ** 2 * swell_mult + windsea_h ** 2 * mult)

    pf = _pf(T) if T > 0 else 1.0
    fp_old = _face_pen(strength_old, face_q, wfp_min)
    denom = _curve(eff_old) * pf * fp_old
    if denom <= 0:
        return golf_old  # niet her-scoorbaar (golf was 0) → gemeten waarde
    residual = golf_old / denom
    fp_new = _face_pen(strength, face_q, wfp_min)
    golf_new = _curve(eff_new) * pf * residual * fp_new
    return max(0.0, min(golf_max, golf_new))


def _ordinal_cost(pred: str, ref: str) -> int:
    """Afstand-gewogen fout: flat↔surfable (2) telt zwaarder dan flat↔longboard
    (1). Lost de 0/1-loss-kritiek op (alle fouten even zwaar)."""
    return abs(_VRANK[pred] - _VRANK[ref])


def fit_components(pairs: list[dict]):
    """Grid-search over (WIND_FACE_PENALTY strength, PARTITION wind_sea_multiplier,
    longboard-drempel, surfable-drempel) die de ordinale fout minimaliseert
    (tie-break: meer exacte matches, dan minste afwijking van de seed).
    Floor-bewust: past min_golf_* toe op de her-gescoorde golf. Returnt dict of
    None bij onvoldoende paren-met-score_basis."""
    from src.config import SCORING_WEIGHTS, SURF_THRESHOLDS, WIND_FACE_PENALTY, PARTITION_WEIGHTS
    from src.data.models import ScoreBreakdown
    from src.scoring.context import period_factor
    from src.scoring.hourly import golf_height_curve

    usable = [p for p in pairs if (p.get('features') or {}).get('score_basis')]
    if len(usable) < 6:
        return None

    golf_max = float(SCORING_WEIGHTS['golf_max'])
    swell_mult = float(PARTITION_WEIGHTS['swell_multiplier'])
    wfp_min = float(WIND_FACE_PENALTY['min_factor'])
    mg_lb = float(SURF_THRESHOLDS['min_golf_longboard'])
    mg_sb = float(SURF_THRESHOLDS['min_golf_surfable'])
    seed_strength = float(WIND_FACE_PENALTY['strength'])
    seed_mult = float(PARTITION_WEIGHTS['wind_sea_multiplier'])
    _t0 = datetime(2000, 1, 1)

    strength_grid = [round(0.1 * i, 2) for i in range(0, 8)]      # 0.0..0.7
    mult_grid = [0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.00]        # ≥ seed 0.65
    lb_grid = list(range(10, 61, 2))
    sb_grid = list(range(14, 81, 2))

    def _verdict(total, golf, lb, sb):
        if total >= sb and golf >= mg_sb:
            return 'surfable'
        if total >= lb and golf >= mg_lb:
            return 'longboard'
        return 'flat'

    best = None  # (cost, -exact, reg, params, agreement)
    for strength in strength_grid:
        for mult in mult_grid:
            # Her-scoor golf + totaal één keer per (strength, mult); drempels
            # daarna goedkoop variëren.
            rescored = []
            for p in usable:
                sb_b = p['features']['score_basis']
                golf = _rescore_golf(sb_b, strength, mult, golf_max, swell_mult,
                                     wfp_min, golf_height_curve, period_factor)
                bd = ScoreBreakdown(
                    timestamp=_t0,
                    golf_score=golf,
                    wind_score=sb_b.get('wind_score') or 0.0,
                    tide_score=sb_b.get('tide_score') or 0.0,
                    swell_dir_bonus=sb_b.get('swell_dir_bonus') or 0.0,
                    confidence=sb_b.get('confidence') or 1.0,
                )
                rescored.append((bd.total_score, golf, p['ref']))
            for lb in lb_grid:
                for sb in sb_grid:
                    if sb <= lb:
                        continue
                    cost = exact = 0
                    for total, golf, ref in rescored:
                        pred = _verdict(total, golf, lb, sb)
                        cost += _ordinal_cost(pred, ref)
                        exact += (pred == ref)
                    reg = abs(strength - seed_strength) + abs(mult - seed_mult)
                    key = (cost, -exact, reg)
                    if best is None or key < best[0]:
                        best = (key, {
                            'wind_face_strength': strength,
                            'wind_sea_multiplier': mult,
                            'longboard': float(lb),
                            'surfable': float(sb),
                        }, exact / len(usable))
    if best is None:
        return None
    return {**best[1], 'agreement': best[2], 'n': len(usable),
            'ordinal_cost': best[0][0]}


# ---------------------------------------------------------------------------
# Model ernaast: multinomiale logistische regressie (pure numpy)
# ---------------------------------------------------------------------------
_FEATS = ['hs_m', 'tp_s', 'wind_speed_kn', 'offshore_cos', 'tide_level_norm']


def _vec(rec: dict) -> list[float]:
    return [float(rec.get(f) if rec.get(f) is not None else 0.0) for f in _FEATS]


def train_eval_model(pairs: list[dict]):
    """Leave-one-out overeenkomst van een numpy-softmax-classifier
    (features → verdict). None bij te weinig data of geen numpy."""
    usable = [p for p in pairs if p.get('features')]
    if len(usable) < 6:
        return None
    try:
        import numpy as np
    except ImportError:
        return None

    X = np.array([_vec(p['features']) for p in usable], dtype=float)
    y = np.array([_VRANK[p['ref']] for p in usable], dtype=int)
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0

    def softmax_fit(Xtr, ytr, k=3, epochs=400, lr=0.1, l2=0.01):
        n, d = Xtr.shape
        W = np.zeros((d, k)); b = np.zeros(k)
        Y = np.eye(k)[ytr]
        for _ in range(epochs):
            Z = Xtr @ W + b
            Z -= Z.max(1, keepdims=True)
            P = np.exp(Z); P /= P.sum(1, keepdims=True)
            gW = Xtr.T @ (P - Y) / n + l2 * W
            gb = (P - Y).mean(0)
            W -= lr * gW; b -= lr * gb
        return W, b

    correct = 0
    for i in range(len(usable)):
        idx = [j for j in range(len(usable)) if j != i]
        Xtr = (X[idx] - mu) / sd
        W, b = softmax_fit(Xtr, y[idx])
        xi = (X[i] - mu) / sd
        if int(np.argmax(xi @ W + b)) == y[i]:
            correct += 1
    return correct / len(usable)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description='Fit scoring-params op referentie-data')
    ap.add_argument('--write', action='store_true', help='Schrijf learned_params.json')
    ap.add_argument('--min-pairs', type=int, default=12,
                    help='Minimum aantal gepairde dagen voordat we params wegschrijven')
    args = ap.parse_args()

    pairs = load_pairs()
    print("=" * 60)
    print("REFERENTIE-PARITEIT LEER-LOOP — calibratie")
    print("=" * 60)
    print(f"Gepairde dagen: {len(pairs)}")

    if not pairs:
        print("\nNog GEEN gepairde dagen. De feature-logging (main.py) vult vooruit;")
        print("verwerk referentie-berichten via de ingest-stap.")
        print("Zodra er paren zijn, fit dit script. (Tot dan blijft de fysica-seed.)")
        return

    cm_now, acc_now = confusion(pairs, 'our_verdict')
    print(f"\nHUIDIGE overeenkomst: {acc_now:.0%} ({len(pairs)} dagen)")
    print_confusion(cm_now, "Huidig (seed-params)")

    fit = fit_thresholds(pairs)
    lb = sb = acc_fit = None
    if fit and fit[0] is not None:
        lb, sb, acc_fit = fit
        print(f"\nGEFITTE drempels: longboard>={lb:.0f}, surfable>={sb:.0f} "
              f"→ overeenkomst {acc_fit:.0%}")
    else:
        print("\nDrempel-fit: onvoldoende score-data.")

    model_acc = train_eval_model(pairs)
    if model_acc is not None:
        print(f"Model (numpy-softmax, leave-one-out): {model_acc:.0%}")
    else:
        print("Model ernaast: nog te weinig data (min 6 gepairde dagen).")

    # Component-fit: her-scoort de golf-keten (face-penalty + wind-zee-weging)
    # i.p.v. alleen de drempels te schuiven. Vereist score_basis in de paren.
    comp = fit_components(pairs)
    n_basis = sum(1 for p in pairs if (p.get('features') or {}).get('score_basis'))
    if comp:
        print(f"\nCOMPONENT-FIT ({comp['n']} paren mét score_basis): "
              f"wind_face_strength={comp['wind_face_strength']}, "
              f"wind_sea_multiplier={comp['wind_sea_multiplier']}, "
              f"longboard>={comp['longboard']:.0f}, surfable>={comp['surfable']:.0f} "
              f"→ overeenkomst {comp['agreement']:.0%} (ordinale fout {comp['ordinal_cost']})")
    else:
        print(f"\nComponent-fit: nog te weinig paren mét score_basis "
              f"({n_basis}; min 6). Groeit vooruit zodra nieuwe runs loggen.")

    if not args.write:
        print("\n(Dry-run; gebruik --write om learned_params.json te updaten.)")
        return

    if len(pairs) < args.min_pairs:
        print(f"\nNIET weggeschreven: {len(pairs)} < min-pairs {args.min_pairs}. "
              f"Te weinig data → seed blijft (geen overfit).")
        return

    # Kies de beste fit: component-fit wint als die de overeenkomst het meest
    # verbetert; anders de drempel-only fit. Niets schrijven als geen van beide
    # boven de huidige seed-overeenkomst uitkomt.
    comp_acc = comp['agreement'] if comp else -1.0
    thr_acc = acc_fit if (lb is not None) else -1.0
    if comp and comp_acc >= thr_acc and comp_acc > acc_now:
        out = {
            'WIND_FACE_PENALTY': {'strength': comp['wind_face_strength']},
            'PARTITION_WEIGHTS': {'wind_sea_multiplier': comp['wind_sea_multiplier']},
            'SURF_THRESHOLDS': {'longboard': comp['longboard'], 'surfable': comp['surfable']},
            '_meta': {
                'fitted_at': datetime.now().isoformat(),
                'fit_kind': 'component',
                'n_pairs': len(pairs),
                'n_basis': comp['n'],
                'agreement_before': round(acc_now, 3),
                'agreement_after': round(comp_acc, 3),
                'model_loo_agreement': model_acc,
            },
        }
    elif lb is not None and thr_acc > acc_now:
        out = {
            'SURF_THRESHOLDS': {'longboard': lb, 'surfable': sb},
            '_meta': {
                'fitted_at': datetime.now().isoformat(),
                'fit_kind': 'thresholds',
                'n_pairs': len(pairs),
                'agreement_before': round(acc_now, 3),
                'agreement_after': round(thr_acc, 3),
                'model_loo_agreement': model_acc,
            },
        }
    else:
        print(f"\nNIET weggeschreven: geen fit verbetert boven de seed "
              f"(seed {acc_now:.0%}, component {comp_acc:.0%}, drempel {thr_acc:.0%}).")
        return
    LEARNED_PATH.write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(f"\nGeschreven naar {LEARNED_PATH} ({out['_meta']['fit_kind']}-fit) "
          f"— config.py laadt dit over de seed.")


if __name__ == '__main__':
    main()
