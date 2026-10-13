# transit-gap-map

Finds under-served areas by mapping two things together. The current
deployment covers New York City, one neighborhood-sized area at a time (a
census tract, about 3,800 residents on average here, and called a tract in
the rest of this file):

1. **Walking distance to the nearest train station** — routed along the real
   pedestrian network, not straight-line distance
2. **Population density** — residents per km² of *land* area

The point is the combination. A bivariate map shows where density is high but
station access is poor, and the pipeline reports what share of New Yorkers live
within a 5, 10 and 15-minute walk of a train.

A station is an on-ramp, not a destination, so there is a third measure that
can contradict the first two: **how many of the city's jobs each tract can
reach in 45 minutes**, over a graph built from the rail and ferry schedules.
The average New Yorker reaches **1.25M of 4.47M jobs** that way — 28%.
Living near a train says nothing about whether the train goes anywhere, and
these are the first numbers here that can disagree rather than merely refine.

That figure has a ceiling in it, so there is a fourth measure without one:
**how long until a resident can reach a tenth of the city's jobs.** Same data,
axes swapped — fix the jobs, measure the time. It matters because a
45-minute cutoff *censors*: 1,062 of 2,325 tracts need longer than that to
reach a tenth of the city's jobs, so they all bunch near zero and stop being
distinguishable. Breezy Point reaches 0.13% of jobs in 45 minutes and Arden
Heights 0.12%, which looks like a tie and hides **86 minutes against 121**.
Measured without the cutoff: the average New Yorker needs **44 minutes**, and
the worst tenth of tracts are past 69. This is the measure to read at the bad
end, which is the end this project is about.

Stations counted: all **496 subway stations** (including the 21 Staten Island
Railway stops), plus **LIRR and Metro-North** stations in or just beyond the
city — 557 in total.

## Quick start

```bash
make setup                 # .venv on Python 3.14 + dependencies
cp .env.example .env       # then paste a free Census key into it
make all                   # fetch -> network -> service -> distance ->
                           #   transit -> access -> palette -> surface ->
                           #   build -> verify
make serve                 # http://localhost:8000 (and /stats.html, /about.html)
```

The Census API requires a key for every request now; it is free and instant at
<https://api.census.gov/data/key_signup.html>. `make fetch` fails with a clear
message if it is missing.

First run downloads a **153 MB** OSM extract and spends about **4–5 minutes**
parsing it. Everything after that is cached, so later runs take seconds.

## Why there is no routing server

The obvious way to get walking-network distances is to run OSRM or Valhalla in
Docker and query it once per origin. That is unnecessary here. Because we want
the distance to the *nearest* of N stations for *every* location, one
multi-source Dijkstra pass answers the whole city at once:

```python
dist, pred, sources = dijkstra(graph, indices=station_nodes,
                               min_only=True, return_predecessors=True)
```

`min_only=True` collapses all 557 sources into a single C-speed pass — **0.1 s
over 1.84 million nodes** — and the `sources` array says which station is
nearest to each node, which the map needs anyway for its tooltip. No Docker, no
routing service, no rate limits.

Each station is attached to the graph through its own virtual node, weighted by
its snap offset, so a station mapped slightly off the network pays for that gap
instead of being treated as sitting exactly on it.

## Pipeline

| Stage | Module | What it does |
|---|---|---|
| 1 | `pipeline/fetch.py` | Download stations, boundaries, census, GTFS, OSM extract |
| 2 | `pipeline/network.py` | `.pbf` → cached undirected walking graph |
| 3 | `pipeline/service.py` | GTFS → trains per hour per station, frequent bus stops |
| 4 | `pipeline/distance.py` | Multi-source Dijkstra → walk and walk+wait per node |
| 4b | `pipeline/transit.py` | Rail + ferry GTFS → a frequency graph, solved for every station pair |
| 4c | `pipeline/accessibility.py` | Jobs reachable in 45 min, time to reach a tenth of them, job centers, detour factor |
| 5 | `pipeline/aggregate.py` | Node distances → per-tract statistics and the score |
| 6 | `pipeline/palette.py` | Generate and audit the color system |
| 7 | `pipeline/surface.py` | Walk-time raster + network walkshed polygons |
| 8 | `pipeline/build.py` | Emit `web/data/` payloads |
| — | `pipeline/verify.py` | Barrier test, trip probes, spot checks, and the alignment gate |

Everything under `web/data/` is generated and gitignored, so a fresh clone must
run `make all` before `make serve` — the map reports that explicitly rather than
hanging if the payloads are missing.

`make serve` runs `serve.py` rather than `python -m http.server`, because the
stdlib server sends no `Cache-Control` and a browser will happily serve an
edited page from its own disk cache. It does not compress, though, so the wire
sizes quoted here are gzipped figures measured with `config.wire_kb()` rather
than what this server sends.

The site is three static pages. `web/index.html` is the map,
`web/stats.html` has the figures — a set of plain-language facts, the
borough comparison and every published number — and `web/about.html` is the
methodology: the corridor test and what the measure cannot tell you. All
three render from the same `stats.json` through `web/stats.js`, so no number
appears as a literal in any page's prose and a rebuild cannot leave the words
disagreeing with the map. The stats and methodology pages load that one 46 KB
file and neither MapLibre nor the GeoJSON.

`config.py` holds every path, CRS, threshold and data-source URL.

## Decisions worth knowing about

**The pedestrian network is genuinely disconnected, and that matters.** Keeping
only the largest connected component — the obvious thing to do — silently drops
**48% of nodes**, including all of Staten Island and its 21 SIR stations, because
the Verrazzano-Narrows Bridge carries no footpath. There are 5,931 components;
three of them hold 96% of the nodes: the four bridge-connected boroughs, New
Jersey, and Staten Island. So every component is kept, and stage 4 instead
publishes a *serviceable* mask — nodes in a component that actually contains a
station. Samples snap only to those, which keeps a point from attaching to an
isolated park path and reporting no transit access at all.

**Tracts are measured by a grid, not a centroid.** Each tract's walk time is
the median over a 25 m lattice of points inside its own polygon. A single
centroid misrepresents the long, irregular tracts common in Queens and Staten
Island, and can land in water or a park. Tracts too small or slivered to catch
a lattice point get a finer lattice of their own.

**Density uses land area.** The geometry is NYC DCP's water-excluded tract
layer, and area is recomputed in EPSG:26918 rather than trusting the published
`shape_area` field, whose units (square feet, state plane) are easy to misread.
Tracts with no residents — parks, airports, cemeteries — are flagged and
excluded from the terciles, so they cannot be mislabeled as transit deserts.

**EPSG:26918** (UTM 18N) is the working CRS: it covers all five boroughs and its
units are true meters. The raster surface is built uniform in **EPSG:3857**
instead, because that is the space a MapLibre image source interpolates between
its corners.

**Overpass is avoided.** OSMnx's default download path goes through Overpass,
which returned 406 or 500 from three of four endpoints during development. The
pipeline reads a BBBike `.pbf` extract with pyrosm instead.

## Caching and being a good client

Every input is cached on disk under `data/raw/`, with three layers so repeated
runs put as little load on the providers as possible. `make build` depends on
`make fetch`, so this path runs often — it needs to be cheap.

**1. Freshness window — no request at all.** Per-source TTLs in
`config.CACHE_TTL_DAYS`, set to how fast each source actually moves:

| Source | TTL | Why |
|---|---|---|
| Station lists | 7 days | Edited occasionally |
| DCP boundaries | 30 days | Near-static |
| Census ACS | 30 days | Annual release |
| OSM extract | 30 days | Rebuilt weekly, but barely matters for walk routing |

**2. Conditional request — a 304 instead of a transfer.** Past the TTL, the
request carries a validator so the server can answer "unchanged" in a few
hundred bytes. This matters most for the **153 MB** OSM extract, which would
otherwise be pulled again in full just because a month elapsed:

```
304     subway_stations.csv unchanged on the server (86.1KB kept)
pbf conditional GET -> HTTP 304  (153 MB transfer avoided)
```

Only **one** validator is sent, preferring `Last-Modified`. RFC 9110 requires a
server receiving `If-None-Match` to ignore `If-Modified-Since`, and Socrata's
ETag varies with the response's content-encoding — so sending both means the
ETag never matches, the date is ignored, and a full 200 comes back. Measured on
`data.ny.gov`:

| Headers sent | Result |
|---|---|
| `If-Modified-Since` only | **304** |
| `If-None-Match` only | 200 |
| Both | 200 — ETag takes precedence and the 304 is lost |

Validators are stored in `data/raw/.http_cache.json`. NYC's geospatial export
endpoint sends neither ETag nor `Last-Modified` (it generates the file per
request), so it cannot be conditionally fetched — which is exactly why its TTL
is 30 days rather than 7.

**3. Stale-on-failure.** If a provider is unreachable or rate-limiting and a
cached copy exists, that copy is used with a warning rather than failing the
run. Losing network access should not throw away a usable file. With no cached
copy, the run fails with a clear message.

Also: retries on 429 and 5xx with exponential backoff that honors
`Retry-After` (backing off is the difference between a transient error and a
blocked IP); a descriptive `User-Agent` on every request; downloads written to
a `.part` file and renamed on success, so an interrupted transfer never leaves
a truncated file that a later run would trust.

Force a full refresh with `python -m pipeline.fetch --force`, or skip the big
download with `--skip-pbf`.

## Verification

`make verify` runs the checks. The important one is the **barrier test**: for
probes beside water it compares network distance against straight-line distance
to the nearest station. Straight-line distance gets NYC badly wrong, so if the
detour factors were near 1 the pipeline would not actually be routing.

```
Greenpoint @ Newtown Creek    1771 m network    895 m straight    1.98x
Broad Channel shore           1123 m network    629 m straight    1.79x
Coney Island Creek            1894 m network   1152 m straight    1.64x
```

Greenpoint is the clearest case: 895 m across Newtown Creek, but 1,771 m on
foot, because the walk has to go around via the Pulaski Bridge.

The rail graph gets the same treatment: known trips are checked as wide bands
against what the published schedules do, and each band is set to fail a
specific way of breaking the graph rather than to flatter it.

```
Bay Ridge-95 St -> Times Sq (R local)        50.1  PASS (35-65)
Times Sq -> Grand Central (across complexes)  7.1  PASS (5-15)
Jamaica -> Penn (LIRR)                       21.7  PASS (15-35)
St George -> Whitehall (ferry)               32.5  PASS (26-41)
St George -> Atlantic Av                     52.8  PASS (45-80)
```

Times Square to Grand Central is the sharpest of them. The shuttle ride is
three minutes; the other four are the declared walk across the complex plus
the wait. A graph with free transfers reports three, so anything under five
fails — and free transfers are the single most likely way to build this
wrong. The ferry band is the next sharpest: 25 minutes of crossing on every
trip in the feed plus half the peak headway is 32.5, so losing the crossing
time reads about 8 and losing the wait reads 25.

The last pair has a history worth knowing. Until 2026-10-11 it was asserted
*unreachable*, because the graph was rail only and the Staten Island Railway
has no rail connection to the rest of the system — the assertion existed so
that adding a ferry edge would fail the build and force someone to decide,
rather than quietly changing a number about 495,000 people. That is what
happened, and the decision was to add it: the ferry has a published NYC DOT
schedule, its own right of way and no substitute, so excluding it was not a
conservative choice but a wrong one. The pair is kept with the sign flipped,
as the check that the connection has not disappeared again.

Also checked: stations measured from their own coordinates return ~0 (median
22 m, confirming the virtual-source wiring); dense core tracts come back under
5 minutes; known thin-coverage areas (Breezy Point, Charleston, Bergen Beach,
Country Club) come back far; the population join is complete; 95% of station
pairs are reachable, which a graph that lost its transfer edges would fail;
and the payload budgets hold. `make verify` also prints the 33 cross-feed
interchanges inferred by proximity — the one part of the transfer model that
is a radius rather than a declaration, so the one part that has to be read by
a person. Three of the 33 are the ferry's, at 115 m, 131 m and 194 m.

The headline number is cross-checked against **its own straight-line
equivalent** rather than an external figure. Published "share within a half
mile of the subway" numbers vary with method and rarely say which one they
used, so the pipeline computes both shares itself — same population, same
stations, the only difference being routed versus crow-flies:

```
Within a half mile of a subway/SIR station:
  straight-line      :  71.6%
  walking network    :  65.8%   (5.9 points lower)
```

That pair is self-checking: routed distance is always >= straight-line
distance, so the network share can never exceed the straight-line share. If it
ever did, the projection, the join or the snapping would be wrong. The
5.9-point gap is the cost of NYC's actual geography. It was 9.5 points until
2026-10-12, when stations started being snapped at their 2,072 real
entrances instead of at one point each -- `verify.py`'s barrier probes still
average a 1.65x detour, so the geography has not moved, only the measurement
of where a rider can get in.

## The color system

`pipeline/palette.py` generates `web/data/palette.json`, which both the raster
and the web map read, so there is one definition of every color. Density uses
the documented blue sequential ramp and walk time a ramp derived in OKLCH from
the orange anchor.

The bivariate matrix is an **OKLCH field**, not a blend of corner anchors:

- lightness moves with *both* variables, away from the surface color
- blue chroma rides the density axis and fades as walk time rises
- warm chroma rides the walk-time axis **scaled by density**, so distance from
  a train only shouts where there are people to be stranded

That last line is the whole design. A bilinear blend of four corners cannot
express it, because the sparse+far cell *is* a corner anchor — and that cell is
491 tracts of largely empty land (the Staten Island interior, eastern Queens,
the Jamaica Bay fringe), against 51 tracts in the dense+far cell the map exists
to point at. The first version of this palette gave the empty land a chroma of
0.175 and the finding 0.073, less than half, because the high-high corner was a
blend of blue and warm and averaging opposing hues in OKLab cancels `a` and `b`.
Quieting the anchor instead shrinks the matrix's gamut and drags the center cell
into its neighbors: the measured ceiling is 14.2 among the populated cells,
under the 15 floor, across 816k corner configurations. The field clears it,
with chroma on the right cell:

```
audit [bivariate]       among populated 15.4   headline corner 15.5   ratio 2.26x
audit [bivariate_dark]  among populated 15.5   headline corner 16.2   ratio 3.08x
```

`ratio` is the chroma of dense+far over sparse+far — the finding over the empty
land. It was 0.42x; the gate now requires at least 2.

The gates are grouped by the reader's task rather than held to one floor.
Resolving walk-time terciles *within* the low-density row is not a task this
map supports: those tracts are mostly airports, marshland and empty hillside,
and the design intent is that they recede rather than compete. That row is held
to 8 and stays ordered by lightness, while the populated cells — where the
map's claims live — keep the full 15. Dark mode is stepped against the dark
surface rather than flipped, so lightness rises toward the headline corner
instead of falling, with the ordering of both variables unchanged.

A 4x4 matrix was measured for comparison and scores 10.0 on its weakest pair,
so more classes would need a wider lightness range or a lower gate.
`bivariate_field()` takes `levels` if you want to re-check that.

The **under-service score** layer is the real answer to that resolution question:
one ordered ramp carries 6 steps where a matrix could not carry 16, because the
reader only has to see the order rather than identify a cell. It is violet —
slot 7 of the documented palette — and not a shade of either data ramp, since
blue already means density and warm already means walk time. Its gates are the
ones for an ordered ramp and not for a matrix: monotone lightness, at least
0.06 lightness per step, and every step clearing 2:1 against the page.
Sampling the bivariate field's own diagonal was tried first and rejected: it
crosses from cool grey to dark red, which reads as a diverging scale with a
midpoint the score does not have, and its pale end sits at 1.35:1 on the light
surface.

**Darker means worse served, and it means that in both themes.** Every ordered
ramp used to be stepped twice, once per background, so the well-served end
receded toward the page in each. That kept the *emphasis* on the finding and
inverted the *direction* — dark meant under-served on a light basemap and
light meant under-served on a dark one, so the map changed meaning when the
reader changed theme. One palette now serves both, which is a real constraint
rather than a preference: a color clearing 2:1 against both `#fcfcfb` and
`#1a1a19` has to sit in a middle band of lightness, because "away from the
surface" points in opposite directions on the two surfaces. The band is
measured, not assumed, and it costs the deep ends — the score ramp stops at
OKLCH L 0.435 where it used to reach 0.338, and its worst step reads 2.01:1
against the dark basemap where the old inverted ramp gave it 12.5:1. In dark
mode the finding is clearly visible rather than loud. It also cost a step: 7
steps inside the band are 0.055 of lightness apart against a 0.06 gate, so the
ramp is 6.

The **bivariate matrix is the one exception**, and it is measured rather than
assumed: it is the only layer where lightness carries two variables, so it
needs twice the range. Inside the band its best achievable separation among
populated cells is 12.3 against a 15.0 floor, and the contrast floor has to
fall to 1.2:1 before it passes — which is where a cell starts dissolving
into the page. So that one layer is still stepped per theme.

Stations carry identity by **size and ring rather than hue**, since more hues
there would collide with the choropleth ramps underneath. Walksheds are an
ordered magnitude, so they get one hue in three steps — contour lines, not
categories. Identity is never color-alone: the legend is always present and
cross-highlights the hovered cell, the hover tooltip names the class in words
("Density highest third · Walk longest third") as well as giving exact
numbers, and a table view lists the same data.

## Data sources

| What | Source |
|---|---|
| Subway stations | NY State Open Data `39hk-dx4f` |
| LIRR / Metro-North stations | NY State Open Data `wxmd-5cpm` |
| Census tract geometry | NYC Open Data `63ge-mke6` (water excluded) |
| Borough boundaries | NYC Open Data `gthc-hcne` |
| Population | US Census ACS 5-year (`config.CENSUS_DATASET`) |
| Jobs by workplace | LEHD LODES8 WAC, New York |
| Rail schedules | MTA GTFS (subway, LIRR, Metro-North) |
| Staten Island Ferry schedule | NYC Open Data `b57i-ri22` (NYC DOT) |
| Bus schedules | MTA GTFS, six feeds |
| Pedestrian network | OpenStreetMap via BBBike NYC extract (ODbL) |

## License

The **code** is **AGPL-3.0-or-later** (`LICENSE`). The Affero clause rather than
plain GPL is deliberate: a map's natural form of distribution is a website, and
GPL obligations attach to *conveying* a copy, not to letting the public use a
hosted instance. Under plain GPLv3 someone could fork this, point it at another
city, deploy it, and owe nothing. AGPL section 13 closes that — a modified,
network-reachable version must offer its users the corresponding source.

If you deploy it, set `config.SOURCE_URL` to your repository's public URL. It
flows into `stats.json` and the map renders a source link beside the
attribution, which is what section 13 asks for.

The **data** is governed separately, because a code license cannot relicense
someone else's inputs:

| | |
|---|---|
| Census population | US government work, not copyrightable |
| MTA / NYC Open Data | the respective portals' open-data terms |
| Pedestrian network | OpenStreetMap, **ODbL 1.0** |

ODbL matters for what the pipeline emits. The rendered map and the walk-time
rasters are *Produced Works* — attribution suffices, and `stats.json` carries
it into the title card and onto the stats and methodology pages. But `tracts.geojson` and `walksheds.geojson`, published
as files, are plausibly a *Derivative Database*, which carries ODbL's own
share-alike. Per-tract aggregates sit some distance from the source geometry so
the question is arguable; if you publish those files, label them ODbL and the
question stops mattering. (Not legal advice.)

## Results

With ACS 2023 5-year population (8,516,202 across 2,325 tracts, 2,243 of them
inhabited):

| | |
|---|---|
| Within a 5-minute walk of a train | 23.2% |
| Within 10 minutes | 64.2% |
| Within 15 minutes | 78.3% |
| Mean walk time, population-weighted | 12.0 min |
| Median / p90 | 7.6 min / 24.9 min |

The dense-but-poorly-served list reads as a roll call of NYC's known transit
deserts, which is the clearest sign the routing is behaving: **Canarsie**
(28.8 min), **Flatlands** (27.8), **Co-op City** (25.2 min at 38,886 people/km²),
**Coney Island–Sea Gate**, **East Flushing**.

That list is picked by a hard rule — the top density third, then the longest
walks — and the rule has a cliff in it. The **under-service score** measures
the thing directly instead: `population × max(walk − 10, 0)`, or **excess
person-minutes**, where ten minutes is the standard below which there is no gap
to measure. Citywide that comes to **39.1 million excess person-minutes per
one-way trip**, with **3.05 million New Yorkers** past the standard.

The score was chosen by measurement, not by argument. `pipeline/verify.py` ranks
ten candidate formulations by how many of their worst 25 tracts fall inside the
eight corridors the MTA and its partners have actually proposed, against the 26%
baseline a score with no predictive value would hit:

| score | worst 25 | worst 50 | worst 100 |
|---|---|---|---|
| **`pop × excess walk`** (shipped) | **80%** | 58% | 43% |
| `need × excess walk` | 76% | 60% | 48% |
| `pop × excess access` (with wait) | 76% | 52% | 41% |
| `carfree hh × excess walk` | 56% | 50% | 47% |
| percentile-rank product (previous) | 20% | 26% | 23% |
| baseline (random) | 26% | 26% | 26% |

Two of those rows are worth staring at. **Counting the wait makes the score
worse**, which is coherent rather than embarrassing: a frequency problem is
solved by running more trains on existing track, not by building a line, so
access cost is the better measure of a rider's morning and the worse predictor
of where an agency builds. And **the equity variables make it worse too**, in
every form tested. They ship and can be weighted in the UI — the MTA
characterizes its own Equity Areas by exactly the zero-vehicle variable — but
the default weight is zero and the measurement is why. The build fails if the
shipped score drops below a 40% floor.

Because a line serves a corridor rather than a tract, the score is reported
beside its **contiguity**: connected components over land adjacency. The largest
block is **257 contiguous tracts holding 818,291 people** in southeast Queens
— St. Albans, Baisley Park, Queens Village — with a second of 149 and 412,356
in Brooklyn from Canarsie through Marine Park and a third of 102 and 410,258
on Staten Island. That is a finding no per-tract ranking can produce. 19 of
the 56 blocks are a single isolated tract, which is a complaint rather than a
project. Until 2026-10-12 the first two were one 430-tract block of 1.31M
people: snapping stations at their real entrances split it at the
Brooklyn-Queens line, because part of what joined the halves was tracts that
are only past the standard when the walk is measured to a station point
rather than to a way in. The map draws this as its own **Under-served zones**
layer (the pages say "zone" for a block and "area" for a tract, since a reader
would picture a city block), shaded by the population of the whole block
rather than of the tract,
beside the per-tract score rather than instead of it. And across the 11 of 16
candidate scores that clear the floor, **7 tracts stay in the worst 50 under
every one** — six on the Staten Island North and West Shore, inside that
borough's 105-tract block, both of which have BRT proposals, and one in Throgs
Neck, which has no proposal at all.

Contiguity is also what the external-validity test is measured on now. Of the
shipped score's worst 100 tracts, 77 sit in a block of three or more, and
**52% of those fall inside a proposed corridor** against a 26% baseline —
clumping raises agreement, which is what should happen if the scattered tracts
were noise. The build gates on that number rather than on the per-tract one,
with the floor derived from the baseline rather than from the score's own
result, because the proposed lines are need filtered through cost and politics
and aiming at them would import all of it. Five of the eight blocks sit almost
entirely *outside* any proposal, four of those five in northeastern Queens,
and that disagreement is reported rather than penalized: absence of a plan is
not absence of need.

## Caveats

- Walk times assume **1.35 m/s** (~3.0 mph) on a flat network. Grades are not
  modeled, which understates the effort of a climb like Grymes Hill.
- Staten Island looks poorly served because **the access measures count rail
  and the ferry, and no buses**. Much of the borough depends on buses. The
  frequent-bus layer shows where useful bus service exists — 2,368 of 12,942
  stops have a route holding a 10-minute headway through peak, midday and
  evening — but buses are not in any distance or score calculation, because
  bus service is what the city provides *instead of* rail in precisely the
  places this map looks, so crediting it would hide the finding by
  construction. The ferry is in for the opposite reason: it substitutes for
  nothing, and it has its own right of way and a published schedule. It does
  not rescue the borough's numbers, though — the crossing is 25 minutes and
  the peak wait another 7.5, so the cheapest Staten Island to Manhattan trip
  in the model is 46.4 minutes against a 45-minute budget.
- Station **frequency** is measured and shipped, but the walk layers still
  count every station equally. The "Walk + wait for a train" layer is the one
  that charges for a 30-minute headway; the plain walk figures above do not,
  which is why they stay comparable to published ones.
- The **under-service score** is excess person-minutes and nothing else — no
  wait, no equity term, both measured as making it worse against agency plans.
  It is a quantity rather than an ordering, so it can be quoted, but only in
  its own units: person-minutes of walk beyond the standard, one way.
- **Commuter rail is counted as if it were a subway swipe.** It is not: LIRR
  and Metro-North charge a separate zone-based fare. 897,941 New Yorkers
  (10.5%) have one as their nearest train, and discounting them moves the
  citywide 10-minute share by only 1.9 points while costing those tracts a
  median 20.1 minutes each way.
- ACS tract populations are estimates with margins of error; `pop_moe` is
  fetched alongside. Switch `config.CENSUS_DATASET` to `2020/dec/pl` with
  `P1_001N` for exact decennial counts.
- Ellis and Liberty Islands form a tract with no walkable rail access at all.
  It is flagged `noacc` and, being uninhabited, excluded from every statistic.
