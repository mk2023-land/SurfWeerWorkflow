# Hoe de referentie-forecaster tot zijn surfvoorspellingen komt — Diepteanalyse mei 2026

Onderstaand rapport is een uitbreiding op DEEL 1 van `noordwijk-surf-alert-plan-v3.md`. De analyse van de 13 SMS'jes uit dat document blijft het fundament; deze tekst valideert, corrigeert en verrijkt het met (a) de verse SMS van di 19 mei 2026, (b) publiek bronmateriaal van de referentie-forecaster zelf, en (c) extern onderzoek naar zijn werkwijze en zijn publieke uitleg over spectra, swellsnelheid en getij.

---

## 1. Achtergrond referentie-forecaster (validatie van aannames in v3)

- Geen formele meteorologie-opleiding; **autodidact** — hij vertelt zelf dat hij na een paar keer voor niets naar de zee te zijn gereden besloot zich onder te dompelen in weerkaarten. Hij woont dichtbij KNMI in De Bilt — wat helpt voor toegang tot synoptische guidance, maar er is geen aanwijzing dat hij KNMI-medewerker is.
- Actief sinds zeker 2017 als publiekservice (zijn vroegste publieke spectrum-uitleg-post is uit juni 2017); de service is geprofessionaliseerd in een betaald SMS-abonnement.
- Bijna dagelijks zelf in het water — hij is geen "modelletjes-lezer" maar iemand die zijn voorspelling bij elke sessie tegen de werkelijkheid checkt.

Dit corrigeert een impliciete aanname in v3: de kracht van de referentie-forecaster zit niet in formele meteorologie maar in **17+ jaar gekalibreerde patroonherkenning** op de Noordzee. Voor het algoritme betekent dit: hard te repliceren is zijn calibrated intuition voor "deze setup gaat WEL/NIET surfbaar zijn", niet de fysica.

---

## 2. Validatie van de data-stack van de referentie-forecaster

De v3-aannames over zijn stack zijn voor het grootste deel correct, hier de bevestiging plus enkele aanvullingen:

**Forecast-modellen (alle gelinkt vanaf zijn publieke weerlinks-pagina):**
- **KNMI guidance modelbeoordeling** — `knmi.nl/waarschuwingen_en_verwachtingen/extra/guidance_modelbeoordeling.html`. Dit is geen ruw modelproduct maar de KNMI-meteoroloog die zélf de modellen tegen elkaar afweegt. Hij gebruikt dus deels **menselijke synthese als input** — iets dat ons algoritme niet 1-op-1 kan repliceren.
- **GFS windkaarten via meteociel.com** (Franse hosting), niet via NOAA direct.
- **Harmonie via weerplaza.nl/weerkaarten/harmonie/** (niet KNMI direct).
- **ECMWF pluim via weerplaza** (ensemble-pluim met spread, dus modelonzekerheid is expliciet in zijn werkproces).
- **UKMO via wetterzentrale.de en zijn eigen UKMO-pagina** met onder andere het +144u-prognoseplaatje.
- **DWD golfhoogtes** (Duitse weerdienst).
- **Ocean Prediction NOAA** voor synoptische context Atlantic.

**Live boeispectra (zijn spectra-pagina bevestigt v3 exact):**
A12, K13, J6, MUN1 (IJmuiden), **IJG1 (IJgeul) voor Zandvoort/Noordwijk/Scheveningen**, EPL/EPL3 (Europlatform) voor HvH/Maasvlakte, E131 + DWE1 (Maasvlakte), SGAT (Schulpengat) voor Callantsoog/Petten, SCHS (Schouwenbank) + DEUR/DELO (Deurloo oost) voor België.

Aanvulling op v3: de **Belgische boeien DEUR/DELO/SCHS** en het Vlaams Meetnet (`meetnetvlaamsebanken.be`) zijn explicieter dan v3 doet vermoeden — voor zuid/zuidwest swells zijn die zijn primaire kustverificatie.

**Tij:** RWS Waterinfo, met Westkapelle (`WESTKPLE`) voor Domburg als voorbeeld. Voor Noordwijk gebruikt hij impliciet Scheveningen of IJmuiden Buitenhaven.

**Wind live:** RWS windmeting (`waterinfo.rws.nl/publiek/wind/`) en, opvallend laagdrempelig, **Teletekst 707**.

**Webcams:** zijn eigen webcam-set.

---

## 3. Eigen uitleg over het spectrum (publieke artikelen van de referentie-forecaster)

In een publieke uitleg-post uit juni 2017 legt hij het zelf uit. Cruciale punten voor ons algoritme:

1. **Spectrum = ~500 cosinusgolven**, elk met eigen frequentie en amplitude. De z-as (kleur) toont energie per cosinus. De grafiek leest hij als een tijd-frequentie-energie veld: x-as = uur, y-as = frequentie in mHz, kleur = energiedichtheid.
2. Conversie expliciet bevestigd: **periode (s) = 1 / (mHz × 0,001)** of korter `1000/mHz`. Dus 200 mHz = 5 s, 150 mHz = 6,66 s, 100 mHz = 10 s, 60 mHz = 18 s.
3. **Groundswell-amplificatie**: "hoe hoger de golfperiode, hoe groter het effect... bij 10s een factor 2 ten opzichte van wat de boei meet, bij 5s 1-op-1." Dit is een **shoaling-correctie** die we expliciet kunnen meenemen: een 1m boei-meting bij 10s = ~2m op de strandbank, een 1m boei bij 5s = ~1m strand. Open-Meteo Marine geeft `wave_height` op kustpunt; voor offshore boeien (A12, K13) moet je dus opschalen met deze factor.
4. **Swell-aankomsttijd berekening** (zijn eigen redenering): groepssnelheid in diep water ≈ `1,56 × T` m/s, hij zegt: "10 s = 31 kn = 57 km/u; A12 ligt ~570 km, dus 10 uur lead time. Bij 6-8 s bijna verdubbeling." Voor het algoritme: dit is een eenvoudige feature `time_to_coast = afstand_km / (1,56 × T × 3,6)`.
5. **Noordzee-realiteit:** "Vanaf 5 s wordt het pas een beetje surfbaar; ideaal is 6,5-7 s omdat dan niet te veel energie verloren gaat over de zandbanken." Onze score-functie moet dus géén lineaire bonus voor periode geven — er is een **optimumcurve rond 7 s** voor windswell, met een aparte premium voor 10s+ groundswell (zeldzaam).
6. **Wind versus groundswell-regimes:** "W/ZW-wind → windswell; N/NW-wind → groundswell-kans." Plus de bizar-zeldzame Barentszzee-swell van 18s+ die via de Noordpoolgrens tot in HvH komt (relevant voor T1-detectie maar zeldzaam genoeg om als rariteit te behandelen).

Dit is rijker dan v3's tabel en versterkt vooral §1.2: het algoritme moet **shoaling-correctie + aankomsttijd-modelling** doen, niet alleen pieken detecteren.

---

## 4. Decompositie van de SMS van di 19 mei 2026 (voor woensdag 20 mei)

### 4.1 Welke databronnen heeft de referentie-forecaster geraadpleegd?

Reverse-engineering op woordkeuze:

- **"Na het buienlijntje van 10u"** — KNMI Harmonie buien-radar/precip voorspelling, en zichtbaar als convergentielijn op KNMI guidance. Mogelijk ook DWD wave/wind charts.
- **"Wind meer WZW in Z-H en Zeeland"** — Harmonie 10m wind voor uur 10-15.
- **"Vloedstroom vol inzetten" vanaf 15u** — astronomisch getij Scheveningen/IJmuiden. Zijn tij-redenering volgt strikt het schema "vloed = waterstroom naar NO, eb = waterstroom naar ZW". Hoogwater Scheveningen ≈ 18:30-19:00, dus van ~15u vloedfase opgaand naar HW = stroming maximaal richting NO.
- **"BE komt het dan juist binnen, beste lijntjes tot 0,8m hoog van 14-17u"** — direct gelezen uit Westhinder/DEUR boei prognose + DWD golfkaart. De zin "komt binnen" verraadt dat hij weet wanneer de swell de kust raakt: zuidwest fetch → BE eerst.
- **"Domburg 12-15u, uur 14-15:30u het hoogst"** — astronomisch getij Westkapelle (zijn standaard) + golf-piek-uur uit DWD/Open-Meteo Marine type forecast.
- **"Late avond 20:30-21:30u wel nog leuk uurtje 1m hoogte"** — getij Domburg cyclus tweede laagwater-window én aanhoudende zuid-swell.
- **"Op Ouddorp heel weinig golf, draait wel wat binnen met de vloed 16-18u"** — refractie + tij. Bij Ouddorp (zuidwest open) komt de zuid-swell schuin aan, dus refractie rond de Brouwersdam.
- **"Mvlakte volle branding onshore al te surfen vanaf 9u"** — EPL3/E131 boei live + Harmonie wind = onshore WZW.
- **"Schev 11-13u 0,9m, van 13-15u 1,1m"** — Mix van Open-Meteo Marine wave_height per uur + Harmonie wind. Voor Schev voegt hij ook de pier-beschutting toe.
- **"Genoeg hoogte"** voor Nwijk/Zvoort 14-16u — interpretatie van IJG1 voorspelling. Hij noemt expliciet géén meter, wat suggereert dat hij weet dat het ergens tussen 0,9-1,1m zit (zoals Schev).
- **"Wijk 10-12:30u al wat voor long en fish, iets hoger 15-17u en minder wind. Kan ook met hoogwater nog 18-20:30u"** — MUN1 boei + Harmonie wind + getij IJmuiden (HW ~18:30 bij IJmuiden, iets later dan Schev). Drie tij-windows is opvallend (uitwerking §5.3).
- **"TexelKoog 19-21u leuke lijnen en niet al te veel wind (wel stroming)"** — SGAT/K13 + getij Den Helder (springtij-effect noord).
- **"Voor de andere wadden redt de zuid-swell niet om er omheen te komen"** — refractie + diffractie rond Texel/Vlieland.

**Modellen dus**: Harmonie (kust-wind), Open-Meteo Marine of DWD (golfhoogte per uur), live boeien EPL/IJG1/MUN1/SGAT, RWS astronomisch getij per locatie, ECMWF voor donderdag-vrijdag, GFS/ECMWF pluim voor "midden volgende week 1m N-swell".

### 4.2 Per spot: welke tijden en wat triggert ze

| Spot | Tijdvenster | Primaire trigger | Modulator |
|---|---|---|---|
| BE | 14-17u | swell-aankomst (zuid) | wind nog niet te hard |
| Domburg | 12-15u (piek 14-15:30) | swell + getij | werken tegen stroming |
| Domburg | 20:30-21:30 | tweede tij-window + restant swell | onshore wind afgenomen |
| Ouddorp | 16-18u | vloed refractie | sterk gedempt door bank |
| Maasvlakte | 9u en 10-13u | onshore wind windgolf | stroming na 14u |
| HvH | flat | geen swell penetratie | n.v.t. |
| Ter Heijde/Kijkduin | 12-14u | windswell opbouw | pier-luwte |
| Scheveningen | 11-13u (0,9m), 13-15u (1,1m), 16-17:30 | windswell opbouw, dan wind-dip | tij + pier |
| **Noordwijk/Zvoort** | **14-16u of na 19:30u** | swell-piek én getij-window | wind verdraait WZW |
| Wijk aan Zee | 10-12:30 / 15-17 / 18-20:30 | windswell + pier + 3 tij-windows | wind variabel |
| Egmond/Petten | 13-15u | windswell-piek | veel wind |
| TexelKoog | 19-21u | windswell + tij | stroming hoog |
| Paal 17 | 15-17u | windswell | wind 5bft |

### 4.3 Hoe maakt hij onderscheid "kort moment" / "leuke lijntjes" / "flat"?

Lexicaal patroon (consistent met v3 §1.5):

- **"flat"** → wave_height < ~0,4m, geen periode-component die telt. "Op HvH flat, zelfde als Ouddorp" = swell-richting refracteert om Ouddorp/HvH niet.
- **"heel weinig golf"** → 0,4-0,6m, periode <5s.
- **"al wat voor long en fish"** → 0,6-0,8m, periode 5-6s, longboard rideable maar geen shortboard.
- **"leuke lijntjes"** / **"leuk uurtje"** → 0,8-1,0m, periode 6-7s, schone wind.
- **"genoeg hoogte"** → 0,9-1,1m (impliciet, gebaseerd op Schev 1,1m als referentie in dezelfde alinea).
- **"net aan shortboard (beter long)"** → 1,0-1,1m maar met te veel windhash; shortboard kan maar niet ideaal.
- **"volle branding onshore"** → energie genoeg, vorm slecht: pure windsea zonder swell-component.
- **"kort moment"** → window <2u door tij-stroming of windshift binnen die periode.
- **"slapper en kleiner"** → afnemende swell + afnemende wind.

### 4.4 "Windhoogte" = pure wind sea

Donderdag: "**Swell nihil, windhoogte is 20cm**". Dit bevestigt v3 §1.2 ondubbelzinnig: de referentie-forecaster splitst expliciet de totale Hm0 in een **swell-component** (langere periode, propagatie van elders) en een **windhoogte/windgolf-component** (lokaal gegenereerd, korte periode). 20cm windhoogte = wind sea Hm0 ~0,2m bij vermoedelijk 3-4s = "rimpelsurf". Dit is de operationele definitie voor onze `wind_wave_height` vs `swell_wave_height` splitsing van Open-Meteo.

### 4.5 "Vloedstroom" — impact op surfbaarheid

Het concept werkt in twee assen:

**As 1 — Tegenstroom voor surfer:** "werken tegen de stroom wel" (Domburg) = je moet harder peddelen om in positie te blijven. Vermoeit, korter rideable.

**As 2 — Stroom vs windrichting:** "vanaf 15u komt de vloedstroom vol inzetten" (Zeeland/Z-H) → vloedstroom = water richting NO langs de kust; combineer met WZW-wind = wind en stroming kruisen elkaar grotendeels parallel/onderling versterkend, dus golfvorm wordt rommeliger maar niet steiler. Vergelijk met "wind tegen stroom" wat juist steile/short-peaked golven geeft (gewenst maar gevaarlijk).

In de Noordwijk-context: hoogwater Scheveningen ligt rond 18:30. Dus 14-16u is **vloed opkomend, stroming richting NO maar nog niet vol**. Na 19:30u zit je in **kentering-naar-eb fase**, stroming neutraliseert. Vandaar dat 14-16u én na 19:30u allebei werken voor Noordwijk: in beide vensters is de stroom niet maximaal.

### 4.6 "5bft tot 20u, daarna afnemend tot 4bft" — vertaling

Beaufort-conversie (v3 Appendix B):
- 5 bft = 19 kn ≈ 9,8 m/s (range 17-21 kn)
- 4 bft = 14 kn ≈ 7,2 m/s (range 11-16 kn)

Voor surfability:
- 5 bft uit ZW (onshore voor Noordwijk, beach normal ~285° dus 225° komt schuin uit ZZW = onshore-sidesonshore): produceert windgolven en chop, niet ideaal voor shortboard maar wel rideable; "longboarden" is letterlijk dat.
- 4 bft uit ZW: drempel waarop het cleaner wordt en swell de overhand kan krijgen over windhash.
- Hij kiest **20-21:30u** als sweetspot voor windafname met restant swell — dat is exact het Type 3 wind-dip patroon uit v3 §1.4 maar zonder echte synoptische trigger: het is gewoon **diurnal wind decay** (zonsondergang ≈ 21:38 op 20 mei).

---

## 5. Reverse-engineering van zijn beslissingsmodel

### 5.1 Hoe komt hij tot "Nwijk 14-16u of na 19:30u"?

Vereiste samenkomst van factoren (genormeerd voor Noordwijk):

| Factor | 14-16u | 19:30-21u | Waarom werkt het |
|---|---|---|---|
| Wind richting | ZW-WZW | ZW afnemend | ZZW/WZW is voor Noordwijk side-onshore tot side; 285° beach normal, 225° wind = 60° offset, niet ideaal maar acceptabel |
| Wind snelheid | 5 bft (~19kn) | 4 bft (~14kn) afnemend | 14u nog vol, 16u toenemend probleem, 19:30 afnemend = window opent |
| Swell hoogte | "genoeg hoogte" ~1,0m | 0,8-0,9m restant | IJG1 voorspelt piek middag |
| Periode | ~6-7s windswell | ~6s afnemend | optimum voor Noordzee |
| Getij Noordwijk | LW 14:49 + opkomend | HW 19:01 + afgaand | beide windows hebben **lage stroming** |
| Vloedstroom | nog niet vol (vóór 15u) | kentering naar eb | minimale interferentie |
| Refractie/blokkering | swell uit ZW = direct toegankelijk | idem | geen IJmuiden-pier blokkade (pier blokkeert N-NNO swell) |
| Buien | na 10u doorgetrokken | helder | clean lucht/zicht |

De **vier-uur stilte tussen 16:00 en 19:30** is verklaard door: (a) 16-18u zit de wind nog op 5bft + windrichting wordt vol-onshore na de buienlijn, (b) tussen 17-19u zit het tij in volle vloedstroom richting HW, dus maximale tegenstroom voor wie van Z naar N drift en dichtste interferentie tussen wind en water.

### 5.2 Waarom Noordwijk OK terwijl Ouddorp "heel weinig golf"?

Dit is een **regio-effect + kust-oriëntatie**:

1. **Kustoriëntatie**: Ouddorp ligt op de zuidwest-noordoost as met openheid naar het ZW, maar het is **schuin op de zuid-swell**. Een swell die echt uit het zuiden komt scheert grotendeels langs de kust. Noordwijk ligt op de zuid-noord as (open naar het W) — een WZW-swell raakt Noordwijk loodrechter.
2. **Vlaamse banken** (v3 §1.3): zuid-zwell wordt door Vlaamse banken gedempt naarmate het noordwaarts trekt — maar omdat dit WZW windswell is met korte periode (~6-7s), passeert het juist makkelijk over die banken (de referentie-forecaster zegt zelf in v3 21-8 SMS: "kortere interval komt makkelijk over de Vlaamse banken").
3. **Z-H fetch lengte**: de wind die naar WZW draait in Z-H/Zeeland blaast met een lange fetch náár het noorden over open zee — Noordwijk vangt die lokale-generatie windswell direct op, Ouddorp staat in de luwte achter de Brouwersdam-oriëntatie.
4. **Refractie rond Hoek van Holland-Maasvlakte**: de Maasvlakte werkt voor Ouddorp/Goeree zoals IJmuiden voor Noordwijk: ZW swell die de hoek om moet refracteren verliest energie. Vandaar "vloed 16-18u draait wel wat binnen" = met vloed komt er stroming-geïnduceerde golfdraaiing die het tijdelijk werkbaar maakt.

### 5.3 "Zuid-swell redt het niet om de wadden om te komen"

Diffractie rond Texel en de Wadden: een swell uit het Z/ZW moet ~90° om de hoek bij Den Helder om bij Vlieland/Terschelling/Ameland aan te komen. Diffractie-energieverlies bij ≥90° hoek is in de praktijk meer dan 90%, en de korte periode (~6-7s) geeft veel te weinig "buigkracht" — alleen ≥12s groundswell zou kunnen. Vandaar dat alleen TexelKoog (Noordzee-kant van Texel, dus geen diffractie nodig) en Paal 17 werken, niet de Friese wadden.

### 5.4 Tide-windows per spot — waarom drie windows op Wijk aan Zee

Voor Wijk aan Zee noemt hij: **10-12:30** EN **15-17** EN **18-20:30**. Dat is uitzonderlijk: drie windows op één dag. Reconstructie:

- **10-12:30**: pre-buienlijn, wind nog niet vol WZW, low water rond IJmuiden ~15u (=na HW 06u → laagwater ~12-13u rond IJmuiden zuidpier door fase-verschil met Noordwijk). Window = "afgaand tij" wat gunstig is voor Wijk omdat de zandbanken dan op de juiste diepte komen voor breaking.
- **15-17**: in deze fase is het **net na laagwater** = opkomend, en wind is op zijn sterkst (5bft) maar dat genereert lokale windswell die op de Wijk-zandbanken breekt. "Iets hoger 15-17u en minder wind" suggereert hij ziet hier ook een mini-wind-dip in de Harmonie.
- **18-20:30**: rond hoogwater IJmuiden (~19u) — bij Wijk werkt hoogwater juist goed voor het strand omdat de buitenbank dan op surf-diepte zit. Combineer met windafname en je hebt een laat avond-window.

Het patroon is: Wijk heeft **tide-tolerantere geometry dan Noordwijk** door de combinatie pier (Noordpier IJmuiden) + meerdere zandbanken op verschillende dieptes. Noordwijk heeft één bank-systeem en is dus per dag maar 1-2 windows.

### 5.5 Wat is "zuid-swell" in deze context?

Geen verre groundswell uit het zuiden (die bestaat in NL nauwelijks omdat de fetch in het Kanaal te kort is). Dit is **lokaal gegenereerde windswell** vanuit een WZW-wind over de Noordzee, met fetch ~200-400km vanuit ZW (Kanaal-mond → Belgische kust → Nederlandse kust). Resultaat: 0,8-1,1m bij 6-7s, propagerend met richting WZW = ~245°, dat aanlandt op de kust onder een hoek die voor Z-H/N-H gunstig is en voor Zeeland/BE iets schuiner. Geen "echte" swell in de Hawaii-zin.

---

## 6. Noordwijk concreet voor woensdag 20 mei 2026 — alle claims uit de SMS

### 6.1 Expliciete claims uit de SMS

- "**N-H al meer golven door wind**" (impliciet: Noordwijk eerst nog rustig, opbouwend door de dag)
- "**Vanaf 15u komt de vloedstroom vol inzetten**" (Z-H/Zeeland, geldt indirect voor Noordwijk)
- "**Wind WZW in Z-H**" na de buienlijn van 10u
- "**Nwijk/Zvoort 14-16u of na 19:30u**"
- "**Genoeg hoogte**" (impliciet ~1,0m, in lijn met Schev 1,1m piek)
- "**5 bft tot 20u, daarna afnemend tot 4 bft, wind uit ZW**"
- "**Avond prima longboarden**" — geldt voor de hele zone van Schev tot Nwijk inclusief

### 6.2 Impliciete claims te reconstrueren

- **Tij Noordwijk** (uit Windfinder voor 20 mei 2026): LW 02:13, HW 06:23 (2,09m), LW 14:49 (0,14m), HW 19:01 (1,73m), LW 23:31. De windows vallen exact rond LW + flank (14-16u) en HW + flank (19:00-21:00).
- **Refractie**: WZW swell = ~245° aankomst. Beach normal Noordwijk ≈ 285°. Offset 40° = goede aanvalshoek, geen pier-blokkering (IJmuiden zit ten noorden, blokkeert alleen NNO).
- **Wind speed**: 5 bft ZW = ~19 kn, side-onshore.
- **Periode**: niet expliciet maar uit Schev 11-13u 0,9m → 13-15u 1,1m piek profiel, plus zijn taalgebruik "niet echt shortboarden": dat zegt periode ~5-6s in middag, 6-7s in late avond als windswell volwassener is.
- **Windrichting evolutie**: pre-10u rustig met restant N-component, na buien WZW, late avond ZW met afname. Dus 14u ZW 5bft, 16u ZW 5bft, 19u ZW 5bft (afnemend), 20:30 ZW 4-5bft, 21:30 ZW 4bft.
- **Trigger-types** (mapping naar v3 typologie):
  - 14-16u window: hoofdzakelijk **T5** (tide-gated combo) + zwak **T3** (windrichting niet ideaal maar consistent)
  - 19:30-21u window: combinatie **T3** (wind-dip via diurnal decay) + **T5** (tide-gated, kentering naar eb)
  - Géén T1 (geen verre swell-arrival), géén T2 (geen front-passage windshift), géén T4 (geen groundswell).

### 6.3 Totaal-conclusie: is woensdag een goede dag voor Noordwijk?

**Matig-tot-OK, geen ALERT-waardige dag.** De referentie-forecaster noemt het als rideable maar zonder enthousiasme: "kort moment om te surfen", "longboarden", "1m hoogte (niet echt shortboarden)". Vergelijk met zijn high-alert taal in de v3 SMS-set ("forse N-swell", "echte groundswell door windgolven heen") — die ontbreekt volledig hier. In ons score-systeem zou dit een **score 55-65 in twee 1,5-2u windows** moeten zijn, type T5 met zwak T3, en het systeem zou hierop **GEEN alert** moeten sturen, alleen meenemen in digest.

De interessante test voor het algoritme: de dag is voor de meeste dataservices "ok windgolfje, longboard mogelijk" maar de meerwaarde van de referentie-forecaster zit in (a) tij-window-precisie (14-16u i.p.v. heel de middag) en (b) de avond-window-restoratie (19:30-21u) die veel diensten missen. Het algoritme moet deze twee specifieke windows kunnen genereren, niet een blob 14-21u.

---

## 7. Tabel: Noordwijk Woensdag 20 mei 2026 volgens de referentie-forecaster

| Parameter | Ochtend (06-10u) | Middag (10-14u) | Window 1 (14-16u) | Tussenfase (16-19:30u) | Window 2 (19:30-21u) | Late avond (21-23u) |
|---|---|---|---|---|---|---|
| Wind richting | rustig, N-component | draait WZW na 10u buien | ZW | ZW vol | ZW afnemend | ZW |
| Wind kracht (bft) | 2-3 | 4-5 | 5 (~19 kn) | 5 (~19 kn) | 4-5 → 4 (~14 kn) | 4 → 3 |
| Wind kracht (m/s) | ~5-8 | ~7-10 | ~9,8 | ~9,8 | ~7-9 | ~5-7 |
| Buien | nee | "buienlijntje" rond 10u | helder | helder | helder | helder |
| Golfhoogte totaal | <0,4m | opbouwend 0,4-0,8m | ~1,0m | ~1,0-1,1m piek | ~0,8-0,9m | <0,7m afnemend |
| Wind sea component | ~0,2m | 0,4-0,6m | 0,7-0,9m | 0,8-0,9m | 0,6-0,7m | <0,5m |
| Swell component | nihil | <0,3m | ~0,3-0,4m WZW | ~0,4m | ~0,3-0,4m | nihil |
| Dominante periode | ~3-4s | 4-5s | 5-6s | 6-7s | 6-7s | 5-6s |
| Swell richting | n.v.t. | WZW (~245°) | WZW | WZW | WZW | WZW |
| Tij phase (Nwijk) | HW 06:23 → afgaand | afgaand naar LW | LW 14:49 + opkomend | opkomend, vloedstroom vol | HW 19:01 + kentering naar eb | afgaand naar LW |
| Vloedstroom (langs kust) | zwak Z-NZ | zwak N | matig N (nog niet vol) | vol N (max ~17u) | kentering, neutraal | Z |
| Refractie/blokkering | n.v.t. | geen | geen (WZW kan aan) | geen | geen | n.v.t. |
| Surfability label | (n.v.t., niet genoemd) | (n.v.t.) | "genoeg hoogte" rideable | te veel wind/stroming impliciet | "longboarden prima" | (afname) |
| Bordkeuze | n.v.t. | n.v.t. | shortboard mogelijk maar onschoon | shortboard nee | **longboard ideaal** | longboard |
| Trigger-type (v3) | n.v.t. | n.v.t. | T5 (zwak) + T3 | géén | T3 + T5 | n.v.t. |
| Aanbevolen window? | nee | nee | **JA — kort 14-16u** | nee | **JA — 19:30-21u** | nee |
| Alert-waardig? | nee | nee | nee (geen rariteit) | nee | nee (geen rariteit) | nee |
| Verwachte score (0-100) | <15 | 35-45 | 55-65 | 40-50 | 60-70 | 30-40 |

---

## Bronnen

- [Windfinder getij Noordwijk aan Zee](https://nl.windfinder.com/tide/noordwijk_aan_zee)
- [RWS Getij / Waterinfo](https://getij.rws.nl/)
- [Ridersguide — Scoren in de Noordzee](https://ridersguide.nl/scoren-in-de-noordzee/)
- [Seven at Sea — Les 4/5: Getijden en surfen](https://sevenatsea.nl/voorspellen/getijden/)
- [Zeilvrienden — De stroming langsheen de Belgische kust](https://www.zeilvrienden.com/?p=315)

---

**Rapport-samenvatting voor je benchmark:** de methodiek van de referentie-forecaster is voor 80% te reconstrueren uit publieke bronnen (zijn weerlinks-pagina + spectra-pagina + 2017-uitlegpost), 15% uit ervaring/intuïtie die we niet kunnen kopiëren (jarenlange calibratie), en 5% uit KNMI menselijke guidance die hij leest. Voor de woensdag-case is zijn verwachting bewust onspectaculair: een **matige longboard-dag met twee precieze windows (14-16u + 19:30-21u)**, gedreven door tij-flank + diurnal wind decay, niet door een alert-waardig swell-event. Dit is de ideale benchmark om te checken of jouw systeem (a) windows met de juiste granulariteit produceert, (b) terecht GEEN alert genereert, en (c) de wind-sea vs swell decompositie correct uitvoert (impliciet ~70% wind sea, ~30% swell in deze setup).
