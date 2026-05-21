"""
Tests voor `src.util_files.append_jsonl_with_rotation`.

Dekken:
  - Basis append: één regel → file met één regel.
  - Geen rotatie wanneer onder de drempel.
  - Rotatie wanneer de drempel bereikt is — current → .1.archive en de
    nieuwe regel komt in een verse current.
  - keep_archives respecteren: .4.archive wordt verwijderd bij keep=3.
  - Parent-dir wordt aangemaakt.
  - JSON-serialisatie van niet-standaard types (default=str fallback).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.util_files import append_jsonl_with_rotation


def _read_lines(path: Path) -> list[str]:
    with open(path) as f:
        return [line.rstrip('\n') for line in f if line.strip()]


class TestBasicAppend:
    def test_append_single_line(self, tmp_path):
        log = tmp_path / "log.jsonl"
        append_jsonl_with_rotation(log, {"a": 1})

        assert log.exists()
        lines = _read_lines(log)
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"a": 1}

    def test_append_multiple_lines(self, tmp_path):
        log = tmp_path / "log.jsonl"
        for i in range(5):
            append_jsonl_with_rotation(log, {"i": i}, max_lines=1000)

        lines = _read_lines(log)
        assert len(lines) == 5
        assert [json.loads(line)['i'] for line in lines] == [0, 1, 2, 3, 4]

    def test_creates_parent_dir(self, tmp_path):
        log = tmp_path / "nested" / "subdir" / "log.jsonl"
        append_jsonl_with_rotation(log, {"a": 1})
        assert log.exists()
        assert json.loads(_read_lines(log)[0]) == {"a": 1}

    def test_non_json_native_value_uses_default_str(self, tmp_path):
        """datetime is niet JSON-serialisable native; default=str fallback."""
        log = tmp_path / "log.jsonl"
        dt = datetime(2026, 5, 21, 10, 0, 0)
        append_jsonl_with_rotation(log, {"ts": dt})
        lines = _read_lines(log)
        # Geen exception → mag al door zijn; check dat de timestamp als
        # string in de payload zit.
        payload = json.loads(lines[0])
        assert isinstance(payload['ts'], str)
        assert '2026-05-21' in payload['ts']


class TestRotation:
    def test_no_rotation_when_under_threshold(self, tmp_path):
        log = tmp_path / "log.jsonl"
        for i in range(5):
            append_jsonl_with_rotation(log, {"i": i}, max_lines=10)

        assert log.exists()
        assert not (tmp_path / "log.jsonl.1.archive").exists()
        assert len(_read_lines(log)) == 5

    def test_rotation_triggers_at_threshold(self, tmp_path):
        log = tmp_path / "log.jsonl"
        # max_lines=3 → vóór de 4e append zit het bestand op 3 regels en
        # roteert. Daarna start de nieuwe regel in een verse current-file.
        for i in range(3):
            append_jsonl_with_rotation(log, {"i": i}, max_lines=3)

        # Voor de rotatie: 3 regels in current, geen archive.
        assert len(_read_lines(log)) == 3
        assert not (tmp_path / "log.jsonl.1.archive").exists()

        # De vierde append triggert rotatie (file is al == max_lines).
        append_jsonl_with_rotation(log, {"i": 3}, max_lines=3)
        archive = tmp_path / "log.jsonl.1.archive"
        assert archive.exists()
        assert len(_read_lines(archive)) == 3
        # Current bevat alleen de nieuwste regel.
        assert _read_lines(log) == [json.dumps({"i": 3})]

    def test_rotation_shifts_old_archives(self, tmp_path):
        """Drie achtereenvolgende rotaties moeten .1 → .2 → .3 schuiven."""
        log = tmp_path / "log.jsonl"
        # Rotatie nummer 1: vul tot drempel en trigger.
        for i in range(3):
            append_jsonl_with_rotation(log, {"i": i, "rot": 1}, max_lines=3)
        append_jsonl_with_rotation(log, {"i": 99, "rot": 1}, max_lines=3)
        assert (tmp_path / "log.jsonl.1.archive").exists()

        # Rotatie nummer 2: vul opnieuw tot drempel en trigger.
        for i in range(2):
            append_jsonl_with_rotation(log, {"i": i, "rot": 2}, max_lines=3)
        append_jsonl_with_rotation(log, {"i": 99, "rot": 2}, max_lines=3)
        assert (tmp_path / "log.jsonl.1.archive").exists()
        assert (tmp_path / "log.jsonl.2.archive").exists()
        # De oudste rotatie zit nu in .2; .1 bevat de recente cycle (met een
        # residual record van cycle 1 vanwege Logback-stijl: trigger-record
        # komt in nieuwe file, niet in archive). Geen strikte rot-zuiverheid.
        latest_archive = _read_lines(tmp_path / "log.jsonl.1.archive")
        assert len(latest_archive) == 3

        # Rotatie nummer 3.
        for i in range(2):
            append_jsonl_with_rotation(log, {"i": i, "rot": 3}, max_lines=3)
        append_jsonl_with_rotation(log, {"i": 99, "rot": 3}, max_lines=3)
        assert (tmp_path / "log.jsonl.1.archive").exists()
        assert (tmp_path / "log.jsonl.2.archive").exists()
        assert (tmp_path / "log.jsonl.3.archive").exists()

    def test_keep_archives_drops_oldest(self, tmp_path):
        """Bij keep_archives=2: na 3 rotaties moet .3 NOOIT bestaan; de
        oudste rotatie (was .2) wordt overschreven en .1 schuift door."""
        log = tmp_path / "log.jsonl"

        # Helper: pump één volledige rotatie-cyclus.
        def rotate_once(tag: int) -> None:
            for i in range(3):
                append_jsonl_with_rotation(
                    log, {"i": i, "rot": tag}, max_lines=3, keep_archives=2
                )
            append_jsonl_with_rotation(
                log, {"i": 99, "rot": tag}, max_lines=3, keep_archives=2
            )

        rotate_once(1)
        rotate_once(2)
        rotate_once(3)

        assert (tmp_path / "log.jsonl.1.archive").exists()
        assert (tmp_path / "log.jsonl.2.archive").exists()
        # Met keep_archives=2 mag er nooit een .3.archive zijn.
        assert not (tmp_path / "log.jsonl.3.archive").exists()

        # .1 bevat de meest recente rotatie (3 regels; mix van cycle-boundary).
        latest = _read_lines(tmp_path / "log.jsonl.1.archive")
        assert len(latest) == 3

    def test_keep_archives_zero_drops_old_data(self, tmp_path):
        """keep_archives=0 → geen archives bewaard; current wordt simpelweg
        weggegooid bij rotatie."""
        log = tmp_path / "log.jsonl"
        for i in range(3):
            append_jsonl_with_rotation(log, {"i": i}, max_lines=3, keep_archives=0)
        append_jsonl_with_rotation(log, {"i": 99}, max_lines=3, keep_archives=0)

        assert log.exists()
        assert _read_lines(log) == [json.dumps({"i": 99})]
        # Geen archives.
        assert list(tmp_path.glob("log.jsonl.*.archive")) == []
