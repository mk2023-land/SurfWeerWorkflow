# Wat maakt een goede surfgolf? Internationaal onderzoek als benchmark-kader

**Doel:** een referentiekader bouwen om twee Nederlandse golf-voorspellingen tegen te benchmarken — een van de referentie-forecaster (referentie-forecaster) en een van een geautomatiseerd Python-systeem op Open-Meteo wave data. Onderzoek per 19 mei 2026, gebaseerd op Surfline, Stormsurf, NDBC/NOAA, ECMWF/WAM, SWAN/WW3 modeldocumentatie, Coastal Wiki, en Nederlandse/Belgische kustonderzoek.

---

## 1. De fysische parameters die er echt toe doen

### 1.1 Significante golfhoogte (Hs) — wat het wel en niet zegt

Significant wave height (Hs, ook wel SWH of Hm0) is de **gemiddelde hoogte van de hoogste een‑derde van de golven** in een sea state, of moderner gedefinieerd als `4·√(spectrale variantie)`. De twee definities verschillen typisch maar enkele procenten. Hs beschrijft een gemiddelde over een tijdsfenster (NDBC gebruikt 20 min; ECMWF representeert een 3-uurs gemiddelde over een 30×30 km grid-cel) — het is dus *geen* hoogte van een individuele golf.

Kerninzichten:
- Bij een Rayleigh-verdeling is de **maximum-golf in een 3-uurs sample typisch ~1.85·Hs**, en de gemiddelde hoogste 10% (H1/10) ≈ 1.27·Hs.
- Hs vermengt *alle* energie aan de boei: een 1.5 m Hs-readout kan een combinatie zijn van bv. 0.9 m N-windsea + 0.6 m W-restswell, en is dus niet hetzelfde als 1.5 m "pure swell".
- Mark Sponsler (Stormsurf) benadrukt: "The error most folks make is they look at significant Sea Height and period. That number provides the sum of all energy hitting the buoy. If there are 3 swells in the water, the significant sea height adds them all together." Wat je wilt is **pure swell height per partitie**.

Bron: [NDBC FAQ: wave calculations](https://www.ndbc.noaa.gov/faq/wavecalc.shtml), [Wikipedia: Significant wave height](https://en.wikipedia.org/wiki/Significant_wave_height), [Stormsurf services overview](https://www.stormsurf.com/page2/services/about.html).

### 1.2 Peak period (Tp), mean period (Tm/Tm02), zero-crossing period (Tz)

| Maat | Definitie | Gebruik |
|---|---|---|
| **Tp** | Periode bij de piek van het spectrum (frequency van max E) | Belangrijkste single-getal voor surfability |
| **Tm / Tm02** | Spectraal gemiddelde periode (m0/m2) | Modelvergelijking, wave-steepness berekeningen |
| **Tz** | Zero-up-crossing — meet feitelijke "tijd tussen golven" | Boei-observatie, mean wave statistics |
| **Tm10 / energy period (Te)** | Energie-gewogen periode (m‑1/m0) | Wave-energy converters, energie-flux berekening |

Tp is altijd ≥ Tm en meestal ≥ Tz. Bij een smal spectrum liggen ze dicht bij elkaar; bij een breed spectrum (mixed sea) kan Tp 2–4 s hoger zijn dan Tm. Voor surfvoorspelling is **Tp het primaire signaal**, want het identificeert de dominante swell-partitie.

### 1.3 Waarom periode vaak belangrijker is dan hoogte voor surfability

De fundamentele relatie: **wave energy flux per meter golfkam** in diep water:

`P ≈ (ρ·g² / 64π) · Hs² · Te  ≈  0.49 · Hs² · Te  [kW/m]`

Energie schaalt dus *kwadratisch* met hoogte en *lineair* met periode — maar in de praktijk geeft periode een veel groter effect omdat lange-periode swell:
1. **Sneller reist en minder decay** kent (groep-snelheid ≈ g·T/(4π), in diep water);
2. **Dieper "voelt"**: deep-water grens is d > L/2. Voor T=18 s is L≈506 m, dus de golf voelt de bodem al bij ~250 m diepte; voor T=6 s is L≈56 m en voelt pas bodem bij ~28 m;
3. **Meer shoaling-versterking** ondergaat (Ks tot ~1.5 voordat hij breekt).

Surfline's klassieke vergelijking ([Forecasting Tutorial: Wave Period](https://www.surfline.com/surf-news/forecasting-tutorial-wave-period-explained/96751)):
- **3 ft @ 12 s** = 0.9 m Hs, ≈ 7.6 kW/m, L≈225 m → schoon, georganiseerd, surfbaar
- **6 ft @ 6 s** = 1.8 m Hs, ≈ 15.3 kW/m, L≈56 m → ziet er groot uit maar surft slecht (windsea, dicht-op-elkaar, choppy)
- **4 m @ 18 s** = ≈ 226 kW/m → "big-wave-day" niveau

Concreet: dezelfde 1 m Hs is bij Tp=14 s een totaal ander beest dan bij Tp=6 s.

### 1.4 Wavelength en groepsnelheid

In diep water: `L = g·T²/(2π) ≈ 1.56·T²` (m, met T in s). Groepsnelheid `Cg = ½·C = g·T/(4π) ≈ 0.78·T` m/s. Een 14 s swell reist met ~11 m/s ≈ 39 km/h door diep water.

In ondiep water (d < L/20): `C = √(g·d)`, dus alle frequenties reizen even snel — golven worden niet meer dispersief en het spectrum verandert van vorm.

Bron: [Coastal Wiki: Shallow-water wave theory](https://www.coastalwiki.org/wiki/Shallow-water_wave_theory).

### 1.5 Wave direction en directional spread

**Mean wave direction (MWD)** is de gemiddelde aankomstrichting van energie bij de gegeven frequentie. **Directional spread (σθ)** geeft de spreiding (typisch 10–40°). Een lage spread (<15°) = "schone" swell uit één richting; hoge spread (>30°) = mixed/storm-sea karakter.

Waarom ±15° een spot kan maken of breken:
- Bathymetrie + refractie focust energie selectief. Een NW-spot kan optimaal werken op 295° maar bij 310° vol op het strand komen (closeout) en bij 280° de hoek missen.
- Strandsegmenten zijn zelden recht: kapen, pieren en zandbanken creëren windows van 20–30° waarbinnen het werkt.
- Surfline-forecaster: "There's a big difference between a 181° swell at 15 s 2 m and a 184° swell with a period one second less" ([Surfline: Surf Ratings explainer](https://support.surfline.com/hc/en-us/articles/36277684017819-Surf-Ratings-Colors)).

### 1.6 Wat een 2D spectrum (frequency × direction) toont wat Hs+Tp NIET toont

Het 2D spectrum E(f,θ) is de "röntgenfoto" van de zee. Hs en Tp zijn er afgeleide samenvattingen van.

Wat je extra ziet:
1. **Aantal swell-partities en hun individuele Hs/Tp/MWD** — bv. 0.8 m @ 14 s 290° + 0.5 m @ 7 s 30° (combo);
2. **Spectrum-breedte** — concentratie van energie. Smal piek = consistente sets met lange lulls (pointbreak-friendly); breed piek = mixed, vaker waves maar minder schoon (beachbreak-friendly volgens Surfline);
3. **Directional spread per frequentie** — een long-period component uit een ver-weg storm heeft meestal een veel kleinere spread dan windsea uit het lokale storm-veld;
4. **Bimodale verdelingen** — twee aparte pieken in periode verraden mixed seas; in 10–30% van alle zeestaten zijn double-peaked spectra aanwezig (academische literatuur, Hanson & Phillips 2001).

Surfline: "the LOTUS model has difficulty identifying separate swells in the wave spectrum... A small change in energy at 10 s could cause the 'peak' to jump down from 15 s. The spectra explain that surf doesn't necessarily change as much as expected in that scenario" ([Surfline Swell Spectra feature](https://support.surfline.com/hc/en-us/articles/20294130483099-Advanced-Swell-Swell-Spectra)).

---

## 2. Swell vs wind sea — de cruciale distinctie

### 2.1 Hoe onderscheidt een pro groundswell van wind sea?

Een professional gebruikt vier indicatoren tegelijk:
1. **Periode**: T > ~10 s = groundswell-domein, T < ~8 s = windsea-domein, 8–10 s = grijze zone;
2. **Wave age**: cp/U10 > 1.2 = swell (golf reist sneller dan lokale wind), cp/U10 ≤ 1 = wind sea (wind voedt nog actief);
3. **Directional spread**: smal (<20°) = groundswell, breed (>30°) = wind sea;
4. **Steepness**: H/L hoog (~0.04+) = wind sea, laag (~0.01) = groundswell ([NDBC steepness method](https://www.ndbc.noaa.gov/faq/windsea.shtml)).

### 2.2 Periode-cutoff: wat zeggen welke bronnen?

| Bron | Cutoff groundswell |
|---|---|
| Surfline forecasting tutorial | ≥ 12 s (best 15–20 s) |
| GetFoamie / Stormrider | 10–20 s |
| Surfertoday | "long period" = >10 s |
| Stormsurf (Sponsler) | ≥ 15 s = "long-period", ≤ 14 s = "short" |
| Surfline regional Europe | ≥ 12 s noemt men "ground" voor Atlantische context |

**De praktische cutoff is regio-afhankelijk:** wat in Hawaii een gewone dag is (T=14 s), is voor de Nederlandse kust een uitzonderlijke groundswell. Voor Noordzee-context wordt **T ≥ 9 s al als "groundswell-achtig"** gezien (Surfline UK / Stormrider), terwijl in Cornwall pas vanaf 12 s wordt gesproken over "kwaliteits-groundswell".

### 2.3 Waarom bouwt groundswell beter op een beachbreak — en wanneer juist niet?

De bathymetrie-interactie is de sleutel:
- Groundswells voelen de bodem in dieper water (refractie/shoaling begint verder offshore) en zijn vaak al gevormd voor ze de zandbanken bereiken;
- Korte windswells "voelen" alleen ondiepe zandbanken vlak voor de kust en peelen daar af in geulen en rips → vaak juist betere beach-break golven;
- **Cave:** héél lange perioden (18–20 s) bij beach-breaks "trekken te veel water van de bank" en sluiten af (closeout). Surfline: "really big periods of 18 to 20 seconds don't always equate to good surf… all that deep water energy drawing too much water off the reef before the wave breaks";
- Conclusie: voor beach-breaks is het zoete punt vaak **T = 9–13 s**.

### 2.4 Mixed seas — wat overheerst?

Voor de surfer geldt: **als beide partities binnen ~2 s liggen, mixen ze en wordt het rommelig**. Als ze ver uiteen liggen in periode én richting, ziet de surfer twee gescheiden sets. De pro forecaster splitst altijd partities en kijkt naar:
- *Welke partitie produceert de daadwerkelijke surfable breaking wave?* Vaak de langere-periode component, omdat die dieper voelt en dus eerst shoalt;
- *Welke partitie maakt de wind sea / chop op het oppervlak?* Korte periode, hoge steepness → degradeert de wave face;
- Academische context: het mixed-sea totaal heeft mildere extreme statistics dan windsea alleen (centraal-limietstelling vermengt de twee Gaussisch); juist daarom moet je partitioneren om "gevaar of kwaliteit" goed te zien (Springer Water Waves, 2020).

### 2.5 Fetch, duration, decay — hoe ontstaat groundswell?

Drie criteria voor een storm om groundswell te produceren:
- **Fetch**: lengte van het wind-veld over open water (typisch 500–2000 km nodig voor T>15 s);
- **Duration**: hoe lang de wind constant blijft (>12 h voor mature swell);
- **Wind speed**: U10 ≥ 25 knots voor productie van T>12 s; ≥ 40 knots voor T>16 s.

Tijdens decay verliest de korte component sneller dan de lange (energy ∝ T^n, en langere golven verliezen relatief minder energie per duizend km). Daarom zie je bij aankomst op een verre kust eerst de langste perioden ("forerunners" met T=18–20 s), gevolgd door de hoofdmoot (T=14–16 s), en als laatste de rommel.

---

## 3. Wind-effect op golfkwaliteit

### 3.1 Gradaties offshore → onshore

| Wind | Effect op wave face |
|---|---|
| Light offshore (1–5 kn) | Bijna glassy, lichte "grooming" |
| Moderate offshore (5–12/15 kn) | Sweet spot — schone faces, hold-up effect |
| Strong offshore (15–20 kn) | Spray-plume, paddle wordt zwaar, take-off lastig (wind drukt je tegen de face omhoog) |
| Extreme offshore (>25–30 kn) | Eigen chop tegen de wave-face, dichte spray; degradeert opnieuw |
| Light onshore (<8 kn) | Choppy maar surfbaar |
| Moderate onshore (8–12 kn) | Wave face stort vroegtijdig in, weinig defined shoulder |
| Strong onshore (>12 kn) | Blown-out, voor de meeste surfers onsurfbaar |

Bronnen: [Quiver: Offshore vs Onshore](https://www.quiversurf.app/learn/offshore-vs-onshore-wind-surfing), [Surfertoday: Why offshore winds are good](https://www.surfertoday.com/surfing/why-are-offshore-winds-good-for-surfing).

### 3.2 Bij welke snelheid wordt offshore problematisch?

Internationale consensus: **vanaf ~15 kn offshore beginnen problemen** (paddle, take-off), **vanaf ~25–30 kn** wordt zelfs offshore wind contra-productief omdat hij eigen wind-chop creëert tegen de wave-face en spray hindert het zicht. Voor Noordwijk in praktijk: 6–12 kn ZO (cross-offshore) is ideaal; >18 kn offshore is "ploeg-werk".

### 3.3 Glassy en sub-glassy

Echte glass = U10 ≈ 0 kn. Bij 1–3 kn zie je ripples maar de face blijft "silky". Een dawn-patrol of glass-off venster duurt typisch 30–90 min rond zonsondergang voordat de avond-bries opzet.

### 3.4 Side-shore en cross-offshore

- *Side-shore* (parallel aan kust): meestal vies — golven worden zijwaarts geschoven, lip valt onregelmatig;
- *Cross-offshore* (offshore + side-component): vaak de echte sweet spot omdat hij over de schouder van een peeling wave waait zonder de take-off zone direct te raken.

Voor Noordwijk (N–Z kustlijn, gericht op ~290°): pure offshore = O (90°); de echte cross-offshore voor de beste linkers/rechters loopt vaak ZO (135°) of NO (45°).

---

## 4. Tide / getij

### 4.1 Waarom elke spot een ander tij wil

De waterdiepte verandert bij elk vloed-/eb-cyclus 1.5–2.5 m op de Nederlandse kust. Die diepteverandering bepaalt:
- Welke zandbank/structuur op welke diepte ligt onder het wateroppervlak;
- Op welke afstand voor de kust de golf shoalt en breekt;
- Of het breekpunt over de bank ligt (rideable peel) of buiten de bank (closeout) of óp het droge zand (shore-dump).

Surfline (Nature.com blog): "Tides cause the water level to change baseline elevation at a given beach; waves are superimposed on this... how tides affect wave quality depends entirely on the bottom contour at a given break."

### 4.2 Tidal push / tidal flush

- **Tidal push** (incoming, low → high): extra waterstroom richting kust + hogere waterstand. Plymouth Univ. (Davidson, O'Hare, George) toonde aan dat **wave energy ~1 uur vóór hoogwater pieken** in tidal-range >7.5 m. Voor Noordzee (range 2 m) is het effect kleiner maar reëel;
- **Tidal flush** (outgoing, high → low): minder water tegen de bank, vaak hollower breaks, maar bij kustpieren en harbours kan een eb-stroom rip-currents versterken;
- **Rule of twelfths**: tide stijgt niet constant — in een 6-uurs semi-diurnal cyclus is de verdeling 1/12, 2/12, 3/12, 3/12, 2/12, 1/12 per uur. De middelste twee uren ("mid-flood / mid-ebb") hebben de sterkste stromingen.

### 4.3 Spring vs neap

- **Spring** (nieuw/vol maan): grote range (Noordzee 2.0–2.5 m), snelle stroming. Spot werkt kort op de gewenste tide-stand;
- **Neap** (eerste/laatste kwartier): kleine range (1.0–1.6 m), trage stroming. Mid-tide spots kunnen "de hele dag" werken;
- Voor de Noordzee: semi-diurnal, gemiddelde range Noordwijk ~1.6–2.2 m, spring ~2.3 m, neap ~1.4 m.

### 4.4 Tidal currents als probleem

Bij Noordzee-spots langs gestructureerde kust (pieren bij IJmuiden/Wijk aan Zee) kunnen vloed/eb-stromen 0.5–1 kn parallel aan het strand lopen — onhandig voor positionering, en bij springtij rond mid-tide kan het echt zwemwerk worden. Sponsler: "Strongest currents are at mid-tide when water volume per uur het hoogst is."

Bron: [Surfline: Tides and Surfing](https://www.surfline.com/surf-news/tides-and-surfing/1107), [Saltwater Science: How tides affect breaking waves](https://www.nature.com/scitable/blog/saltwater-science/how_the_tides_affect_breaking/).

---

## 5. Refractie, shoaling, en zee-bodem effecten

### 5.1 Wat verandert er aan een golf van diep naar ondiep?

Onveranderlijk: **periode T blijft constant**. Wel verandert:
- Wavelength L wordt korter;
- Phase celerity C wordt lager;
- Group velocity Cg neemt af, dus de **energie-flux moet zich concentreren** → hoogte neemt toe (shoaling-coëfficiënt Ks tot ~1.5 vlak voor breken);
- Wave direction krijgt component-shift door **refractie** (crests aligneren met dieptecontouren).

Shoaling start bij `d < L/2` (transitioneel) en wordt dominant bij `d < L/20`. Breekt typisch bij `H/d ≈ 0.78` (depth-limited, Miche-criterium).

Bron: [Coastal Wiki: Wave transformation](https://www.coastalwiki.org/wiki/Wave_transformation), [Wikipedia: Wave shoaling](https://en.wikipedia.org/wiki/Wave_shoaling).

### 5.2 Refractie rond obstakels — focussing en defocussing

- **Headlands en ondiepe banken concentreren** energie (focussing) — verklaart waarom kapen consistent groter zijn dan baaien onder dezelfde swell;
- **Baaien en diepe geulen** spreiden energie (defocussing) — verklaart waarom hoeken na een bank ineens "dood" zijn.

Voor Noordwijk: de strandlijn loopt vrij recht NNO–ZZW, géén grote kapen. Lokale variatie komt van *zandbank-systemen* en de Noordwijk Pier (kunstmatige obstakel).

### 5.3 Het "Vlaamse banken effect" en swell-decay langs de Noordzeekust

Het systeem van zandbanken op het Belgisch continentaal plat (Vlaamse Banken, Westhinder, Zeebrugge-banken) ligt op 5–20 m waterdiepte en strekt zich uit tot >30 km offshore. Voor een W of NW swell die vanuit de Atlantic via Het Kanaal komt:
- Lange-periode (T > 12 s) componenten **voelen al ver offshore de banken** (deep-water grens L/2 = 113 m voor T=12 s; bank op 15 m diepte = ruim in "feels-bottom" zone);
- Resultaat: **substantiële bottom-friction dissipatie en refractie** voordat de swell de kust bereikt. SWAN-modelstudies (Lifewatch, Flanders Hydraulics) tonen aan dat een 2 m offshore Hs op de banken al 30–50% kan verliezen voor het Belgische strand;
- Voor de Nederlandse kust: vergelijkbaar maar minder uitgesproken effect via de Zeeuwse banken en zandbanken voor de Hollandse kust. Een swell verliest dus al **5–8 km voor Noordwijk** energie aan bottom friction;
- Praktisch gevolg: **Cornwall krijgt 3 m / 14 s wat in Noordwijk aankomt als 0.8 m / 9 s** (sterk gedissipeerd, korter periode-spectrum). Dit is geen modelartefact maar fysica.

Bron: [Meetnet Vlaamse Banken](https://www.bodc.ac.uk/resources/inventories/edmed/report/5619/), [MDPI Water: Belgian coastal resilience](https://www.mdpi.com/2073-4441/14/13/2104).

### 5.4 Waarom een swell die zuidwaarts langs de kust loopt hoogte verliest

Drie redenen:
1. **Refractie buigt energie zeewaarts** wanneer de coastline-oriëntatie verandert (NL-kust draait ZW bij Westkapelle);
2. **Bottom friction** is cumulatief over de afstand;
3. **Geometrische spreading**: de oorspronkelijke storm-fetch is meestal eindig en zonder constante nalevering "verdunt" de swell langs de kust.

---

## 6. Noordzee specifiek

### 6.1 Wave climate

Bronnen: Rijkswaterstaat MPN-station (Noordwijk Meetpost, 52°16′N 4°17′E, 1993–2009), MARIN, Wetterzentrale archieven, ECMWF ERA5 reanalysis.

| Parameter | Typisch Noordwijk |
|---|---|
| Gemiddelde Hs (jaar) | 1.1–1.4 m |
| Mediaan Hs zomer | 0.7–1.0 m |
| Mediaan Hs winter | 1.4–1.8 m |
| Gemiddelde Tp | 5–7 s (windsea) |
| Tp bij goede dagen | 8–11 s |
| Tp bij "evenement-groundswell" | 11–13 s (zeldzaam, paar keer per jaar) |
| Dominante richtingen | ZW (Atlantic via Kanaal) en NNW (Noordzee fetch) |
| 1-in-jaar storm Hs | 5–6 m |
| 1-in-10000 jaar Hs (Hydraulische randvoorwaarden) | ~12–13 m |

### 6.2 UK vs NL — waarom een wereld van verschil

- **Cornwall / Devon**: directe Atlantic-blootstelling, swell-window 180°–315°, regulier Hs 2–4 m bij Tp 11–15 s. Continental shelf ligt verder offshore dan Frankrijk maar dichterbij dan Ierland → mooi mix van power en gefilterde kwaliteit;
- **Yorkshire / NE Engeland**: Noordzee-blootstelling vergelijkbaar met NL maar met kortere fetch-windows; toch reefbreaks (Cayton Bay, Scarborough);
- **Nederlandse kust**: 100% Noordzee-domein. Geen directe Atlantic-toegang. Atlantische swell moet via het Kanaal en de Vlaamse Banken passeren, verliest >50% Hs en >2–3 s periode onderweg.

### 6.3 Hoe vaak komt echte groundswell (T>10 s) voor in NL?

Op basis van MPN/Europlatform-statistieken en surf-forecast archieven:
- **T ≥ 10 s**: ~5–10% van de tijd in winter (oktober–maart), <2% in zomer;
- **T ≥ 12 s**: ~1–2% van de tijd in winter, vrijwel nooit in zomer;
- **T ≥ 14 s**: maybe 3–8 keer per jaar, gekoppeld aan zware NW-storm 24–48 h eerder.

### 6.4 Wat is een realistische "goede dag" voor Noordwijk?

Internationale consensus van Surfline, Surf-Forecast.com, Wannasurf:

| Component | "Goede dag" Noordwijk |
|---|---|
| Hs | 0.8–1.5 m |
| Tp | 8–11 s (uitzonderlijk: ≥12 s) |
| Swell direction | 285°–325° (WNW–NW) |
| Wind | 5–15 kn uit 90°–180° (O–ZZO), dus offshore tot cross-offshore |
| Wind speed | <15 kn (anders te veel chop op de face) |
| Tide | spot-specifiek; rond mid-tide flood is veilige default |
| Periode-kwaliteit | swell-partitie significant boven windsea-partitie (E_swell / E_total > 0.5) |

Een "klassieke" goede sessie: 1.0 m @ 10 s 300° met 8 kn ZO bij opkomend tij rond 11 uur 's ochtends — gebeurt orde-grootte 15–25 dagen per jaar.

---

## 7. Voorspellingsmodellen — wat zit eronder?

### 7.1 De vier grote modellen

| Model | Eigenaar | Schaal | Roosters |
|---|---|---|---|
| **WAVEWATCH III (WW3) / GFS Wave** | NOAA NCEP | Globaal | 0.25°–0.5° (~25–50 km), regionale nests tot 4 km |
| **ECMWF WAM** | ECMWF | Globaal | 0.125° (~13 km) deterministisch HRES; 0.25° ensemble |
| **ICON Wave** | DWD (Duitsland) | Europa regionaal | ~0.07° (~7 km) |
| **SWAN** | TU Delft / NCEP NWPS | Coastal/nearshore | 100 m – 4 km, vaak nested in WW3 of WAM |

Open-Meteo combineert ICON Wave (DWD), ECMWF WAM en Météo-France/Copernicus modellen — **niet** WW3 op het moment van schrijven (GitHub issue #415 staat nog open).

### 7.2 De bekende zwakte van GLOBAL modellen voor coastal beach breaks

1. **Resolutie**: 13–25 km grid is te grof voor lokale bathymetrie. De Noordwijk-cel in WAM bestrijkt ~13×13 km — dat is van halverwege Zandvoort tot voorbij Katwijk;
2. **Bathymetrie**: globale modellen gebruiken sterk geinterpoleerde diepte-kaarten. Specifieke zandbanken (typisch 50–500 m wide) zijn onzichtbaar voor het model;
3. **Near-shore refraction is niet meegerekend** in een globaal grid omdat de cel-rand al voor de kust ligt;
4. **Bottom friction parameterisatie** is generiek (JONSWAP-formule) en niet gekalibreerd op specifieke sediment-eigenschappen;
5. **Currents (tidal en residual) worden vaak niet meegekoppeld** behalve in coupled-mode runs;
6. **Wind input** komt van een ander model (GFS/ECMWF atmosphere) met eigen errors die downstream propageren.

State-of-the-art mitigatie: **nested SWAN** of **SWAN-SWASH** op coastal grids (100–500 m) met boundary conditions uit WW3/WAM. NOAA NWPS draait dit op 1 nmi (~1.8 km) downscaled tot 10 m focus-areas. Voor NL is er geen publieke real-time SWAN-feed; Rijkswaterstaat heeft wel intern model-data.

Bron: [SWAN-WW3 coupling (NOAA WW3 workshop)](https://polar.ncep.noaa.gov/waves/workshop/pdfs/wwws_2.2.pdf), [MDPI Marine Science: WW3 & SWAN intercomparison](https://www.mdpi.com/2077-1312/13/8/1450).

### 7.3 Waarom pro forecasters lokale boei-data nodig hebben

- **Bias-correctie**: elk model heeft een spot-specifieke bias (vaak −10% tot −30% Hs in nearshore zones);
- **Now-cast**: boei toont *huidige* zee-staat, model toont *voorspelde*. Boei is bron-of-truth voor de eerste 6–24 h;
- **Spectrum-validatie**: boei levert het echte 2D-spectrum, model levert geparameteriseerd spectrum;
- **Anomalie-detectie**: als model en boei stevig uiteenlopen, weet je dat er iets mis is (verkeerde wind-input, missing swell-partitie, model-instability).

Op de NL kust is de relevante meting **Europlatform (boei K13a / station Eierlandse Gat / IJmuiden Munitiestort)** of **MPN Noordwijk Meetpost** (Rijkswaterstaat). Open-Meteo neemt deze niet automatisch mee in zijn API-output.

---

## 8. Wat doen pro forecasters anders dan een dom model?

### 8.1 Mens + model + boei + local knowledge

1. **Multi-model ensemble**: pro's kijken naar WW3 + WAM + ICON simultaan en wegen verschillen;
2. **Spectrum-lezen**: bij twijfel pakken ze het 2D spectrum erbij om te zien welke partitie écht binnen komt;
3. **Synoptiek**: ze kijken naar de drukkaarten en storm-fetch direct om de modeluitkomst te kruisvalideren ("klopt deze 14s swell met de storm 800 nm NW op woensdag?");
4. **Local rules**: "deze swellrichting werkt niet bij high tide", "die zandbank verzandt na elke ZW-storm", "dit spot opent pas vanaf 1.2 m Hs". Surfline gebruikt 35 jaar manual annotations als ML-trainingdata, maar het mens-correctief blijft nodig;
5. **Wind-timing**: pro's voorspellen niet alleen "of" maar "wanneer" de wind kantelt — een 3-uurs venster met juiste wind kan een hele dag maken.

### 8.2 Bekende failure modes van automatische surf scores

Surfline zelf erkent ze ([Surf Ratings: Colors](https://support.surfline.com/hc/en-us/articles/36277684017819-Surf-Ratings-Colors)):
1. **"Good" en "Epic" vereisen mens** — automated stopt bij "Fair-to-Good";
2. **Tide-effects en residual chop worden gemist** zonder forecaster-input;
3. **Subtiel verschil in swelldirection (3°) of periode (1s)** kan niet worden gevangen door een hoogte-wind score;
4. **Spot-specifieke threshold**: 0.3 m in Hawaii = vlak; 0.3 m in een windluwe Nederlandse bocht kan een spot openen;
5. **Bathymetrie-anomalieën** (zandbank verschuift na storm) worden niet bijgewerkt zonder check ter plekke;
6. **Glassy windows** rond zonsopgang/-ondergang worden gemist als wind-input gemiddeld is over 3 h.

---

## 9. BENCHMARK-CRITERIA

**Concrete checklist: wat moet een geautomatiseerde NL-surf-voorspelling minimaal bevatten om vergelijkbaar te zijn met een pro forecaster zoals referentie-forecaster van de referentie-forecaster?**

### A. Verplichte input-parameters (uit modeldata)

| # | Parameter | Reden |
|---|---|---|
| A1 | Hs (significant wave height) van *totaal* sea | Basis-maat |
| A2 | Hs *per partitie* (minimaal twee: swell + windsea) | Voorkomt overschatting bij mixed seas |
| A3 | Tp (peak period) per partitie | Identificatie van swell vs windsea |
| A4 | Tm of Tm02 als secundaire periode-maat | Robuustheid bij brede spectra |
| A5 | Mean wave direction per partitie | Spot-match (window 285°–325° voor Noordwijk) |
| A6 | Directional spread | Kwaliteits-indicator (smal = beter) |
| A7 | U10 (10-m wind speed) | Wave-face quality |
| A8 | Wind direction | Offshore/onshore beoordeling |
| A9 | Tidal height op spot-locatie | Spot-tide preference matching |
| A10 | Tide-stage (rising/falling) en tide-currents | Tidal push en stroming-risico |

### B. Verplichte afgeleide grootheden

| # | Berekening | Drempel |
|---|---|---|
| B1 | Wave energy flux P = 0.49·Hs²·Te [kW/m] | Voor energie-vergelijking over dagen |
| B2 | Wave age cp/U10 | >1.2 = swell; <1.0 = windsea |
| B3 | Wave steepness Hs/L0 | <0.025 = swell-character; >0.04 = wind-character |
| B4 | Swell-energie ratio E_swell / E_total | >0.5 = swell-dominated dag |
| B5 | Wind-component projectie op kust-normaal | Onshore/offshore strength in kn |
| B6 | Cross-shore wind-component | Side-shore impact |

### C. Spot-specifieke calibratie (Noordwijk in dit geval)

| # | Criterium |
|---|---|
| C1 | Swell-window 285°–325° (WNW–NW) — anders penalty |
| C2 | Wind-window: best 90°–180° (O–ZZO), tolereerbaar tot 45° en 225° |
| C3 | Minimum Hs voor surfbaarheid: 0.6 m totaal en 0.4 m in swell-partitie |
| C4 | Periode-bonus: Tp ≥ 9 s = "kwaliteit", Tp ≥ 11 s = "uitzonderlijk" |
| C5 | Wind-cap: U10 > 18 kn = blown-out, ongeacht richting |
| C6 | Tide-preference (heuristic): mid-flood ± 1.5 h optimaal voor de meeste NL spots |
| C7 | Closeout-risk flag: Hs > 1.8 m én Tp < 8 s = waarschijnlijk closeout windsea |

### D. Validatie en kwaliteitsbewaking

| # | Praktijk |
|---|---|
| D1 | Vergelijk modeloutput met dichtstbijzijnde boei (Europlatform / IJmuiden Munitiestort / MPN Noordwijk) — Hs en Tp bias-correctie |
| D2 | Track forecast-error per spot over tijd (rolling RMSE) — leer welke condities het model systematisch mist |
| D3 | Multi-model check: vergelijk Open-Meteo (ICON+WAM) met minstens één tweede bron (Stormglass, WindGuru/GFS Wave, Surfline) |
| D4 | Spectrum-check waar mogelijk: vlag dagen met bimodale spectra apart |
| D5 | Wind-tijdresolutie: minimaal 1-uurs voorspelling, niet 3-uurs gemiddeld |
| D6 | "Good day" definitie expliciet maken en niet alleen score-getal: combinatie van Hs, Tp, MWD, U10, tide, allemaal binnen spot-windows |

### E. Uitvoer / communicatie (waar pro forecasters mee winnen)

| # | Eigenschap |
|---|---|
| E1 | Time-window aanduiding ("06:00–10:00 best") in plaats van dag-gemiddeld score |
| E2 | Identificatie van *welke* parameter beperkend is ("wind kantelt om 11u onshore") |
| E3 | Onzekerheid-indicatie wanneer modellen uiteenlopen of partitie-detectie onzeker is |
| E4 | Tide-shift overlay: laat zien hoe de score evolueert binnen het tij-window |
| E5 | Spot-vergelijking: laat zien of een ander spot in regio betere score heeft |

### F. Wat een dom model gegarandeerd mist (failure modes om bewust te accepteren)

1. **Zandbank-verschuivingen** (vereisen veldobservatie);
2. **Hyperlokale wind-eddies** door duinen/pieren;
3. **Residual chop** na storm (oppervlakte-conditie blijft slecht 6–12 h na windafname);
4. **Glassy windows** korter dan model-tijdresolutie;
5. **"Swell direction sweet spot" verschillen <5°**;
6. **Crowd-factor** (irrelevant voor wave-fysica, wel voor "dag-rating").

---

## Conclusie

Een goede surfvoorspelling is een **gewogen integratie** van: (1) golf-spectrumstructuur, niet alleen Hs/Tp samenvattingen; (2) wind-vector op uur-resolutie; (3) tide-stand op spot-niveau; (4) bathymetrie-aware refractie/shoaling; (5) bias-gecorrigeerde model-output gevalideerd tegen boeien. Pro forecasters voegen daar local knowledge en synoptisch oordeel aan toe.

Voor het Open-Meteo–gedreven Python-systeem is de cruciale vraag bij benchmarking tegen de referentie-forecaster/referentie-forecaster dus niet *"klopt de voorspelde Hs?"* maar *"voorspelt het systeem de juiste combinatie van parameters in het juiste tijdvenster, met expliciete erkenning van zijn beperkingen ten opzichte van een mens met spectrum-inzicht en lokale ervaring?"*

De checklist in sectie 9 (BENCHMARK-CRITERIA) is hiervoor het concrete operationele instrument.

---

## Belangrijkste geraadpleegde bronnen

**Surfline (forecaster blog & support):**
- [Forecasting Tutorial: Wave Period Explained](https://www.surfline.com/surf-news/forecasting-tutorial-wave-period-explained/96751)
- [Feature Spotlight: Forecast Swell Spectra](https://www.surfline.com/surf-news/feature-spotlight-forecast-swell-spectra/197081)
- [How and Why to Use Buoy Swell Spectra](https://www.surfline.com/surf-news/use-buoy-swell-spectra-score/208713)
- [Groundswell vs. Windswell](https://www.surfline.com/surf-news/groundswell-vs-windswell/2439)
- [Tides and Surfing](https://www.surfline.com/surf-news/tides-and-surfing/1107)
- [Surf Ratings & Colors](https://support.surfline.com/hc/en-us/articles/36277684017819-Surf-Ratings-Colors)
- [Advanced Swell — Swell Spectra](https://support.surfline.com/hc/en-us/articles/20294130483099-Advanced-Swell-Swell-Spectra)
- [Machine Learning for Surf Forecasting (Ben Freeston, Surfline Labs)](https://medium.com/surfline-labs/machine-learning-for-surf-forecasting-4a007f13b3e3)

**Stormsurf (Mark Sponsler):**
- [About Stormsurf — services & methodology](https://www.stormsurf.com/page2/services/about.html)
- [Video Forecast](https://www.stormsurf.com/video/video_forecast.shtml)
- [Surf Science: Create your own forecast with Stormsurf](https://surfscience.com/topics/waves-and-weather/forecasting/create-your-own-surf-forecast-with-stormsurf/)
- [Lookout: The Surf Whisperer profile](https://lookout.co/surfing-mark-sponsler-stormsurf-forecasts-have-earned-a-devoted-following-among-big-wave-surfers/story)

**NOAA / NDBC educational:**
- [NDBC: How are Hs and period calculated?](https://www.ndbc.noaa.gov/faq/wavecalc.shtml)
- [NDBC: Wind-sea and swell estimation](https://www.ndbc.noaa.gov/faq/windsea.shtml)
- [NOAA WW3 SWAN coupling workshop](https://polar.ncep.noaa.gov/waves/workshop/pdfs/wwws_2.2.pdf)
- [Modeling nearshore wave processes (van der Westhuysen, NOAA NCEP MMAB-298)](https://polar.ncep.noaa.gov/mmab/papers/tn298/MMAB_298.pdf)

**Academische bronnen:**
- [MDPI Marine Science: WW3/SWAN intercomparison (2025)](https://www.mdpi.com/2077-1312/13/8/1450)
- [Taylor & Francis: WAM, SWAN, WW3 in Finnish archipelago](https://www.tandfonline.com/doi/full/10.1080/1755876X.2019.1633236)
- [arXiv: Extreme wave statistics in co-propagating windsea and swell](https://arxiv.org/pdf/1904.07207)
- [Hanson & Phillips (2001): Operational windsea/swell separation method](https://journals.ametsoc.org/view/journals/atot/18/12/1520-0426_2001_018_2052_aomfsw_2_0_co_2.xml)
- [ScienceDirect: Probabilistic modelling of extreme storms along the Dutch coast](https://www.sciencedirect.com/science/article/abs/pii/S0378383913002159)
- [ResearchGate: Spectral wave climate of the North Sea](https://www.researchgate.net/publication/222033022_Spectral_wave_climate_of_the_North_Sea)

**Coastal physics / wiki:**
- [Coastal Wiki: Wave transformation](https://www.coastalwiki.org/wiki/Wave_transformation)
- [Coastal Wiki: Shallow-water wave theory](https://www.coastalwiki.org/wiki/Shallow-water_wave_theory)
- [Wikipedia: Wave shoaling](https://en.wikipedia.org/wiki/Wave_shoaling)
- [Wikipedia: Significant wave height](https://en.wikipedia.org/wiki/Significant_wave_height)
- [Wikipedia: Surf forecasting](https://en.wikipedia.org/wiki/Surf_forecasting)
- [Nature/Scitable: How tides affect breaking waves](https://www.nature.com/scitable/blog/saltwater-science/how_the_tides_affect_breaking/)

**Open-Meteo & modellen:**
- [Open-Meteo Marine Weather API](https://open-meteo.com/en/docs/marine-weather-api)
- [Open-Meteo ECMWF Forecast API](https://open-meteo.com/en/docs/ecmwf-api)
- [Open-Meteo: New weather and marine models integrated](https://openmeteo.substack.com/p/new-weather-and-marine-models-integrated)
- [GitHub issue: Add other wave models (WW3 request)](https://github.com/open-meteo/open-meteo/issues/415)

**Belgische / Nederlandse kust:**
- [Meetnet Vlaamse Banken (BODC)](https://www.bodc.ac.uk/resources/inventories/edmed/report/5619/)
- [MDPI Water: Belgian West Coast coastal resilience](https://www.mdpi.com/2073-4441/14/13/2104)
- [Belgian Marine Spatial Plan summary](https://www.health.belgium.be/sites/default/files/uploads/fields/fpshealth_theme_file/19094275/Summary%20Marine%20Spatial%20Plan.pdf)

**Wind & getij praktijk:**
- [Quiver: Offshore vs Onshore Wind](https://www.quiversurf.app/learn/offshore-vs-onshore-wind-surfing)
- [Surfertoday: Why offshore winds are good](https://www.surfertoday.com/surfing/why-are-offshore-winds-good-for-surfing)
- [Surfertoday: Spring and neap tides explained](https://www.surfertoday.com/surfing/spring-and-neap-tides-explained)
- [Conatus Surf Club: No best tide for surfing](https://www.conatussurfclub.com/blog/how-tides-affect-surfing-and-the-best-tides-for-surfing)

**Spot-specifieke gidsen:**
- [Stormrider Surf Guides: England](https://www.stormrider.surf/country/england)
- [Surf-Forecast: Wijk aan Zee Noordpier](https://www.surf-forecast.com/breaks/Wijkaan-Zee-Noordpier)
- [Surfline: Noordwijk Surf Report](https://www.surfline.com/surf-report/noordwijk/584204204e65fad6a77095ed)
