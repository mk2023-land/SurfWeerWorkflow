"""
Eigen surf-sessie-feedback ingestie.

Losstaand van de Tobias-referentie-pijplijn (ingest_reference_message.py) — ander
schema, ander doel. Hier gaat het om de EIGEN ervaring van de gebruiker na een
surf-sessie, niet om een externe forecaster:

- Gebruiker geeft per sessie een 1-10 tevredenheidsscore per onderdeel (tijd,
  hoogte, wind, duur, richting, stroming, optioneel een overall-score) + vrije
  toelichting. Geen ruwe metingen nodig — die halen we zelf op.
- We joinen dat met ONZE eigen voorspelling van dat moment uit
  `data/forecast_features.jsonl` (dezelfde bron als de Tobias-parenpijplijn).
- Vergelijkingsbasis is bewust ONZE VOORSPELLING (niet achteraf gemeten boei-
  data): Open-Meteo's archive-API heeft ~5 dagen vertraging en RWS-boei-historie
  is niet zomaar voor een sessie van dagen terug op te vragen — onze eigen
  forecast_features-snapshot is direct beschikbaar en is precies wat de
  gebruiker op dat moment kon zien/verwachten.

BEPERKING (Fase 1, bewust): `forecast_features.jsonl` logt per forecast-dag
alleen het PIEK-SCORE-UUR, niet elk uur (zie `main.py::_log_forecast_features`).
Valt een sessie niet op dat piek-uur, dan is er geen exacte match — dit script
meldt dat eerlijk via `match_quality` i.p.v. te doen alsof het wel klopt. Fase 2
(losstaand, nog niet gebouwd) zou main.py per daglicht-uur laten loggen zodat
elke sessie exact te matchen is.

Gebruik:
    python scripts/ingest_self_feedback.py --date 2026-08-29 --spot noordwijk \
        --start 14:00 --end 16:00 \
        --scores-json '{"tijd":7,"hoogte":5,"wind":6,"duur":8,"richting":4,"stroming":6,"overall":6}' \
        --note "wind draaide halverwege bij, werd rommeliger"
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

# Zelfde UTF-8-fix als ingest_reference_message.py / calibrate.py (Windows-console
# cp1252 crasht anders op de ↪-tekens hieronder).
import contextlib
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError):
        _stream.reconfigure(encoding='utf-8')

load_dotenv()

# Scripts leven in dezelfde map — hergebruik de bestaande archief-paden en de
# commit/push-helper i.p.v. dupliceren. Werkt zonder extra sys.path-gedoe omdat
# Python de map van het aangeroepen script automatisch aan sys.path toevoegt.
from ingest_reference_message import (  # noqa: E402
    FEATURES_PATH,
    PAIRS_PATH,
    _sync_private_archive,
)

_SCORE_KEYS = ['tijd', 'hoogte', 'wind', 'duur', 'richting', 'stroming']
_OPTIONAL_SCORE_KEYS = ['overall']

# Zelfde privé-archiefrepo/map als de Tobias-parenpijplijn (naast ref_pairs.jsonl),
# apart bestand (ander schema). Volgt PAIRS_PATH mee — die is al REF_PAIRS_PATH/
# .env-aware, dus geen eigen env-var-override nodig om te vergeten.
FEEDBACK_PATH = PAIRS_PATH.parent / 'self_feedback.jsonl'


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)


def _load_all_snapshots(spot: str, forecast_date: str) -> list[dict]:
    """ALLE runs (niet één) die op `forecast_date`/`spot` een snapshot hebben
    gelogd. Meerdere runs per dag zijn de norm (8×/dag productie-cron) en hun
    piek-uur kan per run verschillen (forecast wijzigt gedurende de dag) — voor
    een sessie op een specifiek klokuur willen we ALLE kandidaten kunnen
    doorzoeken, niet blind de eerste/oudste run van de dag pakken."""
    if not FEATURES_PATH.exists():
        return []
    cands = []
    for line in FEATURES_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get('spot') == spot and r.get('forecast_date') == forecast_date:
            cands.append(r)
    return cands


def _match_snapshot(spot: str, forecast_date: str, start: str, end: str):
    """Kies de beste snapshot voor een sessie [start,end]:
    1) 'exact' — de MEEST RECENTE run waarvan het gelogde piek-uur binnen het
       sessievenster valt (dat is letterlijk "onze voorspelling van dat moment"
       voor dat uur);
    2) 'nearest' — anders de MEEST RECENTE run van de dag (verst gevorderde,
       dus meest actuele info), met het tijdsverschil gemeld;
    3) 'none' — geen enkele run gevonden.

    Returnt (snapshot_dict_of_None, match_quality, toelichting_of_None).
    """
    cands = _load_all_snapshots(spot, forecast_date)
    if not cands:
        return None, 'none', None

    cands.sort(key=lambda r: r.get('run_timestamp', ''))  # oud → nieuw

    in_window = [
        r for r in cands
        if r.get('peak_hour') and _minutes(start) <= _minutes(r['peak_hour']) <= _minutes(end)
    ]
    if in_window:
        return in_window[-1], 'exact', None  # laatste (= meest actuele) match

    latest = cands[-1]
    peak_hour = latest.get('peak_hour')
    if not peak_hour:
        return latest, 'nearest', 'snapshot zonder peak_hour-veld'
    diff_min = min(
        abs(_minutes(peak_hour) - _minutes(start)),
        abs(_minutes(peak_hour) - _minutes(end)),
    )
    return latest, 'nearest', f'piek-uur {peak_hour} (meest recente run) ligt {diff_min} min buiten [{start}-{end}]'


def _validate_scores(scores: dict) -> list[str]:
    """Zachte validatie — waarschuwt, blokkeert niet (consistent met de losse
    parse-heuristiek elders in dit project)."""
    issues = []
    missing = [k for k in _SCORE_KEYS if k not in scores]
    if missing:
        issues.append(f"ontbrekende score(s): {', '.join(missing)}")
    for k, v in scores.items():
        if k not in _SCORE_KEYS + _OPTIONAL_SCORE_KEYS:
            issues.append(f"onbekende score-key genegeerd: {k!r}")
            continue
        if not isinstance(v, (int, float)) or not (1 <= v <= 10):
            issues.append(f"{k}={v!r} buiten verwacht bereik 1-10")
    return issues


def write_feedback(record: dict) -> None:
    """Upsert (op date+session_start+spot) naar self_feedback.jsonl — zelfde
    idempotente aanpak als write_training_pairs in ingest_reference_message.py."""
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[tuple, dict] = {}
    if FEEDBACK_PATH.exists():
        for line in FEEDBACK_PATH.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                key = (r['date'], r['session_start'], r.get('spot', 'noordwijk'))
                existing[key] = r
            except (json.JSONDecodeError, KeyError):
                continue
    key = (record['date'], record['session_start'], record['spot'])
    existing[key] = record
    with FEEDBACK_PATH.open('w', encoding='utf-8') as f:
        for k in sorted(existing):
            f.write(json.dumps(existing[k], ensure_ascii=False) + '\n')


def main() -> int:
    ap = argparse.ArgumentParser(description='Ingest eigen surf-sessie-feedback')
    ap.add_argument('--date', required=True, help='Datum van de sessie (YYYY-MM-DD)')
    ap.add_argument('--spot', default='noordwijk')
    ap.add_argument('--start', required=True, help='Sessie-start (HH:MM)')
    ap.add_argument('--end', required=True, help='Sessie-eind (HH:MM)')
    ap.add_argument(
        '--scores-json', required=True,
        help='JSON dict met 1-10 scores: tijd, hoogte, wind, duur, richting, '
             'stroming (+ optioneel overall). Bv. '
             '\'{"tijd":7,"hoogte":5,"wind":6,"duur":8,"richting":4,"stroming":6}\'',
    )
    ap.add_argument('--note', default=None, help='Vrije toelichting, optioneel')
    args = ap.parse_args()

    try:
        datetime.strptime(args.date, '%Y-%m-%d')
    except ValueError:
        print(f"✗ Ongeldige --date {args.date!r}, verwacht YYYY-MM-DD", file=sys.stderr)
        return 1
    try:
        _minutes(args.start)
        _minutes(args.end)
    except (ValueError, IndexError):
        print("✗ --start/--end verwacht HH:MM", file=sys.stderr)
        return 1

    try:
        scores = json.loads(args.scores_json)
    except json.JSONDecodeError as e:
        print(f"✗ --scores-json is geen geldige JSON: {e}", file=sys.stderr)
        return 1

    issues = _validate_scores(scores)
    for issue in issues:
        print(f"⚠ {issue}", file=sys.stderr)

    snapshot, quality, note_quality = _match_snapshot(args.spot, args.date, args.start, args.end)

    record = {
        'date': args.date,
        'spot': args.spot,
        'session_start': args.start,
        'session_end': args.end,
        'user_scores': {k: scores[k] for k in _SCORE_KEYS + _OPTIONAL_SCORE_KEYS if k in scores},
        'note': args.note,
        'match_quality': quality,
        'match_note': note_quality,
        'matched_hour': (snapshot or {}).get('peak_hour'),
        'our_verdict': (snapshot or {}).get('our_verdict'),
        'our_peak_score': (snapshot or {}).get('our_peak_score'),
        'our_features': {
            k: (snapshot or {}).get(k)
            for k in ('hs_m', 'tp_s', 'wind_speed_kn', 'wind_dir_deg',
                     'offshore_cos', 'tide_level_norm', 'tide_phase')
        } if snapshot else None,
        'our_score_basis': (snapshot or {}).get('score_basis'),
    }

    write_feedback(record)

    print(f"✓ Opgeslagen: {FEEDBACK_PATH}")
    print(f"  Datum:          {args.date} {args.start}-{args.end}u ({args.spot})")
    print(f"  Match-kwaliteit: {quality}" + (f" ({note_quality})" if note_quality else ""))
    if snapshot:
        print(f"  Onze voorspelling toen: {snapshot.get('our_verdict')} "
              f"(piek {snapshot.get('peak_hour')}, score {snapshot.get('our_peak_score')})")
    else:
        print("  Geen eigen snapshot gevonden voor deze dag/spot — alleen de "
              "score is opgeslagen, koppel later na als data alsnog binnenkomt.")
    print(f"  Scores: {record['user_scores']}")
    if args.note:
        print(f"  Toelichting: {args.note}")

    _sync_private_archive(
        args.date, commit_message=f'Eigen surf-feedback {args.date} {args.start}-{args.end}',
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
