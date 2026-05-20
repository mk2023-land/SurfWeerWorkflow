"""
Unit tests voor src/llm/validator.py — output validator (anti-hallucinatie).

Regressie voor B8: het dead-code `pass` blok deed niets en onbekende
2-4 letter NOZW-tokens (zoals 'NWN', 'ZOZ') werden niet gevangen.
"""
from __future__ import annotations

import os
import sys

import pytest

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
