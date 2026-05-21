"""Shared pytest fixtures voor de SurfWeerWorkflow test-suite.

Probleem dat hier wordt opgelost: cross-test event-loop pollution.

Verschillende test-files gebruiken eigen async-runners:
- `test_rws.py` en `test_open_meteo.py` hebben een `_run(coro)` helper.
- `test_orchestration.py` heeft `_run_async(coro)` die `asyncio.new_event_loop()` gebruikt
  en daarna `asyncio.set_event_loop(asyncio.new_event_loop())` zet.

Voorheen gebruikten de `_run` helpers `asyncio.get_event_loop()`. Combinatie:
1) test_orchestration sluit zijn loop, en zet een nieuwe als default.
2) test_rws gebruikt daarna `asyncio.get_event_loop()` — krijgt de (mogelijk
   nooit-gestarte) nieuwe default loop. Module/test-niveau primitives zoals
   `asyncio.Lock()` of `asyncio.Semaphore()` worden aan DIE loop gebonden,
   maar `run_until_complete` kan op een andere loop draaien → ValueError.

Fix: autouse fixture die VOOR elke test een verse event loop installeert
als de current event loop, en die loop NA de test sluit. Dit garandeert dat:
- Alle `asyncio.Lock()` / `asyncio.Semaphore()` calls binnen test-bodies binden
  aan dezelfde loop als de `_run` helpers.
- Tests kunnen niet meer per ongeluk elkaars loop sluiten.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _fresh_event_loop():
    """Installeer een nieuwe event loop als current loop voor ELKE test.

    Yieldt de loop zodat tests die de loop expliciet nodig hebben hem
    via een eigen fixture kunnen opvragen (niet nodig in deze suite,
    maar het is gratis).
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        # Sluit netjes en zet een nieuwe default loop terug zodat eventuele
        # post-test teardown-code (in andere fixtures) niet stuk gaat op een
        # gesloten loop.
        if not loop.is_closed():
            loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())
