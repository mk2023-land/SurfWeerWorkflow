# Noordwijk Surf Alert — Bouwplan v3 voor Claude Code

> **Belangrijkste veranderingen t.o.v. v2:**
> - Diepe analyse van de methodiek van de referentie-forecaster op basis van 13 SMS'jes (juli–augustus 2025 + mei 2026 + september 2025) waarin hij expliciet uitlegt hoe hij denkt
> - Vijf onderscheiden alert-typen, niet één generieke alert-trigger
> - Frequentie-spectrum analyse als eerste-klas data (`seconden = 1000/mhz`)
> - IJgeul boei (IJG1) als primaire meting voor Noordwijk — de referentie-forecaster noemt deze boei expliciet voor deze spot
> - Refractie-modellering: groundswell uit het noorden refracteert om de pier van IJmuiden heen wel/niet
> - Synoptische context: drukverdeling (UKMO) als input voor 5-10 dagen outlook
> - Per-SMS validatieset met expected algoritme-uitkomst

---

## DEEL 0 — Executive summary

We bouwen een Python-systeem op GitHub Actions dat elke 6 uur:
1. Data ophaalt uit dezelfde bronnen die de referentie-forecaster gebruikt (RWS-boeien inclusief 2D-spectra, KNMI Harmonie, ECMWF, GFS, UKMO via Open-Meteo)
2. Per uur een score 0-100 berekent met componenten golf/wind/tij/swell
3. Vijf typen alert-triggers checkt (swell-arrival, wind-shift, wind-dip, sustained groundswell, tide-gated)
4. Beslist over daily digest (vast tijdstip) of alert-SMS (event-driven)
5. Claude Haiku 4.5 een natuurlijke SMS-tekst laat formuleren op basis van gestructureerde input
6. Output valideert tegen hallucinatie
7. SMS verstuurt via MessageBird (~€0,08/SMS) of als gratis alternatief via Telegram

**Doel**: alert-frequentie en alert-kwaliteit zo dicht mogelijk bij de werkwijze van de referentie-forecaster houden, voor 1 spot (Noordwijk).

---

## DEEL 1 — Hoe de referentie-forecaster denkt (gereconstrueerd uit zijn SMS'jes)

### 1.1 Zijn data-stack (uit weerlinks-pagina en verwijzingen in SMS)

De referentie-forecaster gebruikt — in volgorde van hoe hij ernaar verwijst in de SMS'jes:

**Voorspellingen (toekomst):**
- KNMI Harmonie (NL 2.5km wind, 48u vooruit)
- ECMWF pluim (ensemble, 10-15 dagen, voor outlook)
- GFS (globaal, 16 dagen)
- UKMO synoptische kaarten (drukverdeling, fronten — gebruikt visueel voor "waar zit de storm")
- Franse weermodel (genoemd 21-8: *"Schev mogelijk voor 8u even aflandig volgens het Franse weermodel"*)

**Live metingen (heden, kritiek voor "is de swell er al?"):**
- A12 boei (~100km uit kust, frequentie-spectrum) — vroege swell-arrival indicator
- J06 boei (noord van Texel) — voor NH/wadden
- K13 boei (NW van Texel)
- **IJG1 boei (IJgeul)** — expliciet voor Zandvoort, **Noordwijk** en Scheveningen
- MUN1 (IJmuiden Munitiestort) — voor Wijk aan Zee
- EPL3 (Europlatform) — voor HvH/Maasvlakte
- Schulpengat (SGAT) — voor Callantsoog/Petten
- E131 — voor Maasvlakte
- Schouwenbank/Deurloo oost — voor België

**Voor tij:**
- RWS astronomisch getij per locatie

**Belangrijk: hij leest 2D-spectra (frequentie × richting), niet alleen significante golfhoogte.** Dit is fundamenteel anders dan "Open-Meteo wave_height = 1.2m". Met een 2D-spectrum zie je:
- Of er één swell uit één richting komt (clean) of meerdere swells overlappen (messy)
- Of er een groundswell-piek onder de wind sea zit (perfect voorspellingsmoment voor "swell komt door de windgolven heen")
- Wanneer een nieuwe storm-swell begint aan te komen (nieuwe piek bij lagere frequentie)

### 1.2 Zijn frequentie-taal

De referentie-forecaster rekent in **millihertz** en converteert naar seconden:

```
periode_seconden = 1000 / frequentie_mhz
```

Frequenties die hij noemt en wat hij ermee doet:

| Frequentie | Periode | Wat het betekent | Woorden van de referentie-forecaster |
|---|---|---|---|
| ≥200 mhz | ≤5 sec | Pure wind sea (windgegenereerd, lokaal) | "rommel", "wild-wash", "chop" |
| 140 mhz | 7 sec | Wind swell, kan over Vlaamse banken refracteren | *"ideale golfperiode op 140 mhz... afgerond zo'n 7 seconde"* |
| 125 mhz | 8 sec | Stevige wind swell | "1,5m op 8sec hoog" — dik fish/shortboard |
| 100 mhz | 10 sec | Groundswell — energie raakt de bodem | *"swell op een frequentie van 100mhz (omgerekend 1000:100 = 10 seconde groundswell)"* |
| ≤85 mhz | ≥12 sec | Echte verre groundswell (zeldzaam in NL) | "zware groundswell" |

**Het cruciale inzicht** dat hij geeft in de SMS van 6-8-2025: een **wind sea op 200mhz + een groundswell op 100mhz** zijn **twee aparte energiepieken** in hetzelfde spectrum. Het systeem moet ze los van elkaar tracken, NIET één gemiddelde nemen. De alert van de referentie-forecaster op 6-8 was specifiek omdat de groundswell-piek dóór de windgolven heen kwam.

### 1.3 Zijn ruimtelijke modellering

Hoe de referentie-forecaster denkt over de kust:

**Refractie**: een swell uit een bepaalde richting "bocht" om obstakels heen. *"Richting v/d swell is te veel uit NNO afkomstig en draait slechts N en niet genoeg NNW om rond de pier van IJmuiden te komen"* (23-8) — dit is exact wave refraction. Voor Noordwijk: swell uit NNW komt direct, swell uit NNO is minder voor Noordwijk omdat de pier van IJmuiden hem deels afschermt.

**Vlaamse banken filter**: ondiepe zandbanken (Thorntonbank etc.) voor de Belgische/Zeeuwse kust dempen lange-periode swells: *"door deze kortere interval komt de swell ook gemakkelijk over de Vlaamse banken"* (21-8). Dus voor BE/ZL: shorter period = beter doorgang. Voor Noordwijk minder relevant.

**Refractiezuid-effect**: *"Hoe zuidelijker hoe meer het de bodem raakt, afremt en kleiner wordt"* (6-8). Een N-swell verliest hoogte zuidelijker langs de kust. Z-H krijgt minder dan N-H bij N-swell.

**Beschuttingseffect strekdammen/havens**: spots met pieren (Scheveningen, Wijk, IJmuiden) zijn gunstiger bij specifieke windrichtingen. Noordwijk heeft geen pier vlakbij = recht open zonder beschutting.

**Maan-effect (spring/neap tide)**: *"de maan die van achteren net nieuw vol is, die meer springtij achtige stromingscondities geeft"* (23-8). Bij volle/nieuwe maan = sterker tij = meer stroming = vaak korter goed window.

### 1.4 Zijn meteorologische logica (5 alert-typen)

Op basis van de SMS-analyse zijn er **vijf typen alerts** die elk een eigen meteorologische trigger hebben:

#### TYPE 1: Swell-arrival alert (verre storm aankomend)
**Trigger**: A12/K13 boei spectrum toont nieuwe piek bij lagere frequentie. "Verspringend ruggetje" (23-8).
**Lead time**: 6-12 uur vóór kustaankomst.
**Voorbeeld**: 23-8-2025 SMS: *"De toename is/was zichtbaar in het A12 spectrum van RWS... met een verspringend ruggetje ergens deze zaterdagochtend"*.
**Algoritmische detectie**: vergelijk huidige A12-spectrum met dat van 6u geleden; piek-frequentie verschoven naar lager + piek-amplitude gestegen = alert.

#### TYPE 2: Wind-shift alert (koufront/trog passage)
**Trigger**: KNMI Harmonie/ECMWF voorspelt windrichting verandering die van onshore naar offshore gaat.
**Lead time**: 12-48 uur (uit forecast).
**Voorbeeld**: 20-8-2025: *"passeert vanuit het noorden een afzwakkend koufront. Deze... NNO naar ONO, wat op veel plaatsen even een aflandige richting met cleane golven kan opleveren"*.
**Algoritmische detectie**: forecast toont wind-richtingshift ≥45° binnen 6u, waarbij na de shift de wind aflandig (75°-135°) of side-offshore wordt, EN er voldoende swell aanwezig is (uit forecast OF live boei).

#### TYPE 3: Wind-dip alert (lokale windstilte door synoptische storing)
**Trigger**: KNMI Harmonie voorspelt korte windsnelheid-afname (≥4kn drop) binnen een verder rommelige dag.
**Lead time**: 6-24 uur.
**Voorbeeld**: 21-8-2025: *"De oorzaak van de winddip is waarschijnlijk door de lichte buienlinie die over Z-H trekt (terug te zien aan de cyclonale isobaren kromming in het 1020hPa lijntje)"*.
**Algoritmische detectie**: local minimum in wind speed forecast met een waarde minimaal 5kn onder omliggende 4u, EN swell aanwezig.

#### TYPE 4: Sustained groundswell alert
**Trigger**: Boei meet >9s periode én >0.8m swell-component (los van wind sea), aanhoudend ≥6u.
**Lead time**: real-time (uit live boei).
**Voorbeeld**: 6-8-2025: *"die nu omhoog piekt naar 1,4m swell op een frequentie van 100mhz... 10 seconde groundswell"*.
**Algoritmische detectie**: live boei toont swell_wave_period ≥ 9s EN swell_wave_height ≥ 0.7m gedurende minimaal 3 metingen achter elkaar.

#### TYPE 5: Tide-gated window alert
**Trigger**: combinatie van acceptabele swell + acceptable wind + gunstig tij voor specifieke periode (1-3u).
**Lead time**: 12-24 uur.
**Voorbeeld**: 5-8-2025: massa-alert met spot-specifieke tijdwindows.
**Algoritmische detectie**: composiet score >75 voor ≥1u met tide_norm in [0.3, 0.8] EN wind_speed <12kn.

**Belangrijk**: meerdere typen kunnen tegelijkertijd triggeren. De SMS moet dan vermelden welke typen actief zijn ("groundswell + windstilte na frontpassage").

### 1.5 Wanneer de referentie-forecaster géén alert stuurt

Even relevant. Uit zijn SMS'jes:
- Conditie is wel goed maar **niet ongewoon voor het seizoen** ("de moeite" zonder "alerts")
- **Te onzeker** ("Hopelijk haperen bij 4bft" — geen alert, want voorwaardelijk)
- **Te gevaarlijk** (storm condities, te hoog: "wadden niet meer te doen" — waarschuwing, niet alert)
- **Geen swell, geen wind** (28-8 weken: "merendeels flat")
- **Verkeerde windrichting** ondanks swell (frequent: swell aanwezig maar wind 5bft onshore = "wild-wash")

### 1.6 Zijn seizoenscontext

Uit 9-8-2025 SMS: *"Geen alerts meer tot en met 20 augustus. Die hebben we de afgelopen weken al gehad..."* en uit 13-8: *"vanaf vrijdagmiddag komt hier verandering in door het hogedrukbied dat met centrum boven Schotland komt te liggen"*.

Hij denkt in **synoptische tijdsblokken** (3-7 dagen) en signaleert deze in de SMS. Het algoritme moet dit ook doen: lange flat-periodes durf je voorspellen op basis van een persistent hogedrukgebied; lange alert-periodes durf je voorspellen op basis van een aanhoudende W/NW depressie-track.

---

## DEEL 2 — Per-SMS analyse (validatieset voor het algoritme)

Deze tabel is de **goldset** waar het algoritme tegen gevalideerd wordt in Stap 8 van het bouwplan.

| Datum | Status Noordwijk | Type | Redenering referentie-forecaster | Wat algoritme zou moeten zien |
|---|---|---|---|---|
| 9-9-2025 di | flat | n.v.t. | "Nauwelijks wind" → geen golfgeneratie | wind <8kn, wave <0.4m, score <15 |
| 10-9-2025 wo | flat | n.v.t. | Hogedruk warm, geen wind | idem |
| 11-9-2025 do | OK 8-13u | (geen alert) | Krachtige W/WZW 's nachts genereert golven, windafname overdag | score 50-65 middag, geen alert (geen rarity) |
| 14-5-2026 do | matig | (geen alert) | "wat kleins door een mix van WZW en NW swell" | score 40-55, geen alert |
| 15-5-2026 vr | OK middag | (geen alert) | swell 0.9m, onshore wind | score 45-60 |
| 16-5-2026 za | smal alert window | T5 + T3 | "Zvoort/Nwijk heel even 11-12u zonder wind" + windstilte-window | score 70-80 in smal window, ALERT (window stabiliteit + wind-dip) |
| 17-5-2026 zo | ochtend & avond OK | T2 | Aflandige zuid offshore wind 1m N deining | score 65-75 in twee windows |
| 30-7-2025 wo | avond OK | T3 | "Z-H even helemaal geen wind vanaf schev" | wind-dip, score 60-75 avond |
| 31-7-2025 do | mediocre | (geen alert) | hoogte blijft, lichte W/WNW wind | score 50-60 |
| 1-8-2025 vr | onshore | (geen alert) | toenemende NW wind, geen offshore window | score <50 |
| 2-8-2025 za | avond beter | (geen alert NL) | hoogte+wind onstabiel | score 50-65 avond |
| 3-8-2025 zo | OK | (mogelijk T1) | swell loopt door | score 55-70 |
| 5-8-2025 wo | groot alert | T1+T4+T5 | 1.5m swell uit N op 10sec | score 75-90, ALERT |
| 6-8-2025 wo | ochtend alert 6-8u | T4 | "1,4m swell op 100mhz" groundswell door windgolven heen | score 75-85 ochtend, ALERT |
| 7-8-2025 do | ? | (te verifiëren) | swell loopt af | score 50-65 |
| 9-8 t/m 18-8 | flat | n.v.t. | hogedruk gevestigd | score <20 |
| 20-8-2025 wo | smal alert 12:30-14:30u | T2 | koufrontpassage NNO→ONO | score 70-80 in 2u window, ALERT |
| 21-8-2025 do | mediocre | (geen alert NW) | swell ok, wind W ≤3bft | score 55-70 |
| 22-8-2025 vr | OK | T3 | wind-dip mogelijk | score 60-75 |
| 23-8-2025 za | avond top | T1+T3 | nieuwe N-swell op 140mhz, windafname laat | score 70-85 avond, mogelijk ALERT |
| 28-8-2025 do | flat | n.v.t. | geen wind, geen swell | score <15 |

### 2.1 Belangrijk inzicht over Noordwijk specifiek

De referentie-forecaster noemt Noordwijk in deze SMS'jes meestal als deel van groepering "Katwijk/Nwijk/Zvoort" of "Wssnaar-Nwijk". Patronen voor Noordwijk-alert-momenten:

1. **N tot NNW swell** (NNO te veel afgeschermd door IJmuiden pier)
2. **Tij**: meestal niet kritisch (geen pier), mid-tij prima
3. **Wind**: <3bft of side-offshore (zuid tot oost)
4. **Tijdblok**: ochtend zeebries-vrij (06-09u) of avond na zeebries-collapse (17-21u)

Noordwijk werkt **niet** als alleen-spot bij:
- Volledig onshore (W/NW 4bft+)
- Te lange periode swell (>11s) die over de zandbank rolt naar Wijk aan Zee
- NNO swell (IJmuiden pier blokkeert)

---

## DEEL 3 — Het scoring-algoritme (v3)

### 3.1 Architectuur in lagen

```
Layer 0 (rauwe data):     boei + forecast + tij data
       ↓
Layer 1 (deconstructie):  scheid wind sea / wind swell / groundswell pieken
       ↓
Layer 2 (per-uur score):  composiet 0-100
       ↓
Layer 3 (event detectie): 5 alert-typen
       ↓
Layer 4 (window analyse): clusters detecteren, stabiliteit checken
       ↓
Layer 5 (beslissing):     digest of alert? welk type? wat in SMS?
       ↓
Layer 6 (LLM generatie):  natuurlijke SMS-tekst
       ↓
Layer 7 (validatie+send): hallucinatie-check, dan MessageBird
```

### 3.2 Layer 1 — Swell-deconstructie

Open-Meteo Marine levert al uitgesplitst:
- `wind_wave_*` (lokaal door wind gegenereerd)
- `swell_wave_*` (verre swell, langere periode)

Voor live boei-data (RWS) parsen we het 2D-spectrum naar:
```python
@dataclass
class WaveSpectrum:
    timestamp: datetime
    significant_height_total: float    # Hm0
    peaks: List[SpectralPeak]          # alle pieken in spectrum
    
@dataclass
class SpectralPeak:
    frequency_mhz: int
    period_s: float       # = 1000 / frequency_mhz
    height_m: float       # gepartitioneerd Hm0 onder deze piek
    direction_deg: int
    type: Literal['wind_sea', 'wind_swell', 'ground_swell']
    # wind_sea: period < 7s
    # wind_swell: 7-9s  
    # ground_swell: >= 9s
```

Belangrijk: één spectrum kan **2-3 pieken** hebben. Het inzicht van de referentie-forecaster: een groundswell-piek (10s) + wind sea-piek (4s) tegelijk = mooie surf (de groundswell komt door).

### 3.3 Layer 2 — Per-uur score

```python
def score_hour(state: HourState) -> ScoreBreakdown:
    # Component A: Golf (max 40)
    # We waarderen swell + wind swell hoger dan pure wind sea
    golf = max(
        groundswell_score(state.ground_swell),    # max 40
        windswell_score(state.wind_swell) * 0.85,  # max 34
        wind_sea_score(state.wind_sea) * 0.55      # max 22
    )
    
    # Component B: Wind (max 35)
    wind = wind_score(state.wind_kn, state.wind_dir, BEACH_NORMAL_NWIJK)
    
    # Component C: Tij (max 15)
    tide = tide_score(state.tide_norm, state.tide_trend)
    
    # Component D: Swell-direction match voor Noordwijk (max 10)
    # Penalty als swell uit NNO (IJmuiden pier blokkeert)
    swell_dir_bonus = directional_bonus(
        state.dominant_swell_dir, 
        preferred_range=(270, 340),    # W tot NNW
        blocked_range=(0, 30)          # NNO geblokkeerd
    )
    
    return ScoreBreakdown(golf, wind, tide, swell_dir_bonus)
```

### 3.4 Layer 3 — Alert-event detectie

Vijf parallelle detectoren, elke kan triggeren onafhankelijk:

```python
class AlertDetectors:
    def detect_swell_arrival(history, current):
        """Type 1: A12 spectrum piek-frequentie verschoven naar lager + hoger"""
        # Vergelijk huidige A12 dominant_swell_period met 6u terug
        # Als period gestegen met >= 1.5s EN height gestegen met >= 30%:
        #   ALERT - swell komt aan
        
    def detect_wind_shift(forecast_hours):
        """Type 2: koufront/trog passage met windrichting-shift"""
        # Loop over de komende 48u
        # Detecteer punt waar wind_dir verandert >= 45° binnen 6u
        # En de nieuwe richting valt in offshore/side-offshore window
        # En wind_speed na shift <= 12kn
        
    def detect_wind_dip(forecast_hours):
        """Type 3: lokale windsnelheid-minimum"""
        # Detecteer local minimum: wind_speed_uur < (gemiddelde van uren -4..+4) - 5kn
        # En swell aanwezig (height >= 0.7m)
        # En duurt >= 1u
        
    def detect_sustained_groundswell(live_buoy_history):
        """Type 4: aanhoudende groundswell op live boei"""
        # IJG1 boei: period >= 9s EN height >= 0.7m gedurende >= 3 metingen achter elkaar
        
    def detect_tide_gated_window(forecast_hours):
        """Type 5: combinatie windows"""
        # Per uur: score >= 75 EN tide_norm in [0.3, 0.8] EN duurt >= 1u
        # Cluster aaneengesloten uren
```

### 3.5 Layer 4 — Window-analyse

Een **window** is een aaneengesloten reeks uren met score >= 60. Voor elk window:

```python
@dataclass
class SurfWindow:
    start: datetime
    end: datetime
    peak_score: int
    median_score: int
    peak_hour: datetime
    triggers: List[str]  # welke alert-typen aanwezig
    stability: float     # 1.0 = score blijft constant, 0 = grote schommeling
    rarity_percentile: float  # 0-100, vs seizoensbaseline
```

### 3.6 Layer 5 — Beslissing

```python
def decide(windows, state, baseline) -> Decision:
    is_digest_time = is_morning_first_run(state)
    
    alerts = []
    for w in windows:
        # Een alert wordt gestuurd als:
        # - peak_score >= 75 EN
        # - heeft >= 1 actieve trigger (T1-T5) EN
        # - stability >= 0.6 (binnen window niet meer dan 15 punten variatie) EN
        # - rarity_percentile >= 70 OF type in [T1, T2, T4] (deze typen zijn 
        #   per definitie zeldzame events)
        # - niet al gestuurd in vorige run (deduplicatie)
        # - cooldown 4u sinds laatste alert
        if is_alert_window(w, state):
            alerts.append(w)
    
    return Decision(
        send_digest=is_digest_time,
        send_alerts=alerts[:1],  # max 1 alert per run om spam te voorkomen
        ...
    )
```

### 3.7 Layer 6 — LLM generatie (Claude Haiku 4.5)

System prompt (Nederlands):

```
Je schrijft korte surf-SMS'jes voor Noordwijk in de stijl van de 
referentie-forecaster. Bondig, surferslang oké, geen overdrijving.

STRIKTE REGELS:
1. Gebruik ALLEEN getallen die in de structured_input staan.
2. Verzin GEEN windrichtingen, golfhoogtes, periodes of tijden.
3. Houd berichten <320 tekens (= 2 SMS) waar mogelijk.
4. Bij type "alert": begin met "NWIJK ALERT [datum]".
5. Bij type "digest": begin met "Nwijk [dag]:".
6. Vermeld altijd: tijdvenster, golfhoogte, periode, windrichting+kracht.
7. Bij alert vermeld kort de REDEN: 
   T1=swell aankomst, T2=wind draait aflandig, 
   T3=windstilte-window, T4=groundswell door, T5=goede combo
8. Eindig met "Cam: surfweer.nl/webcams/noordwijk/"
9. Geen speculatie, geen "denk ik", geen voorbehouden anders dan al in input
```

User-message JSON example:
```json
{
  "type": "alert",
  "date": "2025-08-06",
  "window": {
    "start": "06:00", "end": "08:00",
    "peak_score": 82
  },
  "conditions": {
    "wave_total_m": 1.4,
    "groundswell": {"height_m": 1.2, "period_s": 10, "direction_deg": 350},
    "wind_sea": {"height_m": 0.4, "period_s": 4},
    "wind_kn": 4, "wind_dir_deg": 180, "wind_label": "zuid offshore"
  },
  "tide": {"phase": "afgaand", "next_low": "08:14"},
  "trigger_types": ["T4"],
  "trigger_explanation": "Groundswell 10s blijft door windgolven heen komen",
  "rarity": "95e percentile voor week 32",
  "webcam_url": "https://surfweer.nl/webcams/noordwijk/"
}
```

### 3.8 Layer 7 — Output validatie

Voor we de SMS versturen:

```python
def validate_llm_output(sms_text: str, structured_input: dict) -> ValidationResult:
    extracted_numbers = extract_all_numbers(sms_text)  # regex
    allowed_numbers = recursive_extract_numbers(structured_input)
    
    issues = []
    for n in extracted_numbers:
        if not any(abs(n - a) <= 0.1 for a in allowed_numbers):
            issues.append(f"Number {n} not in input")
    
    extracted_directions = extract_compass_directions(sms_text)
    allowed_directions = get_directions_from_input(structured_input)
    
    for d in extracted_directions:
        if d not in allowed_directions:
            issues.append(f"Direction {d} not in input")
    
    return ValidationResult(passed=len(issues)==0, issues=issues)
```

Bij failed validation → fallback naar deterministische template:
```
NWIJK ALERT {datum} {start}-{end}u: {hoogte}m/{periode}s,
wind {dir} {kn}kn. {trigger_uitleg}. 
Cam: surfweer.nl/webcams/noordwijk/
```

---

## DEEL 4 — Databronnen (volledige stack)

### 4.1 Open-Meteo (free, no API key)

**Marine API:**
```
https://marine-api.open-meteo.com/v1/marine
  ?latitude=52.24&longitude=4.43
  &hourly=wave_height,wave_direction,wave_period,
          wind_wave_height,wind_wave_direction,wind_wave_period,wind_wave_peak_period,
          swell_wave_height,swell_wave_direction,swell_wave_period
  &timezone=Europe/Amsterdam
  &forecast_days=7
```

**Forecast API met multi-model:**
```
https://api.open-meteo.com/v1/forecast
  ?latitude=52.24&longitude=4.43
  &hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,
          temperature_2m,precipitation,pressure_msl,cloud_cover
  &wind_speed_unit=kn
  &timezone=Europe/Amsterdam
  &forecast_days=7
  &models=knmi_seamless,ecmwf_ifs025,gfs_seamless,ukmo_global_deterministic
```

Spread tussen de modellen = onzekerheid. Bij grote spread → in SMS voorbehoud noemen ("modellen onzeker").

**Archive API** voor backtest en seizoensbaseline:
```
https://archive-api.open-meteo.com/v1/archive
  ?latitude=52.24&longitude=4.43
  &start_date=2021-01-01&end_date=2025-12-31
  &hourly=wind_speed_10m,wind_direction_10m
```
+ marine variant.

### 4.2 Rijkswaterstaat — boeien en spectra

**Documentatie**: https://rijkswaterstaat.github.io/wm-ws-dl/

**Voor Noordwijk: IJG1 (IJgeul) is primaire boei**, want de referentie-forecaster gebruikt deze expliciet voor Noordwijk/Zandvoort/Scheveningen.

```python
RWS_STATIONS = {
    'IJG1': {  # IJgeul - PRIMARY voor Noordwijk
        'name': 'IJgeul',
        'lat': 52.450, 'lon': 4.050,  # approx
        'use_for': ['noordwijk', 'zandvoort', 'scheveningen'],
        'lead_time_hours': 1,  # bijna real-time voor kust
    },
    'EURPFM': {  # Europlatform - early warning
        'name': 'Europlatform',
        'lat': 52.000, 'lon': 3.275,
        'use_for': ['early_warning_west_swell'],
        'lead_time_hours': 2,
    },
    'A12': {  # A12 platform - verre early warning
        'name': 'A12',
        'lat': 55.400, 'lon': 3.817,  # ~250km uit kust
        'use_for': ['early_warning_north_swell'],
        'lead_time_hours': 10,
    },
    'J6': {  # J6 platform - noord van Texel
        'name': 'J6',
        'lat': 53.817, 'lon': 2.950,
        'use_for': ['early_warning_north_swell_short'],
        'lead_time_hours': 5,
    },
    'K13': {  # K13 platform - NW van Texel
        'name': 'K13',
        'lat': 53.217, 'lon': 3.217,
        'use_for': ['early_warning_west_north'],
        'lead_time_hours': 4,
    },
    'MUN1': {  # IJmuiden Munitiestort
        'name': 'IJmuiden Munitiestort',
        'lat': 52.466, 'lon': 4.583,
        'use_for': ['wijk_aan_zee'],
        'lead_time_hours': 0,
    },
}
```

**API call voor live golfhoogte op IJG1**:
```python
import requests
url = "https://waterwebservices.rijkswaterstaat.nl/ONLINEWAARNEMINGENSERVICES_DBO/OphalenLaatsteWaarnemingen"
body = {
    "AquoPlusWaarnemingMetadataLijst": [{
        "AquoMetadata": {
            "Compartiment": {"Code": "OW"},  # Oppervlakte water
            "Grootheid": {"Code": "Hm0"}     # Significante golfhoogte
        }
    }, {
        "AquoMetadata": {
            "Compartiment": {"Code": "OW"},
            "Grootheid": {"Code": "Tm02"}    # Gemiddelde periode
        }
    }, {
        "AquoMetadata": {
            "Compartiment": {"Code": "OW"},
            "Grootheid": {"Code": "Th0"}     # Gemiddelde richting
        }
    }],
    "LocatieLijst": [{"X": 4.050, "Y": 52.450, "Code": "IJG1"}]
}
response = requests.post(url, json=body)
```

**Spectrumdata** (2D wave spectrum) is via Rijkswaterstaat ook beschikbaar, maar moeilijker te parsen. Voor MVP gebruiken we de gepartitioneerde data (Hm0, Tm02, Th0) en de Open-Meteo wind_wave vs swell_wave splitsing. Voor v2 kan spectrum-parsing toegevoegd worden.

**Spectrum-images (RWS waterberichtgeving)** zijn beschikbaar via:
```
https://waterberichtgeving.rws.nl/dynamisch/forecast/image/spec_{boei}.jpg
```
waar `{boei}` = `a121`, `k133`, `j61`, `mun1`, `ijg1`, `epl3`, `e131`. Niet machine-readable, maar handig in SMS als deeplink ("Check spectrum: ...").

### 4.3 Andere bronnen

**Cefas Wavenet (UK)**: voor verre swell uit het westen
- API: https://wavenet.cefas.co.uk/Map
- Relevante boei: Hastings, Sandettie Light Vessel
- Voor Noordwijk: 8-12u lead time voor W swell

**Vlaams Meetnet (BE)**: Westhinder boei voor swell uit het ZW/W
- API: https://api.meetnetvlaamsebanken.be (registratie gratis)
- Voor Noordwijk: 1-3u lead time voor zuid-swells

**Tij**: Rijkswaterstaat astronomisch getij
- Endpoint: `/ONLINEWAARNEMINGENSERVICES_DBO/OphalenWaarnemingen` met parameter "WATHTE" astronomisch
- Voor Noordwijk: gebruik Scheveningen station (SCHEVNGN) of IJmuiden buitenhaven (IJMDBTHVN)

### 4.4 Synthese - welke bron waarvoor

| Beslissing | Primaire bron | Backup |
|---|---|---|
| Wind forecast (vandaag-morgen) | Open-Meteo KNMI Harmonie | Open-Meteo ECMWF |
| Wind outlook (d+3 t/m d+7) | Open-Meteo ECMWF | Open-Meteo GFS |
| Wave forecast | Open-Meteo Marine (DWD-based) | — |
| Live golfhoogte Noordwijk | RWS IJG1 boei | Open-Meteo Marine nowcast |
| Verre swell early warning | RWS A12 + K13 | Cefas (UK boeien) |
| Swell decompositie (sea/swell/ground) | Open-Meteo `wind_wave_*` + `swell_wave_*` | RWS spectrum (later) |
| Tij | RWS astronomisch | — |
| Synoptische context | UKMO via Open-Meteo (druk-data) | ECMWF |
| Onzekerheid | Spread tussen 4 modellen | — |
| Seizoensbaseline | Open-Meteo Archive (5 jaar) | — |

---

## DEEL 5 — Concrete bouwstappen voor Claude Code

### Stap 1: Project setup

```bash
mkdir noordwijk-surf-alert && cd noordwijk-surf-alert
git init
python -m venv .venv && source .venv/bin/activate
pip install requests httpx python-dateutil pytz messagebird anthropic pytest pandas numpy
```

Maak `requirements.txt`, `.gitignore`, `README.md`. Vraag de gebruiker:
- GitHub repo-naam
- Public of private (aanbevolen: private vanwege secrets)

### Stap 2: Configuratie module

`config.py`:
```python
from dataclasses import dataclass

NOORDWIJK = {
    'lat': 52.241, 'lon': 4.428,
    'beach_normal_deg': 285,
    'preferred_swell_dir_min': 270,
    'preferred_swell_dir_max': 340,
    'blocked_swell_dir_min': 350,  # NNO geblokkeerd door IJmuiden pier
    'blocked_swell_dir_max': 30,
}

ALERT_CONFIG = {
    'min_peak_score': 75,
    'min_window_duration_hours': 1,
    'max_score_drop_in_window': 15,
    'min_rarity_percentile': 70,
    'cooldown_hours_between_alerts': 4,
    'max_alerts_per_week': 8,
}

# ... etc
```

### Stap 3: Data-source modules

Bouw één file per bron:
- `sources/open_meteo.py` - marine + forecast + archive
- `sources/rws.py` - boeien + tij
- `sources/cefas.py` - UK boeien
- `sources/vlaamsemeetnet.py` - Westhinder

Elke module exposeert async functies, retry-logica, en een uniform dataformat.

### Stap 4: Scoring module

`scoring.py`:
- `score_hour(hour_state)` zoals in Deel 3.3
- `decompose_spectrum(wind_wave, swell_wave)` 
- `partition_spectrum(spectrum_data)` voor RWS 2D-data (v2)

Unit tests in `test_scoring.py`:
- 12 testcases minimum, gebaseerd op de validatie-tabel in Deel 2
- Edge cases: NNO swell (geblokkeerd), groundswell door windsea (Type 4), windstilte window (Type 3)

### Stap 5: Alert engine met 5 detectoren

`alerts.py`:
- `class SwellArrivalDetector` (Type 1)
- `class WindShiftDetector` (Type 2)
- `class WindDipDetector` (Type 3)
- `class SustainedGroundswellDetector` (Type 4)
- `class TideGatedWindowDetector` (Type 5)

Elke detector heeft `detect(state, history) -> Optional[AlertCandidate]`.

`engine.py`:
- `evaluate_forecast(forecast, live_data, state, baseline) -> Decision`
- Runt alle 5 detectoren parallel
- Combineert resultaten
- Deduplicatie tegen state.json
- Cooldown + weekly cap

### Stap 6: Seizoensbaseline bouwer

`baseline.py`:
- Run 1x per jaar (workflow_dispatch trigger)
- Pull 5 jaar Open-Meteo archive
- Bereken score per uur historisch
- Per week-of-year: 50/70/90 percentile van daily peak scores
- Schrijf naar `data/seasonal_baseline.json`

### Stap 7: LLM SMS-generator

`llm.py`:
- `generate_sms(decision) -> str`
- System prompt zoals in Deel 3.7
- Anthropic Haiku 4.5
- Output-validatie zoals in Deel 3.8
- Fallback template bij failed validation

### Stap 8: Hoofdscript

`main.py`:
```python
async def main():
    config = load_config()
    state = load_state()
    baseline = load_baseline()
    
    # Fetch in parallel
    forecast, live, tide = await asyncio.gather(
        fetch_all_forecasts(config.location),
        fetch_live_buoys(),
        fetch_tide_predictions()
    )
    
    # Score
    scores = score_forecast(forecast, live, tide)
    
    # Detect alerts
    decision = evaluate_forecast(scores, live, state, baseline)
    
    # Generate + validate + send
    if decision.send_digest or decision.send_alerts:
        sms = generate_sms(decision)
        if validate_sms(sms, decision.structured_input):
            send_via_messagebird(sms)
        else:
            send_via_messagebird(fallback_template(decision))
    
    save_state(decision.new_state)
    log_run(decision)

if __name__ == "__main__":
    asyncio.run(main())
```

### Stap 9: Validatie

`validate.py` - kritieke stap voor kwaliteitsborging:
1. Pull Open-Meteo archive voor alle datums in Deel 2 (de validatie-tabel)
2. Run het algoritme alsof het toen draaide
3. Vergelijk output met "Verwachte algoritme uitkomst" kolom
4. Bij score-verschil >15 punten of verkeerd alert-type: rapporteer in markdown-tabel

Iteratief drempelwaarden in `config.py` aanpassen tot minimaal 70% van validatie-cases klopt.

### Stap 10: GitHub Actions

`.github/workflows/check.yml` zoals in v2, met deze extras:
- Cron 4x per dag: 06:15, 12:15, 18:15, 00:15 NL-tijd
- Workflow voor `rebuild-baseline` (handmatig 1x/jaar)
- Workflow voor `run-validation` (om backtest te draaien tegen update)

### Stap 11: Logging

`forecasts_log.jsonl` per run, append-only. Velden:
```json
{
  "timestamp": "...",
  "run_type": "scheduled" | "manual",
  "scores_today_peak": 72,
  "scores_tomorrow_peak": 28,
  "alert_types_detected": ["T3"],
  "windows_total": 1,
  "windows_alertworthy": 1,
  "decision": "send_alert" | "send_digest" | "skip",
  "sms_sent": "...",
  "llm_used": true,
  "llm_validation_passed": true,
  "llm_validation_issues": [],
  "buoy_ijg1_height": 1.2,
  "buoy_ijg1_period": 9.4,
  "buoy_a12_period": 10.1
}
```

Maakt achteraf bij elke "waarom kreeg ik geen alert?"-vraag mogelijk om in 1 grep terug te zien wat het systeem dacht.

### Stap 12: Deploy & gradual rollout

1. Push naar GitHub
2. Configureer secrets
3. Run `baseline.py` via workflow_dispatch (eenmalig, kost 10-20 min)
4. Run `validate.py` via workflow_dispatch, bekijk resultaten
5. Kalibreer drempelwaarden indien nodig (terug naar stap 4 totdat ≥70% van validatieset klopt)
6. Handmatige workflow run om eerste SMS te triggeren
7. Eerste week: alleen digest, geen alerts (commit `ALERTS_ENABLED = False` als safety)
8. Tweede week: alerts aan, maar met 2x cooldown (8u) voor extra zekerheid
9. Daarna: normaal regime

### Stap 13: Monitoring

Optioneel: maak een GitHub Pages dashboard van `forecasts_log.jsonl`:
- Aantal SMS per maand
- Per type alert hoe vaak getriggerd
- Score-distributie over tijd
- Validatie-runs vs actuele predictions

Helpt om te zien of het systeem drift krijgt.

---

## DEEL 6 — Vragen voor de gebruiker (Claude Code stelt deze tijdens build)

1. GitHub username + repo naam
2. Repo public/private (aanbevolen private)
3. Telefoonnummer voor SMS (+31...)
4. MessageBird API key + originator
5. Anthropic API key (voor Haiku 4.5)
6. Meetnet Vlaamse Banken login (optioneel, voor Westhinder)
7. Voorkeurstijd daily digest (default 07:00)
8. Max SMS per week (default 8 ≈ €0,65/week)
9. **Belangrijk**: bevestig de validatieresultaten in Stap 9 voor go-live. Toon de tabel, vraag akkoord per testdag.

---

## DEEL 7 — Risico's en mitigaties

| Risico | Mitigatie |
|---|---|
| LLM hallucineert getallen | Output-validatie + fallback template |
| Open-Meteo API uitval | Multi-model + cached responses + retry |
| RWS API uitval | Open-Meteo Marine als fallback voor wave data |
| MessageBird saldo op | Hard cap per week; failure-notification via GitHub email |
| Te veel alerts (cry wolf) | Min rarity_percentile, max 8/week, manual dry-run eerste week |
| Te weinig alerts (gemist) | Logging van scoreverloop; user kan log openen om te zien waarom |
| Verkeerd alert-type | Validatie tegen 13 historische SMS-dagen |
| Stijl van de referentie-forecaster te dichtbij overgenomen | LLM output is paraphrase, geen letterlijke quotes |
| Stormwaarschuwing als alert verkeerd geïnterpreteerd | Hard cap: wind >35kn of waveheight >3m = NO alert, juist waarschuwing in digest |
| Mei-juli alerts te zeldzaam | Lage seizoensbaseline maakt absoluut bescheidener events alert |

---

## DEEL 8 — Latere uitbreidingen (post-MVP)

1. **2D-spectrum parser**: RWS levert ook richting-gefilterde spectra. Implementeer een echte FFT-deconstructie van wind_sea / wind_swell / ground_swell pieken met directionele info. Dit benadert de "spectra"-analyse van de referentie-forecaster beter dan de huidige gepartitioneerde data.

2. **Webcam computer vision**: machine vision op de Noordwijk-webcam (https://surfweer.nl/webcams/noordwijk/) om de actuele werkelijke conditie te vergelijken met de voorspelling. Bij grote discrepantie: noot in digest.

3. **Multi-spot vergelijking**: ook IJmuiden, Scheveningen, Zandvoort scoren. SMS noemt de beste spot van de dag.

4. **Sonnet 4.6 als second opinion**: voor edge cases (score 70-80, grens-alert) een gericht Sonnet-call met alle context vragen om "is dit echt een alert?". Kost ~€0,05/alert maar veel betere kwaliteit op grensgevallen.

5. **Reply-feedback loop**: stuur in elke SMS "antwoord 1=goed 2=mediocre 3=miste/te laat 4=te vroeg"; verzamel feedback en gebruik om drempels bij te stellen.

6. **Telegram-fallback**: bij MessageBird-falen automatisch naar een Telegram bot.

7. **Synoptische narratie**: voor multi-day outlook in digest: laat de LLM een 1-zinsverklaring genereren over wat er meteorologisch gebeurt ("hogedruk boven Schotland, geen swell verwacht"). Dit benadert de synoptische uitleg van de referentie-forecaster.

---

## Appendix A — Bronnen van de referentie-forecaster die we direct meenemen

| Bron referentie-forecaster | Onze implementatie |
|---|---|
| KNMI guidance / Harmonie | Open-Meteo `knmi_seamless` |
| DWD golfhoogtes | Open-Meteo Marine (default EU) |
| ECMWF pluim | Open-Meteo `ecmwf_ifs025` |
| GFS windkaarten | Open-Meteo `gfs_seamless` |
| UKMO maps | Open-Meteo `ukmo_global_deterministic` |
| Spectra A12 boei | RWS Waterinfo API (later: 2D spectrum) |
| Spectra J06 boei | RWS Waterinfo API |
| Spectra K13 boei | RWS Waterinfo API |
| Spectra IJgeul (IJG1) | RWS Waterinfo API — **primary voor Noordwijk** |
| RWS windmeting live | RWS Waterinfo API |
| Tide info | RWS astronomisch getij |
| BE meetnet | api.meetnetvlaamsebanken.be |
| Cefas Wavenet | wavenet.cefas.co.uk |
| Webcam | URL in SMS |

## Appendix B — Afkortingen en spotgroepen van de referentie-forecaster

```python
NEARBY_SPOTS = {
    'noordwijk':  ['Nwijk'],
    'cluster':    ['Katwijk', 'Nwijk', 'Zvoort', 'Wssnaar'],  # groepering de referentie-forecaster
    'similar':    ['Zandvoort', 'IJmuiden', 'Scheveningen'],
    'reference_buoy': 'IJG1',  # IJgeul
}

DIRECTION_ABBREVIATIONS = {
    'N': 0, 'NNO': 22.5, 'NO': 45, 'ONO': 67.5,
    'O': 90, 'OZO': 112.5, 'ZO': 135, 'ZZO': 157.5,
    'Z': 180, 'ZZW': 202.5, 'ZW': 225, 'WZW': 247.5,
    'W': 270, 'WNW': 292.5, 'NW': 315, 'NNW': 337.5
}

BEAUFORT_TO_KNOTS = {
    1: 2, 2: 5, 3: 9, 4: 14, 5: 19, 6: 25, 7: 32, 8: 39
}
```

## Appendix C — Meteorologische verklaringen van de referentie-forecaster om in algoritme te verwerken

Uit zijn SMS'jes leerden we:

1. **"Cyclonale isobaren kromming in het 1020hPa lijntje"** (21-8): trog → wind-dip lokaal. Detectie: vergelijk druk-gradient over de regio; lage gradient = lage wind.

2. **"De swell heeft een totaal andere frequentie dan de windgolven"** (6-8): wind sea (200mhz/5s) + groundswell (100mhz/10s) zijn aparte energiepieken. We moeten ze los waarderen.

3. **"De Vlaamse banken filter"** (21-8): swell met kortere periode (7s) komt makkelijker over ondiepe zandbanken. Voor BE/ZL = relevant. Voor Noordwijk minder.

4. **"Refractie om de pier"** (23-8): swell uit NNO komt niet rond IJmuiden pier voor Noordwijk. Implementatie: penalty op swell-richting 350°-30°.

5. **"Springtij-effect bij volle/nieuwe maan"** (23-8): stroming sterker, korter window. Voor MVP: maan-fase niet gemodelleerd, maar wel als notitie in SMS bij springtij dagen.

6. **"Omgekeerde zeewind bij koud binnenland"** (uit 4-1-2025 archief): in winter kan na zonsopkomst de wind even gaan liggen of naar oost draaien. Lokaal effect rond Noordwijk-Castricum.

## Appendix D — De 13 SMS-validatieset

Volledige tekst-paraphrases beschikbaar in `data/historical_sms_set.json`. Velden per entry:
- `date`
- `ref_alert_explicit` (boolean)
- `ref_noordwijk_assessment` (text paraphrase)
- `ref_alert_type` (een van T1-T5 of "none")
- `expected_algorithm_output` (score range, alert ja/nee)

Deze set wordt door `validate.py` in Stap 9 gebruikt om het algoritme te kalibreren.

---

## Slotwoord

Dit plan benadert de werkwijze van de referentie-forecaster zo goed als algoritmisch mogelijk is voor één spot (Noordwijk). De LLM-laag zorgt voor natuurlijke berichten, de deterministische scoring zorgt voor reproduceerbare beslissingen, en de validatieset zorgt voor kwaliteitsborging tegen zijn historische output.

Het is geen vervanging van de referentie-forecaster zelf — hij heeft 16+ jaar surferservaring en kan dingen zien die het algoritme niet ziet. Maar voor de specifieke use case "stuur me een SMS als Noordwijk binnen 24u goed wordt" is dit een sterke benadering.

Belangrijkste afhankelijkheden voor succes:
1. Toegang tot Anthropic API (Haiku 4.5)
2. RWS Waterinfo API blijft beschikbaar (publiek)
3. Open-Meteo blijft gratis (zo lijkt het)
4. MessageBird account met saldo
5. **Genoeg historische bericht-data van de referentie-forecaster om tegen te valideren** — als gebruiker meer kan aanleveren, wordt het systeem proportioneel beter
