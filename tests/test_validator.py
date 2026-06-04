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

    def test_period_word_seconden_is_validated(self):
        """D-fix: periode uitgeschreven als 'seconden'/'sec' (referentie-forecaster-proza) mag
        niet aan de validator ontsnappen — eerder matchte alleen 'Xs'."""
        allowed = {
            'wave_heights_m': [1.0], 'wave_periods_s': [7],
            'wind_speeds_kn': [], 'wind_directions_compass': [],
            'wave_directions_compass': ['W'], 'times_hhmm': [],
        }
        cam = " Cam: surfweer.nl/webcams/noordwijk/"
        # 12 niet in allowed (7) → moet gevangen worden, ook uitgeschreven.
        for txt in ("periode 12 seconden", "periode 12 sec"):
            r = self.v.validate_sms("Nwijk do: 1,0m W, " + txt + "." + cam,
                                    _make_days_input(allowed))
            assert not r.passed and any('period' in i.lower() for i in r.issues), \
                f"{txt!r} ontsnapte: {r.issues}"
        # Legitieme '7 seconden' (in allowed) mag GEEN false-positive geven.
        r_ok = self.v.validate_sms("Nwijk do: 1,0m W, periode 7 seconden." + cam,
                                   _make_days_input(allowed))
        assert not any('period' in i.lower() for i in r_ok.issues), \
            f"False-positive op legitieme 7 seconden: {r_ok.issues}"


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


class TestPerDayLeakage:
    """Per-dag leakage: een getal valid voor dag X mag NIET op dag Y staan,
    ook al staat het in de globale merged-whitelist. Regression-test voor de
    2026-05-31 hallucinatie waar '2,2m' op donderdag in een SMS stond terwijl
    donderdag's model max 1,6m gaf."""

    def setup_method(self):
        self.v = SMSValidator()

    def _five_day_input(self) -> dict:
        # 5 dagen: do (laatste) heeft alleen 1,6m max. Andere dagen ook klein.
        return {
            'days': [
                {'date': '2026-05-31', '_allowed_citations': {
                    'wave_heights_m':[0.4], 'wave_periods_s':[4.1],
                    'wind_speeds_kn':[7.6], 'wind_directions_compass':['W'],
                    'wave_directions_compass':['WNW'], 'times_hhmm':['19u'],
                }},
                {'date': '2026-06-01', '_allowed_citations': {
                    'wave_heights_m':[0.4], 'wave_periods_s':[5.2],
                    'wind_speeds_kn':[6.4], 'wind_directions_compass':['ZW'],
                    'wave_directions_compass':['WNW'], 'times_hhmm':['06u'],
                }},
                {'date': '2026-06-02', '_allowed_citations': {
                    'wave_heights_m':[0.4], 'wave_periods_s':[4.7],
                    'wind_speeds_kn':[8.5], 'wind_directions_compass':['WZW'],
                    'wave_directions_compass':['W'], 'times_hhmm':['22u'],
                }},
                {'date': '2026-06-03', '_allowed_citations': {
                    'wave_heights_m':[1.4,1.5], 'wave_periods_s':[4.3],
                    'wind_speeds_kn':[11.0], 'wind_directions_compass':['ZZW'],
                    'wave_directions_compass':['WZW'], 'times_hhmm':['22u'],
                }},
                {'date': '2026-06-04', '_allowed_citations': {
                    'wave_heights_m':[1.1,1.3,1.4,1.5,1.6], 'wave_periods_s':[4.3,4.9,5.3],
                    'wind_speeds_kn':[19.0], 'wind_directions_compass':['ZW'],
                    'wave_directions_compass':['WZW'], 'times_hhmm':['14u'],
                    'data_horizon_extended': True,
                }},
            ],
        }

    def test_22m_hallucination_on_thursday_caught(self):
        """De originele 2026-05-31 hallucinatie: '2,2m' op donderdag terwijl
        model max 1,6m geeft. Per-dag check moet flaggen."""
        sms = ("Nwijk zo: 0,4m WNW. Nwijk ma: 0,4m WNW. Nwijk di: 0,4m W. "
               "Nwijk wo: 1,5m WZW met 4,3s rond 22u. "
               "Nwijk do: 2,2m WZW met 3,0s rond 14u, wind 19kn ZW. "
               "Cam: surfweer.nl/webcams/noordwijk/")
        result = self.v.validate_sms(sms, self._five_day_input())
        assert not result.passed, "2,2m moet gevangen worden"
        assert any('2.2' in i and 'dag 2026-06-04' in i for i in result.issues), \
            f"Per-dag flag mist, issues: {result.issues}"

    def test_leakage_from_other_day_caught(self):
        """1,5m bestaat op wo en do allowed, maar niet op ma. Mag dus niet
        op ma worden geciteerd."""
        sms = ("Nwijk zo: 0,4m WNW. "
               "Nwijk ma: 1,5m WNW. "
               "Nwijk di: 0,4m W. Nwijk wo: 1,5m WZW. Nwijk do: 1,6m WZW. "
               "Cam: surfweer.nl/webcams/noordwijk/")
        result = self.v.validate_sms(sms, self._five_day_input())
        assert not result.passed, "1,5m op ma is leakage van wo/do"
        assert any('dag 2026-06-01' in i and '1.5' in i for i in result.issues), \
            f"Leakage-flag mist, issues: {result.issues}"

    def test_correct_per_day_passes(self):
        """Sanity: alle getallen op de juiste dag → valid."""
        sms = ("Nwijk zo: 0,4m WNW met 4,1s, wind 7,6kn W. "
               "Nwijk ma: 0,4m WNW met 5,2s, wind 6,4kn ZW. "
               "Nwijk di: 0,4m W met 4,7s, wind 8,5kn WZW. "
               "Nwijk wo: 1,5m WZW met 4,3s rond 22u, wind 11kn ZZW. "
               "Nwijk do: 1,6m WZW met 5,3s rond 14u, wind 19kn ZW — "
               "verre forecast, kan nog draaien. "
               "Cam: surfweer.nl/webcams/noordwijk/")
        result = self.v.validate_sms(sms, self._five_day_input())
        assert result.passed, f"Issues: {result.issues}"
