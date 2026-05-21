"""
Deterministische fallback-templates voor SMS-berichten — gebruikt als de
Anthropic API faalt of geen API-key beschikbaar is.

Hier mag geen Claude-call zitten. Doel is: ALTIJD een nuttig bericht produceren,
ook bij volledige LLM-uitval, met dezelfde data-velden als de LLM zou krijgen.
"""
from datetime import datetime

from src.data.models import (
    AlertCandidate,
    HourState,
    ScoreBreakdown,
    SurfWindow,
)

from .sms_formatting import (
    degrees_to_compass,
    wind_label_for_noordwijk,
)
from .sms_input import _group_by_day


def _fallback_alert_template(alert: AlertCandidate) -> str:
    if not alert.window:
        return f"NWIJK ALERT: {alert.explanation}. Cam: surfweer.nl/webcams/noordwijk/"
    time_str = f"{alert.window.start.strftime('%H:%M')}-{alert.window.end.strftime('%H:%M')}u"
    trigger_str = ", ".join([t.value for t in alert.window.triggers]) or "goede condities"
    return (f"NWIJK ALERT {alert.detection_time.strftime('%d-%m')} {time_str}: "
            f"{alert.window.peak_score}/100, {trigger_str}. "
            f"Cam: surfweer.nl/webcams/noordwijk/")


def _fallback_digest_template(
    hour_states: list[HourState],
    scores: list[ScoreBreakdown],
    windows: list[SurfWindow],
) -> str:
    """
    Deterministische 4-daagse digest met rijke context — fallback bij LLM-faal.

    Per dag wordt opgenomen:
      - peak_hour conditions (golf, periode, windrichting+snelheid)
      - board-suitability (via recommend_boards; fallback: heuristiek)
      - venster-grenzen indien aanwezig + multi-window join met "ook"
      - springtij-flag per dag (daily_range_m >= 2.0)
      - visibility-concern (mist) en convective_warning (onweer)
      - "flat" wanneer hele dag < 0.5m
    """
    if not hour_states or not scores:
        return (
            "Surfweerbericht: geen data beschikbaar. "
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
    now = datetime.now()
    date_today = now.strftime("%-d-%-m-%Y")

    labels = ["Vandaag", "Morgen", "Overmorgen", "+3"]
    parts: list[str] = []

    for i, (date_obj, day_states, day_scores) in enumerate(days[:4]):
        if not day_states:
            continue
        label = labels[i] if i < len(labels) else date_obj.strftime("%a %d/%m")

        # "Flat" check: hele dag onder 0.5m → korte regel.
        max_height_day = max(
            s.wave_spectrum.significant_height_total for s in day_states
        )
        if max_height_day < 0.5:
            parts.append(f"{label} flat.")
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
        wind_label_for_noordwijk(ps.wind.direction_deg)
        peak_hour_str = ps.timestamp.strftime("%-Hu")

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

        if not boards:
            board_str = "niet aan beginnen"
        elif 'shortboard' in boards:
            board_str = "alles werkt"
        elif 'fish' in boards:
            board_str = "long, mid en fish"
        elif 'midlength' in boards:
            board_str = "long en mid"
        else:
            board_str = "alleen longboard"

        # Windows op deze dag (chosen + others).
        day_windows = [
            w for w in windows
            if day_states[0].timestamp <= w.peak_hour <= day_states[-1].timestamp
        ]
        window_strs: list[str] = []
        if day_windows:
            # Sort op start_time voor logische volgorde
            sorted_w = sorted(day_windows, key=lambda w: w.start)
            for w in sorted_w[:3]:  # max 3 vensters benoemen
                window_strs.append(
                    f"{w.start.strftime('%H:%M')}-{w.end.strftime('%H:%M')}"
                )
            # Multi-window join: "14-16u ook 19:30-21u"
            if len(window_strs) >= 2:
                venster = window_strs[0] + " ook " + " ook ".join(window_strs[1:])
            else:
                venster = window_strs[0]
        else:
            venster = None

        # Springtij-flag per dag.
        spring_suffix = ""
        if ps.tide.daily_range_m is not None and ps.tide.daily_range_m >= 2.0:
            spring_suffix = " (springtij)"

        # Visibility-concern flag.
        vis_suffix = ""
        if visibility_concern is not None:
            vc = visibility_concern(
                ps.visibility_m, ps.dew_point_c, ps.air_temperature_c
            )
            if vc == 'haarmist_risico':
                vis_suffix = " (! mist mogelijk)"
            elif vc == 'dichte_mist':
                vis_suffix = " (! dichte mist)"

        # Convective warning.
        conv_suffix = ""
        if convective_warning is not None:
            if convective_warning(ps.cape_jkg, ps.lifted_index):
                conv_suffix = " (! onweer-risico)"

        # Wind sterk-marker bij ≥18kn.
        wind_strength_marker = " (sterk)" if wind_kn >= 18 else ""

        base = (
            f"{label} rond {peak_hour_str}: {h}m, {p_s}s {wave_dir}, "
            f"wind {wind_kn}kn {wind_dir}{wind_strength_marker}"
        )
        if venster:
            base += f" — {board_str}, venster {venster}"
        else:
            base += f" — {board_str}"
        base += spring_suffix + vis_suffix + conv_suffix + "."
        parts.append(base)

    body = "\n".join(parts) if parts else "geen data."
    return (
        f"Surfweerbericht van {date_today}:\n{body}\n"
        f"Cam: surfweer.nl/webcams/noordwijk/"
    )
