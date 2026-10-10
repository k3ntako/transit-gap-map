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
make all          # fetch -> network -> distance -> palette -> surface -> build -> verify
make serve        # static server on :8000 (PORT=xxxx to change)
make verify       # the correctness gate
make clean        # wipe data/processed and web/data
```

Single stage, which is the normal development loop:

```bash
.venv/bin/python -m pipeline.<stage> [--force]
```

`--force` means "recompute, ignore the cached intermediate" and exists on
`fetch`, `network`, `distance`, `aggregate`, `surface` and `build`. Without it a
stage reuses `data/processed/`, so an edit to stage 3 will *not* show up
downstream unless the later stages are forced.

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

Seven stages, each a module under `pipeline/` runnable on its own, each writing
a cached artifact that the next one reads. The cache boundaries are the design:
stage 2 takes ~4-5 minutes and stages 3-7 take seconds, so iteration happens
downstream of the expensive parse.

| Stage | Module | Writes |
|---|---|---|
| 1 | `fetch.py` | `data/raw/` — stations, boundaries, census, 153 MB OSM extract |
| 2 | `network.py` | `walk_graph.npz` — undirected CSR adjacency + node coords |
| 3 | `distance.py` | `node_distance.npz` — nearest-station distance per node |
| 4 | `aggregate.py` | `tract_stats.parquet` — per-geography statistics |
| 5 | `palette.py` | `web/data/palette.json` |
| 6 | `surface.py` | walk-time raster PNGs + `walksheds.geojson` |
| 7 | `build.py` | `tracts.geojson`, `stations.geojson`, `stats.json` |
| — | `verify.py` | nothing; checks stage 3 |

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
walkable link to the other boroughs, so stage 3 works per connected component
and publishes a `serviceable` mask: nodes in a component that actually contains a
station. Stage 4 snaps only to those, which stops a sample point attaching to an
isolated park path and being reported as having no transit access at all. Keeping
only the largest component would drop ~48% of nodes.

**Tracts are measured by a grid, not a centroid** — a ~75 m sample grid clipped
to each polygon, reduced with the median, because NYC tracts are long and
irregular and a centroid often lands in water or a park.

**`palette.py` is the only place colors are defined.** Both the raster in stage 6
and the browser read `palette.json`, so there is exactly one definition of every
color. The bivariate matrix is an **OKLCH field** (`FIELD` + `bivariate_field()`),
not a four-corner bilinear blend — that was tried and provably cannot put the
chroma on the finding. `audit()` enforces perceptual-separation `GATES` grouped
by the reader's task (populated cells held to ΔE 15, the sparse row to 8); it
raises rather than warns. Load the **dataviz skill** before touching any of this.

**The web map is static and dumb on purpose.** `web/` is four files reading four
payloads. `web/data/` is gitignored and entirely generated, so a fresh clone must
`make all` before `make serve`. Anything a user might want to re-weight should
recompute in the browser from per-tract fields rather than round-tripping through
the pipeline.

**Payload budgets are asserted, not advisory.** `BUDGET_TRACTS_MB = 2.0` raises a
`SystemExit` in `build.py`; the surface PNG budget warns. Raising a budget is a
deliberate decision to state, not a way past a failing build. `build.py` also
interns five repeated string columns into `stats.json` lookups, which is what
buys the geometry fidelity — a new string field should usually join `INTERNED`.

### Fetch-stage conventions worth not breaking

Three cache layers: a per-source freshness window (`CACHE_TTL_DAYS`), then a
conditional request, then stale-on-failure. **Exactly one validator is sent, and
`Last-Modified` is preferred** — this looks like an omission and is not. RFC 9110
requires a server receiving `If-None-Match` to ignore `If-Modified-Since`, and
Socrata's ETag varies with content-encoding, so sending both reliably produces a
full 200 instead of a 304. Measured; `README.md` has the table.

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

Stage 4 prints three more cross-checks, and these **warn rather than fail** —
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
