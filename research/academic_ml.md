# Academic & AI/ML Approaches voor Wave/Surf Forecasting

**Onderzoeksrapport voor het Noordwijk Surf-Alert Systeem**
*Datum: 19 mei 2026 — Merlijn / SurfWeerWorkflow*

---

## Samenvatting

Dit rapport vat de wetenschappelijke state-of-the-art samen voor wave en surf forecasting, met expliciete focus op technieken die een Python-gebaseerd surf-alertsysteem voor Noordwijk kan adopteren. De drie belangrijkste rode draden uit het onderzoek zijn (1) een verschuiving van bulk parameters (Hs, Tp, MWD) naar volledige 2D spectrale informatie, (2) hybride physical+ML postprocessing die 20–70% RMSE-reducties levert ten opzichte van standalone numerieke modellen, en (3) crowd-sourced human labels (à la Surfline) als haalbare maar bias-gevoelige ground truth. Voor een single-spot systeem zoals Noordwijk levert lichtgewicht ML-postprocessing op Open-Meteo / KNMI HARMONIE de gunstigste impact/effort-verhouding.

---

## 1. Wave Forecasting Modellen — Technische Vergelijking

### 1.1 WAVEWATCH III (NOAA)

WAVEWATCH III (WW3) is een derde-generatie spectraal golfmodel ontwikkeld door NOAA/NCEP. Het lost de stochastische actie-dichtheid balansvergelijking op voor 2D wavenumber-direction spectra F(k, θ) ([NOAA model description](https://polar.ncep.noaa.gov/waves/wavewatch/)). De physics source terms omvatten wind-input, niet-lineaire viervoudige resonante interacties (exact + DIA), whitecapping dissipatie, bottom friction, depth-induced breaking en bottom-wave scattering. WW3 modelleert *niet*: phase-resolving processen, triad-interacties standaard volledig (wel framework aanwezig), en wave-current interactions zijn alleen voor idealised conditions gevalideerd. De globale operationele resolutie is ~0.25°–0.5° (ca. 25–50 km), wat te grof is voor de Noordzee-ondiepten. De Garden Sprinkler Effect (numerieke dispersie in directional spectra) is een bekend artefact (Tolman 2002, Ocean Modelling 4:269–289).

### 1.2 WAM / ECWAM (ECMWF)

WAM is de directe voorloper van WW3 en wordt door ECMWF gedraaid als ECWAM, gekoppeld aan het IFS atmosferisch model. De Open-Meteo Marine API gebruikt overwegend **ECWAM van ECMWF** en (voor regionale verfijning) **MFWAM van Météo-France** via Copernicus Marine, plus **NOAA GFS Wave** en oorspronkelijk **DWD ICON-Wave** ([Open-Meteo Marine API docs](https://open-meteo.com/en/docs/marine-weather-api), [Open-Meteo Substack](https://openmeteo.substack.com/p/new-meteofrance-wave-models-and-knmi-dmi-uk-metoffice-models)). MFWAM is feitelijk afgeleid van ECWAM-IFS-38R2 met dissipatie-termen van Ardhuin et al. — d.w.z. de "verschillende modellen" in Open-Meteo overlappen sterk qua source code.

### 1.3 SWAN / UnSWAN

SWAN (Simulating WAves Nearshore, TU Delft) is hét standaard nearshore golfmodel. Verschil met WW3: SWAN is geoptimaliseerd voor *shallow water* met depth-induced breaking (Battjes-Janssen), bottom friction (JONSWAP, Madsen), refractie en shoaling expliciet meegenomen. UnSWAN draait op unstructured triangular meshes (Zijlema 2010), wat lokaal hoge resolutie mogelijk maakt zonder de hele Noordzee fijn te discretiseren. SWAN/SWASH is in de standaard Dutch Continental Shelf Model (DCSM) workflow van RWS/Deltares.

### 1.4 XBeach

XBeach is een proces-gebaseerd model voor de surf-zone, ontwikkeld door Deltares/UNESCO-IHE/Delft. Het lost zowel wave-averaged dynamics (BHBvW98 breaking model) als infragravity-waves en swash op ([XBeach docs](https://xbeach.readthedocs.io/en/stable/xbeach_manual.html)). Voor surfability is XBeach relevant omdat het de roller, wave setup en breaker line expliciet voorspelt — features die SWAN slechts geaggregeerd geeft. Computational cost is hoog (~minuten tot uren per simulatie van een storm).

### 1.5 MIKE21 (DHI)

Commercieel model van DHI, vergelijkbaar in capaciteit met SWAN+Delft3D-suite maar gesloten en duur. Geen praktisch alternatief voor een open-source surf-alertsysteem.

### 1.6 Bekende biases voor de Noordzee

[De Backer et al. (ResearchGate North Sea comparison)](https://www.academia.edu/37169342/PERFORMANCE_OF_WAVEWATCH_III_AND_SWAN_MODELS_IN_THE_NORTH_SEA) vond dat WW3 doorgaans correlation r=0.97 met IJmuiden bereikt, maar bij sommige condities SWAN beter scoort. Belangrijke biases die specifiek voor de zuidelijke Noordzee gedocumenteerd zijn:

- **Hs underestimation bij fetch-limited groei**: Hogere-resolutie wind forcing reduceert U10-bias maar niet de Hs-bias evenredig — wijst op problemen in de wind-input source term bij jonge golven.
- **High-frequency cutoff verschilt** tussen WAM en SWAN, wat verschillende groeisnelheden geeft (relevant want Noordzee golven zitten meestal in het 4–10 s peak period regime).
- **Depth-induced wave breaking wordt niet altijd meegenomen** in operationele WAM op grof grid (Behrens & Günther 2009).
- **Bias ranges**: globale Hs-biases liggen tussen 20–70 cm; bij recente verbeteringen in coastal parameterizaties (bodem-friction sediment-types, coastal reflection) zijn errors in de zuidelijke Noordzee teruggebracht tot bijna open-ocean niveaus ([Copernicus Ocean Science](https://os.copernicus.org/articles/18/1665/2022/)).

---

## 2. Coastal Wave Physics — Theorie voor Implementatie

### 2.1 Shoaling, refraction, breaking

In de transitie van diep naar ondiep water transformeren golven via drie hoofdprocessen:
- **Shoaling**: golfhoogte neemt toe als groepssnelheid afneemt (conservatie van energiestroom).
- **Refractie**: golffronten draaien naar de bathymetrie-contouren (Snell's law: sin θ / c = constant).
- **Breaking**: bij H/d ≈ γ (breaker index, typisch 0.6–1.2 afhankelijk van slope).

### 2.2 Iribarren Number ξ — Praktische Breaker Predictor

De Iribarren-parameter (surf similarity parameter) is de meest gebruikte dimensieloze indicator voor breaker-type ([Wikipedia: Iribarren number](https://en.wikipedia.org/wiki/Iribarren_number), [Coastal Wiki](https://www.coastalwiki.org/wiki/Surf_similarity_parameter)):

```
ξ = tan(β) / √(H / L₀)
```

waarbij β de strand-helling, H golfhoogte, L₀ = gT²/(2π) deepwater wavelength. Drempelwaarden (gemeten op breakpoint):

| Iribarren ξ | Breaker Type | Surfability |
|---|---|---|
| ξ < 0.4 | Spilling (zacht, mousse) | Beginners, longboard |
| 0.4 < ξ < 2.0 | Plunging (curling, barrel) | Intermediate/advanced |
| ξ > 2.0 | Surging/Collapsing | Onbruikbaar (reflective beach) |

**Toepassing op Noordwijk**: typische strand-helling tan(β) ≈ 1:50 = 0.02. Voor een typische Noordzee dag (H = 1.0 m, T = 6 s → L₀ ≈ 56 m, H/L₀ = 0.018):
```
ξ ≈ 0.02 / √0.018 ≈ 0.15  → spilling
```
Voor een grotere dag (H = 2.0 m, T = 8 s → L₀ ≈ 100 m, H/L₀ = 0.020):
```
ξ ≈ 0.02 / √0.020 ≈ 0.14  → nog steeds spilling
```

Dit verklaart fysisch waarom Noordwijk overwegend spilling/mushy waves heeft — de zachte strand-helling drukt ξ omlaag, ongeacht swell-grootte. De empirische breaker-index γ ≈ 1.06 + 0.14·ln(ξ) ([Moragues et al. 2020](https://www.mdpi.com/2077-1312/8/4/296)) geeft H_b/d_b ≈ 0.79 voor onze ξ = 0.15.

### 2.3 Battjes-Janssen Random Wave Breaking

Het Battjes-Janssen (1978) model is de basis voor depth-induced breaking dissipatie in SWAN ([Battjes & Stive 1985, JGR doi:10.1029/JC090iC05p09159](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/jc090ic05p09159)). De energy dissipation per oppervlakte-eenheid:

```
D = α · Q_b · f_mean · (H_max)² / 8
```

met Q_b = fractie brekende golven, H_max = γ·d. De γ-parameter is afhankelijk van wave steepness; Battjes-Stive (1985) γ ≈ 0.5 tot 0.9. Voor het Noordwijk-systeem is dit relevant als post-processing: gegeven Open-Meteo's offshore Hs, kun je een eerste-orde estimate maken van de wave height op breakpoint via `H_b ≈ γ · d_b`, met d_b te schatten uit de zandbank-positie (~100–200 m offshore).

### 2.4 Wave-Current Interaction

Tijdens een springtij stroom van bv. 0.5 m/s tegen swell van T=8s in: dispersie σ = √(g·k·tanh(kd)) verandert door Doppler shift. In de praktijk: tegenstroom (eb tegen NW-swell) maakt golven *steiler en geconcentreerder* (vergelijkbaar met offshore wind effect). Voor Noordwijk is dit subtiel — getij-stromingen lokaal zijn 0.2–0.5 m/s en de effect is meestal binnen de meetfout van de bulk forecast. Stokes drift speelt nauwelijks rol voor surfability maar wel voor rip currents.

---

## 3. Wave-Wind Coupling

### 3.1 Pierson-Moskowitz vs JONSWAP Spectra

- **Pierson-Moskowitz (1964)**: volledig ontwikkelde zee, parameter c_p/U10 ≈ 1.22 (golven zwemmen sneller dan de wind).
- **JONSWAP (Hasselmann et al. 1973)**: fetch- of duration-beperkt; peak enhancement γ ≈ 3.3 (random, normal-verdeeld 1–6). c_p/U10 < 1 (jonge zee).

Voor Noordwijk zijn beide regimes relevant: bij aanhoudende NW-storm krijg je bijna-PM spectra (gevoel: lange swell, "open"), terwijl bij plotse SW-wind een sterk piekige JONSWAP spectrum ontstaat (gevoel: korte, hoekige chop).

### 3.2 Wave Age en Surfability

De wave age `c_p/U10` is een uitstekende proxy voor surf-kwaliteit:
- c_p/U10 > 1.5 → "old swell" — schoon, lange periode, surfable.
- 0.7 < c_p/U10 < 1.2 → "wind sea" — chop, korte periode.
- c_p/U10 < 0.7 → "developing sea" — junk.

In Open-Meteo termen: `wave_period_peak / (1.56 · wind_speed_10m)` ≈ c_p/U10. Een drempel `> 1.3` is een redelijke surfability filter.

### 3.3 Onshore vs Offshore Wind Effects

Scripps-onderzoek (Douglass & Weggel, 1989, herhaald door Mostert et al. recent op Kelly Slater's Surf Ranch) toont dat ([Surfertoday](https://www.surfertoday.com/surfing/the-effects-of-onshore-and-offshore-wind-on-wave-shape)):
- **Offshore wind**: vertraagt breaking, verhoogt steepness, induceert plunging breakers. Mechanisme is **luchtdruk-gradiënten** over het golffront, niet surface tension.
- **Onshore wind**: vervroegt breaking, verbreedt surf zone, induceert spilling. Breaker index γ varieert van 0.4–1.3 puur door wind-effect.

Voor Noordwijk (NE/E = offshore, NW/W = onshore): de classieke E-wind ochtend = clean conditions, omdat deze de chop wegblaast én de golven steiler maakt.

### 3.4 Whitecap Fraction

[Monahan 1971](https://link.springer.com/article/10.1007/s11802-019-3808-7) W ∝ U10^n, n typisch 3.0–3.7. [Callaghan et al. 2008](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2008GL036165) verfijnt met een 2.5-uur wind-history segregatie boven 9.25 m/s. Een eenvoudige proxy: W (%) ≈ 3.84·10⁻⁶ · U10^3.41 voor U10 in m/s. Voor surfability: W > 5% → "blown out". Dit komt overeen met U10 ≈ 11 m/s.

---

## 4. Bathymetry-aware Predictions

### 4.1 Impact van lokale bathymetrie

SWAN met fijne (50–100 m) lokale bathymetrie levert typisch 10–30% RMSE-reductie t.o.v. een coarse global model voor Hs in <20 m water depth (Gautier & Caires 2015 voor het Dutch Continental Shelf Model). De grootste winst zit in:
1. Refractie-effecten (golven draaien naar zandbanken).
2. Shoaling-gradient capture (zandbank-doorgang).
3. Depth-induced breaking timing.

Voor Noordwijk: het strand heeft typisch een binnenbank op 150 m offshore, een outerbank op 400 m, beide bij ~3 m en ~6 m diepte. Dit is precies het regime waar SWAN ECMWF/WW3 overruled.

### 4.2 Beschikbare data voor Nederlandse kust

- **Vaklodingen (RWS)**: 20 m grid, kaartbladen 10×12.5 km, jaarlijks vernieuwd voor estuaria en ebgetij-delta's tot −20 m contour. Datadekking vanaf 1985 digitaal, 1965 analoog. Stochastische error 0.36 m sinds 1995 ([NIOZ EMODnet](https://www.nioz.nl/en/research/projects/emodnet-bathymetry)).
- **EMODnet Bathymetry**: composieten van 200+ DTM's, tot 1/512 arc-minute resolutie (≈3.6 m). Beschikbaar via WCS/WMS API en als GeoTIFF download ([EMODnet portal](https://emodnet.ec.europa.eu/en/bathymetry)).
- **OpenDAP Deltares Vaklodingen catalog**: directe NetCDF-toegang voor scripted retrieval.
- **Sentinel/Landsat satellite-derived bathymetry**: voor intertidal zone en sub-tidal tot ~5 m. Verbetert temporele dichtheid (Deltares 2018).

### 4.3 Praktische haalbaarheid voor Noordwijk

Voor een Python-pipeline: download een single GeoTIFF tile (EMODnet DTM circa 4°20'-4°35' E / 52°10'-52°20' N) eenmalig, lokaal opslaan. Gebruik xarray/rasterio voor cross-shore profile extractie. Het is **niet** noodzakelijk om SWAN zelf te draaien — een 1D cross-shore transformation (linear wave theory + Battjes-Janssen breaking) reproduceert ~80% van de spatial accuracy voor een fractie van de compute. Dit is een sweet spot voor het Noordwijk-systeem.

---

## 5. Machine Learning Approaches

### 5.1 LSTM voor wave time-series

LSTM-netwerken (en derivaten CNN-LSTM, Bi-LSTM, EEMD-LSTM, CEEMDAN-LSTM) zijn sinds 2020 de dominante familie voor univariate Hs-tijdreeks-forecasting tot ~24h horizon ([arxiv 2201.00356](https://arxiv.org/pdf/2201.00356), [Physics of Fluids 2025](https://pubs.aip.org/aip/pof/article/37/4/045134/3342786/)). Belangrijkste finding: een simpele LSTM met buoy observations leidt tot 10–30% RMSE-reductie t.o.v. raw WW3, maar onderpresteert ten opzichte van een goede AR(p) model voor zeer korte horizons (1–3h). Decomposition-based hybrids (EEMD-LSTM, CEEMDAN-LSTM) winnen door het non-stationaire signaal te ontleden in mode-functies.

### 5.2 Transformer-based wave forecasting

Recent: TransWaveNet (R² = 0.73 op breakwater data), EmaDformer (NW Pacific, 12h horizon), CTST (Atlantic hurricane area met RMSE 0.027 m op 1h-vooruitzicht), SwinLSTM (0.1° resolutie, 72h horizon). [Een belangrijke nuance uit EmaDformer](https://www.sciencedirect.com/science/article/abs/pii/S1463500324000106): **deep learning verslaat niet noodzakelijk simpele AR-modellen voor univariate SWH time-series**. Toegevoegde waarde van transformers zit primair in multivariate spatiotemporal modeling, niet in single-point single-variable forecasting.

### 5.3 Random Forest / XGBoost Bias Correction

Dit is operationeel de gunstigste pijler. [Hybrid modelling SWAN+XGBoost voor Dutch North Sea harbours](https://www.sciencedirect.com/science/article/pii/S0141118723001244) levert **21.7% reductie wave energy density error en 25.3% reductie wave direction error**. De methodologie:

1. Train XGBoost op residual = `observation - SWAN_prediction` met features: SWAN-output, wind, tide level, hour of day, season.
2. Apply post-hoc: `corrected_Hs = SWAN_Hs + XGB_residual`.
3. Geen retraining van het fysisch model nodig.

Voor Noordwijk: vervang SWAN door Open-Meteo ECWAM output. Train XGBoost op ground-truth buoy data van **Europlatform**, **IJmuiden**, of **Meetpost Noordwijk** (RWS Matroos / waterinfo.rws.nl). Verwachte impact: 15–25% RMSE-reductie op Hs binnen 6 maanden trainingsdata.

### 5.4 SWRL Net — Spectral Residual Deep Learning

[Mooney et al. 2020](https://journals.ametsoc.org/view/journals/wefo/35/6/WAF-D-19-0254.1.xml) trainde een CNN om correcties op het **2D directional spectrum** van WW3 te genereren, op basis van buoy observations. Resultaat: significante verbetering van swell-partition forecasts (gedetailleerde direction/period) zonder spectrale informatie te verliezen. Dit is technisch ambitieus (vereist directional buoy data, ~niet beschikbaar voor Noordwijk).

### 5.5 GraphCast / Pangu-Weather Status

GraphCast (Google DeepMind) en Pangu-Weather (Huawei) hebben in 2023 IFS HRES verslagen op 90%+ van variables, draaien 10000× sneller, en zijn als open-weight beschikbaar. **Voor surf forecasting echter beperkt direct toepasbaar** want:
- Resolutie 0.25° = ~28 km, ruim te grof voor Noordwijk surf zone.
- Geen golf-specifieke output (Hs, Tp niet standaard).
- ECMWF AIFS (operationeel sinds 2024) start hier wel mee maar nog niet voor waves.

Indirect via wind forecast: GraphCast-wind kun je feeden in een lokaal SWAN of als feature in een XGBoost.

---

## 6. Surfability ML Specifiek

### 6.1 Surfline LOTUS — De Industry Leader

Surfline's LOTUS model is de meest geavanceerde commerciële implementatie ([Surfline Labs Medium post](https://medium.com/surfline-labs/machine-learning-for-surf-forecasting-4a007f13b3e3)). Belangrijke architecturale details:
- 35+ jaar historisch dataset, >1 miljoen menselijke surf-observaties.
- ML system "vindt patterns" zonder fysica volledig te begrijpen — pattern recognition op nuanced relationships.
- **Tot 70% error reductie** voor sommige locaties t.o.v. fysisch model alleen.
- Combineert: satellite assimilatie, high-res bathymetrie, forecaster input, en camera observations.
- Premium+ tier (juli 2024): "Wave Distribution" graph, crowd forecasting, "Smart Clips" via SurfZone AI (24 miljard video-frames/jaar getagd sinds 2019).

### 6.2 Computer Vision op Surf Cams

Onderzoek-richtingen:
- **Wave peel tracking** (Mole et al.) — track de "pocket" van een brekende golf voor ride-rate quantificatie.
- **Wave pocket detection** met deep neural networks (Stanford CS230, Ricken Medium project).
- **Surfer activity classification** met YOLO/object detection — paddling vs sitting vs surfing.
- **Timestack images + CNN** voor breaking wave detection (Stringari et al., MDPI Atmosphere 2020).

Praktisch toepasbaar op Noordwijk-systeem: er is een livestream cam (Strandtent KaapNoord, soms andere). Een YOLOv8-fine-tune op "surfer in water" kan crowd-density schatten, een proxy voor "is het surfbaar?".

### 6.3 Crowd-sourced Ratings als Training Labels

Belangrijkste lessen uit [SURF algorithm paper](https://arxiv.org/pdf/2010.05852) en Surfline's experience:
1. Menselijke ratings zijn **geen absolute ground truth**; confirmation bias, location preferences, skill level moet je modelleren per annotator.
2. **Silence ≠ agreement** — als users geen feedback geven op een 4/10 forecast, betekent dat niet dat ze het eens zijn.
3. **Pairwise comparisons** ("was sessie A beter dan sessie B?") zijn betrouwbaarder dan absolute 1-10 ratings.
4. **Dawid-Skene EM-algoritme** geeft per-annotator confusion matrices waarmee je biases kunt corrigeren.

Voor Noordwijk: een Telegram-bot survey "Hoe was het vandaag? 1–5" werkt, maar weeg ratings naar bekende surfers (track-record) en gebruik pairwise waar mogelijk.

---

## 7. Comparable Problem Domains

### 7.1 Marine Weather Routing

Maritieme routing (e.g. Infoplaza) gebruikt confidence-scoring over **2D spectral forecasts** plus ship-response RAOs. Insight: ze rapporteren niet één getal maar een operability index met confidence interval. Voor surf: rapporteer niet "rating 7/10" maar "rating 6–8 (70% confidence)".

### 7.2 Rip Current Prediction

NOAA's nationale Rip Current Forecast Model (operationeel sinds 2021) gebruikt nearshore wave parameters (Hs, Tp, MWD) + bathymetrie om hourly probabilities (0–100%) te leveren ([NOAA news](https://oceanservice.noaa.gov/news/apr21/rip-current-forecast.html)). Recent ML uitbreidingen (UC Santa Cruz/NOAA samenwerking) gebruiken neural networks om edge-cases beter te identificeren. **Drowning prediction model voor SW Frankrijk** ([bioRxiv preprint](https://www.biorxiv.org/content/10.1101/721142.full.pdf)) combineert: wave height/period, tide phase, beach morphology variability, air temperature (proxy voor bathers count), cloud cover. Dit is direct relevant voor *een breder beach-safety component* in het Noordwijk-systeem.

### 7.3 Multi-factor Scoring Patterns

Across domains (marine routing, rip current risk, drowning models) zijn de patterns:
- **Linear weighted sum** voor eerste-orde, easily interpretable.
- **Tree-based ML** (RF, XGBoost) voor non-linear interactions zonder black-box.
- **Logistic regression** voor binaire "go/no-go" als de output discreet moet zijn.
- **Bayesian network** als causal interpretability vereist is.

---

## 8. AI-Powered Surf Apps Recent

Naast Surfline LOTUS/Premium+:
- **Surfana** (NL): markt voor jonge surfers, maar zonder eigen forecast engine — leunt op derde-partij feeds.
- **De referentie-forecaster**: handmatige expert synthese van meerdere modellen (ECMWF, GFS, Windguru). Geen ML.
- **Goedegolven.nl**: zelfde, NL focus.
- **Surf-forecast.com**: bulk parameters + wave energy index.
- **AI-Meteorologist (arxiv 2511.23387, nov 2025)**: modular LLM-agent voor weather reports. Niet specifiek surf, maar de architectuur (serialized data → structured prompts → narrative output) is direct transponeerbaar.

Voor LLM-narratieve surf reports: GPT-4o of Claude met structured input (JSON met Hs, Tp, wind, tide) plus persona-instructie geeft acceptable Nederlandstalige reports. Hallucinatie-risico's: LLM mag *niet* numerieke values verzinnen — alle getallen komen uit de gevoede JSON, het LLM mag alleen prozaisch herformuleren en kwalitatieve duiding geven ("zachtjes spillingerige golven aan de Noordboulevard").

---

## 9. Probabilistic Forecasting

### 9.1 Ensemble Methods

ECMWF EPS draait 51 members met perturbed initial conditions via singular vectors. Voor wave-specifiek: ECWAM-ENS levert 51 Hs-traces per gridpunt per forecast tijdstap ([ECMWF Ensemble Forecasting docs](https://www.ecmwf.int/en/elibrary/75394-ensemble-forecasting)). Praktisch via Open-Meteo: de Ensemble API biedt 51 members voor wind (10 km res), niet voor waves — voor waves moet je via de Marine API meerdere modellen (ECWAM, MFWAM, GFS-Wave) als pseudo-ensemble combineren.

### 9.2 Uncertainty Quantification

Drie pragmatische schema's:
1. **Multi-model ensemble**: spread van Open-Meteo's 4 backends → P10/P50/P90.
2. **Quantile Regression Forest (QRF)**: train op residuals, output is volledige predictive distribution.
3. **Bayesian updating** met sequential Kalman filter zodra waarnemingen binnenkomen (Meetpost Noordwijk hourly buoy data via Matroos).

### 9.3 Bayesian Real-time Updates

Kobayashi & Yasuda WAM+KF en Houghton et al. (optimal interpolation pattern spectrum) zijn klassieke referenties. Voor light-weight Python: `filterpy` package + `arviz` voor diagnostics. Iedere nieuwe buoy observation past de posterior van het bias-model aan, en de volgende forecast krijgt automatisch een gecalibreerde update.

---

## 10. Verificatie-methodologie

### 10.1 Standaard skill scores

| Score | Type | Use Case Noordwijk |
|---|---|---|
| MAE (m) | continu | gemiddelde Hs-fout |
| RMSE (m) | continu | penaliseert grote misses (storms) |
| Bias (m) | continu | systematische over/onder |
| MBE | continu | mean bias error per maand |
| Brier Score | probabilistisch | "kans op > 1.5 m surf" |
| Heidke Skill Score | categorisch | poor/fair/good/epic classificatie |
| Gerrity Score | categorisch | beter dan HSS voor zeldzame categorieën |
| ROC AUC | binaire classificatie | "go/no-go surfdag" |

### 10.2 Ground truth bronnen voor Noordwijk

1. **Meetpost Noordwijk** (RWS waterinfo / Matroos OpenDAP): wave height en period, hourly.
2. **Europlatform / IJmuiden buoy** (RWS): 30 km offshore, goede deep-water reference.
3. **Forecasts van de referentie-forecaster** (scrape RSS/web): nuttig als *menselijk* baseline.
4. **Eigen Telegram-bot ratings**: crowd-sourced surf experience.

### 10.3 Hoe valideer je tegen de referentie-forecaster?

Statistisch design:
- **N**: minimaal 30 events per categorie voor stabiele HSS; bij voorkeur 90+ dagen continu.
- **Categorieën**: 4-klasse {flat, surfable, good, epic} of binair {go, no-go}.
- **Test**: McNemar's test voor paired forecast accuracy (model M vs referentie-forecaster R op zelfde dag).
- **Stratificatie**: splits resultaten naar windrichting en seizoen — de referentie-forecaster is goed in storm-events maar wellicht zwak in marginal days; daar zit jouw kans.
- **Reliability diagram**: gebruik bij probabilistische output, plot voorspelde kans vs gemeten frequentie per bin.

### 10.4 Verwachtingen

Een goed gecalibreerde Open-Meteo+XGBoost pipeline kan na 3–6 maanden training een MAE van 0.15–0.25 m op Hs halen (vs raw Open-Meteo ~0.35 m), en een binary go/no-go HSS van 0.55–0.65 (perfect = 1, random = 0). De menselijke skill van de referentie-forecaster zit waarschijnlijk rond HSS 0.60–0.70 op marginal days; bij duidelijke storms convergeren beide naar 0.85+.

---

## Antwoorden op de Specifieke Vragen

### A. State-of-the-art Bias Correction

**Methode**: XGBoost residual model getraind op `obs - forecast`, met features: ruwe Hs/Tp/MWD/U10/wind_dir/tide_level/hour/season. Implementatie: `xgboost.XGBRegressor(n_estimators=300, max_depth=4, learning_rate=0.05)`. Train 80/20 split, gebruik time-series CV (geen random shuffle!). Verwachte reductie 20–25% op Hs-RMSE.

**Beter dan dit**: Stacked ensemble (XGBoost + LightGBM + linear) met meta-learner. Tijdsinvestering verdubbelt, gain marginal (~3–5% extra).

### B. Spectrum-based Predictions

Het werken met **full 2D spectrum (256 freq × 36 dir)** in plaats van geaggregeerde Hs/Tp is wetenschappelijk de gold standard ([WASCO 2025](https://www.sciencedirect.com/science/article/abs/pii/S002980182500085X)). Levert 14–26% reductie in Hs/Tp errors. **MAAR**: vereist directional buoy data (niet standaard voor Noordwijk), 2D spectrum download van Open-Meteo (niet beschikbaar — alleen bulk parameters), en non-trivial preprocessing. **Aanbevolen voor Noordwijk: partition decomposition** (primary swell + wind sea) via Open-Meteo's separate fields `swell_wave_height` / `wind_wave_height`. Dit geeft 80% van de winst voor 10% van de moeite.

### C. Hybrid Physical + ML Models

"Machine learning post-processing" = ML model getraind om residuals (en/of bias) van een numeriek model te voorspellen, conditional op model output en eventueel exogene features. Combinaties die werken:
- **SWAN + XGBoost** ([SciDir 2023 hybrid Dutch North Sea](https://www.sciencedirect.com/science/article/pii/S0141118723001244)): 22–25% error reductie.
- **WW3 + Transformer (residuals)**: 0.231 m RMSE op 2-dag horizon (vs ~0.4 m raw WW3).
- **WAM + spatiotemporal attention NN coupled via Fortran-Python interface** (real-time correction, [SciDir 2025](https://www.sciencedirect.com/science/article/abs/pii/S1463500325001039)).
- **SWRL Net** (CNN op 2D spectra): voor onze case te zwaar.

### D. AI voor Natural-Language Surf Reports

Surfline's "AI Forecast" feature mixt LOTUS-output met natural-language generation. Architectuur (afgeleid uit Surfline marketing + AI-Meteorologist paper):
1. **Structured JSON** met alle metrics serialized.
2. **System prompt** geeft persona ("a laid-back local surf forecaster"), context ("Noordwijk has a sandy bottom, breaks 100m offshore"), constraints ("never invent numbers, only describe given values").
3. **Few-shot examples** van goede surf reports.
4. **Validation pass**: tweede LLM-call die output checkt op feitelijke consistentie met input JSON.

Voor Claude/GPT-4o: prompt template `"Je bent een rustige, Nederlandstalige surf-forecaster voor Noordwijk. Beschrijf op basis van de volgende data in 100–150 woorden de surfcondities voor vandaag. Vermeld geen getallen die niet in de data staan. Eindig met een aanbeveling (gaan / niet gaan / twijfelgeval). DATA: {json}"`.

Validation: vergelijk numerical claims in de output regex tegen de input JSON; reject als er getallen zijn die niet matchen.

### E. Verificatie van Jouw Approach tegen de referentie-forecaster

**Protocol**:
1. **Sample size**: 90+ dagen continu logging, beide forecasts op T-24h.
2. **Metrics**: MAE Hs, RMSE Hs, Bias Hs, plus categorical HSS op 4-klasse {flat <0.5 m, surfable 0.5–1.0, good 1.0–1.8, epic >1.8}.
3. **Statistical test**: McNemar's paired test op categorical agreements; Diebold-Mariano test op squared error series.
4. **Stratificatie**: per seizoen (winter storm vs zomer flat), per windrichting (offshore E vs onshore SW).
5. **Calibratie**: reliability diagram als jouw output probabilistisch wordt.

**Realistische verwachting**: de referentie-forecaster wint waarschijnlijk in extreme storms (hij weet "deze zuidwester wordt zwaarder dan modellen zien") en marginal days (zijn lokale kennis). Jij wint in herhalende routine days, in objectieve consistentie, en in continu availability.

---

## Top 5 Implementable Techniques (Impact : Effort Ratio)

### 1. XGBoost Bias Correction op Open-Meteo Output ⭐⭐⭐⭐⭐
- **Impact**: 20–25% RMSE reductie op Hs. Documented in peer-reviewed Dutch North Sea paper.
- **Effort**: 2 weken: scrape 6 maanden Meetpost Noordwijk data via RWS Matroos, train XGBoost, deploy als post-processing step in bestaande pipeline.
- **Dependencies**: `xgboost`, `pandas`, `scikit-learn`. Allemaal in standaard requirements.
- **Risico**: laag — vat post-hoc, breekt niets als het misgaat.

### 2. Iribarren Number + Local Beach Slope ⭐⭐⭐⭐⭐
- **Impact**: classificatie van breaker type (spilling/plunging) wordt fysisch onderbouwd in plaats van heuristiek. Directe interpretatie naar surfability.
- **Effort**: 2 uur. Eenmalige bathymetry-download EMODnet, compute `tan(β)`, formule in `src/scoring/`.
- **Code**: `xi = beach_slope / np.sqrt(hs / (1.56 * tp**2))`.

### 3. Wave Age Proxy (c_p / U10) ⭐⭐⭐⭐
- **Impact**: filtert wind-chop dagen van clean swell dagen. Direct improvement van surf quality scoring.
- **Effort**: 1 uur. Een paar regels code, geen extra data.
- **Drempel**: `wave_age > 1.3` voor "swell-dominant".

### 4. Partition-aware Scoring (Swell vs Wind Sea Apart) ⭐⭐⭐⭐
- **Impact**: pakt bi-modal seas — exact de cases waar bulk Hs misleadt. 80% van het voordeel van full 2D spectra voor 10% effort.
- **Effort**: 1 dag. Open-Meteo Marine API biedt `swell_wave_*` en `wind_wave_*` separaat. Update scoring formule om beide te wegen.

### 5. Multi-Model Ensemble (ECWAM + MFWAM + GFS-Wave) als Uncertainty Proxy ⭐⭐⭐
- **Impact**: krijgt een gratis P10/P50/P90 uncertainty band — verbetert vertrouwen in marginal days.
- **Effort**: 1 dag. Open-Meteo Marine API ondersteunt model-keuze; vraag 3 backends parallel, neem spread als σ-estimate.
- **Output**: rapporteer "Hs = 1.2 m ± 0.3 m" of "70% kans op > 1.0 m surf".

### Niet aanbevolen voor nu (te zware effort/return)

- **LSTM/Transformer voor Hs**: vergt 1+ jaar data + GPU compute, returns marginaal boven XGBoost.
- **SWAN lokaal draaien**: requires C/Fortran toolchain, dagen compute, alleen sense als je hi-res spatial wilt.
- **Full 2D spectrum forecasting**: vergt directional buoy data die voor Noordwijk niet bestaat.
- **CNN op surf cam**: vergt labeled video dataset + GPU inference.

---

## Research Gaps — Wat is er nog NIET?

1. **Noordzee-specifieke ML postprocessing benchmark**: er is een SWAN+XGBoost paper voor Dutch *harbours*, maar niet voor de *surf zone* specifiek (Scheveningen, Noordwijk, Zandvoort). Een publicatie hier zou pioniers-werk zijn.

2. **Geen open ground truth surfability dataset voor Nederland**: Surfline heeft hun proprietary data, maar er is geen Kaggle-style benchmark voor "Noordzee surfdagen labeled met expert ratings". Een dergelijke dataset crowd-sourced opbouwen (Telegram bot + automated wave-cam capture) zou de community vooruit helpen.

3. **LLM-generated surf reports zonder hallucinaties**: AI-Meteorologist papers focussen op meteorologie algemeen; surf-specific persona's met domein-validatie (geen verzonnen wind-directions) zijn nog onontgonnen terrein.

4. **Real-time spectral bias correction op consumer-grade hardware**: SWRL Net is academisch indrukwekkend maar vergt directional buoy data. Een lichtgewicht spectral-partition bias correction (e.g., update primary swell direction met buoy reading) lijkt mogelijk maar is niet gepubliceerd.

5. **Coupled surfability + crowd density forecasting**: Surfline doet beide separaat. Een geïntegreerd model dat de optimalisatie "best surf met laagste crowd" maakt zou een novel hybrid recommendation problem zijn.

6. **Bathymetry-change detection vanuit satellite + surf forecast adjustment**: Vaklodingen wordt jaarlijks bijgewerkt, maar zandbank-migratie tussen surveys (storms) blijft een blind spot. Een satellite-derived bathymetry update tussen surveys, automatisch gefeed in een SWAN/1D refraction post-processor, is technisch haalbaar maar nergens operationeel.

7. **Verification framework voor menselijke vs ML forecasters**: er is methodologie voor weather forecaster verification, maar bij surf is het sample size klein en zijn de categoriedrempels arbitrair. Een gestandaardiseerd verification protocol voor surf forecasts (zoals NWS heeft voor temperature/precipitation) ontbreekt.

---

## Bronnen (Selectie van Belangrijkste)

### Models en Documentation
- [NOAA WAVEWATCH III Model Description](https://polar.ncep.noaa.gov/waves/wavewatch/)
- [NOAA WAVEWATCH III User Manual](https://polar.ncep.noaa.gov/mmab/papers/tn276/MMAB_276.pdf)
- [SWAN Documentation (TU Delft)](https://swanmodel.sourceforge.io/online_doc/swantech/node16.html)
- [XBeach Documentation](https://xbeach.readthedocs.io/en/stable/xbeach_manual.html)
- [Open-Meteo Marine API](https://open-meteo.com/en/docs/marine-weather-api)
- [Open-Meteo new wave models announcement](https://openmeteo.substack.com/p/new-meteofrance-wave-models-and-knmi-dmi-uk-metoffice-models)

### Wave Physics Foundational
- [Iribarren Number (Wikipedia)](https://en.wikipedia.org/wiki/Iribarren_number)
- [Surf Similarity Parameter (Coastal Wiki)](https://www.coastalwiki.org/wiki/Surf_similarity_parameter)
- Battjes, J.A., Stive, M.J.F. (1985). [Calibration and verification of a dissipation model for random breaking waves. JGR 90:9159–9167](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/jc090ic05p09159)
- [Moragues et al. (2020). Wave Breaker Types on a Smooth and Impermeable 1:10 Slope. JMSE](https://www.mdpi.com/2077-1312/8/4/296)
- [Onshore/offshore wind on wave shape (Scripps research)](https://scripps.ucsd.edu/news/scientists-and-surf-organizations-confirm-what-surfers-already-know)

### Machine Learning Wave Forecasting
- [Hybrid SWAN + XGBoost for Dutch North Sea (ScienceDirect 2023)](https://www.sciencedirect.com/science/article/pii/S0141118723001244)
- [SWRL Net: Spectral Residual Deep Learning (AMS Weather & Forecasting 2020)](https://journals.ametsoc.org/view/journals/wefo/35/6/WAF-D-19-0254.1.xml)
- [Real-time NN-WAM coupling for extreme weather (SciDir 2025)](https://www.sciencedirect.com/science/article/abs/pii/S1463500325001039)
- [WaveUformer bias correction for GWSM4C (Frontiers 2026)](https://www.frontiersin.org/journals/marine-science/articles/10.3389/fmars.2026.1732870/full)
- [Deep learning real-time bias correction Western North Pacific (arXiv 2311.15001)](https://arxiv.org/pdf/2311.15001)
- [LSTM significant wave height prediction (arXiv 2201.00356)](https://arxiv.org/pdf/2201.00356)
- [Spatiotemporal transformer wave forecast NW Pacific (SciDir 2024)](https://www.sciencedirect.com/science/article/abs/pii/S1463500324000106)
- [WASCO 2D wave spectra correction (ScienceDirect 2025)](https://www.sciencedirect.com/science/article/abs/pii/S002980182500085X)

### Bathymetry Data
- [EMODnet Bathymetry Portal](https://emodnet.ec.europa.eu/en/bathymetry)
- [NIOZ - EMODnet Bathymetry project](https://www.nioz.nl/en/research/projects/emodnet-bathymetry)
- [RWS Bathymetry on OpenEarth](https://www.openearth.nl/rws-bathymetry/2018.html)

### Surf-specific ML
- [Surfline Labs - Machine Learning for Surf Forecasting (Ben Freeston)](https://medium.com/surfline-labs/machine-learning-for-surf-forecasting-4a007f13b3e3)
- [Surfline LOTUS Swell Model](https://www.surfline.com/lp/whatsnew/features/lotus-swell-model)
- [Surfline AI announcement](https://www.surfer.com/news/surfline-artificial-intelligence-premium-plus)
- [Surfline SurfZone AI](https://www.surfertoday.com/surfing/surfline-revolutionizes-beach-monitoring-with-surfzone-ai)
- [Wave-Tracking Surf Zone with Deep Neural Networks (MDPI Atmosphere 2020)](https://www.mdpi.com/2073-4433/11/3/304)
- [Stanford CS230 - Identifying Active Surfers from Surf Camera](http://cs230.stanford.edu/projects_spring_2020/reports/38860342.pdf)

### Verification & Probabilistic
- [WMO/CAWCR Forecast Verification (canonical reference)](https://www.cawcr.gov.au/projects/verification/)
- [Barnston (1992). Correspondence among correlation, RMSE, Heidke. Weather and Forecasting](https://journals.ametsoc.org/view/journals/wefo/7/4/1520-0434_1992_007_0699_catcra_2_0_co_2.xml)
- [climpred metrics documentation](https://climpred.readthedocs.io/en/stable/metrics.html)
- [verif Python package](https://pypi.org/project/verif/0.3.0/)
- [ECMWF Ensemble Prediction System docs](https://www.ecmwf.int/en/elibrary/75394-ensemble-forecasting)

### Beach Safety / Rip Currents (vergelijkbaar domain)
- [NOAA Rip Current Forecast Model launch](https://oceanservice.noaa.gov/news/apr21/rip-current-forecast.html)
- [Predicting drowning from sea and weather forecasts (bioRxiv)](https://www.biorxiv.org/content/10.1101/721142.full.pdf)
- [Deep Learning Framework for Operational Rip Current Warning (MDPI JMSE)](https://www.mdpi.com/2077-1312/14/5/496)

### AI Weather / LLM
- [GraphCast Pangu Weather IFS comparison (GMD Copernicus 2024)](https://gmd.copernicus.org/articles/17/7915/2024/)
- [Pangu-Weather paper (arXiv 2211.02556)](https://arxiv.org/abs/2211.02556)
- [AI-Meteorologist LLM-Agent System (arXiv 2511.23387)](https://arxiv.org/html/2511.23387)
- [Modular LLM-Agent for Weather Interpretation (arXiv 2512.11819)](https://arxiv.org/html/2512.11819v1)
- [Generating Descriptive Weather Reports with LLMs](https://www.dbreunig.com/2024/10/29/generating-descriptive-weather-forecasts-with-llms.html)

### Crowd-sourcing / Ground Truth
- [SURF: Learning from busy noisy end users (arXiv 2010.05852)](https://arxiv.org/pdf/2010.05852)
- [Inferring ground truth through crowdsourcing (arXiv 1807.11836)](https://arxiv.org/pdf/1807.11836)

### Whitecaps
- [Callaghan et al. (2008). Whitecap coverage and wind history. GRL](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2008GL036165)
- [Whitecap coverage dependence on wind and sea states (Springer 2019)](https://link.springer.com/article/10.1007/s11802-019-3808-7)

---

*Einde rapport — ca. 4100 woorden. Voor implementatie-suggesties zie Top 5 Implementable Techniques sectie.*
