"""
referentie-forecaster-SMS ingestie voor Sprint 4 training-labels.

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
    cat referentie-forecaster_today.txt | python scripts/ingest_reference_message.py --date 2026-05-20

Bestand-layout:
    data/ref_archive/
        2026-05-19.txt        ← raw SMS-tekst
        2026-05-19.meta.json  ← geparste metadata (datum, spots, verdict)
        ...
"""
import argparse
import json
import re
import sys
from datetime import datetime, date
from pathlib import Path


ARCHIVE_DIR = Path(__file__).resolve().parent.parent / 'data' / 'ref_archive'

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
    text_lower = text.lower()

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


def main():
    parser = argparse.ArgumentParser(description='Ingest referentie-forecaster de referentie-forecaster SMS')
    parser.add_argument(
        '--date', type=str, default=None,
        help='Datum van het bericht (YYYY-MM-DD). Default: vandaag.'
    )
    parser.add_argument(
        '--text', type=str, default=None,
        help='SMS-tekst inline. Anders gelezen van stdin.'
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

    if txt_path.exists():
        print(f"⚠ {txt_path.name} bestaat al — overschrijven? [y/N] ", end='')
        if input().strip().lower() != 'y':
            print("Afgebroken.")
            return 1

    txt_path.write_text(text, encoding='utf-8')

    meta = parse_metadata(text, msg_date)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                         encoding='utf-8')

    print(f"✓ Opgeslagen: {txt_path}")
    print(f"✓ Metadata:   {meta_path}")
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
