"""
Deterministische fallback-templates voor SMS-berichten — gebruikt als de
Anthropic API faalt of geen API-key beschikbaar is.

Hier mag geen Claude-call zitten. Doel is: ALTIJD een nuttig bericht produceren,
ook bij volledige LLM-uitval, met dezelfde data-velden als de LLM zou krijgen.
"""
from src.data.models import (
    AlertCandidate,
    HourState,
    ScoreBreakdown,
    SurfWindow,
)

from .sms_formatting import degrees_to_compass, wind_label_for_noordwijk
from .sms_input import _group_by_day


def _fallback_alert_template(alert: AlertCandidate) -> str:
    if not alert.window:
        return f"NWIJK ALERT: {alert.explanation}. Cam: surfweer.nl/webcams/noordwijk/"
    time_str = f"{alert.window.start.strftime('%H:%M')}-{alert.window.end.strftime('%H:%M')}u"
    trigger_str = ", ".join([t.value for t in alert.window.triggers]) or "goede condities"
    return (f"NWIJK ALERT {alert.detection_time.strftime('%d-%m')} {time_str}: "
            f"{alert.window.peak_score}/100, {trigger_str}. "
            f"Cam: surfweer.nl/webcams/noordwijk/")


# Nederlandse dag-afkortingen (ma=maandag … zo=zondag); index = date.weekday().
_DAY_ABBR = ["ma", "di", "wo", "do", "vr", "za", "zo"]

# Dagdelen (NL-tijd, HourState.timestamp = Europe/Amsterdam). Gelijk aan de
# wind-banden in sms_input._wind_summary_for_day zodat digest + LLM-input één
# dagdeel-indeling delen. Uren buiten 6-22 zijn sowieso geen daglicht (score 0).
_DAYPARTS = [("ochtend", 6, 12), ("middag", 12, 17), ("avond", 17, 22)]


def _fmt_t(dt, unit: bool = False) -> str:
    """Compacte tijd: hele uren als '15', anders '15:30'; voeg 'u' toe bij unit.

    Geen `%-H` (Linux/macOS-only strftime-modifier, geen leidende nul strippen) —
    dat crasht met ValueError op Windows. `str(dt.hour)` is platform-onafhankelijk
    en geeft hetzelfde resultaat (geen leidende nul, want dt.hour is al een int)."""
    base = str(dt.hour) if dt.minute == 0 else f"{dt.hour}:{dt.minute:02d}"
    return base + ("u" if unit else "")


def _nl(x: float) -> str:
    """NL-decimaal met komma (0,6 i.p.v. 0.6) — zelfde stijl als de LLM-digest."""
    return f"{round(x, 1):.1f}".replace(".", ",")


def _fallback_digest_template(
    hour_states: list[HourState],
    scores: list[ScoreBreakdown],
    windows: list[SurfWindow],
) -> str:
    """
    Deterministische 5-daagse digest — fallback bij LLM-uitval.

    Output volgt hetzelfde verdict-eerst + tijdvenster-format als de LLM-digest
    (per dag `Nwijk <dag>: <verdict> <venster> — <getallen>`), zodat de
    fallback óók de digest-format-validator passeert (vereist een dagafkorting)
    én leesbaar blijft i.p.v. de oude losse-piek-stijl.

    Per dag:
      - verdict + tijdvenster vooraan (venster = aaneengesloten rijdbare span)
      - getallen (hoogte, periode, windrichting+snelheid) als onderbouwing
      - "flat" wanneer hele dag < 0.5m
      - springtij / mist / onweer als suffix-flags
    """
    if not hour_states or not scores:
        return (
            "Nwijk: geen data beschikbaar. "
            "Cam: surfweer.nl/webcams/noordwijk/"
        )

    # Lazy import: scoring.recommend_boards en visibility/convective helpers
    # zijn niet altijd aanwezig in unit-test contexts met mocked scoring.
    try:
        from src.scoring.context import verdict_from_conditions
        from src.scoring.hourly import (
            convective_warning,
            recommend_boards,
            visibility_concern,
        )
    except ImportError:
        recommend_boards = None
        verdict_from_conditions = None
        visibility_concern = None
        convective_warning = None

    def _assess(state):
        """(tier, boards, dominante-piek) voor één uur — DEZELFDE bron als het
        gelogde snapshot-verdict (verdict_from_conditions/recommend_boards)."""
        spec = state.wave_spectrum
        dom = max(spec.peaks, key=lambda p: p.height_m) if spec.peaks else None
        tp = (dom.period_s if dom else spec.mean_period) or 0.0
        if recommend_boards is not None:
            boards = recommend_boards(
                hs_m=spec.significant_height_total, tp_s=tp,
                wind_speed_kn=state.wind.speed_kn,
                wind_direction_deg=state.wind.direction_deg,
            )
        else:
            boards = []
            hs_ = spec.significant_height_total
            if hs_ >= 0.4:
                boards += ['longboard', 'midlength']
            if hs_ >= 0.5:
                boards.append('fish')
            if hs_ >= 1.0 and tp >= 6:
                boards.append('shortboard')
        if verdict_from_conditions is not None:
            tier = verdict_from_conditions(
                hs_m=spec.significant_height_total, tp_s=tp,
                wind_speed_kn=state.wind.speed_kn,
                wind_direction_deg=state.wind.direction_deg,
            )
        else:
            tier = 'surfable' if boards else 'flat'
        return tier, boards, dom

    def _verdict_text(tier, boards):
        """Tekst-nuance binnen een rijdbaar dagdeel (tier != flat)."""
        if tier == 'longboard':
            return "longboard"
        if 'shortboard' in boards:
            return "alles werkt"
        if 'fish' in boards:
            return "surfbaar (long/mid/fish)"
        if 'midlength' in boards:
            return "surfbaar (long/mid)"
        return "surfbaar"

    days = _group_by_day(hour_states, scores)
    parts: list[str] = []

    for date_obj, day_states, day_scores in days[:5]:
        if not day_states:
            continue
        dag = _DAY_ABBR[date_obj.weekday()]
        max_height_day = max(
            s.wave_spectrum.significant_height_total for s in day_states
        )

        # Dag-brede vlaggen (springtij/mist/onweer) uit het hoogste-golf-
        # daglichtuur — op de dag-header i.p.v. per dagdeel herhaald.
        daylight = [j for j, sc in enumerate(day_scores) if sc.total_score > 0]
        if daylight:
            peak_day_idx = max(
                daylight,
                key=lambda j: day_states[j].wave_spectrum.significant_height_total,
            )
        else:
            peak_day_idx = max(
                range(len(day_states)),
                key=lambda j: day_states[j].wave_spectrum.significant_height_total,
            )
        ps_day = day_states[peak_day_idx]
        flags = ""
        if ps_day.tide.daily_range_m is not None and ps_day.tide.daily_range_m >= 2.0:
            flags += " (springtij)"
        if visibility_concern is not None:
            vc = visibility_concern(
                ps_day.visibility_m, ps_day.dew_point_c, ps_day.air_temperature_c
            )
            if vc == 'haarmist_risico':
                flags += " (! mist mogelijk)"
            elif vc == 'dichte_mist':
                flags += " (! dichte mist)"
        if convective_warning is not None and convective_warning(
            ps_day.cape_jkg, ps_day.lifted_index
        ):
            flags += " (! onweer-risico)"

        # Rijdbare daglicht-uren → aaneengesloten sessies (gat > 1u = nieuwe
        # sessie). Per uur getoetst op board-geschiktheid, zodat we de ECHTE
        # uren tonen ("een dag heeft veel uren") i.p.v. één piekmoment.
        rideable = []
        for st, scb in zip(day_states, day_scores, strict=False):
            if scb.total_score <= 0:
                continue
            tier, boards, dom = _assess(st)
            if tier != 'flat' and boards:
                rideable.append((st, scb, tier, boards, dom))
        rideable.sort(key=lambda r: r[0].timestamp)

        runs: list[list] = []
        for item in rideable:
            if runs and (
                item[0].timestamp - runs[-1][-1][0].timestamp
            ).total_seconds() <= 3600:
                runs[-1].append(item)
            else:
                runs.append([item])

        # Geen enkele rijdbare sessie → compacte flat-regel (geen 3× "flat").
        if not runs:
            parts.append(
                f"Nwijk {dag}: flat — tot {round(max_height_day * 100)}cm, "
                f"te klein.{flags}"
            )
            continue

        # Ken elke sessie toe aan het dagdeel van zijn top-uur (hoogste score).
        run_by_part: dict[str, list] = {name: [] for name, _, _ in _DAYPARTS}
        for run in runs:
            top = max(run, key=lambda r: r[1].total_score)
            top_hour = top[0].timestamp.hour
            for name, h0, h1 in _DAYPARTS:
                if h0 <= top_hour < h1:
                    run_by_part[name].append((run, top))
                    break

        daypart_lines = []
        for name, _h0, _h1 in _DAYPARTS:
            label = name.ljust(8)
            band_runs = run_by_part[name]
            if not band_runs:
                daypart_lines.append(f"  {label}flat")
                continue
            segs = []
            numbers = None
            for run, top in sorted(
                band_runs, key=lambda rt: rt[0][0][0].timestamp
            )[:2]:
                st, scb, tier, boards, dom = top
                verdict = _verdict_text(tier, boards)
                start_t = _fmt_t(run[0][0].timestamp)
                end_t = _fmt_t(run[-1][0].timestamp, unit=True)
                top_t = _fmt_t(st.timestamp, unit=True)
                segs.append(f"{verdict} {start_t}-{end_t} (top {top_t})")
                if numbers is None:
                    spec = st.wave_spectrum
                    h = _nl(spec.significant_height_total)
                    p_s = _nl(dom.period_s if dom else spec.mean_period)
                    wave_dir = degrees_to_compass(
                        dom.direction_deg if dom else spec.mean_direction
                    )
                    wind_dir = degrees_to_compass(st.wind.direction_deg)
                    wind_kn = round(st.wind.speed_kn)
                    strong = " sterk" if wind_kn >= 18 else ""
                    wlabel = wind_label_for_noordwijk(st.wind.direction_deg)
                    numbers = (
                        f"{h}m {wave_dir} {p_s}s, "
                        f"{wind_kn}kn {wind_dir}{strong} {wlabel}"
                    )
            daypart_lines.append(f"  {label}{' ook '.join(segs)} — {numbers}")

        parts.append(f"Nwijk {dag}:{flags}\n" + "\n".join(daypart_lines))

    body = "\n".join(parts) if parts else "Nwijk: geen data."
    return f"{body}\nCam: surfweer.nl/webcams/noordwijk/"
