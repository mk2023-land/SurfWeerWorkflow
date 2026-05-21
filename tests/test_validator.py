"""
Unit tests voor src/llm/validator.py — output validator (anti-hallucinatie).

Regressie voor B8: het dead-code `pass` blok deed niets en onbekende
2-4 letter NOZW-tokens (zoals 'NWN', 'ZOZ') werden niet gevangen.
"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.llm.validator import SMSValidator


class TestB8CompassExtraction:
    def setup_method(self):
        self.v = SMSValidator()

    def test_simple_compass_extracted(self):
        dirs = self.v._extract_compass_directions("Wind uit ZW 12kn")
        assert "ZW" in dirs

    def test_longer_direction_wins_over_shorter(self):
        """NNO mag niet ook als N tellen — span-tracking voorkomt double-count."""
        dirs = self.v._extract_compass_directions("Swell NNO 1.2m")
        assert "NNO" in dirs
        assert "N" not in dirs

    def test_unknown_pseudo_direction_detected(self):
        """
        Hallucinatie als 'NWN' is geen geldige Nederlandse compass-code
        maar zou voorheen stilzwijgend door de validator glijden.
        Nu moet de sweep het oppikken zodat allowed_dirs-check faalt.
        """
        dirs = self.v._extract_compass_directions("Wind uit NWN 8kn")
        assert "NWN" in dirs

    def test_unknown_pseudo_direction_zoz(self):
        dirs = self.v._extract_compass_directions("ZOZ 1.4m groundswell")
        assert "ZOZ" in dirs

    def test_wind_label_context_does_not_emit_compass(self):
        """
        Bij wind-label context ('zij-aflandig N') mag de N niet als losse
        compass-richting tellen — was voorheen broken door dead 'pass'.
        """
        dirs = self.v._extract_compass_directions("Wind zij-aflandig 5kn")
        # 'aflandig' is een wind-label, geen compass — geen N/O/Z/W uit
        # de bare letters mag opduiken vanuit de wind-label expressie zelf.
        # (er is geen losse 'N' of 'O' woord in deze string)
        assert "N" not in dirs
        assert "O" not in dirs

    def test_validation_flags_unknown_direction(self, monkeypatch):
        """
        End-to-end: een SMS met 'NWN' moet de validatie laten falen
        (niet in allowed_dirs).
        """
        sms = "NWIJK ALERT 20-05 14:00-16:00u: 1.2m WNW, wind 8kn NWN aflandig"
        # Maak een minimal allowed-citations input zonder NWN.
        allowed = {
            'wave_heights_m': [1.2],
            'wind_speeds_kn': [8],
            'wave_periods_s': [],
            'times_hhmm': ['14:00', '16:00'],
            'wind_directions_compass': ['WNW'],
            'wave_directions_compass': ['WNW'],
            'dates_ddmm': ['20-05'],
            'tide_directions': [],
        }
        input_data = {'allowed_citations': allowed}
        result = self.v.validate_sms(sms, input_data)
        # Validation moet falen op de NWN
        assert not result.passed
        assert any('NWN' in issue for issue in result.issues), \
            f"Issues missen NWN-flag: {result.issues}"


def _make_days_input(allowed: dict) -> dict:
    """Helper: bouw structured_input met één day_block + _allowed_citations."""
    return {
        'days': [{
            '_allowed_citations': allowed,
        }],
    }


class TestRangeExpressions:
    """Range-uitdrukkingen '15-20kn', '0.8-1.2m', '6-8s' moeten beide getallen checken."""

    def setup_method(self):
        self.v = SMSValidator()

    def test_wind_range_both_numbers_in_allowed_passes(self):
        sms = "Nwijk di: ZW 15-20kn. Cam: surfweer.nl/webcams/noordwijk/"
        allowed = {
            'wave_heights_m': [],
            'wave_periods_s': [],
            'wind_speeds_kn': [15, 20],
            'wind_directions_compass': ['ZW'],
            'wave_directions_compass': ['ZW'],
            'times_hhmm': [],
        }
        result = self.v.validate_sms(sms, _make_days_input(allowed))
        assert result.passed, f"Issues: {result.issues}"

    def test_wind_range_lower_bound_missing_fails(self):
        """SMS '15-20kn' met allowed_citations={wind_speeds_kn:[20]} (15 ontbreekt) → FAIL."""
        sms = "Nwijk di: ZW 15-20kn. Cam: surfweer.nl/webcams/noordwijk/"
        allowed = {
            'wave_heights_m': [],
            'wave_periods_s': [],
            'wind_speeds_kn': [20],  # alleen 20
            'wind_directions_compass': ['ZW'],
            'wave_directions_compass': ['ZW'],
            'times_hhmm': [],
        }
        result = self.v.validate_sms(sms, _make_days_input(allowed))
        assert not result.passed
        assert any('15' in i and 'Wind speed' in i for i in result.issues), \
            f"Verwacht een 15kn-issue, kreeg: {result.issues}"

    def test_wave_height_range_both_validated(self):
        sms = "Nwijk di: 0,8-1,2m WNW. Cam: surfweer.nl/webcams/noordwijk/"
        allowed = {
            'wave_heights_m': [1.2],  # 0.8 ontbreekt
            'wave_periods_s': [],
            'wind_speeds_kn': [],
            'wind_directions_compass': [],
            'wave_directions_compass': ['WNW'],
            'times_hhmm': [],
        }
        result = self.v.validate_sms(sms, _make_days_input(allowed))
        assert not result.passed
        assert any('0.8' in i or '0,8' in i for i in result.issues), \
            f"Verwacht 0.8m-issue, kreeg: {result.issues}"

    def test_wave_period_range_both_validated(self):
        sms = "Nwijk di: 1,0m 6-8s WNW. Cam: surfweer.nl/webcams/noordwijk/"
        allowed = {
            'wave_heights_m': [1.0],
            'wave_periods_s': [8],  # 6 ontbreekt
            'wind_speeds_kn': [],
            'wind_directions_compass': [],
            'wave_directions_compass': ['WNW'],
            'times_hhmm': [],
        }
        result = self.v.validate_sms(sms, _make_days_input(allowed))
        assert not result.passed
        assert any('6' in i and 'period' in i.lower() for i in result.issues), \
            f"Verwacht period 6s-issue, kreeg: {result.issues}"


class TestTideTimeTolerance:
    """Tide-time tolerance: 15min default, 30min bij rond/omstreeks."""

    def setup_method(self):
        self.v = SMSValidator()

    def _allowed_with_times(self, times):
        return {
            'wave_heights_m': [],
            'wave_periods_s': [],
            'wind_speeds_kn': [],
            'wind_directions_compass': [],
            'wave_directions_compass': [],
            'times_hhmm': times,
        }

    def test_exact_time_30min_off_fails(self):
        """'hoogwater 14:30u' + allowed 14:00 → FAIL (30min > 15min default)."""
        sms = "Nwijk di: hoogwater 14:30u. Cam: surfweer.nl/webcams/noordwijk/"
        allowed = self._allowed_with_times(['14:00'])
        result = self.v.validate_sms(sms, _make_days_input(allowed))
        assert not result.passed, f"Verwacht FAIL maar passed; issues: {result.issues}"
        assert any('14:30' in i for i in result.issues)

    def test_rond_30min_off_passes(self):
        """'rond 14:30u' + allowed 14:00 → PASS (30min binnen rond-tolerance)."""
        sms = "Nwijk di: rond 14:30u nog wat lijntjes. Cam: surfweer.nl/webcams/noordwijk/"
        allowed = self._allowed_with_times(['14:00'])
        result = self.v.validate_sms(sms, _make_days_input(allowed))
        # Mag falen op andere issues maar NIET op de tijd zelf.
        time_issues = [i for i in result.issues if '14:30' in i]
        assert not time_issues, f"14:30 had moeten passen onder 'rond'; issues: {result.issues}"

    def test_omstreeks_60min_off_fails(self):
        """'omstreeks 15u' + allowed 14:00 → FAIL (60min > 30min rond-tolerance)."""
        sms = "Nwijk di: omstreeks 15u nog wat. Cam: surfweer.nl/webcams/noordwijk/"
        allowed = self._allowed_with_times(['14:00'])
        result = self.v.validate_sms(sms, _make_days_input(allowed))
        assert not result.passed
        assert any('15u' in i for i in result.issues), \
            f"Verwacht 15u-issue, kreeg: {result.issues}"


class TestForbiddenUnits:
    """bft / km/u moeten altijd flagged worden ongeacht allowed_citations."""

    def setup_method(self):
        self.v = SMSValidator()

    def test_bft_unit_flagged(self):
        sms = "Nwijk di: ZW 4bft aflandig. Cam: surfweer.nl/webcams/noordwijk/"
        # Maximaal vrijgevige allowed-citations
        allowed = {
            'wave_heights_m': [],
            'wave_periods_s': [],
            'wind_speeds_kn': [4],  # zou matchen als kn, maar bft moet falen
            'wind_directions_compass': ['ZW'],
            'wave_directions_compass': ['ZW'],
            'times_hhmm': [],
        }
        result = self.v.validate_sms(sms, _make_days_input(allowed))
        assert not result.passed
        assert any('bft' in i for i in result.issues), \
            f"Verwacht bft-issue, kreeg: {result.issues}"

    def test_kmu_unit_flagged(self):
        sms = "Nwijk di: ZW 20km/u aflandig. Cam: surfweer.nl/webcams/noordwijk/"
        allowed = {
            'wave_heights_m': [],
            'wave_periods_s': [],
            'wind_speeds_kn': [20],
            'wind_directions_compass': ['ZW'],
            'wave_directions_compass': ['ZW'],
            'times_hhmm': [],
        }
        result = self.v.validate_sms(sms, _make_days_input(allowed))
        assert not result.passed
        assert any('km/u' in i for i in result.issues), \
            f"Verwacht km/u-issue, kreeg: {result.issues}"
