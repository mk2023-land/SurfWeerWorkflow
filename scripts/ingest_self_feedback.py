"""
Eigen surf-sessie-feedback ingestie.

Losstaand van de Tobias-referentie-pijplijn (ingest_reference_message.py) — ander
schema, ander doel. Hier gaat het om de EIGEN ervaring van de gebruiker na een
surf-sessie, niet om een externe forecaster:

- Gebruiker geeft per sessie een 1-10 tevredenheidsscore per onderdeel (tijd,
  hoogte, wind, duur, richting, stroming, optioneel een overall-score) + vrije
  toelichting. Geen ruwe metingen nodig — die halen we zelf op.
- KERNPUNT: we vergelijken tegen wat wij zeiden over JOUW eigen sessie-uren,
  NIET tegen ons gelogde piek-uur van die dag. Dat laatste zou het hele doel
  ondermijnen — als ons piek-uur zelf fout zit (verkeerd moment aangewezen),
  moet die fout juist zichtbaar worden in de vergelijking, niet weggemiddeld
  worden door altijd naar "onze beste voorspelling" te kijken.
- Daarom herberekenen we live, met dezelfde productiecode als main.py
  (`SurfAlertSystem._build_hour_states` + `score_hour_series`), wat we voor
  PRECIES jouw tijdvak zouden hebben gezegd — inclusief het beste moment BINNEN
  dat venster (dat kan om een heel ander uur gaan dan onze dag-piek).
- Vergelijkingsbasis blijft bewust ONZE VOORSPELLING (niet achteraf gemeten
  boei-data): Open-Meteo's archive-API heeft ~5 dagen vertraging en RWS-boei-
  historie is niet zomaar voor dagen terug op te vragen. Live herberekenen werkt
  voor vandaag/recente dagen binnen de forecast-horizon; ligt de datum te ver
  terug, dan valt dit script terug op de gelogde piek-uur-snapshot uit
  `data/forecast_features.jsonl` (zwakkere garantie, expliciet gemarkeerd via
  `match_quality`) en anders op niets.

Gebruik:
    python scripts/ingest_self_feedback.py --date 2026-08-29 --spot noordwijk \
        --start 14:00 --end 16:00 \
        --scores-json '{"tijd":7,"hoogte":5,"wind":6,"duur":8,"richting":4,"stroming":6,"overall":6}' \
        --note "wind draaide halverwege bij, werd rommeliger"
"""
import argparse
import asyncio
import contextlib
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Zelfde UTF-8-fix als ingest_reference_message.py / calibrate.py (Windows-console
# cp1252 crasht anders op de ↪/✓/✗-tekens hieronder).
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

# src/ importeerbaar maken voor de live-herbereken-stap (zelfde patroon als
# calibrate.py: repo-root op sys.path, geen her-implementatie van scoring-code).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SCORE_KEYS = ['tijd', 'hoogte', 'wind', 'duur', 'richting', 'stroming']
_OPTIONAL_SCORE_KEYS = ['overall']

# Zelfde privé-archiefrepo/map als de Tobias-parenpijplijn (naast ref_pairs.jsonl),
# apart bestand (ander schema). Volgt PAIRS_PATH mee — die is al REF_PAIRS_PATH/
# .env-aware, dus geen eigen env-var-override nodig om te vergeten.
FEEDBACK_PATH = PAIRS_PATH.parent / 'self_feedback.jsonl'


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)


# ---------------------------------------------------------------------------
# PRIMAIRE route: live herberekenen wat wij voor het ECHTE sessievenster zouden
# zeggen — géén piek-uur-shortcut. Werkt alleen als Open-Meteo/RWS nog data
# hebben voor `forecast_date` (vandaag/recent); anders None (caller valt terug).
# ---------------------------------------------------------------------------
async def live_session_conditions(spot: str, forecast_date: str, start: str, end: str) -> dict | None:
    if spot != 'noordwijk':
        # Alleen Noordwijk heeft een LocationConfig in config.py — andere spots
        # zijn nu niet ondersteund, geen stille verkeerde aanname doen.
        return None

    from src.config import NOORDWIJK
    from src.data.sources.open_meteo import fetch_all_openmeteo_data
    from src.data.sources.rws import fetch_all_rws_data
    from src.main import SurfAlertSystem
    from src.scoring.context import verdict_from_conditions
    from src.scoring.hourly import compute_wind_spread_per_hour, score_hour_series
    from src.scoring.wind import _wind_direction_cosine

    system = SurfAlertSystem(dry_run=True)

    try:
        openmeteo_data = await fetch_all_openmeteo_data(NOORDWIJK.lat, NOORDWIJK.lon)
    except Exception:
        return None
    if not openmeteo_data:
        return None
    try:
        rws_data = await fetch_all_rws_data() or {}
    except Exception:
        rws_data = {}

    try:
        from src.scoring.bias_correction import apply_bias_to_forecast, compute_buoy_bias
        boei_obs = (rws_data.get('primary_buoy') or {}).get('raw_data') or []
        marine_rows = (openmeteo_data or {}).get('marine') or []
        if boei_obs and marine_rows:
            bias = compute_buoy_bias(boei_obs, marine_rows, when=datetime.now())
            if bias:
                openmeteo_data['marine'] = apply_bias_to_forecast(marine_rows, bias, when=datetime.now())
    except Exception:
        pass  # bias-correctie is best-effort, zelfde als main.py

    hour_states = system._build_hour_states(openmeteo_data, rws_data)
    if not hour_states:
        return None

    forecast_by_model = (openmeteo_data or {}).get('forecast') or {}
    primary_model = forecast_by_model.get('knmi_seamless') or []
    wind_spread_full = compute_wind_spread_per_hour(forecast_by_model)
    spread_by_ts = {e['timestamp']: e for e in wind_spread_full}
    primary_by_ts = {row['timestamp']: row for row in primary_model}

    pressure_series, cloud_series, wind_spread_series = [], [], []
    for st in hour_states:
        row = primary_by_ts.get(st.timestamp) or {}
        pressure_series.append(row.get('pressure') or 1013.0)
        cloud_series.append(row.get('cloud_cover'))
        wind_spread_series.append(spread_by_ts.get(st.timestamp) or {})

    scores = score_hour_series(
        hour_states, pressure_series=pressure_series,
        cloud_cover_series=cloud_series, wind_spread_series=wind_spread_series,
    )

    try:
        target_date = datetime.strptime(forecast_date, '%Y-%m-%d').date()
    except ValueError:
        return None
    start_m, end_m = _minutes(start), _minutes(end)

    window = []
    for st, sc in zip(hour_states, scores, strict=False):
        if st.timestamp.date() != target_date:
            continue
        hm = st.timestamp.hour * 60 + st.timestamp.minute
        if start_m <= hm <= end_m:
            window.append((st, sc))
    if not window:
        return None  # datum buiten forecast-bereik, of venster valt buiten daglicht/data

    # Beste moment BINNEN het echte sessievenster — dit kan een ander uur zijn
    # dan de dag-piek, en dat is precies het punt: we testen JOUW venster.
    st_peak, sc_peak = max(window, key=lambda p: p[1].total_score)
    spec = st_peak.wave_spectrum
    dom = max(spec.peaks, key=lambda p: p.height_m) if spec.peaks else None
    tp = (dom.period_s if dom else spec.mean_period) or 0.0
    cos_off = _wind_direction_cosine(int(st_peak.wind.direction_deg), NOORDWIJK.beach_normal_deg)
    verdict = verdict_from_conditions(
        spec.significant_height_total, tp, st_peak.wind.speed_kn,
        int(st_peak.wind.direction_deg), NOORDWIJK.beach_normal_deg,
    )
    scores_in_window = [round(sc.total_score, 1) for _, sc in window]

    return {
        'hours_in_window': len(window),
        'peak_hour_in_window': st_peak.timestamp.strftime('%H:%M'),
        'score_range_in_window': [min(scores_in_window), max(scores_in_window)],
        'our_verdict': verdict,
        'our_peak_score': round(sc_peak.total_score, 1),
        'our_features': {
            'hs_m': round(spec.significant_height_total, 2),
            'tp_s': round(tp, 1),
            'wind_speed_kn': round(st_peak.wind.speed_kn, 1),
            'wind_dir_deg': int(st_peak.wind.direction_deg),
            'offshore_cos': round(cos_off, 3),
            'tide_level_norm': round(st_peak.tide.normalized_level, 2),
            'tide_phase': st_peak.tide.phase,
        },
        'our_score_basis': {
            'golf_score': round(sc_peak.golf_score, 2),
            'wind_score': round(sc_peak.wind_score, 2),
            'tide_score': round(sc_peak.tide_score, 2),
            'swell_dir_bonus': round(sc_peak.swell_dir_bonus, 2),
            'confidence': round(sc_peak.confidence, 4),
        },
    }


# ---------------------------------------------------------------------------
# FALLBACK route: alleen gebruikt als live herberekenen niks opleverde (datum
# buiten forecast-bereik e.d.). Zoekt de gelogde PIEK-uur-snapshot van die dag
# — expliciet zwakker, want dat is niet per se jouw eigen sessie-uur.
# ---------------------------------------------------------------------------
def _load_all_snapshots(spot: str, forecast_date: str) -> list[dict]:
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


def _fallback_logged_peak(spot: str, forecast_date: str, start: str, end: str):
    """Laatste redmiddel: de meest recente gelogde dag-piek, ongeacht of die in
    [start,end] valt. Retourneert (dict_of_None, match_quality, toelichting)."""
    cands = _load_all_snapshots(spot, forecast_date)
    if not cands:
        return None, 'none', None
    cands.sort(key=lambda r: r.get('run_timestamp', ''))
    latest = cands[-1]
    peak_hour = latest.get('peak_hour')
    note = (
        "LET OP: dit is ons gelogde dag-piek-uur, niet jouw eigen sessie-uur "
        "(live herberekenen lukte niet voor deze datum)"
    )
    if peak_hour and _minutes(start) <= _minutes(peak_hour) <= _minutes(end):
        note = "dag-piek viel toevallig binnen je venster; live herberekenen lukte niet"
    return latest, 'fallback_logged_peak', note


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


async def _amain(args) -> int:
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

    for issue in _validate_scores(scores):
        print(f"⚠ {issue}", file=sys.stderr)

    live = await live_session_conditions(args.spot, args.date, args.start, args.end)
    if live:
        quality, match_note = 'live', (
            f"{live['hours_in_window']} uur live herberekend binnen "
            f"[{args.start}-{args.end}], score {live['score_range_in_window'][0]}"
            f"-{live['score_range_in_window'][1]}"
        )
        matched_hour = live['peak_hour_in_window']
        our_verdict = live['our_verdict']
        our_peak_score = live['our_peak_score']
        our_features = live['our_features']
        our_score_basis = live['our_score_basis']
    else:
        snapshot, quality, match_note = _fallback_logged_peak(args.spot, args.date, args.start, args.end)
        matched_hour = (snapshot or {}).get('peak_hour')
        our_verdict = (snapshot or {}).get('our_verdict')
        our_peak_score = (snapshot or {}).get('our_peak_score')
        our_features = {
            k: (snapshot or {}).get(k)
            for k in ('hs_m', 'tp_s', 'wind_speed_kn', 'wind_dir_deg',
                     'offshore_cos', 'tide_level_norm', 'tide_phase')
        } if snapshot else None
        our_score_basis = (snapshot or {}).get('score_basis')

    record = {
        'date': args.date,
        'spot': args.spot,
        'session_start': args.start,
        'session_end': args.end,
        'user_scores': {k: scores[k] for k in _SCORE_KEYS + _OPTIONAL_SCORE_KEYS if k in scores},
        'note': args.note,
        'match_quality': quality,
        'match_note': match_note,
        'matched_hour': matched_hour,
        'our_verdict': our_verdict,
        'our_peak_score': our_peak_score,
        'our_features': our_features,
        'our_score_basis': our_score_basis,
    }

    write_feedback(record)

    print(f"✓ Opgeslagen: {FEEDBACK_PATH}")
    print(f"  Datum:          {args.date} {args.start}-{args.end}u ({args.spot})")
    print(f"  Match-kwaliteit: {quality}" + (f" ({match_note})" if match_note else ""))
    if our_verdict is not None:
        print(f"  Onze voorspelling voor jouw venster: {our_verdict} "
              f"(beste moment {matched_hour}, score {our_peak_score})")
    else:
        print("  Geen conditie-data gevonden voor deze dag/spot — alleen de "
              "score is opgeslagen.")
    print(f"  Scores: {record['user_scores']}")
    if args.note:
        print(f"  Toelichting: {args.note}")

    _sync_private_archive(
        args.date, commit_message=f'Eigen surf-feedback {args.date} {args.start}-{args.end}',
    )
    return 0


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
    return asyncio.run(_amain(args))


if __name__ == '__main__':
    sys.exit(main())
