# Master Improvement Plan — SurfWeerWorkflow

**Synthese van vier diepgaande onderzoekssporen**
*Datum: 20 mei 2026 — gebaseerd op ~21.000 woorden aan voorafgaand onderzoek*

---

## 0. Bronnen die in dit plan gesynthetiseerd zijn

1. **`gap_analysis.md`** (4411 woorden) — Per-case gap-analyse van de referentie-forecaster 13 historische SMSes + woensdag 20 mei
2. **`industry_models.md`** (5750 woorden) — Surfline LOTUS, Magicseaweed, Stormsurf, Surf-Forecast.com, Windguru, NL-diensten
3. **`pro_forecaster_methods.md`** (6600 woorden) — WSL, Pat Caldwell, Sean Collins, wave physics, NL-specifiek
4. **`academic_ml.md`** (4100 woorden) — WW3/SWAN/ECWAM, XGBoost bias correction, ML approaches, EMODnet bathymetrie

Plus eerder onderzoek: `wave_physics_benchmark.md`, `reference_methodology.md`, `benchmark_comparison.md`.

---

## 1. Executive Summary

Het huidige systeem matcht de woensdag-output van de referentie-forecaster op **2 van 3 windows** (12-14u en 18-21u) maar genereert nog een extra morning-window (06-09u) dat de referentie-forecaster niet noemt. Diepere benchmarks op van de referentie-forecaster 13 historische SMSes laten zien dat de overblijvende gap **drie families** heeft:

**A. Enkelvoudige wind-data** — één model (Open-Meteo `knmi_seamless`) terwijl de referentie-forecaster 4 modellen triangulateert. **Belangrijkste bottleneck.**

**B. Te grove wave-decompositie** — geen continue refractie, geen wave-age, geen wave energy flux, T4-bonus te zwak. **Direct te fixen met bestaande data.**

**C. Geen real-time correctie** — geen RWS-boei feedback, geen multi-day learning, geen ECMWF ensemble. **Vereist nieuwe data-infrastructuur.**

Belangrijke meta-conclusie uit alle vier rapporten: **niemand bouwt naïeve "size × wind" scoring meer.** Surfline LOTUS gebruikt ML op 1M+ menselijke observaties, Stormsurf weigert algoritmes en blijft handmatig, MSW kapt bonus binnen size-cap. De gemene deler: **fysieke realisme + bias-correctie op spot-niveau + uncertainty-expressie**.

De academic literatuur is helder: een hybrid pipeline van **fysisch model + XGBoost bias correction op boei-residuals** levert 20-25% RMSE-reductie in Dutch North Sea harbour applicaties (peer-reviewed). Dit is de single most impactful technique die nog niet geïmplementeerd is.

---

## 2. Cross-cutting root-cause categorisering

Distillaat van alle vier rapporten: **7 systemische gaps** die vaker dan eens terugkomen.

### R1. Single-model wind-data (genoemd in 4/4 rapporten)
Open-Meteo `knmi_seamless` is één getal per uur per grid-cel (~5 km). De referentie-forecaster gebruikt Harmonie 2.5km + ECMWF + KNMI menselijke guidance. Wat dit oplevert dat ik mis:
- Spread tussen modellen = onzekerheidssignaal (vooral relevant bij frontpassages)
- Sub-uurlijkse shifts (modellen middelen weg)
- Spatial variability (Z-H vs N-H kan 30-60° verschillen)

### R2. Spectrum-decompositie te grof (genoemd in 4/4)
Open-Meteo Marine geeft één swell-partitie + één wind-zee partitie. De referentie-forecaster leest 2D-spectra met 2-3 aparte pieken. Surfline rolt nu actief "Swell Spectra" feature uit als premium feature.
- Mijn pipeline gebruikt `swell_wave_*` en `wind_wave_*` apart maar score ze gemiddeld
- Mixed-sea detectie (wave_dir vs swell_dir hoek > 30°) ontbreekt
- Combo-swell waar matige primaire + goed-georiënteerde secundaire werkt (Ridersguide-regel) wordt nu niet correct gescoord

### R3. Binaire refractie i.p.v. continu (genoemd in 3/4)
Mijn `blocked_swell_dir_min=350, blocked_swell_dir_max=30` is een harde knip. In werkelijkheid:
- 0° N: ~60% energie passeert pier
- 10° NNO: ~30%
- 20° NNO: ~10%
- Lange-periode swell refracteert beter rond pier dan korte

Dit triggert false-negatives op N-swell ALERT cases (CASE 3, CASE 11 in gap-analyse — de meest impactvolle cases).

### R4. Geen wave-age / spin-up modellering (genoemd in 3/4)
Open-Meteo's wave-model rapporteert quasi-stationaire equilibrium-respons op de wind. In werkelijkheid heeft een wave-veld inertie van uren — een ochtend-veld dat 0.5 → 1.3m groeit is *minder* surfbaar dan een zelfde-hoogte veld dat al 12 uur stabiel draait.

De opmerking "ochtend rustig, opbouwend" van de referentie-forecaster is impliciete kennis hiervan. Wetenschappelijke proxy: wave age = c_p/U10:
- c_p/U10 > 1.2 = matured swell, surfbaar
- 0.83 < c_p/U10 < 1.2 = wind-zee, marginal
- c_p/U10 < 0.83 = jonge wind-zee, pure chop

### R5. Geen real-time buoy-correctie (genoemd in 4/4)
Surfline doet hourly buoy assimilatie, Stormsurf vertrouwt primair op live buoys, de referentie-forecaster checkt elke ochtend IJG1 spectrum, academic top-1 is XGBoost op buoy-residuals. **Ik gebruik buoys nergens voor correctie.**
- Verwachte impact: 15-25% RMSE-reductie op de eerste 6-12u nowcast
- Data is gratis (RWS Waterinfo / DDAPI20)
- Bias decay: zwaarder gewicht op eerste uren, decay naar 1.0 over 24-48h

### R6. Geen synoptische context (genoemd in 2/4)
De referentie-forecaster kijkt naar drukkaarten, frontpassages. Mijn `pressure_msl` wordt opgehaald maar niet in scoring gebruikt. Drukgradiënt-derivative (`|dp/dt| > 1.5 hPa/uur` = frontpassage) zou veel automatisch detecteerbaar maken zonder extra API.

### R7. Deterministische output i.p.v. probabilistisch (genoemd in 3/4)
De referentie-forecaster zegt "kan zaterdag wat zijn" (uncertain), Surfline rapporteert ranges, ECMWF ensemble heeft P10/P90. Ik geef één getal. Hierdoor verliest het systeem **operationele beslissingswaarde** voor de gebruiker bij twijfelgevallen.

---

## 3. Improvement Catalog — 18 concrete verbeterpunten

Volgnummering grenzeloos doorlopend. Elke fix heeft: **What** | **Why (consensus van bronnen)** | **Data needed** | **Effort** | **Impact** | **Sprint #**.

### 3.1 Quick wins (Sprint 1: ~1 dag totaal, allemaal XS)

| # | Fix | Why | Data | Effort | Impact | Sprint |
|---|---|---|---|---|---|---|
| **1** | **T4 groundswell-through-windsea bonus opwaarderen +1 → +8/+12 pt** | Gap-analyse Gap 8 — meest impactful voor zeldzame events; CASE 4 (6-aug-2025 groundswell) gaat nu naar ALERT. Industry consensus: groundswell-by-windsea is *het* paradigma-voorbeeld voor alert-waardigheid. | Geen extra — bestaat al | XS (1u) | Hoog voor rare events | 1 |
| **2** | **Wave-age proxy** `cp/U10` als pre-filter | Pro-forecaster mechanism 6, academic top-3. Wind-sea met cp/U10 < 0.83 = pure chop. het "5s minimum ongeacht hoogte" van de referentie-forecaster is empirische proxy. | Tp en U10 al beschikbaar | XS (1u) | Hoog (filtert ochtend-spin-up correct) | 1 |
| **3** | **Wave energy flux** `P = 0.49·Hs²·Te` als size-metric | Pro-forecaster mechanism 4, academic 2.2, Surf-Forecast.com basis-formule. Combineert periode én hoogte in fysische eenheid; voorkomt dat 1.4m@4s windhash even hoog scoort als 0.9m@8s clean swell. | Hs en Te al beschikbaar | XS (2u) | Hoog (juiste relatieve weging) | 1 |
| **4** | **Wind-gust ratio** als instabiliteits-flag | Gap-analyse Gap 3. `gust/sustained > 1.5` = vlagerig (post-front, instabiel). het "vlagerig" van de referentie-forecaster wordt automatisch gedetecteerd. | `wind_gusts_10m` al opgehaald, ongebruikt | XS (1u) | Middel (2-3 cases) | 1 |
| **5** | **Mixed-sea detector** wave_dir vs swell_dir hoek > 30° | Gap-analyse Gap 5. Detecteert "rommelige" combo-seas. LLM kan dan "rommelig" zeggen in plaats van geforceerd één richting. | Open-Meteo Marine geeft beide | XS (1-2u) | Laag-middel (verbetert taalkwaliteit) | 1 |
| **6** | **Drukgradiënt-derivative** voor synoptische detectie | Gap-analyse Gap 7, pro-forecaster mechanism 1. `|dp/dt| > 1.5 hPa/uur` = front/trog. `pressure_msl` al opgehaald. | Geen extra | XS (2u) | Middel (auto-detect frontale shifts) | 1 |
| **7** | **Iribarren-number breaker-quality bonus** | Academic top-2 (⭐⭐⭐⭐⭐ rating). Voor Noordwijk ξ≈0.15 = spilling/mushy. Bij grotere swells stijgt ξ richting plunging = quality-bonus. | Beach slope (vast, ~0.02), Hs, Tp | XS (2u) | Middel-laag (quality modifier) | 1 |

### 3.2 Structurele verbeteringen (Sprint 2: ~3-4 dagen)

| # | Fix | Why | Data | Effort | Impact | Sprint |
|---|---|---|---|---|---|---|
| **8** | **Multi-model wind-spread confidence-penalty** | Gap-analyse Gap 1, pro-forecaster mechanism 1, ALLE rapporten consensus. Spread tussen knmi_seamless/ecmwf_ifs025/gfs_seamless > 5 kn of > 25° → confidence-penalty. Detecteert de woensdag-ochtend-onzekerheid automatisch. | Open-Meteo Forecast API met `models=...` | S (½ dag) | Hoog (4-6 cases verbeterd) | 2 |
| **9** | **Continue pier-refractie** vervangt binaire blocked sector | Gap-analyse Gap 4, pro-forecaster mechanism 9. Sigmoid-based op offset van swell-richting vs pier-shadow center, periode-afhankelijk. Voorkomt false-negative alerts op N-swell. | Pier-positie (vast), swell_dir, Tp | S (½ dag) | Hoog voor N-swell cases (2-3 cases, allemaal ALERT-waardig) | 2 |
| **10** | **Wave energy flux + period-quality weighting per partition** | Pro-forecaster cheat-sheet, Surfline Wave Energy concept. `score = (swell_E × swell_quality) + (wind_E × wind_quality)` met aparte quality-factoren per partition. Ridersguide-regel: secundaire goed-georiënteerde swell kan dominant matige verslaan. | Beide partities al beschikbaar | M (1 dag) | Hoog (combo-seas correct gescored) | 2 |
| **11** | **Tide-flank features** als rising/falling derivative | Pro-forecaster mechanism 7, industry P4. Niet alleen `tide_normalized` 0-1, ook `tide_velocity` (rising/falling), `time_to_next_high`, `time_to_next_low`. Sweet spot: mid-rising voor de meeste NL beach breaks. | RWS getij-data al beschikbaar | S (½ dag) | Hoog (windows preciezer) | 2 |
| **12** | **Diurnal wind-decay** rond zonsondergang | Pro-forecaster mechanism 8. Empirische regel: `als sunset_h − 2 ≤ uur ≤ sunset_h + 1 én cloud_cover < 50%: wind -= 2-3 kn`. Modelleert het "na 19:30 wind valt weg"-effect van de referentie-forecaster. | Sunset-tijd berekenbaar, cloud_cover al opgehaald | S (½ dag) | Middel (verbetert avond-window detectie) | 2 |
| **13** | **Hard size-cap met multiplicative aggregation** | Industry Gap 2 (Surfline + MSW consensus). Wind/tide/dir mogen nooit een 0.5m wave naar "epic" tillen. `total = min(size_proxy, max_per_size) × wind_factor × tide_factor` ipv puur additief. Versterkt huidige min_golf gate. | Geen extra | S (½ dag) | Middel-hoog (voorkomt edge-case false positives) | 2 |

### 3.3 Real-time correctie & data-infra (Sprint 3: ~3-5 dagen)

| # | Fix | Why | Data | Effort | Impact | Sprint |
|---|---|---|---|---|---|---|
| **14** | **RWS IJG1-boei real-time bias-correctie** | Gap-analyse Gap 9, pro-forecaster mechanism 2, industry P3, academic top-1. Vergelijk live boei-Hs met model-Hs voor laatste 3-6 uur, bereken `bias_factor`, pas toe op forecast met exponential decay. Wetenschappelijk gefundeerd (Surfline 30-40% error reductie). | RWS DDAPI20 endpoint | M (1 dag) | Hoog (alle nowcasts beter, 3-4 cases) | 3 |
| **15** | **Boei-spectrum history voor T1 swell-arrival** | Gap-analyse Gap 6. Functioneel niet geïmplementeerd. Schrijf periodiek A12+K13 spectrum naar `data/buoy_spectra_history.jsonl`. Detect peak-frequency shift naar lager + amplitude stijging → T1 trigger. Maakt CASE 5 detecteerbaar. | RWS DDAPI20 + storage discipline | M (1 dag) | Middel (alleen voor verre-storm cases, 2-3 cases) | 3 |
| **16** | **Boei-vs-model bias logger** voor lange-termijn learning | Voorbereiding op XGBoost (Sprint 4). Bij elke run log model_prediction én boei_observation per veld; later trainbaar. | Pipeline-aanpassing | S (½ dag) | Laag direct, hoog op termijn | 3 |
| **17** | **Probabilistische output met model-spread** | Gap-analyse Gap 10, pro-forecaster mechanism 12. P25-P75 range ipv single getal in LLM-input. Overlap met #8 (multi-model). LLM mag dan "1.0-1.4m" of "modellen nog onzeker" schrijven. | Multi-model uit #8 | S (½ dag) | Middel (verbetert vertrouwen + de referentie-forecaster-fit van taal) | 3 |

### 3.4 ML laag (Sprint 4: 2 weken, optioneel)

| # | Fix | Why | Data | Effort | Impact | Sprint |
|---|---|---|---|---|---|---|
| **18** | **XGBoost bias-correctie op Meetpost Noordwijk residuals** | Academic top-1 ⭐⭐⭐⭐⭐. Hybrid SWAN+XGBoost paper Dutch North Sea: 22% Hs-RMSE reductie, 25% direction-error reductie. Features: model Hs/Tp/dir, wind, tide, hour, season. Trainbaar op 6+ maanden data van Meetpost Noordwijk (RWS Matroos). | RWS Matroos historische boei-data (gratis, 6+ maanden nodig); `xgboost`, `pandas`, `scikit-learn` | L (2 weken) | Zeer hoog (15-25% RMSE-reductie aanhoudend) | 4 (post-MVP) |

### 3.5 Niet aanbevolen / accepted-loss

| Item | Waarom NIET |
|---|---|
| Echte 2D RWS spectrum-parsing | Data alleen als JPG-image, machine-unreadable. Open-Meteo's 2-partition decompositie haalt 80% van de winst voor 10% effort |
| Vlaamse Banken filter | Alleen relevant voor België/Zeeland spots, post-MVP voor multi-spot |
| Sandbank-specifieke kennis (Vaklodingen RWS) | Vereist 1+ jaar eigen waarneming + bank-survey integratie; lange aanlooptijd |
| LSTM / Transformer time-series forecasting | Academic onderzoek toont marginale winst boven XGBoost; vereist 1+ jaar data + GPU |
| Lokaal SWAN draaien | C/Fortran toolchain, dagen compute, nauwelijks winst vs ECWAM van Open-Meteo |
| CNN op surf-cam footage | Vereist labeled dataset (10k+ frames), GPU inferentie. Voor 1-spot systeem disproportioneel. |
| Klimatologische "deze week vs historie" (Pat Caldwell-stijl) | Mooi feature maar zonder geverifieerde Noordwijk-archief moeilijk. Post-MVP. |
| Crowd-sourced Telegram-bot ratings | Vereist user-base + bias-correctie per rater. Lange opbouw, niet bewezen voor 1-spot. |

---

## 4. Prioritization matrix — Impact vs Effort

```
                       │ XS effort     │ S effort        │ M effort       │ L effort
─────────────────────┼─────────────────┼───────────────────┼──────────────────┼─────────────
HIGH impact          │ #1 T4 bonus  │ #8 multi-model  │ #14 IJG1 bias │ #18 XGBoost
                       │ #2 wave-age   │   wind-spread     │   correction    │   bias correctie
                       │ #3 wave-energy│ #9 continue       │ #10 partition  │
                       │   flux         │   refractie       │   weighting     │
                       │                  │ #11 tide-flank    │                  │
                       │                  │ #13 hard size-cap │                  │
─────────────────────┼─────────────────┼───────────────────┼──────────────────┼─────────────
MEDIUM impact      │ #4 gust-ratio │ #6 drukgrad     │ #15 T1 detector│
                       │ #5 mixed-sea  │ #12 diurnal wind│                  │
                       │ #7 Iribarren │ #17 probabilistic│                  │
                       │                  │ #16 bias logger  │                  │
─────────────────────┴─────────────────┴───────────────────┴──────────────────┴─────────────
```

**Vuistregel: alles in HIGH/XS en HIGH/S eerst. Dat is Sprint 1+2 = max 5 dagen werk.**

---

## 5. Concrete 4-sprint roadmap

### Sprint 1 — Quick wins (1 dag werk)
**Doel:** systematisch alle "we hebben de data al maar gebruiken het niet" gaps dichten.

**Status: Done (commit 300b1e6).**

- [x] **#1** T4 groundswell bonus +1 → +8 pt (config + windowsanalyse extra check)
- [x] **#2** Wave-age filter (`cp/U10 < 0.83` → cap golf_score op 20% van max)
- [x] **#3** Wave energy flux als size-component (`P = 0.49·Hs²·Te`)
- [x] **#4** Wind-gust ratio penalty (`gust/sustained > 1.5` → −3 pt op wind_score)
- [x] **#5** Mixed-sea detector (`|wave_dir − swell_dir| > 30°` → flag + penalty)
- [x] **#6** Drukgradiënt-derivative (`|dp/dt| > 1.5 hPa/u` → "synoptische storing" flag)
- [x] **#7** Iribarren-number quality bonus

**Test-criterium:** woensdag-ochtend window krijgt nu wave-age penalty (Hs groeit van 0.5→1.3m in 5u = young wind-sea). Verwacht resultaat: morning-window valt onder longboard-threshold OF wordt expliciet als "spin-up fase" gemarkeerd.

**Acceptance check tegen van de referentie-forecaster 13 SMSes:** verwacht 4-6 cases verbeterd. Specifiek: CASE 1 (woensdag morgen wordt correct uitgesloten), CASE 4 (groundswell-event correct als ALERT), CASE 13 (vandaag correct flat).

### Sprint 2 — Structurele verbeteringen (3-4 dagen)
**Doel:** de twee grootste single-source-of-truth problemen oplossen (wind & refractie) en spectrum-decompositie correct doen.

**Status: Done (commit d78c7c3).**

- [x] **#8** Multi-model wind triangulatie via Open-Meteo `models=` parameter
- [x] **#9** Continue refractie-functie voor pier-blokkade (sigmoid blend)
- [x] **#10** Partition-aware scoring (swell × wind-sea apart wegen met quality-factoren)
- [x] **#11** Tide-flank features (velocity, time-to-turn, sweet-spot)
- [x] **#12** Diurnal wind-decay rond zonsondergang
- [x] **#13** Hard size-cap met multiplicatieve aggregation

**Test-criterium:** CASE 3 en CASE 11 (N-swell cases) genereren nu ALERT (waren false-negative). Combo-swell dagen worden correct als surfable gescored ook wanneer dominante Hs matig is.

**Acceptance:** ~7-8 cases binnen tolerantie van de referentie-forecaster (was ~5).

### Sprint 3 — Real-time correctie & infrastructuur (3-5 dagen)
**Doel:** boei-data echt benutten en lange-termijn learning voorbereiden.

**Status: Done (commit d6d67f7).**

- [x] **#14** RWS IJG1 boei bias-correctie voor eerste 6-12 uur forecast
- [x] **#15** Boei-spectrum history opslag + T1 swell-arrival detector
- [x] **#16** Forecast-vs-observation bias logger (voor ML voorbereiding)
- [x] **#17** Probabilistische output (model-spread ranges in LLM input)

**Test-criterium:** nowcast Hs-MAE daalt van ~0.35m naar ~0.25m. T1 alerts triggeren bij echte verre-storm-aankomst (validatie tegen historische SMSes 5 en 11).

### Sprint 4 — ML laag (2 weken, optioneel post-MVP)
**Doel:** Surfline-level bias correctie.

- [ ] **#18** XGBoost residual model getraind op Meetpost Noordwijk
- [ ] Time-series cross-validation (geen random shuffle!)
- [ ] Per-windrichting bias correction strata
- [ ] Verification framework met McNemar + Diebold-Mariano tests tegen de referentie-forecaster

**Test-criterium:** wetenschappelijk gevalideerde 20-25% RMSE reductie aanhoudend over 90+ dagen. HSS go/no-go classification > 0.60 vs de referentie-forecaster als baseline.

---

## 6. Verwacht eindresultaat na alle sprints

| Metric | Huidig (na laatste benchmark) | Na Sprint 1 | Na Sprint 2 | Na Sprint 3 | Na Sprint 4 |
|---|---|---|---|---|---|
| Cases binnen de referentie-forecaster-tolerantie | 5/13 | 7/13 | 8-9/13 | 10-11/13 | 11-12/13 |
| Hs MAE (m) | ~0.35 (ruw Open-Meteo) | ~0.32 | ~0.28 | ~0.25 | ~0.18-0.22 |
| Tp MAE (s) | ~1.5 | ~1.3 | ~1.0 | ~0.8 | ~0.6 |
| ALERT precision (false alerts) | onbekend | gelijk | beter (R3 fix) | beter | hoog |
| ALERT recall (gemist alerts) | matig (mist N-swell) | gelijk | veel beter | veel beter | uitstekend |
| Output-stijl (de referentie-forecaster-lijkenheid) | 70% | 75% | 80% | 85% | 85% |
| Probabilistische uncertainty | nee | nee | nee | ja | ja |
| Operationele kosten/maand | €3 (LLM) | €3 | €4 (multi-model) | €5 (boei polling) | €5 |

**Honest limits — wat geen enkele Sprint oplost:**

1. **Spot-specific sandbank kennis.** Vereist 1+ jaar eigen waarneming. het "Wijk werkt op X, Noordwijk op Y" van de referentie-forecaster blijft buiten bereik.
2. **KNMI menselijke guidance.** de referentie-forecaster leest een geredigeerde menselijke synthese die niet als API beschikbaar is. We benaderen dit via multi-model spread maar 100% replicatie is onmogelijk.
3. **Subjectieve voorkeur ("ochtend nooit, middag altijd").** Sommige picks van de referentie-forecaster zijn persoonlijke conventie. Zonder die mining van zijn historische SMSes blijft mijn output objectiever (= soms onverwachte morning-windows).

---

## 7. Architecture decision records (ADRs)

Belangrijke ontwerpkeuzes die uit het onderzoek volgen:

### ADR-1: Geen lokale SWAN, wel partition-aware ECWAM
Open-Meteo's ECWAM is goed genoeg op spotniveau. Lokale SWAN draaien levert 10-30% winst maar kost dagen compute en C/Fortran toolchain. **Beter: Sprint 1+2 fixes geven vergelijkbare winst voor 10% van de moeite.**

### ADR-2: Geen 2D directional spectra (te duur)
Surfline's "Swell Spectra" feature is hun premium-killer maar vereist directional buoy data of full 2D model output. Onze Open-Meteo geeft alleen geaggregeerde partities. **80% van de winst zit in partition-aware scoring met de twee bestaande partities.**

### ADR-3: ML als laatste stap, niet eerste
Volgorde: eerst fysisch correcte heuristieken (Sprint 1-3), dán ML bias-correctie (Sprint 4). Reden: zonder fysisch correcte features leert XGBoost de verkeerde patronen. Plus: 6 maanden boei-logging nodig voor goede training set.

### ADR-4: Multi-model > single-model, ook bij vergelijkbare bron
Open-Meteo's vier backends (ECWAM, MFWAM, GFS-Wave, ICON-Wave) overlappen sterk qua source code. Toch is hun spread informatief — verschillen komen uit verschillende grid-resoluties, verschillende wind-forcing, verschillende cycle-tijden. **Gebruik alle vier als pseudo-ensemble, kost geen extra API quota.**

### ADR-5: Per-spot bathymetrie eenmalig downloaden
EMODnet DTM tile rond Noordwijk eenmalig downloaden, lokaal opslaan. Beach slope (~0.02) wordt vaste constant in `LocationConfig`. Voor Iribarren-berekening voldoende. **Geen complexity van remote bathymetry-API tijdens runtime.**

### ADR-6: LLM blijft prozaisch laag, geen scoring laag
LLM mag NOOIT numeriek redeneren of cijfers verzinnen. Alle scoring in Python, LLM alleen narratief. Validator blijft strict op getalcitaties. **Geen "AI scoring" — wel "AI verteltrant".**

---

## 8. Risico's en mitigaties

| Risico | Mitigatie |
|---|---|
| Sprint 1 fixes leveren stapeling van penalty's → alle scores nul | Stage-gate test na elke fix: ten minste 2 cases van de referentie-forecaster moeten blijven scoren als surfable. Bij regressie: tune-down tot acceptabel. |
| Multi-model wind-fetch verviervoudigt API-calls | Open-Meteo expliciet ondersteund multi-model in één call; geen extra quota. Wel iets tragere response (~3 sec ipv 1). |
| RWS IJG1 boei valt uit / DDAPI20 breekt | Implementeer met graceful degradation: bij missing boei-data, val terug op model-only forecast met explicit confidence-decrement. |
| Continue refractie-functie overpaste op CASE 3/11 → false ALERT bij NNO swells | Validate tegen alle 13 cases; calibreer sigmoid curvature. Hardstop: bij swell direction exact 0°±5° (pier-shadow center), max 50% refractie. |
| XGBoost overfit op trainingsperiode | Time-series CV verplicht (geen random shuffle). Out-of-sample test op meest recent 20% van data. Verwerp model als test-RMSE > 80% van naive baseline. |
| LLM met probabilistische input gaat hallucineren | Strakke schema: LLM krijgt `hs_p25`, `hs_p50`, `hs_p75` apart velden. Prompt: "noem nooit een waarde buiten dit P25-P75 bereik." Validator strikt. |

---

## 9. Implementatie-volgorde van bestanden

Per sprint, welke bestanden worden geraakt:

### Sprint 1 (~7 commits)
- `src/scoring/hourly.py` — wave_age, wave_energy_flux, iribarren, gust_ratio, mixed_sea
- `src/scoring/deconstruct.py` — T4 bonus opwaardering
- `src/config.py` — drempels per nieuwe metric
- `tests/test_scoring.py` — uitbreiden met fixtures voor elke nieuwe feature
- `src/data/sources/open_meteo.py` — pressure_msl al opgehaald, geen wijziging

### Sprint 2 (~5 commits)
- `src/data/sources/open_meteo.py` — multi-model `models=` parameter
- `src/scoring/hourly.py` — score_swell_direction_bonus refactoren naar continue, tide-flank features
- `src/scoring/wind_interaction.py` (nieuw) — diurnal wind decay heuristic
- `src/data/models.py` — uitbreiden ScoreBreakdown met confidence, multi-model spread
- `src/llm/generator.py` — uncertainty velden in input
- `tests/test_scoring.py` — refractie continu, multi-model spread tests

### Sprint 3 (~4 commits)
- `src/data/sources/rws.py` — boei nowcast, spectrum history append
- `src/scoring/bias_correction.py` (nieuw) — boei-vs-model bias berekenen
- `data/buoy_spectra_history.jsonl` — gitignored data file
- `src/scoring/trigger_T1.py` (nieuw) — swell-arrival detector
- `tests/test_bias_correction.py` (nieuw)

### Sprint 4 (~3 commits + data verzameling)
- `src/ml/xgboost_postprocessor.py` (nieuw)
- `notebooks/train_bias_model.ipynb` — research notebook
- `data/training_set_meetpost_noordwijk.parquet` — gitignored, lokaal
- `src/scoring/hourly.py` — optionele postprocessor-call achter feature flag

---

## 10. Aanbevolen volgende stap

**Mijn aanbeveling: begin met Sprint 1.** Het is ~1 dag werk en pakt 7 van de 12 belangrijkste mechanismen die alle vier rapporten noemen. Concrete uitvoervolgorde:

1. **#3 wave energy flux** (eerste — herijkt size-metric, basis voor alle andere quality-mods)
2. **#2 wave-age proxy** (tweede — filtert ochtend-spin-up, direct CASE 1 verbetering)
3. **#1 T4 bonus opwaarderen** (derde — meest impactvolle ALERT-fix)
4. **#7 Iribarren bonus** (vierde — fysieke breaker-classificatie)
5. **#4 gust-ratio** + **#5 mixed-sea** + **#6 drukgradiënt** (parallel — kleine penalties die samen synoptische context geven)

Per fix: implementeer, run dry-run-benchmark op woensdag-20-mei case, vergelijk met de referentie-forecaster. Commit per fix met test-case bewijs.

Na Sprint 1: tweede benchmark-ronde. Als die laat zien dat ochtend-window nu correct uit de referentie-forecaster-pattern valt, **dan pas Sprint 2** beginnen. Anders eerst Sprint 1-fine-tuning.

---

## 11. Open vragen waar gebruiker-input nodig is

Voordat we Sprint 1 starten, drie vragen:

**Q1.** Welk aggregatie-model verkiezen we voor wave-energy + bestaande golf_score?
- (a) wave-energy vervangt volledig huidige height-based golf_score
- (b) wave-energy is een nieuw component met eigen gewicht (golf_score blijft basis)
- (c) wave-energy is een multiplier op huidige golf_score

Mijn voorstel: (c) — minst disruptief, behoudt backward compat tests.

**Q2.** Hoe streng moet de wave-age filter zijn?
- (a) Hard cap: `cp/U10 < 0.83` → golf_score = 0 (treat as flat)
- (b) Soft penalty: `cp/U10 < 0.83` → golf_score × 0.4 (zwaar gepenaliseerd maar niet nul)
- (c) Continue weighing: `quality_factor = min(1.0, cp/U10/1.2)` (lineair)

Mijn voorstel: (b) — fysisch eerlijk maar laat ruimte voor longboard bij borderline cases.

**Q3.** ALERT-mechanisme uitbreiden met "boei-confirmed only"?
- (a) Alle ALERTs direct verzenden (huidige)
- (b) ALERTs vereisen boei-bevestiging op IJG1 (extra latency 1u, hogere precision)
- (c) Twee-tier: "predicted ALERT" (model) + "confirmed ALERT" (boei) als aparte messages

Mijn voorstel: (c), maar pas in Sprint 3 implementeren.

---

## 12. Slotsamenvatting

Het onderzoek over 4 onafhankelijke sporen (industry, pro-forecaster, academic, gap-analyse de referentie-forecaster) convergeert op 7 systemische gaps die ALLEMAAL terugkomen, met **multi-model wind-triangulatie + partition-aware scoring + buoy bias-correctie** als de drie absolute hoofdthema's.

De goede news: 12 van de 18 voorgestelde verbeteringen kunnen met BESTAANDE data (Open-Meteo + RWS + huidige config). Geen extra API's, geen ML-infrastructuur, geen jaar wachten op trainingsdata.

De realistische ambitie: na Sprint 1+2 (max 5 dagen werk) zit het systeem op **10/13 cases binnen de referentie-forecaster-tolerantie**. Na Sprint 3 op **11/13**. Volledig de referentie-forecaster-niveau vereist Sprint 4 (XGBoost op 6+ maanden Meetpost Noordwijk data) — niet realistisch deze maand maar wel binnen 6 maanden bereikbaar.

De honest cap: zelfs perfect uitgevoerd haalt het systeem geen 13/13 omdat sommige picks van de referentie-forecaster subjectief zijn (lokale conventie, persoonlijke voorkeuren) of leunen op spot-specifieke bank-kennis die zonder jaren-lange waarneming niet replicabel is.

**Voor de gebruiker, kort en bondig:** Sprint 1 is laagdrempelig, lage risk, hoge bewezen impact. Sprint 2 vereist iets meer architecturale aanpassingen maar lost de twee belangrijkste structurele gaps op (single-model + binaire refractie). Sprint 3+4 zijn voor wanneer het systeem productie-rijp moet zijn met probabilistische uncertainty en ML.

---

*Einde masterplan. Geen code gewijzigd in deze ronde. Bereid om Sprint 1 te starten zodra de gebruiker akkoord geeft.*
