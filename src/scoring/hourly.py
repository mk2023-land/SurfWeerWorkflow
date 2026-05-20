"""
Per-uur scoring module.
Berekent scores voor golf, wind, tij en swell richting.
"""
import logging
from datetime import datetime
from typing import Optional
import math

from src.util import to_utc

from src.data.models import (
    HourState,
    ScoreBreakdown,
    WaveSpectrum,
    SpectralPeak,
    SwellType
)

from src.config import (
    NOORDWIJK,
    SCORING_WEIGHTS,
    SURF_MINIMUMS,
    WIND_DIRECTIONS,
    PARTITION_WEIGHTS,
    TIDE_FLANK,
    DIURNAL_WIND_DECAY,
    WIND_SPREAD_THRESHOLDS,
    PIER_REFRACTION,
    SIZE_CAP_AGGREGATION,
)


def wave_energy_flux(hs_m: float, te_s: float) -> float:
    """
    Wave energy flux per meter golfkam, in kW/m.

    Standaard fysica-formule: P = (ρg²/64π) · Hs² · Te ≈ 0.49 · Hs² · Te.
    Pro-forecasters (Stormsurf, Surf-Forecast.com) gebruiken dit als de
    ECHTE size-metric die periode én hoogte fysisch correct combineert.

    Voorbeelden voor referentie:
        1.0m @ 5s windswell  →  2.45 kW/m  (NL marginaal)
        1.0m @ 7s windswell  →  3.43 kW/m  (NL OK-dag, referentie)
        1.0m @ 10s groundswell  → 4.90 kW/m
        1.5m @ 9s   →  9.92 kW/m  (kwaliteits-dag)
        2.0m @ 12s  →  23.5 kW/m  (big-day NL)
    """
    return 0.49 * hs_m * hs_m * te_s


def wave_energy_factor(hs_m: float, te_s: float, reference_kw_m: float = 3.43) -> float:
    """
    Multiplier op golf_score gebaseerd op wave energy flux relatief aan
    referentie-conditie (1.0m @ 7s = NL OK-dag ≈ 3.43 kW/m).

    Vermenigvuldigingsfactor in [0.75, 1.20] — bewust mild zodat de
    bestaande period_factor en height-score niet overruled worden, maar
    wel de "echte power" van een wave correct gewogen wordt naast hoogte
    en periode apart.

    Q1=(c) keuze: multiplier ipv nieuwe component — minst disruptief, behoudt
    bestaande tests, maakt fysisch correct onderscheid tussen 1.4m@4s
    windchop (lage power) en 1.0m@10s groundswell (hoge power).
    """
    if hs_m <= 0 or te_s <= 0:
        return 1.0
    p = wave_energy_flux(hs_m, te_s)
    # Genormaliseerd t.o.v. referentie. log-schaal werkt rustiger dan lineair
    # voor brede ranges. Reference = 1.0; range capped op realistische uitersten.
    ratio = p / reference_kw_m
    # Sigmoid-achtig: 0.5×ref → 0.85, 1× → 1.0, 2× → 1.13, 3× → 1.18
    import math
    factor = 1.0 + 0.15 * math.tanh((ratio - 1.0) / 1.5)
    return max(0.75, min(1.20, factor))


def wave_age(tp_s: float, wind_speed_kn: float) -> float:
    """
    Wave-age: c_p / U10. Maat voor "rijpheid" van wind-zee.

    Fysica:
        c_p = 1.56 · T (m/s, phase velocity diep water)
        U10 = wind op 10m

    Interpretatie:
        > 1.5 : oude swell, schone face (groundswell-territory)
        1.0-1.5: matured wind-zee, surfbaar
        0.83-1.0: borderline, marginal
        < 0.83: jonge wind-zee, choppy, wind voedt nog actief (Pierson-Moskowitz
                fully-developed sea drempel)

    Tobias' empirische cutoff (5s minimum, zie research_tobias_methodology.md)
    is een proxy hiervoor: bij T=5s en 10 m/s wind is wave_age=0.78 → marginal.
    """
    if wind_speed_kn <= 0 or tp_s <= 0:
        return 999.0  # geen wind = wave is "oud" per definitie
    cp = 1.56 * tp_s
    u10_ms = wind_speed_kn / 1.944  # kn → m/s
    return cp / u10_ms


def wave_age_factor(tp_s: float, wind_speed_kn: float) -> float:
    """
    Soft penalty (Q2=(b)) op golf_score gebaseerd op wave-age.

    Curve (genuanceerd na benchmark-tuning — Tobias accepteert wave_age ≈ 0.9
    als longboard, dus penalty bij borderline mag mild zijn; alleen écht
    jonge wind-zee < 0.6 wordt zwaar gestraft):
        wave_age < 0.5  : factor 0.50 (zware chop)
        0.5 - 0.9       : factor 0.50 → 0.92 (opbouwende sea, mild penalty)
        0.9 - 1.2       : factor 0.92 → 1.00 (mature wind-zee)
        >= 1.2          : factor 1.00 (swell-domein, geen extra bonus —
                          groundswell-bonus zit al elders)

    Het effect is asymmetrisch: jong wind-zee wordt gestraft, swell krijgt
    geen bonus (wordt elders al beloond). Dit voorkomt double-counting van
    "lange periode is goed".
    """
    age = wave_age(tp_s, wind_speed_kn)
    if age >= 1.2:
        return 1.00
    if age >= 0.9:
        return 0.92 + (age - 0.9) * (0.08 / 0.30)
    if age >= 0.5:
        return 0.50 + (age - 0.5) * (0.42 / 0.40)
    return 0.50


def iribarren_factor(hs_m: float, tp_s: float, beach_slope: float = 0.02) -> float:
    """
    Iribarren-getal ξ = tan(β) / √(H/L₀) — voorspelt breaker-type.

    Voor NL beachbreaks (tan(β) ≈ 0.02 = 1:50) — voor het overgrote deel van
    de tijd ξ ≈ 0.10-0.20 = spilling. Dat is de "standaard" NL conditie, niet
    een penalty. Alleen extreem mushy of net-plunging krijgt aanpassing.

        ξ < 0.10  : zware mushy → factor 0.93 (mild penalty)
        0.10-0.18 : standaard spilling → factor 0.98
        0.18-0.45 : neigend naar plunging (kwaliteit!) → factor 1.00 → 1.10
        0.45-0.80 : plunging (zeldzaam NL, kwaliteit-event) → factor 1.10
        > 0.80    : surging/collapsing edge case → factor 1.00 (neutraal)

    Effect: kleine wave + lange periode (0.5m@10s groundswell) krijgt
    quality-bonus omdat ξ stijgt; chop (1.4m@4s) blijft "standard NL".

    Bron: Coastal Wiki surf similarity parameter, Wikipedia Iribarren.
    """
    if hs_m <= 0 or tp_s <= 0:
        return 1.00
    L0 = 1.56 * tp_s * tp_s  # diep-water wavelength
    if L0 <= 0:
        return 1.00
    import math
    xi = beach_slope / math.sqrt(hs_m / L0)
    if xi < 0.10:
        return 0.93
    if xi < 0.18:
        return 0.98
    if xi < 0.45:
        return 1.00 + (xi - 0.18) * (0.10 / 0.27)  # linear 1.00 → 1.10
    if xi < 0.80:
        return 1.10
    return 1.00


def wind_spread_confidence(
    speed_std_kn: Optional[float],
    direction_spread_deg: Optional[float],
) -> float:
    """
    Sprint 2 #8 — multiplier op golf_score op basis van model-spread.

    Wanneer ECMWF / KNMI Harmonie / GFS-Wave duidelijk uiteenlopen, is er
    sprake van synoptische onzekerheid (typisch bij frontpassages of
    onbestendig weer). Pro-forecasters wegen dat zwaarder dan een single-
    model output — Tobias' "modellen lopen uiteen, ik laat dit nog even
    liggen" verwoording.

    Spread > 5 kn of > 25° → start penalty (multiplier 1.0 → 0.85 lineair).
    Maximum penalty bereikt bij 12 kn / 60° spread.

    Returns multiplier in [0.85, 1.0]. Bij None / geen spread-info: 1.0.
    """
    if not speed_std_kn and not direction_spread_deg:
        return 1.0

    s_warn = WIND_SPREAD_THRESHOLDS['speed_kn_warning']
    s_max = WIND_SPREAD_THRESHOLDS['speed_kn_max']
    d_warn = WIND_SPREAD_THRESHOLDS['direction_deg_warning']
    d_max = WIND_SPREAD_THRESHOLDS['direction_deg_max']
    min_factor = WIND_SPREAD_THRESHOLDS['min_factor']

    speed_severity = 0.0
    if speed_std_kn is not None and speed_std_kn > s_warn:
        speed_severity = min(1.0, (speed_std_kn - s_warn) / (s_max - s_warn))

    dir_severity = 0.0
    if direction_spread_deg is not None and direction_spread_deg > d_warn:
        dir_severity = min(1.0, (direction_spread_deg - d_warn) / (d_max - d_warn))

    severity = max(speed_severity, dir_severity)
    if severity <= 0:
        return 1.0
    return 1.0 - severity * (1.0 - min_factor)


def angular_spread_deg(directions_deg: list) -> float:
    """
    Bereken de spread tussen meerdere richting-waarden (°), rekening houdend
    met de 360° wrap-around.

    Aanpak: converteer naar eenheidsvectoren, neem het gemiddelde, bepaal
    het mean-direction en de circular standard deviation.
    Returns een hoek-spread in graden (0-180).
    """
    if not directions_deg or len(directions_deg) < 2:
        return 0.0
    rads = [math.radians(d % 360) for d in directions_deg]
    sin_sum = sum(math.sin(r) for r in rads) / len(rads)
    cos_sum = sum(math.cos(r) for r in rads) / len(rads)
    R = math.sqrt(sin_sum ** 2 + cos_sum ** 2)  # mean resultant length
    if R <= 0:
        return 180.0
    # Circular std-dev in radians: sqrt(-2 ln R). Capped voor numerieke stabiliteit.
    R = min(R, 0.999999)
    circ_std_rad = math.sqrt(-2.0 * math.log(R))
    return math.degrees(circ_std_rad)


def diurnal_wind_decay_kn(
    timestamp: datetime,
    wind_speed_kn: float,
    cloud_cover_pct: Optional[float],
) -> float:
    """
    Sprint 2 #12 — diurnal sea-breeze decay rond zonsondergang.

    Empirische pro-forecaster regel: bij lage bewolking (<50%) ontstaat
    overdag een land-zee thermische gradiënt die een sea-breeze van 2-3 kn
    op de gemiddelde wind oplegt. Na zonsondergang valt dat thermische
    veld weg (lucht boven land koelt af) en de effectieve wind aan de
    kust daalt. Tobias' "na 19:30 valt de wind weg" verwoordt precies dit
    fenomeen.

    Window: sunset - 2u t/m sunset + 1u, alleen bij cloud_cover < 50%.
    Effect: aftrek van ~2.5 kn op effectieve wind-snelheid (voor scoring,
    niet als display-waarde naar de gebruiker).

    Returns: gecorrigeerde wind_speed_kn (≥ 0).
    """
    if cloud_cover_pct is None or cloud_cover_pct >= DIURNAL_WIND_DECAY['max_cloud_cover_pct']:
        return wind_speed_kn

    from src.scoring.daylight import _sunrise_sunset_utc_hours
    from src.util import to_utc

    dt_utc = to_utc(timestamp)
    _, sunset_utc_h = _sunrise_sunset_utc_hours(dt_utc.date())
    hour_utc = dt_utc.hour + dt_utc.minute / 60.0

    start_h = sunset_utc_h - DIURNAL_WIND_DECAY['hours_before_sunset']
    end_h = sunset_utc_h + DIURNAL_WIND_DECAY['hours_after_sunset']

    if not (start_h <= hour_utc <= end_h):
        return wind_speed_kn

    # Lineair "ramp-up" naar volledige reductie:
    # bij sunset-2: factor 0 (geen reductie nog)
    # bij sunset: factor 1.0 (volle reductie)
    # bij sunset+1: factor 1.0
    if hour_utc < sunset_utc_h:
        ramp = (hour_utc - start_h) / DIURNAL_WIND_DECAY['hours_before_sunset']
    else:
        ramp = 1.0
    reduction = ramp * DIURNAL_WIND_DECAY['speed_reduction_kn']
    return max(0.0, wind_speed_kn - reduction)


def wind_gust_penalty(wind_speed_kn: float, wind_gust_kn: Optional[float]) -> float:
    """
    Penalty op wind_score voor vlagerige condities.

    gust/sustained ratio > 1.3 wijst op turbulente luchtmassa — typisch
    achter een frontpassage, in convectieve buien, of bij thermisch onstabiele
    stratificatie. Tobias' "vlagerig" — wind die geen schone face oplevert
    ondanks gemiddelde snelheid binnen acceptabel bereik.

    Returns: 0 (geen penalty) tot -5 (zware gust-variabiliteit).
    """
    if not wind_gust_kn or wind_speed_kn < 4:
        return 0.0  # geen gust-info of te weinig wind voor zinvolle ratio
    ratio = wind_gust_kn / wind_speed_kn
    if ratio < 1.3:
        return 0.0
    if ratio < 1.5:
        return -1.0 * ((ratio - 1.3) / 0.2) * 2.0  # 0 → -2
    if ratio < 2.0:
        return -2.0 - ((ratio - 1.5) / 0.5) * 3.0  # -2 → -5
    return -5.0  # extreme gust-variabiliteit


def mixed_sea_penalty(
    spectrum: WaveSpectrum,
    angle_threshold_deg: float = 30.0,
    min_height_m: float = 0.4,
) -> tuple:
    """
    Detecteer "mixed sea" — twee swell-componenten uit duidelijk verschillende
    richtingen. Resultaat is een rommelige zee zonder dominante set-richting,
    moeilijker surfbaar dan single-swell sea van zelfde hoogte.

    Wave_direction = totaal (energy-weighted gemiddelde van alle partities).
    Swell_wave_direction = de pure swell-partitie. Als die >30° verschillen
    en beide componenten substantieel zijn (>0.4m elk), is er sprake van
    significant mixed sea.

    Returns: (is_mixed: bool, penalty: float in pt)
        is_mixed=True  → penalty -3.0 op golf_score, en flag voor LLM
        is_mixed=False → (False, 0.0)
    """
    import math
    if not spectrum.peaks or len(spectrum.peaks) < 2:
        return (False, 0.0)
    # Sorteer pieken op hoogte; pak top-2
    sorted_peaks = sorted(spectrum.peaks, key=lambda p: p.height_m, reverse=True)
    p1, p2 = sorted_peaks[0], sorted_peaks[1]
    if p1.height_m < min_height_m or p2.height_m < min_height_m:
        return (False, 0.0)
    # Hoek-verschil tussen twee partities (modulo 360, neem korte zijde)
    raw = abs(p1.direction_deg - p2.direction_deg) % 360
    angle = min(raw, 360 - raw)
    if angle >= angle_threshold_deg:
        return (True, -3.0)
    return (False, 0.0)


def pressure_gradient_factor(pressure_history_hpa: list) -> float:
    """
    Detecteer synoptische storing (front/trog-passage) uit druk-trend.

    |dp/dt| > 1.5 hPa/uur over 3-uurs venster = front passing.
    Tijdens en kort na frontpassage: turbulente luchtmassa, instabiele wind,
    onbetrouwbare wave-forecast — penalty op de output.

    Args:
        pressure_history_hpa: lijst [3u terug, 2u terug, 1u terug, nu] (4 elementen)
                              of korter (dan return 1.0).

    Returns:
        Multiplier 0.85-1.0 op wind_score component.
        0.85 = sterke synoptische storing
        1.00 = stabiel
    """
    if not pressure_history_hpa or len(pressure_history_hpa) < 4:
        return 1.0
    # Lineaire regressie-vrije derivative: gewoon (nu - 3u_terug) / 3
    dp_dt = (pressure_history_hpa[-1] - pressure_history_hpa[0]) / 3.0  # hPa/uur
    abs_grad = abs(dp_dt)
    if abs_grad < 1.5:
        return 1.0
    # Lineaire scaling: 1.5 → 1.0, 4.0 → 0.85
    factor = 1.0 - min(0.15, (abs_grad - 1.5) * (0.15 / 2.5))
    return factor


def _wind_direction_cosine(wind_dir_deg: int, beach_normal_deg: int) -> float:
    """
    Cosinus van de hoek tussen de wind-richting en de pure offshore-richting.

    Pure offshore = anti-beach-normal (beach faces 285° → offshore wind FROM 105°).
    Return value: +1.0 = pure offshore, 0.0 = pure cross-shore, −1.0 = pure onshore.

    Dit vervangt de oude 4-bucket aanpak (offshore/side-offshore/onshore) met een
    continu signaal dat geen kunstmatige sprongen rond 225°/315° heeft.
    """
    offshore_dir = (beach_normal_deg + 180) % 360
    delta_raw = (wind_dir_deg - offshore_dir) % 360
    delta = delta_raw if delta_raw <= 180 else 360 - delta_raw
    return math.cos(math.radians(delta))


def wave_face_quality(wind_speed_kn: float, cos_offshore: float) -> float:
    """
    Multiplier (0.4-1.0) op de effectiviteit van een gegeven wave als surf-target,
    gebaseerd op hoe de wind de wave-face beïnvloedt.

    Fysica (zonder hardcoding van uitkomsten):
    - Pure offshore wind: blaast over de top, houdt de face strak rechtop ("held up").
      Wind tegen de wave-richting in vertraagt het breken iets en clean-t de face.
    - Cross-shore wind: neutraal, geen significante face-vervorming.
    - Onshore wind: blaast met de wave mee, drukt de top om, breekt 'm te vroeg af,
      en creëert chop op de face. Hoe sterker de wind, hoe slechter de face.

    Het effect is multiplicatief op de wave-energy (golf_score) omdat een 1.5m
    chop-wave levert minder rideable face dan een 1.0m clean wave — Tobias' core
    principle "clean beats big".

    Curve:
        wind <3 kn: face_q = 1.0 (geen wind, geen impact)
        per kn onshore-component voegt 0.033 penalty toe, gecapt op 0.60
    """
    if wind_speed_kn < 3:
        return 1.0
    # onshore_component: 0 bij offshore/cross, 1 bij pure onshore
    onshore = max(0.0, -cos_offshore)
    # Effectieve onshore wind in kn: hoeveel kn er recht aanlandig blaast
    onshore_kn = onshore * wind_speed_kn
    # Per kn onshore ~3.3% face quality verlies, totaal cap 0.60
    penalty = min(0.60, 0.033 * onshore_kn)
    return 1.0 - penalty


def wind_trend_factor(
    wind_history_kn: list,
    wave_history_m: list,
) -> float:
    """
    Bonus/penalty op basis van wind-trend in de afgelopen 2 uur.

    Fysica: een wave-veld dat is opgebouwd door wind die NU nog blaast, is een
    jonge wind-zee — steile, korte, choppy golven. Als de wind WEGGAAT terwijl
    de wave nog hoog is, klaart de oppervlakte op terwijl de wave-energie blijft —
    dat is Tobias' "wind valt weg, swell loopt door" sweet spot (zijn klassieke
    avond-window patroon, door diurnal wind decay).

    Args:
        wind_history_kn: [wind 2u terug, wind 1u terug, wind nu] (3 elementen)
        wave_history_m: [wave 2u terug, wave 1u terug, wave nu]

    Returns:
        Multiplier ~0.85-1.15
        - 1.15 = wind significant gedaald (≥4 kn in 2u) terwijl wave hoog blijft
        -  1.0 = stabiele wind of irrelevante delta
        - 0.85 = wind sterk gestegen (≥4 kn in 2u) = jonge wind-zee, choppy
    """
    if len(wind_history_kn) < 3 or len(wave_history_m) < 3:
        return 1.0

    wind_delta = wind_history_kn[-1] - wind_history_kn[0]  # nu - 2u terug
    wave_now = wave_history_m[-1]
    wave_max_recent = max(wave_history_m)

    # Wave blijft "hoog" als hij ≥85% van zijn recente piek is
    wave_holding = wave_now >= 0.85 * wave_max_recent

    # Wind drop: -4 kn of meer in 2u én wave nog hoog → clean opening
    if wind_delta <= -4.0 and wave_holding:
        magnitude = min(1.0, abs(wind_delta) / 8.0)  # cap bij 8 kn delta
        return 1.0 + 0.15 * magnitude

    # Wind rise: +4 kn of meer in 2u → jonge wind-zee, chop
    if wind_delta >= 4.0:
        magnitude = min(1.0, wind_delta / 8.0)
        return 1.0 - 0.15 * magnitude

    return 1.0


def recommend_boards(
    hs_m: float,
    tp_s: float,
    wind_speed_kn: float,
    wind_direction_deg: int,
    beach_normal_deg: int = None,
) -> list:
    """
    Geef terug welke board-types op dit moment surfbaar zijn.

    Returns: subset van ['longboard', 'midlength', 'fish', 'shortboard'].
    Lege lijst = niet surfbaar (te klein, te korte periode, of stormwind).

    Categorisering (NL-context, ervaring + Tobias' lexicon):

    - **Longboard** (8'-10'+): drijfvermogen vangt elke knietjeshoge golf.
      Min Hs 0.30m. Tolereert chop en aanlandige wind redelijk.

    - **Midlength** (6'8"-7'10"): brug tussen long en short, iets minder
      forgiving dan longboard. Min Hs 0.40m.

    - **Fish** (5'4"-6'2" breed): houdt van punchy wind-sea, snel paddel.
      Heeft wat power nodig — min Hs 0.50m. Begint te falen bij >25kn wind.

    - **Shortboard** (5'8"-6'4" thruster): wil clean face, voldoende energie.
      Min Hs 1.00m, Tp ≥ 5s. Sterke aanlandige wind (cos ≤ -0.5, >12kn)
      maakt shortboard frustrerend onmogelijk; longboard kan dan nog wel.

    Hard floor: Hs < 0.30m OF Tp < 4.0s OF wind > 28kn = niets.
    """
    from src.config import SURF_MINIMUMS

    if hs_m < SURF_MINIMUMS['min_hs_m']:
        return []
    if tp_s < SURF_MINIMUMS['min_period_s']:
        return []
    if wind_speed_kn > 28:
        return []  # storm-wind, alles plat

    if beach_normal_deg is None:
        beach_normal_deg = NOORDWIJK.beach_normal_deg

    cos_offshore = _wind_direction_cosine(wind_direction_deg, beach_normal_deg)

    boards = []

    # Longboard: laagste lat. Pakt knietjeshoge golven, drijfvermogen wint.
    if hs_m >= SURF_MINIMUMS['min_hs_longboard_m']:
        boards.append('longboard')

    # Midlength: iets meer hoogte nodig dan longboard.
    if hs_m >= SURF_MINIMUMS['min_hs_midlength_m']:
        boards.append('midlength')

    # Fish: wil echte energie + niet té veel wind (anders gewoon rommel).
    if hs_m >= SURF_MINIMUMS['min_hs_fish_m'] and wind_speed_kn < 25:
        boards.append('fish')

    # Shortboard: strenge criteria — moet écht werken.
    # Min hoogte 1.0m, min periode 5s, EN clean face (geen significante onshore).
    # cos > -0.3 = max side-onshore acceptabel. Pure cross-shore (cos=0) of meer
    # offshore is prima; alles wat 30%+ onshore is, ruïneert de wave face voor
    # shortboard maar laat longboard wel werken.
    # Uitzondering: lichte wind (≤10kn) kan zelfs bij onshore nog shortboard
    # toelaten omdat de chop minimaal is.
    if (hs_m >= SURF_MINIMUMS['min_hs_shortboard_m']
            and tp_s >= SURF_MINIMUMS['min_period_shortboard_s']):
        if cos_offshore > -0.3 or wind_speed_kn <= 10:
            boards.append('shortboard')

    return boards

from src.scoring.deconstruct import (
    decompose_spectrum,
    has_groundswell_through_windsea,
    is_clean_swell
)
from src.scoring.daylight import is_daylight_noordwijk

logger = logging.getLogger(__name__)


def _period_factor(period_s: float) -> float:
    """
    Continue periode-multiplier voor golf-score.

    Vervangt de oude binaire type_multiplier (0.8/1.0/1.2 op basis van
    SwellType bucket). Tobias' eigen uitleg: "vanaf 5s wordt het pas een
    beetje surfbaar; ideaal is 6,5-7s omdat dan niet te veel energie
    verloren gaat over de zandbanken". Voor groundswell (≥9s) extra premium.

    Curve:
      <4s    : 0.60  (te kort, choppy/onsurfbaar)
      4-5s   : 0.60 → 0.75 (wind sea)
      5-6s   : 0.75 → 0.85
      6-7s   : 0.85 → 1.00 (NL sweet spot wind-swell)
      7-9s   : 1.00 → 1.15 (kwaliteits-windswell)
      9-13s  : 1.15 (groundswell plateau)
      13-17s : 1.15 → 0.95 (te lang, beachbreak closeout)
      >17s   : 0.90
    """
    T = period_s
    if T < 4:
        return 0.60
    if T < 5:
        return 0.60 + (T - 4) * 0.15
    if T < 6:
        return 0.75 + (T - 5) * 0.10
    if T < 7:
        return 0.85 + (T - 6) * 0.15
    if T < 9:
        return 1.00 + (T - 7) * 0.075
    if T < 13:
        return 1.15
    if T < 17:
        return 1.15 - (T - 13) * 0.05
    return 0.90


def partition_energy_components(wave_spectrum: WaveSpectrum) -> dict:
    """
    Per-partition wave-energy decompositie (#10).

    Splitst de spectrale energie naar swell-component (groundswell + lange
    wind-swell) en wind-zee-component. Open-Meteo Marine leverde al beide
    apart op (`swell_wave_*` en `wind_wave_*`), maar de oude scoring gebruikte
    alleen `significant_height_total` — daardoor werd een 1,0m wave-veld dat
    bestond uit 0,7m clean swell + 0,7m wind-chop identiek gescoord als
    1,0m pure wind-chop.

    Returns dict met:
      swell_energy_kwm     : kW/m wave-energy flux voor swell-partitie
      wind_sea_energy_kwm  : idem voor wind-zee-partitie
      swell_height_m       : Hs van swell-partitie (0 als geen swell)
      wind_sea_height_m    : Hs van wind-zee-partitie (0 als geen wind-zee)
      dominant_period_s    : Tp van zwaarste partitie (voor period_factor)
      effective_height_m   : "rideability-gewogen" totale hoogte (sqrt(E)),
                             dit is de hoogte die we als basis voor de
                             height-score gebruiken — wind-zee draagt met
                             0.65 multiplier bij, swell met 1.0.
    """
    decomposition = decompose_spectrum(wave_spectrum)

    # Swell-partitie = groundswell + wind_swell (alles ≥7s)
    swell_h = 0.0
    swell_T = 0.0
    if decomposition['ground_swell']:
        gs = decomposition['ground_swell']
        # Wanneer ground + wind_swell beide bestaan: combineer kwadratisch
        if decomposition['wind_swell']:
            ws_swell = decomposition['wind_swell']
            swell_h = math.sqrt(gs.height_m ** 2 + ws_swell.height_m ** 2)
            # Energy-weighted average period — zwaarder gewicht op grootste
            e_gs = gs.height_m ** 2 * gs.period_s
            e_ws = ws_swell.height_m ** 2 * ws_swell.period_s
            swell_T = (e_gs + e_ws) / (gs.height_m ** 2 + ws_swell.height_m ** 2) if (gs.height_m + ws_swell.height_m) > 0 else 0
        else:
            swell_h = gs.height_m
            swell_T = gs.period_s
    elif decomposition['wind_swell']:
        ws = decomposition['wind_swell']
        swell_h = ws.height_m
        swell_T = ws.period_s

    wind_h = 0.0
    wind_T = 0.0
    if decomposition['wind_sea']:
        wsea = decomposition['wind_sea']
        wind_h = wsea.height_m
        wind_T = wsea.period_s

    swell_energy = wave_energy_flux(swell_h, swell_T) if swell_h > 0 and swell_T > 0 else 0.0
    wind_energy = wave_energy_flux(wind_h, wind_T) if wind_h > 0 and wind_T > 0 else 0.0

    # Effectieve hoogte: gewogen kwadratische som per partitie. Sqrt(E_total)
    # met E_total = swell_E + wind_E × multiplier.
    # Wind-zee multiplier 0.65 betekent: 1m wind-chop telt voor scoring als
    # 0.81m equivalente swell-hoogte (sqrt(0.65) ≈ 0.806).
    swell_e_h_sq = swell_h ** 2 * PARTITION_WEIGHTS['swell_multiplier']
    wind_e_h_sq = wind_h ** 2 * PARTITION_WEIGHTS['wind_sea_multiplier']
    eff_height = math.sqrt(swell_e_h_sq + wind_e_h_sq)

    # Fallback: bij geen partities (legacy data of test-fixture zonder peaks)
    # gebruiken we significant_height_total met multiplier 0.80 — neutraal
    # tussen pure swell (1.0) en wind-zee (0.65). Voorkomt dat een spectrum
    # zonder peaks een 0-score krijgt terwijl Hs wel rapporteert.
    if eff_height < 0.01 and wave_spectrum.significant_height_total > 0:
        eff_height = wave_spectrum.significant_height_total * 0.90

    # Dominante periode: pak de partitie met grootste energy contributie
    if swell_energy >= wind_energy and swell_T > 0:
        dominant_T = swell_T
    elif wind_T > 0:
        dominant_T = wind_T
    elif wave_spectrum.peaks:
        dominant = max(wave_spectrum.peaks, key=lambda p: p.height_m)
        dominant_T = dominant.period_s
    else:
        dominant_T = wave_spectrum.mean_period or 5.0

    return {
        'swell_energy_kwm': swell_energy,
        'wind_sea_energy_kwm': wind_energy,
        'swell_height_m': swell_h,
        'swell_period_s': swell_T,
        'wind_sea_height_m': wind_h,
        'wind_sea_period_s': wind_T,
        'dominant_period_s': dominant_T,
        'effective_height_m': eff_height,
    }


def score_golf_component(wave_spectrum: WaveSpectrum) -> float:
    """
    Bereken golf score (max SCORING_WEIGHTS['golf_max']).

    Sprint 2 partition-aware refactor (#10):
    - Hoogte-basis = `effective_height_m` uit `partition_energy_components`,
      d.w.z. swell + 0.65×wind-zee gewogen kwadratisch. Een 1m wind-chop
      veld scoort lager dan 1m clean swell.
    - Periode-factor uit zwaarste partitie (swell > wind_sea bij gelijke E).
    - Ridersguide-regel: secundaire goed-georiënteerde swell verhoogt
      `effective_height_m` boven dominant_height — een 1.0m wind-chop met
      0.5m@8s NW swell scoort hoger dan pure 1.0m wind-chop.

    Behoudt: T4 groundswell-through-windsea bonus, clean swell bonus.
    """
    decomposition = decompose_spectrum(wave_spectrum)
    partitions = partition_energy_components(wave_spectrum)
    eff_height = partitions['effective_height_m']

    if eff_height < 0.5:
        height_score = 0
    elif eff_height < 1.0:
        height_score = (eff_height - 0.5) * 40
    elif eff_height < 2.0:
        height_score = 20 + (eff_height - 1.0) * 20
    else:
        height_score = 40

    T = partitions['dominant_period_s']

    height_score *= _period_factor(T)

    # T4 bonus: groundswell DOOR de windsea heen (Tobias' iconische pattern).
    # Voorheen +1pt — veel te bescheiden. Een 1.4m groundswell op 100mhz die
    # door windgolven heen komt is HET ALERT-paradigma. Per benchmark-onderzoek:
    # bonus moet 8-12pt zijn mits substantiële groundswell-dominantie.
    decomp = decompose_spectrum(wave_spectrum)
    if decomp.get('ground_swell') and decomp.get('wind_sea'):
        gs = decomp['ground_swell']
        ws = decomp['wind_sea']
        # Strenge T4-criteria: groundswell substantieel + dominant qua hoogte
        gs_substantial = gs.height_m >= 0.7 and gs.period_s >= 9.0
        gs_dominant = gs.height_m >= 0.6 * ws.height_m
        if gs_substantial and gs_dominant:
            t4_bonus = 8.0
            # Extra +4 voor lange-periode groundswell (≥11s, zeldzaam in NL)
            if gs.period_s >= 11.0:
                t4_bonus += 4.0
            height_score += t4_bonus
        elif has_groundswell_through_windsea(wave_spectrum):
            # Compromise: groundswell + windsea aanwezig maar niet dominant.
            # Minor bonus, was de oude default.
            height_score += 1

    if is_clean_swell(wave_spectrum):
        height_score += 1

    return min(SCORING_WEIGHTS['golf_max'], height_score)


def score_wind_component(wind_speed_kn: float, wind_direction_deg: int) -> float:
    """
    Bereken wind score (max SCORING_WEIGHTS['wind_max'], default 32).

    Speed + direction worden ADDITIEF gecombineerd, niet multiplicatief.
    Oude versie was te punitief op 15-22 kn (cruciaal NL-bereik):
    een 17 kn ZW wind kreeg score 3 en filterde Tobias' longboard-windows weg.

    Speed-score (max 25, monotoon dalend):
      0-8 kn   : 25      (sweet spot offshore én onshore)
      8-15 kn  : 25 → 17 (toenemend chop)
      15-22 kn : 17 → 8  (rideable, longboard-territorium NL)
      22-30 kn : 8 → 0   (stormwind, surf-onvriendelijk)

    Direction-bonus (additief, ±7):
      pure offshore : +7
      pure cross    : 0
      pure onshore  : -7
      Continu via cosinus, geen sprongen.

    Totaal wordt geclamped op [0, wind_max].
    """
    # Speed-score in segmenten — monotoon dalend, gladde overgangen
    if wind_speed_kn <= 8:
        speed_score = 25.0
    elif wind_speed_kn <= 15:
        speed_score = 25.0 - (wind_speed_kn - 8) * (8.0 / 7.0)       # 25 → 17
    elif wind_speed_kn <= 22:
        speed_score = 17.0 - (wind_speed_kn - 15) * (9.0 / 7.0)      # 17 → 8
    elif wind_speed_kn <= 30:
        speed_score = max(0.0, 8.0 - (wind_speed_kn - 22) * (8.0 / 8.0))  # 8 → 0
    else:
        speed_score = 0.0

    # Direction-bonus: cosinus van hoek t.o.v. pure offshore, schaal ±7
    cos_term = _wind_direction_cosine(wind_direction_deg, NOORDWIJK.beach_normal_deg)
    direction_bonus = 7.0 * cos_term

    return max(0.0, min(SCORING_WEIGHTS['wind_max'], speed_score + direction_bonus))


def tide_flank_bonus(
    tide_level_normalized: float,
    is_rising: bool,
) -> float:
    """
    Sprint 2 #11 — bonus voor mid-tide flank.

    Sweet spot voor de meeste NL beachbreaks volgens pro-forecaster lore
    (zie research_pro_forecaster_methods.md mechanism 7):
    - Mid-tide rising = sandbank krijgt vol energie, zandbanken werken
      optimaal want het water bouwt zich op. Bonus +2pt.
    - Mid-tide falling = ook surfbaar maar zandbanken raken bloot, de wave
      verliest energie sneller. Bonus +1pt.
    - Buiten mid-tide of buiten flanken: 0 bonus.

    Grenzen: 0.40 ≤ norm ≤ 0.70 = "mid-tide" venster.
    """
    if not (TIDE_FLANK['mid_low'] <= tide_level_normalized <= TIDE_FLANK['mid_high']):
        return 0.0
    return TIDE_FLANK['mid_rising_bonus'] if is_rising else TIDE_FLANK['mid_falling_bonus']


def tide_velocity_mh(
    last_turn_time: Optional[datetime],
    next_turn_time: Optional[datetime],
    tide_range_m: Optional[float],
) -> float:
    """
    Schatting van verticale tij-snelheid (m/u) op het huidige moment.

    Halve cyclus duurt ~6.2u (NL). Sinusoïdale beweging tussen kentering en
    kentering, dus piek-snelheid (mid-cycle) = π × range / (2 × half_cycle_h).

    Returns signed float (positief = rising, negatief = falling) als
    `is_rising` apart wordt afgeleid. Hier returnen we de absolute snelheid;
    caller bepaalt richting via `phase`.

    Bij ontbrekende info: returnt 0.0.
    """
    if not (last_turn_time and next_turn_time and tide_range_m):
        return 0.0
    if last_turn_time.tzinfo:
        last = last_turn_time.replace(tzinfo=None)
    else:
        last = last_turn_time
    if next_turn_time.tzinfo:
        nxt = next_turn_time.replace(tzinfo=None)
    else:
        nxt = next_turn_time
    half_cycle_h = (nxt - last).total_seconds() / 3600.0
    if half_cycle_h <= 0:
        return 0.0
    # Piek-snelheid (mid-cycle, sinus-derivative) in m/u
    peak_velocity_mh = math.pi * tide_range_m / (2.0 * half_cycle_h)
    # Approximatie: huidige snelheid varieert sinusoïdaal van 0 → peak → 0.
    # Caller kan via tidal_current_intensity al moment-fractie afleiden.
    # Voor LLM is een gemiddelde benadering voldoende: peak_velocity zelf.
    return peak_velocity_mh


def score_tide_component(
    tide_level_normalized: float,
    tide_phase: str,
    dominant_period_s: float = 8.0,
    tide_range_m: Optional[float] = None,
    hours_to_next_high: Optional[float] = None,
    tidal_current_intensity: float = 0.0,
) -> float:
    """
    Bereken tij score (max 20 punten).

    Periode-afhankelijk optimaal niveau-venster (blok 2):
    - Groundswell T≥9s: venster [0.20, 0.90] — lange swell voelt bodem eerder,
      werkt op breder tij-venster.
    - Wind-swell 7-9s: venster [0.35, 0.85] — middenklasse.
    - Wind-sea T<7s: venster [0.50, 0.90] — korte swell heeft hoger water nodig
      om door te breken op NL-zandbanken.

    Spring/doodtij modulator:
    - Springtij (range ≥ 2.0m): venster krimpt 5% aan beide zijden (sterkere
      cross-shore stroming verkort het surfbare venster).
    - Doodtij (range < 1.6m): venster verbreedt 2.5% (stabieler water).

    Phase bonus +2 voor opgaand (push laag→mid is gunstig op NL-beachbreaks).

    Timing-fit modifier: +1 wanneer we 1-2.5u vóór hoogtij zitten én tij stijgt
    (klassieke "push naar HW" — surfweer-conventie).

    Tidal-current penalty (Tobias' "vloedstroom"): horizontale stroming piekt
    mid-cycle (3u na slack) en is nul op kentering. Sterke stroming maakt het
    moeilijker te peddelen en kort surf-vensters in. Penalty schaalt
    kwadratisch: penalty = -8 · intensity² (max -8pt bij springtij mid-cycle).

    Soft cap blijft op SCORING_WEIGHTS['tide_max'].
    """
    # 1) Bepaal optimaal niveau-venster op basis van dominante periode.
    # Versoepeld na benchmark: Tobias accepteert wind-sea aan LW-kentering
    # (zijn "14-16u" window valt op LW-flank). De oude lo=0.50 voor wind-sea
    # filterde dit weg. Empirisch werkt elke fase tussen 0.30 en 0.85 voor
    # wind-sea op NL beachbreaks — de "voorkeur voor hoger water" is een
    # tendens, geen harde regel.
    if dominant_period_s >= 9:
        lo, hi = 0.20, 0.90
    elif dominant_period_s >= 7:
        lo, hi = 0.30, 0.85
    else:
        lo, hi = 0.35, 0.85

    # 2) Spring/doodtij modulator op het venster
    if tide_range_m is not None:
        if tide_range_m >= 2.0:
            lo += 0.05
            hi -= 0.05
        elif tide_range_m < 1.6:
            lo = max(0.0, lo - 0.025)
            hi = min(1.0, hi + 0.025)

    # 3) Level-score binnen het venster (vlakke max), lineair afval daarbuiten
    if lo <= tide_level_normalized <= hi:
        level_score = 18.0
    elif tide_level_normalized < lo:
        level_score = (tide_level_normalized / lo) * 17 if lo > 0 else 0.0
    else:
        level_score = ((1.0 - tide_level_normalized) / (1.0 - hi)) * 17 if hi < 1 else 0.0

    # 4) Phase-bonus voor opgaand tij
    phase_bonus = 2.0 if tide_phase == "opgaand" else 0.0

    # 5) Timing-fit: kleine bonus voor "push naar HW" (opgaand én 1-2.5u vóór HW)
    timing_bonus = 0.0
    if (tide_phase == "opgaand"
            and hours_to_next_high is not None
            and 1.0 <= hours_to_next_high <= 2.5):
        timing_bonus = 1.0

    # 6) Tidal-current penalty: piekt mid-cycle, nul op kentering.
    # Squared om de hoek mid-cycle te accentueren — kentering-flanks krijgen
    # nauwelijks penalty, mid-cycle krijgt vol penalty.
    current_penalty = -8.0 * (tidal_current_intensity ** 2)

    # 7) Tide-flank bonus (#11): mid-rising sweet spot voor NL beachbreaks.
    # Geldt alleen binnen het mid-tide niveau-venster (0.40-0.70 standaard),
    # ongeacht period_factor venster. Rising krijgt vol +2, falling +1.
    is_rising = (tide_phase == "opgaand")
    flank_bonus = tide_flank_bonus(tide_level_normalized, is_rising)

    total = level_score + phase_bonus + timing_bonus + current_penalty + flank_bonus
    return max(0.0, min(SCORING_WEIGHTS['tide_max'], total))


def pier_transmission_factor(swell_direction_deg: int, period_s: float = 7.0) -> float:
    """
    Sprint 2 #9 — continue refractie-factor voor pier-shadow.

    Vervangt de oude binaire `blocked_swell_dir_min..max` knip. Pier-shadow
    center op ~10° NNO (geometrisch tussen IJmuiden-pier en Noordwijk).
    Buiten ±25° voorbij shadow center: vrijwel geen blokkade.

    Gaussian-achtige transmission curve:
        T(dir) = T_min + (T_max - T_min) × (1 - exp(-(Δ/σ)²))
    waarbij Δ = angular afstand tot shadow center, σ = shadow_half_width_deg.

    Periode-modifier: lange-periode swell (Tp ≥ 10s) refracteert beter
    rond obstakels (kleinere k = grotere refractie-radius). Bij Tp ≥ 10s
    krijgt de transmissie +15% (gecapt op 1.0).

    Returns:
        Transmission-factor in [PIER_REFRACTION['min_transmission'], 1.0].
        1.0 = geen blokkade. 0.10 = 90% energie geblokkeerd.

    Voorbeelden:
        dir=10° (shadow center), Tp=6s → ~0.10 (zwaar geblokkeerd)
        dir=15° (5° offset), Tp=6s → ~0.34
        dir=25° (15° offset), Tp=6s → ~0.83
        dir=0° (N), Tp=8s → ~0.51 (matig)
        dir=0° (N), Tp=10s → ~0.66 (lange-periode bonus)
        dir=45° (NO), Tp=6s → ~1.00 (vol)
    """
    d = swell_direction_deg % 360
    center = PIER_REFRACTION['shadow_center_deg']
    sigma = PIER_REFRACTION['shadow_half_width_deg']
    t_min = PIER_REFRACTION['min_transmission']
    t_max = PIER_REFRACTION['max_transmission']

    # Korte angular afstand tot shadow center (modulo 360, korte zijde)
    raw = (d - center) % 360
    delta = min(raw, 360 - raw)

    # Gaussian transmission: T → t_min in center, → t_max ver weg
    gaussian = math.exp(-(delta / sigma) ** 2)
    transmission = t_max - (t_max - t_min) * gaussian

    # Lange-periode bonus: groundswell (Tp ≥ 10s) refracteert beter
    if period_s >= 10.0:
        bonus = PIER_REFRACTION['long_period_bonus']
        # Schaal bonus zodat geen reductie áboven 1.0 maar wel echte
        # verlichting in shadow center. Eindwaarde gecapt op 1.0.
        transmission = min(1.0, transmission + bonus * (1.0 - transmission))

    return max(t_min, min(1.0, transmission))


def score_swell_direction_bonus(swell_direction_deg: int, period_s: float = 7.0) -> float:
    """
    Bereken swell richting bonus voor Noordwijk (max 10 punten).

    Sprint 2 #9 vervangt de oude binaire pier-blockade door een continue
    pier-transmission-factor. De richting-bonus (klassieke NL voorkeur
    W/NW/N) wordt vermenigvuldigd met deze factor: bij exact-NNO-swell
    daalt de bonus naar ~10% van de raw waarde; bij N (0°) met lange
    periode komt nog ~50-60% door.

    `period_s` is de zwaarste swell-periode (default 7s als onbekend);
    bij Tp ≥ 10s krijgt de transmissie een refractie-bonus.

    Behoudt de bestaande directionele preferenties; de pier-factor schaalt
    ze tot een fractionele bonus.
    """
    direction = swell_direction_deg % 360

    # Continue pier-transmission (Sprint 2 #9): vervangt binaire blocked-sector
    transmission = pier_transmission_factor(direction, period_s)

    # Raw richtings-preferentie (continue mapping vergelijkbaar met oude versie)
    if 270 <= direction <= 360:
        raw = 10.0
    elif 45 <= direction <= 90:
        raw = 8.0
    elif 0 <= direction <= 45 or 90 <= direction <= 135:
        raw = 5.0
    elif 135 <= direction <= 225:
        raw = 3.0
    else:
        raw = 5.0

    return raw * transmission


def _dominant_period_for_tide(spectrum: WaveSpectrum) -> float:
    """
    Bepaal de dominante swell-periode voor tij-scoring.

    Voorkeur: hoogste spectrale piek; fallback: spectrum.mean_period; bij geen
    info default 8.0 (mid-band — neutraal venster).
    """
    if spectrum.peaks:
        dominant = max(spectrum.peaks, key=lambda p: p.height_m)
        return dominant.period_s
    if spectrum.mean_period and spectrum.mean_period > 0:
        return spectrum.mean_period
    return 8.0


def _hours_until(when: datetime, target: Optional[datetime]) -> Optional[float]:
    """
    Aantal uren tussen `when` en `target` (positief als target in toekomst).
    Naive datetimes (Open-Meteo) worden als Europe/Amsterdam local geïnterpreteerd,
    aware datetimes (RWS) worden naar UTC genormaliseerd — beide consistent.
    """
    if target is None:
        return None
    delta = (to_utc(target) - to_utc(when)).total_seconds() / 3600.0
    return delta if delta >= 0 else None


def score_hour(state: HourState, context: Optional[dict] = None) -> ScoreBreakdown:
    """
    Bereken totale score voor één uur.

    Args:
        state: HourState met alle data
        context: optioneel dict met sleutels:
            - 'wind_history_kn': lijst [t-2, t-1, t] wind-snelheden in kn
            - 'wave_history_m': lijst [t-2, t-1, t] wave-hoogtes in m
          Beide nodig voor wind_trend_factor (clean-opening detectie).

    Returns:
        ScoreBreakdown met component scores en totaal
    """
    # Daglicht-filter: 's nachts surfen is op Noordwijk niet zinvol. Score = 0
    # zodat night-uren niet in surf-windows of als piek-uren verschijnen.
    if not is_daylight_noordwijk(state.timestamp):
        return ScoreBreakdown(
            timestamp=state.timestamp,
            golf_score=0.0,
            wind_score=0.0,
            tide_score=0.0,
            swell_dir_bonus=0.0,
        )

    # FYSIEKE MINIMUM-GATE: onder een absolute Hs- of Tp-drempel is GEEN bord
    # in NL surfbaar — ongeacht hoe goed de wind, het tij of de richting is.
    # Voorheen liet de combinatie "perfect wind + perfect tij + clean_swell-bonus"
    # een 0,16m wave-hour tot score 60 oplopen (= 'surfable'). Dit is fysiek
    # onmogelijk — Tobias noemt zo'n dag "windhoogte 20cm, rimpelsurf, niets aan".
    # De gate zet ALLES op 0 zodat het uur niet in windows of als piek verschijnt.
    Hs = state.wave_spectrum.significant_height_total
    Tp = _dominant_period_for_tide(state.wave_spectrum)
    if Hs < SURF_MINIMUMS['min_hs_m'] or Tp < SURF_MINIMUMS['min_period_s']:
        return ScoreBreakdown(
            timestamp=state.timestamp,
            golf_score=0.0,
            wind_score=0.0,
            tide_score=0.0,
            swell_dir_bonus=0.0,
        )

    # Golf component — basisscore op Hs/Tp/type + opgewaardeerde T4-bonus
    golf_score = score_golf_component(state.wave_spectrum)

    # Wave energy flux multiplier (Q1=(c)): fysisch correcte size-metric die
    # periode en hoogte combineert. Mild ±15-25% effect bovenop bestaande
    # period_factor — beloont echte power (1.0m@10s) boven height-only (1.4m@4s).
    we_factor = wave_energy_factor(Hs, Tp)
    golf_score *= we_factor

    # Wave-age soft penalty (Q2=(b)): cp/U10 < 0.83 = jonge wind-zee = chop.
    # Mature wave (>1.2) krijgt geen extra bonus (zit elders al), maar marginal
    # (0.83-1.0) en jong (<0.83) krijgen schaalbare penalty op golf_score.
    age_factor = wave_age_factor(Tp, state.wind.speed_kn)
    golf_score *= age_factor

    # Iribarren-getal: continue quality-modifier op basis van breaker-type.
    # NL beachbreaks doen meestal mushy spilling; bij lange periode + matige
    # hoogte stijgt ξ richting plunging = kwaliteits-bonus.
    iri_factor = iribarren_factor(Hs, Tp)
    golf_score *= iri_factor

    # Mixed-sea detector: twee swell-componenten uit verschillende richtingen
    # = rommelig, lagere effectieve surfability ondanks dezelfde totaal-Hs.
    is_mixed_sea, mixed_pen = mixed_sea_penalty(state.wave_spectrum)
    if is_mixed_sea:
        golf_score = max(0.0, golf_score + mixed_pen)

    # Wave face quality: wind op de wave-face. Onshore wind degradeert de face
    # ongeacht hoogte. Toegepast als multiplier op golf_score zodat een 1.5m
    # wave-veld onder sterke aanlandige wind minder telt dan een 1.0m wave
    # onder offshore wind — Tobias' "clean beats big" principe.
    cos_offshore = _wind_direction_cosine(
        state.wind.direction_deg, NOORDWIJK.beach_normal_deg
    )
    face_q = wave_face_quality(state.wind.speed_kn, cos_offshore)
    golf_score *= face_q

    # Wind trend: clean opening na wind-decay vs. jonge wind-zee.
    # Alleen toegepast als context met historie is meegegeven.
    if context:
        trend = wind_trend_factor(
            context.get('wind_history_kn') or [],
            context.get('wave_history_m') or [],
        )
        golf_score *= trend

    # Diurnal wind decay (#12): bij lage bewolking + 2u rond sunset valt
    # de thermische sea-breeze-component weg. Aftrek op effectieve wind-
    # snelheid voor scoring (niet voor display). Cloud_cover komt via context
    # mee uit Open-Meteo forecast — fallback: geen decay.
    cloud_cover_pct = None
    if context:
        cloud_cover_pct = context.get('cloud_cover_pct')
    effective_wind_kn = diurnal_wind_decay_kn(
        state.timestamp, state.wind.speed_kn, cloud_cover_pct
    )

    # Wind component (gebruikt effective wind ná diurnal-decay)
    wind_score = score_wind_component(effective_wind_kn, state.wind.direction_deg)

    # Wind-gust ratio penalty: vlagerige wind (gust/sustained > 1.3) = chop
    # op de face, onbetrouwbaar windveld. Tobias' "vlagerig" — extra penalty
    # bovenop normale wind-score. Gust gebruikt actual wind_speed (raw),
    # niet de diurnal-decay versie, omdat de ratio iets zegt over
    # turbulentie en die wijzigt niet door thermische effecten.
    gust_pen = wind_gust_penalty(state.wind.speed_kn, state.wind.gusts_kn)
    wind_score = max(0.0, wind_score + gust_pen)

    # Multi-model wind-spread confidence-penalty (#8): bij hoge spread
    # tussen ECMWF/KNMI/GFS wind-voorspellingen is de wind onzeker en
    # daalt het vertrouwen in de hele forecast. Dit komt vooral voor bij
    # frontpassages. Effect: multiplier op golf_score (niet wind_score)
    # omdat ook de wave-respons indirect onzeker wordt.
    if context:
        spread = context.get('wind_spread') or {}
        conf_mult = wind_spread_confidence(
            spread.get('speed_std_kn'),
            spread.get('direction_spread_deg'),
        )
        if conf_mult < 1.0:
            golf_score *= conf_mult

    # Drukgradiënt-derivative: synoptische storing detectie. Sterke druk-
    # verandering (>1.5 hPa/uur over 3u) = front/trog-passage = instabiele
    # wind die niet door enkelvoudig uurgemiddeld goed gevangen wordt.
    if context:
        pres_factor = pressure_gradient_factor(
            context.get('pressure_history_hpa') or []
        )
        wind_score *= pres_factor

    # Tij component — periode-afhankelijk venster + spring/doodtij + timing-fit
    # + tidal-current penalty (Tobias' "vloedstroom" effect)
    dominant_period_s = _dominant_period_for_tide(state.wave_spectrum)
    hours_to_high = _hours_until(state.timestamp, state.tide.next_high)
    tidal_current = state.tide.tidal_current_intensity(state.timestamp)
    tide_score = score_tide_component(
        state.tide.normalized_level,
        state.tide.phase,
        dominant_period_s=dominant_period_s,
        tide_range_m=state.tide.daily_range_m,
        hours_to_next_high=hours_to_high,
        tidal_current_intensity=tidal_current,
    )

    # Swell richting bonus (gebruik dominant swell richting). Pier-refractie
    # is nu periode-afhankelijk (#9): groundswell refracteert beter rond
    # obstakels dan wind-zee.
    decomposition = decompose_spectrum(state.wave_spectrum)
    swell_dir_deg = 0
    swell_period_s = 7.0

    if decomposition['ground_swell']:
        swell_dir_deg = decomposition['ground_swell'].direction_deg
        swell_period_s = decomposition['ground_swell'].period_s
    elif decomposition['wind_swell']:
        swell_dir_deg = decomposition['wind_swell'].direction_deg
        swell_period_s = decomposition['wind_swell'].period_s
    elif decomposition['wind_sea']:
        swell_dir_deg = decomposition['wind_sea'].direction_deg
        swell_period_s = decomposition['wind_sea'].period_s
    else:
        swell_dir_deg = state.wave_spectrum.mean_direction
        swell_period_s = state.wave_spectrum.mean_period or 7.0

    swell_dir_bonus = score_swell_direction_bonus(swell_dir_deg, period_s=swell_period_s)

    return ScoreBreakdown(
        timestamp=state.timestamp,
        golf_score=golf_score,
        wind_score=wind_score,
        tide_score=tide_score,
        swell_dir_bonus=swell_dir_bonus
    )


def compute_wind_spread_per_hour(model_forecasts: dict) -> list:
    """
    Sprint 2 #8 — bereken per uur de spread tussen meerdere wind-modellen.

    Args:
        model_forecasts: dict van model-naam → lijst van hourly dicts met
            'timestamp', 'wind_speed', 'wind_direction'. Bijvoorbeeld output
            van OpenMeteoClient.fetch_forecast_data.

    Returns:
        Lijst dicts (één per uur, geïndexeerd op tijd van het eerste model):
            {
                'timestamp': datetime,
                'speed_std_kn': float (sample std-dev),
                'speed_max_min_kn': float (range, alternatief),
                'direction_spread_deg': float (circular std),
                'n_models': int,
            }
    """
    if not model_forecasts:
        return []

    # Pak alle modellen, gebruik de eerste als index. Aanname: alle modellen
    # hebben identieke tijd-as (één API call) — Open-Meteo doet dit zo.
    model_names = list(model_forecasts.keys())
    if not model_names:
        return []
    base = model_forecasts[model_names[0]]
    n_hours = len(base)

    out = []
    for i in range(n_hours):
        speeds = []
        directions = []
        for name in model_names:
            ser = model_forecasts.get(name) or []
            if i >= len(ser):
                continue
            row = ser[i]
            sp = row.get('wind_speed')
            di = row.get('wind_direction')
            if sp is not None:
                speeds.append(float(sp))
            if di is not None:
                directions.append(float(di))

        if len(speeds) >= 2:
            mean_sp = sum(speeds) / len(speeds)
            speed_std = math.sqrt(sum((x - mean_sp) ** 2 for x in speeds) / len(speeds))
            speed_range = max(speeds) - min(speeds)
        else:
            speed_std = 0.0
            speed_range = 0.0

        dir_spread = angular_spread_deg(directions) if len(directions) >= 2 else 0.0

        out.append({
            'timestamp': base[i]['timestamp'],
            'speed_std_kn': speed_std,
            'speed_max_min_kn': speed_range,
            'direction_spread_deg': dir_spread,
            'n_models': len(speeds),
        })
    return out


def score_hour_series(
    states: list,
    pressure_series: list = None,
    cloud_cover_series: list = None,
    wind_spread_series: list = None,
) -> list:
    """
    Score een tijdreeks van HourStates met wind-trend EN druk-gradient context.

    Wind-trend (clean-opening detectie) en druk-gradient (synoptische storing)
    beide hebben historie nodig — deze helper bouwt rolling windows en geeft
    die mee aan score_hour.

    Args:
        states: lijst HourStates in chronologische volgorde.
        pressure_series: optionele parallelle lijst druk-waarden (hPa) voor
            elke state. Gebruikt voor pressure_gradient_factor. Als None of
            mismatching length: drukgradient wordt niet toegepast.
        cloud_cover_series: optionele parallelle lijst cloud_cover (%) per
            uur. Gebruikt voor diurnal-wind-decay (#12).
        wind_spread_series: optionele parallelle lijst dicts met spread-
            informatie (output van compute_wind_spread_per_hour). Gebruikt
            voor multi-model confidence-penalty (#8).

    Aanbevolen entrypoint voor multi-uurs scoring; score_hour() blijft
    direct bruikbaar voor single-hour use (zonder trend/gradient).
    """
    scores = []
    have_pressure = (
        pressure_series is not None and len(pressure_series) == len(states)
    )
    have_cloud = (
        cloud_cover_series is not None and len(cloud_cover_series) == len(states)
    )
    have_spread = (
        wind_spread_series is not None and len(wind_spread_series) == len(states)
    )
    for i, state in enumerate(states):
        # Pak vorige 2 uur voor wind/wave trend (3-uurs venster met huidige).
        hist_start = max(0, i - 2)
        hist_states = states[hist_start:i + 1]
        while len(hist_states) < 3:
            hist_states = [hist_states[0]] + hist_states
        wind_hist = [s.wind.speed_kn for s in hist_states]
        wave_hist = [s.wave_spectrum.significant_height_total for s in hist_states]

        # Druk-historie: 4-uurs venster (t-3 t/m t) voor 3-uurs derivative
        ctx = {'wind_history_kn': wind_hist, 'wave_history_m': wave_hist}
        if have_pressure:
            p_start = max(0, i - 3)
            p_hist = list(pressure_series[p_start:i + 1])
            while len(p_hist) < 4:
                p_hist = [p_hist[0]] + p_hist
            ctx['pressure_history_hpa'] = p_hist
        if have_cloud:
            ctx['cloud_cover_pct'] = cloud_cover_series[i]
        if have_spread:
            ctx['wind_spread'] = wind_spread_series[i]

        scores.append(score_hour(state, context=ctx))
    return scores


def calculate_confidence(forecast_sources: dict) -> float:
    """
    Bereken confidence score op basis van model spread.

    Args:
        forecast_sources: Dictionary met forecasts van verschillende modellen

    Returns:
        Confidence score 0.0-1.0
    """
    if len(forecast_sources) <= 1:
        return 1.0  # Geen spread als één model

    # Bereken spread in wind speed en richting
    wind_speeds = []
    wind_directions = []

    for model_name, forecast_data in forecast_sources.items():
        if forecast_data and len(forecast_data) > 0:
            # Gebruik eerste uur als voorbeeld
            first_hour = forecast_data[0]
            if 'wind_speed' in first_hour:
                wind_speeds.append(first_hour['wind_speed'])
            if 'wind_direction' in first_hour:
                wind_directions.append(first_hour['wind_direction'])

    if len(wind_speeds) <= 1:
        return 1.0

    # Bereken standaard deviatie
    wind_speed_std = math.sqrt(sum((x - sum(wind_speeds) / len(wind_speeds)) ** 2 for x in wind_speeds) / len(wind_speeds))

    # Confidence: lage spread = hoge confidence
    # Spread van 0kn = confidence 1.0, spread van 20kn = confidence 0.5
    confidence = max(0.5, 1.0 - (wind_speed_std / 40.0))

    return confidence