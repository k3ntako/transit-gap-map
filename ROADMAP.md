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
| **Access cost** | How hard is it to get to a platform? | Partial — distance to a station *point*, on foot, ignoring stairs, slope and which door |
| **Service quality** | Once you are there, what do you get? | **Missing** — a Staten Island Railway stop with a 30-minute headway counts the same as Times Square |
| **Dependence** | How much do the people there need it? | Partial — population density only |

A station is not a binary, and a person near a bad station can be worse off than
a person a bit further from a good one. Most of the value left on the table is in
the second row, and the cheapest way to get it is GTFS.

Two design principles follow:

1. **Ship raw normalized metrics, score in the browser.** Anything the user
   might want to re-weight — density floors, score weights, which metrics
   count — should recompute client-side from per-tract fields, with no pipeline
   round trip. Only things that need the walk graph or a transit graph get
   pre-computed.
2. **Percentile-rank before combining.** NYC's distributions are heavy-tailed
   (density is roughly log-normal, walk time has a long right tail, income more
   so). A weighted sum of raw values is dominated by whichever metric has the
   fattest tail. Rank each metric to 0–1 within the city first, then weight.

---

## Phase 0 — known debt

**Done 2026-10-10**, except the last item, which is blocked.

- ~~Rebalance the palette's chroma geometry.~~ **Done.** The matrix was
  shouting its least interesting cell: sparse+far (491 tracts of largely empty
  land) carried the highest chroma in the matrix at 0.175, while dense+far —
  51 tracts, the finding the map exists to show — sat at 0.073. The cause was
  specific: the high-high corner was built by blending `BLUE[650]` with the
  warm ramp, and averaging opposing hues in OKLab cancels `a` and `b`, so no
  chroma boost afterwards recovers it.

  Fixed by replacing the four-corner bilinear blend with an OKLCH field,
  because bilinear provably cannot do this: the sparse+far cell *is* a corner
  anchor, so quieting it means desaturating that anchor, which shrinks the
  whole matrix's gamut and drags the center cell toward its neighbors. The
  measured ceiling for bilinear is **14.2** among the populated cells — under
  the 15 floor — across 816k corner configurations searched. The field encodes
  the semantics instead: lightness moves with both variables, blue chroma rides
  the density axis, and warm chroma rides the walk-time axis **scaled by
  density**, so distance from a train only shouts where there are people to be
  stranded.

  | | before | after |
  |---|---|---|
  | chroma, dense+far (the finding) | 0.073 | **0.125** |
  | chroma, sparse+far (491 tracts) | 0.175 | **0.055** |
  | ratio | 0.42× | **2.26×** (3.08× dark) |
  | weakest pair among populated cells | — | 15.4 |
  | headline corner | 15.1 | 15.5 |

  The audit gates were regrouped by the reader's task at the same time. Holding
  all 36 pairs to one floor was the wrong test: resolving walk-time terciles
  *inside* the low-density row is not something this map supports, since those
  tracts are mostly airports, marshland and empty hillside and the whole point
  is that they recede. That row is now held to 8 and stays ordered by
  lightness; the populated cells keep the full 15.

- ~~Gray out the 82 unpopulated tracts.~~ **Already implemented — this roadmap
  item was wrong.** All 82 carry `bi/dc/wc == -1`, and `fillColor()`'s `match`
  fallback already paints them with `palette.unpopulated`. Verified against the
  built payload: the set `pop == 0 and bi != -1` is empty. So the red mass was
  never unpopulated land — it was 491 *populated* low-density tracts, which is
  what the palette work above addresses.

- ~~Fix `load_graph`'s union return.~~ **Done** — now a `WalkGraph` NamedTuple.
  The `with_components` flag was redundant (`save_graph` always writes the
  labels) and chose the return *shape* from an argument's value, which no type
  checker can narrow. pyright 43 → 38 errors, no new ones; the remainder are
  stub gaps rather than defects (pyrosm typed as returning `Optional`,
  `scipy.spatial.cKDTree` missing from the stubs, `shapely`
  `BaseGeometry.geoms`, `Series.map` with a dict), so they want casts, not
  fixes. Behavior-preserving: a forced recompute through every changed call
  site reproduces `node_distance.npz`, `tracts.geojson`, `stations.geojson`,
  `walksheds.geojson`, both surface PNGs and `palette.json` byte-identically.

- **Set `config.SOURCE_URL`** — **still open**, blocked on the repo having a
  public remote. Until then the map says "source in this distribution", which
  is AGPL-compliant for a local run.

## Phase 1 — legibility and configurability (no new data)

Everything here reads fields that are already in `tracts.geojson`.

- **Density floor.** A slider in a settings panel: hide tracts below *N* people
  per km². This is the single cheapest high-value change — most of the visual
  noise is physically huge, nearly empty tracts. Two notes:
  - Fade filtered tracts to ~12% opacity rather than removing them. A choropleth
    with holes in it reads as missing data, not as "excluded".
  - Offer a *population* floor too (`pop < 500`), which catches small odd tracts
    that a density threshold misses.
- **Settings panel.** The existing `#controls` panel has room; add a
  `<details>` block for thresholds and weights so the default view stays clean.
- **A single sequential "under-served" layer.** See Phase 3 — but note here that
  one ordered ramp of 7–9 steps is *far* easier to read than any matrix, and it
  is the right home for a score. Keep the bivariate matrix as the secondary
  "explain why" view.
- **Permalink state.** Encode layer, weights and thresholds in the URL hash so a
  configuration is shareable. Cheap, and it makes every later feature citable.

### On 16 or 25 color classes

**Measured 2026-10-10: a 4×4 matrix scores 10.0 on its weakest pair, against
15.1 for the 3×3 it would replace.** That is below the floor, so 16 classes
need either a wider lightness range or a lower gate — a deliberate trade rather
than a surprise. `bivariate_field()` takes `levels`, so re-measuring after any
palette change is a one-line call.

The rest of the reasoning stands:

- A 4×4 bivariate matrix is defensible *if* the gate is renegotiated. **5×5 is
  not** — nobody decodes 25 cells against a legend, and the per-cell perceptual
  separation falls below the point where two adjacent classes are
  distinguishable at choropleth sizes.
- More classes will not fix the problem you actually saw. "Hard to tell between
  neighborhoods because a lot of parts are red" is the chroma-geometry bug in
  Phase 0, not a resolution limit. Sixteen classes on the current corner
  geometry will make it *worse*, because the same perceptual volume gets divided
  into more pieces.
- The better route to more resolution is a *score* on one axis with more steps,
  which is Phase 3. One ordered ramp carries 7–9 readable steps; a matrix does
  not.

## Phase 2 — service quality from GTFS

The highest value per unit of effort in the whole roadmap, and it needs no
routing engine — just a join and a group-by.

- **Trains per hour per station.** `gtfs_subway.zip` is 5.6 MB and the station
  table already carries `gtfs_stop_id`, so this is a key join, not fuzzy name
  matching. From `stop_times` × `trips` × `calendar`, count scheduled trips in a
  window and attach to each station:
  - weekday AM peak (8–9am) — the headline number
  - midday, evening, Saturday
  - **3am** — the subway runs 24/7 but service collapses overnight and most
    buses stop entirely. A night-service map is genuinely under-reported and
    costs nothing extra once the loader exists.
- **Span of service.** First and last train, per station, per day type. A stop
  with no service after 11pm is a different kind of under-served.
- **Weight the station set by frequency.** This is the conceptual change: the
  Dijkstra pass in `pipeline/distance.py` currently treats all 557 stations as
  equal sources. Add a frequency-derived wait penalty to each station's virtual
  source cost (`headway / 2` is the standard expected-wait approximation for
  turn-up-and-go service, capped for long headways where people time their
  arrival). The existing architecture already supports this — it is exactly what
  the snap-offset weighting does — so the change is a few lines plus the GTFS
  loader.
- **Commuter rail honestly.** LIRR (1.8 MB) and MNR (4.4 MB) feeds. Many NYC
  stations on these lines run hourly off-peak and cost a separate fare. The
  frequency penalty handles the first; a fare flag should mark the second,
  because counting a $10 peak LIRR ticket as equivalent to a subway swipe
  overstates access in eastern Queens and the north Bronx substantially.

### Buses

Your instinct about a penalty is right, but bus *proximity* is the wrong metric.
There are roughly 15,000 bus stops in the city — a bus-stop walkshed layer would
cover nearly everything and tell you nothing. The informative versions:

- **Frequent Transit Network layer.** Show only stops whose peak headway is ≤10
  minutes. That map has real structure, and it is the standard planning
  definition of useful service.
- **Buses in the time-to-hub graph (Phase 4), with a penalty.** Slower, less
  reliable, more exposed to traffic. Model it as a wait multiplier and a
  schedule-adherence factor rather than a flat dislike term, and make the
  multiplier a visible, documented parameter rather than a hidden constant.
- Six bus feeds, ~62 MB total (`gtfs_b` 19.6 MB, `gtfs_bx` 9.6 MB, `gtfs_busco`
  9.6 MB, `gtfs_m` 9.5 MB, `gtfs_si` 7.5 MB, `gtfs_q` 6.8 MB). Budget a cache
  stage; the existing conditional-GET cache in `pipeline/fetch.py` handles it.

## Phase 3 — the under-service score

With Phase 2 done there is something worth scoring.

```
need      = rank(density) ⊕ rank(zero-car households) ⊕ rank(poverty) ...
deficit   = rank(walk time) ⊕ rank(wait penalty) ⊕ rank(time to hub) ...
score     = need × deficit          (or w·need + (1−w)·deficit)
```

Design notes that matter more than the formula:

- **Multiplicative beats additive** for this question. A sum gives a high score
  to empty land with terrible access; a product requires *both* need and
  deficit to be present, which is what "under-served" means. Offer both, default
  to the product.
- **Weights live in the UI.** Sliders, recomputed client-side, with the top-20
  table re-ranking live and the weights in the permalink. This turns the map from
  an assertion into an instrument — anyone who disagrees with the weighting can
  move it and see.
- **Show the decomposition.** The tooltip should say *why* a tract scores high
  (e.g. "92nd percentile need, 88th percentile deficit; driven by wait time").
  A score nobody can interrogate does not persuade anyone.
- **Publish the sensitivity.** Which tracts stay in the worst 50 across a wide
  range of weightings? Those are the robust findings; the rest are artifacts of
  the weighting. This is the single most credibility-building thing in the
  roadmap.

## Phase 4 — travel time to a hub

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

## Phase 6 — access cost, done properly

- **Step-free access.** 2,120 subway entrances exist; **116 are step-free**
  (102 elevators, 10 ramps, 4 combinations). Recomputing the entire map with the
  station set filtered to ADA-accessible stations produces a dramatically
  different and genuinely under-reported picture — and it costs almost nothing,
  because it is the same pipeline with a filtered source set. The station table
  already carries `ada`, `ada_northbound`, `ada_southbound`. High impact,
  low effort; arguably belongs in Phase 1.
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
- **Proposed-project toggles.** Interborough Express along the Bay Ridge Branch,
  Second Avenue Subway Phase 2, Triboro proposals. Each is a station list and an
  alignment; the pipeline recomputes with them added, and the map shows the
  before/after population moved inside each threshold. This directly answers
  "does the IBX fix the Brooklyn–Queens gap this map shows?"
- **Click to place a station.** The live version of the above. Needs the graph in
  the browser (too big) or a small local endpoint — so the pre-computed marginal
  surface is the better default, with the interactive version as a `make serve`
  extra rather than a feature of the static deployment.

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
- **Crowding, maybe.** Hourly ridership (`wujg-7c2s` through 2024, `5wq4-mkjj`
  for 2025+) gives entries per station per hour. "Far from a station *and* the
  nearest one is already at capacity" is a distinct and worse condition. Large
  dataset; needs a server-side aggregation step. Lower priority than it sounds.

---

## Suggested order

Phase 0 is done; the density floor is next — hours of work, and it changes what
the map looks like immediately. Then step-free access, which is the biggest
finding available for the least new machinery. Then Phase 2 (GTFS frequency),
because it is what makes a score meaningful. Then the score. Then time-to-hub,
which is a project rather than a feature.

Deliberately *not* first: more color classes, which solves a problem the map
does not have, and bus proximity, which would add 62 MB of data to draw a layer
that covers the whole city.

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
| ACS variables | `api.census.gov/data/2023/acs/acs5` | `B08201_002E`, `B19013_001E`, `B17001_002E`, `B08301_010E`, `B18101`, `B01001` all confirmed |

The subway station table already carries `gtfs_stop_id`, so every GTFS join is a
key join. It also carries `structure` (elevated / subway / open cut) and a `cbd`
flag, neither of which the map currently uses.
