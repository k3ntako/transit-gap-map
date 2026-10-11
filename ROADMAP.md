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
   *Contested for walk time as of 2026-10-10 — see "Proposal — what the gap
   score should measure" below.*

Phases 0 and 1 are shipped and have been dropped from this file. The numbering
of what is left is deliberately unchanged, because commit messages, the README
and the sections below all refer to it.

---

## Small things, no new data

Everything here reads fields that are already in `tracts.geojson` or touches
only `web/`. None of it depends on a phase below.

- **Filter by class, and a minimum-walk floor.** The display floors fade tracts
  under a density or population threshold; the same mechanism should fade by
  *class* — show only the worst two gap bands, or only tracts over N minutes'
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
  the seven gap rows to wrap or abbreviate. Measure the widest label in each of
  `renderLegend()`'s cases first, then pick the width — and check the gap
  legend, which has the most rows and the longest numbers.

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
- **Payload, measured:** 2.9 MB total — `tracts.geojson` 1.7 MB, `walksheds`
  816 KB, `stations` 138 KB, two surface PNGs at ~130 KB, `stats.json` 24 KB,
  `palette.json` 4 KB. Fits any static host's free tier and loads over a phone
  connection, so no tiling and no CDN decision is required.
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

## Proposal — what the gap score should measure

**Proposed 2026-10-10. Not implemented; no code has moved.** The walk-access
gap layer shipped with `score = rank(density)^w × rank(walk)^(1−w)`. Hovering
Mariner's Harbor is what exposed a defect in it, and measuring the obvious fix
turned up a second one.

### The defect

Percentile rank destroys the walk-time tail, which is the only part of the walk
number carrying information:

| actual walk | percentile rank |
|---|---|
| 8.4 min (city median) | 0.50 |
| 12.9 min | 0.69 |
| 25 min | 0.89 |
| 88.8 min | 0.995 |
| 127.5 min | 1.000 |

Ten times the walk is 1.4 times the rank. Past ~25 minutes every tract is
effectively tied, so the top of the ramp is decided by density alone. The
symptom: Upper East Side–Yorkville, 110,790 people/km² and a **12.9-minute**
walk, paints in the worst band, while Mariner's Harbor at 10,145 people/km²
and a **104-minute** walk sits at the 67th percentile.

Rank was the right medicine for density, which spans 279 to 110,790 and would
bulldoze minutes in any sum. It was then prescribed to walk time out of
symmetry, and walk time did not need it.

### What does not fix it

**Capping walk time on a minute scale is a near-no-op.** Replacing the walk
rank with `min(walk, 30) / 30` leaves the ordering 0.938 Spearman-correlated
with the shipped score. Measured 2026-10-10 over all 2,243 scored tracts.

**No choice of cap fixes Yorkville.** Its density rank is exactly 1.000 — it is
the densest tract in the city — so at w=0.5 the score is √(walk term), and no
walk scale makes the square root of a number near 1 small. It holds the worst
band at every cap tested from 15 to 127 minutes.

**A lower cap makes the original complaint worse.** The cap is the point above
which everything ties, and ties are then broken by density alone:

| cap | tracts at the ceiling | Mariner's Harbor's worst tract |
|---|---|---|
| 15 min | 25.6% | 55th percentile |
| 20 min | 17.1% | 70th |
| 25 min | 11.2% | 81st |
| 30 min | 7.4% | 88th |

At a 20-minute cap a 104-minute walk scores identically to an 18-minute walk,
and Belmont (49,459 people/km²) wins the tiebreak over Mariner's Harbor
(10,145). "Nobody regularly walks 20 minutes to a train" is correct about
behavior and wrong about where that number belongs in the formula.

### Where the behavioral number does belong

At a floor, not a ceiling: inside roughly ten minutes there is no gap to
measure. El-Geneidy et al. measured 16,014 home-based transit trips in
Montreal along the street network — median walk 294 m, 75th percentile 525 m,
85th percentile 678 m; by mode the 85th percentile is 524 m for bus, **873 m
for metro** and over 1,250 m for commuter rail
([paper](https://www.tram.mcgill.ca/Research/Publications/Transit_service_area.pdf)).
At this project's 1.35 m/s that is 10.8 minutes to a subway and 15.4 to
commuter rail. The half-mile rail standard, 800 m, is 9.9 minutes — the same
number arrived at from planning convention rather than observation.
Corroborating: Calgary LRT mean 649 m with the 75th percentile at 840 m; a
second operator in the same review, mean 805 m to rail and 75th at 1,018 m; a
recent light-rail study, mean 968 m and 75th at 1,167 m.

Caveat worth keeping next to the number: those are *riders'* revealed walks,
conditional on having chosen the train. This project measures the median
network distance from a grid over a tract to the nearest station whether anyone
there rides or not. The ten-minute anchor is a planning standard that observed
behavior happens to agree with, not a like-for-like comparison.

### The proposal

```
excess = population × max(walk_min − 10, 0)   # person-minutes, one-way
```

Real units, one research-anchored constant, nothing normalized and nothing
weighted. Measured on the shipped data:

- The worst tracts are four of Mariner's Harbor's, plus New Springville and
  Westerleigh — 415,000 to 568,000 excess person-minutes each.
- Yorkville falls to band 3 of 6. The worst band holds 69 Queens tracts, 56
  Staten Island, 14 Bronx and 14 Brooklyn — and no Manhattan tract at all.
- Citywide: **39.1 million excess person-minutes per one-way trip**, and
  **3.02 million New Yorkers** beyond the ten-minute standard. The project
  publishes no headline number of this kind today.
- Spearman correlation with the shipped score: **0.145**. This is a different
  map, not a retuning — which is the point, and also the risk.
- **It is the only version that survives an external check.** Against the eight
  corridors agencies have actually proposed, the shipped score's worst 25
  tracts hit 24% — the 26% baseline a random pick would get — while this
  measure hits 80%. See *Validating against official plans* in Phase 8; that
  test is now the strongest argument on this page for making the change.

### What it costs

- **59.2% of tracts score exactly zero**, being inside the standard. Quantile
  bands cannot split a tie, so three of the seven colors come out empty. The
  fix is structural, not cosmetic: band 0 becomes "within the ten-minute
  standard" and the other six are quantiles of the 915 tracts that are not,
  which comes out at 1,328 / 152 / 153 / 152 / 153 / 152 / 153. Most of the map
  then paints the palest step — the honest picture, and a visibly emptier one
  than today's.
- **The weight slider loses its meaning.** There are no longer two normalized
  halves to trade off. It shipped three commits ago and would be retired, along
  with the `w=` permalink key; unknown keys are ignored, so existing links
  degrade quietly rather than breaking.
- **Density leaves the formula, replaced by population.** For "how many people
  does this happen to", population is the honest term. Tract populations run
  1,687 to 6,444 across the middle 80%, so it still does real work rather than
  acting as a constant.
- **It moves closer to the plain walk-time layer** that already ships: ρ = 0.77
  against raw walk time, with 28 of the worst 40 tracts shared. Today's score
  is more independent of that layer, and this is the strongest argument against
  the change.
- **Design principle 2 would need amending** to "percentile-rank the skewed
  metric; keep a meaningful one in its own units." The principle is
  load-bearing for Phase 3, where most candidate terms (income, zero-car
  households) genuinely are skewed and genuinely do want ranking. The amendment
  is narrow: a metric whose units mean something to a reader, and whose tail is
  the finding, should not be ranked.

### What would change the recommendation

- **Phase 2 changes the shape of the question.** Once a wait penalty exists the
  deficit side stops being one number, and a product of ranks may be the right
  container for three or four deficit terms even though it is wrong for one.
  This proposal is for the single-term case; Phase 3 should revisit it, not
  inherit it.
- **The floor may want to be per-mode.** Ten minutes is the subway figure;
  commuter rail's 85th percentile is 15.4. The nearest station's mode is
  already in the payload as `smode`, so a per-mode floor costs nothing but a
  decision about whether a $10 peak LIRR ticket should get a more generous
  standard than a subway swipe.
- **This reintroduces the failure mode Phase 3 warns about, in miniature.**
  Population is not normalized, so a small population times a huge excess can
  outscore a crowd with a modest one: the Arlington marshland tract — 1,078
  people, a 127.5-minute walk — scores 126,665 and lands in the **worst band**,
  where today's product of ranks correctly buries it at the 3rd percentile.
  That is the "empty land with terrible access" result the Phase 3 notes argue
  against. Measured containment: only **3 of the 153 worst-band tracts have
  fewer than 2,000 people**, so it is three bad pixels rather than a pervasive
  bias, and the shipped population floor fades them on demand. Deciding whether
  the floor should instead be *inside* the score is part of accepting this
  proposal, not a detail after it.

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
- **Rename the gap layer here, and not before.** "Walk-access gap" is the
  honest name while service quality is missing from it; a frequency-weighted
  station set is what earns the word *under-served*. The rename is the last
  step of this phase, not the first.
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
  move it and see. *Half-shipped:* one weight, in the paint expression and the
  permalink. What is missing is the table re-ranking with it — and until that
  lands **the map and the top-20 table disagree on purpose**. The table still
  picks its rows the old way, top density tercile then the longest walks, and
  **9 tracts score worse than the worst tract in it**, led by a Canarsie tract
  at 21,783 people/km² with a 33.1-minute walk. Worth knowing before someone
  quotes both.
- **Show the decomposition.** The tooltip should say *why* a tract scores high
  (e.g. "92nd percentile need, 88th percentile deficit; driven by wait time").
  A score nobody can interrogate does not persuade anyone.
- **Publish the sensitivity.** Which tracts stay in the worst 50 across a wide
  range of weightings? Those are the robust findings; the rest are artifacts of
  the weighting. This is the single most credibility-building thing in the
  roadmap.

### Contiguity: a line serves a corridor, not a tract

Every version of the score in this file ranks tracts independently, and that is
a genuine misfit with the decision it informs. A new line is justified by a
*run* of under-served tracts along a plausible alignment. One bad tract ringed
by good ones is a bus-stop problem, or a park, or a measurement artifact — and
ranked independently the two are indistinguishable. Forty bad tracts in a row
are a transit project; one is a complaint.

This needs no new data. Tract adjacency falls out of geometry already in
`data/processed/`, and the DCP polygons are water-excluded, so tracts facing
each other across the East River do not share a boundary — contiguity is
already *land* contiguity, which is the right default for a walking measure.
The only explicit decision is whether to add edges for the bridges.

- **Report cluster size and cluster population beside the tract score.** The
  honest headline is not "the worst tract" but "the largest contiguous block of
  tracts above the standard, and how many people live in it". That second
  number is the one an agency would recognize, and it is a connected-components
  pass over the adjacency graph on a single boolean — cheap, and legible enough
  to put in the tooltip.
- **Then the spatial statistic, not before it.** A Local Moran's I cluster map
  is the formal way to say the same thing (`libpysal` for the weights, `esda`
  for the statistic) and distinguishes a real hot spot from a run of noise. It
  is also harder to explain, so it earns its place only after the plain
  component count is on the page.
- **It changes the alignment test in Phase 8.** Those corridors are runs of
  tracts, so a per-tract precision figure is comparing a point measure against
  a line measure. Precision at the cluster level is the fairer version of that
  test, and probably a kinder one to both scores.
- **It is also the honest answer to the orbital problem.** The IBX corridor is
  not a set of individually bad tracts — its median walk is the city median.
  What makes it a project is that it is a *continuous band* of them with no
  radial service. Phase 4 needs the travel-time graph to see that; contiguity
  gets part of the way there with nothing new.

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
  St–Grand Concourse. Phase 2's ridership join is the arbiter — a proposed hub
  that is not among the busiest stations in the city is the wrong hub, which is
  the sanity check already noted there.
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
  — plus a terminal node per airport. The fare premium reuses the flag Phase 2
  already plans for LIRR; the 3am service window reuses the span-of-service
  work there. **No GTFS feed for AirTrain JFK turned up in a search** and the
  Port Authority's developer pages are unchecked, so assume hand-coding and
  treat a feed as a bonus.
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
- **Crowding** has moved up to Phase 2 — see *Ridership and crowding* there. It
  landed in this phase originally on the assumption that it was a nice-to-have;
  it is really a service-quality measure, and it means very little until
  scheduled service is in hand.

### Validating against official plans

The map has no ground truth, and "does it look right" is not a standard. The
closest available check is whether the tracts it calls worst are the ones
transit agencies have already decided to spend money on. **Measured 2026-10-10,
and the result is severe enough to be the main argument for the proposal
above.**

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
elsewhere** — the variable Phase 5 already ranks first.

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
density is spending that weight on a variable that has already saturated.

**The alignment test.** Tracts were matched by neighborhood name to eight
corridors that agencies have actually proposed: Penn Station Access (four new
Bronx stations, 2027), the Staten Island North Shore BRT on the abandoned rail
right-of-way, Staten Island West Shore, the Utica Avenue corridor, the 3-train
extension at East New York, QueensLink on the Rockaway Beach Branch, the
Interborough Express, and the Red Hook extension. **580 of 2,243 tracts (26%)
sit in one of them**, which is the baseline a useless score would hit.

| | worst 25 | worst 50 | worst 100 |
|---|---|---|---|
| Shipped score (ranks, w=0.5) | 24% | 24% | 25% |
| Proposed excess person-minutes | **80%** | **58%** | **43%** |
| Baseline (random) | 26% | 26% | 26% |

The shipped score scores **at the baseline at every depth**. Against the
agencies' own revealed priorities it has no predictive value whatsoever. The
proposed measure runs 3.1× the baseline at the top.

**Where the disagreements are, and why.** The pattern is systematic rather than
noisy, and it maps exactly onto the three axes of the reframe at the top of
this file:

- **Agreement where the plan is about walk access.** Penn Station Access,
  SI North Shore, SI West Shore, Utica Avenue and QueensLink all score high,
  and the proposed measure ranks all five at the 98th–100th percentile. Co-op
  City is the single worst tract on the current map and officials called it a
  transit desert at the Penn Access groundbreaking. SI West Shore is the
  sharpest case: the shipped score puts it at the 76th percentile, the proposed
  measure at the 100th.
- **Accidental agreement where the plan is about network shape.** The IBX
  corridor scores 98th percentile on the shipped score — but its median walk is
  **8.4 minutes, exactly the city median**. The map is flagging it for density,
  not for the orbital gap that is IBX's entire justification. Same for the
  3-train extension (median walk 6.2 minutes) and Red Hook (8.3). These are
  capacity and connectivity projects, and this map cannot see either. They are
  the argument for *Secondary hubs, and the orbital problem* in Phase 4.
- **False positives that are the defect in miniature.** The shipped score's
  worst 25 includes Yorkville, Lenox Hill, East Village, Belmont, Corona,
  Tremont, Claremont Village and Flushing — all 11–19-minute walks, none
  proposed for new rail by anyone, because they already have it.
- **False positives that are probably real findings.** The proposed measure's
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

- **Buses.** Rail-only is a deliberate v1 scope, and on Staten Island, in
  eastern Queens and in the east Bronx it is the difference between *no*
  transit and *slow* transit. Phase 2 above.
- **Car ownership.** A 40-minute walk where 90% of households have a car is a
  different finding from the same walk where 10% do. Phase 5 — and the MTA's own
  Equity Areas definition says the same thing in its own words.
- **Whether a line is buildable at all.** Geology, right-of-way, yard capacity
  and where the tunnels would actually connect kill more real proposals than
  low need ever does. Phase 7 is the only part of this file that touches it.
- **Cost.** Excluded for now on purpose, and that exclusion is exactly why this
  map can point at things no agency would fund. Phase 6 is about the rider's
  cost, not the builder's.
- **Contiguity.** The one with no home anywhere in this file, and probably the
  most consequential of the five: a line serves a *corridor*, not a tract. See
  *Contiguity* in Phase 3.

Four of the five are already scheduled, which is the reassuring reading of the
alignment test: the measure is incomplete in ways this file has already named,
rather than wrong in ways it has not.
---

## Suggested order

The scoring machinery, its decomposition in the tooltip and its weight in the
permalink all exist, which is what makes Phase 3 *adding terms to a score that
already works* rather than building one.

One decision is open ahead of everything else: the gap score's definition,
written up as a proposal above. It is cheap to act on and it changes which
tracts the map calls worst, so settle it before the top-20 table is re-ranked
to agree with the map. The alignment test in Phase 8 is what settles it: the
shipped score cannot beat a random baseline against agencies' own revealed
priorities, and the proposal beats it by 3.1×. **Move that test into
`verify.py` either way** — it is the only external check this project has, and
without it a future score change is unfalsifiable.

Next is step-free access, the biggest finding available for the least new
machinery. Then Phase 2 (GTFS frequency), because it is what makes a score mean
anything. Then the score. Then time-to-hub, which is a project rather than a
feature.

Secondary hubs and the detour factor sit inside Phase 4 rather than beside it:
they need the transit graph, and they are the reason to build it with more than
one destination in mind from the start. Airports are the cheapest thing in that
phase and a good first test of its fare and night-service machinery.

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

**Contiguity is the sleeper.** It is in Phase 3 because it is a property of the
score, but it needs nothing from Phase 2 and nothing from the Census — only an
adjacency graph built from polygons already on disk. It is also the cheapest
thing on this page that changes what the map *says* rather than how it looks,
because "the largest contiguous block above the standard, and its population"
is a headline no version of the current score can produce.

Proposed-line toggles (IBX, the T) are the most tempting thing to pull forward,
and they should not be. Their entire value is the before/after comparison, so
they are only as credible as the walk and service model underneath them —
shipping them early would produce confident answers from the weaker model.

Deliberately *not* first: more color classes — settled 2026-10-10, a 4×4
bivariate matrix measures 10.0 on its weakest pair against a 15.1 floor, and
the resolution belongs on a one-axis ramp (now 7 steps) rather than in more
cells; bus proximity, which would add 62 MB of data to draw a layer
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
| Bike routes | `data.cityofnewyork.us/resource/mzxg-pwib` | 29,695 segments (7,418 protected) |
| Citi Bike GBFS | `gbfs.citibikenyc.com/gbfs/en/station_information.json` | 2,519 stations, 74,258 docks, 1.4 MB |
| ACS variables | `api.census.gov/data/2023/acs/acs5` | `B08201_002E`, `B19013_001E`, `B17001_002E`, `B08301_010E`, `B18101`, `B01001` all confirmed |

The subway station table already carries `gtfs_stop_id`, so every GTFS join is a
key join. It also carries `structure` (elevated / subway / open cut) and a `cbd`
flag, neither of which the map currently uses.
