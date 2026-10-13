# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A GIS pipeline plus a static web map that answers *which parts of New York are
under-served by transit*, by relating network walking distance to the nearest
train station against population density. Python computes everything offline and
emits a handful of small payloads; the browser only draws them.

`ROADMAP.md` is the plan of record for where this is going and carries the
reasoning behind choices already made — read it before proposing a feature, and
check whether an idea is already there (often with an argument against it).
Reorganized 2026-10-12 and it has four parts now: *Suggested order* near the
front is a ranked table of everything left, a phase per question in the
middle, *Decisions still open* for the choices that block work and are not
settled by measurement, and *The record* at the back for the pre-registered
results and the corrections. **A phase number is not a priority** — Phase 10
is ranked seventh — and nothing punted sits at the top of the file any more:
an unfinished item lives in the phase that owns its question.

## Working with the owner

Asked for on 2026-10-12.

- **Explain things in plain terms.** The owner does not have a data science
  background. When reporting a result, proposing a change or answering a
  question, say what it means in everyday words first, then give the
  technical detail if it helps. Define a statistical term the first time it
  comes up (quantile, Spearman correlation, coefficient of variation,
  censoring) or avoid it. The design record below can stay technical, but
  anything said *to* the owner should not need it to be understood.
- **Best results come first.** A slower build or a slower page load is
  acceptable when it buys a more accurate or more useful map. When a speed or
  size limit and result quality pull against each other, favor quality and
  say what it costs. Two things this does *not* change: the payload budgets
  stay as tripwires, so going over one is still a stated decision rather
  than a silent one (this preference makes "raise it" a legitimate answer
  when the extra bytes buy something real); and extra computation past the
  point where results stop changing is not a better result — the 25 m grid
  spacing below is the example.

## Commands

```bash
make setup        # .venv (Python 3.14) + requirements; then cp .env.example .env
make all          # fetch -> network -> service -> distance -> transit -> access
                  #   -> palette -> surface -> build -> verify
make serve        # serve.py on :8000 (PORT=xxxx to change); sends no-store
make transit      # rail + ferry transit graph: station-to-station time
make access       # jobs reachable, time to a tenth, hubs, detour factor
make verify       # the correctness gate
make clean        # wipe data/processed and web/data
make publish      # upload web/ to the live site, then smoke-test it
make lock         # re-pin constraints.txt from the current venv
```

Deploying is in `DEPLOY.md`: S3 + CloudFront from two Terraform modules
split by privilege, and a manual GitHub Actions workflow that builds and
publishes. `make setup` installs under `constraints.txt` so CI builds with the
same library versions as a laptop; an upgrade is `make lock`, `make verify`,
commit.

Single stage, which is the normal development loop:

```bash
.venv/bin/python -m pipeline.<stage> [--force]
```

`--force` means "recompute, ignore the cached intermediate" and exists on
`fetch`, `network`, `service`, `distance`, `aggregate`, `surface` and `build`.
Without it a stage reuses `data/processed/`, so an edit to stage 4 will *not*
show up downstream unless the later stages are forced.

Type checking (pyright is **not** installed in the venv; use npx):

```bash
npx -y pyright
```

**Baseline is 35 errors, 0 warnings.** All 35 are third-party stub gaps, not
defects — pyrosm typed as returning `Optional`, `scipy.spatial.cKDTree` absent
from the stubs, shapely's `BaseGeometry.geoms`, `Series.map` with a dict. Treat
36 as a regression; they want casts, not fixes. It was 34 until `siting.py`
arrived on 2026-10-12 and imported `cKDTree`, which is one more instance of
the same stub gap rather than a new kind of error — the rule for raising this
number is that the new error has to be in that catalog. It was 38 until Phase 4, and
the move is inference noise rather than a fix: merging the accessibility table
into `df` in `aggregate.run` changed what pyright infers for it and six of
that file's errors went away, while two new `cKDTree` imports arrived. The
patterns that keep new code off the list are in `pipeline/transit.py` — wrap
a `pd.to_numeric` result in `np.asarray`, write `.map(lambda k: d.get(k))`
rather than `.map(d)`, and construct a `pd.DataFrame(...)` around a
`groupby().agg()` whose return the stubs type as `NDFrame`.

`pyrightconfig.json` also has the unused-code rules turned on, but note the
limit: pyright only reports unused
*locals* and *private* module symbols, never a public module-level function, so
it is a sufficient signal for dead code and not a necessary one.

## Architecture

Eight stages, each a module under `pipeline/` runnable on its own, each writing
a cached artifact that the next one reads. The cache boundaries are the design:
stage 2 takes ~4-5 minutes and stages 3-8 take seconds, so iteration happens
downstream of the expensive parse.

| Stage | Module | Writes |
|---|---|---|
| 1 | `fetch.py` | `data/raw/` — stations, boundaries, census, 3 GTFS feeds, 153 MB OSM extract |
| 2 | `network.py` | `walk_graph.npz` — undirected CSR adjacency + node coords; `--bike` writes `bike_graph.npz` from the same code |
| 3 | `service.py` | `station_service.parquet` + `bus_stop_service.parquet` — trains and buses per hour, from GTFS |
| 4 | `distance.py` | `node_distance.npz` — nearest-station distance per node |
| 5 | `aggregate.py` | `tract_stats.parquet` — per-geography statistics |
| 6 | `palette.py` | `web/data/palette.json` |
| 7 | `surface.py` | walk-time raster PNGs + `walksheds.geojson` |
| 4b | `transit.py` | `rail_station_time.npz` + `transit_graph.parquet` — a rail + Staten Island Ferry transit graph, solved for every station pair |
| 4c | `accessibility.py` | `tract_accessibility.parquet` + `tract_transit_time.npz` — jobs reachable, time to a tenth of them, hub times, detour factor |
| 9 | `siting.py` | `siting.parquet` — one hypothetical station per lattice site, and what it would remove |
| 8 | `build.py` | `tracts.geojson` (geometry), `tract_cols.json` (every column), `stations.geojson`, `bus_ftn.geojson`, `hubs.json`, `siting.geojson`, `stats.json` |
| — | `verify.py` | nothing; checks stages 4 and 4b |

Stage 3 depends only on stage 1, not on the walk graph, so it can be re-run
without touching the expensive parse. **Stage 4b is the same** — it reads the
three rail GTFS feeds and nothing else — while 4c is the one place the walk
graph and the rail graph meet.

`config.py` is the single source of truth for every path, CRS, threshold, URL
and tunable. Nothing else should hardcode any of those. `ROOT` is derived from
`__file__`, so the checkout can be renamed or moved freely.

### The ideas that are easy to miss

**One Dijkstra answers the whole city.** There is deliberately no routing server
and no per-origin queries. Because the question is "distance to the *nearest* of
N stations for *every* location", `scipy.sparse.csgraph.dijkstra(...,
min_only=True)` collapses all station sources into one C-speed pass and returns
which station is nearest to each node as a side effect. Any proposal that adds
OSRM/Valhalla or loops over origins is moving backwards.

**Station cost is a virtual source plus the snap offset.** A station 40 m off the
mapped network pays for that gap rather than being treated as on it. This is also
the hook for future wait penalties (see Phase 2 in `ROADMAP.md`) — add to the
source cost, don't restructure the pass.

**The pedestrian network is genuinely disconnected.** Staten Island has no
walkable link to the other boroughs, so stage 4 works per connected component
and publishes a `serviceable` mask: nodes in a component that actually contains a
station. Stage 5 snaps only to those, which stops a sample point attaching to an
isolated park path and being reported as having no transit access at all. Keeping
only the largest component would drop ~48% of nodes.

**GTFS joins on keys, and three details decide whether the numbers mean
anything.** `service.py` attaches scheduled trains per hour to each station.
The joins are exact — the subway table's `gtfs_stop_id` is a GTFS parent
station (496/496) and the commuter-rail table's `code` is the GTFS `stop_code`
(40/40 LIRR, 21/21 Metro-North) — so no name matching exists anywhere in the
stage. What does need care: `stop_times` references *platforms*, so a subway
station must be resolved through `parent_station` or every frequency in the
city is halved; service-day hours run to 28, so hours are taken **mod 24**,
which is what unions the 3am trains recorded as hour 27 with those recorded as
hour 3; and the commuter-rail feeds mark drop-off-only stops with
`pickup_type=1`, which are not service from a boarding rider's point of view.
Frequency is always **per direction** (total ÷ directions with service), since
that is the headway a rider experiences. Service levels are sampled on one
representative Wednesday and Saturday inside the *intersection* of all three
feeds' date ranges — they do not overlap by default, and a date outside one
feed makes its stations read as closed.

**The Frequent Transit Network is defined twice over, and both choices were
measured.** The bus layer ships only stops where a *single route* holds a
10-minute headway across `C.FTN_WINDOWS` — peak, midday and evening. Per
route rather than per stop, because three routes an hour each going somewhere
different is not twenty-minute service (the combined definition admits 1,736
more stops); and all day rather than at the peak, because 8,027 of 12,942
stops qualify on the AM peak alone — 62%, the "covers the whole city and says
nothing" layer this was built to avoid. The all-day per-route test leaves
2,368 stops, 18%, which has a shape. The six bus feeds are **not** partitioned
by borough despite their names: **five of the six** share one identical
307-route `routes.txt` (`gtfs_bus_busco` has its own, 92 routes, for 399 in
all) and they partition *trips* (zero `trip_id` overlaps), while stops do
overlap and must be deduplicated after the union. `stop_times.txt` across the
six is ~10M rows, so it is read in chunks and window-filtered inside the loop.

**Tracts are measured by a grid, not a centroid** — a 25 m sample grid clipped
to each polygon, reduced with the median, because NYC tracts are long and
irregular and a centroid often lands in water or a park. The spacing is
converged, not chosen: the per-tract median moves 0.05 min between 25 m and a
15 m reference run, so finer is measurement noise. `config.py` carries the
numbers. Every meter-valued tunable is named `*_METERS`, spelled out, because
`_M` beside `HALF_MILE` could be read as miles.

**`palette.py` is the only place colors are defined.** Both the raster in stage 7
and the browser read `palette.json`, so there is exactly one definition of every
color. The four sequential choropleths stay inside that system rather than
beside it: `app.js:seqRamp()` reads `pal.bands`, the ramps `palette.py`
audits, as shipped. The detour factor has **no layer**, deliberately —
measured, the middle 80% of tracts run 0.79 to 1.28, so a choropleth of it is
close to one color. It ships in the tooltip, and a diverging ramp for it would
need new steps through `audit()`'s gates.

**One palette serves both themes, and the arithmetic of why is worth keeping.**
Revised 2026-10-11. Every ordered ramp used to be stepped twice, once per
surface, so that the well-served end receded toward the page in both modes.
That preserved *emphasis* and inverted *direction*: dark meant under-served on
a light basemap and light meant under-served on a dark one, so **the map
changed meaning when the reader changed theme**. Direction won.

The constraint is not a matter of taste. A color clearing
`CONTRAST_FLOOR` (2:1) against both `#fcfcfb` and `#1a1a19` must sit in a
middle band — measured on these hues, OKLCH L roughly **0.42 to 0.78** —
because "away from the surface" points in opposite directions on the two
surfaces. `dual_band()` solves it by scan rather than algebra, since the
chroma a hue can carry varies with lightness and `_fit_chroma` is what
decides it. Four consequences, all measured:

- **Six steps, not seven.** Seven inside the band are 0.055 of lightness
  apart against a 0.06 gate; six are 0.067. The step count gave way rather
  than the gate. `app.js:SEQ_STEPS` must match `palette.py:SEQ_STEPS` —
  asking for more bands than the ramp has colors repeats one and silently
  merges two.
- **The deep ends are gone.** The violet score ramp ran to L 0.338 and now
  stops at 0.435; its worst step reads 2.01:1 against the dark basemap where
  the old inverted dark ramp gave it 12.5:1. In dark mode the finding is
  clearly visible rather than loud. That is the price of the change.
- **The band anchors are not the categorical anchors.** `banded()` reads the
  anchor's *hue*, and sRGB cannot hold every hue across the band: measured,
  `ANCHOR_ORANGE` drifts **7.7 degrees** of hue over six banded steps and
  would fail `HUE_SPREAD_MAX`, while `warm[10]` drifts 0.3 and `blue[3]` 0.4.
  So `BAND_ANCHOR` indexes into the families rather than reusing the anchors
  they were derived from.
- **The raster is one PNG now**, not a light one and its reverse, which also
  dropped 63 KB from the payload. `walk_surface.json` still carries both keys
  so a stale cache does not 404.

`audit_ordinal` takes a *sequence* of surfaces as of this change, and the
plural is the gate: a shared ramp checked against one surface is checked
against half of what it claims. All seven shared ramps land at 2.01:1.

**And it was checking a color the map never drew.** Found 2026-10-12 from a
screenshot, fixed the same day. The gate reads `contrast(hex, surface)` while
`app.js` drew the choropleth at `fill-opacity: 0.78`, so the audited 2.01:1
was the contrast of a swatch, not of a tract. Composited, every shared ramp's
darkest step reached the screen at **1.66:1** against the page and **1.76:1**
over dark-matter's actual land, which is darker than `SURFACE_DARK` because
that constant is the *page* color and not the basemap's. The shortfall landed
entirely on the under-served end, since that is the end nearest the page in
dark mode — the gate was softest exactly where the finding lives.

Two things follow, and the second is the general one. `MAP_FILL_ALPHA` now
sits beside `CONTRAST_FLOOR`, `audit_ordinal` composites at it before
measuring, and `app.js:fillOpacity()` must match it — the same standing
requirement as `SEQ_STEPS`, enforced the same way, by a gate that fails rather
than by a comment. Lowering it is a palette decision and not a styling one:
swept against the 0.06 lightness gate, the shared band holds **four** steps at
0.78, six exactly on the floor at 0.90, and the shipped six at 1.0. And the
opacity turned out to be unaffordable for a reason that had nothing to do with
color: `tracts-fill` was added with no `beforeId`, so it sat on top of the
entire basemap *including every label*, and the transparency was buying washed
out street names under a choropleth rather than context. The tract layers now
insert beneath the basemap's trailing run of symbol layers — 26 in positron,
27 in dark-matter, zero non-symbol layers above the anchor in either — so the
labels draw on top and the fill can be opaque. `labelAnchor()` walks back from
the end of the style rather than naming `watername_ocean`, so a renamed layer
degrades to the old appending behavior instead of throwing.

The general lesson is the one this file already states about censored
measures, in a second costume: **a gate that measures the input rather than
the output will pass a thing that fails.** `verify.py` has the same hazard
and avoids it by construction, measuring the shipped artifact; `palette.py`
measured its own intermediate for a day.

**The bivariate matrix is the one exception, and it is measured rather than
conceded.** It stays stepped per mode (`FIELD` keeps both entries) because it
is the only layer where lightness carries *two* variables and therefore needs
twice the range. Searched inside the band: its best achievable
`among_populated_delta_e` is **12.3 against a 15.0 floor**, and the
dual-surface floor has to fall to **1.2:1** before it passes — which is
where a cell genuinely starts dissolving into the page. So one layer still
flips direction with the theme while the other eight do not. Anyone
revisiting this should re-run the search before assuming it is possible.

**So the map opens on the score, not on the matrix.** Changed 2026-10-12, and
the flip is only half the reason. The bivariate layer was the default and was
the one layer with no validated number behind it: `alignment()` measures the
shipped score at 80% of its worst 25 inside a proposed corridor and 52%
clumped, while the matrix cross-tabs the same two inputs into terciles and has
never been tested at all. Measured on the shipped payload, its dense+far cell
ties **53 tracts whose scores span 5,261 to 80,727 excess person-minutes** —
a 15x spread inside one swatch, which is the resolution a project like this
exists to show. The matrix stays as an opt-in lens, because separating the two
variables is a job the score genuinely cannot do: a far tract is not always an
empty one. But it is no longer what a reader sees first, and it is no longer
what a bare link means — `app.js:syncHash` omits the default layer, so that
constant and `state.choropleth` must change together or every short URL
silently names the wrong layer. Doing this before the map is published is what
made it free; afterwards it would have changed what existing links mean.

**The under-served blocks layer — "Under-served zones" on the page — draws the
contiguity measure, and shades it by the block's population rather than the
tract's.** Added 2026-10-11 beside the
per-tract score, not in place of it — both views ship, because a tract past
the standard on its own says something different from the same tract inside a
430-tract band. There is deliberately no categorical fill per block: 58 blocks
would mean 58 hues, which breaks the rule against generating or cycling them,
so magnitude gets a sequential ramp and identity goes in the tooltip. Two
details are load-bearing. `cluid` is **-1** for every tract not past the
standard and those paint `unpop()`, because "no gap to measure" is not "the
smallest gap". And `seqBreaks` takes a `positive` flag for this layer, since
`clupop` is 0 on 1,402 of 2,325 tracts and those zeros would otherwise swallow
the bottom quantiles whole. Even so the breaks collapse 6 → 4: the largest
block holds 430 of the 923 clumped tracts, which all share one value, so the
top band's label reads "and up" rather than "over".

The bivariate matrix is an **OKLCH field** (`FIELD` + `bivariate_field()`),
not a four-corner bilinear blend — that was tried and provably cannot put the
chroma on the finding. `audit()` enforces perceptual-separation `GATES` grouped
by the reader's task (populated cells held to ΔE 15, the sparse row to 8); it
raises rather than warns. Load the **dataviz skill** before touching any of this.

**The web map is static and dumb on purpose.** `web/` is three pages —
`index.html` + `app.js` (the map), `stats.html` + `stats-page.js` (the
figures and fun facts), `about.html` + `about.js` (the methodology) — plus
`stats.js` (every figure and sentence built from `stats.json`), `doc.js`
(theme and links for the two document pages) and `style.css`, reading the
payloads in `web/data/`
(`tracts`, `stations`, `bus_ftn` and `walksheds` GeoJSON, `stats.json`,
`palette.json`, `hubs.json`, `walk_surface.json` and `walk_surface.png`, which
is one image for both themes since the ramps stopped flipping). `hubs.json` is
fetched with a `.catch(() => null)`: it is 39 KB only
the hub switcher reads, and a build from before Phase 4 has no such file, so a
rejection must not take the map down with it. `web/data/` is gitignored
and entirely generated, so a fresh clone must `make all` before `make serve`.

**`make serve` runs `serve.py`, not `python -m http.server`, and the reason is
a bug that looked like a different bug.** The stdlib server sends
`Last-Modified` with no `Cache-Control`, so a browser applies heuristic
freshness and can serve an edited page out of its own disk cache — which it
did, on 2026-10-12, making a shipped change to `index.html` look like a change
that had not worked. `serve.py` sends `no-store` on every response, so a
reload always shows what is on disk. It still does not compress, deliberately:
the wire figures everywhere in this repo are gzipped because that is what a
CDN serves, so the map is slower in development than in production and the
budgets stay measured rather than observed.
Anything a user might want to re-weight should recompute in the browser from
per-tract fields rather than round-tripping through the pipeline.

**The map page carries no figures; `stats.html` has them and `about.html`
says how they were made.** The title card went from fifteen stat rows to a
hero number and four rows on 2026-10-11, and on 2026-10-12 to none: it is a
description of the map and links to the other two pages. The external-validity
sentence went with it, and is now a fact on the stats page rather than a line
beside the map. **Copy is plain on purpose** — headings name the section
("Limitations", "Commuter rail fares"), links name the page ("Stats",
"Methodology"), and a fact's sentence says what the figure counts rather than
teasing it. Asked for on 2026-10-12, after links like "every
number, and what it leaves out" read as clickbait.

**The pages say "area", never "tract", and "zone", never "block".** Same day.
"Tract" is planner jargon and "block" made readers picture a city block,
which is also a different, smaller census unit. The map card defines an area
once ("Each area is a census tract, home to about N people", N from
`stats.json`), and the stats and methodology pages each say it in passing for
a reader who lands there first. Code, `stats.json`'s `schema`, and these docs
keep "tract" and "block", where they are the precise terms. Don't use "area"
for anything bigger than one tract on a page — "one continuous area" had to
become "zone" for that reason — and don't use "neighborhood" for a tract,
since a neighborhood holds dozens of them.

Three rules hold the split together. **No figure is ever a literal in prose**:
every sentence on every page interpolates from `stats.json`, so a rebuild
cannot leave the words contradicting the map — this is the whole reason
`stats.js` takes `stats` as an argument and reads no DOM and holds no state.
`facts()` there returns the stats page's cards as data, each guarded so a
build without that section drops the card rather than printing a dash. **The
document pages load `stats.json` alone** (46 KB), not the 2 MB of GeoJSON and
not MapLibre. And **the live table stays with the map**: `renderTable()`
ranks the worst tracts by the function the map is painting with at the
current slider weight, which a static page cannot do, while the borough table
is on `stats.html` and the block and corridor tables on `about.html`.

The theme travels between pages in the URL: the **query string** on the
document pages (`stats.html?t=dark`), which leaves their hash free to be a
section anchor, and the hash on the map, which is where its permalink lives.
Every cross-page link carries `data-page`, and both `app.js:syncPageLinks()`
and `doc.js` rewrite all of them in one pass, so a new link needs only the
attribute. `doc.js:scrollToHash()` handles an anchor after the fetch
resolves, because the section does not exist when the browser's own anchor
handling runs.

**`map.addSource` serializes the GeoJSON object, so a property written after it
never exists.** `rankTracts()` writes the derived fields the paint expressions
read (`rd`, `rnv`, `rpv`, `exc`) onto `tracts.features` and then freezes every
properties object, because the failure mode is silent and total: the
under-service layer read an `nb` property written on the first `gapCompute()`,
which runs inside `addLayers()` — one call after `addSource` — so `["get",
"nb"]` returned null, `*` threw on all 2,243 populated tracts, and MapLibre
fell back to the `fill-color` default, which is **black**. The city painted one
color and the slider moved only the legend, which is computed in JS. A
reweighting control must therefore be arithmetic over properties the source
already holds: `needExpr()` is `needBlend()` written as an expression, and the
two were checked to agree to 0.0 on every tract at five weights before shipping.
The freeze turns a repeat of this into an immediate throw.

**The transit graph's node is a leg, not a station and not a route, and the
reason is where the wait comes from.** `transit.py` builds a rail-plus-ferry
frequency graph. One node per *station* lets a shortest path change vehicles
at no cost, so transfers become free and the graph claims a connectivity the
city does not have — the opposite of what this measures. One node per
`(station, route, direction)` fixes that and introduces a different error:
`headway / 2` is the headway of the service a rider waits for, and taken per
route it is wrong wherever several routes at one station go to the same place
next. Metro-North runs two inbound trains an hour on the Harlem line at
Fordham and two on the New Haven line, both to Grand Central; charging each a
15-minute wait tells a Grand Central-bound rider to wait twice as long as they
do. Measured: the per-route rule overstates 856 subway and 118 commuter-rail
waits, by up to 14 minutes at Jamaica. Taken per *station* it is wrong the
other way — at Union Square it would hand an L-train rider the combined
headway of eight routes — and a transitive "shares any next stop" grouping
collapses Jamaica's wait to 0.8 minutes, which is absurd.

So the node is `(route, direction, from, to)`: one per leg. The wait charged
is the combined headway of **every service making the same next stop**, which
is the Chriqui-Robillard common-lines result applied one hop at a time, and
staying on the train is a zero-wait edge to the next leg of the same route.
Two details are load-bearing. A leg may only be *boarded* if it runs in the
window, even though the wait it pays is its whole group's — otherwise a 6am
express inherits the 8am local's wait and gets ridden the length of its own
pattern. And arrival and departure are separate station nodes, with the
in-station change time on the edge between them, so only a rider who actually
transfers pays it; the same declared time also sits on a departure-to-departure
edge, because without it a rider standing on the Times Square 1/2/3 platform
who wants the Grand Central shuttle gets routed onto a train, around and back.
Median wait comes out at 3.0 minutes against 3.8 per route alone.

The residual bias is stated rather than fixed: a rider who would accept either
the local or the express still pays the wait for whichever group they board,
because resolving that needs hyperpath routing rather than a shortest path.
Fordham to Grand Central therefore reads 37 minutes against a real expected
~27. It biases toward *overstating* under-service on commuter rail, which is
the direction this project has to be most suspicious of.

**Carless crossed with poverty was predicted to be the strongest need signal
here, and the measurement says the opposite.** Added 2026-10-12, Phase 5.
Each geography is placed against the citywide medians (50.6% of households
without a car, 13.7% below the poverty line) and the four quadrants come out:

| group | population | median walk | past the 10-min standard |
|---|---|---|---|
| Car-owning, better off | 2,354,364 | 15.8 min | **71.3%** |
| Poorer, car-owning | 1,231,671 | 10.6 min | 50.6% |
| Carless without means | 3,287,755 | 6.2 min | **18.2%** |
| Carless by choice | 1,633,214 | 5.5 min | 8.9% |

So **"carless without means" is among the best-served groups in the city**,
because poverty and carlessness concentrate in the dense core the subway was
built around. The crossing names who *depends* on transit, which is worth
knowing, and it does not name who is *missing* it — and multiplying it into
the score would pull the score toward the well-served core. That is the same
result the alignment table already reports from the other direction (the
equity variables lower agreement, car-free households alone to 56%), now with
a mechanism rather than only a number. The figure to carry is the overlap:
**596,782 people are both carless without means and past the standard.**

The quadrant is **derived in the browser**, not shipped: it is
`(nveh > median) + 2 * (pov > median)`, both shares are already properties
and both medians are in `stats.json`, so a column would cost ~2 KB to save a
comparison. `app.js:NEED_QUADRANTS` is indexed by that arithmetic, so its
order is fixed by the expression and not by matching labels.

**Every ACS share now arrives with its margin of error, and the margins are
wide.** `CENSUS_EXTRA_MOE_VARS` is generated from `CENSUS_EXTRA_VARS` so a
variable cannot be added without one, which is what keeps the pairing
honest — 46 variables in one request against the API's limit of 50, and
`fetch.py` raises rather than silently truncating at 51. Shares use the
Bureau's *derived proportion* formula, not the ratio formula, because the
numerator is a subset of the denominator; where the term under the root goes
negative the Bureau's own fallback to the ratio formula applies, which
happened on 226 of 2,325 geographies. Reliability is the coefficient of
variation (MOE / 1.645 / estimate) against a 0.30 threshold, which is the
convention agencies use: **56.4% of populated tracts** are over it on the
zero-vehicle or poverty share. Read that as "imprecise relative to its own
size", not as wrong — a tract at 2% poverty ±1.5 points is over the threshold
and still a low-poverty tract — and note that it matters precisely where a
reader ranks geographies by these shares, which is what the equity slider
does. Flagged in the tooltip, never dropped, and it reaches no published
figure.

**Step-free access is a lens and a station ranking, never a term in the
score.** Added 2026-10-12. `distance.py` takes a `step_free` flag beside
`subway_only` and `weighted` — the same multi-source Dijkstra over the 219 of
557 stations with a step-free route built — and ships `sf` per geography. It
is kept out of the score because the two answer different questions with
different interventions: the score ranks *places* and its answer is new
service, while this ranks *stations* and its answer is an elevator, so
folding them together would argue for a rail line as the fix for a missing
lift. It is also the one input here being actively *changed* while the map is
built, so anything load-bearing on it decays.

Three measurements and one wording trap. The median area's walk goes
**8.4 → 14.4 minutes**, which is +2.8 at the median area — the median of the
per-area differences, not the difference of the two medians, the same
distinction `median_penalty_min` already draws for commuter rail. Past the
10-minute standard: **35.8% of the population, 67.4%** if the station has to
be step-free, which is the figure the layer exists to produce. And **"your
nearest station excludes you" (59.9%, 5.1M people) is not "how much further
you must travel"** — the first is a count over nearest stations, the second
needs the filtered pass, because the real nearest step-free station may be a
mile past the one a rider cannot use. Both ship, labeled apart.

The station table is the other output, and its own finding is deflating:
**249 stations without a step-free route are somebody's nearest, and the
worst 15 hold only 17% of the affected population.** There is no short list
of elevators that fixes this, so the table publishes 40 rows
(`C.STEPFREE_TABLE_ROWS`) precisely so it cannot be read as one. Everything
is labeled "step-free **by design** as of ⟨fetch date⟩", from the station
table's mtime rather than from prose, because the flag means a route was
built and MTA's live outages are not in this.

**A bike closes the middle of the gap and barely touches the end of it, and
that is the whole finding.** Added 2026-10-12, Phase 6. `network.py --bike`
parses the same extract as a cycling network (1.02M nodes against the walk
graph's 1.84M), `distance.py --bike` runs the identical multi-source pass over
it, and `C.bike_minutes` converts at `BIKE_SPEED_MPS` = 3.6 m/s (13 km/h).
Nothing downstream changed shape, which is what the one-Dijkstra design was
for. Measured, by how far the walk is today:

| walk today | population | median ride | inside 10 min by bike |
|---|---|---|---|
| 0–10 min | 5,464,431 | 2.2 min | 100% |
| 10–20 min | 1,814,076 | 5.1 min | 100% |
| 20–30 min | 666,157 | 9.0 min | 72% |
| over 30 min | 571,538 | 14.7 min | **0.5%** |

Citywide that reads "75% of the 3.05M people past the walking standard are
inside it by bike", and quoting that alone would be close to a lie: the
figure is carried by the middle band, and **3,109 of the 571,538 people in
the worst areas** are rescued. Those places are far by street *network*, not
merely far on foot, so a bike moves a 39-minute walk to a 15-minute ride.
That is the measured case against treating bike lanes as a substitute for a
line, and the case for them in the middle band.

Two things to state with any bike number, both in the schema and on the
page: it is **every street** and not the protected network, because matching
the DOT facility data onto OSM edges is a method decision the roadmap has
not settled, so this is a best case; and it assumes somewhere to leave the
bike, which at outer stations is the binding constraint and is in none of
this data.

`bike_min` is the first **optional** payload field (`build.py:OPTIONAL`): the
cycling graph is a second four-minute parse, so a checkout without it builds
everything else and the layer hides itself. One trap worth not re-deriving —
`distance.py`'s node-level distance line is **not comparable between
variants**: the bike median reads 3,536 m against the walk's 1,079 because
the cycling network joins the far corners of the buffered bbox into one
component where the pedestrian network leaves them in station-less fragments
the serviceable mask drops. At geography level the ride is shorter than the
walk everywhere, as it must be.

**Where one more station would help most is a ranking of points, measured in
the score's own unit.** Added 2026-10-12, Phase 7. `siting.py` puts one
hypothetical station on each of 4,840 lattice sites at
`C.SITING_SPACING_METERS` (400 m) and recomputes the map. Three things make
it 10 seconds rather than hours: a new station cannot change an answer beyond
`C.SITING_REACH_METERS` — `ACCESS_FLOOR_MINUTES` of walking, 810 m — so every
pass is a *bounded* Dijkstra at 12 ms; the existing nearest-station distance
is already on disk, so the comparison is against a vector rather than a
recomputation; and only the tracts holding an improved sample point need a
new median.

**It reports excess person-minutes removed, not people moved, and the
difference is load-bearing.** The best site removes 821k person-minutes; the
best site that moves *nobody* inside the 10-minute standard still removes
560k, because it cuts a 25-minute walk to 14 without reaching 10. A count of
people moved scores that at zero, which would rank the outer city's worst
gaps as worthless. Person-minutes is also the unit the shipped score is in,
so a site's benefit and a tract's deficit are the same quantity.

**The gate is that this stage's baseline must *be* the published walk time.**
It rebuilds the 25 m lattice, the snapping rule and the median from the same
functions `aggregate` uses, so the two agree to 0.000 m on 2,324 of 2,325
tracts (the one exception is measured upstream by the finer refinement
lattice, and is named rather than tolerated). `_check_baseline` raises past
1 m. A ranking of sites computed against a slightly wrong baseline would look
entirely reasonable, which is exactly why it is asserted.

Two publishing rules. The ranked list suppresses near-duplicates — a site is
kept only if it is `SITING_MIN_SEPARATION_METERS` (twice the reach) from
every site already kept, because adjacent candidates serve almost the same
people and an unsuppressed top ten is one finding printed ten times. And the
map layer draws **hollow** circles in the station color, off by default,
labeled *hypothetical* rather than *proposed*: nobody has proposed these, and
the roadmap's rule is that a screenshot must not be mistakable for the
network as it is. The top sites converge with the alignment test's
disagreement table from a completely different direction — College Point,
Pomonok, Throgs Neck, the Staten Island north shore — which is the useful
part, since those are the places the agencies' own plans do not cover.

**Buses are in neither the transit graph nor the score, and that is an
epistemic decision, not a data one.** Bus service is what the city provides
*instead of* rail in precisely the places this map exists to find, so letting
a bus close the gap would hide the finding by construction. The
frequent-bus-network layer keeps the job of showing what runs today, drawn on
top of the score rather than netted off it.

**The Staten Island Ferry is in, and it is the same rule rather than an
exception to it.** The bus rule is about substitution, not about vehicles:
the ferry substitutes for nothing. It is the only fixed-guideway trunk link
across the harbor, on its own right of way at a published headway, and no
proposal replaces it. Leaving it out did not produce a conservative number,
it produced a false one — the rail-only graph answered "how does a Staten
Islander reach a job" with 18,000 of 4.47M, for 495,000 people, which is true
of rail and false of transit. The operative line is dedicated-right-of-way
trunk service against street-running service that shares its lanes with
traffic. `config.GTFS_FERRY_FEED` carries the argument and the feed facts;
NYC DOT publishes the feed through NYC Open Data (dataset `b57i-ri22`), and
not through the nyc.gov URL its own metadata advertises, which 403s anything
without a browser user agent.

Added 2026-10-11, and three things it changed are worth knowing before
reading any Staten Island number:

- **It did not move jobs-in-45, and the arithmetic says why.** The crossing is
  25 minutes on every one of the feed's 416 trips, and the expected wait at
  the 8–9am headway (four sailings an hour) is 7.5, so the trip costs 32.5
  minutes before anyone walks anywhere. Measured, the cheapest door-to-door
  Staten Island → Manhattan trip in the model is 8.5 + 32.5 + 5.4 = **46.4
  minutes**, just past the 45-minute budget. So Staten Island still reaches
  ~18,000 jobs — now because the ferry is slow rather than because the model
  pretended it was absent, which is a different claim and a defensible one.
- **It did move the detour factor, a lot.** Staten Island's inter-borough
  pairs used to be unreachable and were therefore *excluded* from its median,
  so the layer was flattering the borough by construction. Its median detour
  went 1.17 → **1.39**, making it the worst-detour borough, and station-pair
  reachability went 89.2% → 94.6%.
- **It moved a pre-registered result without breaking it.** The largest
  worst-decile detour block was 56 tracts in Flatlands/Marine Park with 54 of
  them inside a large under-service block; it is now **57 tracts, all on
  Staten Island, 44 of them** inside the 105-tract Staten Island
  under-service block. The two-independent-measures agreement survived and
  relocated. Note the wording trap this exposed: that cross-check matches
  against *any* under-service block past 100 tracts, and there are two (430
  across Brooklyn and Queens, 105 on Staten Island) — calling it "the
  largest" was accidentally true before and false after.

`verify.py` used to *assert* St George → Atlantic Av was unreachable,
precisely so that adding a ferry edge would fail the build and force this
decision. It did. The pair is kept as `ISLAND_LINK` with the sign flipped: it
now checks that the connection has not quietly disappeared again.

**The accessibility measure has one budget, and the first version had two.**
`C.JOBS_BUDGET_MINUTES` is 45 minutes door to door. An early pass also capped
the access walk at 15 minutes, which told 625 tracts — 27% of the city, and
exactly the under-served ones — that they had no rail access at all. That is
a truncation manufacturing the project's own thesis. The cap was also
unnecessary: a 40-minute walk leaves five minutes of riding and reaches almost
nothing, so long access walks drop out on their own. Walking the whole way
stays a competing option, because a Financial District tract can walk to
several hundred thousand jobs without touching a train.

**And the budget that survived is itself a truncation — the same mistake, one
level up, which took until 2026-10-11 to notice.** The paragraph above catches
a cap on the *walk* and congratulates itself; the cap on the *trip* was sitting
right beside it doing a milder version of the same thing. A 45-minute
cumulative-opportunity cutoff right-censors: 1,062 of 2,325 tracts cannot
reach a tenth of the city's jobs inside it, so they saturate near zero and stop
being distinguishable from each other — and they are precisely the tracts
this map exists to find. The fix is the same shape as the one above: stop
truncating. `C.JOBS_THRESHOLD_SHARE` swaps the axes (fix the jobs, measure the
time), ships as `jobs_time` / `jt10`, and is the primary accessibility measure
now. `JOBS_BUDGET_MINUTES` stays, because cumulative opportunity at a fixed
budget is what every peer analysis publishes and it answers a real question
about opportunity *available*; it is no longer the number quoted about the
worst-served places. `config.py` has both arguments and the measurements.

The lesson generalizes past this field and is why both halves are written down:
a threshold on the *axis you are measuring along* truncates, and a threshold on
the *axis you are measuring* rescales. The walk cap and the trip budget were
the first kind. The jobs share is the second, which is why its exact value
turns out not to matter — Spearman 0.968 to 0.991 from 2% to 25%.

Access and egress both read the same `W[station, tract]` matrix — the walk
graph is undirected, so one bounded pass per station serves both ends, and the
measure on the two ends of a trip is identical. It is a per-tract median over
the same 25 m sample grid everything else uses, which makes it
`min_station(median_sample)` where stage 4 publishes
`median_sample(min_station)`. Those are not the same number; measured, the
difference is +0.25 min at the median and within 2 minutes for 96.6% of
tracts, and it leans conservative.

**Payload budgets are asserted, not advisory, and they are measured gzipped.**
`BUDGET_TRACTS_WIRE_KB = 420` raises a `SystemExit` in `build.py` and
`BUDGET_WALKSHEDS_WIRE_KB = 150` does the same in `surface.py`, both against
`config.wire_kb()` — gzip level 6, which is what a CDN serves. The raw size is
printed and ignored because the wire size is what a reader downloads, not
because the two diverge: measured at 24.4 KB raw and 5.1 KB gzipped per new
numeric field, they move together at a constant ~5×. (An earlier comment
claimed 0.3 KB gzipped; that was a measurement error, corrected in
`config.py`.)

Headroom is finite, and Phase 4 spent all of the old one: 305 KB → **343 KB
against a 350 KB budget**, which left room for no further field at all.
Phase 4's three fields cost 51 KB in this file against 19 KB measured
standalone, which is the interleaving tax made concrete — a column costs
roughly **2.5x more as a per-feature property than as a contiguous array**,
because gzip's window cannot span 2,325 features. `hubs.json` carries eight
hub columns as parallel arrays aligned to an explicit `geoids` list for 39 KB,
where the same numbers interleaved would have been about 100.
`app.js:attachHubs()` joins them onto the features **before** `addSource`,
which is mandatory and not stylistic (see the black-map note below).

**The budget was raised to 420 on 2026-10-11, deliberately and once**, and the
reasoning matters more than the number. The alternative was to do the
columnar-sidecar refactor immediately, which is the better answer and is
written down in `ROADMAP.md` — but doing it in the same commit as the fields
that provoked it is how a measured lever turns into an unreviewable diff. 420
is the then-current 345 KB plus roughly five fields of measured headroom. The
number to watch is not this one; it is **whether the next field arrives as a
property or as an array**. A second raise without the refactor is the thing
this note exists to make embarrassing.

**The loan was repaid on 2026-10-12, and the split is now the rule rather
than the plan.** `tracts.geojson` carries geometry and `geoid` and nothing
else; every column ships as a parallel array in `tract_cols.json`, aligned to
an explicit `geoids` list, and `app.js:attachCols()` joins them onto the
features **before** `addSource` — the same mandatory ordering `attachHubs`
has, enforced by the same freeze. Measured: 350 KB interleaved became 216 KB
of geometry plus 76 KB of 28 columns, **292 KB for the pair**, and the
step-free field that provoked the refactor cost **2 KB** where it would have
cost about 5 as a property. So `BUDGET_TRACTS_WIRE_KB` now asserts against
the **sum of both files**, because a column moving between them saves a
reader nothing, and what the budget guards from here is geometry fidelity
rather than field count — the headroom is roughly sixty columns.

Two things fell out of proving the move changed no value, both worth keeping.
Comparing the old properties against the sidecar per geoid found `det` had
been shipping as `1.1900001` on 89 tracts: a float32 column rounded in
float32, which `.round(2)` cannot fix, so every rounded column now casts to
float64 first through one `rounded()` helper. And the sidecar is **not
optional the way `hubs.json` is** — with no columns there is nothing to
paint, no table to rank and no tooltip to show — so it is preloaded in
`index.html`, required by `publish.sh`, and a partial join reports itself in
the page instead of leaving the loading overlay up.

**A new float field must join the rounding tuple in `build.py`**:
unrounded float64 costs 15 KB gzipped instead of 5, and four such fields ate
most of the budget in one commit. The surface PNG budget stays raw and warns
rather than raising, because a PNG is already compressed. Raising a budget is a
deliberate decision to state, not a way past a failing build. `build.py` also
interns six repeated string columns into `stats.json` lookups, which is what
buys the geometry fidelity — a new string field should usually join `INTERNED`.

### Fetch-stage conventions worth not breaking

Three cache layers: a per-source freshness window (`CACHE_TTL_DAYS`), then a
conditional request, then stale-on-failure. **Exactly one validator is sent, and
`Last-Modified` is preferred** — this looks like an omission and is not. RFC 9110
requires a server receiving `If-None-Match` to ignore `If-Modified-Since`, and
Socrata's ETag varies with content-encoding, so sending both reliably produces a
full 200 instead of a 304. Measured; `README.md` has the table.

**The under-service score was chosen by measurement, and the measurement is a
gate.** `score = pop × max(walk_min − 10, 0)` — excess person-minutes, in real
units, with one research-anchored constant (`C.ACCESS_FLOOR_MINUTES`, where the
half-mile standard and El-Geneidy et al.'s observed 85th-percentile metro walk
agree). It replaced a product of percentile ranks, which `verify.py` measures at
20% against a 26% baseline — indistinguishable from random. Ten is a **floor,
not a cap**: capping was measured and fails, because the cap is the point above
which everything ties and ties break on density alone.

`verify.alignment()` ranks every candidate score by how many of its worst 25
tracts fall inside the eight corridors agencies have actually proposed. Two
results there are counter-intuitive and both are load-bearing, so don't
"improve" the score past them without re-running it:

- **Adding the wait penalty lowers alignment** (80% → 76% at worst-25). Access
  cost is the better measure of a rider's morning and the worse predictor of
  where an agency builds — a frequency problem is solved by more trains on
  existing track, not a new line.
- **Adding the equity variables lowers it too**, in every form tried: as rates,
  as counts, in a product, in a weighted blend (car-free households alone,
  56%). They ship and are weightable in the UI because the MTA defines its own
  Equity Areas by exactly the zero-vehicle variable, but the default weight is
  zero and the measurement is why.
- **Phase 4 lowers it further, and the prediction was on record in
  `ROADMAP.md` before anything was computed.** Jobs accessibility and the
  detour factor both come in at or *below* the 26% baseline: `pop x jobs out
  of reach` 16%, `pop x detour` 0%, against 80% for the shipped score. Hub
  minutes do better — `pop x excess hub min` 56% — but still lose.
  **The mechanism is diagnosable and is the useful part:** alignment tracks
  each factor's dynamic range exactly, and monotonically. Coefficient of
  variation, with the Spearman correlation between `pop x factor` and `pop`
  alone, and the clumped hit rate beside the per-tract one:

  | factor | CoV | rho vs pop | worst-25 | clumped |
  |---|---|---|---|---|
  | excess walk min (shipped) | 2.24 | -0.15 | 80% | 52% |
  | excess hub min | 1.34 | -0.05 | 56% | 54% |
  | hub minutes | 0.50 | 0.61 | 48% | 44% |
  | jobs out of reach | 0.33 | 0.71 | 16% | 29% |
  | detour factor | 0.19 | 0.88 | 0% | 3% |
  | *time to a tenth of the jobs* | *0.48* | *0.71* | *56%* | *40%* |

  A narrow-range factor multiplied by population *is* a population ranking,
  and the city's most populous tracts are its well-served cores, which no
  agency is proposing to serve again. So the shipped score keeps working
  because excess walk minutes span 0 to 100+, not because walking matters
  more than travel time. The detour factor at CoV 0.19 is near-constant
  citywide, which is itself the answer to the question the layer was built to
  ask — see the pre-registered ring test in `ROADMAP.md`.

  Two honest wrinkles in that table. The CoV ordering survives the switch to
  clumps and the rank ordering mostly does, but **`excess hub min` edges ahead
  of the shipped score once the singletons are dropped** (54% against 52%),
  which the per-tract figures do not show. That is one block's worth of
  difference and not a reason to change the score, but it is the kind of thing
  the clumped measure exists to surface and should be re-read whenever either
  number moves. And the detour row moved with the ferry edge: it read CoV
  0.18 and 4% on the rail-only graph, because Staten Island's inter-borough
  pairs were unreachable and therefore excluded from its own median.

**The `jobs out of reach` row is a warning about measures, not about jobs, and
the Phase 4 write-up got the diagnosis wrong.** Corrected 2026-10-11. That row
sits at 16%, *below* the 26% random baseline, and Phase 4 recorded it as "jobs
accessibility is a weak predictor of where agencies build". The last row of the
table is the same jobs data with its axes swapped — fix the jobs, measure the
time — and it hits **56%**. So job accessibility is not the weak part. **The
cutoff is.**

A fixed time budget *right-censors*: every geography that cannot reach much of
anything inside it saturates near zero and stops being ordered. Measured, 1,062
of 2,325 tracts — 46% of the city — need longer than 45 minutes to reach a
tenth of the city's jobs, so for nearly half the map `j45` reports the censored
tail rather than the trip. Breezy Point reads 0.13% and Arden Heights 0.12%,
which looks like a tie and hides 86 minutes against 121. And the budget sits at
the *median* of the quantity it thresholds (the median time to a tenth of the
jobs is 42.6 min), which is the worst available place to cut when the far side
is the subject.

The dynamic-range rule the phase inferred is **not** weakened by this — it is
confirmed by it, which is the useful part. Uncensoring widened the factor's CoV
from 0.33 to 0.48 and alignment followed, exactly as the rule predicts. The
correction is to the sentence "this factor has a narrow range", which was true
of the *field* and not of the thing it was measuring. The general lesson, worth
more than the specific fix: **before concluding a factor is weak, check whether
the measure of it is censored.** A ceiling in a field looks exactly like a
narrow distribution in the world.

`time to jobs alone` is the one candidate in the table with no population term
at all, and it scores 60% at worst-25, **62% at worst-50 — the highest of any
candidate** — and 51% clumped, against the shipped score's 52%. Its Spearman
against population is −0.22, so it is not a population ranking in disguise,
and its worst 100 form only 7 blocks, the largest holding 69 tracts, the most
spatially coherent of anything tested. None of that makes it the score: it says
nothing about how many people are affected, which is what `pop ×` is for. It
does mean a pure travel-time measure matches a population-weighted walk measure
on this test, which was not expected and is worth not forgetting.

`SHIPPED_SCORE` in `verify.py` names the candidate that `aggregate.add_score`
implements; if one changes without the other the gate silently measures the
wrong thing.

**What gates the build is the *clumped* hit rate, not the per-tract one, and
the floor is derived rather than chosen.** Added 2026-10-11. The per-tract
measure compares a point measure against corridors that are lines, so a score
that scatters its worst tracts across five boroughs can tie one that puts them
all in a single band — and only the second describes something a line could
fix. Phase 4 made that overdue rather than untidy: sixteen candidates are
compared this way and six arrived in one commit. So `alignment()` also takes
each score's worst `CLUSTER_PROBE_K` (100) tracts, groups them with the same
land adjacency `add_clusters` uses, keeps the blocks of `CLUSTER_MIN_TRACTS`
(3) or more, and measures the hit rate over **those tracts only** — the same
statistic against the same 26% baseline, with the singletons dropped. Measured
for the shipped score: 80% at worst-25 per tract, 43% at worst-100 per tract,
and **52% clumped** over 8 blocks covering 77 of the worst 100. Clumping
*raises* agreement, which is the direction it should move if the scattered
tracts were noise.

The gate is `baseline + ALIGNMENT_CLUSTER_MARGIN` (26% + 10 = 36%), computed
at runtime, and **never** the shipped score's own number. That is deliberate
and is the whole design: official plans are need filtered through cost,
politics, and whether an abandoned right-of-way is lying around, so a floor set
near where the score already sits would quietly make agreement with the MTA a
target and import all of it. The gate answers "is this score noise?" and
refuses to answer "does it agree with the agencies?". `ALIGNMENT_MIN_PRECISION`
(40%) survives as the per-tract number, reported and used to pick the
"credible" set for the robustness intersection, but it no longer fails a build.

**The disagreement table is the output worth reading.** `alignment()` prints
and ships (`stats.alignment.disagree`) every block with its corridor overlap,
because a block the score ranks badly that nobody has proposed anything for is
the finding, not the error. Measured: of the shipped score's 8 blocks, Staten
Island's 40-tract block overlaps 88% and Marine Park-Mill Basin 100%, while
**five blocks overlap 0–33%, and four of those five are northeastern
Queens** —
College Point/Whitestone/Bay Terrace (11 tracts, 59,639 people), Pomonok,
Oakland Gardens, Glen Oaks/Douglaston/Bellerose — plus Throgs Neck. That
pattern is consistent with the constraint the test already warns about: the
plans cluster on existing rights-of-way, and northeastern Queens has none.

**Digitizing the eight corridors as geometry is deliberately not done.** Four
follow rights-of-way already in the OSM extract (the Bay Ridge Branch, the
Rockaway Beach Branch, the North Shore right-of-way, the Hell Gate Line) and
four have no published alignment at all, so half the work would be drawing
lines by hand — putting the author's judgment back inside the one test built
to keep it out. If it ever happens it belongs with the rail-lines work, where
the first four arrive free.

**Contiguity is reported beside the per-tract score**, because a line serves a
corridor. Connected components over land adjacency — the DCP polygons exclude
water, so tracts across the East River don't touch, and no bridge edges are
added. The result is the headline no per-tract ranking can produce: **430
contiguous tracts holding 1.31M people** past the standard, Canarsie through
Flatlands to St. Albans, while 24 of the 58 blocks are a single isolated tract.

## Verification

**There is no unit test suite.** `pipeline/verify.py` is the correctness gate and
it tests the thing most likely to be silently wrong: whether the pipeline is
actually routing. It compares network distance against straight-line distance at
known probes and requires the **barrier probes** (Newtown Creek, Hunts Point,
Broad Channel, Coney Island Creek) to show an inflated detour factor. A
pipeline using straight-line distance would pass every other check and fail this
one.

For a refactor, the expected standard in this repo is a **byte-identical
proof**: force a full recompute through every changed call site and confirm
`node_distance.npz`, `tracts.geojson`, `stations.geojson`, `walksheds.geojson`,
the surface PNG and `palette.json` reproduce exactly. The outputs are
deterministic, so anything less is a weaker claim than the repo already makes in
its own commit messages.

Stage 5 prints three more cross-checks, and these **warn rather than fail** —
read the output, don't assume a zero exit means they passed: unmatched DCP
geoids (filled with 0 population), a total-population band of 8.0-9.5M, and the
half-mile subway share against published figures (62.2% network vs 71.6%
straight-line, a 9.5-point routing penalty that is itself a result worth
quoting). `palette.py`'s gates, by contrast, are hard `assert`s.

## Conventions

- **US English throughout**, in code, comments, docs and commit messages. A past
  commit converted the whole history; don't reintroduce `colour`, `centre`,
  `optimise` and friends.
- **Commit messages explain the reasoning**, including what was tried and
  rejected and what the measurement was. They are long on purpose and are the
  project's design record alongside `ROADMAP.md`.
- `LICENSE` is verbatim AGPL-3.0 and is excluded from every text transform.
  `config.SOURCE_URL` exists because AGPL section 13 requires a deployed
  instance to offer its source; it reaches the browser through `stats.json`.
- `.env` holds `CENSUS_API_KEY` and is gitignored. Stage 1 fails with a pointer
  to the free signup URL if it is missing. The Census API returns the
  "Missing Key" page with **HTTP 200**, so it has to be detected by content.
