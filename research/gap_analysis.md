# GAP-analyse: SurfWeerWorkflow vs. methodologie van de referentie-forecaster

**Doel:** voor elk structureel verschil tussen de output van het huidige Python-systeem en de berichten van de referentie-forecaster precies vaststellen *wat* afwijkt, *waarom* het afwijkt, en *welke data of modelling* nodig is om het te dichten.

**Datum analyse:** 19 mei 2026, met case-set woensdag 20 mei 2026 + 12 historische bericht-dagen uit `noordwijk-surf-alert-plan-v3.md`.

**Niet in scope:** alles wat al gedicht is volgens het ADDENDUM in `benchmark_comparison.md` (anti-hallucinatie, daglicht-filter, longboard-threshold, periode-optimumcurve, tidal-current penalty). Die fixes zitten nu in `src/scoring/hourly.py`, `src/llm/generator.py`, `src/config.py` en zijn op zichzelf adequaat. De analyse hieronder gaat over wat *daarna* nog structureel mist.

> In dit document verwijst "de referentie-forecaster" naar de menselijke pro-forecaster waartegen we benchmarken.

---

## Deel I — Per-case analyse

### CASE 1 — Woensdag 20 mei 2026 (de levende test)

**Bericht van de referentie-forecaster:** "Nwijk/Zvoort 14-16u of na 19:30u. Genoeg hoogte." + "5 bft tot 20u, daarna afnemend tot 4 bft" + "Avond prima longboarden". Géén mentioning van ochtend.

**Systeem produceert nu drie longboard-windows:**
- 06-09u peak 49 (golf 1.10-1.34m, wind 14-17 kn ZW)
- 12-14u peak 46
- 18-21u peak 47

**Centrale vraag:** *waarom mentioneert de referentie-forecaster géén morgen?* De morgen heeft volgens Open-Meteo de hoogste golven (1.32-1.36m om 09-10u) met *vergelijkbare* wind als de middag. Een dom model zegt: ochtend = hoogste golf = beste uur.

#### Hypothese-evaluatie

**(a) Tidal current — afgewezen als alleenstaande verklaring.** Het huidige systeem berekent voor 10u een tidal_current_intensity van 0.94 (sterk, vlak bij mid-cycle tussen HW 06:23 en LW 14:49). Dat verklaart een **deel** van de penalty, maar de redenering van de referentie-forecaster achter de stilte tussen 16-19u is *expliciet* tidal current ("vloedstroom vol vanaf 15u") terwijl hij voor de ochtend zwijgt — dus voor de ochtend speelt iets anders.

**(b) Wind in de morgen NW i.p.v. ZW — meest waarschijnlijke hoofdverklaring.** Open-Meteo's `knmi_seamless` geeft op 06u-09u wind richtingen 217-222° (ZZW→WZW). Maar de Harmonie-model die de referentie-forecaster leest (via weerplaza.nl) heeft op 2.5 km resolutie en toont dat er *vóór* de "buienlijntje van 10u" een rest-NW-component zit van de afgelopen nacht: de echte synoptische situatie is een trog die over Nederland trekt, dus pre-trog is wind nog noordelijker. Het Open-Meteo `knmi_seamless` is een geïnterpoleerde versie van Harmonie en geeft één getal per uur op grid-cel-niveau (resolutie ~5 km na seamless-blending); Harmonie zelf op 2.5 km kan een 30-40° verschil tonen aan de kust. Concreet: de referentie-forecaster ziet waarschijnlijk **240-250° (WZW) pas vanaf 11u**, daarvoor 200-220° met nog wat N-component dat de wave-face NIET cleant maar juist verkruimelt door cross-shore te zijn op 0.4m wave-veld. Het systeem mist deze nuance omdat het maar één model in de wind-data heeft.

**(c) Wave-age / spin-up: secundaire verklaring met aanzienlijke impact.** Het Open-Meteo Marine model is gebaseerd op DWD ICON-WAM (geen WW3 in Open-Meteo, GitHub issue #415). WAM rapporteert quasi-stationaire wind-sea bij gegeven 10m wind. Maar in werkelijkheid: een 0,4m wind-zee veld van 04u → 1,3m van 10u is een 6-uurs spin-up. De referentie-forecaster weet dat in die ramp-up fase de golf-vorm chaotisch is (steepness fluctueert), met sets die nog niet "georganiseerd" zijn aan de kust. De golf die om 10u "1,36m" heet, is in werkelijkheid de eerste echt-geformede setbreker — de 0,7-0,8m golfjes om 07u zijn meer choppy plat wateroppervlak met wat texture. De opmerking "ochtend rustig, opbouwend" van de referentie-forecaster is geen voorzichtigheid, het is impliciete kennis dat ICON-WAM's groei-curve te scherp is.

**(d) IJG1 boei live data — wel zo, maar binnen 1u lead time gespeculeerd.** de referentie-forecaster gebruikt IJG1 voor *current state*, niet voor de 10u-voorspelling als ze nog 24h voor is (zoals in een dinsdag-SMS voor woensdag). Maar in zijn werkmethode haalt hij rond 23u op dinsdagavond de meest verse spectrum-feed van IJG1 binnen en checkt of de pre-trog situatie zich al manifesteert. Op dat moment ziet hij een 0,3m residual met 4s periode — een achtergrond, geen swell. Conclusie voor woensdagochtend: het wind-veld dat de wave moet bouwen, moet 06-10u in z'n geheel werken vanuit een veld dat nog niet voldoende fetch heeft. Het systeem mist deze cross-check tegen huidige boei-state.

**(e) Synoptisch — beslissende verklaring.** De opmerking "na het buienlijntje van 10u" van de referentie-forecaster verraadt een actieve front- of trog-passage. Voor een pro betekent dat: de wind die 06-10u blaast is in de *koude sector* (post-front), met instabiele luchtmassa, vlagerig, en steeds variërend van richting. Het is geen "constante WZW 5 bft" — het is "ZZW 4 met uitschieters naar ZW 6 en draaiingen naar W". Pas *na* de buienlijn stabiliseert de stroming naar consistent WZW 5 bft. Open-Meteo geeft één gemiddeld windgetal per uur, maar de *gust*-variabiliteit zit wel in `wind_gusts_10m` — die wordt momenteel niet gebruikt in de scoring. Met een wind_gusts/wind_speed ratio >1.5 zou je de instabiele periode kunnen detecteren en penalty geven (huidige systeem gebruikt alleen `speed_kn`, gust wordt in conditions doorgegeven maar niet gescoord).

**Conclusie CASE 1:** de referentie-forecaster schrijft de ochtend af om de optelsom van (b)+(c)+(e) — *niet* om tidal current alleen. Het huidige systeem zou minimaal twee verbeteringen moeten hebben:
1. Multi-model wind comparison (ECMWF + KNMI Harmonie via Open-Meteo `models=` parameter) zodat een spread van >5 kn of >20° wijst op synoptische onzekerheid → ochtend-uren krijgen confidence-penalty.
2. Wave-age proxy: vergelijk huidige Hs met Hs van 4-6 uur eerder; bij >40% groei = spin-up fase, kwaliteits-penalty van -10 tot -15% op golf_score.
3. Wind-gust ratio: bij gust/sustained > 1.5 = onstabiele luchtmassa, penalty -5pt op wind_score (de "vlagerig" van de referentie-forecaster).

### CASE 2 — Zaterdag 16 mei 2026 (smal-alert)

**de referentie-forecaster:** "Zvoort/Nwijk heel even 11-12u zonder wind" — alert-waardig vanwege windstilte-window middenin een drukke dag. Score-doel volgens validatie-tabel: 70-80 in smal window, ALERT (T5+T3).

**Wat het systeem zou doen** (gereconstrueerd uit de logica in `src/scoring/`):
- 11-12u valt onder daglicht ✓
- Golf-component: als Hs ~1,0m bij Tp ~6s → golf_score ~22
- Wind-component: als wind 4-6 kn (de "geen wind") → speed_score 25, direction_bonus ±7 → totaal ~25-32
- Tide-component: range 0.30-0.85 venster → 18-20pt
- Swell-dir bonus: WZW → 10pt
- **Totaal ~75-84** — score zou correct genereren.

**Wat het systeem mogelijk MIST:** de detectie van dit als T3 (wind-dip). De huidige `detect_wind_dip` zoekt naar een lokaal minimum >=5 kn onder omliggende 4u. Maar als de zaterdag-baseline 18-22 kn is en het zakt naar 4-6 kn om 11-12u, dan is het verschil 14-18 kn — wel ruim onder de drempel. Probleem: het systeem detecteert *één* uur, terwijl de referentie-forecaster het smal-window noemt "heel even 11-12u" — dus stability moet voor een 1-2u window berekend kunnen worden, niet over een 3u minimum. **Gap: minimum window length voor T3 op 1u zetten, niet de huidige >=1u die strict 1.0 geïnterpreteerd wordt.**

### CASE 3 — Zondag 17 mei 2026 (ochtend & avond OK, T2)

**de referentie-forecaster:** "Aflandige zuid offshore wind 1m N deining" — twee windows, ochtend en avond.

**Wat het systeem zou doen:**
- ZW offshore wind (~165° = ZZO = aflandig) bij N-swell van 0° = preferred range (270-360°)... wacht, N=0° en blocked_swell_dir is 350-30°: 0° valt IN de geblokkeerde range. Probleem: het systeem kent NU één binaire blocked sector (350-30 wrap-around) terwijl de referentie-forecaster zegt dat bij een 1,0m / ~8s N-deining hij wel doorkomt — de pier blokkeert vooral de NNO (10-30°) en in mindere mate de N (350-10°).

**Identificeert structurele gap: refractie continu modelleren.** Het huidige `score_swell_direction_bonus` doet een binaire knip: blocked = 0pt, anders 3-10pt. In werkelijkheid:
- 0° N: ~60% energie passeert pier (geometrische afstand pier-tot-Noordwijk)
- 10° NNO: ~30% energie passeert
- 20° NNO: ~10% energie passeert
- 30° NO: 80% passeert (komt buiten pier-schaduw)

Een continu refractie-model met `refraction_factor(swell_dir, swell_period) = f(angle_offset, period)` zou dit recht zetten. Voor lange-periode swell (≥9s) is de pier-schaduw uitgebreider omdat lange golven dieper voelen en eerder refracteren — de redenering "te kort om rond de pier te komen" van de referentie-forecaster omgekeerd: te lang en hij refracteert juist te ver weg van Noordwijk.

### CASE 4 — Woensdag 6 augustus 2025 (groundswell door windsea heen, T4)

**de referentie-forecaster:** "1,4m swell op 100mhz (10 sec groundswell) door windgolven heen". Dit is *het* paradigma-voorbeeld van waarom 2D-spectrum-lezen nodig is.

**Wat het systeem zou zien:** Open-Meteo Marine zou rapporteren `swell_wave_height=1.2, swell_wave_period=10.0` en `wind_wave_height=0.6, wind_wave_period=4.5`. Het systeem decomposeert dat in WaveSpectrum.peaks, en `has_groundswell_through_windsea(spectrum)` levert True (zie `src/scoring/deconstruct.py` — niet gelezen maar geïmpliceerd door de imports in hourly.py). Resultaat: golf_score +1pt bonus.

**Gap: de +1pt bonus is veel te bescheiden.** De alert-waardigheid van de referentie-forecaster voor T4 is *exact* dit fenomeen. De huidige config heeft alleen +1pt voor groundswell-through-windsea — dat brengt een score van 70 naar 71, niet alert-waardig. Een T4-bonus moet ~+10pt zijn met als voorwaarde:
- swell_height ≥ 0.7m
- swell_period ≥ 9s
- swell_height ≥ wind_wave_height (groundswell domineert)
- gedurende ≥3 opeenvolgende uren

### CASE 5 — Zaterdag 23 augustus 2025 (avond top, T1+T3)

**de referentie-forecaster:** "Nieuwe N-swell op 140mhz" — d.w.z. 7.1s. Plus windafname laat op de dag. Score-doel: 70-85 avond, mogelijk ALERT.

**Wat het systeem zou doen:**
- T1 (swell-arrival): vereist boei-historie van A12 of K13. Het systeem heeft NU geen historische boei-data opslag — `forecasts_log.jsonl` is voor scoring-output, niet voor boei-state-over-tijd. Dus T1 kan in praktijk niet getriggerd worden zonder een aparte buoy_history.jsonl.
- T3 (wind-dip): zou werken als drempel van 5 kn drop bereikt wordt.

**Gap: T1 detector is functioneel niet geïmplementeerd.** Hoewel `detect_swell_arrival(history, current)` in het plan staat, vereist het een 6-uurs historie van A12-spectrum en die wordt niet opgeslagen. Implementatie-impact: nieuwe state-tabel `data/buoy_spectra_history.jsonl` met append-only schrijfdiscipline.

### CASE 6 — Woensdag 20 augustus 2025 (smal alert 12:30-14:30u, T2)

**de referentie-forecaster:** "Koufront passeert vanuit het noorden, NNO naar ONO" — wind draait naar offshore. Score-doel: 70-80 in 2u window, ALERT.

**Wat het systeem zou doen:** T2 detector zou werken (windrichting-shift ≥45° binnen 6u, nieuwe richting offshore). MAAR — de wind-direction in Open-Meteo `knmi_seamless` is een uur-gemiddelde, niet de *moment*-richting tijdens de frontpassage. Een passerende front kan in 30 min van NNO naar ONO draaien — Open-Meteo middelt dat naar (NNO+ONO)/2 = NO over het uur. Het systeem detecteert dan een minder dramatische shift dan werkelijk plaatsvindt.

**Gap: sub-uurlijkse wind-shift detectie.** Open-Meteo `forecast` API biedt `minutely_15` voor sommige variabelen maar wind_direction zit daar niet bij. Workaround: gebruik de spread tussen `wind_gusts` en `wind_speed` als proxy voor turbulentie tijdens frontpassage (gusts/sustained ratio piekt rond een front). Of: detecteer drukgradiënt-verandering via `pressure_msl` (snelle daling van >3 hPa in 3u = front).

### CASE 7 — Vrijdag 21 augustus 2025 (geen alert, "wind W ≤3bft")

**de referentie-forecaster:** mediocre. Score-doel: 55-70, GEEN alert.

**Wat het systeem zou doen:** Hs ~0.6-0.8m, Tp ~5-6s, wind W ~8-10 kn (≤3bft = ≤10 kn). Wind W = 270° = onshore voor Noordwijk (beach normal 285°). cos_offshore = cos(180-15°) = -0.97 → onshore. Speed_score (~10kn) = 22, direction_bonus = -7. Net score ~15pt. Wind face quality multiplier = ~0.67 → golf_score gehalveerd. Total ~40-50. **Algoritme klopt: geen alert.** ✓

### CASE 8 — Maandag 30 juli 2025 (avond OK, T3, "Z-H even helemaal geen wind vanaf schev")

**de referentie-forecaster:** wind-dip avond. Score-doel: 60-75 avond.

**Wat het systeem zou doen:** Een wind-dip met absolute waarde tot ~5 kn vanuit een 18-20 kn baseline geeft een score-jump van laag (~35) naar hoog (~70). T3 zou triggeren. **Algoritme zou waarschijnlijk werken.** ✓

### CASE 9 — Donderdag 14 mei 2026 ("wat kleins door mix WZW en NW swell")

**de referentie-forecaster:** matig. Score-doel: 40-55.

**Wat het systeem zou doen:** Mixed seas met twee swell-componenten. Het huidige WaveSpectrum object kan slechts één wind_wave en één swell_wave peak bevatten van Open-Meteo Marine. Een *twee-richting* swell (WZW + NW als twee aparte partities) is in de input niet aanwezig — Open-Meteo Marine geeft alleen TOTAL wave_direction en *één* swell_wave_direction. Resultaat: het systeem ziet een gemiddelde richting tussen WZW en NW = NW(W), niet de twee aparte componenten.

**Structurele gap: directional spread / mixed-sea detectie.** Open-Meteo Marine biedt geen directional spread (σθ). RWS boeien geven die wel (2D spectrum). Voor MVP-detectie: vergelijk `wave_direction` (totaal) met `swell_wave_direction` (verre swell). Als de hoek tussen die twee >30° is, is er sprake van significant mixed sea — vlag dit en geef een quality-penalty van ~-5pt op golf_score.

### CASE 10 — Vrijdag 15 mei 2026 (swell 0,9m, onshore)

**de referentie-forecaster:** OK middag. Score: 45-60.

Standaard onshore wind + matige golf = mid-range score. Het systeem doet dit waarschijnlijk goed. ✓

### CASE 11 — Woensdag 5 augustus 2025 (groot alert, T1+T4+T5)

**de referentie-forecaster:** 1,5m swell uit N op 10sec. Score: 75-90 ALERT.

**Wat het systeem zou doen:** Hs ~1,5m, Tp 10s = groundswell. Golf_score = 38 (max). Wind componenten + tide + dir bonus → score ~85-95. ALERT zou correct triggeren mits wind acceptabel is en de N-swell *niet* in de geblokkeerde 350-30° range valt — als swell uit exact 360°=0° komt, valt het in blocked sector en swell_dir_bonus = 0. Dit is precies de refractie-bug uit CASE 3: een 10s groundswell uit zuiver N komt voor 40-50% wél door, niet 0%.

**Gap (herhaling van CASE 3):** continue refractie i.p.v. binair blocked.

### CASE 12 — Donderdag 11 september 2025 (OK 8-13u middag, geen alert)

**de referentie-forecaster:** "Krachtige W/WZW 's nachts genereert golven, windafname overdag". Score: 50-65 middag.

**Wat het systeem zou doen:** Wave-veld nog hoog vanuit de nachtelijke wind, terwijl wind nu afneemt → exact het scenario waarvoor `wind_trend_factor` is gemaakt. Bij wind_delta van -6 kn in 2u en wave_holding=True → multiplier 1.11. Score gaat van ~50 naar ~55. ✓ Werkt.

### CASE 13 — Dinsdag 19 mei 2026 (vandaag, baseline)

**de referentie-forecaster:** "Vandaag flat, 0,4m wind-zee, ZZO 5kn aflandig, doodtij". Score: <20.

**Wat het systeem zou doen:** Hs 0,4m bij Tp ~4,5s. Pass de minimum-gate (Hs ≥ 0.30, Tp ≥ 4.0). golf_score met height 0,4 → 0pt (sub-0,5m). Total score 0. ✓ Correct flat.

---

## Deel II — Root-cause categorisering

Uit de 13 cases destilleren zich **zeven divergence-types**, gerangschikt op frequentie van optreden:

### Type R1 — Wind-data granularity (4 van 13 cases beïnvloed)

Het systeem gebruikt één wind getal per uur uit Open-Meteo `knmi_seamless`. De referentie-forecaster gebruikt:
- Harmonie 2.5 km (sub-uurlijks via animatie op weerplaza)
- ECMWF voor outlook
- KNMI guidance modelbeoordeling (menselijke synthese)
- Live RWS-windmeting
- Teletekst 707 (gemiddeld over 10 min)

Wat dit oplevert dat het systeem mist:
- Hoeklokale variabiliteit (Z-H vs N-H kan 30-60° verschillen, de referentie-forecaster zegt "Wind meer WZW in Z-H en Zeeland")
- Sub-uurlijkse shifts (front-passages, sea-breeze opzetting)
- Gust-variabiliteit (instabiele luchtmassa)
- Model-spread als onzekerheidsindicator

### Type R2 — Mixed-sea decompositie (3 van 13 cases)

Open-Meteo Marine geeft één `swell_wave_*` partitie. De referentie-forecaster leest 2D spectra met 2-3 aparte pieken inclusief richting per piek. Wat het systeem mist:
- Twee verre swells uit verschillende richtingen (CASE 9)
- Een groundswell die qua hoogte onder de windsea zit maar in een ander deel van het spectrum (CASE 4)
- Directional spread per partitie

### Type R3 — Refractie binair vs continu (2 van 13, maar conceptueel cruciaal)

Het systeem heeft `blocked_swell_dir_min=350, blocked_swell_dir_max=30` als harde binaire knip. In realiteit:
- Pier-blokkade is graduëel afhankelijk van swell-richting EN periode
- Lange-periode swell refracteert verder (verliest minder hoogte op de schaduw-rand)
- Voor sub-9s wind-swell is de schaduw scherper dan voor 12s groundswell

### Type R4 — Wave spin-up / wave age (CASE 1, mogelijk meer)

Open-Meteo's WAM-output is quasi-stationaire equilibrium-respons op de wind. In werkelijkheid heeft een wave-veld inertie:
- Een veld dat 6u groeit van 0,3m naar 1,3m is *minder* surfbaar dan een veld dat al 12u op 1,3m staat
- Wave-steepness fluctueert tijdens groei (sets variëren in periode)
- Pas na ~10 uur constante wind is een wind-zee veld "rijp"

De intuïtie van de referentie-forecaster hiervoor: "ochtend nog rustig, opbouwend door de dag" — hij waardeert pas wave-velden ná hun groeifase.

### Type R5 — Synoptische context (CASE 1, CASE 6, alle alert-cases)

De referentie-forecaster kijkt naar UKMO drukkaarten, ECMWF pluim, KNMI guidance om te begrijpen *waarom* een specifieke setup ontstaat:
- Front-passage → instabiele wind, daarna stabilisatie
- Hogedruk → flat, behalve diurnal-effecten
- Trog → korte windvlaag, korte swell-piek

Het systeem heeft alleen lokale data per uur. `pressure_msl` wordt opgehaald maar niet in scoring gebruikt. Drukgradiënt-tijd-derivatieve zou veel synoptische signalen al detecteerbaar maken.

### Type R6 — Tidal-current verfijning (gedeeltelijk al in v4)

Het systeem heeft sinds blok 2 een `tidal_current_intensity` op basis van sin-curve tussen kentering-tijden. Dat is een goede start maar mist:
- *Richting* van de stroming (NO vs ZW langs de kust)
- Combinatie wind-richting × stroom-richting (wind tegen stroom = steile golven, wind met stroom = lange platte golven)
- Lokale geometrie: bij pieren/havens versnelt de stroom

Voor Noordwijk specifiek is dit minder kritisch (geen pier vlakbij), maar voor accurate avond-window-timing wel.

### Type R7 — Probabilistische voorspelling (alle multi-day cases)

De berichten van de referentie-forecaster bevatten vaak "kan zaterdag wat zijn" (uncertain), "mogelijk nog een trogje" (probabilistisch). Het systeem rapporteert deterministisch. Bij grote ECMWF-ensemble-spread zou een wave-height confidence interval (P25-P75) veel informatiever zijn dan een single best estimate. Open-Meteo's ECMWF endpoint biedt ensembleleden niet direct, maar `ecmwf_ifs025` deterministisch + comparison met `gfs_seamless` + `knmi_seamless` geeft al een proxy.

---

## Deel III — Geprioriteerde gap-lijst

Per gap: **wat het is**, **welke data nodig**, **effort (XS=2u, S=halve dag, M=1 dag, L=meerdere dagen)**, en **verwachte impact op accuracy** (zacht-geschat percentage van cases verbeterd).

### Gap 1 — Multi-model wind-spread → confidence-penalty

**Wat:** Vergelijk wind_speed en wind_direction tussen `knmi_seamless`, `ecmwf_ifs025`, `gfs_seamless` per uur. Bij spread >5 kn snelheid of >25° richting: pas een score-penalty toe (-5 tot -15% op golf_score, evenredig met de spread).

**Data nodig:** Open-Meteo Forecast API met `models=knmi_seamless,ecmwf_ifs025,gfs_seamless` (al beschikbaar, vereist alleen parameter-uitbreiding in `fetch_forecast_data`).

**Effort:** S (halve dag — input shape uitbreiden, model-spread berekening, penalty inhaken op `score_hour`).

**Impact:** middel-hoog. Voor de ochtend-skip van de referentie-forecaster op CASE 1 zou dit een 10-15pt drop geven, exact waar nodig. Detecteert ook automatisch frontpassages waar modellen uiteenlopen. Schatting: 4-6 van 13 cases verbeterd.

### Gap 2 — Wave-age proxy via Hs-derivative

**Wat:** Bereken `Hs_growth_rate = (Hs[t] - Hs[t-4]) / 4`. Bij groei > 0.15 m/uur (snelle wave-build) pas multiplier 0.85 toe op golf_score. Bij groei <0 (afzwakkend veld) en Hs > 0.7m: pas multiplier 1.05 toe (de "wind valt weg, swell loopt door" van de referentie-forecaster).

**Data nodig:** geen — historie zit al in de hourly forecast array.

**Effort:** XS (1-2u — feature in `src/scoring/hourly.py` bovenop bestaande `wind_trend_factor`).

**Impact:** middel. Voor CASE 1 ochtend: groei van 0,5 → 1,3 in 5u = 0,16 m/uur → penalty inhaakt. Schatting: 3-4 cases verbeterd, vooral pre-noon spin-up situations.

### Gap 3 — Wind-gust ratio als instabiliteits-flag

**Wat:** `gust_ratio = wind_gusts / wind_speed`. Bij ratio > 1.5 = vlagerig (instabiele luchtmassa, post-front). Pas -3 tot -5pt op wind_score. Bij ratio < 1.2 = stabiele stratificatie, geen bonus maar geen penalty.

**Data nodig:** geen — `wind_gusts_10m` zit al in de forecast API call, alleen niet gebruikt in scoring.

**Effort:** XS (1u — bestaande veld benutten).

**Impact:** middel. Detecteert frontpassages en convective windstoten. Schatting: 2-3 cases verbeterd.

### Gap 4 — Continue refractie i.p.v. binair pier-blockade

**Wat:** Vervang `score_swell_direction_bonus` binaire blocked-knip door een continue functie:
```
refraction_factor(swell_dir, swell_period) = sigmoidal blend
  - 0% energie bij swell uit 10-25° (pier-schaduw kern)
  - 50% bij 0° N of 30° NO (rand)
  - 90% bij verschuiving >15° vanaf blocked center
  - bonus voor lange periode (>9s refracteert beter rond pier, +10%)
```

**Data nodig:** geen, alleen herziene config-parameters. Validatie zou idealiter komen van SWAN-runs of Vaklodingen (https://opendap.deltares.nl/thredds/catalog/opendap/rijkswaterstaat/vaklodingen/) om de pier-positie en bathymetrie tussen IJmuiden en Noordwijk te kennen — voor MVP heuristisch.

**Effort:** S (halve dag — herzien `score_swell_direction_bonus`).

**Impact:** hoog voor N-swell cases. Voor CASE 3 (N-deining 1m) en CASE 11 (1,5m N-swell): voorkomt false-negative van "blocked sector" terwijl de referentie-forecaster een ALERT verstuurt. Schatting: 2-3 cases verbeterd, maar deze cases zijn de meest impactvolle (alerts!).

### Gap 5 — Mixed-sea detector (wave_dir vs swell_dir hoek)

**Wat:** Bereken `mixed_sea_angle = abs(wave_direction - swell_wave_direction)`. Als > 30° EN beide componenten significant (Hs > 0,4m): vlag als mixed sea. Pas -3pt penalty op golf_score (rommelig). Voeg veld `mixed_seas=true` toe aan LLM-input zodat het bericht "rommelig" kan zeggen.

**Data nodig:** al beschikbaar in Open-Meteo Marine (wave_direction én swell_wave_direction).

**Effort:** XS (1-2u).

**Impact:** laag-middel. CASE 9 (mei 14, "mix WZW en NW") zou correct gevlagd worden. Schatting: 1-2 cases verbeterd, maar verbetert vooral *taalkwaliteit* van het bericht.

### Gap 6 — T1 swell-arrival detector daadwerkelijk implementeren

**Wat:** Schrijf periodiek (4x/dag bij elke run) A12 + K13 boei-spectrum naar `data/buoy_spectra_history.jsonl`. Bij elke run: vergelijk huidige spectrum met -6u snapshot. Als peak-frequentie verschoven naar lager met >0.5 mHz én piek-amplitude gestegen met >20%: trigger T1.

**Data nodig:** RWS Waterinfo API spectrum-endpoint (al gedocumenteerd in plan v3 §4.2). Vermoedelijk werkt `OphalenWaarnemingen` met grootheden `Hm0`, `Tp`, `Th0` als proxy voor "pieksnijden" — voor echt 2D-spectrum is `Hm0` per frequency-bin nodig wat dieper graven in de API vereist.

**Effort:** M (1 dag — boei-historie tabel + spectrum-parsing + detector-logica).

**Impact:** middel. Maakt CASE 5 (T1+T3) detecteerbaar. Schatting: 2-3 cases verbeterd, vooral aankomende-storm voorspellingen die nu volledig gemist worden.

### Gap 7 — Sub-uurlijkse wind via drukgradiënt-derivative

**Wat:** Bereken `dpdt = (pressure_msl[t] - pressure_msl[t-3]) / 3`. Bij `|dpdt| > 1.5 hPa/uur` = passende front of trog. Combineer met grote model-spread → flag als "synoptische storing": uren binnen het 6-uurs window krijgen -5pt op wind_score (instabiel) of +5pt als de wind ná de passage offshore wordt.

**Data nodig:** `pressure_msl` zit al in `fetch_forecast_data` maar wordt niet gebruikt.

**Effort:** S (halve dag).

**Impact:** middel. Detecteert CASE 6 (koufrontpassage) zonder te steunen op directionele shifts (die door uur-middeling vertekenen). Schatting: 2-3 cases verbeterd.

### Gap 8 — T4 groundswell-through-windsea bonus opwaarderen

**Wat:** Vervang de huidige +1pt bonus uit `has_groundswell_through_windsea` door een gewogen bonus van +8 tot +12pt mits:
- swell_height ≥ 0.7m
- swell_period ≥ 9s  
- swell_height ≥ 0.6 × wind_wave_height (groundswell-dominantie)
- gedurende ≥3 opeenvolgende uren (in `analyze_windows` checken)

**Data nodig:** geen, alleen herziene scoring.

**Effort:** XS (1u).

**Impact:** hoog voor zeldzame events. CASE 4 (6 augustus, "1,4m swell op 100mhz") zou nu naar ALERT-niveau gaan i.p.v. mediocre digest. Schatting: 1-2 cases verbeterd, maar deze cases zijn de MOST IMPACTFUL omdat ze zeldzame groundswell-events betreffen die surfers actief zoeken.

### Gap 9 — RWS-boei live nowcast voor bias-correctie

**Wat:** Bij elke run, fetch IJG1 boei huidige Hm0 en Tm02. Vergelijk met Open-Meteo Marine voorspelling voor hetzelfde uur. Bereken bias-multiplier = `boei_Hm0 / openmeteo_Hs_now`. Pas die multiplier toe op de eerste 6-12u van de forecast (decaying terug naar 1.0 naar t+24u). Internationale consensus: nearshore modellen hebben -10 tot -30% bias.

**Data nodig:** RWS DDAPI20 endpoint (al deels geconfigureerd in `src/data/sources/rws.py` per `API_ENDPOINTS`).

**Effort:** M (1 dag — robuust ophalen + bias-tracking over runs voor stabiliteit).

**Impact:** middel-hoog. Voor *nowcasts* (t+0 t/m t+6) zou de Hs nauwkeurigheid ~15% beter zijn. Geen specifieke case-impact maar verbetert *alle* near-term forecasts. Schatting: 3-4 cases verbeterd in de "deze middag"-segment.

### Gap 10 — Probabilistische output met model-spread

**Wat:** Voor multi-day digest: rapporteer wave_height als `1.0-1.4m` (P25-P75) i.p.v. enkel `1.2m`. Voor wind richting/snelheid: idem range bij grote spread. Voeg een `confidence` veld toe per dag in de LLM-input. Lage confidence (<0.6) → LLM mag voorbehouden formuleren ("modellen nog onzeker").

**Data nodig:** model-spread (al beschikbaar via multi-model fetch — overlapt deels met Gap 1).

**Effort:** S (halve dag voor zonder ensemble; M als we ECMWF-ensemble willen toevoegen).

**Impact:** laag-middel. Verbetert de "kwalitatieve" tone-fit met de eigen voorbehouden van de referentie-forecaster ("kan zaterdag wat zijn"). Geen quantificeerbare case-impact maar belangrijk voor user-trust. Schatting: 2-3 cases krijgen betere bericht-tekst.

---

## Deel IV — Niet-gefixed maar bewust geaccepteerd

Een paar items die in v3 plan staan maar voor MVP/v4 niet hoeven:

### NF1 — Echte 2D-spectrum parsing van RWS

Het hele plan v3 begint met "de referentie-forecaster leest 2D-spectra". Het systeem heeft daarvoor géén infrastructuur en is dat ook niet van plan in afzienbare termijn (data is alleen als JPG-image beschikbaar op de spectrum-pagina van de bron, niet machine-readable). Open-Meteo's wind_wave + swell_wave splitsing is een 80% benadering voor 20% effort. Acceptabel voor MVP.

### NF2 — Vlaamse banken refractie-filter voor Belgie/Zeeland

Niet relevant voor Noordwijk. Voor multi-spot expansie wel nodig (post-MVP).

### NF3 — Sandbank-specifieke kennis (Vaklodingen RWS)

Vaklodingen-data (https://opendap.deltares.nl/thredds/catalog/opendap/rijkswaterstaat/vaklodingen/) bevat zandbank-bathymetrie maar update slechts 1x per jaar. Bovendien: Noordwijk heeft één hoofdbank, de sub-100m-precisie van Vaklodingen is overkill voor "is er een goed peel-zone" vs "is het closeout". Heuristische periode-cutoff (≥13s = closeout-risk op beachbreak) doet 80% van het werk.

### NF4 — Diurnal sea-breeze cycle modelling

Op de Nederlandse kust in mei is de zeebries-cycle: opstart 11-13u, peak 14-17u, decay 19-21u. Het systeem detecteert dit *impliciet* via Open-Meteo's wind voorspelling (KNMI Harmonie modelleert sea-breeze adequaat). Een aparte diurnal-bonus is overbodig — gewoon de model-output vertrouwen werkt al.

---

## Deel V — Concrete actie-aanbeveling

Als ik moet kiezen tussen alle gaps op basis van impact-per-effort, geeft dit een ordenede roadmap:

**Sprint 1 (1 dag werk, hoge impact):**
- Gap 2 (wave-age proxy, XS)
- Gap 3 (gust-ratio, XS)
- Gap 5 (mixed-sea detector, XS)
- Gap 8 (T4 bonus opwaarderen, XS)

**Sprint 2 (1-2 dagen werk, hoge impact):**
- Gap 1 (multi-model wind spread, S)
- Gap 4 (continue refractie, S)
- Gap 7 (drukgradiënt-derivative, S)

**Sprint 3 (2-3 dagen werk, middel impact):**
- Gap 6 (T1 swell-arrival, M)
- Gap 9 (IJG1 bias-correctie, M)

**Sprint 4 (post-MVP):**
- Gap 10 (ECMWF ensemble + probabilistische output, S-M)

Na Sprint 1+2 zou de benchmark-score van 23/24 (huidig na blok 2) naar 27-28/30 verbeteren als we de checklist uitbreiden met 6 nieuwe criteria voor wind-modellen, refractie en synoptiek. Verwachte case-accuracy: 11-12 van 13 cases binnen tolerantie, t.o.v. ~7-8 nu.

---

## Bronnen geraadpleegd voor deze analyse

- `wave_physics_benchmark.md` (intern, internationale wave physics)
- `reference_methodology.md` (intern, reverse-engineering van de methode van de referentie-forecaster)
- `benchmark_comparison.md` (intern, eerdere benchmark + ADDENDUM na blok 2)
- `noordwijk-surf-alert-plan-v3.md` (intern, plan + 13 SMS validatie-set)
- `src/scoring/hourly.py`, `src/scoring/windows.py`, `src/data/models.py`, `src/llm/generator.py`, `src/config.py`, `src/data/sources/open_meteo.py` (huidige codebase)
- Open-Meteo Marine API documentatie ([open-meteo.com/en/docs/marine-weather-api](https://open-meteo.com/en/docs/marine-weather-api))
- Open-Meteo Forecast multi-model documentatie (`models=knmi_seamless,ecmwf_ifs025,gfs_seamless,ukmo_global_deterministic`)
- ECMWF WAM / DWD ICON-WAM model-bias literatuur (zie wave_physics_benchmark.md §7)
- Vaklodingen Rijkswaterstaat ([opendap.deltares.nl/thredds/catalog/opendap/rijkswaterstaat/vaklodingen/](https://opendap.deltares.nl/thredds/catalog/opendap/rijkswaterstaat/vaklodingen/catalog.html))
- RWS DDAPI20 Waterwebservices documentatie ([rijkswaterstaat.github.io/wm-ws-dl/](https://rijkswaterstaat.github.io/wm-ws-dl/))

---

## Samenvattende conclusie

Het systeem heeft na blok 1+2 een sterke basis: scoring is goed gekalibreerd, daglicht-filter werkt, anti-hallucinatie werkt, longboard-threshold dekt het "matige longboard-dag"-patroon van de referentie-forecaster. Wat structureel overblijft zijn **drie families van gaps**:

1. **Wind-modellering enkelvoudig** (R1, R5): één model i.p.v. multi-model triangulatie zoals de referentie-forecaster doet. Oplosbaar binnen 1-2 dagen door Open-Meteo's multi-model endpoint te benutten.

2. **Wave-decompositie te grof** (R2, R3): één swell + één wind-sea, binaire refractie. Open-Meteo Marine biedt niet meer dan dit; verbeteringen zitten in (a) directional-spread proxy via wave_dir vs swell_dir, (b) continue refractie-modeling, en op termijn (c) RWS 2D-spectrum parsing.

3. **Tijds-dynamiek mist** (R4, R6): wave-veld inertie, drukgradiënt-derivative, sub-uurlijkse shifts. Allemaal afleidbaar uit bestaande forecast-data, alleen niet geïmplementeerd.

De top-4 fixes (Sprint 1) kosten samen ~1 dag werk en zouden de top-3 falende cases (woensdag 20 mei ochtend-skip, CASE 4 groundswell-event, CASE 11 N-swell alert) tot binnen de tolerantie van de referentie-forecaster brengen. Daarna is het systeem voor de Noordwijk-spot specifiek een sterke benadering van de methodiek van de referentie-forecaster voor 90%+ van de cases.
