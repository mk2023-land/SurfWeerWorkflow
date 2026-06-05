"""
Leer-loop: fit de scoring-parameters op het referentie-forecaster-archief (data-driven,
geen hand-gekozen verdict-drempels).

Idee (referentie-forecaster-pariteit, toekomstbestendig):
  1. ONZE kant — `data/forecast_features.jsonl` (door main.py per run gevuld):
     per forecast-dag de fysische features op het piek-uur + ons verdict +
     onze piek-score voor Noordwijk.
  2. referentie-forecaster-labels — `~/Merlijn/referentie-forecaster/data/ref_archive/*.meta.json`:
     het verdict (flat/longboard/surfable) per spot/dag uit zijn berichten.
  3. PAIR op datum → (features, onze_score, ons_verdict, referentie-forecaster_verdict).
  4. EVALUEER: hoe vaak komt ons verdict overeen met referentie-forecaster? (confusion matrix)
  5. FIT: zoek de longboard/surfable-drempels op onze piek-score die de
     overeenkomst met referentie-forecaster maximaliseren → schrijf naar
     `data/learned_params.json` (config.py laadt dat over de seed-waarden).
  6. MODEL ERNAAST: train een lichte numpy-classifier (features → verdict) en
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
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

VERDICTS = ['flat', 'longboard', 'surfable']  # ordinaal: flat < longboard < surfable
_VRANK = {v: i for i, v in enumerate(VERDICTS)}

FEATURES_PATH = Path(os.getenv('FEATURES_PATH', 'data/forecast_features.jsonl'))
LEARNED_PATH = Path(os.getenv('LEARNED_PARAMS_PATH', 'data/learned_params.json'))
_referentie-forecaster_DEFAULT = Path(__file__).resolve().parent.parent.parent / 'referentie-forecaster' / 'data' / 'ref_archive'
referentie-forecaster_DIR = Path(os.getenv('REF_ARCHIVE_DIR', _referentie-forecaster_DEFAULT))

# referentie-forecaster-spots die we als Noordwijk-equivalent accepteren (hij groepeert
# "zvoort/nwijk" en Zuid-Holland-strand vaak samen). Volgorde = voorkeur.
_NWIJK_KEYS = ['Noordwijk', 'noordwijk', 'Zandvoort', 'zandvoort', 'nwijk', 'zvoort']


# ---------------------------------------------------------------------------
# referentie-forecaster-labels laden
# ---------------------------------------------------------------------------
def load_referentie-forecaster_labels() -> dict[str, str]:
    """{forecast_date_iso: referentie-forecaster_verdict} voor Noordwijk, best-effort.

    Leest de geparste `verdicts_per_spot` uit de meta-bestanden. Het verdict
    geldt voor de DAG van het bericht (referentie-forecaster schrijft 's avonds voor de dag
    erna; we koppelen hier conservatief op de bericht-datum zelf — de
    feature-snapshots bevatten meerdere forecast-dagen per datum, dus de join
    vindt de juiste). Dagen zonder Noordwijk-label worden overgeslagen.
    """
    labels: dict[str, str] = {}
    if not referentie-forecaster_DIR.exists():
        return labels
    for meta_file in sorted(referentie-forecaster_DIR.glob('*.meta.json')):
        try:
            meta = json.loads(meta_file.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            continue
        d = meta.get('date')
        vps = meta.get('verdicts_per_spot') or {}
        verdict = None
        for key in _NWIJK_KEYS:
            if key in vps:
                verdict = vps[key]
                break
        if d and verdict in VERDICTS:
            labels[d] = verdict
    return labels


# ---------------------------------------------------------------------------
# Onze feature-snapshots laden
# ---------------------------------------------------------------------------
def load_our_snapshots() -> dict[str, dict]:
    """{forecast_date_iso: record} — kies per forecast-dag de meest relevante
    snapshot: bij voorkeur die gemaakt OP de dag zelf (day_offset==0), anders
    de laatste vóór de dag. Zo vergelijken we onze 'nowcast' met referentie-forecaster."""
    if not FEATURES_PATH.exists():
        return {}
    by_date: dict[str, list[dict]] = defaultdict(list)
    for line in FEATURES_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get('spot') != 'noordwijk' or not r.get('forecast_date'):
            continue
        by_date[r['forecast_date']].append(r)
    chosen: dict[str, dict] = {}
    for fdate, recs in by_date.items():
        recs.sort(key=lambda r: (abs(r.get('day_offset', 99)), r.get('run_timestamp', '')))
        chosen[fdate] = recs[0]
    return chosen


def build_pairs() -> list[dict]:
    labels = load_referentie-forecaster_labels()
    ours = load_our_snapshots()
    pairs = []
    for d, tob in labels.items():
        if d in ours:
            r = ours[d]
            pairs.append({
                'date': d,
                'referentie-forecaster': tob,
                'our_verdict': r.get('our_verdict'),
                'our_peak_score': r.get('our_peak_score'),
                'features': r,
            })
    return pairs, labels, ours


# ---------------------------------------------------------------------------
# Evaluatie
# ---------------------------------------------------------------------------
def confusion(pairs: list[dict], pred_key: str) -> tuple[dict, float]:
    cm = defaultdict(lambda: defaultdict(int))
    correct = 0
    for p in pairs:
        t, o = p['referentie-forecaster'], p[pred_key]
        cm[t][o] += 1
        if t == o:
            correct += 1
    acc = correct / len(pairs) if pairs else 0.0
    return cm, acc


def print_confusion(cm: dict, title: str) -> None:
    print(f"\n  {title} (rij=referentie-forecaster, kolom=onze):")
    header = "    " + "".join(f"{v:>11}" for v in VERDICTS)
    print(header)
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


def fit_thresholds(pairs: list[dict]) -> tuple[float, float, float]:
    """Zoek (longboard_thr, surfable_thr) op onze piek-score die de overeenkomst
    met referentie-forecaster maximaliseert. Returnt (lb, sb, agreement)."""
    scored = [p for p in pairs if isinstance(p.get('our_peak_score'), (int, float))]
    if not scored:
        return None
    best = (None, None, -1.0)
    # Grid over plausibele drempels; sb > lb afgedwongen.
    grid = [x for x in range(10, 91, 2)]
    for lb in grid:
        for sb in grid:
            if sb <= lb:
                continue
            ok = sum(
                1 for p in scored
                if verdict_from_score(p['our_peak_score'], lb, sb) == p['referentie-forecaster']
            )
            acc = ok / len(scored)
            if acc > best[2]:
                best = (float(lb), float(sb), acc)
    return best


# ---------------------------------------------------------------------------
# Model ernaast: multinomiale logistische regressie (pure numpy)
# ---------------------------------------------------------------------------
_FEATS = ['hs_m', 'tp_s', 'wind_speed_kn', 'offshore_cos', 'tide_level_norm']


def _vec(rec: dict) -> list[float]:
    return [float(rec.get(f) if rec.get(f) is not None else 0.0) for f in _FEATS]


def train_eval_model(pairs: list[dict]) -> float | None:
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
    y = np.array([_VRANK[p['referentie-forecaster']] for p in usable], dtype=int)
    # Standaardiseer features (stabiliteit).
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
        pred = int(np.argmax(xi @ W + b))
        if pred == y[i]:
            correct += 1
    return correct / len(usable)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description='Fit scoring-params op referentie-forecaster-archief')
    ap.add_argument('--write', action='store_true', help='Schrijf learned_params.json')
    ap.add_argument('--min-pairs', type=int, default=12,
                    help='Minimum aantal gepairde dagen voordat we params wegschrijven')
    args = ap.parse_args()

    pairs, labels, ours = build_pairs()
    print("=" * 64)
    print("referentie-forecaster-PARITEIT LEER-LOOP — calibratie")
    print("=" * 64)
    print(f"referentie-forecaster-labels (Noordwijk):   {len(labels)}")
    print(f"Onze feature-snapshots:      {len(ours)} forecast-dagen")
    print(f"Gepairde dagen (overlap):    {len(pairs)}")

    if not pairs:
        print("\nNog GEEN overlap tussen referentie-forecaster-labels en onze feature-snapshots.")
        print("De feature-logging (main.py) is net toegevoegd en vult vooruit:")
        print("elke productie-run legt onze kant vast; stuur referentie-forecaster-berichten via")
        print("scripts/ingest_reference_message.py. Zodra er overlap is, fit dit script.")
        print("(Tot dan blijft de fysica-seed in config.py de waarheid.)")
        return

    # Huidige overeenkomst (ons verdict zoals het systeem nu beslist)
    cm_now, acc_now = confusion(pairs, 'our_verdict')
    print(f"\nHUIDIGE overeenkomst met referentie-forecaster: {acc_now:.0%} ({len(pairs)} dagen)")
    print_confusion(cm_now, "Huidig (seed-params)")

    # Drempel-fit
    fit = fit_thresholds(pairs)
    if fit and fit[0] is not None:
        lb, sb, acc_fit = fit
        print(f"\nGEFITTE drempels: longboard>={lb:.0f}, surfable>={sb:.0f} "
              f"→ overeenkomst {acc_fit:.0%}")
    else:
        lb = sb = None
        print("\nDrempel-fit: onvoldoende score-data.")

    # Model ernaast
    model_acc = train_eval_model(pairs)
    if model_acc is not None:
        print(f"Model (numpy-softmax, leave-one-out): {model_acc:.0%} overeenkomst")
    else:
        print(f"Model ernaast: nog te weinig data (min 6 gepairde dagen).")

    # Wegschrijven?
    if args.write:
        if len(pairs) < args.min_pairs:
            print(f"\nNIET weggeschreven: {len(pairs)} < min-pairs {args.min_pairs}. "
                  f"Te weinig data → seed blijft staan (geen overfit-hardcoding).")
            return
        if lb is None or acc_fit <= acc_now:
            print(f"\nNIET weggeschreven: fit verbetert niet t.o.v. huidige seed "
                  f"({acc_fit:.0%} ≤ {acc_now:.0%}).")
            return
        out = {
            'SURF_THRESHOLDS': {'longboard': lb, 'surfable': sb},
            '_meta': {
                'fitted_at': datetime.now().isoformat(),
                'n_pairs': len(pairs),
                'agreement_before': round(acc_now, 3),
                'agreement_after': round(acc_fit, 3),
                'model_loo_agreement': model_acc,
            },
        }
        LEARNED_PATH.write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(f"\nGeschreven naar {LEARNED_PATH} — config.py laadt dit over de seed.")
    else:
        print("\n(Dry-run; gebruik --write om learned_params.json te updaten.)")


if __name__ == '__main__':
    main()
