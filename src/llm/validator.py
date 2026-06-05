"""
Output validator module — semantisch hallucinatie-detectie.

Oude implementatie checkte of een getal "ergens in de input voorkomt" met
±0.1 tolerantie. Dat liet hallucinaties door (1,0m geaccepteerd omdat
confidence=1.0 in een ander veld stond). Nieuwe versie:

1. Per dag (uit `days[]._allowed_citations`) staat exact welke wave-heights,
   periods, wind-speeds, directions en tijden de LLM mag citeren.
2. Patroon-extractie: "1,0m" → check tegen wave_heights_m; "5,8s" → check
   tegen wave_periods_s; "14kn" → wind_speeds_kn; "14u" / "14:30" → times.
3. Comma decimals (NL) worden naar punt-decimals geconverteerd voor matching.
4. Tolerantie blijft 0.1 voor afrondingen (1,4m ↔ 1,36m mag), maar de
   citatie moet wel in het JUISTE veld vallen.

De validator faalt liever te streng dan te losjes — een gefaalde validatie
triggert het fallback-template, wat altijd correcte data heeft.
"""
import contextlib
import logging
import re
from typing import Optional

from src.config import SMS_VALIDATOR_MAX_LEN

logger = logging.getLogger(__name__)


# Dutch compass directions — 16-punts roos plus enkele variaties
_COMPASS_DIRS = [
    'NNO', 'ONO', 'OZO', 'ZZO', 'ZZW', 'WZW', 'WNW', 'NNW',
    'NO', 'ZO', 'ZW', 'NW',
    'N', 'O', 'Z', 'W',
]

# Wind-label woorden die NIET als compass-richtingen tellen
_WIND_LABELS = {'aflandig', 'zijaflandig', 'aanlandig', 'zij-aanlandig',
                'offshore', 'onshore', 'side-offshore'}


class ValidationResult:
    """Resultaat van output validatie."""

    def __init__(self, passed: bool, issues: list[str] = None):
        self.passed = passed
        self.issues = issues if issues is not None else []

    def __bool__(self):
        return self.passed

    def __str__(self):
        if self.passed:
            return "Validation passed"
        return f"Validation failed: {', '.join(self.issues)}"


def _parse_nl_decimal(s: str) -> float:
    """'1,5' → 1.5; '1.5' → 1.5."""
    return float(s.replace(',', '.'))


def _within(value: float, allowed: list[float], tol: float) -> bool:
    """True als `value` binnen `tol` ligt van enige waarde in `allowed`."""
    return any(abs(value - a) <= tol for a in allowed)


# 16-punts kompasroos in volgorde (kloksgewijs) — voor afstand-in-stappen.
_COMPASS_RING = [
    'N', 'NNO', 'NO', 'ONO', 'O', 'OZO', 'ZO', 'ZZO',
    'Z', 'ZZW', 'ZW', 'WZW', 'W', 'WNW', 'NW', 'NNW',
]
_COMPASS_INDEX = {d: i for i, d in enumerate(_COMPASS_RING)}


def _compass_step_distance(a: str, b: str) -> Optional[int]:
    """Aantal 22,5°-stappen tussen twee kompaslabels op de 16-punts roos.
    None als één van beide geen geldig label is."""
    ia, ib = _COMPASS_INDEX.get(a), _COMPASS_INDEX.get(b)
    if ia is None or ib is None:
        return None
    diff = abs(ia - ib) % 16
    return min(diff, 16 - diff)


def _compass_within_tol(d: str, allowed: set, tol_steps: int = 1) -> bool:
    """True als `d` exact in `allowed` zit, óf binnen `tol_steps` 22,5°-stappen
    van enige toegestane richting ligt.

    Reden: de digest-data heeft per uur een discreet kompaslabel (bv. ZZO),
    maar Claude beschrijft een dag-gemiddelde en kan een buur-label kiezen
    (ZO, 22,5° ernaast). Dat is afronding, geen hallucinatie. Vóór deze
    tolerantie viel élke digest met zo'n buur-label terug op de nood-template
    (zie 2026-06-04/05: 'Direction ZO niet in allowed [...ZZO...]')."""
    if d in allowed:
        return True
    return any(
        (dist := _compass_step_distance(d, a)) is not None and dist <= tol_steps
        for a in allowed
    )


def _time_to_minutes(hhmm: str) -> Optional[int]:
    """'14:30' → 870; '14' → 840. None bij parsing-fout."""
    try:
        if ':' in hhmm:
            h, m = hhmm.split(':')
            return int(h) * 60 + int(m)
        return int(hhmm) * 60
    except (ValueError, AttributeError):
        return None


class SMSValidator:
    """Valideert SMS output tegen input data — contextueel/semantisch."""

    def validate_sms(self, sms_text: str, structured_input: dict) -> ValidationResult:
        """
        Valideer SMS tegen structured input.

        Gebruikt `days[]._allowed_citations` waar beschikbaar (nieuwe pipeline)
        en valt anders terug op de oude recursieve whitelist.
        """
        issues: list[str] = []

        allowed = self._collect_allowed_citations(structured_input)

        # 1. Wave heights "X,Ym" of "X.Ym".
        # Eerst range-uitdrukkingen ("0.8-1.2m") afhandelen — beide getallen
        # moeten in allowed staan, anders glipt het lagere getal door de
        # enkelvoudige regex heen. Tracking van consumed spans voorkomt
        # double-counting.
        consumed_spans: list[tuple] = []

        def _claim(s: int, e: int) -> bool:
            for cs, ce in consumed_spans:
                if not (e <= cs or s >= ce):
                    return False
            consumed_spans.append((s, e))
            return True

        for match in re.finditer(
            r'(\d+[\.,]?\d*)\s*[-–—]\s*(\d+[\.,]?\d*)\s*m(?![/a-zA-Z])',
            sms_text,
        ):
            if not _claim(match.start(), match.end()):
                continue
            for grp in (match.group(1), match.group(2)):
                val = _parse_nl_decimal(grp)
                if not _within(val, allowed['wave_heights_m'], tol=0.15):
                    issues.append(
                        f"Wave height {val}m not in allowed heights {allowed['wave_heights_m']}"
                    )

        for match in re.finditer(r'(\d+[\.,]\d+|\d+)\s*m(?![/a-zA-Z])', sms_text):
            if any(not (match.end() <= cs or match.start() >= ce)
                   for cs, ce in consumed_spans):
                continue
            val = _parse_nl_decimal(match.group(1))
            if not _within(val, allowed['wave_heights_m'], tol=0.15):
                issues.append(
                    f"Wave height {val}m not in allowed heights {allowed['wave_heights_m']}"
                )

        # 2. Wave periods "Xs" — ranges eerst ("6-8s"), dan losse waarden.
        period_spans: list[tuple] = []
        for match in re.finditer(
            r'(\d+[\.,]?\d*)\s*[-–—]\s*(\d+[\.,]?\d*)\s*(?:s|sec|seconden)(?=[\s\.,;:!?]|$)',
            sms_text,
        ):
            period_spans.append((match.start(), match.end()))
            for grp in (match.group(1), match.group(2)):
                val = _parse_nl_decimal(grp)
                if not _within(val, allowed['wave_periods_s'], tol=0.5):
                    issues.append(
                        f"Wave period {val}s not in allowed periods {allowed['wave_periods_s']}"
                    )

        for match in re.finditer(r'(\d+[\.,]\d+|\d+)\s*(?:s|sec|seconden)(?=[\s\.,;:!?]|$)', sms_text):
            if any(not (match.end() <= cs or match.start() >= ce)
                   for cs, ce in period_spans):
                continue
            val = _parse_nl_decimal(match.group(1))
            if not _within(val, allowed['wave_periods_s'], tol=0.5):
                issues.append(
                    f"Wave period {val}s not in allowed periods {allowed['wave_periods_s']}"
                )

        # 3. Wind speeds "Xkn" / "X kn" / "Xknopen" — ranges eerst ("15-20kn").
        # Beide getallen moeten in wind_speeds_kn OF wind_gusts_kn zitten
        # (gusts kunnen als bovenste range-grens worden gebruikt).
        allowed_wind = set(allowed['wind_speeds_kn']) | set(
            allowed.get('wind_gusts_kn', [])
        )
        allowed_wind_list = sorted(allowed_wind)

        wind_spans: list[tuple] = []
        for match in re.finditer(
            r'(\d+[\.,]?\d*)\s*[-–—]\s*(\d+[\.,]?\d*)\s*kn(?:open)?\b',
            sms_text,
        ):
            wind_spans.append((match.start(), match.end()))
            for grp in (match.group(1), match.group(2)):
                val = _parse_nl_decimal(grp)
                if not _within(val, allowed_wind_list, tol=1.0):
                    issues.append(
                        f"Wind speed {val}kn not in allowed speeds {allowed_wind_list}"
                    )

        for match in re.finditer(r'(\d+[\.,]\d+|\d+)\s*kn(?:open)?\b', sms_text):
            if any(not (match.end() <= cs or match.start() >= ce)
                   for cs, ce in wind_spans):
                continue
            val = _parse_nl_decimal(match.group(1))
            if not _within(val, allowed_wind_list, tol=1.0):
                issues.append(
                    f"Wind speed {val}kn not in allowed speeds {allowed_wind_list}"
                )

        # 3b. Verboden eenheden — referentie-forecaster gebruikt nooit bft of km/u in onze
        # pipeline (input is altijd knopen). Elke voorkomen is per definitie
        # een hallucinatie, ongeacht allowed_citations.
        for match in re.finditer(r'\b(\d+[\.,]?\d*)\s*bft\b', sms_text, re.IGNORECASE):
            issues.append(
                f"Verboden eenheid 'bft' ({match.group(0)}) — gebruik knopen"
            )
        for match in re.finditer(r'\b(\d+[\.,]?\d*)\s*km/u?\b', sms_text, re.IGNORECASE):
            issues.append(
                f"Verboden eenheid 'km/u' ({match.group(0)}) — gebruik knopen"
            )

        # 4. Tijden "HH:MM" of "HHu" — moeten matchen met allowed times.
        # Bereik-uitdrukkingen "14-16u" zijn samengesteld uit twee aparte tijden;
        # check elk separaat.
        #
        # Tolerantie-logica:
        #   - Default: 15 min (referentie-forecaster is precies, "HW 14:00" mag geen 14:30 zijn).
        #   - Verhoogd naar 30 min ALS de tijd direct voorafgegaan wordt door
        #     "rond"/"omstreeks"/"tegen"/"ongeveer" (zachte indicatie).
        time_matches = list(re.finditer(r'(\d{1,2}):(\d{2})', sms_text))
        time_matches += list(re.finditer(r'\b(\d{1,2})u\b', sms_text))
        allowed_minutes = {_time_to_minutes(t) for t in allowed['times_hhmm']}
        allowed_minutes.discard(None)

        # Pre-compute "soft" tijd-spans: positions waar een rond/omstreeks
        # vlak voor staat (max 5 chars whitespace tussen woord en cijfer).
        soft_spans: list[tuple] = []
        for sm in re.finditer(
            r'(?:rond|omstreeks|tegen|ongeveer)\s+(\d{1,2}):?(\d{2})?u?',
            sms_text,
            re.IGNORECASE,
        ):
            soft_spans.append((sm.start(1), sm.end()))

        def _is_soft(start: int) -> bool:
            return any(ss <= start <= se for ss, se in soft_spans)

        for m in time_matches:
            if ':' in m.group(0):
                mins = int(m.group(1)) * 60 + int(m.group(2))
            else:
                mins = int(m.group(1)) * 60
            tol = 30 if _is_soft(m.start()) else 15
            if allowed_minutes and not any(abs(mins - am) <= tol for am in allowed_minutes):
                issues.append(
                    f"Tijd {m.group(0)} niet in allowed times {sorted(allowed['times_hhmm'])}"
                )

        # 5. Compass richtingen — moeten in wind- of wave-directions zitten
        # Tokenize op woorden, langste prefix-match voor "NNO" boven "N"
        sms_dirs = self._extract_compass_directions(sms_text)
        allowed_dirs = (
            set(allowed['wind_directions_compass']) |
            set(allowed['wave_directions_compass'])
        )
        for d in sms_dirs:
            if not _compass_within_tol(d, allowed_dirs, tol_steps=1):
                issues.append(
                    f"Direction '{d}' niet in of naast allowed directions "
                    f"{sorted(allowed_dirs)} (tolerantie ±1 kompasstap)"
                )

        # 6. Verboden hallucinatie-indicatoren
        hallucination_phrases = [
            'denk ik', 'waarschijnlijk', 'misschien', 'hopelijk', 'wellicht',
            'lijkt me', 'gevoel van',
        ]
        for phrase in hallucination_phrases:
            if phrase.lower() in sms_text.lower():
                issues.append(f"Hallucination indicator: '{phrase}'")

        # 6b. Board-claims moeten matchen met boards_suitable per dag.
        # Een bord-mention is een POSITIEVE claim als er geen negatie vlakbij
        # staat — "longboard prima" = claim, "shortboard niet" = correcte
        # afwijzing en mag dus altijd. referentie-forecaster gebruikt zelf vaak negatieve
        # mentions ("geen shortboard", "shortboard moet wachten").
        board_patterns = {
            'longboard': r'\blong(?:board)?\b',
            'midlength': r'\bmid(?:length|lenght|-length)?\b',
            'fish': r'\bfish\b',
            'shortboard': r'\bshort(?:board)?\b',
        }
        negation_cues = [
            'geen', 'niet', 'nee', 'hoeft niet', 'moet wachten', 'kan niet',
            'lastig', 'onmogelijk', 'no go', 'sla over', 'hoeft', 'zonder',
        ]
        allowed_boards = set()
        for day in structured_input.get('days') or []:
            ph = (day or {}).get('peak_height_hour', {}) or {}
            allowed_boards.update(ph.get('boards_suitable') or [])
            bw = (day or {}).get('best_window', {}) or {}
            pc = bw.get('peak_conditions') or {}
            allowed_boards.update(pc.get('boards_suitable') or [])

        sms_lower = sms_text.lower()
        for board, pattern in board_patterns.items():
            if board in allowed_boards:
                continue  # geen probleem, mag genoemd worden
            # Zoek alle positie-matches van dit bord-woord
            for m in re.finditer(pattern, sms_lower):
                start = m.start()
                # Kijk 30 karakters terug voor een negatie-cue
                preceding = sms_lower[max(0, start - 30):start]
                if any(cue in preceding for cue in negation_cues):
                    continue  # negatief, mag
                issues.append(
                    f"Board '{board}' positief geclaimd maar niet in "
                    f"boards_suitable (toegestaan: {sorted(allowed_boards) or 'NIETS'})"
                )
                break  # één issue per bord-type is genoeg

        # 7. "Springtij" alleen als input dat zegt
        if 'springtij' in sms_text.lower():
            if not self._input_mentions_spring_tide(structured_input):
                issues.append("Springtij geclaimd maar niet in input")

        # 7b. "Doodtij" alleen als input dat zegt (symmetrisch met springtij).
        # Voorkomt LLM die "doodtij" gebruikt als generieke "weinig tij"-term
        # waar de werkelijke fase iets anders is.
        if 'doodtij' in sms_text.lower():
            if not self._input_mentions_neap_tide(structured_input):
                issues.append("Doodtij geclaimd maar niet in input")

        # 7b-2. PER-DAG check voor doodtij/springtij. _input_mentions_* zijn
        # globaal — als één dag spring/neap is, accepteren ze die term overal
        # in de SMS. Hier strikter: doodtij/springtij mag alleen genoemd
        # worden in het dag-blok waar het label er expliciet voor staat
        # (of waar het globale tide_context geldt). Voorkomt run-4-pattern
        # van "Springtij deze week" + "Doodtij maandag" door elkaar — die
        # zijn semantisch tegenstrijdig.
        tc_global = structured_input.get('tide_context') or {}
        global_spring = bool(tc_global.get('spring_tide'))
        global_neap = bool(tc_global.get('neap_tide'))
        days_for_tide = structured_input.get('days') or []
        tide_blocks = re.split(r'(?=Nwijk\s+\w+:)', sms_text)
        tide_blocks = [b for b in tide_blocks if re.match(r'Nwijk\s+\w+:', b)]
        for i, block in enumerate(tide_blocks):
            if i >= len(days_for_tide):
                break
            day = days_for_tide[i] or {}
            ts = day.get('tide_summary') or {}
            day_label = ts.get('spring_neap_label')
            day_spring = (day_label == 'springtij') or ts.get('is_spring_tide') is True
            day_neap = (day_label == 'doodtij') or ts.get('is_neap_tide') is True
            block_l = block.lower()
            if 'springtij' in block_l and not (day_spring or global_spring):
                issues.append(
                    f"Springtij genoemd op dag {day.get('date','?')} "
                    f"maar niet in input voor die dag (label={day_label})"
                )
            if 'doodtij' in block_l and not (day_neap or global_neap):
                issues.append(
                    f"Doodtij genoemd op dag {day.get('date','?')} "
                    f"maar niet in input voor die dag (label={day_label})"
                )

        # 7b-3. Springtij EN doodtij in dezelfde SMS — vrijwel altijd
        # tegenstrijdig (één week kan niet beide globaal zijn). Tenzij de
        # input WEL beide bevat (uitzonderlijk, bv. data-glitch).
        sms_lower = sms_text.lower()
        if 'springtij' in sms_lower and 'doodtij' in sms_lower:
            input_has_both = (
                self._input_mentions_spring_tide(structured_input)
                and self._input_mentions_neap_tide(structured_input)
            )
            if not input_has_both:
                issues.append(
                    "Springtij EN doodtij in dezelfde SMS — semantisch "
                    "tegenstrijdig en niet door input ondersteund"
                )

        # 5b. PER-DAG wave/periode/wind validatie (safety-kritiek).
        # De globale checks (1-3) mergen ALLE dagen + lookahead in één
        # whitelist — een 2,2m piek op T+5 maakt dan "2,2m" overal in de
        # SMS valide, ook op een T+4 dag waar het model max 1,6m geeft.
        # Hier extra: split SMS in dag-blokken en check elk getal tegen
        # DAT DAG'S allowed_citations. Wezenlijk strikter dan globaal.
        days_pd = structured_input.get('days') or []
        blocks_pd = re.split(r'(?=Nwijk\s+\w+:)', sms_text)
        blocks_pd = [b for b in blocks_pd if re.match(r'Nwijk\s+\w+:', b)]
        for i, block in enumerate(blocks_pd):
            if i >= len(days_pd):
                break
            day = days_pd[i] or {}
            day_cit = day.get('_allowed_citations') or {}
            day_date = day.get('date', '?')
            day_heights = day_cit.get('wave_heights_m') or []
            day_periods = day_cit.get('wave_periods_s') or []
            day_winds = list(day_cit.get('wind_speeds_kn') or [])
            day_winds += list(day_cit.get('wind_gusts_kn') or [])

            # Wave-heights in dit blok
            for m in re.finditer(r'(\d+[\.,]\d+|\d+)\s*m(?![/a-zA-Z])', block):
                val = _parse_nl_decimal(m.group(1))
                if day_heights and not _within(val, day_heights, tol=0.15):
                    issues.append(
                        f"Wave height {val}m op dag {day_date} niet in DAG-"
                        f"specifieke allowed {day_heights} — citatie van "
                        f"andere dag/lookahead, mag niet hier staan"
                    )

            # Wave-periods in dit blok
            for m in re.finditer(r'(\d+[\.,]\d+|\d+)\s*(?:s|sec|seconden)(?=[\s\.,;:!?]|$)', block):
                val = _parse_nl_decimal(m.group(1))
                if day_periods and not _within(val, day_periods, tol=0.5):
                    issues.append(
                        f"Wave period {val}s op dag {day_date} niet in DAG-"
                        f"specifieke allowed {day_periods}"
                    )

            # Wind-speeds in dit blok (inclusief gusts)
            for m in re.finditer(r'(\d+[\.,]\d+|\d+)\s*kn(?:open)?\b', block):
                val = _parse_nl_decimal(m.group(1))
                if day_winds and not _within(val, day_winds, tol=1.0):
                    issues.append(
                        f"Wind speed {val}kn op dag {day_date} niet in DAG-"
                        f"specifieke allowed {sorted(day_winds)}"
                    )

        # 7c. Forecast-certainty frasen ALLEEN op dagen waar
        # _allowed_citations.data_horizon_extended=true (T+4+ fallback model).
        # Op primary dagen (T+0..T+3) is "modellen onzeker" een hallucinatie
        # over data die de LLM niet heeft. Veiligheidskritiek: gebruiker
        # baseert beslissing om zee in te gaan op deze SMS, een vals
        # "modellen onzeker" op T+0 kan een feitelijk solide voorspelling
        # ondermijnen.
        # Regex-based detectie (vangt variaties: "modellen onzeker",
        # "modellen nog onzeker", "modellen niet eensgezind", "nog onzeker
        # zo ver vooruit", etc.). Substring-matching faalde op "Modellen
        # nog onzeker" omdat de exacte string "modellen onzeker" niet
        # letterlijk voorkwam.
        cert_patterns = [
            (r'modellen?\s+(?:nog\s+)?(?:onzeker|oneens|uiteen|niet\s+eensgezind)',
             'modellen-onzeker variant'),
            (r'verre\s+forecast', 'verre forecast'),
            (r'kan\s+nog\s+draaien', 'kan nog draaien'),
            (r'\bnog\s+onzeker\b', 'nog onzeker'),
            (r'forecast\s+kan\s+nog', 'forecast kan nog'),
        ]
        # Split SMS in dag-blokken op "Nwijk <weekdag>:" zodat we per dag
        # de toestemming kunnen checken.
        days = structured_input.get('days') or []
        day_blocks = re.split(r'(?=Nwijk\s+\w+:)', sms_text)
        day_blocks = [b for b in day_blocks if re.match(r'Nwijk\s+\w+:', b)]
        for i, block in enumerate(day_blocks):
            if i >= len(days):
                break
            day = days[i] or {}
            cit = day.get('_allowed_citations') or {}
            if cit.get('data_horizon_extended'):
                continue  # extended horizon — hint is hier expliciet toegestaan
            block_l = block.lower()
            for pattern, label in cert_patterns:
                if re.search(pattern, block_l):
                    issues.append(
                        f"Forecast-certainty frase ({label}) op primary dag "
                        f"{day.get('date','?')} — hallucinatie, LLM heeft "
                        f"geen grond voor model-onzekerheid op T+0..T+3"
                    )
                    break  # één issue per blok is genoeg

        # 8. Lengte cap — referentie-forecaster' eigen SMS'jes zitten op 1400-1700 tekens.
        # Voor ntfy maakt het niet uit. SMS_VALIDATOR_MAX_LEN (src.config) als
        # centrale waarde — gedeeld met notifier-laag.
        if len(sms_text) > SMS_VALIDATOR_MAX_LEN:
            issues.append(
                f"SMS too long: {len(sms_text)} characters "
                f"(max {SMS_VALIDATOR_MAX_LEN})"
            )

        # 9. Webcam URL aanwezig
        if "surfweer.nl/webcams/noordwijk/" not in sms_text:
            issues.append("Missing webcam URL")

        passed = len(issues) == 0
        if not passed:
            logger.warning(f"SMS validation failed ({len(issues)} issues): {issues}")
        return ValidationResult(passed, issues)

    def _collect_allowed_citations(self, structured_input: dict) -> dict[str, list]:
        """
        Verzamel alle toegestane citaties uit `days[]._allowed_citations`.
        Valt terug op recursieve extractie (oude pipeline) als die ontbreken.
        """
        merged: dict[str, set] = {
            'wave_heights_m': set(),
            'wave_periods_s': set(),
            'wind_speeds_kn': set(),
            'wind_directions_compass': set(),
            'wave_directions_compass': set(),
            'times_hhmm': set(),
        }

        days = structured_input.get('days') or []
        found_any = False
        for day in days:
            cit = (day or {}).get('_allowed_citations') or {}
            if not cit:
                continue
            found_any = True
            for k in merged:
                merged[k].update(cit.get(k) or [])

        # Fallback: oude recursieve extractie als _allowed_citations ontbreekt.
        # Wel breder (alle nummers gelden), dus eigenlijk een no-op qua hallucinatie-
        # detectie — maar voorkomt regressie tijdens transitie.
        if not found_any:
            logger.warning(
                "Geen _allowed_citations in input — validator valt terug op brede whitelist."
            )
            all_nums = self._extract_numbers_from_input(structured_input)
            merged['wave_heights_m'].update(all_nums)
            merged['wave_periods_s'].update(all_nums)
            merged['wind_speeds_kn'].update(all_nums)

        # Voeg ook globale tide-context tijden toe (springtij-label etc.)
        # zodat opmerkingen over tide_context.next_high_time niet falen.
        tide_ctx = structured_input.get('tide_context') or {}
        for k in ('next_high_time', 'next_low_time'):
            v = tide_ctx.get(k)
            if v:
                merged['times_hhmm'].add(v)

        # Lookahead (dagen 5-8 buiten de digest-window): de optionele
        # `lookahead.allowed_citations` levert wave-heights/periods/dirs voor
        # de vooruitblik-zin aan het einde van het bericht. Zonder dit
        # zou Claude een geldige vooruitblik krijgen geflagd als hallucinatie.
        lookahead = structured_input.get('lookahead') or {}
        la_cit = lookahead.get('allowed_citations') or {}
        for k in ('wave_heights_m', 'wave_periods_s', 'wave_directions_compass'):
            merged[k].update(la_cit.get(k) or [])

        return {k: sorted(v) for k, v in merged.items()}

    def _extract_numbers_from_input(self, input_data: dict) -> set[float]:
        """Recursief extractie van alle getallen (legacy fallback)."""
        numbers: set[float] = set()

        def walk(node):
            if isinstance(node, (int, float)):
                numbers.add(float(node))
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
            elif isinstance(node, str):
                with contextlib.suppress(ValueError):
                    numbers.add(float(node.replace(',', '.')))

        walk(input_data)
        return numbers

    def _extract_compass_directions(self, text: str) -> list[str]:
        """
        Extraheer compass-richtingen uit tekst. Langste-eerst gretig matchen:
        'NNO' wordt voor 'N' herkend zodat 'NNO 5kn' niet als 'N' telt.

        Levert ook ONBEKENDE 2-4 letter NOZW-tokens (bv. 'NWN', 'ZOW') terug
        zodat de caller die als afwijking kan flaggen — anders zou een
        hallucinatie als "wind uit NWN" stilzwijgend goedkeuren omdat NWN
        niet in _COMPASS_DIRS staat.

        Span-tracking voorkomt double-counting: een match die binnen een
        eerdere langere match valt (bv. 'N' binnen 'NNO') wordt geskipt.
        """
        upper = text.upper()
        consumed_spans: list[tuple] = []  # (start, end) van al gematchte richtingen

        def _overlaps(s: int, e: int) -> bool:
            return any(not (e <= cs or s >= ce) for cs, ce in consumed_spans)

        # Pre-pass: markeer dag-afkortingen na "Nwijk " of begin-van-alinea als
        # consumed, zodat ze niet als compass-richting worden geïnterpreteerd.
        # `zo` (zondag) zou anders verward worden met `ZO` (zuidoost). De andere
        # dagen botsen niet met compass-codes, maar we markeren ze allemaal voor
        # consistentie. Het bericht-format is bv. "Nwijk zo: ..." of "\nzo: ...".
        for m in re.finditer(
            r'(?:NWIJK\s+|^|\n)(MA|DI|WO|DO|VR|ZA|ZO)(?=\s*[:\-—])',
            upper,
            re.MULTILINE,
        ):
            consumed_spans.append((m.start(1), m.end(1)))

        found: list[str] = []
        for d in _COMPASS_DIRS:  # al gesorteerd langste-eerst
            for m in re.finditer(r'\b' + re.escape(d) + r'\b', upper):
                start, end = m.start(), m.end()
                if _overlaps(start, end):
                    continue
                # Skip als het deel is van een wind-label expressie.
                # Voorheen stond hier `pass` (no-op) — match werd alsnog
                # toegevoegd. Nu skippen we de match echt.
                context = upper[max(0, start - 10):min(len(upper), end + 15)]
                if any(w.upper() in context for w in _WIND_LABELS if w.upper() != d):
                    consumed_spans.append((start, end))
                    continue
                consumed_spans.append((start, end))
                found.append(d)

        # Sweep: onbekende NOZW-tokens (2-4 letters). Een hallucinatie als
        # 'NWN' of 'ZOZ' (geen geldige compass-codes) moet als afwijking
        # gerapporteerd worden zodat allowed_dirs-check faalt.
        for m in re.finditer(r'\b[NOZW]{2,4}\b', upper):
            token = m.group(0)
            if token in _COMPASS_DIRS:
                continue  # al via reguliere matcher gevangen
            start, end = m.start(), m.end()
            if _overlaps(start, end):
                continue
            found.append(token)

        return found

    def _input_mentions_spring_tide(self, structured_input: dict) -> bool:
        """Check of springtij ergens in de input expliciet is gezegd."""
        tc = structured_input.get('tide_context') or {}
        if tc.get('spring_tide') is True:
            return True
        if tc.get('spring_tide_label') == 'springtij':
            return True
        for day in structured_input.get('days') or []:
            ts = (day or {}).get('tide_summary') or {}
            if ts.get('spring_neap_label') == 'springtij':
                return True
        return False

    def _input_mentions_neap_tide(self, structured_input: dict) -> bool:
        """Check of doodtij ergens in de input expliciet is gezegd."""
        tc = structured_input.get('tide_context') or {}
        if tc.get('neap_tide') is True:
            return True
        if tc.get('spring_tide_label') == 'doodtij':
            return True
        for day in structured_input.get('days') or []:
            ts = (day or {}).get('tide_summary') or {}
            if ts.get('spring_neap_label') == 'doodtij':
                return True
            if ts.get('is_neap_tide') is True:
                return True
        return False

    def validate_alert_format(self, sms_text: str) -> ValidationResult:
        """Valideer specifieke alert formaat eisen."""
        issues = []
        if not sms_text.startswith("NWIJK ALERT"):
            issues.append("Alert SMS must start with 'NWIJK ALERT'")
        if not re.search(r'\d{1,2}-\d{1,2}', sms_text):
            issues.append("Alert SMS must contain date (DD-MM format)")
        return ValidationResult(len(issues) == 0, issues)

    def validate_digest_format(self, sms_text: str) -> ValidationResult:
        """Valideer specifieke digest formaat eisen.

        Twee geldige starts:
          - 'Nwijk' — Claude-output (referentie-forecaster-stijl, per-dag alineas)
          - 'Surf-update Noordwijk' — deterministische fallback-template
        """
        issues = []
        if not (sms_text.startswith("Nwijk") or sms_text.startswith("Surf-update Noordwijk")):
            issues.append("Digest SMS must start with 'Nwijk' or 'Surf-update Noordwijk'")
        if not any(dag in sms_text.lower() for dag in ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo']):
            issues.append("Digest SMS must contain day abbreviation")
        return ValidationResult(len(issues) == 0, issues)
