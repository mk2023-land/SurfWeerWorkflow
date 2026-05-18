# Noordwijk Surf Alert Systeem

Geautomatiseerd surfweer alert systeem voor Noordwijk dat elke 6 uur surfcondities analyseert en SMS alerts verstuurt bij gunstige golven.

## 📋 Overzicht

Dit systeem analyseert surfcondities voor Noordwijk door:

1. **Data verzameling** uit meerdere bronnen (Open-Meteo, Rijkswaterstaat)
2. **Scoring** van surfcondities (0-100 punten) op basis van golf, wind, tij en swell richting
3. **5 alert types** detecteren (swell arrival, wind shift, wind dip, sustained groundswell, tide-gated windows)
4. **SMS alerts** versturen via MessageBird met Claude Haiku voor natuurlijke berichten
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
2. **GitHub Secrets configureren** (Settings → Secrets and variables → Actions → New repository secret):
   - `ANTHROPIC_API_KEY`: Anthropic API key (voor Claude Haiku)
   - `TWILIO_ACCOUNT_SID`: Twilio Account SID (begint met `AC...`)
   - `TWILIO_AUTH_TOKEN`: Twilio Auth Token
   - `TWILIO_PHONE_NUMBER`: Twilio afzender-nummer (`+1...`)
   - `RECIPIENT_PHONE_NUMBER`: Jouw telefoonnummer (`+31612345678`)

   **Variables** (Settings → Variables) — optioneel, hebben sensible defaults:
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
│   ├── generator.py       # SMS generator (Claude Haiku)
│   └── validator.py       # Output validatie
├── sms/
│   └── messagebird.py     # SMS verzending
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

### Twilio SMS

1. Maak een Twilio account aan op https://www.twilio.com/try-twilio
2. Activeer een phone number (Twilio console → Phone Numbers → Manage → Buy a number)
3. Noteer Account SID, Auth Token (console homepage) en het Twilio phone number
4. Sla op als GitHub Secrets:
   - `TWILIO_ACCOUNT_SID`
   - `TWILIO_AUTH_TOKEN`
   - `TWILIO_PHONE_NUMBER`

Lokaal staan dezelfde waardes in `.env` (zie `.env.example` als template).

### Telefoonnummer

Format: `+31612345678` (NL formaat met landcode)
Sla op als GitHub Secret: `RECIPIENT_PHONE_NUMBER`

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

## 📱 SMS Voorbeelden

### Alert
```
NWIJK ALERT 06-08: 06:00-08:00u: 82/100, groundswell 10s door windgolven.
Cam: surfweer.nl/webcams/noordwijk/
```

### Digest
```
Nwijk wo 09-10: vandaag 82, morgen 28. Cam: surfweer.nl/webcams/noordwijk/
```

## 🛡️ Safety Features

- **Output validatie**: LLM output wordt gevalideerd tegen hallucinatie
- **Fallback templates**: Bij validatie falen wordt deterministische template gebruikt
- **Cooldown**: Minimaal 4u tussen alerts
- **Weekly cap**: Max 8 alerts per week
- **Rarity threshold**: Alleen alerts bij ≥70e percentile
- **Dry run mode**: Lokale testing zonder SMS verzending

## 🔧 Troubleshooting

### Geen SMS ontvangen

1. Check workflow logs op GitHub Actions
2. Verifieer GitHub Secrets zijn correct ingesteld
3. Check MessageBird saldo
4. Controleer telefoonnummer formaat (+31...)

### Te veel/few alerts

1. Pas `min_peak_score` aan in `config.py`
2. Pas `cooldown_hours_between_alerts` aan
3. Check `forecasts_log.jsonl` voor details

### Validation faalt

1. Run `pytest tests/test_scoring.py -v` voor unit tests
2. Check scoring parameters in `config.py`
3. Draai validation script: `python tests/test_validation.py`

## 📊 Kosten

| Service | Kosten |
|---------|--------|
| Twilio SMS | ~€0.07-0.09/SMS NL (~€0.50-0.65/week bij 7-8 SMS) |
| Anthropic Haiku | ~€0.001/SMS (verwaarloosbaar) |
| Open-Meteo | Gratis |
| Rijkswaterstaat WaterWebservices | Gratis |
| GitHub Actions | Gratis (publiek repo, 2000 min/maand privé) |

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

Gebaseerd op meteorologische analyse van Tobias van surfweer.nl.
Gebruikt Open-Meteo, Rijkswaterstaat, Anthropic Claude Haiku, en MessageBird.

## 📞 Support

Voor issues en vragen:
- Open GitHub Issue
- Check `forecasts_log.jsonl` voor debug info
- Run validation script voor diagnose