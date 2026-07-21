"""Regressie voor _parse_sent_verdict (scripts/ingest_reference_message.py).

De parser leidt ONS verstuurde dag-0-verdict af uit de digest/alert-tekst voor de
leer-loop-benchmark (sent_agreement). Brak eerder tweemaal: (1) doorzocht de hele
5-daagse blob i.p.v. dag-0, (2) herkende alleen de fallback-template-frasen, niet
de vrije LLM-frasen ("voor long, mid en fish", "voor longboard of midlength").
"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from ingest_reference_message import _parse_sent_verdict as p


class TestSentVerdictParsing:
    def test_fallback_phrases(self):
        assert p("Nwijk wo: surfbaar 16-22u, top rond 17u") == "surfable"
        assert p("Nwijk vr: longboard 11-14u, top rond 13u") == "longboard"
        assert p("Nwijk do: niet aan beginnen, max rond 8u") == "flat"
        assert p("Nwijk za: klein maar te doen 6-14u") == "surfable"
        assert p("Nwijk zo: alles werkt 6-22u, top rond 7u") == "surfable"

    def test_free_llm_board_phrases(self):
        # LLM (credits aan) schrijft board-lijsten uit i.p.v. "surfbaar (long/mid/fish)".
        assert p("Nwijk za: voor long, mid en fish 7-22u, top rond 19u") == "surfable"
        assert p("Nwijk ma: voor long, mid en fish 6-21u, top rond 7u") == "surfable"
        # midlength aanwezig -> surfable (fallback: "surfbaar (long/mid)"), niet longboard.
        assert p("Nwijk di: voor longboard of midlength 06-17u, top rond 08u") == "surfable"
        # alleen longboard -> longboard.
        assert p("Nwijk di: voor longboard 06-17u, top rond 08u") == "longboard"

    def test_day0_only_not_whole_blob(self):
        # Multi-dag blob: alleen de EERSTE Nwijk-regel telt (dag-0 = verzenddag).
        blob = ("Nwijk vr: flat — 0,3m NNW.\n\n"
                "Nwijk za: surfbaar 6-14u — 1,2m.\n\n"
                "Nwijk zo: alles werkt 6-22u — 1,5m.")
        assert p(blob) == "flat"

    def test_alert_text_parsed(self):
        alert = ("SURF ALERT — Noordwijk di 14 jul\n\n"
                 "Nwijk di: alles werkt 6-22u, top rond 21u — wind draait.")
        assert p(alert) == "surfable"

    def test_empty(self):
        assert p(None) is None
        assert p("") is None
