# transit-gap-map

Finds under-served areas by mapping two things together. The current
deployment covers New York City, by census tract:

1. **Walking distance to the nearest train station** — routed along the real
   pedestrian network, not straight-line distance
2. **Population density** — residents per km² of *land* area

The point is the combination. A bivariate map shows where density is high but
station access is poor, and the pipeline reports what share of New Yorkers live
within a 5, 10 and 15-minute walk of a train.

Stations counted: all **496 subway stations** (including the 21 Staten Island
Railway stops), plus **LIRR and Metro-North** stations in or just beyond the
city — 557 in total.

## Quick start

```bash
make setup                 # .venv on Python 3.14 + dependencies
cp .env.example .env       # then paste a free Census key into it
make all                   # fetch -> network -> service -> distance ->
                           #   palette -> surface -> build -> verify
make serve                 # http://localhost:8000
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
| 5 | `pipeline/aggregate.py` | Node distances → per-tract statistics and the score |
| 6 | `pipeline/palette.py` | Generate and audit the color system |
| 7 | `pipeline/surface.py` | Walk-time raster + network walkshed polygons |
| 8 | `pipeline/build.py` | Emit `web/data/` payloads |
| — | `pipeline/verify.py` | Barrier test, spot checks, and the alignment gate |

Everything under `web/data/` is generated and gitignored, so a fresh clone must
run `make all` before `make serve` — the map reports that explicitly rather than
hanging if the payloads are missing.

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

Also checked: stations measured from their own coordinates return ~0 (median
22 m, confirming the virtual-source wiring); dense core tracts come back under
5 minutes; known thin-coverage areas (Breezy Point, Charleston, Bergen Beach,
Country Club) come back far; the population join is complete; and the payload
budgets hold.

The headline number is cross-checked against **its own straight-line
equivalent** rather than an external figure. Published "share within a half
mile of the subway" numbers vary with method and rarely say which one they
used, so the pipeline computes both shares itself — same population, same
stations, the only difference being routed versus crow-flies:

```
Within a half mile of a subway/SIR station:
  straight-line      :  71.6%
  walking network    :  62.2%   (9.5 points lower)
```

That pair is self-checking: routed distance is always >= straight-line
distance, so the network share can never exceed the straight-line share. If it
ever did, the projection, the join or the snapping would be wrong. The 9.5-point
gap is the cost of NYC's actual geography, and the median tract detour factor
is 1.34x.

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
one ordered ramp carries 7 steps where a matrix could not carry 16, because the
reader only has to see the order rather than identify a cell. It is violet —
slot 7 of the documented palette — and not a shade of either data ramp, since
blue already means density and warm already means walk time. Its gates are the
ones for an ordered ramp and not for a matrix: monotone lightness, at least
0.06 lightness per step, and a near-surface step that still clears 2:1 against
the page, measured at 0.068 and 2.23:1 light, 0.067 and 2.44:1 dark. Sampling
the bivariate field's own diagonal was tried first and rejected: it crosses
from cool grey to dark red, which reads as a diverging scale with a midpoint
the score does not have, and its pale end sits at 1.35:1 on the light surface.

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
it into the title card. But `tracts.geojson` and `walksheds.geojson`, published
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
block is **430 contiguous tracts holding 1,313,894 people**, running from
Canarsie through Flatlands to St. Albans — a finding no per-tract ranking can
produce. 24 of the 58 blocks are a single isolated tract, which is a complaint
rather than a project. And across the nine candidate scores that clear the
floor, **12 tracts stay in the worst 50 under every one** — all Staten Island
North and West Shore, all inside that borough's 105-tract block, and both of
those corridors have BRT proposals.

## Caveats

- Walk times assume **1.35 m/s** (~3.0 mph) on a flat network. Grades are not
  modeled, which understates the effort of a climb like Grymes Hill.
- Staten Island looks poorly served because **the access measures count trains
  only**. Much of the borough depends on buses and the ferry. The frequent-bus
  layer shows where useful bus service exists — 2,368 of 12,942 stops have a
  route holding a 10-minute headway through peak, midday and evening — but
  buses are not in any distance or score calculation.
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
