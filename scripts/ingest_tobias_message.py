"""
Forecaster-referentiebericht ingestie voor Sprint 4 training-labels.

Sla originele SMS-tekst op met datum-stempel zodat we later (Sprint 4
XGBoost / fine-tuning) kunnen vergelijken met onze eigen voorspelling op
dezelfde dag. Een 6-maands archief geeft genoeg data voor supervised
learning op surfability-categorisering.

Gebruik (interactief):
    python scripts/ingest_reference_message.py
    [plak SMS, Ctrl+D / Cmd+D om af te sluiten]

Gebruik (CLI argument):
    python scripts/ingest_reference_message.py --date 2026-05-20 --text "..."

Gebruik (via stdin pipe):
    cat msg_today.txt | python scripts/ingest_reference_message.py --date 2026-05-20

Bestand-layout (in de private archive-repo ~/Merlijn/referentie-forecaster):
    data/ref_archive/
        2026-05-19.txt        ← raw SMS-tekst
        2026-05-19.meta.json  ← geparste metadata (datum, spots, verdict)
        ...
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

# referentie-forecaster-referentie-archief leeft sinds 2026-05-22 in een aparte private repo
# (auteursrechtelijk materiaal): ~/Merlijn/referentie-forecaster. Default verwacht die naast
# de hoofdrepo; override met de REF_ARCHIVE_DIR env-var voor een andere locatie.
_default_archive = (
    Path(__file__).resolve().parent.parent.parent
    / 'referentie-forecaster' / 'data' / 'ref_archive'
)
ARCHIVE_DIR = Path(os.environ.get('REF_ARCHIVE_DIR', _default_archive))

# Bekende spot-namen die referentie-forecaster gebruikt — voor extractie van per-spot windows
SPOT_PATTERNS = {
    'Noordwijk':    [r'\bNwijk\b', r'\bNoordwijk\b'],
    'Zandvoort':    [r'\bZvoort\b', r'\bZandvoort\b'],
    'Scheveningen': [r'\bSchev\b', r'\bScheveningen\b'],
    'Wijk aan Zee': [r'\bWijk(?:\s+aan\s+Zee)?\b'],
    'IJmuiden':     [r'\bIJmuiden\b'],
    'Maasvlakte':   [r'\bMvlakte\b', r'\bMaasvlakte\b'],
    'Hoek van Holland': [r'\bHvH\b', r'\bHoek\s+van\s+Holland\b'],
    'Domburg':      [r'\bDomburg\b'],
    'Ouddorp':      [r'\bOuddorp\b'],
    'Texel':        [r'\bTexel\b', r'\bTexelKoog\b', r'\bPaal\s*\d+\b'],
    'Egmond':       [r'\bEgmond\b'],
    'Petten':       [r'\bPetten\b'],
}

# Verdict-keywords (referentie-forecaster' lexicon, zie research/reference_methodology.md §4.3)
VERDICT_KEYWORDS = {
    'flat':       [r'\bflat\b', r'\brimpelsurf\b', r'\bniet\s+aan\s+beginnen\b',
                   r'\bgeen\s+golven\b', r'\bswell\s+nihil\b'],
    'longboard':  [r'\blongboard\b', r'\bvoor\s+long\b', r'\bfish\b',
                   r'\bknietjes\b', r'\bleuke\s+lijntjes\b'],
    'surfable':   [r'\bshortboard\b', r'\bgenoeg\s+hoogte\b',
                   r'\bnet\s+aan\s+shortboard\b'],
    'alert':      [r'\bALERT\b', r'\bgroundswell\b', r'\bgroot\s+alert\b',
                   r'\bswell\s+breekt\s+door\b', r'\bbig\s+day\b'],
}

# Windgegevens uit referentie-forecaster' tekst (bv. "5bft", "tot 4bft", "ZW")
WIND_BFT_PATTERN = re.compile(r'(\d+)\s*bft', re.IGNORECASE)
TIME_RANGE_PATTERN = re.compile(r'(\d{1,2})(?::(\d{2}))?\s*[-–—]\s*(\d{1,2})(?::(\d{2}))?u?')


def parse_metadata(text: str, msg_date: date) -> dict:
    """Extract gestructureerde metadata uit ruwe SMS-tekst."""
    text.lower()

    # Spots genoemd
    spots_mentioned = []
    for spot, patterns in SPOT_PATTERNS.items():
        for p in patterns:
            if re.search(p, text):
                spots_mentioned.append(spot)
                break

    # Verdict per spot (heuristisch — kijkt in context-venster van 200 chars)
    verdicts = {}
    for spot in spots_mentioned:
        for p in SPOT_PATTERNS[spot]:
            m = re.search(p, text)
            if not m:
                continue
            ctx = text[m.start():m.start() + 200].lower()
            spot_verdict = None
            for v, vp_list in VERDICT_KEYWORDS.items():
                if any(re.search(vp, ctx, re.IGNORECASE) for vp in vp_list):
                    spot_verdict = v
                    break
            if spot_verdict:
                verdicts[spot] = spot_verdict
            break

    # Tijd-vensters per spot (HH-HHu, HH:MM-HH:MM)
    windows_per_spot = {}
    for spot in spots_mentioned:
        for p in SPOT_PATTERNS[spot]:
            m = re.search(p, text)
            if not m:
                continue
            ctx = text[m.start():m.start() + 300]
            windows = []
            for tm in TIME_RANGE_PATTERN.finditer(ctx):
                start_h = int(tm.group(1))
                end_h = int(tm.group(3))
                if 0 <= start_h <= 23 and 0 <= end_h <= 23:
                    windows.append(f"{start_h:02d}-{end_h:02d}u")
            if windows:
                windows_per_spot[spot] = windows
            break

    # Wind beaufort (algemeen of per dag)
    bft_mentions = WIND_BFT_PATTERN.findall(text)

    # Algemene verdict (max verdict-strength over hele tekst)
    overall_verdict = 'unknown'
    verdict_strength = {'flat': 1, 'longboard': 2, 'surfable': 3, 'alert': 4}
    if verdicts:
        overall_verdict = max(verdicts.values(),
                              key=lambda v: verdict_strength.get(v, 0))

    return {
        'date': msg_date.isoformat(),
        'ingested_at': datetime.now().isoformat(),
        'char_count': len(text),
        'spots_mentioned': spots_mentioned,
        'verdicts_per_spot': verdicts,
        'windows_per_spot': windows_per_spot,
        'bft_mentions': [int(b) for b in bft_mentions],
        'overall_verdict': overall_verdict,
    }


# Hoofdrepo-paden (script leeft in <repo>/scripts/).
_REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURES_PATH = _REPO_ROOT / 'data' / 'forecast_features.jsonl'
PAIRS_PATH = _REPO_ROOT / 'data' / 'training' / 'referentie-forecaster_pairs.jsonl'

_VALID_VERDICTS = {'flat', 'longboard', 'surfable'}


def _load_our_snapshot(forecast_date: str) -> dict | None:
    """Onze beste feature-snapshot voor Noordwijk op `forecast_date` (de
    nowcast met day_offset==0 indien aanwezig, anders dichtstbij)."""
    if not FEATURES_PATH.exists():
        return None
    cands = []
    for line in FEATURES_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get('spot') == 'noordwijk' and r.get('forecast_date') == forecast_date:
            cands.append(r)
    if not cands:
        return None
    cands.sort(key=lambda r: (abs(r.get('day_offset', 99)), r.get('run_timestamp', '')))
    return cands[0]


def write_training_pairs(noordwijk_days: list[dict]) -> list[dict]:
    """Voor elke gelabelde forecast-dag: join met onze feature-snapshot en
    schrijf een trainingspaar (label + onze features/score) naar
    data/training/referentie-forecaster_pairs.jsonl. Dagen zonder snapshot worden overgeslagen
    (gerapporteerd). Idempotent per (date): vervangt een bestaand paar."""
    PAIRS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if PAIRS_PATH.exists():
        for line in PAIRS_PATH.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                p = json.loads(line)
                existing[p['date']] = p
            except (json.JSONDecodeError, KeyError):
                continue
    made = []
    for day in noordwijk_days:
        d = day.get('date')
        verdict = day.get('verdict')
        if not d or verdict not in _VALID_VERDICTS:
            continue
        snap = _load_our_snapshot(d)
        pair = {
            'date': d,
            'referentie-forecaster_verdict': verdict,
            'referentie-forecaster_windows': day.get('windows') or [],
            'paired': snap is not None,
            'our_verdict': (snap or {}).get('our_verdict'),
            'our_peak_score': (snap or {}).get('our_peak_score'),
            'features': snap,
        }
        existing[d] = pair
        if snap is not None:
            made.append(pair)
    with PAIRS_PATH.open('w', encoding='utf-8') as f:
        for d in sorted(existing):
            f.write(json.dumps(existing[d], ensure_ascii=False) + '\n')
    return made


def main():
    parser = argparse.ArgumentParser(description='Ingest forecaster-referentiebericht (SMS) in privé-archief')
    parser.add_argument(
        '--date', type=str, default=None,
        help='Datum van het bericht (YYYY-MM-DD). Default: vandaag.'
    )
    parser.add_argument(
        '--text', type=str, default=None,
        help='SMS-tekst inline. Anders gelezen van stdin.'
    )
    parser.add_argument(
        '--force', action='store_true',
        help='Overschrijf een bestaand bericht zonder prompt (gecorrigeerde labels).'
    )
    parser.add_argument(
        '--labels-json', type=str, default=None,
        help='JSON met Noordwijk-labels per forecast-dag (door Claude volgens '
             'de vaste rubriek geëxtraheerd): '
             '[{"date":"YYYY-MM-DD","verdict":"flat|longboard|surfable",'
             '"windows":["06-08u"]}]. Maakt direct trainingsparen.'
    )
    args = parser.parse_args()

    # Datum bepalen
    if args.date:
        try:
            msg_date = datetime.strptime(args.date, '%Y-%m-%d').date()
        except ValueError:
            print(f"✗ Ongeldige datum '{args.date}', verwacht YYYY-MM-DD",
                  file=sys.stderr)
            return 1
    else:
        msg_date = date.today()

    # Tekst bepalen
    if args.text:
        text = args.text
    else:
        if sys.stdin.isatty():
            print(f"→ Plak referentie-forecaster' SMS voor {msg_date.isoformat()}, druk Ctrl+D wanneer klaar:")
        text = sys.stdin.read()

    text = text.strip()
    if not text:
        print("✗ Geen tekst — niets opgeslagen.", file=sys.stderr)
        return 1

    # Opslaan
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    txt_path = ARCHIVE_DIR / f"{msg_date.isoformat()}.txt"
    meta_path = ARCHIVE_DIR / f"{msg_date.isoformat()}.meta.json"

    if txt_path.exists() and not args.force:
        if not sys.stdin.isatty():
            print(f"⚠ {txt_path.name} bestaat al — gebruik --force om te "
                  f"overschrijven (of corrigeer interactief).", file=sys.stderr)
            return 1
        print(f"⚠ {txt_path.name} bestaat al — overschrijven? [y/N] ", end='')
        if input().strip().lower() != 'y':
            print("Afgebroken.")
            return 1

    txt_path.write_text(text, encoding='utf-8')

    meta = parse_metadata(text, msg_date)

    # Canonieke Noordwijk-labels (door Claude volgens de vaste rubriek geleverd).
    # Dit is de BETROUWBARE labelbron — de regex-heuristiek hierboven pakt
    # Noordwijk vaak niet uit referentie-forecaster' groeperende proza. Slaat de labels op in
    # de meta én maakt direct trainingsparen met onze feature-snapshots.
    noordwijk_days = []
    made_pairs = []
    if args.labels_json:
        try:
            noordwijk_days = json.loads(args.labels_json)
        except json.JSONDecodeError as e:
            print(f"✗ --labels-json is geen geldige JSON: {e}", file=sys.stderr)
            return 1
        meta['noordwijk_days'] = noordwijk_days
        # Back-compat: zet ook het verdict van de bericht-dag in verdicts_per_spot.
        for day in noordwijk_days:
            if day.get('date') == msg_date.isoformat() and day.get('verdict') in _VALID_VERDICTS:
                meta.setdefault('verdicts_per_spot', {})['Noordwijk'] = day['verdict']
        made_pairs = write_training_pairs(noordwijk_days)

    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                         encoding='utf-8')

    print(f"✓ Opgeslagen: {txt_path}")
    print(f"✓ Metadata:   {meta_path}")
    if args.labels_json:
        n_lbl = len(noordwijk_days)
        n_pair = len(made_pairs)
        print(f"✓ Noordwijk-labels: {n_lbl} dag(en); trainingsparen gemaakt: {n_pair}")
        for day in noordwijk_days:
            d = day.get('date'); v = day.get('verdict')
            paired = any(p['date'] == d for p in made_pairs)
            print(f"    {d}: {v:10s} {'↔ gepaird met onze snapshot' if paired else '(nog geen snapshot → alleen label)'}")
    print()
    print(f"Datum:            {meta['date']}")
    print(f"Lengte:           {meta['char_count']} tekens")
    print(f"Overall verdict:  {meta['overall_verdict']}")
    print(f"Spots genoemd:    {len(meta['spots_mentioned'])} ({', '.join(meta['spots_mentioned'])})")
    print(f"Vensters:         {sum(len(v) for v in meta['windows_per_spot'].values())} totaal")
    if meta['verdicts_per_spot']:
        print()
        print("Per spot:")
        for spot, verdict in meta['verdicts_per_spot'].items():
            wins = ', '.join(meta['windows_per_spot'].get(spot, []))
            print(f"  {spot:18s} → {verdict:10s} {wins}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
