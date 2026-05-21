"""
SMS generator module met Claude Haiku.

Bouwt structured-input voor Claude in fysische eenheden (meters, knopen, graden) —
NOOIT scores als golfhoogte/wind doorgeven, dat heeft eerder hallucinaties veroorzaakt
(score 51 werd "51m golfhoogte"). Stijl-template: referentie-forecaster van de referentie-forecaster.

Digest is multi-day (vandaag + 3 dagen vooruit) en bevat per dag de beste window,
piek-condities, tij-richting (opkomend/afgaand) en eerstvolgende hoog/laag, plus
een lokale spring/dood-tij notitie op basis van maan-fase.
"""
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import anthropic

from src.config import ANTHROPIC_CONFIG, NOORDWIJK
from src.util import to_utc
from src.data.models import (
    AlertCandidate,
    HourState,
    ScoreBreakdown,
    SurfWindow,
    SwellType,
)

logger = logging.getLogger(__name__)


_COMPASS_16 = ['N', 'NNO', 'NO', 'ONO', 'O', 'OZO', 'ZO', 'ZZO',
               'Z', 'ZZW', 'ZW', 'WZW', 'W', 'WNW', 'NW', 'NNW']

_DAY_NL_SHORT = ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo']


def degrees_to_compass(deg: float) -> str:
    """Vertaal hoek (graden) naar 16-punts kompasrichting (NL)."""
    idx = int(((deg % 360) + 11.25) / 22.5) % 16
    return _COMPASS_16[idx]


def wind_label_for_noordwijk(wind_dir_deg: int) -> str:
    """Wind-categorie voor Noordwijk: aflandig / zijaflandig / aanlandig / zij-aanlandig."""
    from src.config import WIND_DIRECTIONS
    d = wind_dir_deg % 360
    if WIND_DIRECTIONS['offshore'][0] <= d <= WIND_DIRECTIONS['offshore'][1]:
        return 'aflandig'
    if WIND_DIRECTIONS['side_offshore'][0] <= d <= WIND_DIRECTIONS['side_offshore'][1]:
        return 'zijaflandig'
    if WIND_DIRECTIONS['onshore'][0] <= d <= WIND_DIRECTIONS['onshore'][1]:
        return 'aanlandig'
    return 'zij-aanlandig'


def is_blocked_by_ijmuiden_pier(swell_dir_deg: int) -> bool:
    """True als swell-richting binnen de NNO-sector valt die door IJmuiden-pier wordt afgeschermd."""
    blocked_min = NOORDWIJK.blocked_swell_dir_min
    blocked_max = NOORDWIJK.blocked_swell_dir_max
    if blocked_min == 0 and blocked_max == 0:
        return False
    d = swell_dir_deg % 360
    if blocked_min <= blocked_max:
        return blocked_min <= d <= blocked_max
    return d >= blocked_min or d <= blocked_max


def _hours_to(when: datetime, target: Optional[datetime]) -> Optional[float]:
    """
    Uren tussen `when` en `target` (positief als target in toekomst, anders None).
    Naive timestamps worden als Europe/Amsterdam local geïnterpreteerd (consistent
    met Open-Meteo input), aware timestamps converteren naar UTC.
    """
    if target is None:
        return None
    delta = (to_utc(target) - to_utc(when)).total_seconds() / 3600.0
    return round(delta, 1) if delta >= 0 else None


def peak_block(window) -> Dict:
    """
    Vind de aaneengesloten uren binnen `window` waar de totaal-score binnen 10
    punten van de piek zit. Levert een mini-venster ("14:00-16:00") binnen het
    hoofdvenster ("14:00-19:00") zodat de LLM kan schrijven "14-19 surfbaar,
    piek 14-16u".

    Returns: {"start_time", "end_time", "duration_hours"} of {} als window leeg.
    """
    scores = window.hourly_scores
    if not scores:
        return {}

    peak_total = max(s.total_score for s in scores)
    threshold = peak_total - 10.0

    peak_idx = max(range(len(scores)), key=lambda i: scores[i].total_score)

    left = peak_idx
    while left > 0 and scores[left - 1].total_score >= threshold:
        left -= 1
    right = peak_idx
    while right < len(scores) - 1 and scores[right + 1].total_score >= threshold:
        right += 1

    return {
        "start_time": scores[left].timestamp.strftime("%H:%M"),
        "end_time": scores[right].timestamp.strftime("%H:%M"),
        "duration_hours": right - left + 1,
    }


def _tide_window_quality(tide_norm: float, dominant_period_s: float) -> str:
    """
    Label tij-venster kwaliteit op basis van niveau + dominante periode. Gebruikt
    dezelfde venster-grenzen als score_tide_component zodat tekst en score op
    elkaar aansluiten.

    - "good": binnen optimaal venster (groundswell ruim, wind-sea smal)
    - "fair": net buiten venster — surfen kan maar niet ideaal
    - "poor": ver buiten venster (extreem hoog/laag)
    """
    if dominant_period_s >= 9:
        lo, hi = 0.20, 0.90
    elif dominant_period_s >= 7:
        lo, hi = 0.35, 0.85
    else:
        lo, hi = 0.50, 0.90

    if lo <= tide_norm <= hi:
        return "good"
    # 'Fair' = tot ~30% buiten venster aan dezelfde kant.
    fair_margin = 0.15
    if (lo - fair_margin) <= tide_norm <= (hi + fair_margin):
        return "fair"
    return "poor"


def moon_phase_info(when: datetime) -> Tuple[float, str, bool]:
    """
    Simpele maan-fase berekening (synodische maand 29.53 dagen, referentie nieuwe maan
    2000-01-06 18:14 UTC). Goed genoeg voor "springtij of niet".

    Returns:
        (phase_age_days, label_nl, is_spring_tide).
        is_spring_tide = binnen 2 dagen van nieuwe of volle maan.
    """
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    when_utc = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)
    days = (when_utc - ref).total_seconds() / 86400.0
    age = days % 29.530588
    # Labels per ~3.7-dagen kwart.
    if age < 1.85 or age >= 27.68:
        label = 'nieuwe maan'
    elif age < 5.54:
        label = 'wassende sikkel'
    elif age < 9.23:
        label = 'eerste kwartier'
    elif age < 12.92:
        label = 'wassende maan'
    elif age < 16.61:
        label = 'volle maan'
    elif age < 20.30:
        label = 'afnemende maan'
    elif age < 23.99:
        label = 'laatste kwartier'
    else:
        label = 'afnemende sikkel'
    # Springtij-venster: <2 dagen rond nieuwe maan (0/29.53) of volle maan (14.77).
    distance_new = min(age, 29.530588 - age)
    distance_full = abs(age - 14.765)
    is_spring = distance_new < 2.0 or distance_full < 2.0
    return age, label, is_spring


SYSTEM_PROMPT = """Je schrijft surf-berichten voor Noordwijk in de stijl van referentie-forecaster van
de referentie-forecaster. Lopende zinnen, surfers-jargon mag, géén overdrijving, géén voorbehouden.

═══════════════════════════════════════════════════════════════════════
ANTI-HALLUCINATIE — DIT IS DE BELANGRIJKSTE REGEL
═══════════════════════════════════════════════════════════════════════
Je MAG NOOIT een getal, tijd, richting, hoogte, periode, windsnelheid,
tij-stand of tij-tijdstip noemen dat niet LETTERLIJK in de JSON-input
staat. NOOIT.

- Niet afronden: 0,7m blijft 0,7m, NIET 1m of 0,8m.
- Niet interpoleren tussen waarden.
- Niet "ongeveer" of "rond X" als X niet in de input staat.
- Geen verzonnen tij-tijden, geen verzonnen wind-snelheden, geen
  verzonnen golfhoogtes.
- Geen woorden als "springtij" tenzij `tide_context.spring_tide=true`
  of `tide_summary.spring_neap_label="springtij"` letterlijk in de
  input staat voor die dag.
- Geen "piekt het om HH" tenzij die HH in de input voorkomt als een
  expliciet peak-veld VOOR DIE DAG.

Cite-regel: elke getalwaarde die je in het bericht zet (m, s, kn, °, uur)
MOET met je vinger te vinden zijn in het JSON-input-blok voor díe dag.
Als je twijfelt: laat het getal weg en schrijf kwalitatief
("kleine golfjes", "matige wind").

ELKE dag in `days` heeft een `_allowed_citations` veld dat exact opsomt
welke waarden je voor die dag mag noemen. Houd je daaraan.

═══════════════════════════════════════════════════════════════════════
WELK MOMENT IS DE "PIEK"?
═══════════════════════════════════════════════════════════════════════
Er zijn TWEE verschillende "piek"-begrippen, hou ze uit elkaar:

1. `peak_height_hour` — het uur waarop de golf het HOOGSTE is (m).
   Dit is wat surfers bedoelen met "piek": de moment dat het golfje
   op zijn grootst is. Gebruik dit veld als je iets zegt over
   "hoogste golf vandaag", "piekt op X meter".

2. `best_window.peak_time` — alleen aanwezig als er een echt
   surfvenster is. Dit is het beste-score-uur binnen dat venster.
   Gebruik dit alleen in context van "surfen 14-16u, top om 15u".

NOOIT het hoogste-score uur als "piek" presenteren als dat uur NIET
in een surf-window valt. Een uur kan een hoge score hebben (combinatie
wind+tij) zonder een goede golf te hebben.

═══════════════════════════════════════════════════════════════════════
STIJL & FORMAT — HARDE EISEN
═══════════════════════════════════════════════════════════════════════
- PLAIN TEXT. Geen Markdown headers (#, ##), geen vetgedrukt (**), geen
  bullets, geen scheidingslijnen (---), geen emoji.
- Schrijf SPREEKTAAL met lopende zinnen, geen telegram-stijl. Mag een
  grapje, mag een korte duiding ("wind blijft te hard", "swell loopt af").
- Begin EXACT met "Nwijk [day_label_today]: " (kleine letters voor de dag,
  bv. "Nwijk di: "). Geen tekst ervoor.
- Per dag één alinea van 2-4 zinnen. Dek per dag: golf (hoogte/periode/
  richting), wind (snelheid/richting/label), tij (richting + relevante
  HW/LW), bord-keuze, en eventueel venster of duiding.
- LAYOUT: dagen worden gescheiden door een LEGE REGEL (twee newlines,
  '\\n\\n'). NOOIT alle dagen op één regel met dubbele punten. NOOIT alleen
  een enkele newline. Het bericht moet er zo uitzien:

      Nwijk di: <alinea over dinsdag>

      Nwijk wo: <alinea over woensdag>

      Nwijk do: <alinea over donderdag>

      Nwijk vr: <alinea over vrijdag>

      Cam: surfweer.nl/webcams/noordwijk/

- Lengte: 700-1400 tekens inclusief webcam-regel. Bondig per zin, maar
  géén telegram-stijl en géén afgekapte dagen.

═══════════════════════════════════════════════════════════════════════
BOARDS — HARDE REGEL
═══════════════════════════════════════════════════════════════════════
Elk uur (peak_height_hour, best_window.peak_conditions) heeft een
`boards_suitable` veld. Dit is een lijst uit:
  ['longboard', 'midlength', 'fish', 'shortboard']
Of leeg ([]) bij is_unsurfable=true.

REGEL: noem ALLEEN borden die in dit veld staan. Verzin GEEN borden.

Vertaal als volgt naar tekst (referentie-forecaster-stijl):
- `boards_suitable=[]` of `is_unsurfable=true` → "flat", "rimpelsurf",
  "niet aan beginnen", "wachten op de volgende swell". NOOIT "surfbaar".
- `['longboard']` → "alleen longboard", "longboard-uurtje", "knietjes voor long".
- `['longboard', 'midlength']` → "voor longboard of midlength",
  "long en mid".
- `['longboard', 'midlength', 'fish']` → "voor long, mid en fish",
  "longboard prima, fish kan ook".
- `['longboard', 'midlength', 'fish', 'shortboard']` → "alles werkt",
  "shortboard kan ook", "long en fish, ook shortboard mogelijk".

NOOIT "surfbaar" zeggen zonder bordtype erbij. NOOIT "shortboard" noemen
als 'shortboard' NIET in boards_suitable staat.

═══════════════════════════════════════════════════════════════════════
PER DAG IN `days` (4 dagen)
═══════════════════════════════════════════════════════════════════════

Casus A — `best_window` aanwezig EN `best_window.kind="surfable"`:
- Noem het venster: "start_time-end_time" of "14-16u" stijl.
- Bij duration_hours > 3: noem ook peak_block range.
- Beschrijf condities op peak_time uit `best_window.peak_conditions`.
- Gebruik de `boards_suitable` lijst om bord-aanbeveling te geven.

Casus B — `best_window` aanwezig EN `best_window.kind="longboard"`:
- Schrijf "longboard-uurtje" of "voor long en fish" zoals
  `peak_conditions.boards_suitable` aangeeft (altijd ⊂ niet-shortboard).
- Noem het venster maar maak duidelijk dat shortboard niet ideaal is.

Casus C — GEEN best_window (dag is niet surfbaar):
- NOOIT een tijdblok of "HH:MM-HH:MM" opbouwen.
- Als `peak_height_hour.is_unsurfable=true`: schrijf "flat" / "rimpelsurf"
  / "20cm windhoogte" / "niet aan beginnen". Mag wel het hoogste-golf
  moment noemen met het feit dát het te klein is.
- Combineer peak_height_hour.time NIET met next_high_time/next_low_time
  tot een nep-venster.

MEERDERE WINDOWS — referentie-forecaster' "14-16u of na 19:30u" patroon:
- Naast `best_window` kun je `other_windows[]` krijgen — dit zijn andere
  surfbare blokken op dezelfde dag (bv. middag en avond apart).
- Als er meerdere windows zijn EN ze verschillen ≥2u in starttijd: noem
  ze allebei in referentie-forecaster-stijl met "OF" tussen:
    "Best 06-09u, OF nog later na 18u".
  Of: "voor het middaguurtje 12-14u, OF schoner 18-21u".
- Beoordeel of het zinvol is om alle te noemen: drie verschillende windows
  in één dag noem je alleen als ze duidelijk verschillen in karakter
  (bv. ochtend nog windswell, middag tij-kentering, avond clean opening).

Daarna in alle gevallen:
- Wind: gebruik exact `wind_speed_kn` + `wind_direction_compass` + `wind_label`.
- Tij — verweven in de zin, NIET als window-grens:
  - tide_summary.next_high_time / next_low_time zijn TIJ-EVENTS, geen
    surfvenster-grenzen. Verwoord ze als losse referenties.
  - tide_window_quality="good" → mag je benoemen.

═══════════════════════════════════════════════════════════════════════
EXTRA SIGNALEN
═══════════════════════════════════════════════════════════════════════
- `tide_context.spring_tide=true` voor de hele forecast OF
  `tide_summary.spring_neap_label="springtij"` voor díe dag → noem
  "springtij". Anders NIET noemen.
- `peak_height_hour.swell_refracts_around_ijmuiden=true` → noem pier-blokkade.
- `peak_height_hour.swell_type="groundswell"` → benoem groundswell + periode.
- `model_spread_warning=true` op een dag → noem dat "modellen nog uiteen"
  of "voorspelling nog onzeker" voor díe dag. Niet kwantitatief uitleggen.
- `peak_height_hour.tide_is_rising=true` met `tide_velocity_mh` ≥ 0.4 →
  mag "tij komt stevig op" of "vloed bouwt op" zeggen. Bij is_rising=false
  én velocity ≥ 0.4 → "tij valt nog stevig".
- `confidence_label="laag"` → mag je voorbehouden formuleren ("modellen nog
  onzeker", "spreiding tussen modellen"). Bij "hoog" of "matig": géén
  voorbehoud, schrijf zoals altijd.
- `convective_warning=true` op `peak_height_hour` → noem "onweer-risico"
  (kort, één keer). Anders niet noemen.
- `visibility_concern="haarmist_risico"` → noem "mist mogelijk in de
  ochtend"; `="dichte_mist"` → "dichte mist"; anders niet noemen.
- `precipitation_flag=true` → noem "regen"; `=false` mag eventueel "droog"
  zeggen wanneer dat de toon dient. Geen mm-getal noemen tenzij in
  `_allowed_citations.precipitations_mm`.
- `storm_surge_warning=true` → noem "opzet" of "water staat hoger dan
  astronomisch tij". Anders niet noemen.

═══════════════════════════════════════════════════════════════════════
EENHEDEN — HARDE EIS
═══════════════════════════════════════════════════════════════════════
Gebruik EXACT zoals in input:
- wind: knopen (kn) — NOOIT bft, NOOIT km/u, NOOIT m/s
- golf: meters (m) — niet cm tenzij <0.3m als "20cm windhoogte"
- periode: seconden (s)
- temperatuur: °C
- richting: 16-punts kompas (N/NNO/NO/.../NNW) — NOOIT in graden

Geen "knoop" of "knopen" voluit in cijfer-context: schrijf "12kn", niet
"12 knopen". (Wel: "harde wind" / "stevige bries" als kwalitatieve term.)

═══════════════════════════════════════════════════════════════════════
VOORBEELDEN — referentie-forecaster-stijl (gebruik dit als kalibratie)
═══════════════════════════════════════════════════════════════════════
Voorbeeld 1 (klein windswell-uurtje met longboard):
  Input: 11-13u: 0,9m WNW, 6,5s, wind 12kn ZW zijaflandig, tij opgaand.
  Output-fragment: "Rond 11u zit er 0,9m WNW met 6,5s erop, wind 12kn ZW
  zijaflandig — leuk longboard-uurtje tot 13u."

Voorbeeld 2 (geen swell, alleen rimpel):
  Input: peak 0,2m, periode 3,5s, wind 18kn N aanlandig.
  Output-fragment: "Donderdag flat, swell nihil, windhoogte is 20cm en
  18kn N aanlandig — niet aan beginnen."

Voorbeeld 3 (multi-window: ochtend en avond apart):
  Input: best_window 14-16u 1,1m WNW 7s, wind 8kn ZW. other_windows
  19:30-21u 1,0m WNW 7s, wind 5kn ZZW.
  Output-fragment: "Nwijk/Zvoort 14-16u of na 19:30u, genoeg hoogte rond
  1m WNW, 7s erop — wind 8kn ZW middag, 's avonds 5kn ZZW."

Voorbeeld 4 (volledige layout — let op de lege regels tussen dagen):
  Nwijk di: rond 14u 1,1m WNW met 7s erop, wind 8kn ZW zijaflandig en tij
  loopt nog op tot 16u. Beste venster 14-16u voor long, mid en fish, of
  later 19:30-21u als de wind verder zakt naar 5kn ZZW.

  Nwijk wo: kleinere dag, peak om 11u op 0,7m WNW met 6s, wind 12kn ZW
  zijaflandig. Longboard-uurtje rond hoog water 11-13u, daarna te slap.

  Nwijk do: flat, swell zakt naar 0,2m windhoogte, 18kn N aanlandig —
  niet aan beginnen, lessen zien hooguit rimpel.

  Nwijk vr: nog steeds geen golven, wind 11kn NW aanlandig. Weekend ook
  stil; pas midden volgende week komt er N-swell binnen.

  Cam: surfweer.nl/webcams/noordwijk/

═══════════════════════════════════════════════════════════════════════
STRIKTE REGELS — SAMENVATTING
═══════════════════════════════════════════════════════════════════════
1. Gebruik UITSLUITEND getallen die letterlijk in de JSON-input staan.
   Geen afronden, geen interpoleren, geen "ongeveer".
2. Eindig met " Cam: surfweer.nl/webcams/noordwijk/"
3. Geen "denk ik" / "waarschijnlijk" / "misschien" / emoji / disclaimers.
4. Geen night-uren noemen.
5. Geen verzonnen tij-stand of tij-tijdstip.
6. Geen "springtij" tenzij expliciet zo in input.
7. Score-getallen (0-100) noem je nooit.
8. Geen bft, geen km/u — uitsluitend kn voor wind.
"""


class SMSGenerator:
    """Genereert SMS berichten met Claude Haiku."""

    def __init__(self):
        if not ANTHROPIC_CONFIG['api_key']:
            logger.warning("No Anthropic API key configured, using fallback templates only")
            self.client = None
        else:
            self.client = anthropic.Anthropic(api_key=ANTHROPIC_CONFIG['api_key'])

    # ---------- public API ----------

    def generate_alert_sms(self, alert: AlertCandidate) -> str:
        if not self.client:
            return self._fallback_alert_template(alert)
        try:
            structured_input = self._prepare_alert_input(alert)
            max_tokens = ANTHROPIC_CONFIG.get(
                'max_tokens_alert', ANTHROPIC_CONFIG['max_tokens']
            )
            return (
                self._call_claude(structured_input, max_tokens=max_tokens)
                or self._fallback_alert_template(alert)
            )
        except Exception as e:
            logger.error(f"Failed to generate alert SMS with Claude: {e}")
            return self._fallback_alert_template(alert)

    def generate_digest_sms(
        self,
        hour_states: List[HourState],
        scores: List[ScoreBreakdown],
        windows: List[SurfWindow],
        forecast_summary: Optional[Dict] = None,
        wind_spread_series: Optional[List[Dict]] = None,
    ) -> str:
        if not self.client:
            return self._fallback_digest_template(hour_states, scores, windows)
        try:
            structured_input = self._prepare_digest_input(
                hour_states, scores, windows,
                forecast_summary or {},
                wind_spread_series=wind_spread_series,
            )
            max_tokens = ANTHROPIC_CONFIG.get(
                'max_tokens_digest', ANTHROPIC_CONFIG['max_tokens']
            )
            return (
                self._call_claude(structured_input, max_tokens=max_tokens)
                or self._fallback_digest_template(hour_states, scores, windows)
            )
        except Exception as e:
            logger.error(f"Failed to generate digest SMS with Claude: {e}")
            return self._fallback_digest_template(hour_states, scores, windows)

    # ---------- LLM call ----------

    def _call_claude(
        self,
        structured_input: Dict,
        max_tokens: Optional[int] = None,
    ) -> Optional[str]:
        """
        Anthropic Messages API call met retry voor transient overload + model fallback.

        Strategie:
          1. Probeer primair model (sonnet-4-5) — tot 2x retry bij 529/429.
          2. Bij aanhoudende overload: schakel naar fallback_model (haiku-4-5).
          3. Pas dáárna geef op en laat caller fallback-template gebruiken.

        Sonnet is duurder (~3x Haiku) maar fungeert als primair model voor
        rijkere referentie-forecaster-stijl prose. Haiku is fallback bij overload.

        Backoff respecteert Anthropic's `retry-after` header indien aanwezig
        (RFC 6585): bij 429/529 antwoorden geeft Anthropic vaak een hint over
        hoe lang te wachten. Blinde 2**attempt kan te kort OF onnodig lang zijn.
        """
        import time
        from anthropic._exceptions import OverloadedError, RateLimitError

        effective_max_tokens = max_tokens or ANTHROPIC_CONFIG['max_tokens']

        def _retry_after_seconds(err: Exception, fallback_s: float) -> float:
            """Lees retry-after uit response-headers; val terug op exponential backoff."""
            try:
                resp = getattr(err, 'response', None)
                headers = getattr(resp, 'headers', None) or {}
                # httpx Headers is dict-like; .get() werkt voor beide.
                ra = headers.get('retry-after') if hasattr(headers, 'get') else None
                if ra is not None:
                    return float(int(ra))
            except (TypeError, ValueError, AttributeError):
                pass
            return fallback_s

        def _attempt(model_name: str, retries: int) -> Optional[str]:
            body = {
                "model": model_name,
                "max_tokens": effective_max_tokens,
                "temperature": ANTHROPIC_CONFIG['temperature'],
                "system": SYSTEM_PROMPT,
                "messages": [{
                    "role": "user",
                    "content": json.dumps(structured_input, indent=2, default=str),
                }],
            }
            for attempt in range(retries):
                try:
                    message = self.client.messages.create(**body)
                    sms_text = message.content[0].text.strip()
                    logger.info(
                        f"Generated SMS via {model_name} "
                        f"(max_tokens={effective_max_tokens}): {sms_text[:80]}..."
                    )
                    return sms_text
                except (OverloadedError, RateLimitError) as e:
                    backoff_s = 2 ** (attempt + 1)
                    wait_s = _retry_after_seconds(e, fallback_s=backoff_s)
                    logger.warning(
                        f"{model_name} {type(e).__name__} "
                        f"(retry {attempt + 1}/{retries} na {wait_s}s "
                        f"{'[retry-after]' if wait_s != backoff_s else '[backoff]'})"
                    )
                    if attempt < retries - 1:
                        time.sleep(wait_s)
            return None

        primary_model = ANTHROPIC_CONFIG['model']
        fallback_model = ANTHROPIC_CONFIG.get('fallback_model')

        # Stap 1: primair model, 2 retries
        result = _attempt(primary_model, retries=2)
        if result:
            return result

        # Stap 2: fallback model, 1 retry
        if fallback_model and fallback_model != primary_model:
            logger.warning(
                f"{primary_model} uitgeput → switch naar {fallback_model}"
            )
            result = _attempt(fallback_model, retries=2)
            if result:
                return result

        logger.error(
            "Anthropic API niet beschikbaar — beide modellen overloaded"
        )
        return None

    # ---------- input shaping ----------

    def _prepare_alert_input(self, alert: AlertCandidate) -> Dict:
        input_data: Dict = {
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
        self,
        hour_states: List[HourState],
        scores: List[ScoreBreakdown],
        windows: List[SurfWindow],
        forecast_summary: Dict,
        wind_spread_series: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Multi-day digest: vandaag + 3 dagen vooruit. Per dag: peak_hour-condities,
        beste window (indien surfable), tij-richting + eerstvolgende hoog/laag,
        en springtij-context.

        Sprint 2 #8 — optioneel `wind_spread_series` met inter-model spread per
        uur. Indien aanwezig wordt een dag-level `model_spread_warning` veld
        toegevoegd aan elk day_block zodat de LLM "modellen lopen nog uiteen"
        kan verwoorden.
        """
        days = self._group_by_day(hour_states, scores)
        day_blocks: List[Dict] = []
        labels = ["vandaag", "morgen", "overmorgen", "+3"]

        # Map timestamp → spread-dict voor snelle lookup per dag
        spread_by_ts = {}
        if wind_spread_series:
            for entry in wind_spread_series:
                spread_by_ts[entry['timestamp']] = entry

        for i, (date_obj, day_states, day_scores) in enumerate(days[:4]):
            if not day_states or not day_scores:
                continue
            label = labels[i] if i < len(labels) else date_obj.strftime("%a %d/%m")
            day_block = self._summarize_day(
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
            day_blocks.append(day_block)

        now = datetime.now()
        _, moon_label, is_spring = moon_phase_info(now)

        return {
            "type": "digest",
            "date_today": now.strftime("%Y-%m-%d"),
            "day_label_today": _DAY_NL_SHORT[now.weekday()],
            "days": day_blocks,
            "tide_context": {
                "moon_phase_nl": moon_label,
                "spring_tide": is_spring,
                "spring_tide_label": "springtij" if is_spring else None,
            },
            "forecast_summary": forecast_summary,
            "webcam_url": "https://surfweer.nl/webcams/noordwijk/",
        }

    def _group_by_day(
        self,
        hour_states: List[HourState],
        scores: List[ScoreBreakdown],
    ) -> List[Tuple]:
        """Groepeer (state, score) op kalenderdag in chronologische volgorde."""
        groups: Dict = {}
        for s, sc in zip(hour_states, scores):
            d = s.timestamp.date()
            groups.setdefault(d, ([], []))
            groups[d][0].append(s)
            groups[d][1].append(sc)
        return [(d, *groups[d]) for d in sorted(groups.keys())]

    def _summarize_day(
        self,
        day_states: List[HourState],
        day_scores: List[ScoreBreakdown],
        all_windows: List[SurfWindow],
        date_obj,
        label_nl: str,
    ) -> Dict:
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
        peak_height_score = day_scores[peak_height_idx]

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

        # Alle "andere" windows van de dag (niet de chosen) — referentie-forecaster noemt vaak
        # meerdere vensters op een dag ("14-16u of na 19:30u"). Door deze ook
        # mee te geven kan de LLM dat patroon repliceren.
        other_windows = [w for w in day_windows if w is not chosen_window]

        peak_height_conditions = self._hour_state_to_conditions(peak_height_state)

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

        result: Dict = {
            "label_nl": label_nl,
            "date": date_obj.strftime("%Y-%m-%d"),
            "day_short": _DAY_NL_SHORT[date_obj.weekday()],
            "is_surfable": best_score.total_score >= 60,
            "peak_height_hour": peak_height_conditions,  # hier zit dé golfhoogte-piek
            "tide_summary": self._tide_summary_for_day(day_states, peak_height_state),
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
                "peak_conditions": self._hour_state_to_conditions(peak_state),
            }

        if chosen_window:
            result["best_window"] = _window_payload(chosen_window)
        else:
            result["best_window"] = {"is_surfable": False, "kind": None}

        # Andere windows van de dag (referentie-forecaster' "14-16u of na 19:30u" patroon)
        result["other_windows"] = [_window_payload(w) for w in other_windows]

        # Anti-hallucinatie vangnet — exact wat de LLM mag citeren
        result["_allowed_citations"] = self._build_allowed_citations(
            peak_height_conditions,
            result.get("best_window"),
            result["tide_summary"],
            other_windows=result["other_windows"],
        )

        return result

    def _build_allowed_citations(
        self,
        peak_height_conditions: Dict,
        best_window: Optional[Dict],
        tide_summary: Dict,
        other_windows: Optional[list] = None,
    ) -> Dict:
        """
        Bouw een whitelist van getallen, tijden en richtingen die de LLM voor
        deze dag mag noemen. Wordt ook door SMSValidator gebruikt om
        hallucinaties te detecteren.
        """
        heights_m = {peak_height_conditions["wave_height_m"]}
        periods_s = {peak_height_conditions["wave_period_s"]}
        wind_speeds_kn = {peak_height_conditions["wind_speed_kn"]}
        wind_dirs = {peak_height_conditions["wind_direction_compass"]}
        wave_dirs = {peak_height_conditions["wave_direction_compass"]}
        times_hhmm = {peak_height_conditions["time"]}
        # Uitgebreide whitelist — boei-extras + atmospheric context.
        gusts_kn = {peak_height_conditions.get("wind_gust_kn")}
        air_temps_c = {peak_height_conditions.get("air_temperature_c")}
        ssts_c = {peak_height_conditions.get("sea_surface_temperature_c")}
        precipitations_mm = {peak_height_conditions.get("precipitation_mm")}
        visibilities_m = {peak_height_conditions.get("visibility_m")}

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

    def _hour_state_to_conditions(self, state: HourState) -> Dict:
        """Pak fysische condities uit HourState. Alles in expliciete eenheden."""
        from src.scoring.hourly import (
            recommend_boards,
            tide_velocity_mh,
            convective_warning,
            visibility_concern,
            storm_surge_warning,
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
        # geeft de LLM materiaal om referentie-forecaster-stijl te schrijven ("opkomend tot 14u",
        # "rond hoog water", "afgaand tot 17u laag").
        hours_to_high = _hours_to(state.timestamp, state.tide.next_high)
        hours_to_low = _hours_to(state.timestamp, state.tide.next_low)

        # Sprint 2 #11 — tide-flank features. Tide velocity (m/u) en is_rising
        # boolean geven de LLM materiaal om referentie-forecaster-stijl te schrijven
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

    def _tide_summary_for_day(self, day_states: List[HourState], peak_state: HourState) -> Dict:
        """Eerstvolgende hoog- en laagtij + huidige tij-richting op piek-moment."""
        tide = peak_state.tide
        # next_high/next_low zijn al berekend per HourState; pak de eerste van deze dag.
        next_high = peak_state.tide.next_high
        next_low = peak_state.tide.next_low
        # Daily range geeft springtij-context (≥2.0m = springtij, sterke stroming).
        spring_label = None
        if tide.daily_range_m is not None:
            if tide.daily_range_m >= 2.0:
                spring_label = "springtij"
            elif tide.daily_range_m < 1.6:
                spring_label = "doodtij"
        return {
            "phase_at_peak": tide.phase,                       # opgaand/afgaand/onbekend
            "level_m_at_peak": round(tide.level_m, 2),
            "next_high_time": next_high.strftime("%H:%M") if next_high else None,
            "next_low_time": next_low.strftime("%H:%M") if next_low else None,
            "daily_range_m": round(tide.daily_range_m, 2) if tide.daily_range_m else None,
            "spring_neap_label": spring_label,
        }

    # ---------- fallback templates ----------

    def _fallback_alert_template(self, alert: AlertCandidate) -> str:
        if not alert.window:
            return f"NWIJK ALERT: {alert.explanation}. Cam: surfweer.nl/webcams/noordwijk/"
        time_str = f"{alert.window.start.strftime('%H:%M')}-{alert.window.end.strftime('%H:%M')}u"
        trigger_str = ", ".join([t.value for t in alert.window.triggers]) or "goede condities"
        return (f"NWIJK ALERT {alert.detection_time.strftime('%d-%m')} {time_str}: "
                f"{alert.window.peak_score}/100, {trigger_str}. "
                f"Cam: surfweer.nl/webcams/noordwijk/")

    def _fallback_digest_template(
        self,
        hour_states: List[HourState],
        scores: List[ScoreBreakdown],
        windows: List[SurfWindow],
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
                "Surf-update Noordwijk: geen data beschikbaar. "
                "Cam: surfweer.nl/webcams/noordwijk/"
            )

        # Lazy import: scoring.recommend_boards en visibility/convective helpers
        # zijn niet altijd aanwezig in unit-test contexts met mocked scoring.
        try:
            from src.scoring.hourly import (
                recommend_boards,
                visibility_concern,
                convective_warning,
            )
        except ImportError:
            recommend_boards = None
            visibility_concern = None
            convective_warning = None

        days = self._group_by_day(hour_states, scores)
        now = datetime.now()
        date_today = now.strftime("%-d-%-m-%Y")

        labels = ["Vandaag", "Morgen", "Overmorgen", "+3"]
        parts: List[str] = []

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
            wind_label = wind_label_for_noordwijk(ps.wind.direction_deg)
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
            window_strs: List[str] = []
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
            f"Surf-update Noordwijk van {date_today}:\n{body}\n"
            f"Cam: surfweer.nl/webcams/noordwijk/"
        )
