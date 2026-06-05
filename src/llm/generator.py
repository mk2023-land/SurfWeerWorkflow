"""
SMS generator module met Claude Sonnet / Haiku.

Bouwt structured-input voor Claude in fysische eenheden (meters, knopen, graden) —
NOOIT scores als golfhoogte/wind doorgeven, dat heeft eerder hallucinaties veroorzaakt
(score 51 werd "51m golfhoogte"). Stijl-template: referentie-forecaster van de referentie-forecaster.

Digest is multi-day (vandaag + 3 dagen vooruit) en bevat per dag de beste window,
piek-condities, tij-richting (opkomend/afgaand) en eerstvolgende hoog/laag, plus
een lokale spring/dood-tij notitie op basis van maan-fase.

Module-layout na splitsing:
- `src/llm/prompts/digest_system.md` — de referentie-forecaster-stijl SYSTEM_PROMPT (eenmalig
  geladen op import zodat prompt-caching maximaal grip krijgt).
- `src/llm/sms_formatting.py` — pure helpers (kompasrichtingen, peak_block,
  maan-fase, ...).
- `src/llm/sms_input.py` — `_prepare_*_input` en alle dag-/uur-shapers.
- `src/llm/sms_fallback.py` — deterministische templates voor LLM-uitval.
- `src/llm/generator.py` — alleen de SMSGenerator class + `_call_claude`.

Re-exports onderaan houden de public API stabiel (`from src.llm.generator
import peak_block` blijft werken).
"""
import json
import logging
from pathlib import Path
from typing import Optional

import anthropic
import httpx

# OverloadedError zit in anthropic._exceptions maar is niet re-exported op
# top-level (RateLimitError wel). Beide tegelijk importeren voor uniforme
# error-handling in _call_claude.
from anthropic._exceptions import OverloadedError, RateLimitError


def _classify_api_error(e: Exception) -> str:
    """Vertaal een Anthropic/SDK-exception naar een korte fallback-reden.

    Onderscheidt 'credits op' / ongeldige key van een generieke API-fout, zodat
    de gebruiker een gerichte waarschuwing krijgt i.p.v. alleen 'er ging iets mis'.
    Een credit-tekort komt bij Anthropic binnen als een billing-gerelateerde
    400/403 ('credit balance is too low' / 'insufficient'); auth-fouten als 401.
    """
    name = type(e).__name__.lower()
    msg = str(e).lower()
    if any(k in msg for k in ('credit', 'billing', 'quota', 'insufficient',
                              'payment', 'balance is too low')):
        return 'credits_exhausted'
    if 'authentication' in name or 'permission' in name or \
            any(k in msg for k in ('invalid x-api-key', 'authentication', '401')):
        return 'auth_error'
    return 'api_error'


# Mensvriendelijke NL-labels per fallback-reden. Eén bron zodat de digest- én
# de alert-tak in main.py dezelfde tekst tonen (voorheen lokaal gedupliceerd).
_FALLBACK_REASON_LABELS = {
    'credits_exhausted': 'Claude-credits op',
    'auth_error': 'Claude API-key ongeldig',
    'no_api_key': 'geen Claude API-key',
    'api_error': 'Claude API onbereikbaar',
    'validation_failed': 'Claude-tekst kwam niet door de check',
    'empty_response': 'Claude gaf lege tekst',
}


def fallback_reason_label(reason: str) -> str:
    """Mensvriendelijke NL-omschrijving van een ``last_fallback_reason``."""
    return _FALLBACK_REASON_LABELS.get(reason, reason)

from src.config import ANTHROPIC_CONFIG
from src.data.models import (
    AlertCandidate,
    HourState,
    ScoreBreakdown,
    SurfWindow,
)

from .sms_fallback import (  # noqa: F401
    _fallback_alert_template,
    _fallback_digest_template,
)

# Re-exports voor backward compatibility — callers in main.py, tests en
# scripts importeren historisch uit generator.py.
from .sms_formatting import (  # noqa: F401
    _COMPASS_16,
    _DAY_NL_SHORT,
    _hours_to,
    _tide_window_quality,
    degrees_to_compass,
    is_blocked_by_ijmuiden_pier,
    moon_phase_info,
    peak_block,
    wind_label_for_noordwijk,
)
from .sms_input import (  # noqa: F401
    _build_allowed_citations,
    _group_by_day,
    _hour_state_to_conditions,
    _prepare_alert_input,
    _prepare_digest_input,
    _summarize_day,
    _tide_summary_for_day,
)

logger = logging.getLogger(__name__)


# Eenmalige load van de SYSTEM_PROMPT vanuit markdown — een module-level
# constante zodat Anthropic prompt-caching (ephemeral) maximaal kan herbruiken:
# identieke bytes per call → cache hit, identieke prefix → cache hit op tools+
# system. Geen datetime.now() of f-string interpolatie in de prompt-tekst
# (zou cache invalidate elke call).
SYSTEM_PROMPT: str = (Path(__file__).parent / "prompts" / "digest_system.md").read_text(
    encoding="utf-8"
)


class SMSGenerator:
    """Genereert SMS berichten met Claude (Sonnet primair, Haiku fallback)."""

    def __init__(self):
        if not ANTHROPIC_CONFIG['api_key']:
            logger.warning("No Anthropic API key configured, using fallback templates only")
            self.client = None
        else:
            # SDK doet built-in retry op connection-errors, 408/409/429/5xx via
            # exponential backoff. max_retries=2 dekt onze meeste transient-cases;
            # voor model-fallback (Sonnet→Haiku bij aanhoudende overload) hebben
            # we onze eigen single-step fallback in _call_claude (zie hieronder).
            #
            # httpx.Timeout splitst connect (5s — snel falen bij netwerk-issue)
            # van overall (60s — genoeg voor Sonnet bij grote prompts).
            self.client = anthropic.Anthropic(
                api_key=ANTHROPIC_CONFIG['api_key'],
                max_retries=2,
                timeout=httpx.Timeout(60.0, connect=5.0),
            )

        # Reden waarom de laatste generate_*-call op de nood-template terugviel,
        # of None als Claude de tekst wél leverde. main.py leest dit na elke
        # call om de gebruiker te waarschuwen (incl. 'credits_exhausted').
        self.last_fallback_reason = None if self.client else 'no_api_key'
        # Sub-uitkomst van _generate_with_retry: 'api_error' of 'validation_failed'.
        self._retry_outcome = None

    # ---------- public API ----------

    def generate_alert_sms(self, alert: AlertCandidate) -> str:
        # Spiegelt generate_digest_sms: zet last_fallback_reason zodat main.py
        # ook bij een ALERT de gebruiker kan waarschuwen dat Claude niet de
        # tekst leverde (geen key / credits op / API-fout / validatie 3× faal).
        # Voorheen viel deze tak stil terug op de template zonder enige melding.
        if not self.client:
            self.last_fallback_reason = 'no_api_key'
            return self._fallback_alert_template(alert)
        self.last_fallback_reason = None
        self._retry_outcome = None
        try:
            structured_input = self._prepare_alert_input(alert)
            max_tokens = ANTHROPIC_CONFIG.get(
                'max_tokens_alert', ANTHROPIC_CONFIG['max_tokens']
            )
            text = self._generate_with_retry(
                structured_input, max_tokens=max_tokens, kind='alert',
            )
            if text:
                return text
            # _generate_with_retry gaf None → API-fout of validatie 3× gefaald.
            self.last_fallback_reason = self._retry_outcome or 'empty_response'
            return self._fallback_alert_template(alert)
        except Exception as e:
            self.last_fallback_reason = _classify_api_error(e)
            logger.error(
                f"Failed to generate alert SMS with Claude "
                f"({self.last_fallback_reason}): {e}"
            )
            return self._fallback_alert_template(alert)

    def generate_digest_sms(
        self,
        hour_states: list[HourState],
        scores: list[ScoreBreakdown],
        windows: list[SurfWindow],
        forecast_summary: Optional[dict] = None,
        wind_spread_series: Optional[list[dict]] = None,
    ) -> str:
        if not self.client:
            self.last_fallback_reason = 'no_api_key'
            return self._fallback_digest_template(hour_states, scores, windows)
        self.last_fallback_reason = None
        self._retry_outcome = None
        try:
            structured_input = self._prepare_digest_input(
                hour_states, scores, windows,
                forecast_summary or {},
                wind_spread_series=wind_spread_series,
            )
            max_tokens = ANTHROPIC_CONFIG.get(
                'max_tokens_digest', ANTHROPIC_CONFIG['max_tokens']
            )
            text = self._generate_with_retry(
                structured_input, max_tokens=max_tokens, kind='digest',
            )
            if text:
                return text
            # _generate_with_retry gaf None → API-fout of validatie 3× gefaald.
            self.last_fallback_reason = self._retry_outcome or 'empty_response'
            return self._fallback_digest_template(hour_states, scores, windows)
        except Exception as e:
            self.last_fallback_reason = _classify_api_error(e)
            logger.error(
                f"Failed to generate digest SMS with Claude "
                f"({self.last_fallback_reason}): {e}"
            )
            return self._fallback_digest_template(hour_states, scores, windows)

    def _generate_with_retry(
        self,
        structured_input: dict,
        max_tokens: int,
        kind: str,
        max_attempts: int = 3,
    ) -> Optional[str]:
        """
        Genereer LLM-tekst met validator-feedback retry-loop.

        Bij hallucinatie (validator faalt) wordt de uitkomst + de issues
        teruggegeven aan Claude met de opdracht "fix dit en genereer opnieuw".
        Claude krijgt zo tot `max_attempts` pogingen om binnen de
        `_allowed_citations` te blijven. Pas als alle pogingen falen
        valt de caller terug op de deterministische fallback-template.

        Bij echte API-fouten (network down, beide modellen overloaded) returnt
        `_call_claude` None → we breken meteen af en laten caller fallback doen.
        """
        # Lazy import om circular dependency tussen generator ↔ validator te
        # voorkomen.
        from src.llm.validator import SMSValidator
        validator = SMSValidator()

        messages: list[dict] = [{
            "role": "user",
            "content": json.dumps(structured_input, indent=2, default=str),
        }]

        for attempt in range(max_attempts):
            text = self._call_claude(messages, max_tokens=max_tokens)
            if text is None:
                # API faalde echt (network/overload/auth) — geen retry zin.
                logger.warning(f"{kind} attempt {attempt + 1}: Claude API faalde")
                self._retry_outcome = 'api_error'
                return None

            # Anti-hallucinatie check
            result = validator.validate_sms(text, structured_input)
            if result.passed:
                if attempt > 0:
                    logger.info(
                        f"{kind} attempt {attempt + 1}/{max_attempts}: validatie OK na retry"
                    )
                return text

            # Validatie faalde — geef feedback en probeer opnieuw
            issues_str = "\n".join(f"- {issue}" for issue in result.issues)
            logger.warning(
                f"{kind} attempt {attempt + 1}/{max_attempts} validatie faalde: "
                f"{len(result.issues)} issues"
            )

            if attempt == max_attempts - 1:
                # Laatste poging faalde — caller doet fallback.
                logger.error(
                    f"{kind}: alle {max_attempts} pogingen faalden op validatie. "
                    f"Laatste issues: {result.issues}"
                )
                # Volledige afgekeurde tekst loggen — zonder dit is een
                # validator-false-positive niet te debuggen (de INFO-regel kapt
                # op 80 chars en MANUAL_RUN archiveert de fallback niet).
                logger.error(f"{kind}: laatste afgekeurde tekst: {text!r}")
                self._retry_outcome = 'validation_failed'
                return None

            # Voeg assistant-output + correctie-instructie toe aan conversation.
            messages.append({"role": "assistant", "content": text})
            messages.append({
                "role": "user",
                "content": (
                    "Je vorige bericht bevat hallucinaties — getallen, tijden of "
                    "richtingen die NIET in de _allowed_citations van de "
                    "betreffende dag staan. Concrete fouten:\n"
                    f"{issues_str}\n\n"
                    "Genereer het bericht NU OPNIEUW, in dezelfde referentie-forecaster-stijl en "
                    "format, maar zonder deze fouten. Loop voordat je schrijft "
                    "elke dag mentaal langs _allowed_citations.wind_directions_compass "
                    "en wave_directions_compass — gebruik UITSLUITEND die richtingen. "
                    "Geef alleen het nieuwe bericht terug, geen uitleg vooraf."
                ),
            })

        return None

    # ---------- methode-aliassen op SMSGenerator ----------
    # Bewust als methoden — main.py en bestaande tests roepen
    # `self.sms_generator._prepare_alert_input(...)` en
    # `self.sms_generator._fallback_digest_template(...)`. De
    # onderliggende functies (zonder `self`) leven in sms_input /
    # sms_fallback; deze methoden zijn dunne adapters.

    def _prepare_alert_input(self, alert: AlertCandidate) -> dict:
        return _prepare_alert_input(alert)

    def _prepare_digest_input(
        self,
        hour_states: list[HourState],
        scores: list[ScoreBreakdown],
        windows: list[SurfWindow],
        forecast_summary: dict,
        wind_spread_series: Optional[list[dict]] = None,
    ) -> dict:
        return _prepare_digest_input(
            hour_states, scores, windows, forecast_summary,
            wind_spread_series=wind_spread_series,
        )

    def _group_by_day(self, hour_states, scores):
        return _group_by_day(hour_states, scores)

    def _summarize_day(self, *args, **kwargs):
        return _summarize_day(*args, **kwargs)

    def _hour_state_to_conditions(self, state: HourState) -> dict:
        return _hour_state_to_conditions(state)

    def _build_allowed_citations(self, *args, **kwargs):
        return _build_allowed_citations(*args, **kwargs)

    def _tide_summary_for_day(self, day_states, peak_state):
        return _tide_summary_for_day(day_states, peak_state)

    def _fallback_alert_template(self, alert: AlertCandidate) -> str:
        return _fallback_alert_template(alert)

    def _fallback_digest_template(
        self,
        hour_states: list[HourState],
        scores: list[ScoreBreakdown],
        windows: list[SurfWindow],
    ) -> str:
        return _fallback_digest_template(hour_states, scores, windows)

    # ---------- LLM call ----------

    def _call_claude(
        self,
        structured_input_or_messages,
        max_tokens: Optional[int] = None,
    ) -> Optional[str]:
        """
        Anthropic Messages API call met built-in SDK retry + model fallback.

        Accepteert:
          - `dict`: backwards-compat — wordt naar één user-message gewrapped.
          - `list[dict]`: messages-list voor multi-turn retry-conversaties
            (zie `_generate_with_retry`).

        Strategie:
          1. Primair model (Sonnet) — SDK doet transparent retry op 429/5xx
             via `max_retries=2` (zie __init__). Wij vangen alleen
             OverloadedError + RateLimitError nadat de SDK al retried heeft.
          2. Bij aanhoudende overload: één call naar fallback_model (Haiku).
          3. Daarna geven we op en laat caller fallback-template gebruiken.

        SYSTEM_PROMPT wordt verzonden als content-block list met
        `cache_control: ephemeral` zodat de Sonnet-side prompt-cache de
        ~3k-token referentie-forecaster-prompt kan herbruiken. Eerste call kost ~1.25×
        write-premium, opvolgende calls ~0.1× lees-prijs. Bij ~30-60
        calls/maand levert dat 50-70% korting op input-tokens.
        """
        effective_max_tokens = max_tokens or ANTHROPIC_CONFIG['max_tokens']

        # Normaliseer naar messages-list. Dict-input = single user message.
        if isinstance(structured_input_or_messages, dict):
            messages = [{
                "role": "user",
                "content": json.dumps(
                    structured_input_or_messages, indent=2, default=str
                ),
            }]
        else:
            messages = structured_input_or_messages

        # Cached system block — moet identieke bytes hebben elke call zodat
        # de prefix-match werkt. Geen datetime / uuid / lokale state hier.
        system_blocks = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        def _do_call(model_name: str):
            message = self.client.messages.create(
                model=model_name,
                max_tokens=effective_max_tokens,
                temperature=ANTHROPIC_CONFIG['temperature'],
                system=system_blocks,
                messages=messages,
            )
            # Prompt-caching telemetrie op DEBUG: laat zien of de cache hits
            # binnenkomen. Bij `cache_read_input_tokens == 0` over meerdere
            # calls heen is er een silent invalidator (datetime, varying tools).
            usage = getattr(message, 'usage', None)
            if usage is not None:
                cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
                cache_write = getattr(usage, 'cache_creation_input_tokens', 0) or 0
                logger.debug(
                    f"{model_name} prompt-cache: read={cache_read} "
                    f"write={cache_write} input={getattr(usage, 'input_tokens', 0)}"
                )
            sms_text = message.content[0].text.strip()
            logger.info(
                f"Generated SMS via {model_name} "
                f"(max_tokens={effective_max_tokens}): {sms_text[:80]}..."
            )
            return sms_text

        primary_model = ANTHROPIC_CONFIG['model']
        fallback_model = ANTHROPIC_CONFIG.get('fallback_model')

        # Stap 1: primary. SDK doet zelf max_retries=2 op transient errors.
        try:
            return _do_call(primary_model)
        except (OverloadedError, RateLimitError) as e:
            logger.warning(
                f"{primary_model} {type(e).__name__} na SDK-retries — "
                f"fallback naar {fallback_model or '(geen)'}"
            )

        # Stap 2: fallback model, één poging. SDK doet ook hier max_retries=2.
        if fallback_model and fallback_model != primary_model:
            try:
                return _do_call(fallback_model)
            except (OverloadedError, RateLimitError) as e:
                logger.error(
                    f"{fallback_model} {type(e).__name__} — beide modellen "
                    f"uitgeput, return None"
                )

        logger.error(
            "Anthropic API niet beschikbaar — beide modellen overloaded"
        )
        return None
