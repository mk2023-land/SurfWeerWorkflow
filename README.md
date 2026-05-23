# Noordwijk Surf Alert Systeem

[![Surf Alert Check](https://github.com/mk2023-land/SurfWeerWorkflow/actions/workflows/check.yml/badge.svg)](https://github.com/mk2023-land/SurfWeerWorkflow/actions/workflows/check.yml)

Geautomatiseerd surfweer alert systeem voor Noordwijk. Stuurt één ochtenddigest (5-daagse outlook) en daarnaast push/email/SMS-alerts wanneer de algoritme-score boven drempel komt. Notificaties via ntfy.sh push (default), SMTP-mail, of Twilio-SMS.

## 📋 Overzicht

Dit systeem analyseert surfcondities voor Noordwijk door:

1. **Data verzameling** uit meerdere bronnen (Open-Meteo multi-model: KNMI + ECMWF + GFS, Rijkswaterstaat DDAPI20 boeien)
2. **Scoring** van surfcondities (0-100 punten) op basis van golf, wind, tij, swell richting, plus modifiers voor wave-age, energy-flux, Iribarren, pier-refractie en wind-wave interactie
3. **5 alert types** detecteren (T1 swell arrival via boei-spectrum trends, T2 wind shift, T3 wind dip, T4 sustained groundswell-through-windsea, T5 tide-gated windows)
4. **Notificaties** versturen via ntfy.sh (default, gratis push), SMTP-mail of Twilio-SMS, met Claude Sonnet 4.5 (Haiku als fallback) voor natuurlijke Nederlandse berichten in forecaster-stijl
5. **Automatische runs** op GitHub Actions: 8 scheduled runs/dag — 5 ochtend-buffers (03:00-05:00 UTC) voor cron-jitter, 3 verspreid voor middag/avond/nacht. `is_morning_first_run()` dedupliceert zodat er per dag één ochtenddigest komt; alle runs kunnen alerts firen.

### Hoe het werkt

Het systeem berekent per uur een score op basis van:

- **Golf (38pt)**: Hoogte, periode, energy-flux, wave-age, Iribarren breaker-type, partition-aware (swell ×1.00 + wind-zee ×0.65)
- **Wind (32pt)**: Snelheid + richting (cosinus-additief), gust-penalty, diurnal decay, wave-face quality bij onshore
- **Tij (20pt)**: Waterstand, fase, periode-afhankelijk venster (groundswell breder dan wind-sea), spring/doodtij modulator, timing-fit bonus
- **Swell richting (10pt)**: W-NNW = beste, continue pier-refractie rond NNO (10°) i.p.v. binair geblokkeerd

Plus multiplicatieve size-cap (#13) zodat marginale golven niet via perfect environment alsnog 60+ halen, en confidence-penalty op basis van multi-model wind-spread.

Scores ≥60 = surfbaar (shortboard, alert-candidate), ≥42 = longboard-only (alleen digest), ≥75 = alert-waardig.

## 🚀 Quick Start

### Lokale setup

```bash
# Clone repository
git clone https://github.com/mk2023-land/SurfWeerWorkflow.git
cd SurfWeerWorkflow

# Install dependencies (uv aanbevolen — sneller en reproduceerbaar via uv.lock)
uv sync --frozen
# Of klassiek: pip install -r requirements.txt

# Configureer environment variables
cp .env.example .env
# Edit .env met je API keys

# Draai systeem (dry run — LLM-call wordt gemaakt, geen notificatie verstuurd)
uv run python -m src.main --dry-run
```

### GitHub Actions setup

1. **Repository aanmaken** (private aanbevolen)
2. **GitHub Secrets** configureren (Settings → Secrets and variables → Actions → New repository secret):
   - `ANTHROPIC_API_KEY` — voor Claude Sonnet 4.5 (primair) + Haiku 4.5 (fallback) tekst-generatie

   Daarna één set afhankelijk van je gekozen notifier (default: `ntfy`):

   - **ntfy.sh push (gratis, aanbevolen):**
     - `NTFY_TOPIC` — geheime topic-naam (zelfverzonnen, onraadbaar)
   - **SMTP-mail (gratis):**
     - `SMTP_USER`, `SMTP_PASSWORD`, `RECIPIENT_EMAIL`
   - **Twilio SMS (betaald, ~€0.08/sms):**
     - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `RECIPIENT_PHONE_NUMBER`

   **Variables** (Settings → Variables) — optioneel, hebben sensible defaults:
   - `NOTIFIER`: `ntfy` (default), `email`, of `twilio`
   - `ALERTS_ENABLED`: `true` (default) of `false` voor alleen daily digest
   - `COOLDOWN_HOURS`: `4` (default) — minimaal aantal uren tussen alerts
   - `MAX_ALERTS_PER_WEEK`: `8` (default)

3. **Workflows activeren**:
   - `check.yml`: Hoofdpijplijn, 8x scheduled per dag + auto-commit van `data/sms_archive/` (contents:write permission nodig)
   - `rebuild-baseline.yml`: Jaarlijkse baseline update (1 januari, of handmatig)
   - `run-validation.yml`: Backtest validatie (op PR met src/-changes, of handmatig)
   - `smoke-test.yml`: End-to-end dry-run (dagelijks 02:00 UTC + op PR)

## 📊 Architectuur

```
src/
├── config.py              # Configuratie en constants
├── main.py                # Hoofdscript
├── util.py                # Gedeelde tz/time helpers
├── data/
│   ├── models.py          # Data structuren
│   └── sources/
│       ├── open_meteo.py  # Weer data API (multi-model: KNMI + ECMWF + GFS)
│       └── rws.py         # Rijkswaterstaat DDAPI20 WaterWebservices
├── scoring/
│   ├── deconstruct.py     # Swell deconstructie + partition-aware energy
│   ├── hourly.py          # Per-uur scoring (energy-flux, wave-age, Iribarren, ...)
│   ├── windows.py         # Window analyse + multi-window detectie
│   ├── daylight.py        # Daglicht-filter (asymmetrisch dawn/dusk)
│   ├── bias_correction.py # Real-time IJG1-boei bias correctie (exp decay)
│   └── trigger_T1.py      # Boei-spectrum history + swell-arrival detector
├── alerts/
│   ├── detectors.py       # 5 alert detectors (T1-T5)
│   └── engine.py          # Alert besluit logica + cooldown + budget
├── llm/
│   ├── generator.py       # Bericht generator (Sonnet 4.5 → Haiku fallback)
│   └── validator.py       # Output validatie (anti-hallucinatie)
├── notify/
│   ├── __init__.py        # get_notifier() factory + NL-datum helper
│   ├── ntfy.py            # ntfy.sh push (default)
│   ├── mail.py            # SMTP-mail
│   └── twilio.py          # Twilio-SMS (optionele fallback)
└── baseline/
    └── seasonal.py        # Seizoensbaseline builder

tests/
├── test_scoring.py          # Unit tests (scoring + windows)
├── test_bias_correction.py  # Bias-correctie tests
├── test_trigger_T1.py       # T1 detector tests
├── test_detectors.py        # T1-T5 alert detector tests
├── test_engine_state.py     # AlertEngine cooldown/budget tests
├── test_open_meteo.py       # Open-Meteo client tests
├── test_rws.py              # Rijkswaterstaat DDAPI20 tests
├── test_validator.py        # LLM output-validator tests
├── test_generator.py        # LLM SMS-generator tests
├── test_notify.py           # Notifier (ntfy/mail/twilio) tests
└── test_orchestration.py    # Main-pipeline wiring tests

scripts/
├── send_test_notification.py        # End-to-end test van notifier-pipeline
├── ingest_forecaster_message.py     # Forecaster-referentieberichten archiveren als training-labels (private repo)
└── run_validation_backtest.py       # Backtest validatie tegen historische SMS dataset

research/                  # 9 onderzoeksrapporten + master plan
data/
├── state.json             # Runtime state (cooldowns, weekly counts, last_digest_time)
├── seasonal_baseline.json # Seizoensbaseline (jaarlijks rebuild, committed)
├── forecasts_log.jsonl    # Run-by-run audit log (gepersisteerd via cache)
├── bias_log.jsonl         # Forecast-vs-observation bias (Sprint 4 training)
├── buoy_spectra_history.jsonl # T1 detector rolling buoy history
└── sms_archive/
    └── YYYY-MM.jsonl      # Auto-commit per succesvolle digest/alert (model-training)

# Forecaster-referentie-archief leeft in een aparte private repo
# (auteursrechtelijk materiaal — niet in deze repo).

.github/workflows/
├── check.yml              # Hoofd workflow (8 cron-runs/dag, auto-commit sms_archive)
├── rebuild-baseline.yml   # Baseline update (jaarlijks)
├── run-validation.yml     # Backtest validatie (PR + handmatig)
└── smoke-test.yml         # E2E dry-run (dagelijks + PR)
```

## 🔑 API Keys Setup

### Anthropic API (Claude Sonnet 4.5 primair, Haiku 4.5 fallback)

1. Ga naar https://console.anthropic.com/
2. Maak account aan
3. Genereer API key
4. Sla op als GitHub Secret: `ANTHROPIC_API_KEY`

Sonnet wordt gebruikt voor de daadwerkelijke tekst-generatie omdat het significant rijkere Nederlandse forecaster-stijl prose levert (wind-wave interactie expliciet benoemd, uncertainty gerendered, tij-tijden verweven in lopende zinnen). Bij Sonnet-overload (HTTP 529) schakelt de pipeline na exponential backoff automatisch over op Haiku. Verwachte kosten: €0,50–€1/maand bij 30–60 calls.

### Notifier setup

Default is **ntfy.sh push** (gratis, geen account):

1. Installeer de ntfy-app op je telefoon (iOS / Android)
2. Subscribe op een zelfverzonnen, onraadbare topic (bijv. `nwijksurf-<jouwinitialen>-<random>`)
3. Zet dezelfde naam in `.env` (`NTFY_TOPIC=...`) en in GitHub Secrets

Andere kanalen (SMTP-mail of Twilio-SMS) staan beschreven in `.env.example`.

## 🧪 Testing

### Unit tests

```bash
# Run alle tests (262 tests in totaal: scoring + bias correction + T1 + detectors +
# engine + open-meteo + rws + LLM validator + LLM generator + notify + orchestration +
# util_files)
pytest tests/ -v

# Run alleen scoring tests
pytest tests/test_scoring.py -v

# Run bias-correctie of T1-detector tests
pytest tests/test_bias_correction.py -v
pytest tests/test_trigger_T1.py -v
```

### Backtest validatie

```bash
# Run validatie tegen historische SMS dataset
python scripts/run_validation_backtest.py

# Verwacht: ≥70% accuracy
```

## 📈 Monitoring

### Logs

- **Surf alerts**: `data/surf_alert.log`
- **Forecasts log**: `data/forecasts_log.jsonl` (JSON per run, incl. `sms_text_full`)
- **State**: `data/state.json` (runtime state — cooldowns, weekly counts, last_digest_time)
- **Bias log**: `data/bias_log.jsonl` (forecast-vs-observation, voor Sprint 4 XGBoost training)
- **Boei-spectrum history**: `data/buoy_spectra_history.jsonl` (rolling input voor T1 swell-arrival detector)
- **SMS-archief**: `data/sms_archive/YYYY-MM.jsonl` — elke verstuurde digest/alert permanent in git voor model-training, auto-commit door check.yml
- **Forecaster-referentie-archief**: aparte private repo (user-geleverde SMS + parse-metadata, training-labels). Pad configureerbaar via `FORECASTER_ARCHIVE_DIR` env-var.

**Persistentie**: De GitHub Actions cache bewaart de hele `data/` map tussen runs (unique key per run + restore-keys fallback). Daarnaast wordt `data/sms_archive/` na elke succesvolle send naar git gepushed, zodat training-data permanent bewaard blijft ook als de cache zou expireren.

### Log formaat

Elke run wordt gelogd in `forecasts_log.jsonl`:

```json
{
  "timestamp": "2026-05-23T06:14:50",
  "run_type": "scheduled",
  "scores_today_peak": 0,
  "scores_tomorrow_peak": 0,
  "alert_types_detected": [],
  "windows_total": 0,
  "windows_alertworthy": 0,
  "decision": "digest",
  "sms_sent": "SUCCESS (ntfy): id=OzHyegWrxpK5, msg=Nwijk za: Flat, swell nihil...",
  "sms_text_full": "Nwijk za: Flat, swell nihil — hoogste golf 0,3m NW met 2,1s rond 23:00u, ...",
  "llm_used": true,
  "llm_validation_passed": true,
  "buoy_ijg1_height": 0.22,
  "buoy_ijg1_period": 4.1,
  "buoy_a12_period": 4.0,
  "bias_correction_applied": true,
  "rws_status": "ok",
  "openmeteo_status": "ok",
  "seasonal_baseline_loaded": true
}
```

Bij succesvolle digest/alert wordt een gestripte versie (zonder run-metadata) ook in `data/sms_archive/YYYY-MM.jsonl` opgeslagen voor model-training.

## 🔄 Rollout Strategie

**Week 1 (Safety)**:
- `ALERTS_ENABLED = false`
- Alleen daily digest (07:00)
- Monitoring van scores

**Week 2 (Conservative)**:
- `ALERTS_ENABLED = true`
- Cooldown 8u (dubbel normaal)
- Max 4 alerts/week

**Week 3+ (Normaal)**:
- Cooldown 4u
- Max 8 alerts/week
- Volledige functionaliteit

## ⚙️ Configuratie

Belangrijke configuratie opties in `src/config.py`:

```python
# Alert drempelwaarden
ALERT_CONFIG = {
    'min_peak_score': 75,           # Min score voor alert
    'cooldown_hours_between_alerts': 4,  # Tussen alerts
    'max_alerts_per_week': 8,       # Max alerts per week
    'alerts_enabled': True          # Alerts aan/uit
}

# Scoring gewichten (v4: tij verhoogd 15→20 want top-3 factor voor beachbreaks)
SCORING_WEIGHTS = {
    'golf_max': 38,       # Golf component max
    'wind_max': 32,       # Wind component max
    'tide_max': 20,       # Tij component max
    'swell_dir_max': 10   # Richting bonus max
}

# Dubbele surf-drempels
SURF_THRESHOLDS = {
    'surfable': 60,       # shortboard, alert-candidate
    'longboard': 42,      # longboard-only, alleen voor digest
    'min_golf_surfable': 15,   # min wave-energy floor (~1m bij 6s)
    'min_golf_longboard': 5,   # ~0.5-0.6m bij 5s
}

# Anthropic (Sonnet primair, Haiku fallback bij overload)
ANTHROPIC_CONFIG = {
    'model': 'claude-sonnet-4-5',
    'fallback_model': 'claude-haiku-4-5',
    'max_tokens_alert': 300,
    'max_tokens_digest': 1200,
    'temperature': 0.4,
}
```

## 📱 Bericht-voorbeelden

### Alert (push of mail)
```
NWIJK ALERT 06-08 06:00-08:00u: groundswell 10s door windgolven heen,
0,9m WNW, wind 6kn O aflandig, opgaand tij. Cam: surfweer.nl/webcams/noordwijk/
```

### Digest (5-daagse outlook, push of mail — titel: "Surfweerbericht van za 23 mei")
```
Nwijk za: flat, 0,3m NW windhoogte met 2,5s — niet aan beginnen. Wind
draait van ZZW 's ochtends naar WZW middag en NW 's avonds, maar blijft
te licht om iets op te bouwen. Nwijk zo: nog steeds flat, 0,3m NNW met
2,6s, wind 4,4kn N 's ochtends, bouwt op naar 8kn N middag — ook niks.
Nwijk ma: helemaal niks, 0,1m N met 2,4s, swell wordt geblokkeerd door
de pier. Nwijk di: vlak, 0,0m, wind bouwt van ZZO 3,3kn 's ochtends op
naar N 7,7kn 's avonds — komt net te laat om nog wat te doen vandaag.
Nwijk wo: nog steeds flat, 0,0m, wind NO tot NNO rond 8-9kn — swell
blijft weg, wachten op de volgende deining. Cam: surfweer.nl/webcams/noordwijk/
```

## 🛡️ Safety Features

- **Output validatie**: LLM output wordt gevalideerd tegen hallucinatie (getallen, kompas-richtingen, board-claims, springtij-claims contextueel gecheckt)
- **Fallback templates**: Bij validatie falen of LLM-error wordt deterministische 5-daagse template gebruikt
- **Sonnet → Haiku fallback**: Bij Anthropic 529 overload retry met exponential backoff + automatische switch naar Haiku
- **Cooldown**: Minimaal 4u tussen alerts
- **Weekly cap**: Max 8 alerts per week
- **Rarity threshold**: Alleen alerts bij ≥70e percentile
- **Daglicht-filter**: Geen "piek-uren" buiten asymmetrisch dawn-dusk venster (zonsopkomst -1.5u, zonsondergang +0.5u)
- **Hard size-cap**: Marginale golven (<0.5m) kunnen niet via perfect environment naar score 60+ schalen (multiplicatieve aggregation)
- **Confidence-penalty**: Multi-model wind-spread genereert uncertainty-multiplier op golf-score
- **Dry run mode**: Lokale testing zonder dat er een notificatie verstuurd wordt

## 🔧 Troubleshooting

### Geen notificatie ontvangen

1. Check workflow logs op GitHub Actions
2. Verifieer GitHub Secrets (`NTFY_TOPIC` voor push, of SMTP-/Twilio-variant)
3. **ntfy**: in de app de juiste topic-naam ingetypt? Notificaties voor die app aan?
4. **mail**: kijk in spam-folder; SMTP-auth zichtbaar in logs?
5. **twilio**: saldo / nummer-formaat (+31...) / trial-restricties?

### Te veel/few alerts

1. Pas `min_peak_score` aan in `config.py`
2. Pas `cooldown_hours_between_alerts` aan
3. Check `forecasts_log.jsonl` voor details

### Validation faalt

1. Run `pytest tests/test_scoring.py -v` voor unit tests
2. Check scoring parameters in `config.py`
3. Draai validation script: `python scripts/run_validation_backtest.py`

## 📊 Kosten

In de huidige default-setup (`NOTIFIER=ntfy`) is **alles gratis**:

| Service | Kosten |
|---------|--------|
| ntfy.sh push | Gratis |
| Anthropic Sonnet 4.5 (primair) | ~€0,50-€1/maand bij 30-60 calls |
| Anthropic Haiku 4.5 (fallback) | ~€0,001 per bericht (alleen bij Sonnet-overload) |
| Open-Meteo multi-model | Gratis (KNMI + ECMWF + GFS in 1 request) |
| Rijkswaterstaat DDAPI20 | Gratis |
| GitHub Actions | Gratis (publiek repo, 2000 min/maand privé) |

Als je naar Twilio SMS terugschakelt (`NOTIFIER=twilio`), komt daar ~€0.08/SMS bij — ~€0.50-0.65/week bij 7-8 berichten.

## 🤝 Bijdragen

1. Fork repository
2. Maak feature branch
3. Commit changes
4. Push naar branch
5. Open Pull Request

Bij code changes:
- Run `pytest tests/ -v`
- Run `python scripts/run_validation_backtest.py`
- Check dat accuracy ≥70%

## 📝 License

MIT License - zie LICENSE bestand voor details.

## 🙏 Credits

Gebruikt Open-Meteo multi-model forecast (KNMI + ECMWF + GFS), Rijkswaterstaat DDAPI20 WaterWebservices, Anthropic Claude (Sonnet 4.5 + Haiku 4.5 fallback), en ntfy.sh.

Scoring-fysica gebaseerd op 9 onderzoeksrapporten in `research/`: industry models (Surfline/Stormsurf/Magicseaweed), pro forecaster methodology (Pat Caldwell NWS, WSL), academic ML (XGBoost bias-correctie peer-reviewed Dutch North Sea), en gap-analysis tegen Nederlandse forecaster-referentieberichten.

## 📞 Support

Voor issues en vragen:
- Open GitHub Issue
- Check `forecasts_log.jsonl` voor debug info
- Run validation script voor diagnose