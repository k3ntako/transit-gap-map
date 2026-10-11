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

## Commands

```bash
make setup        # .venv (Python 3.14) + requirements; then cp .env.example .env
make all          # fetch -> network -> service -> distance -> palette -> surface -> build -> verify
make serve        # static server on :8000 (PORT=xxxx to change)
make verify       # the correctness gate
make clean        # wipe data/processed and web/data
```

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

**Baseline is 38 errors, 0 warnings.** All 38 are third-party stub gaps, not
defects — pyrosm typed as returning `Optional`, `scipy.spatial.cKDTree` absent
from the stubs, shapely's `BaseGeometry.geoms`, `Series.map` with a dict. Treat
39 as a regression; they want casts, not fixes. `pyrightconfig.json` also has
the unused-code rules turned on, but note the limit: pyright only reports unused
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
| 2 | `network.py` | `walk_graph.npz` — undirected CSR adjacency + node coords |
| 3 | `service.py` | `station_service.parquet` + `bus_stop_service.parquet` — trains and buses per hour, from GTFS |
| 4 | `distance.py` | `node_distance.npz` — nearest-station distance per node |
| 5 | `aggregate.py` | `tract_stats.parquet` — per-geography statistics |
| 6 | `palette.py` | `web/data/palette.json` |
| 7 | `surface.py` | walk-time raster PNGs + `walksheds.geojson` |
| 8 | `build.py` | `tracts.geojson`, `stations.geojson`, `bus_ftn.geojson`, `stats.json` |
| — | `verify.py` | nothing; checks stage 4 |

Stage 3 depends only on stage 1, not on the walk graph, so it can be re-run
without touching the expensive parse.

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
by borough despite their names: they share one 307-route `routes.txt` and
partition *trips* (zero `trip_id` overlaps), while stops do overlap and must
be deduplicated after the union. `stop_times.txt` across the six is ~10M rows,
so it is read in chunks and window-filtered inside the loop.

**Tracts are measured by a grid, not a centroid** — a 25 m sample grid clipped
to each polygon, reduced with the median, because NYC tracts are long and
irregular and a centroid often lands in water or a park. The spacing is
converged, not chosen: the per-tract median moves 0.05 min between 25 m and a
15 m reference run, so finer is measurement noise. `config.py` carries the
numbers. Every meter-valued tunable is named `*_METERS`, spelled out, because
`_M` beside `HALF_MILE` could be read as miles.

**`palette.py` is the only place colors are defined.** Both the raster in stage 7
and the browser read `palette.json`, so there is exactly one definition of every
color. The bivariate matrix is an **OKLCH field** (`FIELD` + `bivariate_field()`),
not a four-corner bilinear blend — that was tried and provably cannot put the
chroma on the finding. `audit()` enforces perceptual-separation `GATES` grouped
by the reader's task (populated cells held to ΔE 15, the sparse row to 8); it
raises rather than warns. Load the **dataviz skill** before touching any of this.

**The web map is static and dumb on purpose.** `web/` is three source files —
`index.html`, `app.js`, `style.css` — reading the payloads in `web/data/`
(`tracts`, `stations` and `walksheds` GeoJSON, `stats.json`, `palette.json`,
`walk_surface.json` and one of the two surface PNGs). `web/data/` is gitignored
and entirely generated, so a fresh clone must `make all` before `make serve`.
Anything a user might want to re-weight should recompute in the browser from
per-tract fields rather than round-tripping through the pipeline.

**Payload budgets are asserted, not advisory, and they are measured gzipped.**
`BUDGET_TRACTS_WIRE_KB = 350` raises a `SystemExit` in `build.py` and
`BUDGET_WALKSHEDS_WIRE_KB = 150` does the same in `surface.py`, both against
`config.wire_kb()` — gzip level 6, which is what a CDN serves. The raw size is
printed and ignored because the wire size is what a reader downloads, not
because the two diverge: measured at 24.4 KB raw and 5.1 KB gzipped per new
numeric field, they move together at a constant ~5×. (An earlier comment
claimed 0.3 KB gzipped; that was a measurement error, corrected in
`config.py`.) Headroom is finite — 305 KB against 350 is about nine more
fields. **A new float field must join the rounding tuple in `build.py`**:
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
tracts fall inside the eight corridors agencies have actually proposed, and
`main()` fails the build if the shipped score drops below
`ALIGNMENT_MIN_PRECISION`. Two results there are counter-intuitive and both are
load-bearing, so don't "improve" the score past them without re-running it:

- **Adding the wait penalty lowers alignment** (80% → 76% at worst-25). Access
  cost is the better measure of a rider's morning and the worse predictor of
  where an agency builds — a frequency problem is solved by more trains on
  existing track, not a new line.
- **Adding the equity variables lowers it too**, in every form tried: as rates,
  as counts, in a product, in a weighted blend (car-free households alone,
  56%). They ship and are weightable in the UI because the MTA defines its own
  Equity Areas by exactly the zero-vehicle variable, but the default weight is
  zero and the measurement is why.

`SHIPPED_SCORE` in `verify.py` names the candidate that `aggregate.add_score`
implements; if one changes without the other the gate silently measures the
wrong thing.

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
both surface PNGs and `palette.json` reproduce exactly. The outputs are
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
