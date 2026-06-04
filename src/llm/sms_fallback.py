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

from .sms_formatting import degrees_to_compass
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


def _fmt_t(dt, unit: bool = False) -> str:
    """Compacte tijd: hele uren als '15', anders '15:30'; voeg 'u' toe bij unit."""
    base = dt.strftime("%-H") if dt.minute == 0 else dt.strftime("%-H:%M")
    return base + ("u" if unit else "")


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
        from src.scoring.hourly import (
            convective_warning,
            recommend_boards,
            visibility_concern,
        )
    except ImportError:
        recommend_boards = None
        visibility_concern = None
        convective_warning = None

    days = _group_by_day(hour_states, scores)
    parts: list[str] = []

    for date_obj, day_states, day_scores in days[:5]:
        if not day_states:
            continue
        dag = _DAY_ABBR[date_obj.weekday()]

        # "Flat" check: hele dag onder 0.5m → korte regel (mét dagafkorting).
        max_height_day = max(
            s.wave_spectrum.significant_height_total for s in day_states
        )
        if max_height_day < 0.5:
            parts.append(
                f"Nwijk {dag}: flat — tot {round(max_height_day * 100)}cm, te klein."
            )
            continue

        # Peak-hour (= hoogste-golf-uur in daglicht, score > 0)
        daylight = [j for j, sc in enumerate(day_scores) if sc.total_score > 0]
        if daylight:
            peak_idx = max(
                daylight,
                key=lambda j: day_states[j].wave_spectrum.significant_height_total,
            )
        else:
            peak_idx = max(
                range(len(day_states)),
                key=lambda j: day_states[j].wave_spectrum.significant_height_total,
            )
        ps = day_states[peak_idx]
        spectrum = ps.wave_spectrum
        dom = max(spectrum.peaks, key=lambda p: p.height_m) if spectrum.peaks else None
        h = round(spectrum.significant_height_total, 1)
        p_s = round(dom.period_s if dom else spectrum.mean_period, 1)
        wave_dir = degrees_to_compass(
            dom.direction_deg if dom else spectrum.mean_direction
        )
        wind_dir = degrees_to_compass(ps.wind.direction_deg)
        wind_kn = round(ps.wind.speed_kn)
        peak_hour_str = _fmt_t(ps.timestamp, unit=True)

        # Board-suitability (uit scoring) of fallback-heuristiek.
        if recommend_boards is not None:
            boards = recommend_boards(
                hs_m=spectrum.significant_height_total,
                tp_s=(dom.period_s if dom else spectrum.mean_period) or 0.0,
                wind_speed_kn=ps.wind.speed_kn,
                wind_direction_deg=ps.wind.direction_deg,
            )
        else:
            # Simpele heuristiek: shortboard alleen bij Hs > 1.0 en Tp > 6.
            boards = []
            if spectrum.significant_height_total >= 0.3:
                boards.append('longboard')
            if spectrum.significant_height_total >= 0.4:
                boards.append('midlength')
            if spectrum.significant_height_total >= 0.5:
                boards.append('fish')
            if spectrum.significant_height_total >= 1.0 and (
                (dom.period_s if dom else spectrum.mean_period) >= 6
            ):
                boards.append('shortboard')

        # Windows op deze dag (aaneengesloten rijdbare spans).
        day_windows = [
            w for w in windows
            if day_states[0].timestamp <= w.peak_hour <= day_states[-1].timestamp
        ]
        window_strs: list[str] = []
        if day_windows:
            for w in sorted(day_windows, key=lambda w: w.start)[:3]:
                # Sla nul-lengte vensters over (start==end) → die lezen als
                # "6-6u"; val voor die dag terug op één piekmoment.
                if w.end <= w.start:
                    continue
                window_strs.append(f"{_fmt_t(w.start)}-{_fmt_t(w.end, unit=True)}")
        venster = " ook ".join(window_strs) if window_strs else None

        # Verdict-lead afgeleid uit board-aanbeveling.
        if not boards:
            verdict = "niet aan beginnen"
        elif 'shortboard' in boards:
            verdict = "alles werkt"
        elif 'fish' in boards:
            verdict = "surfbaar (long/mid/fish)"
        elif 'midlength' in boards:
            verdict = "surfbaar (long/mid)"
        else:
            verdict = "longboard"

        # Verdict + venster VOORAAN; één los tijdstip alleen op dagen zonder
        # rijdbaar venster (conform format-voorkeur: venster > piekmoment).
        if venster:
            if verdict == "niet aan beginnen":
                verdict = "surfbaar"
            head = f"{verdict} {venster}, top rond {peak_hour_str}"
        elif verdict == "niet aan beginnen":
            head = f"niet aan beginnen, max rond {peak_hour_str}"
        else:
            head = f"{verdict} rond {peak_hour_str}"

        # Suffix-flags: springtij, zicht, onweer.
        suffix = ""
        if ps.tide.daily_range_m is not None and ps.tide.daily_range_m >= 2.0:
            suffix += " (springtij)"
        if visibility_concern is not None:
            vc = visibility_concern(
                ps.visibility_m, ps.dew_point_c, ps.air_temperature_c
            )
            if vc == 'haarmist_risico':
                suffix += " (! mist mogelijk)"
            elif vc == 'dichte_mist':
                suffix += " (! dichte mist)"
        if convective_warning is not None and convective_warning(
            ps.cape_jkg, ps.lifted_index
        ):
            suffix += " (! onweer-risico)"

        wind_marker = " sterk" if wind_kn >= 18 else ""
        numbers = f"{h}m {wave_dir} {p_s}s, wind {wind_kn}kn {wind_dir}{wind_marker}"
        parts.append(f"Nwijk {dag}: {head} — {numbers}{suffix}.")

    body = "\n".join(parts) if parts else "Nwijk: geen data."
    return f"{body}\nCam: surfweer.nl/webcams/noordwijk/"
