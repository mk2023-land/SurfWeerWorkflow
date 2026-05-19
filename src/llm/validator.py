"""
Output validator module.
Valideert LLM output tegen hallucinatie en onjuiste data.
"""
import logging
import re
from typing import Dict, List, Set
import json

from src.config import VALIDATION_CONFIG

logger = logging.getLogger(__name__)


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
        else:
            return f"Validation failed: {', '.join(self.issues)}"


class SMSValidator:
    """Valideert SMS output tegen input data."""

    def validate_sms(self, sms_text: str, structured_input: Dict) -> ValidationResult:
        """
        Valideer SMS tekst tegen structured input.

        Args:
            sms_text: De gegenereerde SMS tekst
            structured_input: De input data die aan de LLM werd gegeven

        Returns:
            ValidationResult
        """
        issues = []

        # Extract alle getallen uit SMS
        sms_numbers = self._extract_numbers(sms_text)

        # Extract toegestane getallen uit input
        allowed_numbers = self._extract_numbers_from_input(structured_input)

        # Valideer getallen
        for num in sms_numbers:
            if not self._is_number_allowed(num, allowed_numbers):
                issues.append(f"Number {num} not in input data")

        # Extract windrichtingen uit SMS
        sms_directions = self._extract_compass_directions(sms_text)

        # Extract toegestane richtingen uit input
        allowed_directions = self._extract_directions_from_input(structured_input)

        # Valideer richtingen
        for direction in sms_directions:
            if direction not in allowed_directions:
                issues.append(f"Direction '{direction}' not in input data")

        # Check voor specifieke strings die op hallucinatie wijzen
        hallucination_indicators = ['denk ik', 'waarschijnlijk', 'misschien', 'hopelijk']
        for indicator in hallucination_indicators:
            if indicator.lower() in sms_text.lower():
                issues.append(f"Hallucination indicator found: '{indicator}'")

        # Check lengte (max 480 tekens = 3 SMS-segmenten via Twilio concat)
        if len(sms_text) > 480:
            issues.append(f"SMS too long: {len(sms_text)} characters (max 480)")

        # Controleer of webcam URL aanwezig is
        if "surfweer.nl/webcams/noordwijk/" not in sms_text:
            issues.append("Missing webcam URL")

        passed = len(issues) == 0

        if not passed:
            logger.warning(f"SMS validation failed: {issues}")

        return ValidationResult(passed, issues)

    def _extract_numbers(self, text: str) -> List[float]:
        """Extraheer alle getallen uit tekst (inclusief decimalen)."""
        # Pattern voor getallen: optionele decimals, punten als decimaal scheidingsteken
        pattern = r'\d+\.?\d*'
        matches = re.findall(pattern, text)
        return [float(m) for m in matches]

    def _extract_numbers_from_input(self, input_data: Dict) -> Set[float]:
        """Recursief extractie van alle getallen uit input data."""
        numbers = set()

        def extract_from_dict(d):
            for key, value in d.items():
                if isinstance(value, (int, float)):
                    numbers.add(float(value))
                elif isinstance(value, str):
                    # Probeer getallen uit strings te extraheren
                    try:
                        num = float(value)
                        numbers.add(num)
                    except ValueError:
                        pass
                elif isinstance(value, dict):
                    extract_from_dict(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            extract_from_dict(item)
                        elif isinstance(item, (int, float)):
                            numbers.add(float(item))

        extract_from_dict(input_data)
        return numbers

    def _is_number_allowed(self, num: float, allowed_numbers: Set[float]) -> bool:
        """Controleer of een nummer toegestaan is (met precisie tolerantie)."""
        precision = VALIDATION_CONFIG['number_precision']

        for allowed in allowed_numbers:
            if abs(num - allowed) <= precision:
                return True

        return False

    def _extract_compass_directions(self, text: str) -> List[str]:
        """Extraheer windrichtingen uit tekst."""
        # NL richtingen
        directions = ['N', 'NNO', 'NO', 'ONO', 'O', 'OZO', 'ZO', 'ZZO',
                     'Z', 'ZZW', 'ZW', 'WZW', 'W', 'WNW', 'NW', 'NNW']

        found = []
        for direction in directions:
            if direction in text.upper():
                found.append(direction)

        return found

    def _extract_directions_from_input(self, input_data: Dict) -> List[str]:
        """Extraheer richtingen uit input data."""
        directions = []

        def extract_from_dict(d):
            for key, value in d.items():
                if isinstance(value, str):
                    # Check voor richtingen
                    all_directions = ['N', 'NNO', 'NO', 'ONO', 'O', 'OZO', 'ZO', 'ZZO',
                                     'Z', 'ZZW', 'ZW', 'WZW', 'W', 'WNW', 'NW', 'NNW',
                                     'offshore', 'onshore', 'aflandig', 'aanlandig']
                    for direction in all_directions:
                        if direction.lower() in value.lower():
                            directions.append(direction)
                elif isinstance(value, dict):
                    extract_from_dict(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            extract_from_dict(item)

        extract_from_dict(input_data)
        return directions

    def validate_alert_format(self, sms_text: str) -> ValidationResult:
        """
        Valideer specifieke alert formaat eisen.

        Args:
            sms_text: SMS tekst

        Returns:
            ValidationResult
        """
        issues = []

        # Check of begint met "NWIJK ALERT"
        if not sms_text.startswith("NWIJK ALERT"):
            issues.append("Alert SMS must start with 'NWIJK ALERT'")

        # Check voor datum
        if not re.search(r'\d{1,2}-\d{1,2}', sms_text):
            issues.append("Alert SMS must contain date (DD-MM format)")

        passed = len(issues) == 0
        return ValidationResult(passed, issues)

    def validate_digest_format(self, sms_text: str) -> ValidationResult:
        """
        Valideer specifieke digest formaat eisen.

        Args:
            sms_text: SMS tekst

        Returns:
            ValidationResult
        """
        issues = []

        # Check of begint met "Nwijk"
        if not sms_text.startswith("Nwijk"):
            issues.append("Digest SMS must start with 'Nwijk'")

        # Check voor dag
        if not any(dag in sms_text.lower() for dag in ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo']):
            issues.append("Digest SMS must contain day abbreviation")

        passed = len(issues) == 0
        return ValidationResult(passed, issues)