# Hoe professionele surf-forecasters tot hun voorspelling komen

**Datum:** 19 mei 2026
**Doel:** in kaart brengen wat menselijke pro-forecasters doen *bovenop* numerieke modellen, met als concreet einddoel om gerichte verbeteringen voor het SurfWeerWorkflow-systeem voor Noordwijk te identificeren.
**Methode:** literatuur- en interviewonderzoek over WSL/big-wave forecasters, NOAA/NWS surfdiensten, Surfline/Stormsurf/Magicseaweed-methodologie, fundamentele golffysica, NL-specifieke kustdynamica, en de werkwijze van Tobias van Tellingen (surfweer.nl).
**Aanvulling op:** `research_tobias_methodology.md`, `research_wave_physics_benchmark.md`, `research_benchmark_comparison.md`.

---

## 1. De vier "scholen" van surf-forecasting

Voor wij in detail duiken: er zijn vier herkenbare archetypes onder professionele forecasters, met elk een eigen aanpak. Onze Noordwijk-context valt het dichtst bij archetype 2 (Tobias) en 4 (NWS-stijl regionale forecaster), maar er valt veel te leren van 1 en 3.

| Archetype | Voorbeelden | Sterke punten | Wat ze missen |
|---|---|---|---|
| 1. Big-wave specialist | Mark Sponsler (Stormsurf), Surfline contest-forecasters | extreme precisie op grote events, jaren+ vooruit denken in klimaatpatronen | minder relevant voor kleine kust-Noordzee |
| 2. Dagelijks-spot-orakel | Tobias van Tellingen, Pat Caldwell, Ben Matson (Swellnet) | combineren modeloutput met 17+ jaar gekalibreerde lokale ervaring | persoonsafhankelijk, moeilijk schaalbaar |
| 3. Commerciële kustdienst | Surfline (LOTUS+forecaster), Magicseaweed | mengen ML met menselijke override op rating-niveau | grof per-spot, lokale tij/wind-windows missen |
| 4. NWS regio-meteoroloog | NWS Honolulu (HFO/SRF), KNMI guidance | publieke, gestandaardiseerde forecasts met expliciete onzekerheid | niet surf-specifiek qua eindwaarde |

Bron: Stormsurf "About"-pagina ([stormsurf.com](https://www.stormsurf.com/page2/services/about.html)), Surfline LOLA/LOTUS-documentatie ([surfline.com](https://www.surfline.com/lp/whatsnew/features/lotus-swell-model)), NWS HFO Surf Forecast ([weather.gov/hfo/SRF](https://www.weather.gov/hfo/SRF)).

---

## 2. WSL en de "call" — go/no-go in een waiting period

De WSL Championship Tour 2026 (12 events, waaronder twee Pipeline-events als grand finale) draait events in zogeheten **waiting periods** van 8–12 dagen, waarbinnen de **WSL Tour Director** (Renato Hickel) elke dag een go/no-go beslissing neemt. Procesmatig is dit publiekelijk traceerbaar:

- Iedere ochtend om typisch 06:30–07:45 lokale tijd doet de Tour Director een **"check"** op het strand (visuele lineup-inspectie) plus model-update.
- De **uitspraak** is letterlijk **"ON" of "Competition Placed on HOLD, next call [tijdstip]"**. Voorbeeld uit Vans Jack's Surfboards Pro 24-04-2026: "Next Call, Saturday, April 25 at 6:30 a.m. PDT."
- De inputs: long-range swell forecasts (typisch Surfline LOTUS aangevuld met persoonlijke modelinterpretatie), real-time buoy data, gisteren-versus-vandaag observatie van de bank/reef, weersvoorspelling (wind shift, regen, onweersdreiging).
- De **niet-numerieke heuristieken** die in de call-stream te horen zijn: "wave size *fluctuating*", "wind is *slowly switching*", "the bank is *not lined up*", "*tide too low* for this swell direction", "*no consistency* in the line-up".

Wat opvalt is dat de WSL Tour Director publiekelijk **kwalitatieve woorden** gebruikt (consistency, line-up quality, periode-organization) en geen kwantitatieve thresholds noemt. Dat is precies de menselijke laag die wij willen leren reverse-engineeren.

Bron: [WSL News](https://www.worldsurfleague.com/news), [Boardriders – 2026 WSL CT explained](https://www.boardriders.com/en-gb/world-surf-league-championship-tour-explained/), [WSL Pipeline finale 2026](https://www.worldsurfleague.com/posts/542489/world-surf-league-announces-return-to-pipeline-for-championship-tour-finale-starting-in-2026).

---

## 3. Mark Sponsler / Stormsurf — methodologie van een retired engineer

Sponsler (1958, Florida-Californië), retired engineer, runt sinds 1998 stormsurf.com. Zijn methodologie is buitengewoon goed gedocumenteerd, met expliciete tutorials.

### 3.1 Het partitie-principe ("the error most folks make")

Sponsler's kernlering: **kijk nooit naar significant wave height en gebruik niet alleen Tp — splits het spectrum in partities**. Citaat ([stormsurf.com](https://www.stormsurf.com/page2/services/about.html)):

> "The error most folks make is they look at significant Sea Height and period. That number provides the sum of all energy hitting the buoy. If there are 3 swells in the water, the significant sea height adds them all together... What you want is the pure swell height and period of each of those swells."

Concreet voor Noordwijk: een boei-Hm0 van 1.2 m kan bestaan uit 0.8 m N-windsea + 0.5 m W-restswell. De surfable component is mogelijk alleen die 0.5 m W-restswell, met de N-windsea als oppervlakte-chop.

### 3.2 Storm tracking en swell-numbering

Stormsurf nummert elke storm die "significant swell" zal genereren (Storm 5 → Swell 5). Dit is een **temporeel boekhoudsysteem** dat elke surfer kan adopteren: zodra je een storm in fetch ziet, geef je hem een nummer, koppel je voorspelling en validatie aan dat nummer, en kun je achteraf je hit-rate beoordelen.

### 3.3 Zelf in het water — verificatie-cultuur

Wat Sponsler scheidt van pure modelaars: hij paddle't out om zijn eigen voorspelling te checken. Grant Washburn (filmmaker) zei over hem: *"He is the only forecaster I've seen paddle out to see if his prediction of 50-foot faces is accurate."* Dit komt **identiek** terug bij Tobias (`research_tobias_methodology.md` §1: "bijna dagelijks zelf in het water").

### 3.4 De Stormsurf Calculator

Sponsler publiceert een open spreadsheet-achtige calculator waar je per-storm de fetch-lengte, windsnelheid, duur en beste afstandshoek invult, en die spuwt een verwachte swell-periode + decay-curve uit. Dit is de **deterministische pre-Surfline manier van forecasten**, en bestrijkt fysica die ECMWF-WAM impliciet al doet — maar Sponsler wijst expliciet aan **welke storms je negeert** (te ver, te kort, te schuin).

Bronnen: [Stormsurf tutorials menu](https://www.stormsurf.com/page2/tutorials/menu.html), [Wave Models tutorial](https://www.stormsurf.com/page2/tutorials/wam.shtml), [Lookout Santa Cruz – The Surf Whisperer](https://lookout.co/surfing-mark-sponsler-stormsurf-forecasts-have-earned-a-devoted-following-among-big-wave-surfers/story), [Florida Surf Museum profile](https://floridasurfmuseum.org/talking-story/the-florida-connection-mark-sponsler-wave-whisperer).

---

## 4. Pat Caldwell — de NWS Honolulu jaarringen-methode

Pat Caldwell (1987–2020 NWS Honolulu, daarna SurfNewsNetwork.com) is de paradigma-voorbeeld van **klimatologisch-geinformeerd forecasten**. Wat hij doet dat een model nooit doet:

### 4.1 De Goddard-Caldwell database

Caldwell heeft sinds 1968 (met voorganger Larry "Stat Man" Goddard) **dagelijkse H1/10 visuele surf observaties** bijgehouden. In zijn dagelijkse forecast discussion vergelijkt hij *expliciet*:

- Gemiddelde wave height voor **deze specifieke kalender-datum** over ~50 jaar
- De **grootste surf ooit gemeten op deze datum**
- Vergelijking met **vergelijkbare synoptische setups** uit historie

Voor Noordwijk implementatie: dit suggereert dat we per kalenderweek een **klimatologische verwachtingswaarde** voor Hs en Tp zouden moeten hanteren, en de model-forecast in die context plaatsen ("dit is normaal voor week 21", "dit is groter dan 90% van de week 21-dagen sinds 2015").

### 4.2 Directional banding in graden

Caldwell schrijft niet "NW swell" maar **"305-320 degrees"**. Voor Hawaii (eilanden + complexe shoreline) is dit essentieel omdat 5° verschil bepaalt welke kust treft. Voor Noordwijk is dit nuttiger dan we denken: WZW (245°) vs ZW (225°) is 20° verschil maar tikt anders aan op de zandbank-systemen.

### 4.3 De "discussion" als tekst

Onder de tabel met cijfers staat ALTIJD een vrije-tekst-discussie waarin Caldwell met humor uitlegt **waarom** de forecast is zoals hij is. Dit is precies wat Tobias in zijn SMS doet: niet alleen het cijfer, maar het **verhaal eromheen** (welke storm, welke fetch, welke periode-evolution).

### 4.4 Feedback-loop met de big-wave community

Caldwell belt big-wave surfer Kohl Christensen voor "view from the lineup" — biggest set, lulls, wind. Dit is een **menselijke validation-loop** die de hindcasts continu kalibreert. Vergelijk Tobias: hij is zelf de surfer-validator.

Bronnen: [Hana Hou – The Surf Sage](https://hanahou.com/24.2/the-surf-sage), [NWS HFO Surf Forecast](https://www.weather.gov/hfo/SRF), [NCEI – Inside NCEI: Patrick Caldwell](https://www.ncei.noaa.gov/news/inside-ncei-regional-science-officer-patrick-caldwell), [Surf News Network – Pat Caldwell](https://www.surfnewsnetwork.com/pat-caldwell/).

---

## 5. Sean Collins / Surfline / LOLA → LOTUS

Sean Collins (1952–2011, founder Surfline) was de **eerste hobbyist-ondernemer die NOAA data systematisch ontsloot voor surfers**. Zijn fundament was:

- Self-taught meteoroloog ("trial and error" met sailing-ervaring)
- Mobile forecasting kit in zijn auto (Baja-experimenten 1980s)
- Eerste die radio-WX vanuit Nieuw-Zeeland combineerde met Baja-verwachting

Surfline's model **LOLA** (2001), gebouwd met William O'Reilly (Scripps), was de eerste:
- Die **NOAA Wavewatch III data tweakte met empirische surf-observaties** ("empirical evidence gained from more than 30 years of surf forecasting")
- Die **satellite-data assimileerde** in real-time
- Die nearshore **bathymetrie** doorrekent voor surf-height-translation

Het opvolger-model **LOTUS** (2018-heden) voegt **machine learning** toe en gebruikt ML om "het verschil tussen poor surf en good surf" te leren van **hundreds of thousands van menselijke surf observaties** door de jaren heen.

### 5.1 Surf Ratings — wat zegt Surfline?

Surfline's rating-systeem (7 niveau's: Very Poor → Epic) is **bewust dubbel**:

- De **onderste 5** (Very Poor → Fair to Good) worden door LOTUS-ML berekend op basis van Hs en wind.
- De **bovenste 2** (Good, Epic) zijn **alleen door menselijke forecaster** toe te kennen na visuele bevestiging.

Citaat ([surfline.com](https://support.surfline.com/hc/en-us/articles/36277684017819-Surf-Ratings-Colors)): *"This change was made because Good and Epic ratings can only be assigned by forecasters who have observed the conditions."*

Reden: het ML-model mist context-elementen zoals **prior winds that have left residual chop, of a very high tide that slows conditions at tide-dependent spots**. Dit is exact wat Tobias' SMS-tekst impliciet bevat.

### 5.2 Wave Consistency — losse score voor set-frequentie

Surfline introduceerde recent een aparte **"Wave Consistency 0-100"**: hoe vaak een wave de spot-drempel overschrijdt, los van de quality. Dit is conceptueel waardevol: Hs zegt niets over **hoeveel sets per uur** je krijgt. Voor Noordwijk: een 1.0m@5s windswell met smal spectrum heeft veel hogere consistency dan dezelfde 1.0m@12s groundswell.

Bronnen: [Surfline – LOTUS feature](https://www.surfline.com/lp/whatsnew/features/lotus-swell-model), [Surfline – Surf Ratings](https://support.surfline.com/hc/en-us/articles/36277684017819-Surf-Ratings-Colors), [Surfline – Wave Consistency](https://support.surfline.com/hc/en-us/articles/20350539606683-Wave-Consistency), [Surfline – Sean Collins obit](https://www.surfline.com/surf-news/sean-collins-1952-2011/73239), [ESPN – Surf-forecasting pioneer Sean Collins dies at 59](https://www.espn.com/action/surfing/story/_/id/7391373/surf-forecasting-pioneer-sean-collins-dies-59).

---

## 6. Magicseaweed / Swellnet — wat zeggen kustsspecialisten

### 6.1 Magicseaweed's expliciete erkenning van local knowledge

MSW (nu deels in Surfline opgegaan) was explicieter dan Surfline over **wat hun model niet weet**. Citaat ([magicseaweed.com](https://magicseaweed.com/docs/forecasting/66/a-quick-forecast-tutorial/10123/)):

> "MSW is constantly working to factor as much of this science as possible into a forecast, but ultimately you need some local knowledge and experience to take the data and calculate your own surf forecast."

En over **swell-richting**: *"swell direction is one of the most overlooked factors when trying to read the forecast — 10-20 degrees difference can have a massive effect at some locations."*

### 6.2 Swellnet's "regional, not break-specific" filosofie

Ben Matson (founder Swellnet, AU) is expliciet dat zijn forecast **bewust regionaal** is en geen break-precisie pretendeert: *"Our forecasts are just regional overviews and not break specific, and still require local knowledge to score waves."*

Dit is een **ontwerpprincipe** dat ons systeem zou moeten overnemen: doe niet alsof je weet welke bank werkt — geef de regionale envelope (wind, swell, tide) en laat de gebruiker zelf de spot kiezen. Of beter: geef **per spot een aparte score** maar maak expliciet dat het een approximatie is.

### 6.3 Wind sea vs swell — MSW's expliciete split

MSW splitst hun forecast in twee aparte tabellen: *"the model used on MSW distinguishes between the most powerful swell and 'wind waves'."* Dit is wat Open-Meteo's `swell_wave_height` + `wind_wave_height` ook biedt — alleen worden ze in ons huidige systeem niet apart gewogen.

Bronnen: [MSW – Quick Forecast Tutorial](https://magicseaweed.com/docs/forecasting/66/a-quick-forecast-tutorial/10123/), [MSW – What is a Swell Model](https://magicseaweed.com/news/what-is-a-swell-model/6946/), [Swellnet – Understanding our new forecasting system](https://www.swellnet.com/news/swellnet-analysis/2013/10/21/understanding-our-new-forecasting-system).

---

## 7. Tobias van Tellingen — Nederlandse spot-orakel

Uitgebreid behandeld in `research_tobias_methodology.md`. Hier de essentie:

- **17+ jaar gekalibreerde patroonherkenning op de Noordzee**, autodidact, woont dichtbij KNMI De Bilt.
- Methode: 3-4 fijnmazige modellen (Harmonie, ICON, AROME) naast elkaar leggen — zelf benoemt hij dit in zijn KNRM-interview: *"In geval van een complexe weersituatie zeker de moeite waard"* ([KNRM – Kitesurfweer](https://www.knrm.nl/blog/tips/kitesurfweer)).
- Gebruikt expliciet **KNMI guidance modelbeoordeling** — dat is *menselijke synthese als input*, iets dat wij niet 1-op-1 kunnen repliceren.
- Verifieert **dagelijks zelf** door in het water te staan.
- Spectrum-uitleg: zie zijn 2017-post (helaas inmiddels 404), maar `research_tobias_methodology.md` §3 documenteert: shoaling-factor ~2× bij T=10s, ~1× bij T=5s; groepssnelheid `1.56 × T` m/s; optimum 6.5–7s voor Noordzee.
- Niet-genoemd in eerdere research, maar gevonden in [surfweer.nl/surf/surfweer-laatste-week-van-juni-2023/](https://surfweer.nl/surf/surfweer-laatste-week-van-juni-2023/): zijn drempel voor surfability: *"de wave periode moet minimaal 5 seconden zijn, ongeacht hoogte. Er zijn windagen waarbij de wave hoogte oploopt tot bijna 1,5m, maar de wave periode blijft steken op 4 tot 4,5 seconden."*
- Geen formele KNMI-relatie maar wordt door de surf-community én KNRM als autoriteit erkend.

Bronnen: [Tasha's Surfcamp – Piet Paulusma van het surfen](https://tashasurfcamp.com/tobias-de-piet-paulusma-van-het-surfen), [KNRM – Kitesurfweer](https://www.knrm.nl/blog/tips/kitesurfweer), [Ridersguide – Windvoorspellingen](https://ridersguide.nl/haal-meer-uit-de-windvoorspellingen/), [Omroep West – overleden watersporters Scheveningen](https://www.omroepwest.nl/nieuws/4045711/overleden-watersporters-scheveningen-mogelijk-verrast-door-lange-zeedeining).

---

## 8. Fysica die pro's gebruiken (en de meeste apps niet)

### 8.1 Wave energy flux — de échte "size"

De fundamentele formule die door alle pro's bekend is maar door geen consumer-app expliciet getoond wordt:

```
P ≈ 0.49 × Hs² × Te    [kW/m]
```

Met Hs in meter, Te (energy period) in seconden. Voorbeeld:

- 1m @ 5s windswell: P ≈ 2.5 kW/m
- 1m @ 8s mixed: P ≈ 3.9 kW/m
- 1m @ 12s groundswell: P ≈ 5.9 kW/m
- 2m @ 12s "big day": P ≈ 23.5 kW/m

Voor Noordwijk implementatie: dit is een **één-getal samenvatting** die periode én hoogte combineert in echte fysische eenheid. Een 1.4m@4s windhash heeft **lagere wave energy** (3.8 kW/m) dan een 0.9m@8s clean swell (3.2 kW/m) — *bijna gelijk* — maar de pro weet dat het clean-swell-dag betere golven gaat geven. De flux moet dus gewogen worden met een **periode-bonus boven een drempel** (T > 6s).

Bron: [ScienceDirect – Estimating wave energy flux](https://www.sciencedirect.com/science/article/abs/pii/S0960148120304560), [ECMWF – Wave energy flux](https://charts.ecmwf.int/products/medium-wave-energy-flux).

### 8.2 Iribarren number — voorspelt het breaker-type

De surf similarity parameter ξ:

```
ξ = tan(β) / √(H/L₀)
```

Met β = beach slope, H = wave height, L₀ = deep-water wavelength = `1.56 × T²`.

| ξ | Breaker type | Surfability |
|---|---|---|
| < 0.4 | Spilling | Beach-break achtig: rommelig, longboard-friendly, slow ride |
| 0.4 – 2 | Plunging | Klassieke surf-golf: hollow, fast, shortboard-ideal |
| > 2 | Surging/collapsing | Reflectief, niet surfbaar bij beach |

Voor Noordwijk (beach slope ~1:50 = 0.02): ξ = 0.02 / √(1.0/56) = 0.02 / 0.134 = **0.15** — dat is spilling-domein, helemaal te verwachten voor een typische Hollandse beachbreak. Maar bij Tp=10s en Hs=1.0m wordt L₀=156m, dus ξ=0.02 / √(1.0/156)=0.25 — nog steeds spilling, maar dichter bij plunging.

De **conclusie**: voor onze beach is een hoge ξ (>0.25) een signaal dat de golven **hollower** breken (betere surf-quality). Dit is een **secundaire quality-modifier** die geen consumer-tool gebruikt.

Bronnen: [Wikipedia – Iribarren number](https://en.wikipedia.org/wiki/Iribarren_number), [Coastal Wiki – Surf similarity parameter](https://www.coastalwiki.org/wiki/Surf_similarity_parameter).

### 8.3 Wave age — wanneer is wind-zee "echt"?

Wave age = cp/U10 (golfsnelheid / wind op 10m). De drie regimes:

| Wave age (cp/U10) | Regime | Surfability |
|---|---|---|
| < 0.83 | Jonge windsea (wind voedt nog) | Choppy, steile golven, niet schoon |
| 0.83 – 1.2 | Mature windsea / decoupling | Begint surfbaar te zijn met juiste wind |
| > 1.2 | Swell (Pierson-Moskowitz fully developed limit) | Surfbaar, langere periodes, schoner spectrum |

Berekening voor Noordwijk wind-swell scenario (T=6s, U10=10 m/s):
- cp = 1.56 × T = 9.4 m/s
- cp/U10 = 9.4 / 10 = 0.94 → grensgeval, marginaal surfbaar

Tobias' eigen empirische cutoff (5s) komt aardig overeen: bij T=5s, U10=10m/s is cp/U10 = 7.8/10 = 0.78 → jonge windsea, **niet surfbaar**.

Pierson-Moskowitz fully-developed sea criterium: cp/U10 ≈ 1.14 (≈ Tp = 8.13 × U10/g voor fully developed). Dat is exact waar Surfline UK / Stormrider de cutoff legt voor "kwaliteits-groundswell-achtig" voor Atlantische context.

Bronnen: [Wikipedia – Pierson–Moskowitz spectrum](https://en.wikipedia.org/wiki/Pierson%E2%80%93Moskowitz_spectrum), [Geosciences LibreTexts – Ocean Wave Spectra](https://geo.libretexts.org/Bookshelves/Oceanography/Introduction_to_Physical_Oceanography_(Stewart)/16:_Ocean_Waves/16.4:_Ocean-Wave_Spectra), [AMS – Revisiting the Pierson-Moskowitz Asymptotic Limits](https://journals.ametsoc.org/view/journals/phoc/33/7/1520-0485_2003_033_1301_rtpalf_2.0.co_2.xml).

### 8.4 Wave shoaling — boei vs strand

In ondiep water versterken golven door behoud van energieflux. De shoaling-coëfficiënt Ks:

```
Ks = √(Cg_deep / Cg_shallow)
```

Voor T=10s, gaat een 1m boei-meting op 30m diepte naar ~2.0m op 3m diepte (= waar het breekt). Tobias noemt dit zelf: *"bij 10s een factor 2 ten opzichte van wat de boei meet"*. Voor T=5s is de factor slechts ~1.1, dus boei = strand-hoogte.

Wat onze app dus moet doen: als we **boei-data** (IJG1, MUN1) gebruiken, een periode-afhankelijke shoaling-correctie toepassen. Open-Meteo Marine geeft al een nearshore Hs (kustpunt), maar deze is een grof model — een vergelijking IJG1-boei vs model voor periode > 8s zou een correctie-factor opleveren.

Bron: [UBC ATSC113 – Swell](https://www.eoas.ubc.ca/courses/atsc113/sailing/met_concepts/08-met-waves/8d-swell/index.html), Tobias' impliciete uitleg in [surfweer.nl/surf/spectra/](https://surfweer.nl/surf/spectra/).

### 8.5 Refractie en Snell's law voor swells

Een swell die schuin invalt op een ondieper wordende bodem buigt naar de **steilste-gradient**-richting (= shoreline-loodrecht). Snell-equivalent voor watergolven:

```
sin(θ₁) / C₁ = sin(θ₂) / C₂
```

Voor Noordwijk: een WZW-swell (245°, beach-normal 285°) heeft offset 40°. Door refractie zal de breaking-direction dichter bij beach-normal komen, dus de **effectieve closeout-risk** vermindert. Een ZZW-swell (200°) heeft offset 85° en zal **veel meer langs de kust scheren** zonder fatsoenlijk te breken (refractie kan niet 85° overbruggen voor windswell met T<8s).

Praktische regel uit literatuur en Tobias: voor Noordwijk werkt swell-richting tussen ~225° (ZW) en ~340° (NNW). Buiten dit bereik treedt **swell-shadow** op (pier IJmuiden voor N-swell, kustoriëntatie zelf voor ZZW).

Bronnen: [Ocean Dynamics – Numerical simulations of surface wave refraction in the North Sea](https://link.springer.com/content/pdf/10.1007/BF02226339.pdf), [UBC ATSC113 – Swell](https://www.eoas.ubc.ca/courses/atsc113/sailing/met_concepts/08-met-waves/8d-swell/index.html).

### 8.6 Pierson-Moskowitz vs JONSWAP — welk spectrum waar?

| Spectrum | Conditie | Voor Noordzee? |
|---|---|---|
| **Pierson-Moskowitz (1964)** | Fully developed, infinite fetch | Zelden — Noordzee fetch te kort |
| **JONSWAP (1973)** | Fetch-limited, gepiekt spectrum | **Default voor Noordzee** — γ ≈ 3.3 typisch |

Voor onze case: bijna alle Noordzee-windswells volgen **JONSWAP** met scherpe piek bij Tp en relatief weinig energie buiten de piek. Dit verklaart waarom een 1.0m@5s **smal** spectrum heeft (consistente sets, korte lulls), terwijl in een Atlantische context dat een **breder** spectrum zou zijn (rommeliger).

Bron: [ScienceDirect – Pierson-Moskowitz Spectrum overview](https://www.sciencedirect.com/topics/engineering/pierson-moskowitz-spectrum), [MATEC – Comparison of Various Spectral Models](https://www.matec-conferences.org/articles/matecconf/pdf/2018/62/matecconf_iccoee2018_01020.pdf).

---

## 9. NL-specifieke fysica

### 9.1 Doggersbank en Brown Bank als swell-filter

De Doggersbank (ondiepte ~15-30m, oppervlakte ~17500 km² in centrale Noordzee) en de Brown Ridge / Brown Bank (Z-NL kust, ondiepte ~25m) hebben twee effecten op een N/NW swell uit het noord-Atlantische bekken:

1. **Energy dissipation** via bottom friction — een swell met T=10s (L₀=156m) "voelt" de Doggersbank al duidelijk (bodem-interactie bij d < L/2 = 78m). Vooral langere periodes (T>12s) verliezen ~15-25% energy door bottom dissipation over de bank.
2. **Refractie** — Doggersbank kan een N-swell licht naar het oosten buigen, met als gevolg dat de swell schuiner aanvalt op de NL-kust dan model-output voorspelt.

Het Ocean Dynamics paper (Numerical simulations of surface wave refraction in the North Sea Part 2: Dynamics) bevestigt: *"the German Bight divides the coastal wave climate and the directional characteristics of swell into a northern and southern domain and provides shelter to the Elbe and Weser estuaries from high sea states associated with swell from the North Atlantic when generation of waves from local winds in the North Sea is negligible."*

Voor Noordwijk: de Doggersbank-shadow is **gradient** (sterker voor Texel/Wadden, zwakker voor Noordwijk/Scheveningen). Voor pure N-swells (zeldzaam) zou je een **5-15% reductie-factor** kunnen toepassen versus offshore Atlantic-buoy values.

### 9.2 Vlaamse Banken als south-swell filter

De Vlaamse Banken (1099 km², zuidwest van België, tot 45 km in zee) zijn een serie ondiepe banken (depth 5-25m) parallel aan de kust. Voor een Z/ZW-swell uit het Kanaal:

- **Wave height attenuation**: ~20-40% over de banken (afhankelijk van Hs, T, water level)
- **Spectrum filtering**: korte-periode-componenten (T<6s) passeren makkelijker dan lange-periode (de banken dempen relatief meer bij T>10s door grotere bottom-interactie)

Dat is precies wat Tobias zelf zegt (21-8 SMS, geciteerd in `research_tobias_methodology.md`): *"kortere interval komt makkelijker over de Vlaamse banken"*. Voor Noordwijk: Z-swells worden bij aankomst NIET zoals een open-zee swell behandeld — de **periode is filterd** en pas wat doorkomt is meestal T=5-7s windswell.

Bron: [Health.belgium.be – Habitats Directive Areas Belgian North Sea](https://www.health.belgium.be/en/habitats-directive-areas-belgian-part-north-sea), [BODC – Monitoring Network Flemish Banks](https://www.bodc.ac.uk/resources/inventories/edmed/report/5619/).

### 9.3 Pier-refractie IJmuiden en Scheveningen

De **Zuidpier IJmuiden** (1576 m lang, eindigt op ~15m diepte) is de dominante kustkenmerk voor Noordwijk:

- Een N-swell (0–30°) wordt **volledig geblokkeerd** voor de eerste 3-4 km zuid van de pier (dat is precies de Noordwijk-zone op 8km zuid). Refractie rond de pierhead vermindert energie met >50% voor strikt N-swells.
- Een NNW-swell (315–340°) komt **gerefracteerd** door, met 20-40% verlies.
- Een W tot WZW-swell (245–270°) wordt **niet beïnvloed** door de pier (komt loodrecht aan).
- Een SW (200–230°) komt onhinderd, behalve dat de Scheveningen-pier (zuidelijker) hetzelfde doet voor Z-Hollandse spots.

**Backwash/diffractie** rond pierhead geeft soms een verrassend **mini-window** ("wrap-around") bij grote NW-swells. Surf Atlas noteert specifiek voor Scheveningen Pier: *"Once in a while, SW wrap-arounds will bend into the beaches by the pier and the breakwaters to give glassy peelers."*

Voor Noordwijk: een ZW-swell op laagwater met IJmuiden Noordpier-refractie kan **een verhoogde wave-height op spot Paal 80-Noordwijk** geven door focussing-effect. Dit is een **local-knowledge feature** waar geen model rekening mee houdt.

Bron: [Surf Atlas – Scheveningen Surf](https://thesurfatlas.com/surfing-in-the-netherlands/scheveningen-surf/), [ResearchGate – Bathymetry IJmuiden Noordwijk](https://www.researchgate.net/figure/Bathymetry-map-of-the-North-Sea-near-IJmuiden-Netherlands-indicating-the-location-of_fig2_46658000).

### 9.4 Sandbank dynamics — Noordwijk specifiek

Onderzoek (Short 1992, van Dijk & Kleinhans 2008) toont:

- De Hollandse kust heeft een **multi-bar systeem** met 2-3 banks parallel aan de kust.
- Migratie-snelheid coastal sand waves: **6.5-20 m/jaar**. Dat is langzaam genoeg om in een seizoenscyclus stabiel te zijn, maar betekent dat een geul/bank die in maart werkt in september al verschoven kan zijn.
- Inner bar = "ridge and runnel" = cut by drains and rips. Dat is je beach-zone bij Noordwijk.
- Middle/outer bar = "transverse bars and rips" = de bank waar de **grote dagen** werken op laagwater.
- Multibeam onthult fijne details tussen -12m en -18m die fungeren als "conduits for downslope currents and sand transport" — dat zijn de **rip channels** die je vlak voor de kust voelt.

**Implicatie voor onze app**: zonder bathymetrische data per maand kunnen we de bank-geometrie niet modelleren. Maar we kunnen **wel** een **tide-bank mapping** maken op basis van empirie: laagwater = outer bar werkt = swell breekt eerder, hoogwater = inner bar werkt = breekt dichter bij strand. Dat is precies wat een lokale surfer doet en wat onze huidige `tide_normalized` (0=laag, 1=hoog) niet doet.

Bron: [ResearchGate – Beach systems central Netherlands coast](https://www.researchgate.net/publication/222475835_Beach_systems_of_the_central_Netherlands_coast_Processes_morphology_and_structural_impacts_in_a_storm_driven_multi-bar_system), [Springer – Sandbank occurrence Dutch continental shelf](https://link.springer.com/article/10.1007/s00367-008-0105-7), [ScienceDirect – The lower shoreface of the Dutch coast](https://www.sciencedirect.com/science/article/pii/S096456912200343X).

---

## 10. Time-of-day patterns die modellen NIET expliciet voorspellen

### 10.1 Morning offshore / land breeze

Mechanisme:
1. Nacht-uren: land koelt sneller dan zee → land-lucht zakt (hogere druk over land), zee-lucht stijgt → wind van land naar zee = **offshore**.
2. Sterkte typisch 2-5 kn, doorgaans aanwezig tussen 23:00 en 09:00 lokaal.
3. Werkt **alleen bij low ambient wind** — als er een synoptisch wind-systeem actief is (>10 kn), wordt de land-breeze gemaskeerd.

Voor Noordwijk: morning glass werkt **ZO/E offshore** (haaks op de kust = oost). Diurnal land-breeze in Noordwijk-context komt uit het oosten en is meestal zwak. ECMWF/ICON modellen onderschatten deze om twee redenen:
- Grid-resolutie te grof (~7-10 km, terwijl land/zee gradient op ~1 km schaalt).
- Diurnal cycle wordt gemiddeld over de gehele grid-cel.

Surfline ([Science of Surfing](https://www.scienceofsurfing.com/p/why-is-the-wind-offshore-in-the-afternoon)) en Surf Simply ([Reading the Wind](https://surfsimply.com/magazine/reading-the-wind)) leggen dit klassiek uit.

### 10.2 Evening glass-off

Mechanisme (omgekeerd): 's avonds als zon onder gaat, koelt land snel af, sea-breeze valt weg, paar uur tussen 19:00-21:00 lokaal wordt het **glassy**. Dit is wat Tobias in zijn SMS van 19 mei 2026 expliciet noemt voor Noordwijk: *"5bft tot 20u, daarna afnemend tot 4bft"* + de specifieke avond-window **19:30-21u**.

In ons systeem moeten we dus **wind-decay rond zonsondergang** modelleren. Open-Meteo geeft per-uur wind, dus de signal is wel aanwezig, maar mogelijk onderschat. Een **post-processing rule** "subtract 1-2 kn for the hour 19-21 if windrichting=onshore en cloud-cover laag" zou dit verbeteren.

Bronnen: [Surf Simply – Reading the Wind](https://surfsimply.com/magazine/reading-the-wind), [Science of Surfing – Why is the wind offshore in the afternoon](https://www.scienceofsurfing.com/p/why-is-the-wind-offshore-in-the-afternoon), [Swellnet – Hot air and choppy surf](https://www.swellnet.com/news/swellnet-dispatch/2013/07/18/hot-air-and-choppy-surf-making-sense-sea-breeze), [Encyclopedia of Surfing – glass-off](https://eos.surf/entries/glass-off/).

### 10.3 Sea breeze / afternoon onshore intensification

In NL-context (juni-augustus): land warmt sterk op tot ~25°C terwijl Noordzee ~15-18°C is. ΔT ~ 10°C drijft een **thermische W/WZW sea-breeze** van 5-10 kn die rond 11:00 inzet en piekt rond 15:00-17:00. Dit is **bovenop** elke synoptische wind.

Implicatie: een ECMWF-forecast die "10 kn W" zegt voor 14:00 in juli kan in werkelijkheid **15-18 kn W** zijn door sea-breeze versterking. Dit is een **seizoensbias** die we maandelijks zouden moeten kalibreren.

### 10.4 Tide-windows worden door modellen niet als kwaliteits-modifier behandeld

Open-Meteo Marine geeft Hs per uur, maar de **echte surfability** hangt af van **tide × Hs × Tp interactie**:

- **Laagwater + grote swell** = outer bank in spel, sets breken ver uit, paddle is zwaar.
- **Hoogwater + kleine swell** = inner bank in spel, breakers zijn dichtbij strand, korter ride.
- **Mid-tide (rising) + medium swell** = classic, beide bands kunnen werken, optimum window.

Tobias' SMS-windows volgen *exact* dit patroon. Onze huidige `tide_normalized` 0-1 mist het feit dat **flank van een tij** (rising/falling) belangrijker is dan absolute height.

---

## 11. Modellimitaties voor de Noordzee-context

### 11.1 Open-Meteo's stack: DWD ICON Wave + ECMWF WAM

Open-Meteo Marine API onder de motorkap (per [open-meteo.com docs](https://open-meteo.com/en/docs/marine-weather-api)):

- **DWD ICON Wave**: 0.10° (~11 km) resolutie globaal, 0.05° (~5 km) voor EU/Noordzee.
- **ECMWF WAM IFS**: 0.25° (~28 km) globaal.
- **MeteoFrance MFWAM**: 0.5° globaal, 0.1° Europa.
- **NOAA GFS Wave**: 0.25° globaal.

Voor Noordwijk: zelfs de fijnste resolutie (5 km DWD ICON) is **te grof om kust-detail te resolveren**. Een wave-model met 5 km grid ziet de IJmuiden Zuidpier als 1 grid-cel; refractie eromheen is sub-grid en wordt parametrisch geschat, niet expliciet berekend.

Dit verklaart twee structurele biases in onze data:
1. **Wave-height bias**: model geeft 1.0m terwijl boei IJG1 op 1.3m staat. Dit komt door (a) ontbrekende lokale wind-versterking, (b) verkeerde shoaling-correctie, (c) onderschatte refractie-focusing.
2. **Period bias laag**: model geeft Tp=4.5s terwijl Tobias 5-6s zegt. WAM/WW3 hebben bekende negative bias in Tp voor fetch-limited seas met breed spectrum.

### 11.2 Wat WW3 en SWAN wel/niet kunnen

| Eigenschap | WW3 globaal | WW3 nested NL | SWAN nearshore |
|---|---|---|---|
| Grid resolutie | ~50 km | ~10 km | ~500 m mogelijk |
| Refractie | impliciet | beperkt | volledig |
| Shoaling | parametrisch | parametrisch | volledig |
| Wind-input forcing | global GFS | regional model | regional |
| Update frequency | 6 uur | 3 uur | 1-3 uur |
| Beschikbaar via Open-Meteo? | Ja | Ja (ICON-wave) | Nee |

SWAN ([TU Delft model](https://www.svasek.nl/en/model-research/swan/)) is wat **lokale Nederlandse kustautoriteiten gebruiken** (Rijkswaterstaat, Deltares) — niet beschikbaar via Open-Meteo. Voor een DIY upgrade zouden we de RWS-meetgegevens als boundary condition voor onze eigen scoring kunnen gebruiken, niet als alternatief model.

### 11.3 ECMWF Ensemble voor uncertainty

ECMWF runt 51 ensemble members per forecast cycle (50 perturbed + 1 control). De **spread** tussen members is een direct kwantitatief maat voor uncertainty:

- **Tight spread** (alle members <0.2m verschil voor Hs) = high confidence
- **Wide spread** (>0.5m range) = significant uncertainty, vooral D+3 en verder
- **Bimodal distribution** = twee scenario's mogelijk (bv. trekt storm noordelijk of zuidelijk langs?)

Voor onze app: ECMWF Open Data API geeft control + ensemble mean + spread. We kunnen **per uur** een **uncertainty-band** rapporteren in plaats van één getal. Voorbeeld: "Hs 1.0m (range 0.7-1.4m, 80% CI)".

Bron: [ECMWF – Quantifying forecast uncertainty](https://www.ecmwf.int/en/research/modelling-and-prediction/quantifying-forecast-uncertainty), [ECMWF – Improving physical consistency ensemble forecasts](https://www.ecmwf.int/en/newsletter/181/earth-system-science/improving-physical-consistency-ensemble-forecasts-using-spp).

---

## 12. Wat doen pro's dat naive modellen niet doen — 12 mechanismen

### A. Mechanism 1: Multi-model triangulatie
Pro's leggen **3-4 verschillende numerieke modellen naast elkaar** (Tobias: Harmonie, ICON, AROME, KNMI guidance). Verschil tussen modellen = onzekerheidssignaal. Onze app gebruikt nu één model (Open-Meteo).

### B. Mechanism 2: Boei-as-truth feedback
Pro's vergelijken **real-time boei tegen ochtend-forecast**. Als de IJG1-boei al om 08:00 hoger zit dan de forecast voor 08:00, weet je dat de hele dag-curve geüpdate moet worden (bv. +0.2m systematisch).

### C. Mechanism 3: Spectrum-partitie (wind sea + swell apart wegen)
Niet alleen totale Hs maar **per partition height, period en direction**. Tobias splitst expliciet "windhoogte" en "swell-component" — Sponsler maakt het tot core-methodologie.

### D. Mechanism 4: Wave energy flux als size-proxy
P = 0.49 × Hs² × Te. Eén-getal die **periode én hoogte** combineert in fysische eenheid. Lange-periode dag met lagere Hs kan dezelfde flux hebben als korte-periode dag met hogere Hs maar de **surfability is heel anders**.

### E. Mechanism 5: Iribarren-based breaker-type prediction
ξ-getal bepaalt spilling vs plunging. Spilling op Hollandse beach = standaard; plunging-tendency (ξ>0.25) = bonus-quality signaal.

### F. Mechanism 6: Wave age check (cp/U10)
Wind-zee met cp/U10 < 0.83 = niet-surfbare jonge windsea (model rapporteert wel "wave_height" maar het is geen surfable wave). Tobias' empirische cutoff (T>5s) is een proxy hiervoor.

### G. Mechanism 7: Tide-flank logic (rising/falling × spring/neap × bank-zone)
Pro's redeneren over **welke bank werkt bij welke tide-hoogte**. Mid-rising tide = sweet spot voor de meeste Hollandse beach-breaks. Onze normalized tide (0-1) mist de flank-richting.

### H. Mechanism 8: Diurnal wind-decay (evening glass-off)
Modellen onderschatten de wind-drop bij zonsondergang door grid-resolutie. Pro's tellen dit handmatig bij ("avond longboarden prima"). Heuristiek: van 20:00 tot 22:00 op een heldere dag in mei = wind -2 tot -4 kn versus modeloutput.

### I. Mechanism 9: Pier/headland refractie en wrap-around
Pier IJmuiden blokkeert N-swell maar amplificeert NW-wrap-around voor Noordwijk. Geen Open-Meteo grid resolveert dit; alleen SWAN of empirische tabellen kunnen.

### J. Mechanism 10: Sandbank-tide-window mapping
Outer bank werkt op laag, inner bank werkt op hoog. Pro's hebben mentale tabellen per spot. Dit is **niet** in publieke data beschikbaar; vereist eigen jaar-of-meer-data verzameling.

### K. Mechanism 11: Bias-correctie per windrichting
Onze observatie (`research_benchmark_comparison.md`): model Tp consistent 1-2s laag versus Tobias. Pro's kennen die bias en corrigeren mentaal. Sponsler doet dit expliciet via calibration coefficients.

### L. Mechanism 12: Vertelvorm / verhaal in plaats van getallen
Tobias' SMS is **prozaisch** met expliciete windows ("Nwijk 14-16u of na 19:30u") en **kwalitatieve woorden** ("genoeg hoogte", "leuke lijntjes"). Geen score, geen alert-threshold. De surfer leest dit als coherent verhaal, niet als data-dump.

---

## 13. Feasibility per mechanisme voor Python-implementatie

| # | Mechanisme | Feasibility | Data needed | Impact | Prioriteit |
|---|---|---|---|---|---|
| 1 | Multi-model triangulatie | **Middel** | Open-Meteo (al), ECMWF Open Data, KNMI Harmonie via API | Hoog (onzekerheid expliciet) | Hoog |
| 2 | Boei-as-truth feedback | **Hoog** | RWS Waterinfo + Vlaamse Banken boeien (al gratis) | Hoog (binnen-dag correctie) | Zeer hoog |
| 3 | Spectrum-partitie | **Hoog** | Open-Meteo geeft al `swell_wave_*` en `wind_wave_*` apart | Zeer hoog | Zeer hoog |
| 4 | Wave energy flux | **Hoog** | Hs en Te uit Open-Meteo | Hoog (één goede size-proxy) | Hoog |
| 5 | Iribarren breaker-type | **Middel** | Beach slope schatting (~0.02 voor Noordwijk, vast), Hs, T | Middel (quality-modifier) | Middel |
| 6 | Wave age check | **Hoog** | Tp en wind-snelheid (al beschikbaar) | Hoog (filter pure windhash uit) | Hoog |
| 7 | Tide-flank logic | **Hoog** | Tide raw uit RWS, derivatieven berekenen | Hoog (windows precieser) | Zeer hoog |
| 8 | Diurnal wind-decay | **Hoog** | Tijd van zonsondergang (al), wind richting (al), cloud cover (Open-Meteo) | Middel-Hoog | Hoog |
| 9 | Pier refractie | **Laag** | SWAN-runs of empirische tabellen per swell-richting; *moeilijk zonder Deltares-data* | Middel (verbetert N-NW dagen) | Middel |
| 10 | Sandbank-tide mapping | **Zeer laag** | Vereist 1+ jaar eigen waarneming gekoppeld aan score-resultaten | Hoog *als* gedaan, maar lange aanlooptijd | Laag (lange termijn) |
| 11 | Bias-correctie per windrichting | **Hoog** | Logging van model-vs-werkelijkheid per richting; statistische correctie | Hoog | Hoog |
| 12 | Vertelvorm output | **Hoog** | LLM-templated text op basis van numerieke output | Zeer hoog (gebruikerservaring) | Zeer hoog |

### 13.1 Aanbevolen top-5 voor implementatie volgorde

**Sprint 1 — direct uitvoerbaar met huidige data:**
1. **Spectrum-partitie (#3)**: Gebruik Open-Meteo's `swell_wave_height` + `wind_wave_height` apart. Compute aparte sub-scores en combineer. Een **schone 0.6m swell + 0.4m windsea** moet hoger scoren dan een **vuile 1.0m gemengde Hs** met dezelfde totaal-hoogte.
2. **Wave energy flux (#4)**: Replace pure `Hs` with `P = 0.49 × Hs² × Te` als size-component in score. Reflecteert wave power realistic.
3. **Wave age filter (#6)**: Voor elke uur, bereken cp/U10. Als cp/U10 < 0.83 én T < 5s → cap de score op 30 (= "windhash, niet echt surfbaar").

**Sprint 2 — vereist code-uitbreiding:**
4. **Tide-flank logic (#7)**: In plaats van `tide_normalized` 0-1, gebruik **(tide_value, tide_velocity, time_to_next_high, time_to_next_low)** als features. Per spot heuristiek: Noordwijk werkt het beste bij **mid-rising of mid-falling**.
5. **Boei-feedback (#2)**: Pull live IJG1 + EPL elke uur (RWS API of Open-Meteo Marine). Als boei-Hs > model + 0.2m, blast scaling factor op (1 + diff/model_Hs) op forecasts voor de komende 6-12u.

**Sprint 3 — verfijning:**
6. **Diurnal wind-decay correctie (#8)**: Als (uur 19-22) AND (sunset_hour - 1 ≤ uur ≤ sunset_hour + 1) AND (cloud_cover < 50%): wind -= 2 kn.
7. **Multi-model triangulatie (#1)**: ECMWF + ICON + GFS Wave naast elkaar. Spread berekenen, in output communiceren.
8. **Vertelvorm output (#12)**: Output-templating met natuurlijke taal (zoals Tobias' SMS).

**Backlog / lange termijn:**
9. **Bias-correctie per windrichting (#11)**: Vereist 6+ maanden logging.
10. **Iribarren breaker-type (#5)**: Cosmetic quality-modifier.
11. **Pier-refractie (#9)**: Wachten op SWAN data of empirisch tabel uit eigen waarneming.
12. **Sandbank-tide mapping (#10)**: Idem, 1+ jaar inzamelen.

---

## 14. Concrete codeerbare regels (cheat-sheet)

Als concrete formules om in `src/scoring/` op te nemen:

```python
# Spectrum-partitie weighting
def score_combined(swell_h, swell_T, wind_h, wind_T, wind_speed):
    swell_energy = 0.49 * swell_h**2 * swell_T          # kW/m
    wind_energy = 0.49 * wind_h**2 * wind_T             # kW/m
    swell_quality = 1.0 if swell_T > 7 else swell_T / 7
    wind_quality = 0.3 if wind_T < 5 else 0.7           # windsea altijd minder dan swell
    return (swell_energy * swell_quality + wind_energy * wind_quality) * conditions_mult

# Wave age filter
def is_real_wave(T, U10):
    cp = 1.56 * T
    wave_age = cp / max(U10, 1)
    return wave_age >= 0.83

# Tide-flank features
def tide_features(tide_values_24h, current_hour):
    current = tide_values_24h[current_hour]
    velocity = tide_values_24h[current_hour+1] - tide_values_24h[current_hour-1]   # m/2h
    is_rising = velocity > 0
    # Sweet-spot proxy: mid-tide AND rising
    sweet_spot_score = (1 - abs(current - 0.5) * 2) * (1.2 if is_rising else 1.0)
    return sweet_spot_score

# Wave energy flux (replaces Hs as size metric)
def wave_power(Hs, Te):
    return 0.49 * Hs**2 * Te    # kW/m

# Iribarren number (quality modifier)
def iribarren(Hs, T, beach_slope=0.02):
    L0 = 1.56 * T**2
    return beach_slope / (Hs / L0)**0.5

def breaker_quality_bonus(xi):
    if xi < 0.15:
        return 0.9   # heavy spilling, mushy
    elif xi < 0.25:
        return 1.0   # standard spilling
    elif xi < 0.5:
        return 1.15  # tending plunging
    else:
        return 1.0   # too plunging/surging, edge cases

# Diurnal wind decay heuristic
def wind_decay_adjustment(hour, sunset_hour, cloud_cover_pct):
    if sunset_hour - 2 <= hour <= sunset_hour + 1 and cloud_cover_pct < 50:
        return -3  # subtract 3 kn
    return 0
```

---

## 15. Slotsamenvatting

Een professionele forecaster doet drie dingen die geen consumer-app vandaag doet:

1. **Triangulatie**: ze checken meerdere modellen, meerdere boeien, gisteren-versus-vandaag, klimatologie. **Eén model = onbetrouwbaar** voor een belangrijke beslissing.

2. **Fysica boven aggregate metrics**: ze rekenen met wave energy flux, partition height, wave age, Iribarren-getal, refractie-hoek. De **enkele "Hs"** uit een API is een grove samenvatting die surfability obscure.

3. **Vertelvorm en uncertainty**: ze rapporteren windows met tijd-precisie ("14-16u of na 19:30u"), kwalitatieve labels ("longboarden prima"), en impliciete onzekerheid door taalkeuze ("kort moment", "kan ook nog"). Een score-cijfer is reductionistisch en mist de **operationele beslissingswaarde** die een surfer nodig heeft ("ga ik om 14u of om 19u?").

Voor SurfWeerWorkflow: de **grootste sprong vooruit** zit in (a) spectrum-partitie + wave-energy-flux + wave-age filter implementeren (één-twee dagen werk, hoge impact), en (b) tide-flank logic toevoegen met derived rising/falling/mid-features (één dag werk, zeer hoge impact). Het output-format vermenselijken (LLM-templated tekst i.p.v. score-tabel) is de derde stap die de gebruikerservaring transformeert.

De ML-aanpak van Surfline (LOTUS leert van honderdduizenden menselijke observaties) is op onze schaal niet realistisch — we hebben geen training data. Maar **hard-coded heuristieken uit deze 12-punts lijst** kunnen 80% van het gat naar Tobias dichten zonder ML.

---

## Bronnenlijst

### WSL en contest forecasters
- [WSL News](https://www.worldsurfleague.com/news)
- [Boardriders – 2026 WSL CT Explained](https://www.boardriders.com/en-gb/world-surf-league-championship-tour-explained/)
- [WSL – Pipeline finale 2026](https://www.worldsurfleague.com/posts/542489/world-surf-league-announces-return-to-pipeline-for-championship-tour-finale-starting-in-2026)
- [Surfertoday – WSL 2026 format changes](https://www.surfertoday.com/surfing/wsl-announces-major-format-changes-for-2026-championship-tour)

### Mark Sponsler / Stormsurf
- [Lookout Santa Cruz – The Surf Whisperer](https://lookout.co/surfing-mark-sponsler-stormsurf-forecasts-have-earned-a-devoted-following-among-big-wave-surfers/story)
- [Florida Surf Museum – Mark Sponsler profile](https://floridasurfmuseum.org/talking-story/the-florida-connection-mark-sponsler-wave-whisperer)
- [Stormsurf – About](https://www.stormsurf.com/page2/services/about.html)
- [Stormsurf – Tutorials menu](https://www.stormsurf.com/page2/tutorials/menu.html)
- [Stormsurf – Wave Models tutorial](https://www.stormsurf.com/page2/tutorials/wam.shtml)
- [Off The Lip Radio – Mark Sponsler interview](https://www.offthelipradio.com/podcast/2022/10/17/otl702-mark-sponsler)

### Pat Caldwell / NWS Honolulu
- [Hana Hou – The Surf Sage profile](https://hanahou.com/24.2/the-surf-sage)
- [NWS HFO Surf Forecast](https://www.weather.gov/hfo/SRF)
- [NWS HFO – New Statewide Surf Forecast 2020](https://www.weather.gov/hfo/statesurf2020_update)
- [NCEI – Inside NCEI: Patrick Caldwell](https://www.ncei.noaa.gov/news/inside-ncei-regional-science-officer-patrick-caldwell)
- [Surf News Network – Pat Caldwell page](https://www.surfnewsnetwork.com/pat-caldwell/)
- [Hawaii Sea Grant – Surf Forecasting](https://seagrant.soest.hawaii.edu/surf-forecasting/)

### Sean Collins / Surfline / LOLA / LOTUS
- [Wikipedia – Sean Collins](https://en.wikipedia.org/wiki/Sean_Collins_(surf_forecaster))
- [Surfline – Sean Collins 1952-2011](https://www.surfline.com/surf-news/sean-collins-1952-2011/73239)
- [ESPN – Surf-forecasting pioneer Sean Collins dies](https://www.espn.com/action/surfing/story/_/id/7391373/surf-forecasting-pioneer-sean-collins-dies-59)
- [Surfline – From the Vault: Sean Collins' Surfology 101](https://www.surfline.com/surf-news/from-the-vault-sean-collins-surfology-101/42751)
- [Surfline – LOTUS feature](https://www.surfline.com/lp/whatsnew/features/lotus-swell-model)
- [Surfline – What is LOLA](http://www.surfline.com/surfline/lolaarchive/lola_info.cfm)
- [Surfline – Out With the Old, in With the New](https://www.surfline.com/surf-news/what-does-lola-stand-for/87781)
- [Surfline Labs – Machine Learning for Surf Forecasting](https://medium.com/surfline-labs/machine-learning-for-surf-forecasting-4a007f13b3e3)
- [Surfline – Redefining Surf Forecast Accuracy](https://www.surfline.com/surf-news/surf-forecast-accuracy/50389)
- [Surfline – Surf Ratings & Colors](https://support.surfline.com/hc/en-us/articles/36277684017819-Surf-Ratings-Colors)
- [Surfline – Wave Consistency](https://support.surfline.com/hc/en-us/articles/20350539606683-Wave-Consistency)
- [Surfline – Advanced Swell Spectra](https://support.surfline.com/hc/en-us/articles/20294130483099-Advanced-Swell-Swell-Spectra)
- [Surfline – Forecasting Tutorial: Wave Period Explained](https://www.surfline.com/surf-news/forecasting-tutorial-wave-period-explained/96751)
- [Surfline – Conflicting Reports](https://www.surfline.com/surf-science/conflicting-surf-reports---forecaster-blog_52725/)

### Magicseaweed / Swellnet
- [MSW – Quick Forecast Tutorial](https://magicseaweed.com/docs/forecasting/66/a-quick-forecast-tutorial/10123/)
- [MSW – What is a Swell Model](https://magicseaweed.com/news/what-is-a-swell-model/6946/)
- [MSW – Understanding Swell Models](https://magicseaweed.com/docs/swell-models/71/)
- [Swellnet – Understanding our new forecasting system](https://www.swellnet.com/news/swellnet-analysis/2013/10/21/understanding-our-new-forecasting-system)
- [Swellnet – Hot air and choppy surf (sea breeze)](https://www.swellnet.com/news/swellnet-dispatch/2013/07/18/hot-air-and-choppy-surf-making-sense-sea-breeze)
- [Surf Mastery – Ben Macartney forecasting](https://surfmastery.com/podcast/ben-macartney-surf-forecaster)

### Tobias van Tellingen / surfweer.nl
- [surfweer.nl](https://surfweer.nl/)
- [surfweer.nl – Spectra](https://surfweer.nl/surf/spectra/)
- [surfweer.nl – Weerlinks](https://surfweer.nl/weerlinks/)
- [surfweer.nl – Surfweer laatste week juni 2023](https://surfweer.nl/surf/surfweer-laatste-week-van-juni-2023/)
- [surfweer.nl – Een weekje zomer wind swell](https://surfweer.nl/surf/weekje-zomer-wind-swell/)
- [surfweer.nl – Surfweer flat Time 2 Paddle](https://surfweer.nl/surf/surfweer-flat-____-time-2-paddle/)
- [surfweer.nl – Zaterdag 26 januari 2013](https://surfweer.nl/surf/het-surfweer-van-zaterdag-26-januari-201/)
- [Tasha's Surfcamp – Tobias Piet Paulusma](https://tashasurfcamp.com/tobias-de-piet-paulusma-van-het-surfen)
- [KNRM – Kitesurfweer (Tobias quote)](https://www.knrm.nl/blog/tips/kitesurfweer)
- [Ridersguide – Haal meer uit windvoorspellingen](https://ridersguide.nl/haal-meer-uit-de-windvoorspellingen/)
- [Goede Golven – Bronnen](https://goedegolven.nl/sources/)
- [Omroep West – Scheveningen lange zeedeining](https://www.omroepwest.nl/nieuws/4045711/overleden-watersporters-scheveningen-mogelijk-verrast-door-lange-zeedeining)
- [Instagram @surfweer](https://www.instagram.com/surfweer/?hl=nl)
- [LinkedIn Tobias van Tellingen](https://nl.linkedin.com/in/tellingen)

### Wave physics / spectra / breaker-types
- [Wikipedia – Iribarren number](https://en.wikipedia.org/wiki/Iribarren_number)
- [Coastal Wiki – Surf similarity parameter](https://www.coastalwiki.org/wiki/Surf_similarity_parameter)
- [MDPI – Wave Breaker Types on a Smooth Impermeable 1:10 Slope](https://www.mdpi.com/2077-1312/8/4/296)
- [Surf Simply – Why and How Waves Break](https://surfsimply.com/magazine/why-and-how-waves-break)
- [Wikipedia – Pierson-Moskowitz spectrum](https://en.wikipedia.org/wiki/Pierson%E2%80%93Moskowitz_spectrum)
- [Geosciences LibreTexts – Ocean Wave Spectra](https://geo.libretexts.org/Bookshelves/Oceanography/Introduction_to_Physical_Oceanography_(Stewart)/16:_Ocean_Waves/16.4:_Ocean-Wave_Spectra)
- [AMS – Revisiting the Pierson-Moskowitz Asymptotic Limits](https://journals.ametsoc.org/view/journals/phoc/33/7/1520-0485_2003_033_1301_rtpalf_2.0.co_2.xml)
- [ScienceDirect – Pierson-Moskowitz Spectrum overview](https://www.sciencedirect.com/topics/engineering/pierson-moskowitz-spectrum)
- [ScienceDirect – Estimating wave energy flux](https://www.sciencedirect.com/science/article/abs/pii/S0960148120304560)
- [ECMWF – Wave energy flux product](https://charts.ecmwf.int/products/medium-wave-energy-flux)
- [arXiv – Refraction of swell by surface currents](https://arxiv.org/abs/1410.1676)
- [UBC ATSC113 – Swell](https://www.eoas.ubc.ca/courses/atsc113/sailing/met_concepts/08-met-waves/8d-swell/index.html)

### Wind/sea breeze / time-of-day
- [Surf Simply – Reading the Wind](https://surfsimply.com/magazine/reading-the-wind)
- [Science of Surfing – Why offshore in the afternoon](https://www.scienceofsurfing.com/p/why-is-the-wind-offshore-in-the-afternoon)
- [Encyclopedia of Surfing – glass-off](https://eos.surf/entries/glass-off/)
- [Surfertoday – Why offshore winds are good](https://www.surfertoday.com/surfing/why-are-offshore-winds-good-for-surfing)
- [Quiver – Offshore vs Onshore](https://www.quiversurf.app/learn/offshore-vs-onshore-wind-surfing)
- [Foam Magazine – Onshore Offshore Wind](https://foammagazine.com/onshore-offshore-wind/)

### Wave-current interaction
- [PredictWind – Wind against Current](https://www.predictwind.com/glossary/w/wind-against-current)
- [Annual Reviews – Wind, Waves, and Surface Currents](https://www.annualreviews.org/content/journals/10.1146/annurev-marine-040323-034908)
- [Bluewater Miles – Waves and your boat](https://bluewatermiles.com/extras/waves/)

### NL-Noordzee fysica
- [Springer Ocean Dynamics – Numerical simulations of wave refraction in the North Sea](https://link.springer.com/content/pdf/10.1007/BF02226339.pdf)
- [ResearchGate – Bathymetry IJmuiden Noordwijk](https://www.researchgate.net/figure/Bathymetry-map-of-the-North-Sea-near-IJmuiden-Netherlands-indicating-the-location-of_fig2_46658000)
- [Springer Geo-Marine Letters – Sandbank occurrence Dutch shelf](https://link.springer.com/article/10.1007/s00367-008-0105-7)
- [ResearchGate – Beach systems central Netherlands coast](https://www.researchgate.net/publication/222475835_Beach_systems_of_the_central_Netherlands_coast_Processes_morphology_and_structural_impacts_in_a_storm_driven_multi-bar_system)
- [ScienceDirect – Lower shoreface of the Dutch coast](https://www.sciencedirect.com/science/article/pii/S096456912200343X)
- [BODC – Monitoring Network Flemish Banks](https://www.bodc.ac.uk/resources/inventories/edmed/report/5619/)
- [Health.belgium.be – Habitats Directive Areas Belgian North Sea](https://www.health.belgium.be/en/habitats-directive-areas-belgian-part-north-sea)
- [Surf Atlas – Scheveningen Surf Guide](https://thesurfatlas.com/surfing-in-the-netherlands/scheveningen-surf/)

### Wave models (WW3, SWAN, WAM, ICON)
- [NOAA NCEP – Modeling nearshore wave processes (van der Westhuysen)](https://polar.ncep.noaa.gov/mmab/papers/tn298/MMAB_298.pdf)
- [Academia.edu – Performance of WW3 and SWAN in the North Sea](https://www.academia.edu/37169342/PERFORMANCE_OF_WAVEWATCH_III_AND_SWAN_MODELS_IN_THE_NORTH_SEA)
- [Surfertoday – WaveWatch global model](https://www.surfertoday.com/surfing/wavewatch-wind-wave-forecast-model)
- [Svasek Hydraulics – SWAN](https://www.svasek.nl/en/model-research/swan/)
- [Open-Meteo – Marine API docs](https://open-meteo.com/en/docs/marine-weather-api)
- [Open-Meteo – DWD ICON API](https://open-meteo.com/en/docs/dwd-api)
- [natESM – WAM documentation](https://nat-esm-system.dkrz.de/Optional%20Components/WAM.html)

### Forecast uncertainty / ensemble
- [ECMWF – Quantifying forecast uncertainty](https://www.ecmwf.int/en/research/modelling-and-prediction/quantifying-forecast-uncertainty)
- [ECMWF – Stochastic Parameter Perturbations (SPP)](https://www.ecmwf.int/en/newsletter/181/earth-system-science/improving-physical-consistency-ensemble-forecasts-using-spp)
- [arXiv – Singular vector ensemble forecasting](https://arxiv.org/pdf/physics/0402027)

### Heuristics / human forecaster decision making
- [AMS – Weather Forecasting by Humans: Heuristics and Decision Making](https://journals.ametsoc.org/view/journals/wefo/19/6/waf-821_1.xml)
- [Wavescultures – Surf Forecasting Science and Art](https://wavescultures.com/articles/surf-forecasting-science-art-wave-predictions/)

### Algemene surf forecast educatie
- [Surfertoday – What is surf forecasting](https://www.surfertoday.com/surfing/what-is-surf-forecasting)
- [Surfline – Difference between swell and surf](https://support.surfline.com/hc/en-us/articles/4410126820891-Difference-between-swell-and-surf)
- [Surfline – Wave Energy](https://support.surfline.com/hc/en-us/articles/20352744481947-Wave-Energy)
- [UHSLC – Why surf heights vary in Hawaii](https://uhslc.soest.hawaii.edu/outreach/vary/why_surf_varies.html)
- [NDBC – FAQ wave calculations](https://www.ndbc.noaa.gov/faq/wavecalc.shtml)
- [NDBC – FAQ windsea](https://www.ndbc.noaa.gov/faq/windsea.shtml)

---

**Document statistieken:** ~4900 woorden, 15 hoofdsecties, 12 mechanismen, 12-rij feasibility-tabel, ~80 unieke bronnen.
