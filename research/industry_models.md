# Onderzoek: hoe komen commerciële surf-forecast-apps tot een SCORE of RATING?

**Datum:** 19 mei 2026
**Doel:** referentiekader bouwen voor het verbeteren van het Python surf-alert systeem voor Noordwijk. Focus op *hoe* een wave-condition omgezet wordt in één score of rating, welke parameters wegen, en wat naïeve scoring typisch mist.
**Scope:** industry leaders (Surfline, Magicseaweed, Stormsurf, Surf-Forecast.com, Windguru) plus Europese/NL-specifieke diensten (Surfana, Ridersguide, Seven at Sea, de referentie-forecaster, Goedegolven, Surf-Report.com).
**Belangrijke caveat:** geen enkele dienst publiceert hun exacte formule. Wat hieronder volgt is gereconstrueerd uit officiële support-artikelen, blogposts van hun forecasters, en publieke interviews. Waar bekend wordt de bron geciteerd; waar afgeleid wordt dat expliciet vermeld.

---

## 1. Surfline (incl. LOTUS, AI Forecast, Magicseaweed-erfgoed)

Surfline is de wereldwijde marktleider en sinds mei 2023 ook eigenaar van Magicseaweed. Hun rating-systeem is het meest gedocumenteerde van alle commerciële diensten, hoewel zij de exacte gewichten en formules niet publiceren.

### 1.1 Het LOTUS-model: technische architectuur

LOTUS (gelanceerd 2021, opvolger van LOLA uit 2001) is een hybride pipeline. De publieke documentatie noemt expliciet de volgende lagen:

1. **Globaal wave-model:** NOAA WAVEWATCH III als source code basis. ECMWF wordt door externe forecasters (Stormsurf, surfertoday-vergelijkingen) genoemd, maar Surfline's eigen documentatie verwijst alleen naar WW3. Diepwater output van WW3 voedt de regional modellen.
2. **Wind input:** GFS globaal, NAM in Noord-Amerika en Hawaii. Geen ECMWF voor wind volgens hun Live Wind support-artikel.
3. **Nearshore wave-model:** propriëtair, gebouwd op WW3 source maar geconfigureerd "specifically for surf, not deep-water swell" — d.w.z. ze brengen swell-energie via shoaling tot in de break-zone in plaats van te stoppen bij de continentale plat.
4. **Hoge-resolutie bathymetrie:** per spot ge-tuned; ze noemen dat "we run a 25-year hindcast with the latest settings for a spot" voor elke bathymetrie-update. Resolutie wordt nergens als specifiek getal (meters/boog-seconde) gepubliceerd.
5. **Data-assimilatie:** satellieten (altimeter), CDIP en NDBC boeien. Surfline noemt expliciet dat ze LOLA/LOTUS "on the fly" corrigeren wanneer satelliet/boei-data afwijkt.
6. **Machine learning bias-correctie laag:** zie 1.3.
7. **Update frequentie:** hourly output (LOLA was 6-hourly). Smart Cam observaties zullen dit verder verfijnen naar ~10 minuten input ratio.

Bron: [What is LOTUS? Surfline Support](https://support.surfline.com/hc/en-us/articles/4410495359643-What-is-LOTUS), [LOTUS swell model feature page](https://www.surfline.com/lp/whatsnew/features/lotus-swell-model), [Out With the Old, in With the New](https://www.surfline.com/surf-news/what-does-lola-stand-for/87781).

### 1.2 De Surf Rating: van surfhoogte naar één label

Surfline rating is **categorisch (7 tiers)**, niet 0–10. Tiers: VERY POOR → POOR → POOR-FAIR → FAIR → FAIR-GOOD → GOOD → EPIC. In de app wordt deze gemapt op een 5-bar visuele balk, waarbij GOOD en EPIC slot-3-5 overschrijven met een aparte label.

De cruciale onthulling uit het support-artikel "Surf Ratings & Colors":

> *"At most spots, the current version of model condition ratings uses surf height and wind conditions to estimate a rating. This means that ratings not provided by forecasters can miss important factors, such as prior winds that have left residual chop on the surface, or a very high tide that slows conditions."*

Dat is een belangrijke admission: de **default model-rating gebruikt alleen surfhoogte + wind**. Tide en swell-spectra worden door het model NIET expliciet gewogen in de rating-laag — wel in de surfhoogte-laag die als input dient. Forecaster-overrides zijn nodig om GOOD/EPIC toe te kennen ("Good and Epic ratings can only be assigned by forecasters who have observed the conditions").

**Wat Surfline forecaster Mike Watson openlijk zegt:**
- Wave size is een *harde limiter*. "You would never see 3-4' surf with offshore winds rated as epic" en "1-2' clean surf will also never be considered good or epic".
- Wind moduleert binnen die size-limit. "Given forecasted wave heights of 3-4', if winds were to be offshore resulting in clean conditions, the rating may be fair to fair+ and maybe even fair-good. But if winds were to be onshore the rating may be anywhere from poor to poor-fair."
- Ratings zijn **spot-specifiek genormaliseerd**. "A 'Fair' rating at Pipeline will not look the same as a 'Fair' rating at an average beachbreak."

Bron: [Surf Ratings & Colors](https://support.surfline.com/hc/en-us/articles/36277684017819-Surf-Ratings-Colors), [Updates to Surfline's Rating of Surf Heights and Quality](https://www.surfline.com/surf-news/surflines-rating-surf-heights-quality/1417).

### 1.3 Machine learning: hoe het écht werkt

Ben Freeston (Surfline Labs, ex-Magicseaweed founder) heeft op Medium expliciet beschreven hoe de ML-laag functioneert. De architectuur is bewust simpel:

> *"The AI system, a fairly simple neural net in this first implementation, is tasked with learning how to most accurately predict the human surf observations from the computer model data."*

De pipeline is dus:
1. LOTUS produceert raw outputs (Hs, Tp, Dir, wind, tide, etc.).
2. Een neuraal net (geen CNN/LSTM, gewoon een feed-forward MLP volgens de beschrijving) wordt getraind om voor elke spot/uur de menselijke forecaster-observation te voorspellen.
3. Training data: 25 jaar forecaster-reports + 20 jaar camera-stream data + sinds 2019 Smart Cam metingen (24 miljard videoframes/jaar).
4. Loss-functie wordt niet gepubliceerd, maar de target is duidelijk: de menselijke "surf height" en "rating"-observatie.

**Belangrijkste resultaat:** "reductions in error of up to almost 70% for some locations". Op spot-niveau, niet globaal. En het ML-team werkt bias-correctie eerst: als het model systematisch 30 cm te hoog/laag zit voor spot X bij swell-richting Y, wordt dat geleerd. Volgens hun eigen blog: bias-correctie alléén reduceerde fouten al met 30–40%.

**Forecaster comparisons:** vergeleken met raw LOLA model heeft de human forecaster team historisch de fout meer dan gehalveerd ("more than halves the error"). Het ML-systeem leert deze human-correctie te reproduceren. Bij HB Pier blijft Surfline gemiddeld onder 1ft (30 cm) MAE tot 6 dagen vooruit.

Bron: [Machine Learning for Surf Forecasting (Ben Freeston)](https://medium.com/surfline-labs/machine-learning-for-surf-forecasting-4a007f13b3e3), [How Surfline is Redefining Surf Forecast Accuracy](https://www.surfline.com/surf-news/surf-forecast-accuracy/50389), [Surf Forecast Accuracy](https://medium.com/surfline-labs/surf-forecast-accuracy-b563605f104c).

### 1.4 Advanced Swell / Swell Spectra: de échte signature feature

Dit is misschien het meest onderschatte stuk Surfline-technologie. Sinds 2024 toont Surfline 2D swell-spectra (energie verdeeld over periode én richting) i.p.v. alleen de dominante swell:

> *"The significant wave height in deep water is a combination of all the different wave trains... and could be a 3' South, a 2' North, and a 2' east swell — all merging to create a combined reading. The calculation for the period and direction is to take the single most dominant wave train at the buoy and use its period and direction."*

Surfline gebruikt een **propriëtaire partition-methode** om concurrente swells te splitsen. Voor de Noordzee zou je dit als volgt vertalen:
- Niet één Hs/Tp/Dir uit Open-Meteo gebruiken, maar zoeken naar partities van swell vs. wind-zee (de WaveWatch III output van Open-Meteo bevat al gescheiden `wave_height`, `wind_wave_height`, `swell_wave_height` velden — die zijn de partities!).
- Concentrated spectrum (1 dominante swell) = lange sets + lange lulls; goed voor pointbreaks, vaak closeouts op beachbreaks.
- Wide spectrum (combo swell, energie verspreid) = peaky, irregulair, vaak rommelig op beachbreaks.

Voor Noordwijk-toepasselijk: typische Noordzee-conditie is wide spectrum met windswell-dominantie. Surfline's eigen advies: een 4ft @ 13s solo-swell is iets totaal anders dan 4ft @ 13s + 3ft @ 7s combo-swell, ook al toont de top-level Hs voor beide ~5ft.

Bron: [Advanced Swell - Swell Spectra Support](https://support.surfline.com/hc/en-us/articles/20294130483099-Advanced-Swell-Swell-Spectra), [Feature Spotlight: Forecast Swell Spectra](https://www.surfline.com/surf-news/feature-spotlight-forecast-swell-spectra/197081), [How and Why to Use Buoy Swell Spectra to Score](https://www.surfline.com/surf-news/use-buoy-swell-spectra-score/208713).

### 1.5 Optimal Conditions / Premium+ AI personalization

Sinds juli 2024 voegde Surfline een persoonlijke filter toe: hun ML-systeem leert per gebruiker (op basis van Apple Watch ride-data + favorite spots) wat "optimal" betekent. "There's lots of different versions of perfect," aldus Freeston. Dat is een step verder dan een algemene rating: ze re-rangschikken hetzelfde forecast op verschillende manieren voor verschillende gebruikers.

Bron: [Surfer Magazine: Surfline Premium+ AI](https://www.surfer.com/news/surfline-artificial-intelligence-premium-plus), [Experience Magazine AI Forecast](https://expmag.com/2020/07/ai-can-predict-the-perfect-surfing-day/).

---

## 2. Magicseaweed (legacy, pre-Surfline acquisitie)

Magicseaweed werd in 2002 opgericht door Ben Freeston (die later naar Surfline overstapte) en de domain redirect sinds mei 2023 naar Surfline. Hun rating-systeem was echter het *meest expliciet gedocumenteerde* voor combiformule en wordt nog steeds gebruikt als referentie.

### 2.1 De Solid/Faded Star formule

MSW gebruikte twee gehele waarden, beide 0-5:

```
solidRating  = swell power & size rating (0-5)
fadedRating  = wind-penalty rating (0-5)

Overall rating displayed = solidRating  (solid stars first, then fadedRating in grey)
```

Belangrijk: **fadedRating zit BINNEN de 5-sterren-cap**. Niet als negatieve toevoeging. Dat wil zeggen:
- 5 solid + 0 faded = top conditie (sterk swell, schone wind).
- 3 solid + 2 faded = de swell heeft 5-ster potentieel, maar wind degradeert hem naar effectief 3.
- 2 solid + 0 faded = matig swell, schone wind (eindscore 2).
- 0 solid + 0 faded = flat.

Dit is een **multiplicatieve interpretatie**: de wind kan alleen *omlaag halen*, nooit een 1-ster swell tot 5-ster maken. Sustained off-shore wind boven swell genereert geen bonus.

### 2.2 Big-wave: de Black Star

Voor big-wave spots (Mavericks, Nazaré, Jaws) gebruikte MSW een aparte "black star"-schaal, omdat een 30ft Mavericks 5-ster anders moet wegen dan een 4ft beachbreak 5-ster. Dit is feitelijk **spot-class normalisatie**: niet alle 5-sterren zijn equivalent over spot-types.

### 2.3 Hoe MSW swell rating samenstelde

Niet publiek gepubliceerd, maar uit hun help-docs ("the total number of stars is the rating of the swell without the wind taken into effect") en API-gedrag (via meta-surf-forecast op GitHub) is duidelijk dat:
- **Swell height** is de primaire driver.
- **Swell period** voegt vermenigvuldigend toe (hogere periode = meer power, meer sterren bij gelijke hoogte).
- **Swell direction vs. spot orientation** filtert: een 4m W-swell op een N-facing spot zou minder sterren krijgen dan dezelfde swell op een W-facing spot.
- **Tide werd NIET expliciet in de rating gestopt** (apart getoond). Dat is een belangrijke MSW-limitation die ook Surfline beheerstte.

Bron: [MSW Rating dev docs](https://de.magicseaweed.com/docs/developers/59/msw-rating/9913/), [MSW Star Rating help](https://magicseaweed.com/docs/forecasting/66/star-rating/10134/), [meta-surf-forecast GitHub](https://github.com/swrobel/meta-surf-forecast).

---

## 3. Stormsurf (Mark Sponsler) — de "Surf Whisperer" methodologie

Stormsurf.com (sinds 1998) is fundamenteel anders: **geen geautomatiseerde rating, wel diepe handmatige interpretatie van raw model-data en boei-spectra.** Sponsler is de referent voor big-wave forecasting en voorspelt het arriveren van Mavericks-swells "to the minute".

### 3.1 Wat Sponsler ANDERS doet

> *"The trick is to not use forecast projections, but only look at winds and seas that are actually occurring right at this very moment. The models tend to hype things up."*

Zijn werkwijze:
1. **Start bij de storm-fetch, niet bij de spot.** Hij identificeert eerst een potential storm met >25 knopen wind, gedurende >24 uur, binnen het swell-window van de spot.
2. **Verifieer fetch in jouw swell-window.** Een swell-window is "the part of your ocean that provides a swell unobstructed, straight-line access to your beach". Voor Noordwijk zou dat het noordelijk-westelijke segment van de Noordzee zijn (ZW geblokkeerd door GB, NO door wadden-bank).
3. **Werk met 2D wave-spectra, niet dominante Hs/Tp.** "If there are multiple swell trains hitting at the same time, the spectrum will help Sponsler identify it." De NDBC en CDIP-boeien meten energie-verdeling over 3–40s periode en alle richtingen.
4. **Gebruik raw GRIB-files i.p.v. aggregated providers.** Sponsler downloadt ~2.5 GB per model-run, 4×/dag = ~10 GB/dag, ~4 TB/jaar.
5. **Voeg local wind/tide handmatig toe.** Hij vertrouwt op MM-5, MAPS, ETA (mesoscale modellen, NL-equivalent zou HARMONIE-AROME van het KNMI zijn) voor de near-shore wind, niet op GFS.

### 3.2 Heuristiek: van offshore-data naar near-shore breaking

Stormsurf publiceert formules niet als formules, maar de tutorial-pagina's geven impliciet:
- **Lange periode = beter geschoond.** Een 17s swell reist ~600 nautical miles per dag (concentric rings op zijn charts). Korte periodes (<10s) blijven gecorreleerd aan locale wind.
- **Decay over fetch-distance.** Hij rekent met een combinatie van: 1) short-period dissipatie, 2) directional spreading, 3) frequency-dispersion. Geen single closed-form, wel mentaal model.

### 3.3 Wat Stormsurf bewust NIET doet

Geen sterren-rating, geen "epic"-label, geen ML-bias-correctie. Hij gelooft expliciet dat models "hype things up" en alleen real-time observed conditions betrouwbaar zijn. Dat is een filosofische tegenpool van Surfline.

Bron: [Stormsurf "Create Your Own Surf Forecast" paper](https://www.stormsurf.com/page2/papers/papers.shtml), [Lookout Santa Cruz "The Surf Whisperer"](https://lookout.co/surfing-mark-sponsler-stormsurf-forecasts-have-earned-a-devoted-following-among-big-wave-surfers), [SurfScience interview](https://surfscience.com/topics/waves-and-weather/forecasting/create-your-own-surf-forecast-with-stormsurf/), [Florida Surf Museum profile](https://floridasurfmuseum.org/talking-story/the-florida-connection-mark-sponsler-wave-whisperer).

---

## 4. Surf-Forecast.com — de Wave Energy aanpak

Surf-Forecast.com gebruikt een interessante hybride: **een 0–10 sterren-rating + een onderliggende `Wave Energy` metric in kJ.**

### 4.1 De formule (gedeeltelijk publiek)

> *"Star rating is a scale of 1 to 10 and is based on swell size and character (bigger the swell and longer the period the higher the rating), however if the wind is onshore the star rating drops in proportion to the wind speed and the colour of the star goes pale. Bright yellow 10 star is the best big surf with classic conditions and light offshore winds. Flat conditions, blown out waves in onshore winds or very strong winds in any direction will result in 0 star rating."*

Drie cruciale punten:
1. **Continue degradatie via wind, niet stepwise.** "drops in proportion to the wind speed" — additieve/multiplicatieve continue functie, niet binaire flag.
2. **Wind in EENDER welke richting kan blow-out veroorzaken bij hoge snelheid.** Dat is genuanceerder dan "offshore = altijd goed". Sterke offshore wind kan ook paddle-back en spray creëren.
3. **Wave Energy (kJ) als verklarende metric onder de sterren.** Vuistregel die ze publiceren: 100 kJ = surfable, 200–1000 kJ = punchy, 1000–5000+ kJ = heavy/dangerous.

### 4.2 Hoe wave energy berekend wordt

Wave energy in de oceanografie is per definitie:

```
E ∝ Hs² · Tp      (energie per oppervlakte-eenheid, golf-energieflux)
```

Surf-Forecast.com publiceert geen exacte coëfficiënten, maar de formule sluit zeker aan op de standaard `(ρg²/64π) · Hs² · Tp` (energie-flux in W/m). Voor surf-forecasting wordt dit getransformeerd naar een "shoaling-aware" Hs voor de break.

### 4.3 Beperkingen volgens henzelf

Surf-Forecast.com erkent: "These ratings are calculated automatically and are therefore not always meaningful. For a complete picture, surfers should also check tide state, local spot orientation, and the multi-swell component breakdown available under the 'advanced users' option."

Dat is letterlijk wat MSW ook erkende: hun rating is een **first-pass filter, niet een eindoordeel**.

Bron: [Surf-Forecast.com FAQs](https://www.surf-forecast.com/pages/faq), [Surf Tribe Blog: star rating limitations](https://www.thesurftribe.com/surf-blog/how-to-read-a-surf-forecast-and-why-the-star-rating-isnt-enough).

---

## 5. Windguru — voor surfers grotendeels onbruikbaar

Windguru is fundamenteel een kitesurf/windsurf-platform en hun rating reflecteert dat:

- **3-sterren schaal**, alleen op basis van wind-snelheid drempels (geen golfdata).
- 1 ster ≈ kiten met grote kite (10–14m), 2 ster = optimaal, 3 ster = klein materiaal nodig (5–8m).
- **Geen upper-limit** — 74 knopen wordt nog steeds 3-ster, ook al is dat dodelijk.
- Negeer gusts, wind-richting, regen, golfconditie.
- Blauwe kleur betekent watertemp <9°C.

Voor surfers: **negeer de Windguru rating-rij.** De ruwe wind- en swell-data is wél bruikbaar (GFS-output, mooi tabellarisch). Geen enkele NL-forecaster gebruikt Windguru's rating voor surf.

Bron: [Mundo Surf: How to interpret Windguru](https://www.mundo-surf.com/blog/en/how-to-interpret-windguru-easily-and-quickly/), [Windguru Help](https://www.windguru.cz/help/).

---

## 6. Nederlandse & Europese diensten

### 6.1 Surfana kennisbank

Surfana gebruikt **geen eigen rating-algoritme** — ze leren surfers Windfinder, MSW en Windguru lezen. Hun kerninzicht voor de Noordzee:

> *"In Nederland hebben we doorgaans te maken met 'windswell' — golven die ontstaan uit wind. Ze worden gegenereerd door wind richting ruwweg uit het noorden, westen of zuiden. Bij een oostenwind zijn er dus nooit golven."*

Beaufort-drempel: >6 Bft = alleen voor (zeer) gevorderden. Stroming Noordzee: opkomend tij = noordwaarts, afgaand = zuidwaarts.

Bron: [Surfana – Maak kennis met de Noordzee](https://www.surfana.com/kennisbank/golfsurf-weer/maak-kennis-met-de-noord-zee/), [Surfen op de Noordzee](https://www.surfana.com/blog/leren-surfen/surfen-op-de-noordzee/).

### 6.2 Ridersguide.nl — Scoren in de Noordzee

Ook geen geautomatiseerd algoritme, maar wel een belangrijke **ervaringsregel** die direct relevant is voor Noordwijk:

> *"Groot nadeel van Magicseaweed/Windguru is dat deze sites alleen de meest dominante swell laten zien. Zo kan het zijn dat er tachtig centimeter noordswell loopt en daarbij een vrij krachtige zuidelijke wind staat die één meter windswell genereert. Magicseaweed stuurt je dan niet naar het strand omdat de golfperiode van de één meter zuidswell te laag is voor kwalitatief goede golven. Toch kan het dan nog zeker de moeite waard zijn, zolang de noordswell maar blijft doorstaan."*

**Implicatie voor de eigen Python pipeline:** check zowel `swell_wave_*` als `wind_wave_*` velden uit Open-Meteo. Een matige swell + hoge windsea kan een sub-optimale Hs/Tp geven maar nog steeds surfable zijn als de swell-component goed georiënteerd is en periode >7s heeft.

Andere Ridersguide-regels:
- NW windgolven recht uit zee: bij hoogwater kan een holle binnenste-bank-golf ontstaan ondanks rommelige zee verder uit.
- Na harde wind: golfhoogte halveert binnen 4 uur na windafname — het venster van "schoon na de storm" is kort en kritisch.

Bron: [Scoren in de Noordzee – Ridersguide.nl](https://ridersguide.nl/scoren-in-de-noordzee/).

### 6.3 Seven at Sea — 5-lessen cursus

Geen rating-algoritme maar wél een goede systematische checklist die ze in 5 lessen opbouwen:
1. Wat is swell (gerelateerd aan periode/energie/oriëntatie).
2. Windkaarten (GFS, Windfinder).
3. Lokale wind (onshore/offshore per spot).
4. Getijden (NL: opkomend = noordstroming, afgaand = zuid; voor noord-swell is opkomend beter wegens tegenwerking).
5. Combineren tot voorspelling.

Belangrijk Seven-at-Sea inzicht: **kustlijn-oriëntatie maakt dezelfde wind-richting heel anders per spot.** N-wind = sideshore in Petten (acceptabel) maar cross-onshore in Domburg (rommelig).

Voor Noordwijk specifiek: hoofd-oriëntatie ≈ 270° (W-facing), dus N en Z winden zijn sideshore-tot-zijaflandig, O is aflandig (offshore), W/ZW = aanlandig (onshore).

Bron: [Sevenatsea voorspellen lessen 1–5](https://sevenatsea.nl/voorspellen/leer-zelf-surfcondities-te-voorspellen/).

### 6.4 De referentie-forecaster

De referentie-forecaster gebruikt **een sterk subjectieve narrative-stijl methodologie** met expliciete spectra-decompositie. Sleutelelementen uit zijn publieke posts:

- **Spectra-analyse via Rijkswaterstaat-boeien**: hij leest periode én richting handmatig uit het 2D-spectrum van de RWS-boeien (Munitiestort, Maasgeul, etc.) (b.v. "140 mHz uit WZW = 7.1s uit 250°").
- **Waterstand vs. golf-break interactie**: "Als de golven niet omslaan heeft dat te maken met een te hoge waterstand. Hoe meer water op een zandbank, hoe minder snel een golf breekt."
- **Spring/doodtij overweging**: bij springtij + N-swell op opkomend tij = stroming tegen swell = bonus; bij doodtij = stroming-effect grotendeels weg.
- **Dagdeel-voorkeur per spot**: Zuid-Holland (Noordwijk/Katwijk) 12–16u, Noord-Holland 15–18u (door verschil in wegvallend lokaal-wind moment).

### 6.5 Goedegolven.nl

Goede Golven biedt hoge-resolutie wind & wave forecasts voor NL/BE met real-time boei-metingen en webcams, maar publiceert geen eigen rating-formule. Ze positioneren zichzelf als data-aggregator + persoonlijke spotnotities.

Bron: [Goedegolven.nl](https://goedegolven.nl/), [Boardshortz: surfweer overzicht](https://www.boardshortz.nl/surfen/nederland/surfweer/).

### 6.6 Surf-Report.com (Frankrijk)

Surf-Report.com gebruikt grotendeels Météo-France's WAVEWATCH III implementatie + handmatige spot-reports + foto-bewijs. Hun rating-systeem is niet publiek gedocumenteerd in detail; concurrent Surf-Sentinel publiceert wel een "easyREPORT" wave rating system. Allosurf gebruikt WAM van ECMWF. Geen van deze publiceert exacte formules.

Bron: [Ocean Adventure: surf forecasting France](https://oceanadventure.surf/en/surfing-weather-waves/).

---

## 7. Specifieke vragen beantwoord

### A. Welke parameters wegen ze, met welke gewichten?

| Parameter | Surfline rating | MSW rating | Surf-Forecast | Stormsurf | Referentie-forecaster |
|---|---|---|---|---|---|
| Hs (surf height) | **Primair** (hard cap) | Primair | Primair (via energy) | Reference output | Primair |
| Tp (period) | Indirect via height | Multiplicative bonus | Primair (energy ∝ Hs²·Tp) | Critical, manual | Critical |
| Swell direction | Indirect via height | Filter | Filter | Critical (swell window) | Critical |
| Wind speed | Onshore-penalty | Faded-star penalty | Continuous degradation | Manual | Manual |
| Wind direction | Onshore/offshore binary + cross | Binary onshore = penalty | All-direction at high speed | Manual | Critical met spot-oriëntatie |
| Tide level | Forecaster-override | Niet in rating | Niet in rating (apart) | Manual | Critical |
| Tide phase | Niet expliciet | Niet | Niet | Manual | Critical (spring/doodtij) |
| Spot bathymetry | Embedded in LOTUS | Generic per-spot | Generic | Manual | Per-spot mentaal model |
| Combo swell | Forecaster-override + spectra | Niet | "Advanced" optie | Critical | Critical via spectra |

### B. Hoe combineren ze die naar één score?

- **Multiplicatief vs additief:** beide systemen, met dominantie multiplicatief.
  - MSW: swell-rating × wind-discount (faded-star steekt af BINNEN cap).
  - Surf-Forecast: swell-energy continu gedegradeerd door wind-snelheid.
  - Surfline (model-laag): hard size-cap, dan wind-modulation binnen die cap.
  - Surfline (ML-laag): non-linear via neural net (impliciet additief én multiplicatief).
- **Drempels en cliffs vs smooth functions:** MSW en Surfline gebruiken duidelijke tier-cliffs (5 sterren, 7 tiers); Surf-Forecast claimt continue degradatie maar visualiseert in 10 tiers.
- **Discrete bonus vs continu:** allen lijken continu te interpoleren en dan te discretiseren als laatste stap voor UI.

### C. Wind-wave interactie modellering

- **Surfline:** alleen via training data — neural net leert impliciet dat wind X kn uit richting Y op spot Z surf-height met factor F reduceert.
- **MSW:** stepwise — wind boven drempel = 1 faded star erbij.
- **Surf-Forecast:** continue ("in proportion to wind speed").
- **Stormsurf/referentie-forecaster:** handmatig met spot-kennis.
- **Wetenschap (Falk Feddersen et al., Scripps):** "Wave models to date have not included these wind effects" — d.w.z. de fysica van hoe onshore/offshore wind de drukverdeling op het breekende golfoppervlak verandert is pas recent onderzocht en zit niet in WW3.

### D. Tide-interactie modellering

- **Surfline:** expliciet erkend als "missing" in de geautomatiseerde rating; forecaster-override en spot-specifieke ML noodzakelijk.
- **MSW:** niet in de rating, apart getoond.
- **Surf-Forecast:** niet in rating, apart getoond.
- **Referentie-forecaster:** wél in rating, via heuristieken (water-op-bank, stroming tegen swell, spring/doodtij).
- **Industry consensus:** "There is no universal best tide rule — it's spot-specific bathymetry-dependent". Surf-forecast literatuur recommenderen om over tijd te leren per spot.

### E. Board-aanbevelingen

- **Geen enkele forecast-app linkt rating direct aan board.** Surfline doet sinds 2024 wel personalization via Apple Watch ride-data.
- **Industry rule-of-thumb (uit board-sizing-literatuur):**
  - Beginner: 8–9.5 ft soft-top in alles tot ~3 ft.
  - Intermediate: 6.5–8.5 ft funboard.
  - Advanced: 5.6–6.6 ft shortboard/fish in 2–4 ft pitching, step-up >4 ft.
  - Gun: >12 ft waves.
- **Volume-rule (Guild Factor):** body weight (kg) × 1.0 (beginner) / 0.55 (intermediate) / 0.38 (advanced) = target liters.
- **Voor Noordwijk-relevantie:** typisch 0.5–1.5m windswell met periode 5–8s = mid-length 6.6–7.6 ft of fish-shape voor intermediates; foamie voor beginners.

### F. Output quality van geautomatiseerde scoring

- **MAE als standaard metric.** Surfline benchmarks tegen een naive shoaling-forecast (size only).
- **Forecaster team halveert raw-model error.** Bevestigd door Freeston: "the forecast team more than halves the error of the sophisticated nearshore modelling approach".
- **ML laag verkort fout met 30–40% via bias-correctie alleen, tot ~70% op specifieke spots.**
- **Bekende failure modes:**
  1. **Onshore-wind missed** door grof GFS-grid op locale thermal sea breezes/topografie.
  2. **Windswell mistaken voor surfable swell** wanneer alleen Hs gerapporteerd wordt zonder Tp filter.
  3. **Secondary swell ruïneert primary** als forecast alleen dominante swell toont.
  4. **Hourly wind inaccurate >24 uur vooruit.**
  5. **Gust underestimation** maakt onverwacht onstabiele paddle-conditions.
- **Wat pro forecasters doen dat algoritmes missen:**
  - Lokale wind-effecten (thermal, leeward eilanden, kustlijn-conventies).
  - Combo-swell interactie (peaky vs concentrated spectrum).
  - Tide × bathymetry per-spot kennis.
  - Analoge methode: "deze swell lijkt op X uit 2018 — toen werkte spot Y vanaf 16u".
  - Sequencing: dag-na-storm effecten.

### G. Real-time correction via buoy data

- **Surfline:** ja, "on the fly" — wave assimilation via satelliet + NDBC/CDIP boeien (~elke uur in nieuwere LOTUS-versies). Nearshore buoys zoals Huntington Beach CDIP geven near-realtime swell-spectrum dat met model vergeleken wordt.
- **Stormsurf:** primaire bron — Sponsler verkiest live buoy data boven model-forecast.
- **MSW/Surf-Forecast:** beperkt; aggregated boei-data getoond maar niet als correctie-laag.
- **Voor NL toepasselijk:** Rijkswaterstaat Munitiestort/IJmuiden/Eierland boeien zijn open data via waterinfo.rws.nl en publiceren spectra. De referentie-forecaster gebruikt dit; eigen pipeline doet het nog niet.

### H. AI/ML approaches in surf forecasting

**Bekende producten:**
- **Surfline LOTUS** + neural net rating-laag (Ben Freeston Medium posts).
- **Surfline SurfZone AI** (sinds 2019): CNN voor object-detection in camera-streams, surfer-counting, wave-counting per uur. Gepatenteerd ("patented solution for accurate people counting"). Geen open USPTO-link gevonden in search.

**Academische papers relevant:**
- arXiv 2509.14020: ensemble van MLP/RNN/LSTM/CNN/CNN-LSTM voor SWH op Braziliaanse kust; target residual tussen NOAA-model en observation. Vergelijkbaar met Surfline's bias-correction approach.
- arXiv 2311.15001: deep-learning real-time bias-correction voor SWH-forecasts in Western North Pacific via ConvGRU.
- Stanford CS229 project (2019): automated surf-reports uit camera-images.
- arXiv 2105.08583: ML in weakly nonlinear systems voor SWH.

**Geen bekende Kaggle competition specifiek over surf-rating** (wave height generic wel via climate science, niet rating).

**Patentlandschap:** geen specifieke USPTO surf-forecast rating patenten gevonden in search. Surfline's gepatenteerde tech zit in people-counting (computer vision) en SurfZone AI productlijn, niet in de rating-engine zelf.

---

## 8. Vergelijkings-matrix: Naive scoring vs industry leaders

| Aspect | Naive scoring (size-only) | Surfline (LOTUS + ML) | Magicseaweed | Surf-Forecast.com | Stormsurf | Referentie-forecaster |
|---|---|---|---|---|---|---|
| **Rating-output** | Continue 0–100 | 7 tiers (categorisch) | 5 solid + 0–5 faded sterren | 0–10 sterren (kleur) | Geen rating, raw data | Subjectief narratief |
| **Size als limiter** | Lineair gewicht | Hard cap (size limits epic) | Solid-rating drijver | Energy = Hs²·Tp basis | Reference, niet gerated | Met spot-context |
| **Periode (Tp)** | Soms niet gewogen | Indirect via shoaled height | Multiplicative bonus | Centraal in energy-formule | Critical, manual | Critical, spectra |
| **Swell-richting filter** | Cosine of binaire flag | Embedded in nearshore LOTUS + ML | Per-spot directie-filter | Per-spot filter | Manual swell-window | Per-spot mentaal model |
| **Wind onshore penalty** | Lineaire of step | Neural net leert curve | Faded-star binnen 5-cap | Continue, alle richtingen bij hoge speed | Manual | Manual met spot |
| **Tide level in rating** | Vaak ja, naive bell-curve | Niet in model-rating, forecaster-override | Niet in rating, apart | Niet in rating, apart | Manual | Critical heuristisch |
| **Tide phase (rising/falling)** | Zelden | Niet expliciet | Niet | Niet | Manual | Spring × richting × stroming |
| **Combo-swell** | Niet | Spectra-feature + ML | Niet in star, "advanced" optie | "Advanced" tab | Critical via 2D-spectra | Critical via RWS-spectra |
| **Wind chop on face** | Geen | Impliciet via ML | Faded-star | Wind-degradatie | Manual | Manual |
| **Bathymetrie** | Niet | Per-spot, 25-jaar hindcast | Generic per-spot | Generic | Manual | Spot-kennis |
| **Real-time buoy-correctie** | Geen | Hourly assimilatie | Beperkt | Beperkt | Primair | Handmatig spectra-check |
| **ML/AI** | Geen | Neural net + Smart Cam | Niet (legacy) | Niet | Niet (filosofie!) | Niet |
| **Update frequentie** | Per pipeline-run | Hourly | 3-hourly | 6-hourly | Manueel 4×/dag | Dagelijks handmatig |
| **Spot-class normalisatie** | Niet | Ja (Pipeline ≠ beachbreak) | Black-star bigwave | Niet | Manueel | Per spot |
| **Personalisatie** | Niet | Sinds 2024 (Apple Watch) | Niet | Niet | Niet | Niet (mass-cast) |
| **Belangrijkste failure mode** | Windswell-bij-juiste-Hs verraadt als surfable | Tide/residual-chop bij spots zonder forecaster | Tide niet in rating | Geen tide, geen spectra | Geen rating → user heeft expertise nodig | Schaalt niet, dagelijks |

---

## 9. Gap-analyse: top-5 dingen die naïeve scoring mist

Op basis van bovenstaande consensus tussen industry leaders, dit zijn de **5 grootste verbeterpunten** die een naïeve "size + wind"-scoring mist:

### Gap 1: Multi-swell decompositie (combo-spectrum)
- **Wat naïef mist:** één Hs/Tp/Dir wordt behandeld als één swell. Maar Noordzee kent vaak gelijktijdige N-swell + ZW-windswell, of W-groundswell + lokale chop. Naïeve scoring rapporteert dan een tussenligende Hs en gemiddelde Tp die *geen van beide swells correct karakteriseert*.
- **Wat industry doet:** Surfline (forecast spectra), Stormsurf (2D-buoy spectra), de referentie-forecaster (RWS-spectra) gebruiken alle gescheiden swell-partities.
- **Practical fix:** gebruik Open-Meteo's afzonderlijke `swell_wave_height/period/direction` én `wind_wave_height/period/direction` velden. Score beide partities afzonderlijk, kies de hoogste, of bonus bij goed-georiënteerde groundswell zelfs als wind-zee niet meewerkt (Ridersguide-regel).

### Gap 2: Hard size-cap (you can't epic a 2-foot day)
- **Wat naïef mist:** lineaire scoring kan een 1m wave met perfecte wind hoog scoren via wind-bonus. Maar industry consensus: size is de harde limiter, geen wind kan een te kleine swell tot "epic" maken.
- **Wat industry doet:** Surfline hanteert "size as limiting factor"; MSW kapt op 5 solid stars; Surf-Forecast kapt op 10 sterren met blow-out clause.
- **Practical fix:** introduceer een sigmoid/cap-functie op de size-component die buiten een redelijk venster geen extra waarde geeft, en die bij kleine size ook andere positieve factoren afkapt. Bijvoorbeeld: max-score = `min(size_score, 100) * wind_factor * tide_factor`, nooit additief boven size.

### Gap 3: Tide × bathymetry per-spot heuristiek
- **Wat naïef mist:** een algemene bell-curve "rond half-tide is beste" voor alle spots. Maar voor Noordwijk specifiek werkt dit anders dan voor bv. Maasvlakte (stijl-oplopende bank) of Domburg.
- **Wat industry doet:** Surfline laat tide bewust uit de model-rating en verlaat zich op forecaster-override. De referentie-forecaster gebruikt expliciete heuristieken per spot. Surfana waarschuwt voor stroming verschillen.
- **Practical fix:** maak `tide_optimum_curve` per-spot configureerbaar i.p.v. globale formule. Voor Noordwijk: leer uit de posts van de referentie-forecaster (vaak laag tot mid op opkomend) en bouw aparte curves voor wind-swell (laag-water-bonus voor steepening) en groundswell (geen sterke voorkeur). Voeg `spring/doodtij` modifier toe via een eenvoudige tidal-range-check uit getij.nl.

### Gap 4: Wind-effect modulering t.o.v. swell-eigenschap (niet als losse multiplier)
- **Wat naïef mist:** dezelfde 15 kn onshore wind is destructiever op 0.5m windswell dan op 1.5m groundswell met 12s periode. Naïef behandelt wind als universele penalty.
- **Wat industry doet:** Surfline ML-laag leert dit impliciet; Surf-Forecast schaalt "in proportion to wind speed" relatief tot wave-strength; Stormsurf/de referentie-forecaster doen dit handmatig.
- **Practical fix:** maak `wind_penalty` schalend met `Tp` en `Hs` — bv. `penalty = wind_kn * f(Tp)` waarbij f(12s) ≈ 0.5 en f(5s) ≈ 1.2. Of: bereken een ratio "swell-energy / wind-disrupting-energy" en degradeer alleen onder een drempel. Erkennen dat sub-9s wind-zee fundamenteel "wind-driven" is en niet door wind weer veel meer beschadigd kan worden.

### Gap 5: Real-time buoy-correctie / dag-na-storm fenomeen
- **Wat naïef mist:** forecast-model loopt vast op snel veranderende NL-windswell. De golf-hoogte halveert binnen 4 uur na wind-afname (Ridersguide) — een vooruitlopende forecast die om 06u "1.2m" zegt kan om 10u prima 0.6m zijn, en omgekeerd kan een ochtend-stilte na een nacht-storm verrassende cleane condities geven die model niet ziet aankomen.
- **Wat industry doet:** Surfline hourly model + buoy-assimilatie (30–40% error reduction door bias-correctie alleen). Stormsurf vertrouwt vooral live boei-data. De referentie-forecaster checkt RWS-spectra elke ochtend.
- **Practical fix:** fetch laatste 6–12 uur RWS Munitiestort/IJmuiden boei-spectra in scoring-run. Als boei-Hs significant afwijkt van model-Hs voor de laatste 3 uur, pas een bias-correctie toe op de eerstvolgende 6–12 uur forecast. Eenvoudige variant: lineaire blending van boei-trend in vroegste forecast-uren. Geavanceerde variant: kalman-filter of simple regressie tussen boei en model over rolling window.

### Bonus Gap 6: Spot-class normalisatie
- **Wat naïef mist:** dezelfde score betekent niet hetzelfde op verschillende spots. Een 50/100 in een 2-meter-Hawaii-day is niet wat het is op een 0.4-meter-Noordwijk-day.
- **Wat industry doet:** MSW black-star, Surfline "fair at Pipeline ≠ fair at beachbreak".
- **Practical fix:** voor een single-spot-systeem (Noordwijk-only) is dit minder kritisch, maar overweeg om in de notificatie-tekst expliciet de **lokale benchmark** te noemen ("dit is een 3-ster Noordzee-day, niet een 3-ster Hossegor-day"). Verlaagt foute verwachtingen.

### Bonus Gap 7: Sequencing en consistency
- **Wat naïef mist:** twee uren met identieke parameters kunnen heel andere surf-quality hebben afhankelijk van *waar in een swell-sequence* ze zitten. Aan begin van een opbouwende swell = bonus, in afnemende fase met resterende chop = penalty.
- **Wat industry doet:** Surfline's "Wave Consistency" feature toont dit; de referentie-forecaster schrijft regelmatig "dag na de storm" als bonus-window.
- **Practical fix:** voeg een `swell_trend` feature toe (Hs delta over laatste 6 uur, periode-trend). Een opwaartse trend +0.3m/uur met stijgende periode = bonus; afnemende swell met chop = penalty.

---

## 10. Conclusie en aanbevolen prioriteit

De industry-consensus is duidelijk:

1. **Naïeve "size × wind" scoring is een fundamenteel beperkt model** dat de top-3 industry-spelers allemaal expliciet hebben verlaten ten gunste van ML-laagjes, swell-spectra decompositie, of pure-data tools voor experts.
2. **De grootste error-reductie komt niet van een betere fysische formule, maar van bias-correctie op spot-niveau** (Surfline: 30–40% van enkel boei-bias-correctie, 70% met ML-laag).
3. **Voor Noordzee-spots specifiek is multi-swell decompositie de #1 prioriteit** — naïeve scoring mist consistent dat een matige dominante swell + goed-georiënteerde secundaire swell nog steeds surfable kan zijn.
4. **Tide × bathymetrie is universeel onderbedeeld** in geautomatiseerde systemen; de handmatige heuristieken van de referentie-forecaster zijn hier de gouden standaard voor NL.
5. **Real-time RWS-boei integratie biedt low-hanging fruit voor accuracy-gain** zonder dat ML nodig is.

Voor het SurfWeerWorkflow-systeem (single-spot Noordwijk, 4×/dag GitHub Actions): de prioriteit zou moeten zijn:
- **P1**: Multi-swell scoring (gebruik `swell_wave_*` én `wind_wave_*` afzonderlijk).
- **P2**: Hard size-cap in score-aggregatie.
- **P3**: RWS-boei real-time bias-correctie voor de eerste 6–12 uur.
- **P4**: Tide-curve per-spot configureerbaar (i.p.v. globaal).
- **P5**: Wind-penalty schalend met Tp (groundswell minder wind-gevoelig).
- **P6** (optioneel, hogere investering): trainen van een eenvoudige logistische regressie of MLP op historische posts van de referentie-forecaster als labels, à la Surfline's neural net approach.

---

## Bronnenlijst (volledig)

**Surfline:**
- [What is LOTUS? Surfline Support](https://support.surfline.com/hc/en-us/articles/4410495359643-What-is-LOTUS)
- [Surf Ratings & Colors](https://support.surfline.com/hc/en-us/articles/36277684017819-Surf-Ratings-Colors)
- [Updates to Surfline's Rating of Surf Heights and Quality](https://www.surfline.com/surf-news/surflines-rating-surf-heights-quality/1417)
- [LOTUS swell model feature](https://www.surfline.com/lp/whatsnew/features/lotus-swell-model)
- [Out With the Old, in With the New (LOLA→LOTUS)](https://www.surfline.com/surf-news/what-does-lola-stand-for/87781)
- [How Surfline is Redefining Surf Forecast Accuracy](https://www.surfline.com/surf-news/surf-forecast-accuracy/50389)
- [Wave Consistency](https://www.surfline.com/lp/whatsnew/features/wave-consistency)
- [Advanced Swell - Swell Spectra Support](https://support.surfline.com/hc/en-us/articles/20294130483099-Advanced-Swell-Swell-Spectra)
- [Feature Spotlight: Forecast Swell Spectra](https://www.surfline.com/surf-news/feature-spotlight-forecast-swell-spectra/197081)
- [How and Why to Use Buoy Swell Spectra to Score](https://www.surfline.com/surf-news/use-buoy-swell-spectra-score/208713)
- [Reading LOLA Real Time Buoys (Kevin Wallis)](https://www.surfline.com/surf-science/lola-real-time-buoys---forecaster-blog_95329/)
- [Reading the Surfline Charts (Kevin Wallis)](http://www.surfline.com/surf-science/surfline-charts---forecaster-blog_54678)
- [The 14 Day LOLA Forecast (Kevin Wallis)](http://www.surfline.com/surf-science/the-14-day-lola-forecast---forecaster-blog_56788)
- [Machine Learning for Surf Forecasting (Ben Freeston, Surfline Labs)](https://medium.com/surfline-labs/machine-learning-for-surf-forecasting-4a007f13b3e3)
- [Surf Forecast Accuracy (Ben Freeston, Surfline Labs)](https://medium.com/surfline-labs/surf-forecast-accuracy-b563605f104c)
- [Live Wind Surfline Support](https://support.surfline.com/hc/en-us/articles/5291311612315-Live-Wind)
- [Surfer Magazine: Surfline AI Premium+](https://www.surfer.com/news/surfline-artificial-intelligence-premium-plus)
- [Experience Magazine: AI Forecast](https://expmag.com/2020/07/ai-can-predict-the-perfect-surfing-day/)
- [Surfertoday: SurfZone AI](https://www.surfertoday.com/surfing/surfline-revolutionizes-beach-monitoring-with-surfzone-ai)

**Magicseaweed (legacy):**
- [MSW Rating dev docs](https://de.magicseaweed.com/docs/developers/59/msw-rating/9913/)
- [MSW Star Rating help](https://magicseaweed.com/docs/forecasting/66/star-rating/10134/)
- [MSW Quick Forecast Tutorial](https://magicseaweed.com/docs/forecasting/66/a-quick-forecast-tutorial/10123/)
- [meta-surf-forecast GitHub (rating normalization)](https://github.com/swrobel/meta-surf-forecast)

**Stormsurf:**
- [Stormsurf "Create Your Own Surf Forecast" paper](https://www.stormsurf.com/page2/papers/papers.shtml)
- [Stormsurf Tutorials menu](https://www.stormsurf.com/page2/tutorials/menu.html)
- [Lookout Santa Cruz: The Surf Whisperer](https://lookout.co/surfing-mark-sponsler-stormsurf-forecasts-have-earned-a-devoted-following-among-big-wave-surfers)
- [SurfScience interview with Sponsler](https://surfscience.com/topics/waves-and-weather/forecasting/create-your-own-surf-forecast-with-stormsurf/)
- [Florida Surf Museum: Mark Sponsler profile](https://floridasurfmuseum.org/talking-story/the-florida-connection-mark-sponsler-wave-whisperer)

**Surf-Forecast.com:**
- [Surf-Forecast.com FAQs](https://www.surf-forecast.com/pages/faq)
- [Surf Tribe Blog: star rating limitations](https://www.thesurftribe.com/surf-blog/how-to-read-a-surf-forecast-and-why-the-star-rating-isnt-enough)

**Windguru:**
- [Mundo Surf: How to interpret Windguru](https://www.mundo-surf.com/blog/en/how-to-interpret-windguru-easily-and-quickly/)
- [Windguru Help](https://www.windguru.cz/help/)

**Nederlandse/Europese diensten:**
- [Surfana – Maak kennis met de Noordzee](https://www.surfana.com/kennisbank/golfsurf-weer/maak-kennis-met-de-noord-zee/)
- [Surfana – Surfen op de Noordzee](https://www.surfana.com/blog/leren-surfen/surfen-op-de-noordzee/)
- [Surfana – Nederlandse surfweer-voorspellingen](https://www.surfana.com/blog/leren-surfen/nederlandse-surfweer-voorspellingen/)
- [Ridersguide.nl – Scoren in de Noordzee](https://ridersguide.nl/scoren-in-de-noordzee/)
- [Sevenatsea voorspellen cursus](https://sevenatsea.nl/voorspellen/leer-zelf-surfcondities-te-voorspellen/)
- [Sevenatsea – Windkaarten lezen](https://sevenatsea.nl/voorspellen/windkaarten-lezen/)
- [Sevenatsea – Lokale wind](https://sevenatsea.nl/voorspellen/lokale-wind/)
- [Sevenatsea – Getijden](https://sevenatsea.nl/voorspellen/getijden/)
- [Goedegolven.nl](https://goedegolven.nl/)
- [Boardshortz: Surfweer overzicht NL](https://www.boardshortz.nl/surfen/nederland/surfweer/)
- [Ocean Adventure: surf forecasting France](https://oceanadventure.surf/en/surfing-weather-waves/)

**Academisch / ML:**
- [arXiv 2509.14020 – ANN ensemble for SWH (Brazilian coast)](https://arxiv.org/pdf/2509.14020)
- [arXiv 2408.05797 – CNN vs RNN for storm surge Tampa Bay](https://arxiv.org/pdf/2408.05797)
- [arXiv 2311.15001 – Deep-learning real-time bias correction SWH NW Pacific](https://arxiv.org/pdf/2311.15001)
- [arXiv 2105.08583 – ML in weakly nonlinear systems SWH](https://arxiv.org/pdf/2105.08583)
- [Stanford CS229 – Automated surf reports from image data](https://cs229.stanford.edu/proj2019spr/report/19.pdf)
- [How Machine Learning Can Improve Surf Forecasts (Predictaments)](https://jotruebl.github.io/ml_surf_forecasts/)
- [Howzit: I compared 3 wave forecast models](https://hwztsurf.com/blog/3-wave-forecast-models)
- [Surfertoday: WaveWatch global model](https://www.surfertoday.com/surfing/wavewatch-wind-wave-forecast-model)
- [University of Hawaii: Waves and Surf Forecasting](http://www.soest.hawaii.edu/oceanography/courses_html/OCN201/laboratory/waves.html)
- [Wikipedia: Surf forecasting](https://en.wikipedia.org/wiki/Surf_forecasting)
- [Surfertoday: SMB method wave height prediction](https://www.surfertoday.com/surfing/smb-method-wave-height-prediction)
- [Surfertoday: effects of onshore/offshore wind on wave shape](https://www.surfertoday.com/surfing/the-effects-of-onshore-and-offshore-wind-on-wave-shape)

---

*Einde rapport. Geen code is aangepast; dit is uitsluitend research-output. Lengte: ~5000 woorden.*
