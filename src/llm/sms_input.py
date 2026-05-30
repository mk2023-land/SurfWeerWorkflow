"""
Input-shaping voor Claude: bouwt de structured-input dictionaries die de LLM
input voeden. Geen LLM-call, geen prompt — puur transformatie van scoring-
output naar JSON-vriendelijke dicts met allowed-citations whitelist.

Functies hier (`_prepare_alert_input`, `_prepare_digest_input`, ...) worden
zowel intern door SMSGenerator gebruikt als extern door main.py voor
validator-input — beide paden delen exact dezelfde shape.
"""
import math
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

_AMSTERDAM = ZoneInfo('Europe/Amsterdam')

from src.data.models import (
    AlertCandidate,
    HourState,
    ScoreBreakdown,
    SurfWindow,
    SwellType,
)

from .sms_formatting import (
    _DAY_NL_SHORT,
    _hours_to,
    _tide_window_quality,
    degrees_to_compass,
    is_blocked_by_ijmuiden_pier,
    moon_phase_info,
    peak_block,
    wind_label_for_noordwijk,
)


def _prepare_alert_input(alert: AlertCandidate) -> dict:
    input_data: dict = {
        "type": "alert",
        "date": alert.detection_time.strftime("%Y-%m-%d"),
        "trigger_types": [t.value for t in alert.window.triggers] if alert.window else [],
        "trigger_explanation": alert.explanation,
        "rarity": f"{alert.window.rarity_percentile:.0f}e percentile" if alert.window else "",
        "webcam_url": "https://surfweer.nl/webcams/noordwijk/",
    }
    if alert.window:
        peak_hour_score = max(alert.window.hourly_scores, key=lambda s: s.total_score)
        input_data["window"] = {
            "start": alert.window.start.strftime("%H:%M"),
            "end": alert.window.end.strftime("%H:%M"),
            "duration_hours": round(alert.window.duration_hours, 1),
            "peak_time": peak_hour_score.timestamp.strftime("%H:%M"),
        }
    return input_data


def _prepare_digest_input(
    hour_states: list[HourState],
    scores: list[ScoreBreakdown],
    windows: list[SurfWindow],
    forecast_summary: dict,
    wind_spread_series: Optional[list[dict]] = None,
) -> dict:
    """
    Multi-day digest: vandaag + 4 dagen vooruit. Per dag: peak_hour-condities,
    beste window (indien surfable), tij-richting + eerstvolgende hoog/laag,
    en springtij-context.

    Sprint 2 #8 — optioneel `wind_spread_series` met inter-model spread per
    uur. Indien aanwezig wordt een dag-level `model_spread_warning` veld
    toegevoegd aan elk day_block zodat de LLM "modellen lopen nog uiteen"
    kan verwoorden.

    Horizon was 4 dagen maar miste de surfable T+4 pulse die Tobias in zijn
    SMS wel meeneemt. Verhoogd naar 5 dagen na benchmark 2026-05-23
    (Tobias: woensdag heuphoog; systeem: stopte bij dinsdag).
    """
    days = _group_by_day(hour_states, scores)
    day_blocks: list[dict] = []
    labels = ["vandaag", "morgen", "overmorgen", "+3", "+4"]

    # Map timestamp → spread-dict voor snelle lookup per dag
    spread_by_ts = {}
    if wind_spread_series:
        for entry in wind_spread_series:
            spread_by_ts[entry['timestamp']] = entry

    for i, (date_obj, day_states, day_scores) in enumerate(days[:5]):
        if not day_states or not day_scores:
            continue
        label = labels[i] if i < len(labels) else date_obj.strftime("%a %d/%m")
        day_block = _summarize_day(
            day_states, day_scores, windows,
            date_obj=date_obj, label_nl=label
        )

        # Sprint 2 #8 — dag-level model spread warning
        if spread_by_ts:
            day_spreads = [
                spread_by_ts[s.timestamp]
                for s in day_states
                if s.timestamp in spread_by_ts
            ]
            if day_spreads:
                max_speed_std = max(d['speed_std_kn'] for d in day_spreads)
                max_dir_spread = max(d['direction_spread_deg'] for d in day_spreads)
                day_block['model_spread'] = {
                    'max_speed_std_kn': round(max_speed_std, 1),
                    'max_direction_spread_deg': round(max_dir_spread, 1),
                    'n_models': day_spreads[0].get('n_models', 1),
                }
                # Warning vlag voor de LLM
                if max_speed_std > 5.0 or max_dir_spread > 25.0:
                    day_block['model_spread_warning'] = True

        # Onderdruk hedging-signalen voor onbetwist flat-dagen. Zelfs als
        # windmodellen 5+ kn spreiding hebben verandert dat niets aan een
        # 0,2m windhoogte → "modellen onzeker" is misleidend voor T+0 als
        # de dag sowieso niet surfbaar is. Drempel: peak Hs < 0,4m én niet
        # surfbaar. Boven die drempel (borderline dagen) blijft hedging
        # relevant. Toegevoegd na benchmark 2026-05-23 (zaterdag, peak 0,3m,
        # systeem schreef "modellen nog onzeker over de details").
        peak_hs = (day_block.get('peak_height_hour') or {}).get('wave_height_m') or 0.0
        if not day_block.get('is_surfable', False) and peak_hs < 0.4:
            if day_block.get('confidence_label') == 'laag':
                day_block['confidence_label'] = 'matig'
            day_block.pop('model_spread_warning', None)

        day_blocks.append(day_block)

    # Lookahead: scan dagen 6-8 (na de digest-window) op aankomende swell.
    # We hebben 8 dagen forecast (192u Open-Meteo) maar tonen er maar 5 in
    # de digest. Als er VERDER in de week iets aankomt, geven we Claude
    # voldoende info om een korte vooruitblik-zin te schrijven aan het einde.
    lookahead = _build_lookahead(days[5:8])

    now = datetime.now()
    _, moon_label, is_spring = moon_phase_info(now)

    return {
        "type": "digest",
        "date_today": now.strftime("%Y-%m-%d"),
        "day_label_today": _DAY_NL_SHORT[now.weekday()],
        "days": day_blocks,
        "lookahead": lookahead,
        "tide_context": {
            "moon_phase_nl": moon_label,
            "spring_tide": is_spring,
            "spring_tide_label": "springtij" if is_spring else None,
        },
        "forecast_summary": forecast_summary,
        "webcam_url": "https://surfweer.nl/webcams/noordwijk/",
    }


def _build_lookahead(future_days: list) -> dict:
    """
    Scan dagen 6-8 (na de 5-daagse digest-window) op aankomende swell.

    Returns dict met:
      - has_swell_arrival: bool — is er een dag met substantiële swell?
      - best_day_label: str | None — bv. "ma" / "di" (kort) als er iets
        noemenswaardigs aankomt
      - best_day_date: str | None — "2026-05-26" voor traceability
      - days_ahead: int | None — afstand t.o.v. vandaag (4, 5, 6, 7)
      - peak_height_m: float | None
      - peak_period_s: float | None
      - peak_wave_direction: str | None — kompasrichting
      - summary_quality: str | None — "klein", "matig", "stevig"
      - allowed_citations: dict — extra wave_heights/periods/dirs voor
        validator zodat Claude deze waarden mag noemen zonder false-positive.

    Drempel voor "aankomende swell": peak Hs ≥ 0.8m EN peak periode ≥ 7s
    (echte swell, niet wind-sea). Lager → niet noemenswaardig, Claude
    zegt niets in de vooruitblik.
    """
    empty = {
        "has_swell_arrival": False,
        "best_day_label": None,
        "best_day_date": None,
        "days_ahead": None,
        "peak_height_m": None,
        "peak_period_s": None,
        "peak_wave_direction": None,
        "summary_quality": None,
        "allowed_citations": {
            "wave_heights_m": [],
            "wave_periods_s": [],
            "wave_directions_compass": [],
            "day_labels_short": [],
        },
    }
    if not future_days:
        return empty

    best = None  # (score, day_index, day_data)
    for offset, (date_obj, day_states, day_scores) in enumerate(future_days):
        if not day_states or not day_scores:
            continue
        # Pak hoogste Hs van die dag (alleen daglicht-uren).
        daylight = [
            (s, sc) for s, sc in zip(day_states, day_scores, strict=False)
            if sc.total_score > 0
        ]
        if not daylight:
            continue
        peak_state, _ = max(
            daylight,
            key=lambda pair: pair[0].wave_spectrum.significant_height_total,
        )
        hs = peak_state.wave_spectrum.significant_height_total
        # Dominante periode + richting via peaks of mean
        if peak_state.wave_spectrum.peaks:
            dom = max(peak_state.wave_spectrum.peaks, key=lambda p: p.height_m)
            tp = dom.period_s
            wave_dir_deg = dom.direction_deg
        else:
            tp = peak_state.wave_spectrum.mean_period or 0.0
            wave_dir_deg = peak_state.wave_spectrum.mean_direction

        # Score voor "noemenswaardigheid": Hs²·T (energy-achtig)
        score = (hs ** 2) * tp
        if hs >= 0.8 and tp >= 7.0:
            candidate = (score, offset, date_obj, hs, tp, wave_dir_deg)
            if best is None or candidate[0] > best[0]:
                best = candidate

    if best is None:
        return empty

    _, offset, date_obj, hs, tp, wave_dir_deg = best
    days_ahead = 5 + offset  # 5 = laatste digest-dag (+4), offset 0 = +5
    if hs >= 1.5 and tp >= 9.0:
        quality = "stevig"
    elif hs >= 1.0:
        quality = "matig"
    else:
        quality = "klein"

    height_r = round(hs, 1)
    period_r = round(tp, 1)
    wave_dir_compass = degrees_to_compass(wave_dir_deg)
    day_short = _DAY_NL_SHORT[date_obj.weekday()]

    return {
        "has_swell_arrival": True,
        "best_day_label": day_short,
        "best_day_date": date_obj.strftime("%Y-%m-%d"),
        "days_ahead": days_ahead,
        "peak_height_m": height_r,
        "peak_period_s": period_r,
        "peak_wave_direction": wave_dir_compass,
        "summary_quality": quality,
        "allowed_citations": {
            "wave_heights_m": [height_r],
            "wave_periods_s": [period_r],
            "wave_directions_compass": [wave_dir_compass],
            "day_labels_short": [day_short],
        },
    }


def _circular_mean_deg(degrees: list[float]) -> float:
    """Cirkelgemiddelde van hoeken in graden. Voorkomt artefacten rond 0/360."""
    if not degrees:
        return 0.0
    sin_sum = sum(math.sin(math.radians(d)) for d in degrees)
    cos_sum = sum(math.cos(math.radians(d)) for d in degrees)
    return (math.degrees(math.atan2(sin_sum, cos_sum)) + 360) % 360


def _wind_summary_for_day(day_states: list[HourState]) -> dict:
    """
    Wind-dagdelen voor de LLM. Tobias benoemt vaak avondwind die op zichzelf
    geen surf geeft maar mogelijk de volgende dag wel ("dinsdag-avond NNO
    4bft, komt te laat om iets te geven, maar woensdag lukt dat wel").

    Bands in NL-tijd (HourState.timestamp is Europe/Amsterdam):
      - morning: 06-12u
      - midday:  12-17u
      - evening: 17-22u

    Veld `is_building_to_evening`=true bij avond ≥ ochtend + 5kn (signal dat
    iets meteorologisch verandert; LLM mag duiden).
    """
    bands = {
        'morning': (6, 12),
        'midday':  (12, 17),
        'evening': (17, 22),
    }
    summary: dict = {}
    for band_name, (start_h, end_h) in bands.items():
        band_states = [
            s for s in day_states
            if start_h <= s.timestamp.hour < end_h
        ]
        if not band_states:
            summary[band_name] = None
            continue
        avg_speed = sum(s.wind.speed_kn for s in band_states) / len(band_states)
        avg_dir = _circular_mean_deg(
            [float(s.wind.direction_deg) for s in band_states]
        )
        gusts = [s.wind.gusts_kn for s in band_states if s.wind.gusts_kn is not None]
        max_gust = max(gusts) if gusts else None
        summary[band_name] = {
            'avg_speed_kn': round(avg_speed, 1),
            'dominant_direction_compass': degrees_to_compass(avg_dir),
            'max_gust_kn': round(max_gust, 1) if max_gust is not None else None,
        }

    morning_kn = (summary.get('morning') or {}).get('avg_speed_kn') or 0.0
    evening_kn = (summary.get('evening') or {}).get('avg_speed_kn') or 0.0
    diff = evening_kn - morning_kn
    summary['speed_change_morning_to_evening_kn'] = round(diff, 1)
    summary['is_building_to_evening'] = diff >= 5.0
    summary['is_dropping_to_evening'] = diff <= -5.0
    return summary


def _group_by_day(
    hour_states: list[HourState],
    scores: list[ScoreBreakdown],
) -> list[tuple]:
    """Groepeer (state, score) op kalenderdag in chronologische volgorde."""
    groups: dict = {}
    for s, sc in zip(hour_states, scores, strict=False):
        d = s.timestamp.date()
        groups.setdefault(d, ([], []))
        groups[d][0].append(s)
        groups[d][1].append(sc)
    return [(d, *groups[d]) for d in sorted(groups.keys())]


def _summarize_day(
    day_states: list[HourState],
    day_scores: list[ScoreBreakdown],
    all_windows: list[SurfWindow],
    date_obj,
    label_nl: str,
) -> dict:
    """
    Per-dag samenvatting voor de LLM met explicietere structuur:
    - `peak_height_hour`: uur van hoogste golf (wat surfers "piek" noemen)
    - `best_window`: alleen aanwezig als er een surfable OF longboard window
      op deze dag is. Bevat `kind` zodat de LLM weet of het shortboard/longboard is.
    - `_allowed_citations`: opsomming van getalwaarden die de LLM letterlijk
      MAG noemen — anti-hallucinatie vangnet, ook gebruikt door validator.
    """
    # Hoogste golfhoogte van de dag — dit is "piek" in surfers-taal.
    # ALLEEN daglicht-uren tellen mee (score > 0): een nacht-uur als "piek"
    # presenteren leidt tot misleidende berichten ("piek om 23u").
    daylight_indices = [i for i, sc in enumerate(day_scores) if sc.total_score > 0]
    if daylight_indices:
        peak_height_idx = max(
            daylight_indices,
            key=lambda i: day_states[i].wave_spectrum.significant_height_total,
        )
    else:
        # Geen daglicht-uren (shouldn't happen — defensive fallback)
        peak_height_idx = max(
            range(len(day_states)),
            key=lambda i: day_states[i].wave_spectrum.significant_height_total,
        )
    peak_height_state = day_states[peak_height_idx]
    day_scores[peak_height_idx]

    # Best score-uur — voor windowdetectie, NIET als "piek" naar LLM
    best_score_idx = max(range(len(day_scores)), key=lambda i: day_scores[i].total_score)
    best_score = day_scores[best_score_idx]

    # Windows op deze dag, gesplitst per kind
    day_windows = [
        w for w in all_windows
        if day_states[0].timestamp <= w.peak_hour <= day_states[-1].timestamp
    ]
    surfable_windows = [w for w in day_windows if w.kind == 'surfable']
    longboard_windows = [w for w in day_windows if w.kind == 'longboard']

    # Surfable wint van longboard als beide bestaan
    chosen_window = None
    if surfable_windows:
        chosen_window = max(surfable_windows, key=lambda w: w.peak_score)
    elif longboard_windows:
        chosen_window = max(longboard_windows, key=lambda w: w.peak_score)

    # Alle "andere" windows van de dag (niet de chosen) — Tobias noemt vaak
    # meerdere vensters op een dag ("14-16u of na 19:30u"). Door deze ook
    # mee te geven kan de LLM dat patroon repliceren.
    other_windows = [w for w in day_windows if w is not chosen_window]

    peak_height_conditions = _hour_state_to_conditions(peak_height_state)

    # Probabilistische confidence (Sprint 3 #17). Score-uren tellen alleen
    # mee als ze daglicht-uren zijn (total_score > 0). Lege fallback → 1.0
    # (volle vertrouwen) zodat ontbrekende multi-model data geen "laag"
    # label oplevert.
    confidence_values = [
        getattr(sc, 'confidence', 1.0) for sc in day_scores
        if sc.total_score > 0
    ]
    day_confidence = (
        sum(confidence_values) / len(confidence_values)
        if confidence_values else 1.0
    )
    if day_confidence >= 0.85:
        confidence_label = "hoog"
    elif day_confidence >= 0.65:
        confidence_label = "matig"
    else:
        confidence_label = "laag"

    result: dict = {
        "label_nl": label_nl,
        "date": date_obj.strftime("%Y-%m-%d"),
        "day_short": _DAY_NL_SHORT[date_obj.weekday()],
        "is_surfable": best_score.total_score >= 60,
        "peak_height_hour": peak_height_conditions,  # hier zit dé golfhoogte-piek
        "tide_summary": _tide_summary_for_day(day_states, peak_height_state),
        "wind_summary": _wind_summary_for_day(day_states),
        "confidence": round(day_confidence, 2),
        "confidence_label": confidence_label,
    }

    def _window_payload(w):
        peak_state = next(
            (s for s in day_states if s.timestamp == w.peak_hour),
            day_states[0],
        )
        return {
            "is_surfable": w.kind == 'surfable',
            "kind": w.kind,
            "start_time": w.start.strftime("%H:%M"),
            "end_time": w.end.strftime("%H:%M"),
            "duration_hours": round(w.duration_hours, 1),
            "peak_time": w.peak_hour.strftime("%H:%M"),
            "peak_block": peak_block(w),
            "peak_conditions": _hour_state_to_conditions(peak_state),
        }

    if chosen_window:
        result["best_window"] = _window_payload(chosen_window)
    else:
        result["best_window"] = {"is_surfable": False, "kind": None}

    # Andere windows van de dag (Tobias' "14-16u of na 19:30u" patroon)
    result["other_windows"] = [_window_payload(w) for w in other_windows]

    # Anti-hallucinatie vangnet — exact wat de LLM mag citeren.
    # Inclusief ALLE daglicht-uren van de dag zodat ochtend-wind én avond-
    # wind beide legitiem mogen worden genoemd ("ochtend ZW, 's avonds draait
    # naar ZO"). Voorheen alleen het peak-uur → echte windrichtingen later
    # op de dag werden onterecht als hallucinatie geflagd.
    daylight_states_with_scores = [
        (s, sc) for s, sc in zip(day_states, day_scores, strict=False) if sc.total_score > 0
    ]
    if not daylight_states_with_scores:
        # Geen daglicht-uren (defensief) — val terug op peak alleen.
        all_day_conditions = [peak_height_conditions]
    else:
        all_day_conditions = [
            _hour_state_to_conditions(s) for s, _ in daylight_states_with_scores
        ]

    result["_allowed_citations"] = _build_allowed_citations(
        peak_height_conditions,
        result.get("best_window"),
        result["tide_summary"],
        other_windows=result["other_windows"],
        all_day_conditions=all_day_conditions,
        wind_summary=result.get("wind_summary"),
    )

    return result


def _build_allowed_citations(
    peak_height_conditions: dict,
    best_window: Optional[dict],
    tide_summary: dict,
    other_windows: Optional[list] = None,
    all_day_conditions: Optional[list] = None,
    wind_summary: Optional[dict] = None,
) -> dict:
    """
    Bouw een whitelist van getallen, tijden en richtingen die de LLM voor
    deze dag mag noemen. Wordt ook door SMSValidator gebruikt om
    hallucinaties te detecteren.

    `all_day_conditions` is een lijst _hour_state_to_conditions() dicts voor
    ALLE daglicht-uren van de dag — wind kan binnen één dag draaien, dus
    de whitelist moet alle uren dekken, niet alleen peak.

    `wind_summary` (optioneel): per-dagdeel wind-gemiddelden uit
    `_wind_summary_for_day`. Bandgemiddelden (morning/midday/evening) worden
    als citeerbare wind-speeds + richtingen toegevoegd zodat de LLM "avond
    bouwt op naar X kn Y" mag schrijven zonder hallucinatie-flag.
    """
    # Start altijd met peak — backwards-compat als all_day_conditions ontbreekt.
    seed = list(all_day_conditions) if all_day_conditions else [peak_height_conditions]

    heights_m: set = set()
    periods_s: set = set()
    wind_speeds_kn: set = set()
    wind_dirs: set = set()
    wave_dirs: set = set()
    times_hhmm: set = set()
    gusts_kn: set = set()
    air_temps_c: set = set()
    ssts_c: set = set()
    precipitations_mm: set = set()
    visibilities_m: set = set()

    for cond in seed:
        heights_m.add(cond.get("wave_height_m"))
        periods_s.add(cond.get("wave_period_s"))
        wind_speeds_kn.add(cond.get("wind_speed_kn"))
        wind_dirs.add(cond.get("wind_direction_compass"))
        wave_dirs.add(cond.get("wave_direction_compass"))
        times_hhmm.add(cond.get("time"))
        gusts_kn.add(cond.get("wind_gust_kn"))
        air_temps_c.add(cond.get("air_temperature_c"))
        ssts_c.add(cond.get("sea_surface_temperature_c"))
        precipitations_mm.add(cond.get("precipitation_mm"))
        visibilities_m.add(cond.get("visibility_m"))

    # Best_window kan 'surfable' of 'longboard' zijn — beide soorten leveren
    # citeerbare condities (wind/golf/tijd) op voor de LLM en validator.
    # Verzamel uit best_window én elk other_window
    all_windows_to_cite = []
    if best_window and best_window.get("kind") is not None:
        all_windows_to_cite.append(best_window)
    if other_windows:
        all_windows_to_cite.extend(other_windows)

    for win in all_windows_to_cite:
        pc = win.get("peak_conditions") or {}
        if pc:
            heights_m.add(pc.get("wave_height_m"))
            periods_s.add(pc.get("wave_period_s"))
            wind_speeds_kn.add(pc.get("wind_speed_kn"))
            wind_dirs.add(pc.get("wind_direction_compass"))
            wave_dirs.add(pc.get("wave_direction_compass"))
            gusts_kn.add(pc.get("wind_gust_kn"))
            air_temps_c.add(pc.get("air_temperature_c"))
            ssts_c.add(pc.get("sea_surface_temperature_c"))
            precipitations_mm.add(pc.get("precipitation_mm"))
            visibilities_m.add(pc.get("visibility_m"))
        times_hhmm.add(win.get("start_time"))
        times_hhmm.add(win.get("end_time"))
        times_hhmm.add(win.get("peak_time"))
        pb = win.get("peak_block") or {}
        times_hhmm.add(pb.get("start_time"))
        times_hhmm.add(pb.get("end_time"))

    if tide_summary.get("next_high_time"):
        times_hhmm.add(tide_summary["next_high_time"])
    if tide_summary.get("next_low_time"):
        times_hhmm.add(tide_summary["next_low_time"])
    # Tobias-stijl: alle HW/LW-tijden van DEZE dag mogen geciteerd worden,
    # niet alleen de "next" (die kan op laat-op-de-dag al morgen zijn).
    for t in (tide_summary.get("high_tide_times_today") or []):
        times_hhmm.add(t)
    for t in (tide_summary.get("low_tide_times_today") or []):
        times_hhmm.add(t)

    # Wind-summary bandgemiddelden + richtingen mogen óók geciteerd worden.
    if wind_summary:
        for band_name in ('morning', 'midday', 'evening'):
            band = wind_summary.get(band_name)
            if not band:
                continue
            wind_speeds_kn.add(band.get('avg_speed_kn'))
            wind_dirs.add(band.get('dominant_direction_compass'))
            if band.get('max_gust_kn') is not None:
                gusts_kn.add(band.get('max_gust_kn'))

    def _clean(seq):
        return sorted({v for v in seq if v is not None})

    return {
        "wave_heights_m": _clean(heights_m),
        "wave_periods_s": _clean(periods_s),
        "wind_speeds_kn": _clean(wind_speeds_kn),
        "wind_directions_compass": _clean(wind_dirs),
        "wave_directions_compass": _clean(wave_dirs),
        "times_hhmm": _clean(times_hhmm),
        # Uitbreidingen — gust + atmospheric (Sprint 4):
        "wind_gusts_kn": _clean(gusts_kn),
        "air_temperatures_c": _clean(air_temps_c),
        "sst_c": _clean(ssts_c),
        "precipitations_mm": _clean(precipitations_mm),
        "visibilities_m": _clean(visibilities_m),
    }


def _hour_state_to_conditions(state: HourState) -> dict:
    """Pak fysische condities uit HourState. Alles in expliciete eenheden."""
    from src.scoring.hourly import (
        convective_warning,
        recommend_boards,
        storm_surge_warning,
        tide_velocity_mh,
        visibility_concern,
    )

    spectrum = state.wave_spectrum
    dominant = max(spectrum.peaks, key=lambda p: p.height_m) if spectrum.peaks else None

    swell_type_label = None
    if dominant:
        swell_type_label = {
            SwellType.GROUND_SWELL: "groundswell",
            SwellType.WIND_SWELL:   "wind-swell",
            SwellType.WIND_SEA:     "wind-sea",
        }.get(dominant.type, "onbekend")

    wave_dir_deg = dominant.direction_deg if dominant else int(spectrum.mean_direction)
    dominant_period_s = dominant.period_s if dominant else spectrum.mean_period

    # Tij-detail voor LLM: niveau, fase, en uren tot eerstvolgende HW/LW —
    # geeft de LLM materiaal om Tobias-stijl te schrijven ("opkomend tot 14u",
    # "rond hoog water", "afgaand tot 17u laag").
    hours_to_high = _hours_to(state.timestamp, state.tide.next_high)
    hours_to_low = _hours_to(state.timestamp, state.tide.next_low)

    # Sprint 2 #11 — tide-flank features. Tide velocity (m/u) en is_rising
    # boolean geven de LLM materiaal om Tobias-stijl te schrijven
    # ("tij komt op stevig", "tij valt nog 2u").
    is_rising = (state.tide.phase == "opgaand")
    tide_vel = tide_velocity_mh(
        state.tide.last_turn_time,
        state.tide.next_turn_time,
        state.tide.daily_range_m,
    )

    # Board-aanbeveling: welke boards werken bij deze Hs/Tp/wind combo?
    # Lege lijst = niet surfbaar voor enig bord. De LLM mag deze lijst
    # letterlijk citeren maar GEEN borden noemen die hier NIET in staan.
    boards_suitable = recommend_boards(
        hs_m=spectrum.significant_height_total,
        tp_s=dominant_period_s or 0.0,
        wind_speed_kn=state.wind.speed_kn,
        wind_direction_deg=state.wind.direction_deg,
    )

    # Atmospheric / oceaan context velden (nieuw — alle optioneel).
    # air_sea_temp_diff_c geeft de LLM materiaal voor stabiliteits-context;
    # precipitation_flag/convective/visibility zijn handelingsvlaggen.
    air_sea_diff = None
    if state.air_temperature_c is not None and state.sea_surface_temperature_c is not None:
        air_sea_diff = round(
            state.air_temperature_c - state.sea_surface_temperature_c, 1
        )
    precipitation_flag = (
        state.precipitation_mm is not None and state.precipitation_mm > 0.3
    )
    conv_warning = convective_warning(state.cape_jkg, state.lifted_index)
    vis_concern = visibility_concern(
        state.visibility_m, state.dew_point_c, state.air_temperature_c
    )
    surge_flag = storm_surge_warning(state.storm_surge_cm)
    storm_surge_cm_out = (
        round(float(state.storm_surge_cm), 0)
        if state.storm_surge_cm is not None and abs(state.storm_surge_cm) >= 20.0
        else None
    )

    return {
        "time": state.timestamp.strftime("%H:%M"),
        "wave_height_m": round(spectrum.significant_height_total, 1),
        "wave_period_s": round(dominant_period_s, 1),
        "wave_direction_deg": int(wave_dir_deg),
        "wave_direction_compass": degrees_to_compass(wave_dir_deg),
        "swell_type": swell_type_label or "onbekend",
        "swell_refracts_around_ijmuiden": is_blocked_by_ijmuiden_pier(int(wave_dir_deg)),
        "wind_speed_kn": round(state.wind.speed_kn, 1),
        "wind_gust_kn": round(state.wind.gusts_kn, 1) if state.wind.gusts_kn else None,
        "wind_direction_deg": int(state.wind.direction_deg),
        "wind_direction_compass": degrees_to_compass(state.wind.direction_deg),
        "wind_label": wind_label_for_noordwijk(state.wind.direction_deg),
        "tide_level_m": round(state.tide.level_m, 2),
        "tide_phase": state.tide.phase,
        "tide_is_rising": is_rising,
        "tide_velocity_mh": round(tide_vel, 2) if tide_vel > 0 else None,
        "hours_to_next_high": hours_to_high,
        "hours_to_next_low": hours_to_low,
        "tide_window_quality": _tide_window_quality(
            state.tide.normalized_level, dominant_period_s
        ),
        "boards_suitable": boards_suitable,
        "is_unsurfable": len(boards_suitable) == 0,
        # ---- Nieuwe atmospheric / oceaan context ----
        "air_temperature_c": (
            round(state.air_temperature_c, 1)
            if state.air_temperature_c is not None else None
        ),
        "sea_surface_temperature_c": (
            round(state.sea_surface_temperature_c, 1)
            if state.sea_surface_temperature_c is not None else None
        ),
        "air_sea_temp_diff_c": air_sea_diff,
        "precipitation_mm": (
            round(state.precipitation_mm, 1)
            if state.precipitation_mm is not None else None
        ),
        "precipitation_flag": precipitation_flag,
        "convective_warning": conv_warning,
        "visibility_m": (
            int(state.visibility_m) if state.visibility_m is not None else None
        ),
        "visibility_concern": vis_concern,
        "storm_surge_cm": storm_surge_cm_out,
        "storm_surge_warning": surge_flag,
        # Boei-observatie (alleen nowcast t=0..3u, anders None)
        "directional_spread_deg": (
            round(spectrum.directional_spread_deg, 1)
            if spectrum.directional_spread_deg is not None else None
        ),
        "peak_period_observed_s": (
            round(spectrum.peak_period_observed_s, 1)
            if spectrum.peak_period_observed_s is not None else None
        ),
    }


def _to_amsterdam_hhmm(dt: Optional[datetime]) -> Optional[str]:
    """Format een datetime als HH:MM in Europe/Amsterdam wall-clock.

    Verwacht ofwel een tz-aware datetime (wordt geconverteerd) of een naive
    die al in Europe/Amsterdam-tijd is (wordt rechtstreeks gestrftime'd).
    RWS-tijden komen aware UTC+01:00 (Dutch standard, geen DST) — naïef
    strftime levert daardoor in zomer de verkeerde wall-clock (1u te vroeg).
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_AMSTERDAM)
    return dt.strftime("%H:%M")


def _dedupe_tide_events(events: list[datetime], cluster_minutes: int = 30) -> list[datetime]:
    """Cluster events binnen `cluster_minutes` tot één representatief tijdstip.

    De RWS-fase-detectie merkt op een vlakke piek soms 2 opeenvolgende 10-min
    punten als HW/LW (bv. 04:40 én 04:50). Hier collapse'n we die tot het
    eerste tijdstip per cluster — voor Tobias-stijl SMS is één HW-tijd per
    halve cyclus genoeg.
    """
    sorted_ev = sorted(events)
    out: list[datetime] = []
    for ev in sorted_ev:
        if out and abs((ev - out[-1]).total_seconds()) < cluster_minutes * 60:
            continue
        out.append(ev)
    return out


def _tide_summary_for_day(day_states: list[HourState], peak_state: HourState) -> dict:
    """Tij-context voor een specifieke dag.

    Levert:
    - `phase_at_peak`: opgaand/afgaand/onbekend op piek-uur (currents-richting)
    - `level_m_at_peak`: tij-hoogte (m NAP) op piek-uur
    - `next_high_time` / `next_low_time`: eerstvolgende HW/LW vanaf peak-uur,
      in Europe/Amsterdam wall-clock (kan tomorrow zijn als dag's laatste HW
      al voorbij is)
    - `high_tide_times_today` / `low_tide_times_today`: ALLE HW/LW-tijden
      die op deze kalenderdag vallen (Europe/Amsterdam), als HH:MM-lijst.
      Tobias citeert vaak exacte tij-keerpunten ("vloed komt vol inzetten
      15u", "laagtij 10u"); deze velden geven de LLM die data per dag.
    - `daily_range_m`: HW-LW range (m); driver voor spring/doodtij-label
    - `spring_neap_label`: "springtij" / "doodtij" / None
    - `current_velocity_norm`: 0-1.2 schatting van horizontale tij-stroming
      op piek-uur (slack-water → 0, mid-cycle springtij → ~1.2). Geen
      kn/ms — alleen relatieve intensiteit voor LLM-kwalificatie.
    """
    tide = peak_state.tide

    # Verzamel ALLE unieke HW- en LW-tijden waar uren in deze dag
    # naartoe kijken (next_high/next_low per HourState). Per dag levert dit
    # de eigen HW/LW + (na de dag's laatste HW) de eerste van morgen — we
    # filteren op kalenderdatum zodat we alleen TIJDEN VAN VANDAAG houden.
    day_dates = {s.timestamp.date() for s in day_states}

    def _on_this_day(dt: datetime) -> bool:
        if dt.tzinfo is not None:
            dt = dt.astimezone(_AMSTERDAM)
        return dt.date() in day_dates

    high_today_raw = sorted({s.tide.next_high for s in day_states
                             if s.tide.next_high and _on_this_day(s.tide.next_high)})
    low_today_raw = sorted({s.tide.next_low for s in day_states
                            if s.tide.next_low and _on_this_day(s.tide.next_low)})
    high_today = _dedupe_tide_events(high_today_raw)
    low_today = _dedupe_tide_events(low_today_raw)

    high_tide_times_today = [t for t in (_to_amsterdam_hhmm(h) for h in high_today) if t]
    low_tide_times_today = [t for t in (_to_amsterdam_hhmm(l) for l in low_today) if t]

    # Daily range geeft springtij-context (≥2.0m = springtij, sterke stroming).
    spring_label = None
    if tide.daily_range_m is not None:
        if tide.daily_range_m >= 2.0:
            spring_label = "springtij"
        elif tide.daily_range_m < 1.6:
            spring_label = "doodtij"

    # Tidal-current intensity op peak (0-1.2). Geeft de LLM een handvat om
    # "stroming staat stevig" vs. "slack" te onderscheiden zonder zelf
    # currents-kn te verzinnen.
    try:
        current_velocity_norm = round(
            tide.tidal_current_intensity(peak_state.timestamp), 2
        )
    except Exception:
        current_velocity_norm = None

    return {
        "phase_at_peak": tide.phase,                       # opgaand/afgaand/onbekend
        "level_m_at_peak": round(tide.level_m, 2),
        "next_high_time": _to_amsterdam_hhmm(tide.next_high),
        "next_low_time": _to_amsterdam_hhmm(tide.next_low),
        "high_tide_times_today": high_tide_times_today,
        "low_tide_times_today": low_tide_times_today,
        "daily_range_m": round(tide.daily_range_m, 2) if tide.daily_range_m else None,
        "spring_neap_label": spring_label,
        "current_velocity_norm": current_velocity_norm,
    }
