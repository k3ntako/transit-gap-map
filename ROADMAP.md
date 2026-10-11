# Roadmap

The map currently answers one question well: *how far is it to walk to the nearest
train station, along real streets?* The goal from here is to answer a harder one:
**which parts of New York are actually under-served?**

Those are not the same question, and the gap between them organizes everything
below.

## The reframe

"Under-served" is a product of three things. The map today measures a weak
version of the first and nothing of the other two.

| Axis | Question | Status |
|---|---|---|
| **Access cost** | How hard is it to get to a platform? | Partial — distance to a station *point*, plus the expected wait once there; still ignores stairs, slope and which door |
| **Service quality** | Once you are there, what do you get? | **Scheduled frequency done** — trains per hour per station per window, and the wait charged on the graph. Ridership and crowding are not in it |
| **Dependence** | How much do the people there need it? | Population, plus car ownership and poverty shipped as weightable terms — and both measured as *lowering* agreement with agency plans |

A station is not a binary, and a person near a bad station can be worse off than
a person a bit further from a good one. The second row is where most of the
value was, and GTFS was the cheap way to get it.

Three design principles, the third learned the hard way:

1. **Ship raw normalized metrics, score in the browser.** Anything the user
   might want to re-weight — density floors, score weights, which metrics
   count — should recompute client-side from per-tract fields, with no pipeline
   round trip. Only things that need the walk graph or a transit graph get
   pre-computed. Corollary now that fields cost ~5 KB gzipped each: never ship
   a column the browser can derive. `excess` and `score` are not in the
   payload for exactly this reason.
2. **Percentile-rank the skewed metric; keep a meaningful one in its own
   units.** The original form of this principle said to rank everything, and
   that was wrong in the one place it mattered. Density, car ownership and
   poverty all want ranking — none has a tail that *is* the finding. Walk time
   does: ten times the walk is 1.4 times the percentile rank, so ranking it
   destroyed the only part carrying information. **Settled 2026-10-10** in
   favor of excess person-minutes; see `verify.py` for the test that settled
   it.
3. **A score change has to be measured against something outside the map.**
   `verify.alignment()` is that something, and it gates the build. Nine of ten
   candidate scores cleared its floor and the one that shipped for three
   commits did not. No future score arrives without a row in that table.

Phases 0 and 1 are shipped and have been dropped from this file. The numbering
of what is left is deliberately unchanged, because commit messages, the README
and the sections below all refer to it.

---

## Small things, no new data

Everything here reads fields that are already in `tracts.geojson` or touches
only `web/`. None of it depends on a phase below.

- **Filter by class, and a minimum-walk floor.** The display floors fade tracts
  under a density or population threshold; the same mechanism should fade by
  *class* — show only the worst two score bands, or only tracts over N minutes'
  walk. `belowFloor()` is already an expression consumed by both
  `fillOpacity()` and `lineOpacity()`, so a minute floor is a third clause and
  a class filter is an `["in", ["get", "bi"], …]` test; the permalink gains one
  key and ignores it when absent. Two cautions: **fade, never hide**, for the
  same reason the density floor fades — a missing tract reads as missing data,
  not as a filtered one; and a class filter stacked on a floor can empty the
  map, so the legend should state how many tracts are currently showing.
- **Collapse the panels.** The panels occupy two fixed columns, 310 px on the
  left and 232 px on the right, and the title card is the largest of them and
  the least re-read. Making each a `<details>` is
  trivial; the coupling that is not is **`fitOptions()`, whose padding is
  hardcoded to the panel widths** (330 left, 250 right above 1200 px). Collapse
  a panel without re-fitting and the recenter button frames the city off-center.
  Either re-fit on toggle or derive the padding from the panels' measured
  widths. Open state is viewer chrome rather than a deliberate choice, so it
  does not obviously belong in the permalink — decide that before adding a key.
- **Narrow the legend to the matrix.** `#legend` is 310 px wide for a bivariate
  grid that is 108 px. The panel is sized by its *labels*, not its swatches, so
  this is not purely cosmetic: narrowing it forces the three-class ramp rows and
  the seven score rows to wrap or abbreviate. Measure the widest label in each
  of `renderLegend()`'s cases first, then pick the width — and check the
  under-service legend, which has the most rows and now the longest labels,
  since the bands are numbers in person-minutes rather than percentiles.
- **The title card is getting long.** It now carries the hero number, six stat
  rows, the straight-line benchmark, the commuter-rail fare note and the
  external-validity paragraph. Each earned its place individually and the
  result is a wall. This is the strongest argument for the collapsible panels
  above, and it suggests the caveats want to be a `<details>` of their own
  rather than three paragraphs in a row.

## Publishing the map

Not a phase, and not blocked by one. `web/` is three source files reading
2.9 MB of generated payload, with no server-side anything — so the hosting
question is already answered and the real question is the build.

- **The obstacle is the data, not the site.** `web/data/` is gitignored because
  generated artifacts are not source, so a clean checkout cannot serve the map
  until `make all` has run — and that needs a 153 MB OSM extract, a Census API
  key, and a ~5-minute graph parse. CI cannot do it from nothing without a
  secret and a large download.
- **Three ways, and a recommendation.** (a) Build locally, push only
  `web/` plus `web/data/` to a `gh-pages` branch — no secret leaves the
  machine, `main` stays clean, and a deploy is one scripted push. (b) Build in
  CI with the Census key as a repository secret and a cache for the `.pbf` —
  more moving parts and a slow job for a payload that changes when ACS does,
  which is annually. (c) Commit the payloads to `main` — rejected, it deletes
  the distinction the gitignore exists to make. **Recommend (a)**, with a
  `make publish` target so the deploy is reproducible and not a shell-history
  artifact.
- **Payload, measured:** 2.8 MB on disk — `tracts.geojson` 1.7 MB, `walksheds`
  816 KB, `stations` 138 KB, two surface PNGs at 62 KB, `stats.json` 24 KB,
  `palette.json` 4 KB. **Over the wire the first paint is 406 KB**, because gzip
  takes the GeoJSON down by a factor of six; the surface PNG is a further 62 KB
  that arrives in the background, after the map goes idle, and only ever one of
  the two. The 406 KB is the number the reader experiences, and it is why no
  tiling and no CDN decision is required. It is also why the host has to be one
  that compresses: `make serve` does not, so the map feels slower in
  development than in production.
- **AGPL section 13 is already satisfied, by construction.** A deployed
  instance must offer its source; `config.SOURCE_URL` reaches the browser
  through `stats.json` and the title card renders it. Confirm it points at the
  public repository before the first deploy, because that link becoming wrong
  is the one way hosting creates a license problem.
- **Cache headers matter here.** The payload filenames are stable, so a long
  `max-age` serves stale data after a rebuild. `stats.json` already carries
  `built_at`; either serve the data directory with a short max-age or add a
  build id to the fetch URLs.
- **Check the basemap terms before any traffic.** CARTO's `positron` and
  `dark-matter` tiles are keyless and already attributed in the title card, but
  keyless is not the same as unlimited. If the map ever gets real traffic this
  is the first thing that breaks.

## What phases 2 and 3 left behind

Both phases shipped (`64ed3f7` through `d96e5fe`). Three things inside them did
not, and they are here rather than deleted because each is a real piece of work
with its findings already paid for.

### Ridership and crowding

Still the right next thing on the service-quality axis, and now computable,
because crowding is a ratio and the frequency work built the denominator:

```
pressure = riders per hour / (trains per hour × capacity per train)
```

Ridership on its own is a popularity measure, and the places this map exists to
find are unpopular by construction — a station with few riders is as likely to
be badly served as to be unneeded. With trains per hour in hand the supply side
exists and the ratio means something.

**Three things measured 2026-10-10 that change the shape of this job**, all of
which the earlier version of this section had wrong:

- **It cannot be downloaded.** `wujg-7c2s` (through 2024) holds **120,855,567
  rows** and `5wq4-mkjj` (2025 onward) another **45,962,558** — one row per
  station × hour × payment method × fare class. This has to be a server-side
  SoQL aggregation (`$select=station_complex_id,sum(ridership)&$group=…`), not a
  fetch-then-group. The existing conditional-GET cache in `fetch.py` does not
  help with a query that has no file behind it.
- **The join key is not `gtfs_stop_id`.** It is `station_complex_id`, which
  joins to the station table's `complex_id`, not to the per-station key
  everything else in the pipeline uses. Times Square alone is four parent
  stations with separate frequencies (34.0, 35.0, 28.5 and 18.0 trains/hour at
  the AM peak), so complex-level ridership over station-level service needs an
  explicit rule — summing the complex's stations is the defensible one, and it
  should be written down rather than assumed.
- **Capacity per train is in none of the data.** Not in GTFS, not in the
  ridership feed. It needs car counts and car lengths per route — IRT cars are
  51 ft and BMT/IND cars 60–75 ft — which means a documented constant per
  division and an honest error bar, or a pressure index expressed in
  riders-per-train rather than percent-of-capacity. The second is weaker and
  defensible from the data alone; the first is what people want and is partly
  an assumption.

The honest limit stated up front, unchanged: these are *fare transactions at a
station*, not passengers on a train. They miss transfers inside the fare gates,
say nothing about direction, and a packed train passing through a quiet station
is invisible to them. A station-level pressure index is defensible; "the 4 train
is at 112% of capacity through Grand Central" is not, from this dataset.

Where it changes the answer: a tract whose only option is a station already at
the edge of its scheduled capacity is worse off than its walk time suggests,
because the next increment of service there is harder to deliver. That is a
distinct condition from "far from a station" and belongs as its own deficit
term, not folded into frequency. Free side benefit: ridership is the sanity
check on Phase 4's hub list — if a chosen hub is not among the busiest stations
in the city, it is the wrong hub.

### The spatial statistic, after the component count

Cluster size and cluster population now ship, and that was the cheap 90%. A
**Local Moran's I** cluster map (`libpysal` for the weights, `esda` for the
statistic) is the formal way to say the same thing and distinguishes a real hot
spot from a run of noise. It earns its place only now that the plain component
count is on the page, and it is harder to explain, so it needs a reason beyond
rigor. The reason to want it: the largest block is **430 tracts and 1.31M
people**, which is so large it is arguably not a project unit but "the whole
outer ring" — a statistic that can split that band into genuine hot spots would
be saying something the component count cannot.

### Cluster-level precision in the alignment test

`verify.alignment()` compares a per-tract score against corridors that are runs
of tracts, so a per-tract precision figure is measuring a point against a line.
Precision at the **cluster** level is the fairer version, and probably a kinder
one to every score in the table. The components exist now, so this is a group-by
rather than new machinery. Worth doing before the 80% figure is quoted anywhere
load-bearing.

### One thing deliberately not done

The roadmap asked for both a multiplicative and an additive form of the score,
defaulting to the product. Only the product shipped. The additive form is three
lines, but it needs a row in the alignment table before it is offered, and a
sum's known failure — giving a high score to empty land with terrible access —
is the thing the product exists to prevent. If it is added, it is added with its
measurement.

## Phase 4 — travel time to hubs

The item that most changes the answer, and the most work. Distance to the nearest
station treats a station as a destination; it is really an on-ramp. Eastern Queens
is a short walk from an LIRR stop that costs $10 and runs hourly; Bay Ridge is a
short walk from the R, which is a 50-minute local ride to Midtown. Only
time-to-somewhere separates those.

**Approach — a frequency-based static transit graph, not a full RAPTOR.**

- Nodes: GTFS stops. Ride edges: median scheduled runtime between consecutive
  stops on each route pattern. Transfer edges: walk time between nearby stops
  plus the expected wait at the boarding stop. Access edges: the existing walk
  graph, so every one of the 1.84M pedestrian nodes connects in.
- Then **one Dijkstra from the hub** over this graph gives time-to-hub for every
  node in the city, and `pipeline/aggregate.py`'s median-over-sample-points
  machinery converts that to per-tract values unchanged. This reuses the entire
  existing architecture rather than bolting a routing server onto it.
- Express service falls out for free: express patterns have faster ride edges,
  so an express stop scores better without any special-casing.
- **Honest limitation:** a static graph with expected waits approximates a
  typical weekday peak. It cannot represent departure-time effects (just-missed
  connections, timed transfers). Say so in the methodology rather than implying
  schedule-exact routing. If that becomes the binding constraint, a proper RAPTOR
  over `stop_times` is the upgrade — tractable in numpy, but a project of its own.
- Hubs to offer: Times Sq–42 St, Grand Central, Atlantic Av–Barclays Ctr,
  Fulton St, Jackson Hts–Roosevelt Av, 125 St. Pre-compute one surface per hub
  (cheap — one Dijkstra each) and let the user switch, rather than routing live.
- **Jobs accessibility** is the generalization of "a hub": LEHD LODES8 WAC
  (verified reachable) gives jobs per block, so *jobs reachable within 45
  minutes* is the standard accessibility measure and needs no arbitrary choice of
  destination. Worth doing once the transit graph exists.

### Secondary hubs, and the orbital problem

Phase 4 as written above picks *a* hub, which quietly assumes the thing this
system is worst at. The subway is radial: it is built to move people into
Manhattan and back out, so a lateral trip is either routed through Manhattan or
handed to a bus. Flushing to Kew Gardens is a few miles and an awkward trip;
most of Brooklyn to most of Queens is worse. A single-hub map would score both
origins as *well served*, because each is a short ride from Midtown.

- **The architecture already handles it, linearly.** Time-to-hub is one Dijkstra
  from one source over the static transit graph. Ten hubs is ten passes, which
  is seconds, not a redesign. Publish per-hub times *and* the minimum across
  hubs — "time to your nearest center" is the polycentric question, and it is
  the one a resident of Jamaica or Fordham actually has.
- **Pick the hub list from data, not from feel.** Candidates: Downtown
  Brooklyn, Jamaica, Flushing, Long Island City, Fordham, St. George, 149th
  St–Grand Concourse. The ridership join is the arbiter — a proposed hub that
  is not among the busiest stations in the city is the wrong hub — which makes
  *Ridership and crowding* a dependency of this bullet rather than a parallel
  nice-to-have.
- **The layer that makes the radial bias visible is a ratio, not a time.**
  Transit time divided by the straight-line time at a reference speed — a
  *detour factor*. This project already has the pattern: `pipeline/verify.py`
  requires an inflated network-to-straight-line ratio at the barrier probes,
  and it is the check that proves the walk routing is real. The same ratio over
  the transit graph is what turns "Flushing to Kew Gardens is annoying" into a
  number, and it will be highest exactly where the orbital gap is worst.
- **The metric that dissolves the hub question entirely** is cumulative
  opportunity: *jobs reachable within 45 minutes*. It needs no chosen center,
  it is the standard measure in the accessibility literature, and it needs LEHD
  LODES workplace-area job counts by tract, which are published and not yet
  probed here. Cost is the catch — one Dijkstra per origin rather than per hub,
  so roughly 2,300 passes over the stop graph. That is an offline batch of
  minutes, not an interactive feature, and it only becomes affordable once the
  transit graph exists. **Do the hub list first; treat this as the version that
  earns a paper.**
- **The payoff is a different headline.** "X% of New Yorkers are within a
  10-minute walk of a train" says nothing about whether the train goes anywhere
  useful. Time-to-nearest-center and the detour factor are the first metrics in
  this roadmap that could contradict the walk-access map rather than refine it,
  and the places they disagree are the finding.

### Airports

A distinct question from commuting, and worth separating because it has two
users with opposite needs.

- **Travelers** make the trip occasionally, carry luggage, travel off-peak, and
  tolerate a fare premium. **Airport workers** make it daily, on shift hours
  that include the middle of the night, and are highly fare-sensitive. This
  project's existing lens — residential population, walk access, 24-hour
  service — serves the second group, and the second group is the
  under-reported one. Build for the worker and the traveler's answer falls out.
- **The three airports are three different problems.** JFK has AirTrain from
  Jamaica (E/J/Z and LIRR) and Howard Beach (A), with an **$8.75** fare at
  those two stations and free travel inside the airport. LGA has **no rail link
  at all** — the AirTrain LGA project was halted in October 2021 — and is
  reached by the Q70 LaGuardia Link, which has been **free year-round since
  1 May 2022** and runs roughly every ten minutes in the daytime from Jackson
  Heights and Woodside. EWR is outside the city but genuinely used by NYC
  residents, via NJ Transit plus its own AirTrain.
- **The modeling is small and already designed for.** These are a handful of
  hand-coded edges on the Phase 4 transit graph — a headway and a fare on each
  — plus a terminal node per airport. The fare premium reuses the commuter-rail
  fare disclosure that now ships; the 3am service window reuses the
  `tph_night` and `night_gap_hours` columns in `station_service.parquet`,
  which already exist. **No GTFS feed for AirTrain JFK turned up in a search**
  and the Port Authority's developer pages are unchecked, so assume
  hand-coding and treat a feed as a bonus.
- **The finding to look for:** the fare premium and the night gap, not the
  distance. A JFK worker living in Brooklyn pays twice and waits twice, and a
  3am shift may have no rail option at all — which is invisible to a map that
  counts stations. Quote travel time *with and without* the premium link, so
  the reader can see what the $8.75 buys and what it costs the people who pay
  it every day.

## Phase 5 — who lives there

All verified present in ACS 2023 5-year at tract level; the fetch stage already
handles multi-variable Census calls.

| Variable | What it is | Why it matters |
|---|---|---|
| `B08201_002E` / `B08201_001E` | Households with **no vehicle available** | The best single need signal. A far-from-transit tract where everyone drives is not under-served the same way as one where nobody can. Ranks above income. |
| `B19013_001E` | Median household income | The obvious equity axis, and the one people will ask for |
| `B17001_002E` | Population below poverty level | Less noisy than median income at tract level |
| `B08301_010E` | Commuters using public transit | Revealed demand — but careful: it is partly an *outcome* of good service, so it belongs in the tooltip more than in the score |
| `B18101` | Disability status by age | Pairs with the step-free layer below |
| `B01001` | Age structure | Over-65 and under-18 shares both raise dependence |

Two cautions worth writing into the methodology:

- ACS 5-year estimates at tract level have real margins of error — the pipeline
  already fetches `B01003_001M` for population and should fetch the MOE for
  every new variable. Showing a tract as "worst in the city" when its income
  estimate is ±$18,000 is a mistake the map should refuse to make, by graying or
  flagging high-MOE tracts.
- Switching to block groups (`GEO_LEVEL = "bg"`, ~6,500 vs ~2,325 — a one-line
  config change already supported) improves spatial resolution exactly where it
  is worst, since tracts are drawn to roughly equal *population* and so are
  physically enormous in low-density areas. But it makes every ACS estimate
  noisier. The right answer may be geometry at block-group level and the social
  variables at tract level.

### Destinations, not just residents

Residential density answers "who needs to leave from here". It says nothing
about "who needs to get *to* here", and the Met is the ideal case: a tract whose
resident population is a few thousand absorbs millions of visits a year, and the
map today treats it as ordinary.

The right shape for this is **not** a boost applied to population density. Two
reasons. First, a boost makes the density field stop meaning people per km²,
which breaks the density floor, the terciles and the legend in one move.
Second, the two kinds of demand want different treatment: a resident's trip is
daily and bidirectional, a visitor's is occasional and often off-peak.

So carry it as a separate **attraction** field and let the score combine the two:

- **Jobs per block** — LEHD LODES8 WAC, already verified reachable and already
  wanted for Phase 4. The best destination signal available: comprehensive,
  uncurated, and it captures the thing most visitor lists miss, which is that
  the largest destination in almost every tract is somebody's workplace.
- **A curated anchor list** for what jobs data under-weights — museums (Met,
  AMNH, MoMA, Brooklyn Museum), stadiums and arenas (Yankee Stadium, Citi Field,
  Barclays, MSG, Arthur Ashe), the big parks and beaches (Prospect, Flushing
  Meadows, the Botanic Gardens, Rockaway, Orchard Beach), major medical centers,
  CUNY campuses, and the intercity gateways (Penn, Grand Central, Port
  Authority, the ferry terminals, the AirTrain connections). Keep it as a small
  hand-editable GeoJSON in the repo with one weight per entry, so the choices
  are reviewable instead of buried in code.
- **Use published attendance as the weight** wherever it exists. Museum and
  stadium attendance figures are public and enormously unequal; a hand-assigned
  1-to-5 importance scale would be inventing data when real counts are available
  for the largest entries.
- **The Met is also a walk-time finding, not only a destination one.** It sits
  mid-block on Fifth Avenue with the park behind it, so the real walk from the
  86 St station is materially longer than the station-point distance implies.
  Phase 6's entrance-level snapping is what fixes that half of the problem, and
  it generalizes — every destination set back from the street grid is
  mis-measured the same way.

One caveat to write down now: a destination layer makes the map answer a second
question, and the two answers want different colors. Resist merging them into a
single score by default. "Under-served residents" and "hard-to-reach
destinations" should be two views that share a pipeline.

### On parking data

Probably not worth building, and the reasoning is worth recording so the
question does not have to be re-opened.

Parking would be a proxy for "can people here simply drive instead?" But there
is no comprehensive off-street parking inventory for NYC. What is published is
municipal lots and garages (a few hundred facilities), licensed-garage records,
and curb *regulation* signage — which describes rules, not supply. None of it
adds up to spaces per tract, and assembling an estimate from building records
would be a project with a worse error bar than the thing it is proxying.

And the proxy is already available directly, and better: `B08201_002E`,
**households with no vehicle available**, is in the table above and measures
exactly what parking would only hint at. Vehicle registrations by ZIP are a
second direct source if car *ownership* rather than access is the question.

**The combination with income is the part worth building.** Zero-car share on
its own is ambiguous and quietly circular: the Upper West Side has very few
cars *because* the transit is excellent, so ranking need by carlessness alone
scores the best-served neighborhoods in the city as the most dependent — the
same outcome-as-input trap flagged for transit commute share above. Crossed
with poverty (`B17001_002E`, less noisy at tract level than median income) it
separates **carless by choice** from **carless without means**, and the second
is the strongest need signal available anywhere in this phase. Carry it as the
product of the two ranks, or as a flagged quadrant, and show both components in
the tooltip so the distinction is visible rather than asserted.

The one version of *parking* that would earn its keep: **park-and-ride capacity
at outer stations**, which is a real access mechanism on Staten Island and at
the eastern Queens and north Bronx commuter-rail stops, and a small enough set
to compile by hand. That belongs as a column on the station table, not as a
citywide layer.

## Phase 6 — access cost, done properly

- **Step-free access.** 2,120 subway entrances exist; **116 are step-free**
  (102 elevators, 10 ramps, 4 combinations). Recomputing the entire map with the
  station set filtered to ADA-accessible stations produces a dramatically
  different and genuinely under-reported picture — and it costs almost nothing,
  because it is the same pipeline with a filtered source set. The station table
  already carries `ada`, `ada_northbound`, `ada_southbound`. High impact,
  low effort.
- **Entrance-level snapping.** The pipeline currently snaps 496 station *points*;
  the entrances dataset (`i9wp-a4ja`, 2,120 rows, 2,072 entry-allowed, with
  `entrance_latitude`/`longitude` and `gtfs_stop_id`) gives the real doors. For
  long complexes — 14 St–Union Sq, Times Sq, the half-mile-long platforms on the
  7 — the difference is a few hundred meters of walking, in the direction that
  currently flatters the map.
- **Slope.** Washington Heights, Staten Island's spine, Riverdale. A DEM plus a
  slope-dependent walk speed (Tobler's hiking function is the standard) changes
  real walk times in exactly the neighborhoods that already score badly.
- **Barrier and crossing cost.** The network already captures rivers and rail
  cuts correctly (mean detour factor 1.68× vs straight line). A signalized-
  crossing delay would add realism, but OSM crossing tagging is uneven in NYC —
  check coverage before building on it.

### Bikes

Worth doing, and cheaper than it sounds, because it is the existing pipeline
with two inputs swapped. The one-Dijkstra design already generalizes: filter
the OSM edges to bikeable ways, raise the speed, re-run the same
`min_only=True` pass, and the output is *bike* minutes to the nearest station
for every node in the city. Nothing downstream has to change shape.

Data probed live 2026-10-10 and both are usable:

- **NYC DOT bike routes**, `data.cityofnewyork.us/resource/mzxg-pwib` — 29,695
  segments with `ft_facilit` (facility type), `onoffst`, `lanecount` and
  `instdate`. The facility breakdown is the whole point: **7,418 Protected**,
  6,545 Conventional, 3,625 Shared, 783 buffered-conventional-or-curbside, and
  **7,302 explicitly `None`**. 24,564 segments are on-street, 5,131 off.
- **Citi Bike GBFS**, `gbfs.citibikenyc.com/gbfs/en/station_information.json`
  — 2,519 stations, 74,258 docks, no key, 1.4 MB. The feed's bounding box stops
  at about −74.10 longitude, so **there is no Citi Bike on Staten Island**,
  which is where this map's worst tracts are.

Three things it would actually answer:

- **Does a bike close the gap, or move it?** Report bike minutes beside walk
  minutes and the honest finding is probably that bikes rescue the middle of
  the distribution — the 15-to-25-minute walks — and do much less for the
  50-minute tracts, because those are far by street network and not merely far
  on foot. If that is what it shows, it is a real result and an argument
  against the common claim that bike infrastructure substitutes for a line.
- **Protected-only is the version that means anything.** A bike catchment
  computed over every street is a fantasy catchment; computed over protected
  and off-street facilities only it is a measure of whether the city has built
  a usable approach to its own stations. Those are two very different maps from
  the same pass, and shipping both as a toggle is the cheapest way to make the
  difference visible. The 7,302 `None` segments are the reminder that the
  "bike network" is largely a route designation, not infrastructure.
- **Bike parking at the station is the binding constraint, and it is missing.**
  A bike catchment assumes you can leave the bike somewhere, and secure
  parking at outer stations is scarce. Citi Bike inverts the problem — the dock
  is the parking — which is exactly why its absence from Staten Island matters
  more than its density in Manhattan.

The trap to avoid: **bike access is not an equity substitute.** Cycling skews
young, male, able-bodied and fair-weather, and this file already refuses to
treat a mode's convenience as a mode's reach — see the step-free item above,
which makes the opposite point with the same logic. Bike minutes belong as
their own layer and their own column, never folded into the access axis as
though a 20-minute ride were a 20-minute walk. The same caution applies to any
"combined access" number: it would quietly reward the neighborhoods with the
best weather-independent alternatives and penalize nobody.

## Phase 7 — scenarios: where should the next station go?

This is where an under-service map becomes a planning tool rather than a
description, and the architecture makes it unusually cheap.

- **Marginal-benefit surface.** For each candidate site on a grid over the city,
  add one virtual station, re-run the multi-source Dijkstra (0.1 s), and record
  how many people move inside the 10-minute threshold. The result is a raster of
  "people newly served per hypothetical station" and a ranked list of the best
  sites. Fully pre-computed, so the web map stays static. Because `min_only`
  Dijkstra already returns the *existing* nearest-station distance for every
  node, the marginal calculation is a comparison against a vector you already
  have — not a re-run from scratch.
- **Proposed-project toggles.** The concrete near-term version of the above, and
  the single most compelling thing on this list — it turns every finding into a
  question with an answer:
  - **Interborough Express** — Brooklyn to Queens along the existing Bay Ridge
    Branch freight right-of-way, roughly 14 miles, with light rail announced as
    the preferred mode. Its alignment runs through exactly the band of dense,
    poorly-served tracts the bivariate map lights up, so it is the strongest
    test case available: *does the IBX fix the Brooklyn–Queens gap this map
    shows?*
  - **Second Avenue Subway** — its own Phase 2 (96th to 125th) is under
    construction;
    the full **T** south of 63rd through Lower Manhattan is unfunded but has a
    published alignment. Toggle the phases separately, because their benefits
    land in completely different places and bundling them hides that.
  - **Triboro and others** — the earlier Triboro RX variants, and room to load
    anything else worth testing.

  Mechanically each one is a station list plus an alignment, added to the source
  set before the Dijkstra pass; the map then reports the before/after population
  moved inside each threshold. Three things to get right:
  - **Take station locations from each project's own published documents**, and
    cite them per project with a vintage. Proposed station lists change, and a
    map quietly using a stale one is worse than no map.
  - **Render a proposal unmistakably as a proposal** — dashed alignment, hollow
    station markers, a persistent "scenario" indicator that survives a
    screenshot. A screenshot of this map with the IBX silently switched on is
    misinformation.
  - **Report the delta as population *moved* inside 10 minutes**, broken out by
    borough and density class. "Serves 400,000 people" is meaningless without
    saying how many of them already had service.
- **Propose the line, not just the station** (the long-term goal). The marginal
  surface above ranks *points*; a line is a connected sequence of them, and
  getting from one to the other is the hard part. A tractable path:
  1. **Cluster, don't search.** The space of possible alignments is hopeless;
     the hottest contiguous bands of the marginal surface are a short list a
     person can actually review. Produce candidate *corridors*, not scored
     polylines.
  2. **Snap to what is buildable** — existing and former rail rights-of-way (the
     IBX's entire premise), wide arterials, bridge and tunnel approaches. A
     corridor that requires taking buildings is not a proposal.
  3. **Cost by mode and construction type**, because that is where the orders of
     magnitude are. Deep-bore tunnel in New York has run well over $1B per mile
     — Second Avenue Phase 1 is among the most expensive subway mileage ever
     built anywhere — while surface light rail on an owned right-of-way,
     bus lanes with signal priority, or a ferry landing are one to three orders
     of magnitude cheaper. Ferries need their own caution: they move very few
     people per dollar of operating subsidy and must not be allowed to win on
     capital cost alone.
  4. **Report cost per newly-served resident, ranked.** Not an absolute dollar
     figure, and not a benefit-cost ratio to two decimal places. The defensible
     output of a model at this level is an *ordering* of corridors plus a
     statement of what would change the order. Anything formatted like a capital
     budget will be quoted as one.

  Worth being clear-eyed: this is the part of the roadmap where it is easiest to
  produce something that looks authoritative and is not. The ordering is
  genuinely useful. Precision is not available here, and the methodology page
  should say so in as many words.
- **Click to place a station.** The live version of the above. Needs the graph in
  the browser (too big) or a small local endpoint — so the pre-computed marginal
  surface is the better default, with the interactive version as a `make serve`
  extra rather than a feature of the static deployment.

### The inverse: where should the density go?

The same two fields read backwards. A tract with **good access and few people**
is not a problem to fix by moving the station — it is a place where housing
could go next to transit that already exists. This is the dual of the siting
question above, and it is the one item in this file that changes who the map is
for: from "where is service missing" to "where could housing go".

- **The cheap version already exists and is unlabeled.** Low density × short
  walk is a corner of the bivariate matrix that is computed, painted and
  nameless. Naming that cell in the legend and letting the class filter isolate
  it costs nothing and would answer the question approximately today.
- **The honest version needs zoning, because the cheap one recommends
  upzoning cemeteries.** A tract can be low-density beside a subway because it
  is zoned for detached houses, or because it is an airport, a park, a
  cemetery, or an industrial M district. Without zoning the layer confidently
  points at Green-Wood and the Navy Yard.
- **The metric to publish is unused zoned capacity, not low density.** DCP's
  MapPLUTO is lot-level and carries `ZoneDist1`–`4` and the maximum residential
  FAR, so `LotArea × ResidFAR − existing residential floor area`, summed to the
  tract and restricted to tracts within ten minutes' walk of a station, is
  *square feet of housing the current zoning already allows and nobody has
  built*. That is a number a planner would ask for, in units they use. NYZD
  gives the district polygons separately and updates monthly. **Neither has
  been probed live yet** — they are not in the verified-sources table below.
- **Honest limits, stated with the layer.** Zoned FAR is an upper bound, not
  buildable capacity: landmarking, lot geometry, assemblage, height and setback
  rules, and existing occupied buildings all cut it, and the map cannot see any
  of them. Publish "unused zoned residential capacity near transit" and let the
  reader draw the conclusion — *"should be upzoned"* is a political claim and
  does not belong in a legend.
- **It pairs with station siting rather than competing.** Two ways to close the
  same gap: move the on-ramp to the people, or let the people move to the
  on-ramp. A scenario view that can do both is more honest than either alone,
  because the cheaper answer is frequently the second one.

## Phase 8 — credibility and reuse

- **Methodology page.** Every parameter that moves a number — walk speed, wait
  penalty, bus multiplier, score weights, sample spacing — stated with its value
  and its justification. The map makes claims; this is where they are defensible.
- **Benchmark against published figures.** The pipeline already cross-checks the
  half-mile subway share (62.2% network vs 71.6% straight-line, a 9.5-point
  penalty — itself a finding worth stating prominently). Extend that to any
  published frequency or accessibility figures so a reader can locate this
  work against numbers they already trust.
- **CSV / GeoJSON export** of the per-tract table, and a documented schema.
  `stats.json` already carries `schema`; surface it as a download.
- **Crowding** is no longer in this phase — see *Ridership and crowding* near
  the top of this file. It landed here originally on the assumption that it was
  a nice-to-have; it is really a service-quality measure, and it meant very
  little until scheduled service was in hand. It is now, so it is next.

### Validating against official plans

The map has no ground truth, and "does it look right" is not a standard. The
closest available check is whether the tracts it calls worst are the ones
transit agencies have already decided to spend money on. **This now lives in
`pipeline/verify.py` and gates the build**, which was the one item in this
section that mattered most: without it a score change is unfalsifiable. What
follows is the reasoning behind the test; the numbers it produces are in
`stats.json` and on the page.

**How the professionals decide.** Federal New Starts money is awarded against
six statutory project-justification criteria — mobility improvements,
environmental benefits, congestion relief, economic development effects,
cost-effectiveness, and existing land use — each rated on a five-step scale,
with at least "medium" required for construction funding (49 CFR 611.203). Not
one of them is "distance to the nearest station". Mobility is measured in
forecast *trips on the project*, which means a model, not a geography. The
nearest thing to this map in the federal process is the land-use criterion, and
the nearest thing in the MTA's own language is its Equity Areas, which it
characterizes by **zero-vehicle households — 65% in Equity Areas against 29%
elsewhere**. That variable now ships (`nveh`, from ACS B08201) and is
weightable in the UI — and measured, weighting it *lowers* agreement with the
agencies' own corridors, which is the most interesting negative result in this
file. Being the operator's own equity definition and being a predictor of where
the operator builds turn out to be different things.

**The classic density test is non-binding in New York, and that matters.**
Pushkarev and Zupan's thresholds are still the standard rule of thumb: roughly
4 dwelling units per residential acre for hourly bus, 7 for 30-minute bus, 9
for light rail, 12 for heavy rail. Converted at 2.5 persons per household and
~55% of gross area residential, the heavy-rail threshold is about 4,100 people
per km² — and **only 5.4% of NYC tracts fall below it**, against a median tract
density of 17,030. Even generous assumptions leave a 4× margin. The density
axis therefore carries far less information here than it would anywhere else in
the country: nearly every tract in the city is dense enough for rail, so
density cannot be what separates them. A score that leans half its weight on
density is spending that weight on a variable that has already saturated —
which is exactly what the retired rank-product score did, and why it measured
at the baseline.

**The alignment test.** Tracts were matched by neighborhood name to eight
corridors that agencies have actually proposed: Penn Station Access (four new
Bronx stations, 2027), the Staten Island North Shore BRT on the abandoned rail
right-of-way, Staten Island West Shore, the Utica Avenue corridor, the 3-train
extension at East New York, QueensLink on the Rockaway Beach Branch, the
Interborough Express, and the Red Hook extension. **580 of 2,243 tracts (26%)
sit in one of them**, which is the baseline a useless score would hit.

Ten candidate scores, re-measured 2026-10-11 with the test in `verify.py`:

| | worst 25 | worst 50 | worst 100 |
|---|---|---|---|
| `pop × excess walk` — **shipped** | **80%** | 58% | 43% |
| `need × excess walk` | 76% | **60%** | **48%** |
| `pop × excess access` (counts the wait) | 76% | 52% | 41% |
| `need × excess access` | 76% | 54% | 40% |
| `pop × need × excess walk` | 72% | 56% | 42% |
| `carfree hh × excess walk` | 56% | 50% | 47% |
| `carfree hh × excess access` | 44% | 44% | 38% |
| Rank product (retired) | 20% | 26% | 23% |
| Baseline (random) | 26% | 26% | 26% |

The retired rank product scored **at or below the baseline at every depth**:
against the agencies' own revealed priorities it had no predictive value
whatsoever. The shipped measure runs 3.1× the baseline at the top, and
`ALIGNMENT_MIN_PRECISION` fails the build below 40%.

**`need × excess walk` leads at depth and did not ship.** It wins at worst-50
and worst-100 while losing worst-25 by four points, and the tiebreaker was not
alignment: a pure need index carries no population term, so it puts 27 of the
worst 153 tracts under 2,000 people — the smallest with 43 residents — against
3 for the shipped score. That is the empty-land failure mode, and preventing it
mattered more than four points at one depth. It stays in the table as the
strongest live alternative, and if cluster-level precision is ever measured it
is the one most likely to overtake.

**Where the disagreements are, and why.** The pattern is systematic rather than
noisy, and it maps exactly onto the three axes of the reframe at the top of
this file:

- **Agreement where the plan is about walk access.** Penn Station Access,
  SI North Shore, SI West Shore, Utica Avenue and QueensLink all score high,
  and the shipped measure ranks all five at the 98th–100th percentile. Officials
  called Co-op City a transit desert at the Penn Access groundbreaking. SI West
  Shore is the sharpest case: the rank product put it at the 76th percentile,
  this measure at the 100th. It is also where the robustness check lands — 12
  tracts stay in the worst 50 under every score that clears the floor, and all
  twelve are SI North and West Shore, inside one 105-tract contiguous block.
- **Accidental agreement where the plan is about network shape.** The IBX
  corridor scored 98th percentile on the rank product — but its median walk is
  **8.4 minutes, exactly the city median**. The map is flagging it for density,
  not for the orbital gap that is IBX's entire justification. Same for the
  3-train extension (median walk 6.2 minutes) and Red Hook (8.3). These are
  capacity and connectivity projects, and this map cannot see either. They are
  the argument for *Secondary hubs, and the orbital problem* in Phase 4.
- **False positives that were the defect in miniature.** The rank product's
  worst 25 included Yorkville, Lenox Hill, East Village, Belmont, Corona,
  Tremont, Claremont Village and Flushing — all 11–19-minute walks, none
  proposed for new rail by anyone, because they already have it. All are gone
  from the worst band now.
- **False positives that are probably real findings.** The shipped measure's
  misses are Todt Hill, College Point and Throgs Neck–Schuylerville, at 50–64
  minutes' walk. No plan targets them. That is at least as likely to be a gap
  in the plans as a gap in the map, and it is the more interesting of the two
  possibilities.

**What this test is not.** Official plans are not need; they are need filtered
through cost, politics and whether a right-of-way happens to be lying around —
two of the eight corridors above exist largely *because* an abandoned alignment
does. Absence of a plan is not absence of need, which is why the third bullet
is reported rather than scored against. The matching is also coarse:
neighborhood-name substrings standing in for digitized corridor geometry, and a
corridor list assembled from agency coverage rather than a machine-readable
source. **Both are worth fixing before this becomes a published number** — the
corridors want real geometry and a citation each, and the whole test wants to
live in `pipeline/verify.py` so a score change cannot quietly lower it.

**And what this map is not.** The disagreement is as much ours as theirs. Five
things a planner weighs are absent here by construction, and each of them would
move tracts:

- **Buses.** Still absent from every distance and score calculation, which on
  Staten Island, in eastern Queens and in the east Bronx is the difference
  between *no* transit and *slow* transit. The frequent-bus layer now shows
  *where* useful service exists — 2,368 of 12,942 stops hold a 10-minute
  headway all day — so the omission is at least visible on the page. Putting
  buses in the graph is Phase 4.
- **Car ownership.** A 40-minute walk where 90% of households have a car is a
  different finding from the same walk where 10% do. Now shipped and
  weightable — and measured as making the alignment *worse*, which is the thing
  about this list that should be least comfortable: the variable with the best
  equity argument behind it is the one the agencies' revealed behavior agrees
  with least.
- **Whether a line is buildable at all.** Geology, right-of-way, yard capacity
  and where the tunnels would actually connect kill more real proposals than
  low need ever does. Phase 7 is the only part of this file that touches it.
- **Cost.** Excluded for now on purpose, and that exclusion is exactly why this
  map can point at things no agency would fund. Phase 6 is about the rider's
  cost, not the builder's.
- **Contiguity.** Now measured, and it was as consequential as expected: the
  largest contiguous block past the standard is 430 tracts and 1.31M people.
  What remains is to measure *precision at the cluster level*, since corridors
  are runs of tracts and the table above compares a point measure against a
  line measure. See *What phases 2 and 3 left behind*.

Four of the five are already scheduled, which is the reassuring reading of the
alignment test: the measure is incomplete in ways this file has already named,
rather than wrong in ways it has not.
---

## Suggested order

The score exists, it is measured against something outside itself, and the
measurement gates the build. That changes what "next" means: every item below
can now be judged by whether it moves the alignment number, and two of them
have already been measured as moving it the wrong way.

**Step-free access is first** — the biggest finding available for the least new
machinery, and the one axis of access cost that is still entirely unmeasured.
The subway-entrance table is fetched and verified (2,120 rows, 116 step-free),
the swap is a source-set filter on the Dijkstra, and the result needs no new
model.

**Then ridership and crowding**, because it is the last piece of the
service-quality axis and because *Secondary hubs* in Phase 4 depends on it: a
hub list picked by feel rather than by ridership is the wrong hub list. Read
the three measured obstacles first — it is a server-side aggregation against
167 million rows, it joins on `station_complex_id` rather than
`gtfs_stop_id`, and train capacity is in none of the data.

**Then cluster-level precision in the alignment test**, which is cheap now that
the components exist and which should happen before the 80% figure is quoted
anywhere load-bearing. A per-tract precision figure compares a point measure
against a line measure, and the corridors are lines.

**Then time to hubs**, which is a project rather than a feature. Secondary hubs
and the detour factor sit inside Phase 4 rather than beside it: they need the
transit graph, and they are the reason to build it with more than one
destination in mind from the start. Airports are the cheapest thing in that
phase and a good first test of its fare and night-service machinery — both of
which now have shipped machinery to reuse rather than to build.

Orthogonal to all of that, and pickable whenever: **publishing the map**, which
needs no new data and currently blocks every other reader from seeing any of
this, and the **small no-new-data items** — the class filter, the collapsible
panels, the legend width. The class filter is worth doing before the zoning
work, because naming the low-density/short-walk corner of the matrix is most of
that question's cheap version.

Bikes sit with step-free access in the "same pipeline, different input set"
bucket, and should be picked up on the same grounds: the swap is an edge filter
and a speed, the data is verified, and the likely result — that bikes rescue
the middle of the distribution and not the tail — is worth having before anyone
proposes a bike lane as an answer to a transit desert.

**Contiguity was the sleeper and it paid off.** It needed nothing but an
adjacency graph over polygons already on disk, and it produced the one headline
no per-tract score can: 430 contiguous tracts holding 1.31M people, Canarsie
through Flatlands to St. Albans. The honest caveat is that a block that large
is arguably not a project unit but "the whole outer ring", which is the
argument for the spatial statistic — a Local Moran's I could split that band
into genuine hot spots, where the component count cannot.

Proposed-line toggles (IBX, the T) are the most tempting thing to pull forward,
and they should not be. Their entire value is the before/after comparison, so
they are only as credible as the walk and service model underneath them —
shipping them early would produce confident answers from the weaker model.

Deliberately *not* first: more color classes — settled 2026-10-10, a 4×4
bivariate matrix measures 10.0 on its weakest pair against a 15.1 floor, and
the resolution belongs on a one-axis ramp (now 7 steps) rather than in more
cells; and **anything that adds a term to the score without a row in the
alignment table**. Two such terms have now been tried and both lowered it: the
wait penalty, and the equity variables. Neither is a reason not to measure
something — both are shipped and visible — but the score itself is now
defended by a number, and a term that lowers that number needs an argument
better than "it ought to matter".

Bus *proximity* is also still off the list for the original reason — 15,000
stops covering the whole city — but note that the objection was to the metric,
not the data: the 62 MB is downloaded and parsed, and the selective version of
the layer ships.

---

## Verified data sources

Probed live 2026-10-10; all reachable, none needing auth except Census.

| Source | Endpoint | Size / count |
|---|---|---|
| Subway GTFS | `rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip` | 5.6 MB |
| LIRR GTFS | `.../gtfslirr.zip` | 1.8 MB |
| Metro-North GTFS | `.../gtfsmnr.zip` | 4.4 MB |
| Bus GTFS ×6 | `.../gtfs_{b,m,q,bx,si,busco}.zip` | 62.7 MB total |
| Subway entrances | `data.ny.gov/resource/i9wp-a4ja` | 2,120 rows (2,072 entry-allowed; 116 step-free) |
| Hourly ridership | `data.ny.gov/resource/wujg-7c2s` (to 2024), `5wq4-mkjj` (2025+) | large |
| Jobs by block | `lehd.ces.census.gov/data/lodes/LODES8/ny/wac/ny_wac_S000_JT00_2022.csv.gz` | reachable |
| Bike routes | `data.cityofnewyork.us/resource/mzxg-pwib` | 29,695 segments (7,418 protected) |
| Citi Bike GBFS | `gbfs.citibikenyc.com/gbfs/en/station_information.json` | 2,519 stations, 74,258 docks, 1.4 MB |
| ACS variables | `api.census.gov/data/2023/acs/acs5` | `B08201_002E`, `B19013_001E`, `B17001_002E`, `B08301_010E`, `B18101`, `B01001` all confirmed |

The subway station table already carries `gtfs_stop_id`, so every GTFS join is a
key join. It also carries `structure` (elevated / subway / open cut) and a `cbd`
flag, neither of which the map currently uses.
