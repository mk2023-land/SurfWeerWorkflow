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
import contextlib
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Forceer UTF-8 stdout/stderr — op Windows is de console-codepage vaak cp1252,
# wat crasht op de →/⚠-tekens hieronder (UnicodeEncodeError). PYTHONUTF8=1
# lost dit ook op maar geldt pas voor NIEUWE shells na het zetten; deze
# reconfigure werkt direct, ongeacht codepage of hoe het script aangeroepen is
# (ook als subprocess vanuit ingest_reference_message.py).
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError):
        _stream.reconfigure(encoding='utf-8')

# Script leeft in <repo>/scripts/ — zorg dat src/ importeerbaar is voor de
# component-fit (her-scoort de golf-keten met de ÉCHTE scoring-helpers, geen
# her-implementatie → geen drift met de live scoring).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Laad .env VOORDAT PAIRS_PATH hieronder wordt bepaald — zelfde patroon als
# src/config.py en scripts/ingest_reference_message.py. Zonder dit moet
# REF_PAIRS_PATH elke keer handmatig gezet worden op machines waar het privé-
# archief niet naast de hoofdrepo staat.
load_dotenv()

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

# Eigen surf-sessie-feedback (scripts/ingest_self_feedback.py) — zelfde privé-
# archiefrepo, ander bestand/schema dan de Tobias-parenpijplijn. Default is
# simpelweg "naast ref_pairs.jsonl" — dat pad is al REF_PAIRS_PATH/.env-aware,
# dus dit volgt automatisch mee op elke machine (geen eigen, apart te vergeten
# env-var-override nodig). SELF_FEEDBACK_PATH blijft beschikbaar voor het
# zeldzame geval dat het toch ergens anders moet staan.
SELF_FEEDBACK_PATH = Path(os.getenv('SELF_FEEDBACK_PATH', PAIRS_PATH.parent / 'self_feedback.jsonl'))


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
                # Wat we ÉCHT verstuurden (board-based digest/alert), naast de
                # snapshot-peak_score-verdict. Voor de leer-loop-diagnostiek.
                'our_sent_verdict': p.get('our_sent_verdict'),
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
    # Begrensd tot fysiek-redelijke drempels (30-70) — voorkomt een overfit-
    # artefact als longboard=10 dat bijna elke dag 'longboard' zou noemen.
    # NB: deze drempel-fit op de scalar score is informatief (report-only) en
    # schrijft NIET naar productie; alleen de component-fit (LOO-gated) schrijft.
    grid = list(range(30, 71, 2))
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


# Grids. Drempels BEGRENSD tot fysiek-redelijke ranges (longboard 30-50,
# surfable 44-66) zodat de fit niet naar een overfit-artefact als longboard=10
# kan vluchten (dat zou bijna elke dag 'longboard' noemen). De fit mag de
# drempels nog wel verlagen om onder-calls te corrigeren, maar binnen rede.
_STRENGTH_GRID = [round(0.1 * i, 2) for i in range(0, 8)]   # 0.0..0.7
_MULT_GRID = [0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.00]     # ≥ seed 0.65
_LB_GRID = list(range(30, 51, 2))
_SB_GRID = list(range(44, 67, 2))


def _component_ctx():
    """Config-afgeleide constanten + helpers voor de component-fit."""
    from src.config import SCORING_WEIGHTS, SURF_THRESHOLDS, WIND_FACE_PENALTY, PARTITION_WEIGHTS
    from src.scoring.context import period_factor
    from src.scoring.hourly import golf_height_curve
    return {
        'golf_max': float(SCORING_WEIGHTS['golf_max']),
        'swell_mult': float(PARTITION_WEIGHTS['swell_multiplier']),
        'wfp_min': float(WIND_FACE_PENALTY['min_factor']),
        'mg_lb': float(SURF_THRESHOLDS['min_golf_longboard']),
        'mg_sb': float(SURF_THRESHOLDS['min_golf_surfable']),
        'seed_strength': float(WIND_FACE_PENALTY['strength']),
        'seed_mult': float(PARTITION_WEIGHTS['wind_sea_multiplier']),
        'curve': golf_height_curve,
        'pf': period_factor,
    }


def _predict_pair(sb_b, strength, mult, lb, sb_thr, ctx):
    """Her-scoor één paar onder kandidaat-params en geef het verdict."""
    from src.data.models import ScoreBreakdown
    golf = _rescore_golf(sb_b, strength, mult, ctx['golf_max'], ctx['swell_mult'],
                         ctx['wfp_min'], ctx['curve'], ctx['pf'])
    bd = ScoreBreakdown(
        timestamp=datetime(2000, 1, 1), golf_score=golf,
        wind_score=sb_b.get('wind_score') or 0.0,
        tide_score=sb_b.get('tide_score') or 0.0,
        swell_dir_bonus=sb_b.get('swell_dir_bonus') or 0.0,
        confidence=sb_b.get('confidence') or 1.0,
    )
    total = bd.total_score
    if total >= sb_thr and golf >= ctx['mg_sb']:
        return 'surfable'
    if total >= lb and golf >= ctx['mg_lb']:
        return 'longboard'
    return 'flat'


def _best_component_params(train: list[dict], ctx: dict):
    """Grid-search op `train` die de ordinale fout minimaliseert (tie-break:
    meer exacte matches, dan minste afwijking van de seed). Returnt params-dict."""
    best = None
    for strength in _STRENGTH_GRID:
        for mult in _MULT_GRID:
            rescored = []
            for p in train:
                sb_b = p['features']['score_basis']
                from src.data.models import ScoreBreakdown
                golf = _rescore_golf(sb_b, strength, mult, ctx['golf_max'],
                                     ctx['swell_mult'], ctx['wfp_min'], ctx['curve'], ctx['pf'])
                bd = ScoreBreakdown(
                    timestamp=datetime(2000, 1, 1), golf_score=golf,
                    wind_score=sb_b.get('wind_score') or 0.0,
                    tide_score=sb_b.get('tide_score') or 0.0,
                    swell_dir_bonus=sb_b.get('swell_dir_bonus') or 0.0,
                    confidence=sb_b.get('confidence') or 1.0,
                )
                rescored.append((bd.total_score, golf, p['ref']))
            for lb in _LB_GRID:
                for sb_thr in _SB_GRID:
                    if sb_thr <= lb:
                        continue
                    cost = exact = 0
                    for total, golf, ref in rescored:
                        if total >= sb_thr and golf >= ctx['mg_sb']:
                            pred = 'surfable'
                        elif total >= lb and golf >= ctx['mg_lb']:
                            pred = 'longboard'
                        else:
                            pred = 'flat'
                        cost += _ordinal_cost(pred, ref)
                        exact += (pred == ref)
                    reg = abs(strength - ctx['seed_strength']) + abs(mult - ctx['seed_mult'])
                    key = (cost, -exact, reg)
                    if best is None or key < best[0]:
                        best = (key, {'wind_face_strength': strength,
                                      'wind_sea_multiplier': mult,
                                      'longboard': float(lb), 'surfable': float(sb_thr)},
                                exact / len(train))
    return best


def fit_components(pairs: list[dict]):
    """Beste component-params op ALLE score_basis-paren (in-sample). None bij <6."""
    usable = [p for p in pairs if (p.get('features') or {}).get('score_basis')]
    if len(usable) < 6:
        return None
    best = _best_component_params(usable, _component_ctx())
    if best is None:
        return None
    return {**best[1], 'agreement': best[2], 'n': len(usable),
            'ordinal_cost': best[0][0]}


def component_loo(pairs: list[dict]):
    """Leave-one-out overeenkomst van de component-fit: fit telkens op N-1 paren,
    voorspel het weggelaten paar. Dit is het EERLIJKE generalisatie-cijfer (de
    in-sample agreement is op klein N overfit). None bij <6 score_basis-paren."""
    usable = [p for p in pairs if (p.get('features') or {}).get('score_basis')]
    if len(usable) < 6:
        return None
    ctx = _component_ctx()
    correct = 0
    for i in range(len(usable)):
        train = usable[:i] + usable[i + 1:]
        best = _best_component_params(train, ctx)
        if best is None:
            continue
        pr = best[1]
        pred = _predict_pair(usable[i]['features']['score_basis'],
                             pr['wind_face_strength'], pr['wind_sea_multiplier'],
                             pr['longboard'], pr['surfable'], ctx)
        correct += (pred == usable[i]['ref'])
    return correct / len(usable)


# ---------------------------------------------------------------------------
# REGIME-SPLIT component-fit — DIAGNOSTISCH, schrijft NOOIT naar learned_params.
#
# Hypothese uit PATTERNS.md (augustus-batch): de twee bestaande knoppen
# (wind_face_strength, wind_sea_multiplier) proberen zowel "kleine aflandige
# golfjes worden te vaak gepromoveerd" als "aanlandige messy-golven worden te
# vaak gepromoveerd" met ÉÉN globale waarde op te lossen — twee tegengestelde
# problemen die dezelfde knop nooit tegelijk goed kan zetten. Hier fitten we
# de knoppen APART voor aflandige/glasachtige uren (`groom>0`, al aanwezig in
# elke score_basis — geen nieuwe features nodig) vs aanlandige/windstille uren
# (`groom==0`), met gedeelde longboard/surfable-drempels.
#
# Alleen aan te roepen met `--regime-diagnostic` (traag: volledige LOO doet
# een aparte grid-search per weggelaten paar). Rapporteert alleen; de
# write-gate in main() kijkt hier niet naar. Wordt dit ooit een winnende
# aanpak (LOO > de bestaande single-fit LOO, op genoeg paren per regime),
# dan is de vervolgstap het WEL wiren naar learned_params.json + config.py +
# de live scoring (wave_modifiers.py) — een aparte, grotere stap die zijn
# eigen review verdient.
# ---------------------------------------------------------------------------
def _regime_of(sb: dict) -> str:
    return 'offshore' if (sb.get('groom') or 0.0) > 0.0 else 'onshore'


def _precompute_candidates(usable: list[dict], ctx: dict) -> list[dict]:
    """Per paar: golf/total-score voor ALLE (strength, mult) combinaties in de
    grids, één keer vooraf berekend. Regime-split en single-fit kunnen dit
    beide hergebruiken i.p.v. los te herberekenen per (lb, sb_thr)-combinatie."""
    from src.data.models import ScoreBreakdown
    out = []
    for p in usable:
        sb_b = p['features']['score_basis']
        cand = {}
        for strength in _STRENGTH_GRID:
            for mult in _MULT_GRID:
                golf = _rescore_golf(sb_b, strength, mult, ctx['golf_max'],
                                     ctx['swell_mult'], ctx['wfp_min'], ctx['curve'], ctx['pf'])
                bd = ScoreBreakdown(
                    timestamp=datetime(2000, 1, 1), golf_score=golf,
                    wind_score=sb_b.get('wind_score') or 0.0,
                    tide_score=sb_b.get('tide_score') or 0.0,
                    swell_dir_bonus=sb_b.get('swell_dir_bonus') or 0.0,
                    confidence=sb_b.get('confidence') or 1.0,
                )
                cand[(strength, mult)] = (bd.total_score, golf)
        out.append({'cand': cand, 'ref': p['ref'], 'regime': _regime_of(sb_b)})
    return out


def _best_regime_params(train: list[dict], ctx: dict):
    """Grid-search met APARTE (strength, mult) per regime, gedeelde drempels.
    Voor een vaste (lb, sb_thr) ontkoppelt de kosten-som per paar over regimes,
    dus per regime onafhankelijk optimaliseren is exact (geen approximatie)."""
    precomp = _precompute_candidates(train, ctx)
    best = None
    for lb in _LB_GRID:
        for sb_thr in _SB_GRID:
            if sb_thr <= lb:
                continue
            per_regime = {}
            for regime in ('offshore', 'onshore'):
                bucket = [r for r in precomp if r['regime'] == regime]
                if not bucket:
                    continue
                best_pm = None
                for strength in _STRENGTH_GRID:
                    for mult in _MULT_GRID:
                        cost = exact = 0
                        for r in bucket:
                            total, golf = r['cand'][(strength, mult)]
                            if total >= sb_thr and golf >= ctx['mg_sb']:
                                pred = 'surfable'
                            elif total >= lb and golf >= ctx['mg_lb']:
                                pred = 'longboard'
                            else:
                                pred = 'flat'
                            cost += _ordinal_cost(pred, r['ref'])
                            exact += (pred == r['ref'])
                        if best_pm is None or cost < best_pm[0]:
                            best_pm = (cost, exact, strength, mult)
                per_regime[regime] = best_pm
            if not per_regime:
                continue
            total_cost = sum(v[0] for v in per_regime.values())
            total_exact = sum(v[1] for v in per_regime.values())
            key = (total_cost, -total_exact)
            if best is None or key < best[0]:
                best = (key, {
                    'longboard': float(lb), 'surfable': float(sb_thr),
                    'params_by_regime': {
                        r: {'wind_face_strength': v[2], 'wind_sea_multiplier': v[3]}
                        for r, v in per_regime.items()
                    },
                }, total_exact / len(train))
    return best


def _predict_pair_regime(sb: dict, params: dict, lb: float, sb_thr: float, ctx: dict) -> str:
    regime = _regime_of(sb)
    pr = params.get(regime) or params.get('offshore') or params.get('onshore')
    golf = _rescore_golf(sb, pr['wind_face_strength'], pr['wind_sea_multiplier'],
                         ctx['golf_max'], ctx['swell_mult'], ctx['wfp_min'],
                         ctx['curve'], ctx['pf'])
    from src.data.models import ScoreBreakdown
    bd = ScoreBreakdown(
        timestamp=datetime(2000, 1, 1), golf_score=golf,
        wind_score=sb.get('wind_score') or 0.0,
        tide_score=sb.get('tide_score') or 0.0,
        swell_dir_bonus=sb.get('swell_dir_bonus') or 0.0,
        confidence=sb.get('confidence') or 1.0,
    )
    total = bd.total_score
    if total >= sb_thr and golf >= ctx['mg_sb']:
        return 'surfable'
    if total >= lb and golf >= ctx['mg_lb']:
        return 'longboard'
    return 'flat'


def regime_fit_and_loo(pairs: list[dict]):
    """Diagnostisch: in-sample fit + leave-one-out van de regime-split.
    Print bucket-groottes zodat duidelijk is of een regime al genoeg paren heeft
    (zelfde N≥5-achtige discipline als de rest van de leer-loop). None bij <6."""
    usable = [p for p in pairs if (p.get('features') or {}).get('score_basis')]
    if len(usable) < 6:
        return None
    ctx = _component_ctx()
    n_offshore = sum(1 for p in usable if _regime_of(p['features']['score_basis']) == 'offshore')
    n_onshore = len(usable) - n_offshore
    best = _best_regime_params(usable, ctx)
    if best is None:
        return None
    _, params, in_sample = best
    correct = 0
    for i in range(len(usable)):
        train = usable[:i] + usable[i + 1:]
        b2 = _best_regime_params(train, ctx)
        if b2 is None:
            continue
        pr = b2[1]
        pred = _predict_pair_regime(
            usable[i]['features']['score_basis'], pr['params_by_regime'],
            pr['longboard'], pr['surfable'], ctx,
        )
        correct += (pred == usable[i]['ref'])
    loo = correct / len(usable)
    return {
        'n_offshore': n_offshore, 'n_onshore': n_onshore,
        'in_sample': in_sample, 'loo': loo, 'params': params,
    }


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
# EIGEN SURF-FEEDBACK — DIAGNOSTISCH, schrijft NOOIT naar learned_params.
#
# Ander signaal dan de Tobias-parenpijplijn hierboven: gepaird met
# scripts/ingest_self_feedback.py, per surf-sessie een 1-10 tevredenheidsscore
# per onderdeel (tijd, hoogte, wind, duur, richting, stroming) van de gebruiker
# zelf, gejoind met ONZE score_basis van dat moment. Vier onderdelen mappen
# direct op een bestaand score-component (hoogte→golf_score, wind→wind_score,
# richting→swell_dir_bonus, stroming→tide_score); tijd/duur gaan over
# venster-detectie, niet een losse formule, dus die worden alleen geteld.
#
# Puur rapporterend — net als --regime-diagnostic hierboven. Bij kleine N is
# een Pearson-r vrijwel ruis; die wordt pas getoond vanaf N>=4 en expliciet als
# "indicatief" gemerkt tot N>=8.
# ---------------------------------------------------------------------------
_FEEDBACK_DIMENSION_MAP = {
    'hoogte': 'golf_score',
    'wind': 'wind_score',
    'richting': 'swell_dir_bonus',
    'stroming': 'tide_score',
}


def load_self_feedback() -> list[dict]:
    if not SELF_FEEDBACK_PATH.exists():
        return []
    out = []
    for line in SELF_FEEDBACK_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _pearson_r(xs: list[float], ys: list[float]):
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def self_feedback_report(records: list[dict]) -> None:
    """Print een leesbaar rapport — geen return-waarde, geen schrijf-pad."""
    if not records:
        print("Nog geen eigen sessie-feedback (scripts/ingest_self_feedback.py).")
        return

    by_quality: dict[str, int] = {}
    for r in records:
        by_quality[r.get('match_quality', '?')] = by_quality.get(r.get('match_quality', '?'), 0) + 1
    print(f"Sessies totaal: {len(records)}  "
          f"(exact={by_quality.get('exact', 0)}, "
          f"nearest={by_quality.get('nearest', 0)}, "
          f"none={by_quality.get('none', 0)})")

    usable = [r for r in records if r.get('our_score_basis')]
    if not usable:
        print("Geen enkele sessie heeft een gekoppelde score_basis (allemaal "
              "match_quality='none') — nog niks te correleren.")
        return

    for dim, comp in _FEEDBACK_DIMENSION_MAP.items():
        pairs = [
            (r['user_scores'][dim], r['our_score_basis'].get(comp))
            for r in usable
            if dim in (r.get('user_scores') or {}) and r['our_score_basis'].get(comp) is not None
        ]
        if not pairs:
            print(f"\n{dim} ↔ {comp}: nog geen paren.")
            continue
        xs, ys = zip(*pairs, strict=True)
        n = len(pairs)
        print(f"\n{dim} ↔ {comp} (N={n}):")
        for x, y in pairs:
            print(f"    gebruiker={x}  ons={y:.1f}")
        r = _pearson_r(list(xs), list(ys))
        if n < 4 or r is None:
            print(f"  Pearson r: n.v.t. (N={n} < 4, te weinig voor een zinnig getal)")
        else:
            flag = "  (indicatief, klein N)" if n < 8 else ""
            print(f"  Pearson r = {r:.2f}{flag}")

    for dim in ('tijd', 'duur'):
        vals = [r['user_scores'][dim] for r in usable if dim in (r.get('user_scores') or {})]
        if vals:
            print(f"\n{dim} (geen 1-op-1 score-component, alleen geteld): "
                  f"N={len(vals)}, gemiddeld={sum(vals) / len(vals):.1f}")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description='Fit scoring-params op referentie-data')
    ap.add_argument('--write', action='store_true', help='Schrijf learned_params.json')
    ap.add_argument('--min-pairs', type=int, default=12,
                    help='Minimum aantal gepairde dagen voordat we params wegschrijven')
    ap.add_argument('--min-basis', type=int, default=12,
                    help='Minimum aantal score_basis-paren voor de component-write '
                         '(de fysica-knoppen). Aparte, hogere lat dan de drempel-fit.')
    ap.add_argument('--regime-diagnostic', action='store_true',
                    help='Print een extra, TRAGE diagnose: aparte wind_face_strength/'
                         'wind_sea_multiplier voor aflandige vs aanlandige uren '
                         '(zie PATTERNS.md augustus-batch). Schrijft nooit naar '
                         'learned_params.json — alleen ter vergelijking met de '
                         'single-fit component-LOO hierboven. Niet gebruiken in de '
                         'auto-calibratie na elke ingest (te traag voor routine-gebruik).')
    ap.add_argument('--self-feedback', action='store_true',
                    help='Print een rapport over de eigen surf-sessie-feedback '
                         '(scripts/ingest_self_feedback.py): per onderdeel de '
                         'gebruikersscore naast het bijbehorende score-component. '
                         'Puur diagnostisch, schrijft nooit naar learned_params.json.')
    args = ap.parse_args()

    if args.self_feedback:
        # Standalone rapport — onafhankelijk van de Tobias-parenpijplijn
        # hieronder (ander bestand, andere join-sleutel). Print en stop.
        print("=" * 60)
        print("EIGEN SURF-FEEDBACK — rapport")
        print("=" * 60)
        self_feedback_report(load_self_feedback())
        return

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
    print(f"\nHUIDIGE overeenkomst (snapshot-verdict): {acc_now:.0%} ({len(pairs)} dagen)")
    print_confusion(cm_now, "Snapshot (peak_score-drempel — wat dit script fit)")

    # Verstuurd-verdict diagnostiek: het board-based verdict dat de gebruiker ECHT
    # kreeg (uit de digest/alert). De component-fit hieronder kan alleen de
    # peak_score-keten tunen, dus fit tegen de SNAPSHOT; maar we RAPPORTEREN het
    # verstuurde signaal zodat de leer-loop ziet dat de twee engines tegengesteld
    # falen (snapshot te laag op windsee, verstuurd te hoog op klein-fish).
    sent_pairs = [p for p in pairs if p.get('our_sent_verdict') in VERDICTS]
    if sent_pairs:
        cm_sent, acc_sent = confusion(sent_pairs, 'our_sent_verdict')
        print(f"\nVERSTUURD-verdict overeenkomst (wat de gebruiker kreeg): "
              f"{acc_sent:.0%} ({len(sent_pairs)} dagen)")
        print_confusion(cm_sent, "Verstuurd (board-based)")
        div = [p for p in sent_pairs if p['our_verdict'] != p['our_sent_verdict']]
        if div:
            snap_better = sum(1 for p in div if p['our_verdict'] == p['ref'])
            sent_better = sum(1 for p in div if p['our_sent_verdict'] == p['ref'])
            print(f"  Snapshot != verstuurd op {len(div)}/{len(sent_pairs)} dagen "
                  f"(snapshot beter: {snap_better}, verstuurd beter: {sent_better}, "
                  f"beide fout: {len(div) - snap_better - sent_better}).")
            print("  → Tegengestelde bias. Eén engine canoniek maken is een "
                  "PRODUCT-keuze (pessimistischer vs optimistischer), geen calibratie.")

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

    # Leave-one-out van de component-fit = het EERLIJKE generalisatie-cijfer.
    # De in-sample agreement is op klein N overfit (memoriseert de paren). De
    # schrijf-gate hieronder gebruikt LOO, niet in-sample.
    comp_loo = component_loo(pairs)
    if comp and comp_loo is not None:
        flag = "  ⚠ overfit-gat" if (comp['agreement'] - comp_loo) >= 0.2 else ""
        print(f"  → component LEAVE-ONE-OUT (generalisatie): {comp_loo:.0%}"
              f"  [in-sample {comp['agreement']:.0%}{flag}]")

    if args.regime_diagnostic:
        print("\n" + "-" * 60)
        print("REGIME-SPLIT DIAGNOSE (aflandig vs aanlandig, --regime-diagnostic)")
        print("-" * 60)
        rd = regime_fit_and_loo(pairs)
        if rd is None:
            print("Te weinig score_basis-paren (min 6).")
        else:
            print(f"Bucket-groottes: offshore(groom>0)={rd['n_offshore']}, "
                  f"onshore/windstil(groom=0)={rd['n_onshore']}")
            print(f"  gedeelde drempels: longboard>={rd['params']['longboard']:.0f}, "
                  f"surfable>={rd['params']['surfable']:.0f}")
            for regime, pr in rd['params']['params_by_regime'].items():
                print(f"  {regime}: wind_face_strength={pr['wind_face_strength']}, "
                      f"wind_sea_multiplier={pr['wind_sea_multiplier']}")
            print(f"In-sample: {rd['in_sample']:.0%}  |  LEAVE-ONE-OUT: {rd['loo']:.0%}")
            if comp_loo is not None:
                verdict = ("WINT van single-fit" if rd['loo'] > comp_loo
                          else "verslechtert t.o.v. single-fit" if rd['loo'] < comp_loo
                          else "gelijk aan single-fit")
                print(f"  → vs. single-fit LOO ({comp_loo:.0%}): {verdict}. "
                      f"Alleen bij een duidelijke, stabiele winst + genoeg paren per "
                      f"regime is dit de moeite van het wiren naar learned_params.json "
                      f"+ config.py + wave_modifiers.py waard — nu puur diagnostisch.")

    if not args.write:
        print("\n(Dry-run; gebruik --write om learned_params.json te updaten.)")
        return

    if len(pairs) < args.min_pairs:
        print(f"\nNIET weggeschreven: {len(pairs)} < min-pairs {args.min_pairs}. "
              f"Te weinig data → seed blijft (geen overfit).")
        return

    # Schrijf-gate. Component-fit wint ALLEEN als hij op LEAVE-ONE-OUT
    # generaliseert (niet de in-sample 100%) ÉN er genoeg score_basis-paren zijn
    # (--min-basis). Zo kan een overfit-fit NOOIT naar productie. Anders de
    # drempel-only fit, mits die boven de seed uitkomt.
    thr_acc = acc_fit if (lb is not None) else -1.0
    if (comp and comp_loo is not None and n_basis >= args.min_basis
            and comp_loo > acc_now):
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
                'agreement_loo': round(comp_loo, 3),
                'agreement_in_sample': round(comp['agreement'], 3),
                'model_loo_agreement': model_acc,
            },
        }
    else:
        # GEEN drempel-fit-write: die schuift alleen knip-punten op een reeds-
        # vervuilde scalar (cosmetisch, audit-bevinding) → report-only. Alleen de
        # component-fit (her-scoort de fysica, LOO-gated) mag naar productie.
        cl = f"{comp_loo:.0%}" if comp_loo is not None else "n.v.t."
        print(f"\nNIET weggeschreven: de component-fit generaliseert nog niet "
              f"genoeg op leave-one-out.")
        print(f"  seed {acc_now:.0%} | component-LOO {cl} | nodig: ≥{args.min_basis} "
              f"score_basis-paren (nu {n_basis}) ÉN LOO > seed.")
        print(f"  (drempel-fit {thr_acc:.0%} is report-only — schrijft niet.)")
        return
    LEARNED_PATH.write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(f"\nGeschreven naar {LEARNED_PATH} ({out['_meta']['fit_kind']}-fit) "
          f"— config.py laadt dit over de seed.")


if __name__ == '__main__':
    main()
