"""Shared pytest fixtures voor de SurfWeerWorkflow test-suite.

Note over event-loop scope:
- pytest-asyncio is geconfigureerd via `pyproject.toml`:
    asyncio_mode = "auto"
    asyncio_default_fixture_loop_scope = "function"
- Dit garandeert dat elke async test/fixture in een eigen verse event loop
  draait. De vroegere autouse `_fresh_event_loop` fixture die handmatig
  `asyncio.new_event_loop()` deed voor elke test is daarmee niet meer nodig
  en is verwijderd.
- Sync helpers in test_rws.py / test_open_meteo.py / test_orchestration.py
  die `asyncio.get_event_loop()` of `asyncio.new_event_loop()` gebruiken
  blijven werken: pytest-asyncio installeert een current loop per test
  scope, en sync helpers die zelf een nieuwe loop maken sluiten die ook
  weer netjes.
"""
from __future__ import annotations
