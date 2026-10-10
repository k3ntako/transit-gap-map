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

## Phase 1 — legibility and configurability (no new data)

Everything here reads fields that are already in `tracts.geojson`.

**Shipped 2026-10-10, in full** — display floors with a settings panel, the
station hover, the class named in words with its legend cell cross-highlighted,
the camera constraints, the permalink, and the walk-access gap layer. Findings
worth keeping:

- `maxBounds` does not merely clamp the center. When the bounds are narrower
  than the viewport, MapLibre zooms *in* until they fill it, so bounds drawn
  tight around the city raise the effective minimum zoom until the city no
  longer fits a small window. `MAX_BOUNDS` is therefore much wider than
  `HOME_BOUNDS`, and wider east-west than north-south because windows are
  landscape and NYC is not. Re-tune the two together, and check the fit at
  ~900 px wide, where the panels take two thirds of the width.
- The station hit target is an invisible circle beneath the visible dots at
  roughly twice the drawn radius. Larger is tempting and wrong: Manhattan
  subway stops are ~15 px apart at the opening zoom, so a generous pad makes
  the island unhoverable as a choropleth. MapLibre's circle hit test reads only
  `circle-radius` and `circle-stroke-width`, never opacity, which is what makes
  an invisible layer work at all.
- The opening view is now `fitBounds` on `HOME_BOUNDS` rather than a fixed
  center and zoom, so the city frames itself on any window and the recenter
  button has one definition to return to.
- The permalink writes only what differs from the default, so a link reads as a
  list of deliberate choices and a later control is one more key — there is no
  format to version. It is written with `history.replaceState`, because
  assigning `location.hash` pushes a history entry on every pan and turns the
  back button into a scrub bar. A hash the *reader* changes reloads the page
  rather than re-syncing every control in place.
- The gap layer is **not** called "under-served", and should not be until
  Phase 2 lands. Two of the three axes in the reframe above are still missing,
  so what it measures is a crowd with a long walk. The name is the only part of
  this that would be dishonest to get wrong.
- **The score is a paint expression, not a precomputed field.** `^` is an
  expression operator, so `rd^w × rw^(1-w)` evaluates in the paint and a weight
  change costs one `setPaintProperty` — no per-feature state to push, no
  re-parse of 1.7 MB of geometry per slider frame. The percentile ranks are
  injected into the fetched GeoJSON *before* it is handed to the source, so
  nothing was added to the payload on disk and the pipeline did not change.
  Phase 3 adds terms to this; it does not need to build it.
- **Quantile bands, not equal intervals.** A product of ranks is heavily skewed
  — half the city scores under 0.18 of a 0–0.79 range — so equal slices put
  nearly everything in the palest band and 8 tracts in the darkest. The bands
  come out equal to a tract or two, except at w=0 or w=1 where the score
  collapses to one variable and that variable's ties cannot be split.
- **The score is an ordering and is never quoted as a quantity.** 0.79 is not
  "79% under-served"; the legend and the tooltip both talk in percentiles of
  tracts. Phase 3's sensitivity analysis is the thing that would earn a number.
- The top-20 table still picks its rows the old way — top density tercile, then
  the longest walks — which is the cliff the score was built to remove. **9
  tracts score worse than the worst tract in that table**, led by a Canarsie
  tract at 21,783 people/km² and a 33.1-minute walk. Re-ranking the table
  belongs with Phase 3's live re-weighting; until then the map and the table
  disagree on purpose, and that is worth knowing before someone quotes both.

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
  neighborhoods because a lot of parts are red" was the palette's chroma
  geometry, since fixed; it was never a resolution limit. Dividing the same
  perceptual volume into more pieces would have made it worse.
- The better route to more resolution is a *score* on one axis with more
  steps. One ordered ramp carries 7–9 readable steps; a matrix does not.
  **Settled 2026-10-10 at 7:** the gap ramp clears 0.068 of lightness per step
  against a 0.06 floor, where 9 steps held 0.047. Sampling the bivariate
  field's own diagonal for it was measured and rejected — it crosses from cool
  grey to dark red, which reads as a *diverging* scale with a midpoint the
  score does not have, and its pale end sits at 1.35:1 against the light
  surface. One hue, violet, generated at even lightness instead.

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

### Ridership and crowding

Worth doing — but **after** the frequency join above, and the reason is the
whole insight. Crowding is a ratio, riders over seats. Ridership on its own is a
popularity measure, and the places this map exists to find are unpopular by
construction: a station with few riders is as likely to be badly served as to be
unneeded. Once Phase 2 has trains per hour, the supply side exists and the ratio
becomes computable:

```
pressure = riders per hour / (trains per hour × capacity per train)
```

- **The data.** Hourly ridership by station (`wujg-7c2s` through 2024,
  `5wq4-mkjj` for 2025 onward) is large but aggregates cleanly to
  station × hour × day type, and it joins on `gtfs_stop_id` like everything else.
- **Honest limit, stated up front.** These are *fare transactions at a station*,
  not passengers on a train. They miss transfers made inside the fare gates,
  they say nothing about direction, and a packed train passing through a quiet
  station is invisible to them. A station-level pressure index is defensible;
  "the 4 train is at 112% of capacity through Grand Central" is not, from this
  dataset.
- **Where it changes the answer.** A tract whose only option is a station
  already running at the edge of its scheduled capacity is worse off than its
  walk time suggests, because the next increment of service there is harder to
  deliver. That is a distinct condition from "far from a station" and belongs as
  its own deficit term in Phase 3 rather than folded into frequency.
- **Free side benefit:** ridership is the sanity check on Phase 4's hub list. If
  a chosen hub is not among the busiest stations in the city, it is the wrong
  hub.

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
  move it and see. *Half-shipped in Phase 1:* one weight, in the paint
  expression and the permalink. What is missing is the table re-ranking with it,
  which is also what would settle the disagreement noted above.
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

### Destinations, not just residents

Residential density answers "who needs to leave from here". It says nothing
about "who needs to get *to* here", and the Met is the ideal case: a tract whose
resident population is a few thousand absorbs millions of visits a year, and the
map today treats it as ordinary.

The right shape for this is **not** a boost applied to population density. Two
reasons. First, a boost makes the density field stop meaning people per km²,
which breaks the density floor, the terciles and the legend in one move — and
those are Phase 1's whole output. Second, the two kinds of demand want different
treatment: a resident's trip is daily and bidirectional, a visitor's is
occasional and often off-peak.

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

The one version that would earn its keep: **park-and-ride capacity at outer
stations**, which is a real access mechanism on Staten Island and at the eastern
Queens and north Bronx commuter-rail stops, and a small enough set to compile by
hand. That belongs as a column on the station table, not as a citywide layer.

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
- **Proposed-project toggles.** The concrete near-term version of the above, and
  the single most compelling thing on this list — it turns every finding into a
  question with an answer:
  - **Interborough Express** — Brooklyn to Queens along the existing Bay Ridge
    Branch freight right-of-way, roughly 14 miles, with light rail announced as
    the preferred mode. Its alignment runs through exactly the band of dense,
    poorly-served tracts the bivariate map lights up, so it is the strongest
    test case available: *does the IBX fix the Brooklyn–Queens gap this map
    shows?*
  - **Second Avenue Subway** — Phase 2 (96th to 125th) is under construction;
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
- **Crowding** has moved up to Phase 2 — see *Ridership and crowding* there. It
  landed in this phase originally on the assumption that it was a nice-to-have;
  it is really a service-quality measure, and it means very little until
  scheduled service is in hand.

---

## Suggested order

**Phase 1 is done** — shipped 2026-10-10, including the permalink and the
walk-access gap layer. The scoring machinery, its decomposition in the tooltip
and its weight in the permalink all exist now, which changes what Phase 3 is:
adding terms to a score that already works, and publishing its sensitivity,
rather than building one.

Next is step-free access, the biggest finding available for the least new
machinery. Then Phase 2 (GTFS frequency), because it is what makes a score mean
anything. Then the score. Then time-to-hub, which is a project rather than a
feature.

Proposed-line toggles (IBX, the T) are the most tempting thing to pull forward,
and they should not be. Their entire value is the before/after comparison, so
they are only as credible as the walk and service model underneath them —
shipping them early would produce confident answers from the weaker model.

Deliberately *not* first: more color classes, which solves a problem the map
does not have; bus proximity, which would add 62 MB of data to draw a layer
covering the whole city; and ridership, which means little before scheduled
service exists.

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
