# Noordwijk Surf Alert Systeem

Geautomatiseerd surfweer alert systeem voor Noordwijk dat elke 6 uur surfcondities analyseert en notificaties verstuurt (push via ntfy.sh, mail via SMTP, of SMS via Twilio) bij gunstige golven.

## 📋 Overzicht

Dit systeem analyseert surfcondities voor Noordwijk door:

1. **Data verzameling** uit meerdere bronnen (Open-Meteo, Rijkswaterstaat)
2. **Scoring** van surfcondities (0-100 punten) op basis van golf, wind, tij en swell richting
3. **5 alert types** detecteren (swell arrival, wind shift, wind dip, sustained groundswell, tide-gated windows)
4. **Notificaties** versturen via ntfy.sh (default, gratis push), SMTP-mail of Twilio-SMS, met Claude Haiku voor natuurlijke berichten
5. **Automatische runs** op GitHub Actions (4x per dag)

### Hoe het werkt

Het systeem berekent per uur een score op basis van:

- **Golf (40pt)**: Hoogte, periode, swell type (groundswell = bonus)
- **Wind (35pt)**: Snelheid en richting (offshore = beste)
- **Tij (15pt)**: Waterstand en fase (mid-tijd = beste)
- **Swell richting (10pt)**: W-NNW = beste, NNO = geblokkeerd door IJmuiden pier

Scores ≥60 = surfbaar, scores ≥75 = alert-waardig.

## 🚀 Quick Start

### Lokale setup

```bash
# Clone repository
git clone <your-repo-url>
cd SurfWeerWorkflow

# Install dependencies
pip install -r requirements.txt

# Configureer environment variables
cp .env.example .env
# Edit .env met je API keys

# Draai systeem (dry run, geen SMS)
cd src
python main.py --dry-run
```

### GitHub Actions setup

1. **Repository aanmaken** (private aanbevolen)
2. **GitHub Secrets** configureren (Settings → Secrets and variables → Actions → New repository secret):
   - `ANTHROPIC_API_KEY` — voor Claude Haiku tekst-generatie

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
   - `check.yml`: Draait automatisch 4x per dag
   - `rebuild-baseline.yml`: Jaarlijkse baseline update (handmatig)
   - `run-validation.yml`: Backtest validatie (handmatig)

## 📊 Architectuur

```
src/
├── config.py              # Configuratie en constants
├── main.py                # Hoofdscript
├── data/
│   ├── models.py          # Data structuren
│   └── sources/
│       ├── open_meteo.py  # Weer data API
│       └── rws.py         # Rijkswaterstaat boeien
├── scoring/
│   ├── deconstruct.py     # Swell deconstructie
│   ├── hourly.py          # Per-uur scoring
│   └── windows.py         # Window analyse
├── alerts/
│   ├── detectors.py       # 5 alert detectors
│   └── engine.py          # Alert besluit logica
├── llm/
│   ├── generator.py       # Bericht-tekst generator (Claude Haiku)
│   └── validator.py       # Output validatie
├── notify/
│   ├── ntfy.py            # ntfy.sh push (default)
│   ├── mail.py            # SMTP-mail
│   └── twilio.py          # Twilio-SMS (optionele fallback)
└── baseline/
    └── seasonal.py        # Seizoensbaseline builder

tests/
├── test_scoring.py        # Unit tests
└── test_validation.py     # Backtest validatie

.github/workflows/
├── check.yml              # Hoofd workflow (cron)
├── rebuild-baseline.yml   # Baseline update
└── run-validation.yml     # Validatie workflow
```

## 🔑 API Keys Setup

### Anthropic API (Claude Haiku 4.5)

1. Ga naar https://console.anthropic.com/
2. Maak account aan
3. Genereer API key
4. Sla op als GitHub Secret: `ANTHROPIC_API_KEY`

### Notifier setup

Default is **ntfy.sh push** (gratis, geen account):

1. Installeer de ntfy-app op je telefoon (iOS / Android)
2. Subscribe op een zelfverzonnen, onraadbare topic (bijv. `nwijksurf-<jouwinitialen>-<random>`)
3. Zet dezelfde naam in `.env` (`NTFY_TOPIC=...`) en in GitHub Secrets

Andere kanalen (SMTP-mail of Twilio-SMS) staan beschreven in `.env.example`.

## 🧪 Testing

### Unit tests

```bash
# Run alle tests
pytest tests/ -v

# Run alleen scoring tests
pytest tests/test_scoring.py -v
```

### Backtest validatie

```bash
# Run validatie tegen historische SMS dataset
cd tests
python test_validation.py

# Verwacht: ≥70% accuracy
```

## 📈 Monitoring

### Logs

- **Surf alerts**: `data/surf_alert.log`
- **Forecasts log**: `data/forecasts_log.jsonl` (JSON per run)
- **State**: `data/state.json` (runtime state)

### Log formaat

Elke run wordt gelogd in `forecasts_log.jsonl`:

```json
{
  "timestamp": "2025-08-06T06:15:00",
  "run_type": "scheduled",
  "scores_today_peak": 82,
  "scores_tomorrow_peak": 28,
  "alert_types_detected": ["T4"],
  "windows_total": 1,
  "windows_alertworthy": 1,
  "decision": "send_alert",
  "sms_sent": "SUCCESS: ID=abc123, To=+31612345678, Msg=NWIJK ALERT...",
  "llm_used": true,
  "llm_validation_passed": true,
  "buoy_ijg1_height": 1.2,
  "buoy_ijg1_period": 9.4,
  "buoy_a12_period": 10.1
}
```

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

# Scoring gewichten
SCORING_WEIGHTS = {
    'golf_max': 40,       # Golf component max
    'wind_max': 35,       # Wind component max
    'tide_max': 15,       # Tij component max
    'swell_dir_max': 10   # Richting bonus max
}
```

## 📱 Bericht-voorbeelden

### Alert (push of mail)
```
NWIJK ALERT 06-08 06:00-08:00u: groundswell 10s door windgolven heen,
0,9m WNW, wind 6kn O aflandig, opgaand tij. Cam: surfweer.nl/webcams/noordwijk/
```

### Digest (4-daagse outlook, push of mail)
```
Nwijk di: Vandaag rond 09:00 iets meer actie met 1,2m en 4,5s WZW, wind
loopt op naar 15,6kn ZW zijaflandig. Morgen flat (0,4m). Donderdag rond
20:00 minimal (0,3m). Vrijdag rond 05:00 nog steeds klein (0,2m).
Cam: surfweer.nl/webcams/noordwijk/
```

## 🛡️ Safety Features

- **Output validatie**: LLM output wordt gevalideerd tegen hallucinatie
- **Fallback templates**: Bij validatie falen wordt deterministische template gebruikt
- **Cooldown**: Minimaal 4u tussen alerts
- **Weekly cap**: Max 8 alerts per week
- **Rarity threshold**: Alleen alerts bij ≥70e percentile
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
3. Draai validation script: `python tests/test_validation.py`

## 📊 Kosten

In de huidige default-setup (`NOTIFIER=ntfy`) is **alles gratis**:

| Service | Kosten |
|---------|--------|
| ntfy.sh push | Gratis |
| Anthropic Haiku | ~€0.001 per bericht (verwaarloosbaar — pak <€0,10/maand) |
| Open-Meteo | Gratis |
| Rijkswaterstaat WaterWebservices | Gratis |
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
- Run `python tests/test_validation.py`
- Check dat accuracy ≥70%

## 📝 License

MIT License - zie LICENSE bestand voor details.

## 🙏 Credits

Gebaseerd op meteorologische analyse van referentie-forecaster van de referentie-forecaster.
Gebruikt Open-Meteo, Rijkswaterstaat DDAPI20 WaterWebservices, Anthropic Claude Haiku, en ntfy.sh.

## 📞 Support

Voor issues en vragen:
- Open GitHub Issue
- Check `forecasts_log.jsonl` voor debug info
- Run validation script voor diagnose