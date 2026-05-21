"""
File-utility helpers.

`append_jsonl_with_rotation` is een opt-in vervanger voor de huidige
`open(path, 'a')` patroon dat `data/forecasts_log.jsonl` en
`data/bias_log.jsonl` ongebreideld laat groeien (geen rotatie). Bij ~4
runs/dag tikt dat aan op enkele MB per jaar, wat de GitHub Actions cache
(10 GB hard cap per repo) en de jsonl-load-tijd in `_check_monthly_budget`-
achtige scans opdrijft.

Bewust géén afhankelijkheden op andere modules in dit pakket: deze helper
moet ook gebruikt kunnen worden voor logs die nog vóór `src/alerts/...`
geladen worden in de orchestration-volgorde. Niet automatisch gewired —
caller (main.py) koppelt het na review aan de bestaande append-points.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Union


def append_jsonl_with_rotation(
    path: Union[str, Path],
    line_dict: dict,
    max_lines: int = 50000,
    keep_archives: int = 3,
) -> None:
    """Append `line_dict` als één JSON-regel naar `path`, met line-count rotatie.

    Args:
        path: Pad naar het jsonl-bestand. Parent-dirs worden aangemaakt als
            ze nog niet bestaan.
        line_dict: Dictionary die naar JSON wordt geserialiseerd (één regel).
        max_lines: Drempelwaarde — als het bestand vóór de append al
            >= `max_lines` regels telt, wordt het eerst geroteerd naar
            `{path}.1.archive` (en de nieuwe regel komt in een leeg bestand).
        keep_archives: Aantal oudste rotaties dat bewaard blijft. Oudere
            (`.N.archive` met N > keep_archives) worden verwijderd. Bij
            `keep_archives=3` blijven `.1.archive` .. `.3.archive` over.

    Rotation strategie (Logback-stijl):
        Bij rotatie schuiven we naar boven: `.2 → .3`, `.1 → .2`, current → `.1`.
        Daarna wordt een nieuw leeg `path` gestart waarin de huidige regel
        wordt geappend. Dit garandeert dat `.1.archive` altijd de meest
        recent geroteerde batch bevat.

    Schrijf-strategie:
        - Append-only op de actieve `path` (geen tmp+rename — een halve
          regel bij crash kost één run, maar geen historische data).
        - Rotatie zelf gebruikt `os.replace` (atomisch op POSIX) zodat een
          crash mid-rotation geen file-system inconsistentie achterlaat.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Stap 1: bepaal of rotatie nodig is. Tellen via line-iteration is
    # voldoende: jsonl is per definitie één entry per regel en groeit
    # langzaam, dus we accepteren de O(n) tel-pas (gebeurt slechts wanneer
    # we vermoeden dicht bij de drempel te zitten).
    if path.exists():
        try:
            with open(path, 'rb') as f:
                # rb + memory-light line-tellen — file kan multi-MB zijn
                # maar past in memory in praktijk; we kiezen iteration
                # om geen volledige read in RAM te dwingen.
                line_count = sum(1 for _ in f)
        except OSError:
            line_count = 0

        if line_count >= max_lines:
            _rotate(path, keep_archives)

    # Stap 2: append de nieuwe regel.
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(line_dict, default=str) + '\n')


def _rotate(path: Path, keep_archives: int) -> None:
    """Schuif oude archives op: .N → .(N+1), current → .1.

    Verwijder alles voorbij `keep_archives`.
    """
    if keep_archives <= 0:
        # Geen archives houden: gewoon huidige file weggooien.
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return

    # Verwijder de oudste (degene die straks > keep_archives zou worden).
    overflow = path.with_name(f"{path.name}.{keep_archives}.archive")
    if overflow.exists():
        with contextlib.suppress(FileNotFoundError):
            overflow.unlink()

    # Schuif van hoog naar laag: .(N-1) → .N.
    for n in range(keep_archives - 1, 0, -1):
        src = path.with_name(f"{path.name}.{n}.archive")
        dst = path.with_name(f"{path.name}.{n + 1}.archive")
        if src.exists():
            import os as _os
            _os.replace(src, dst)

    # Huidige file → .1.archive.
    if path.exists():
        import os as _os
        _os.replace(path, path.with_name(f"{path.name}.1.archive"))
