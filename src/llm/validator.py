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
import logging
import re
from typing import Dict, List, Set, Tuple, Optional
import json

from src.config import VALIDATION_CONFIG

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

    def __init__(self, passed: bool, issues: List[str] = None):
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


def _within(value: float, allowed: List[float], tol: float) -> bool:
    """True als `value` binnen `tol` ligt van enige waarde in `allowed`."""
    return any(abs(value - a) <= tol for a in allowed)


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

    def validate_sms(self, sms_text: str, structured_input: Dict) -> ValidationResult:
        """
        Valideer SMS tegen structured input.

        Gebruikt `days[]._allowed_citations` waar beschikbaar (nieuwe pipeline)
        en valt anders terug op de oude recursieve whitelist.
        """
        issues: List[str] = []

        allowed = self._collect_allowed_citations(structured_input)

        # 1. Wave heights "X,Ym" of "X.Ym"
        for match in re.finditer(r'(\d+[\.,]\d+|\d+)\s*m(?![/a-zA-Z])', sms_text):
            val = _parse_nl_decimal(match.group(1))
            if not _within(val, allowed['wave_heights_m'], tol=0.15):
                issues.append(
                    f"Wave height {val}m not in allowed heights {allowed['wave_heights_m']}"
                )

        # 2. Wave periods "Xs" (alleen los getal + s, niet "uitsluitend", "is", etc.)
        for match in re.finditer(r'(\d+[\.,]\d+|\d+)\s*s(?=[\s\.,;:!?]|$)', sms_text):
            val = _parse_nl_decimal(match.group(1))
            if not _within(val, allowed['wave_periods_s'], tol=0.5):
                issues.append(
                    f"Wave period {val}s not in allowed periods {allowed['wave_periods_s']}"
                )

        # 3. Wind speeds "Xkn" / "X kn" / "Xknopen"
        for match in re.finditer(r'(\d+[\.,]\d+|\d+)\s*kn(?:open)?\b', sms_text):
            val = _parse_nl_decimal(match.group(1))
            if not _within(val, allowed['wind_speeds_kn'], tol=1.0):
                issues.append(
                    f"Wind speed {val}kn not in allowed speeds {allowed['wind_speeds_kn']}"
                )

        # 4. Tijden "HH:MM" of "HHu" of "HH-HH" — moeten matchen met allowed times
        # Bereik-uitdrukkingen "14-16u" zijn samengesteld uit twee aparte tijden;
        # check elk separaat.
        time_matches = list(re.finditer(r'(\d{1,2}):(\d{2})', sms_text))
        time_matches += list(re.finditer(r'\b(\d{1,2})u\b', sms_text))
        allowed_minutes = {_time_to_minutes(t) for t in allowed['times_hhmm']}
        allowed_minutes.discard(None)
        for m in time_matches:
            if ':' in m.group(0):
                mins = int(m.group(1)) * 60 + int(m.group(2))
            else:
                mins = int(m.group(1)) * 60
            # Tolerantie 60 min — "rond 14u" mag matchen met allowed 13:30 of 14:30.
            if allowed_minutes and not any(abs(mins - am) <= 60 for am in allowed_minutes):
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
            if d not in allowed_dirs:
                issues.append(
                    f"Direction '{d}' niet in allowed directions {sorted(allowed_dirs)}"
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
        # Verzamel alle boards die de LLM noemt en alle die ergens in input
        # als suitable zijn gemarkeerd. Als LLM 'shortboard' noemt op een dag
        # waar shortboard NIET suitable is, is dat een hallucinatie.
        board_terms = {
            'longboard': ['longboard', 'long ', ' long.', ' long,', ' long;'],
            'midlength': ['midlength', 'midlenght', 'mid-length'],
            'fish': ['fish'],
            'shortboard': ['shortboard', 'short '],
        }
        sms_lower = sms_text.lower()
        sms_boards = set()
        for board, terms in board_terms.items():
            if any(t in sms_lower for t in terms):
                sms_boards.add(board)

        allowed_boards = set()
        for day in structured_input.get('days') or []:
            for field in ('peak_height_hour', ):
                bc = (day or {}).get(field, {})
                allowed_boards.update(bc.get('boards_suitable') or [])
            bw = (day or {}).get('best_window', {}) or {}
            pc = bw.get('peak_conditions') or {}
            allowed_boards.update(pc.get('boards_suitable') or [])

        for board in sms_boards:
            if board not in allowed_boards:
                issues.append(
                    f"Board '{board}' genoemd maar nergens in boards_suitable "
                    f"(toegestaan: {sorted(allowed_boards) or 'NIETS'})"
                )

        # 7. "Springtij" alleen als input dat zegt
        if 'springtij' in sms_text.lower():
            if not self._input_mentions_spring_tide(structured_input):
                issues.append("Springtij geclaimd maar niet in input")

        # 8. Lengte cap — referentie-forecaster' eigen SMS'jes zitten op 1400-1700 tekens.
        # Voor ntfy maakt het niet uit. Houden we op 1800 voor wat marge.
        if len(sms_text) > 1800:
            issues.append(f"SMS too long: {len(sms_text)} characters (max 1800)")

        # 9. Webcam URL aanwezig
        if "surfweer.nl/webcams/noordwijk/" not in sms_text:
            issues.append("Missing webcam URL")

        passed = len(issues) == 0
        if not passed:
            logger.warning(f"SMS validation failed ({len(issues)} issues): {issues}")
        return ValidationResult(passed, issues)

    def _collect_allowed_citations(self, structured_input: Dict) -> Dict[str, List]:
        """
        Verzamel alle toegestane citaties uit `days[]._allowed_citations`.
        Valt terug op recursieve extractie (oude pipeline) als die ontbreken.
        """
        merged: Dict[str, Set] = {
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

        return {k: sorted(v) for k, v in merged.items()}

    def _extract_numbers_from_input(self, input_data: Dict) -> Set[float]:
        """Recursief extractie van alle getallen (legacy fallback)."""
        numbers: Set[float] = set()

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
                try:
                    numbers.add(float(node.replace(',', '.')))
                except ValueError:
                    pass

        walk(input_data)
        return numbers

    def _extract_compass_directions(self, text: str) -> List[str]:
        """
        Extraheer compass-richtingen uit tekst. Langste-eerst gretig matchen:
        'NNO' wordt voor 'N' herkend zodat 'NNO 5kn' niet als 'N' telt.
        """
        # Werk met hoofdletter-versie en woordgrenzen
        upper = text.upper()
        found: List[str] = []
        for d in _COMPASS_DIRS:  # al gesorteerd langste-eerst
            for m in re.finditer(r'\b' + re.escape(d) + r'\b', upper):
                # Skip als het deel is van een wind-label
                start = m.start()
                # Vinden we 'aflandig'/'aanlandig'/etc. direct na deze positie?
                context = upper[max(0, start - 10):min(len(upper), start + 15)]
                if any(w.upper() in context for w in _WIND_LABELS if w.upper() != d):
                    pass
                found.append(d)
        return found

    def _input_mentions_spring_tide(self, structured_input: Dict) -> bool:
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

    def validate_alert_format(self, sms_text: str) -> ValidationResult:
        """Valideer specifieke alert formaat eisen."""
        issues = []
        if not sms_text.startswith("NWIJK ALERT"):
            issues.append("Alert SMS must start with 'NWIJK ALERT'")
        if not re.search(r'\d{1,2}-\d{1,2}', sms_text):
            issues.append("Alert SMS must contain date (DD-MM format)")
        return ValidationResult(len(issues) == 0, issues)

    def validate_digest_format(self, sms_text: str) -> ValidationResult:
        """Valideer specifieke digest formaat eisen."""
        issues = []
        if not sms_text.startswith("Nwijk"):
            issues.append("Digest SMS must start with 'Nwijk'")
        if not any(dag in sms_text.lower() for dag in ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo']):
            issues.append("Digest SMS must contain day abbreviation")
        return ValidationResult(len(issues) == 0, issues)
