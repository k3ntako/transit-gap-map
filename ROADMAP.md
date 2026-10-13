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

- **Filter by class, and a minimum-walk floor.** *Half done 2026-10-11.* The
  "fade areas meeting the standard" checkbox is the third clause this entry
  predicted, built the way it predicted — `belowFloor()` gains a test, the
  permalink gains `served=1`, and `showCount()` reports how many tracts are
  left, because the caution about stacked floors emptying the map was right.
  It no longer *fades*, though, and the correction is worth keeping because
  this entry's reasoning was right about the goal and wrong about the mark.
  0.12 of the data color was the first answer; measured on a dark basemap, a
  hue at low alpha composites to a muddy brown that still reads as a color, so
  the reader decodes it and "this is excluded" is the one thing it cannot say.
  Changed 2026-10-12: the fill goes to 0 and the **outline** carries the
  polygon, in the secondary ink at 0.40 — 2.6:1 against dark-matter's land
  against the 1.01:1 the old faded outline managed, which is to say nothing.
  The objection this entry raised still stands and is answered one mark over:
  a missing tract reads as missing data, so the tract does not go missing.
  The control now reads "Hide areas meeting the standard".

  What is **not** done is the per-class version. The shipped control uses one
  definition across all nine layers — `exc <= 0`, the project's own
  10-minute standard, which the score, the contiguity blocks and the clump
  layer are already built on — rather than each layer's own good end,
  because one control that means nine different things is worse than one
  that means one. A genuine `["in", ["get", "bi"], …]` class filter and a
  minute floor are still open, and the argument for doing them before the
  zoning work is unchanged.
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
- **A metric / imperial switch.** Raised 2026-10-12. Smaller than it sounds,
  because almost everything on the map is already in **minutes**, which need
  no conversion. What does change is a short list, checked 2026-10-12:
  - **Density**, which is people per km² in four places on the map — the
    tooltip, the bivariate legend's corner sentence, the legend breaks and the
    minimum-density slider — and in one fact on the stats page (`density_km2`
    on the longest dense-tract walk). Imperial is people per square mile,
    2.59 times the per-km² figure. American planning sources quote per square
    mile, so for this map's likely readers imperial is arguably the better
    default.
  - **The half mile**, which runs the other way: it is the planning rule's own
    unit, so in metric it should read "half a mile (805 m)" rather than be
    rounded to a metric number that is not the rule.

  Three things to get right. **Keep one stored unit.** The pipeline, the
  payloads and the permalink's `dens` value stay in km², and only the display
  converts, so a link copied in one unit opens correctly in the other. **The
  slider needs its own steps per unit** — 500 /km² is about 1,295 /sq mi, so
  converting the steps gives ugly labels; give each unit round steps and
  convert the value. **Carry it between pages the way the theme is carried**
  (`u=imperial` beside `t=dark` in the hash and the query string, rewritten
  by `syncPageLinks()` and `doc.js`), and convert in `stats.js` by passing the
  unit in, so the rule that no figure is a literal in prose still holds.
- ~~**The title card is getting long.**~~ **Done 2026-10-11, and not the way
  this entry proposed.** The suggestion here was a `<details>` for the
  caveats; the card had by then reached fifteen stat rows and three
  paragraphs, and folding a wall does not make it smaller, it hides it behind
  a click. It went to its own page instead: `about.html` is the long version
  of the same `stats.json`, and the card keeps the hero number, four rows and
  one sentence of validity. `web/stats.js` renders both, so the summary and
  the long version are one function called twice and no figure is written as
  a literal in either.

  Two things this changed that the entry did not anticipate. The **table
  overlay shrank** rather than growing: only the worst-tract table moves with
  the weight slider, so the borough, block and corridor tables belong on the
  static page and are gone from the overlay. And the argument for the
  collapsible panels below **weakened** — the card is now the smallest panel,
  so the case for collapsing rests on the legend and the controls, which are
  the two a reader actually keeps using.

  **Taken further 2026-10-12:** the card now carries no figures at all. The
  hero number, the four rows and the validity line moved to a third page,
  `stats.html`, which leads with plain-language facts and holds the full
  figure list and borough table that were on `about.html`; the method page
  is now only the method. The card is a description of the map and two
  links.

## Draw the network, not just the stations

Both items here come from feeds already fetched and parsed, but neither is a
"small thing" in the sense above: each needs a new payload out of `build.py`
rather than a field already in `tracts.geojson`. Raised 2026-10-11 and sized
the same day.

- **Ship the rail lines.** The map draws stations as dots, so a reader has to
  supply the network from memory. **Every one of the nine feeds carries a
  `shapes.txt`** — real route geometry, not an approximation — and the subway
  feed also carries `route_color` (the official line colors, `0062CF` for the
  8 Avenue services and so on) and `route_long_name`. Measured cost, one
  representative shape per route, simplified at `C.SIMPLIFY_TOLERANCE_METERS`
  and rounded to `C.COORD_PRECISION`, the same treatment the tracts get:

  | | lines | points | raw | wire |
  |---|---|---|---|---|
  | one per route, full detail | 47 | 48,057 | 982.4 KB | 220.4 KB |
  | **one per route, simplified 15 m** | **47** | **2,840** | **65.2 KB** | **15.2 KB** |
  | both directions, simplified 15 m | 94 | 5,665 | 130.1 KB | 20.6 KB |

  **15.2 KB on the wire for every subway, LIRR and Metro-North line.** It is
  its own payload, so it does not touch `BUDGET_TRACTS_WIRE_KB`; it wants its
  own budget constant beside the others. The two directions overlay each other
  on screen, so one line per route is the version to ship — the third row is
  there only to show that the choice costs 5 KB. Simplification is doing real
  work: 982 KB to 65 KB with no visible change at map zooms.

  **The reason to do this early is that it changes what the map argues, not
  just how it looks.** A reader currently sees dots and a gap and has to know
  the network to see why. With the lines drawn, the 430-tract contiguous band
  from Canarsie through Flatlands to St. Albans visibly sits in a *hole* in the
  network, and the 915 tracts past the standard stop reading as scattered
  polygons and start reading as the space between the lines. Same finding, made
  legible. Highest value per kilobyte left in this file.

- **An express-bus layer, which is not the FTN layer.** Measured 2026-10-11:
  **79 express routes, 1,855 stops, 671 of them served by express only** —
  `SIM1`–`SIM35`, `QM1`–`QM68`, `BxM1`–`BxM18`, `BM1`–`BM5`, `X27`/`X28`/`X37`/
  `X38`. Three things have to be decided before this is built, and two of them
  are reasons it is *not* a quick win:

  - **`route_type` cannot find them.** Every express route is `route_type=3`,
    identical to a local. The only discriminators are the `route_id` prefix and
    "Express" in `route_long_name`, so identifying them is a **string test on a
    route name** — the kind of thing `service.py` otherwise refuses, since it
    joins on keys everywhere else. If this ships, that soft spot goes in the
    methodology, not just in a comment.
  - **The FTN definition excludes them, correctly, so it needs a new one.** Of
    the 1,501 express-served stops in `bus_stop_service.parquet` only 206 are
    `is_ftn`, and those qualify on a *local* route sharing the stop; the 318
    express-only stops have a median best headway of **infinity**, because no
    route there holds 10 minutes across peak, midday and evening. An express
    bus is a scheduled peak-direction commuter run, not turn-up-and-go, so the
    layer needs a peak-direction frequency test that `C.FTN_WINDOWS` does not
    express.
  - **The fare premium cannot be sourced from this repo.** There is **no
    `fare_attributes.txt` in any of the nine feeds** (checked 2026-10-11). The
    existing commuter-rail disclosure is qualitative — "a separate fare, not
    covered by a subway MetroCard" — and the express note must stay qualitative
    too unless someone supplies a dated citation. No dollar figure from memory;
    see Phase 9 for how that goes.

  **Why it is worth building anyway, and what it argues.** Express buses are
  the city's own admission of where rail does not go: the SIM routes exist
  because Staten Island has one railway. Drawn on top of the gap map, an
  express layer annotates the worst tracts with *the city already runs a
  premium peak-only bus from here to Manhattan* — which is evidence for the
  gap, not against it, and is the same argument as the Phase 4 decision to keep
  buses out of the score. Build it after the rail lines, which give it
  something to sit on top of.

## Publishing the map

Not a phase, and not blocked by one. `web/` is two static pages reading
2.9 MB of generated payload, with no server-side anything — so the hosting
question is already answered and the real question is the build.

- **The obstacle is the data, not the site.** `web/data/` is gitignored because
  generated artifacts are not source, so a clean checkout cannot serve the map
  until `make all` has run — and that needs a 153 MB OSM extract, a Census API
  key, and a ~5-minute graph parse. CI cannot do it from nothing without a
  secret and a large download.
- **Three ways, and the one taken.** (a) Build locally, push only
  `web/` plus `web/data/` to a `gh-pages` branch. (b) Build in CI with the
  Census key as a repository secret and a cache for the `.pbf`. (c) Commit the
  payloads to `main` — rejected, it deletes the distinction the gitignore
  exists to make. This section first recommended (a), on the grounds that (b)
  is more moving parts for a payload that changes annually. **(b) was built
  instead, on 2026-10-12**, and the reason is the one that argument missed:
  deploys are rare, and a rare deploy from a laptop publishes whatever that
  laptop's `data/` happens to hold, by steps nobody remembers. In CI the
  deploy is a button, the build starts from current sources every time, and
  `verify.py` gates it. Actions minutes are free for a public repository, so
  the slow job costs nothing. The laptop route survives as `make publish`,
  running the same scripts.
- **What was built**, all in `DEPLOY.md`: S3 behind CloudFront rather than
  GitHub Pages, because CloudFront lets the cache headers below be set and
  Pages does not. Terraform in two modules split by privilege —
  `infra/bootstrap` (state bucket, a deployer IAM user, an OIDC role for
  Actions, a budget alarm; applied once as admin) and `infra/site` (bucket and
  distribution; applied as the deployer, who cannot touch IAM). No custom
  domain yet; the steps for one are written down. Two things found on the
  way: CloudFront compresses by `Content-Type` and its list has
  `application/json` but not `application/geo+json`, so `publish.sh` labels
  the GeoJSON explicitly and `smoke.sh` fails a deploy that arrives
  uncompressed; and a fresh dependency resolve would have moved pyrosm
  0.17 → 0.20 under the walk graph, so `constraints.txt` now pins what the
  published numbers were built with.
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
  through `stats.json` and the title card, the stats page and the method
  page all render it. It points at the public repository (checked
  2026-10-12), and `scripts/github-setup.sh` configures whichever repository
  `SOURCE_URL` names, so deploying from anywhere else means changing the
  link first.
- **Cache headers matter here.** The payload filenames are stable, so a long
  `max-age` serves stale data after a rebuild. **Settled without a build id**:
  CloudFront caches every file for a year and each deploy invalidates `/*`,
  while browsers get `Cache-Control: no-cache` and revalidate against the
  nearest edge. A build id in the fetch URLs would have meant the pipeline
  rewriting the pages, to save a 304 per file on a repeat visit.
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
term, not folded into frequency. **The free side benefit this used to claim is
withdrawn:** it said ridership was the sanity check on the hub list, and that
rule was tested on 2026-10-11 and failed on its own candidates — it is
circular, because the subway is radial, so its busiest stations are the radial
ones. Phase 4 picks hubs by jobs instead. Ridership's remaining jobs are the
crowding numerator and a check that a jobs-derived hub is reachable today.

**Relief as a second reason for a line — raised 2026-10-12.** The
Lexington Avenue line (4/5/6) is the standard example. The Upper East Side is
already within a short walk of a station, so this map scores it as
well served, and that is true. But the Second Avenue Subway was justified
largely by *relief*: taking riders off a line that is too full. Walking
distance cannot see that benefit at all. So crowding should do two jobs, and
they should be reported side by side rather than added together:

- **Coverage:** people who are far from a train (what the map does now).
- **Relief:** people who have a train but cannot comfortably get on it.

Relief needs crowding on the *line between stations*, not just at the
station, and the station-level data above cannot give that: a packed train
passing a quiet station is invisible to it. The way to get it is to take
trips from where they start to where they end, route each one over the
transit graph Phase 4 built, and count how many riders cross each stretch of
track per hour. That count divided by the line's capacity is how packed it is.
The trip data to check first is the MTA's published subway origin-destination
ridership estimates on `data.ny.gov`. Two cautions. Routing every rider on
the single fastest path puts everyone on the express and no one on the local,
which overstates crowding on the express, so the result is a rough estimate
and should be labeled as one. And full trains change how people travel, which
a fixed routing ignores.

Once loads exist, the line simulation in Phase 7 can report how much a
proposed line takes off the most crowded stretch, next to how many people it
brings within walking distance.

### Street pressure: buses and cars

Raised 2026-10-12: *where could a new line take pressure off the streets?*
This is a third benefit beside coverage and relief, and the data for it
exists:

- **Bus speeds** by route and segment, which the MTA publishes on
  `data.ny.gov`. A slow, full bus route is the clearest sign that a street is
  carrying a trip that wants a train.
- **Traffic speeds and volume counts** from NYC DOT on NYC Open Data.
- **How people get to work**, per tract, from the Census: ACS `B08301_003E`
  (drove alone) beside the transit share already listed in Phase 5. The LEHD
  origin-destination file (`ny_od_main_JT00_<year>.csv.gz`, from the same
  LODES8 release as the jobs data) says where those commutes go.

Put together, they would show corridors where many people drive or sit on
slow buses for trips that a rail line could carry. Three rules keep this
honest:

- **It stays out of the score**, for the same reason buses do: it is a
  separate lens, drawn on top, like step-free access. The score measures
  missing rail access. This measures street congestion, which has other
  causes too.
- **Driving is partly a result of poor transit, and partly of income and car
  ownership.** That is the same trap noted for the transit commute share,
  just mirrored. Read the driving share together with income, the way Phase 6
  proposes for car ownership, rather than alone.
- **A traffic count says where cars are, not where their trips started.** A
  lot of New York's street traffic is people passing through from New Jersey,
  Long Island and Westchester. A new subway line in Queens would not take
  those cars off the road. Only the commute data says which trips start and
  end inside the city.

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

### One thing deliberately not done

The roadmap asked for both a multiplicative and an additive form of the score,
defaulting to the product. Only the product shipped. The additive form is three
lines, but it needs a row in the alignment table before it is offered, and a
sum's known failure — giving a high score to empty land with terrible access —
is the thing the product exists to prevent. If it is added, it is added with its
measurement.

## What Phase 4 left behind

**Phase 4 shipped.** `pipeline/transit.py` builds a frequency graph over rail
and the Staten Island Ferry — 735 stations, 3,167 legs, 4,637 nodes, solved
for every station pair in 194 ms — and `pipeline/accessibility.py` turns it
into jobs reachable in 45
minutes, minutes to eight job centers, and the detour factor, in 30 seconds
for the whole city. The design reasoning now lives in `CLAUDE.md`, which is
where the things worth not rediscovering go; what is here is the part a plan
of record owes: the pre-registered test's result, and the work the phase
revealed rather than finished.

### The pre-registered ring test: outcome 3, with a bonus

This file committed to three outcomes before the detour factor was computed,
specifically so the answer could not be chosen after the fact. The result:

**Outcome 3 — no ring. The detour factor is close to flat.** Its coefficient
of variation is **0.19**; the middle 80% of tracts run 0.79 to 1.28 against a
full range of 0.61 to 1.97, and the borough medians are 0.76 (Manhattan),
1.00 (Brooklyn), 1.03 (Bronx and Queens), 1.39 (Staten Island). Outside
Manhattan and Staten Island there is barely a gradient. The worst decile is
225 tracts in 19 contiguous blocks, the largest holding 57 of them — 25%,
not a band.

*(Figures restated 2026-10-11, after the ferry edge. The conclusion did not
move; Staten Island's did. Its inter-borough pairs had been unreachable and
were therefore excluded from its own median, so the original 1.17 was the
layer flattering the borough by construction. On the pre-ferry graph the
numbers read CoV 0.18, range 0.61–2.09, 24 blocks, largest 56.)*

So the orbital problem is **a network property rather than a corridor**:
radial routing taxes lateral trips across the whole city at about the same
rate. That argues against any single new line as *the* fix, which is the
paragraph this file promised to write if the measurement came out this way.
The layer still ships, in the tooltip; it has no choropleth because a
near-constant field drawn as one would be a uniform map pretending to be a
finding.

**The bonus is outcome 2 in miniature, and it was not predicted.** The largest
detour block is not one of the eight proposed corridors' shapes, and **44 of
its 57 tracts also sit in a contiguous under-service block past 100 tracts**.
Two measures that share no inputs agree there: the under-service score is
walk-and-density and knows nothing about travel time, and the detour factor is
travel time and knows nothing about population. `accessibility.py` prints the
overlap on every run so it stays a measurement rather than a memory.

*(Also restated 2026-10-11, and this one relocated. Before the ferry edge the
block was Flatlands, Marine Park-Mill Basin and East Flatbush, 54 of its 56
tracts overlapping; it is now 57 tracts entirely on Staten Island with 44 of
them inside the 105-tract Staten Island under-service block. The agreement
survived the change in the measure, which is a stronger result than the
original, since the two measures now coincide somewhere the first version of
the detour factor could not even see. One wording trap it exposed: the
cross-check matches against **any** under-service block past 100 tracts and
there are two of them — 430 across Brooklyn and Queens, 105 on Staten
Island — so "the largest" was accidentally true before and false after,
and `accessibility.py` no longer says it.)*

### Jobs accessibility lowered alignment, as predicted — for the wrong reason

The prediction was on record here before anything was computed, and it held:
`pop x jobs out of reach` hits **16%** at worst-25 and `pop x detour` **0%**,
against a 26% random baseline and 80% for the shipped score. Hub minutes do
better — `pop x excess hub min` 56% — and still lose. None of them enters
the score; all ship as layers.

What was *not* predicted is why, and it generalizes past Phase 4: **alignment
tracks each factor's dynamic range, monotonically.** A factor with a narrow
spread multiplied by population is a population ranking, and the most populous
tracts are the well-served cores no agency proposes to serve again. The table
is in `CLAUDE.md`. The practical rule it leaves behind: before proposing any
`pop x factor` score, measure the factor's coefficient of variation. Under
roughly 0.5 it will lose to population and the alignment test will say so.

**Corrected 2026-10-11, and the correction is more useful than the original
finding.** The rule above is right. The *diagnosis* attached to it was wrong,
and it was wrong in a way worth recording because the number looked so
convincing: 16% is below a random baseline, which reads as a factor with
nothing in it.

Take the same jobs data and swap the axes — fix the jobs, measure the time,
"how long until a resident can reach a tenth of the city's jobs" — and it
scores **56%**, with the unweighted version at 60% at worst-25 and 62% at
worst-50, the highest of any candidate in the table. Nothing about the data
changed. What changed is that the measure stopped truncating.

A fixed time budget **right-censors**. Measured: 1,062 of 2,325 tracts —
**46% of the city** — need longer than 45 minutes to reach a tenth of the
city's jobs, so for nearly half the map `jobs45` reports the censored tail
rather than the trip. Breezy Point reads 0.13% of jobs and Arden Heights
0.12%, which looks like a tie and conceals 86 minutes against 121. The budget
also sits at the *median* of the quantity it thresholds (median time to a
tenth of the jobs: 42.6 minutes), which is the worst place available to cut
when the far side is the entire subject of the project.

So the CoV rule survives and is strengthened — uncensoring widened the
factor's CoV from 0.33 to 0.48 and alignment followed, exactly as predicted.
But "this factor has a narrow range" was a statement about the *field* that
got written down as a statement about the *world*. The transferable version:
**before concluding a factor is weak, check whether the measure of it is
censored**, because a ceiling in a field is indistinguishable from a narrow
distribution in reality until you remove it.

Shipped as a result: `jobs_time` / `jt10`, minutes to reach a tenth of the
city's jobs, with its own layer, tooltip row and stats-panel rows beside the
budgeted ones. `JOBS_BUDGET_MINUTES` stays — cumulative opportunity at a
fixed budget is what every peer analysis publishes, and it answers a real
question about opportunity *available* — but it is no longer the number
quoted about the worst-served places. A 30/45/60 budget toggle was considered
and dropped: moving the cutoff only moves where the censoring happens, and
measured, the ranking barely responds (Spearman 0.967 at 30, 0.983 at 60),
so the control would mostly have moved the legend.

One general distinction this left behind, which is the part to reuse: a
threshold on **the axis you measure along** truncates, and a threshold on
**the axis you measure** rescales. The 15-minute walk cap and the 45-minute
trip budget were the first kind. The jobs share is the second, which is
exactly why its value turns out not to matter — Spearman 0.968 to 0.991
across thresholds from 2% to 25% of the city's jobs.

### The work this phase revealed

- **Common lines, properly.** The wait is charged per leg group — every
  service making the same next stop — which fixes Jamaica but not Fordham,
  where the inbound local and the inbound express have *different* next stops
  and both end at Grand Central. Fordham to Grand Central therefore reads 37
  minutes against a real expected ~27. Fixing it needs hyperpath routing
  (Chriqui-Robillard with destination-dependent attractive sets), which is a
  different algorithm, not a parameter. It matters because the bias
  **overstates** under-service on commuter rail, which is the direction this
  project is least entitled to be wrong in — eastern Queens and the Bronx
  are where the thesis lives.
- **Payload headroom.** Phase 4 took the file to 343 KB against a 350 KB
  budget, which left room for no further field at all. **Deferred once, on
  2026-10-11, by raising the budget to 420** — and that is the whole of
  what was decided, so read it as a dated loan rather than a solution. The
  real answer is measured and unchanged: a column costs ~2.5x more as a
  per-feature property than as a parallel array, because gzip's window cannot
  span 2,325 interleaved features, and `hubs.json` carries eight columns for
  39 KB where interleaving them would have cost about 100. **So the next
  numeric field should move the existing ones into a columnar sidecar on the
  way in, not add a ninth property.** A second raise without that refactor is
  the thing the note in `config.py` exists to make embarrassing.

### What the alignment test is for — revised 2026-10-11

This is the only external check the project has, and it was quietly drifting
into being a target. The revision is a change of purpose, not of code, and it
came out of a plain objection: **the proposed lines are not optimal routes.**
They are need filtered through cost, politics, and whether an abandoned
right-of-way happens to be lying around — two of the eight exist largely
because one does. The agencies had far more constraints to satisfy than need,
so a score that matched them perfectly would have inherited every one of those
constraints. Agreement is evidence the score is measuring something real;
*more* agreement is not better.

So the test now has two jobs, and the second is the interesting one.

**1. A noise detector, and nothing more ambitious.** What fails the build is
the clumped hit rate against `baseline + ALIGNMENT_CLUSTER_MARGIN` — 26% +
10 = 36% — computed at runtime from the baseline and **never** from how
well the shipped score currently does. A floor set near the score's own number
is exactly the mechanism that turns agreement into a target, which is why it
is derived. The gate asks "is this score noise?" and refuses to answer "does
it agree with the MTA?". `ALIGNMENT_MIN_PRECISION` (40%) survives as the
per-tract figure and as the filter for the robustness intersection, but it no
longer fails a build.

**2. A diagnostic: where we disagree, and why.** The unit is now a clump,
which is the half of the old entry that genuinely needed fixing. A rail line
serves a strip of neighborhoods, so `alignment()` takes each score's worst 100
tracts, groups them over the same land adjacency `add_clusters` uses, keeps
blocks of 3+, and measures the hit rate over those tracts only. Measured, the
shipped score goes from 43% at worst-100 per tract to **52% clumped** over 8
blocks covering 77 of the 100 — clumping *raises* agreement, which is the
direction it should move if the scattered tracts were noise. And the blocks
are printed with their overlap, because that table is the output:

| block | people | overlap | where |
|---|---|---|---|
| 40 tracts | 165,438 | 88% | Staten Island — New Springville, Mariner's Harbor, Westerleigh |
| 11 tracts | 59,639 | **0%** | Queens — College Point, Whitestone, Bay Terrace |
| 5 tracts | 30,363 | **0%** | Queens — Pomonok, Kew Gardens Hills, Queensboro Hill |
| 5 tracts | 25,549 | **0%** | Queens — Oakland Gardens-Hollis Hills |
| 5 tracts | 26,656 | **0%** | Queens — Glen Oaks, Douglaston, Bellerose |
| 4 tracts | 18,424 | 100% | Brooklyn — Marine Park-Mill Basin |
| 4 tracts | 22,086 | **0%** | Bronx — Throgs Neck-Schuylerville |
| 3 tracts | 18,227 | 33% | Queens — South Ozone Park, Springfield Gardens |

**Five of eight blocks sit almost entirely outside any proposal, and four of
those five are northeastern Queens.** That is a hypothesis this file should
state and not yet claim: the plans cluster on existing rights-of-way, and
northeastern Queens has none — no abandoned branch, no freight line, no
wide median. If that is the reason, the disagreement is a property of what is
cheap to build rather than of where people are poorly served, and it is the
most interesting thing the test has produced. Testing it is what the
rail-lines work makes possible: with the network drawn, "is there a
right-of-way here?" becomes a question a reader can answer by looking.

**Digitizing the eight corridors is deliberately not done**, and the same
objection is why. Precise half-mile boundaries buy precision in a measurement
we have just decided not to optimize against. Worse, four of the eight have no
published alignment at all (Utica Ave, the SI West Shore, Red Hook, the 3-train
extension), so half the work would be drawing lines by hand and a
"disagreement" could be an artifact of where they were drawn — the NTA-name
list is coarse, but it is at least somebody else's boundary. If it ever
happens it belongs with the rail lines, where the other four arrive free: the
Interborough Express is the Bay Ridge Branch, QueensLink is the Rockaway Beach
Branch, the SI North Shore BRT is the North Shore right-of-way, and Penn
Station Access is the Hell Gate Line, all four already in the OSM extract.

### Airports, and what the graph is still not

Airports went to **Phase 9** and stayed there: a handful of hand-coded edges,
each carrying a fare and a headway no feed supplies. Unchanged by Phase 4
except that the graph they would attach to now exists.

And the standing limitation, to repeat wherever a number from this is shown: a
static graph with expected waits approximates a typical weekday peak. It
cannot represent departure-time effects — a just-missed connection, a timed
transfer that holds. Both commuter-rail feeds declare their transfers as
trip-to-trip timed transfers, which a frequency graph cannot use at all; a
RAPTOR over `stop_times` is the upgrade, and a project of its own.

### The Roosevelt Island Tram, and a penalty per mode

Raised 2026-10-12, as a maybe. The tram passes the same test the Staten
Island Ferry passed: it runs on its own right of way, on a published
schedule, and shares no lanes with traffic. So by the existing rule it
belongs in the transit graph, as two stations and one edge. It is not in any
of the feeds this project reads (RIOC runs it, not the MTA), so like the
airport links in Phase 9 it would be a hand-entered edge, with its trip time,
headway and fare rules taken from RIOC's published ones and dated.

**Expect it to move very little.** Roosevelt Island already has an F station,
so its tracts are already close to a train. The tram's real value is as a
second link to Manhattan, which is mainly a backup when the F is down.

**The harder question comes first, which is why this waits.** A tram minute
is not the same as a subway minute: the cabin is small, it stops in high
wind, and it may have its own fare. Charging that properly needs a general
rule for **how much worse each kind of service counts per minute**, and that
rule would apply to the ferry, the commuter railroads and the airport links
too, not just the tram. Today the graph has no such rule. Every mode is
charged its schedule time plus half its headway, plus a separate disclosure
for the commuter-rail fare. Decide that rule once, for every mode, and then
the tram is a cheap first test of it. Adding a one-off penalty just for the
tram would be a number nobody could defend.


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

- **Jobs per block** — LEHD LODES8 WAC. **Now fetched and in use:** Phase 4
  ships it as the destination side of the accessibility metric, 4,466,975 jobs
  across 2,303 tracts, so what is left here is the *other* use below. The best
  destination signal available: comprehensive,
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

- **Step-free access — a lens and a station ranking, explicitly not a term in
  the score.** 2,120 subway entrances exist; **116 are step-free** (102
  elevators, 10 ramps, 4 combinations). At station level, measured 2026-10-11
  from `stations.parquet`:

  | mode | stations | step-free |
  |---|---|---|
  | subway | 475 | 168 |
  | SIR | 21 | 6 |
  | LIRR | 40 | 33 |
  | Metro-North | 21 | 12 |
  | **all** | **557** | **219 (39.3%)** |

  Recomputing the map with the source set filtered to those 219 is nearly free
  — the same pipeline with a filtered station set, a third variant flag on
  `distance.py` beside `subway_only` and `weighted`.

  **Decided 2026-10-11: this does not enter the under-service score, and the
  reason is that it answers a different question.** The score ranks *tracts*
  and its intervention is new service. Step-free access ranks *stations* and
  its intervention is an elevator — a different object, a different budget
  line, a different decision-maker. Folding it into one number would make a
  tract score worse because its station lacks an elevator, which argues for a
  new train line as the fix for a missing elevator. It is also the one input
  here that is deliberately being *changed* while the map is being built, so
  anything load-bearing built on it decays. Two outputs instead:

  1. **A toggle: the city as a step-free rider sees it.** Note that this cannot
     be a browser re-weight like the equity slider — filtering the station set
     changes the *Dijkstra*, not an arithmetic blend, so the browser cannot
     derive it. It is a second per-tract field computed in the pipeline, which
     is exactly the pattern `acc` (walk plus expected wait) already follows
     beside `walk`. About 5 KB gzipped against roughly nine fields of headroom.
  2. **An elevator-priority station table**, which is the output this data is
     actually good for. Measured 2026-10-11, population whose *nearest* station
     is not step-free: **5,143,021 of 8,516,202 — 60.4%.** Then per station:

     ```
      73,521  14 tracts  125 St (subway)
      62,197  17 tracts  Woodhaven Blvd (subway)
      62,162  20 tracts  18 Av (subway)
      61,413  14 tracts  77 St (subway)
      60,433  20 tracts  111 St (subway)
      59,370  16 tracts  Grasmere (sir)
      59,323  18 tracts  Fort Hamilton Pkwy (subway)
      57,736   7 tracts  103 St (subway)
      54,527  23 tracts  Hollis (lirr)
      52,633  14 tracts  Halsey St (subway)
     ```

     **And the distribution is flat, which is the finding.** 252 stations are
     some tract's nearest, and the top 15 account for only **17%** of the
     affected population. There is no short list of elevators that fixes this.
     A "top 10 stations for elevators" headline would therefore be misleading;
     the honest claim is that the gap is broad, which is harder to say and more
     useful.

  **Three constraints on how it is published.** The flag means a step-free
  route was *built*, not that it works today — MTA publishes live elevator
  outages and a static map cannot represent "out until April", so the label is
  "step-free by design as of ⟨fetch date⟩" and the date comes from the fetch
  timestamp rather than from prose. The `ada_northbound` / `ada_southbound`
  columns are not decoration: some stations are step-free in one direction
  only, and a single boolean per station flatters the picture when the finer
  data is already on the table. And **the two numbers above are different
  findings, not one**: 60.4% is "your nearest station excludes you", which is
  not "how much further a step-free rider must travel" — that needs the
  filtered Dijkstra, because their real nearest step-free station may be a mile
  away. It is the reason both the toggle and the table have a job.

  **Not asserted here:** the size or schedule of MTA's accessibility
  commitment. There is no citation for it in this repo, and Phase 9 records
  what happens to figures written from memory.
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
- **Show proposed stations on the map, before scoring them.** Raised
  2026-10-12. This is the display half of the toggles below, without the
  recompute: draw where each agency proposes to put stations, on top of the
  existing layers, so a reader can see whether a proposal lands in the gap
  the map shows. It needs no pipeline change beyond one small new payload, so
  it can ship long before the before/after numbers do.

  There is no machine-readable source for this, so it is a **hand-entered
  file of station points**, checked in, with a project name, a source
  document and a date on every row. Four rules, most of them inherited from
  the toggles entry:
  - **Only stations a project has actually published.** Four of the eight
    corridors the alignment test uses have no published alignment at all,
    so they have no station list either. They are left off rather than
    guessed — a guessed station is the
    author's opinion drawn as if it were the agency's.
  - **Hollow markers and a legend line saying "proposed — not counted in any
    figure"**, so a screenshot cannot be mistaken for the network as it is.
    The layer should be off by default.
  - **It must never feed `verify.alignment()`.** That test deliberately
    matches by neighborhood name rather than digitized geometry, to keep the
    author's judgment out of it (see "Digitizing the eight corridors"
    above). A file of hand-entered points is exactly that judgment, so it is
    for display only.
  - **Remove a station when it opens.** Penn Station Access's four Bronx
    stations are Metro-North stations, so once they are in the GTFS feed the
    pipeline picks them up as real stations; a proposed marker left on top
    would count them twice in the reader's eye.
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
- **Simulate a line: how much would it help commutes?** Raised 2026-10-12.
  The toggles above only ask whether a line brings people within walking
  distance of a station. A line also makes trips *faster*, and the transit
  graph from Phase 4 can measure that. Add the proposed line to the graph as
  new legs, then re-run the job-access stage. Each leg needs a run time
  between stations and a headway. Where the project publishes those, use
  them. Where it does not, estimate from distance at a stated speed, and say
  so on the page. Report per tract, before and after:
  - **minutes to reach a tenth of the city's jobs** (`jt10`), the measure
    that does not cut off the worst-served places;
  - **minutes to the hubs**, such as Midtown and Downtown Brooklyn;
  - **actual commutes made faster**, using the LEHD origin-destination file
    (where people live against where they work, see *Street pressure*
    above). This is the closest answer to "how would this line help *my*
    commute?", because it counts real home-to-work trips rather than
    possible ones.
  - Once loads exist, **relief**: how much the line takes off the most
    crowded stretch of existing track (see *Ridership and crowding*).

  Keep coverage, commute time, relief and street pressure as separate
  numbers. Adding them into one total would mean choosing weights between
  them, and that choice is the political part of transit planning, not a
  measurement.
- **Use AI to suggest lines and gather cost research — as an assistant,
  never as the source of a number.** Raised 2026-10-12. A language model is
  good at two jobs here. It can propose candidate alignments to test,
  including ones a person might not think of. And it can find and summarize
  published cost studies and project documents. Both are only useful if the
  output goes through the same checks as everything else:
  - **A suggested line is a candidate, not a result.** It goes through the
    simulation above like any other, and its numbers come from the pipeline,
    not from the model.
  - **Every cost figure needs a source document a person has opened.** This
    file already refuses dollar figures from memory (see the express-bus
    entry). A model's memory is the same thing, and a model can produce a
    confident, specific, wrong number. So the model finds and quotes the
    source, and a person checks that the source says it.
  - **Results should be repeatable.** Save the prompt, the model version and
    the sources with the output, so a suggestion can be traced and rerun.
    That is the same standard as `config.py` for every other input.
  - The *Research* section below is the natural first use: the questions
    there are mostly literature, and a model is a good first pass at finding
    it.

### Choke points, and where a new crossing would help most

Raised 2026-10-12. Two questions that share one method, and the method is
the one the marginal-station surface uses: change the network, re-run the
fast shortest-path pass, and count how many people's trips change.

**Which existing links matter most?** Remove one link at a time and measure
the damage. On the walking network that means a bridge or an underpass. On
the transit graph it means a river tunnel or a key transfer. Report how many
people get pushed past the 10-minute walk, and how much the job-access time
rises. The 2019 plan to shut the L train's Canarsie tunnel is the real-world
version of this question. This is also a resilience map: it shows where one
closure strands the most people.

**Where would a new crossing help most, for the least money?** Find places
that are close in a straight line but far apart on foot. The walking graph
already has the raw material for this: the barrier probes in `verify.py`
(Newtown Creek, Hunts Point, Coney Island Creek) are exactly these places.
Candidates are pairs of points within a short distance of each other whose
walking route is many times longer. For each, add a virtual bridge, re-run,
and count people newly within 10 minutes of a station, or with a shorter
trip to jobs.

Two cautions:
- **Cost is ranked, not priced.** Span length and what is being crossed
  separate cheap from expensive by orders of magnitude: a footbridge over a
  rail cut or an expressway is a different project from a crossing of the
  East River. So rank by *people helped per meter of span, by type of
  crossing*, and do not print a dollar figure without a cited source, for
  the same reason as the line costs above.
- **A walking bridge only helps this score where it shortens a walk to a
  station.** Many crossings would help people reach shops, parks or jobs
  without changing their nearest station, so measure the job-access change
  too, or the method will undervalue them.

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
strongest live alternative, and the prediction this paragraph made has since
come in: cluster-level precision was measured, and it **did** overtake —
**54% clumped against the shipped score's 52%**. It is not alone, either;
`pop × excess hub min` lands on the same 54%. Both margins are one block's
worth and neither changes the score, for the reason above, which was never
alignment in the first place. But the per-tract figures hide it entirely, and
surfacing exactly this is what the clumped measure is for.

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
  the argument the orbital work in Phase 4 was built on — and the detour
  factor it produced says the gap is citywide rather than corridor-shaped,
  which makes this bullet sharper, not weaker: if no single corridor carries
  the orbital penalty, a map that scores IBX at the city median is not
  mis-ranking one project, it is blind to a whole class of them.
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
  headway all day — so the omission is at least visible on the page.
  **Decided in Phase 4 and not deferred: buses are never going in the graph.**
  Bus service is what the city provides instead of rail in exactly these
  places, so letting it close the gap would hide the finding by construction.
  The layer on top is the honest form of the same information.
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

## Phase 9 — airports

**Split out of Phase 4 on 2026-10-11.** See *What Phase 4 left behind* for
why — and note that the Staten Island Ferry is now the same problem with a
larger population behind it. These are the
only edges in the transit graph whose cost comes from neither GTFS nor the walk
network, and the reader they serve is a different one. Nothing here blocks
anything else.

**This phase is the one deliberate exception to "no buses in the graph."** The
Q70 is a bus and it is the *only* public transit link to LaGuardia, so a
rail-only graph answers "how do you get to LGA" with silence. That is a
defensible exception precisely because it is narrow and named: three airport
terminal nodes with hand-coded edges, not 12,942 bus stops. Keep it out of the
under-service score, where the rail-only reasoning in Phase 4 still holds —
LaGuardia's access is a worker's commute question, not a residential
service-gap question.

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
- **Those four figures are the only un-sourced numbers left in this file and
  they are all perishable.** The $8.75 AirTrain fare, the Q70 being free, the
  halt date and the ten-minute headway were written from memory in an earlier
  session and never cited. Fares change; "free since 2022" is the kind of claim
  that is true until a budget cycle says otherwise. **Treat all four as unknown
  until cited.** The right order when this phase is picked up: compute the Q70
  headway from `gtfs_q`, which is on disk and already parsed, so it is a
  measurement rather than a recollection; then find a dated source for each of
  the three policy facts (the fare, the free-fare status, the AirTrain LGA
  halt) and write the date of the citation beside it, because each can change
  without the map noticing. Until then no number in this bullet should appear
  in the UI or the README. This is the one section of the roadmap whose facts
  are not reproducible from the repo, and that is the reason it was split
  out.
- **The modeling is small and the graph now exists.** These are a handful of
  hand-coded edges on `pipeline/transit.py`'s rail graph — a headway and a
  fare on each — plus a terminal node per airport. The fare premium reuses the commuter-rail
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

## Research

Added 2026-10-12. These are questions to answer from published work, not
from the pipeline. They decide how the map's findings should be read and
what a proposal built on them would have to clear. Each answer that ends up
on a page needs a cited source with a date, under the same rules as the cost
figures in Phase 7. AI can do a first pass at finding sources, but a person
checks them.

- **What density do you need to justify a train?** A partial answer is
  already in this file (*Validating against official plans*): Pushkarev and
  Zupan's classic thresholds put heavy rail at roughly 12 homes per
  residential acre, about 4,100 people per km², and only 5.4% of the city's
  tracts fall below that. So in New York the answer is nearly "everywhere
  qualifies", which is why density cannot rank places here. The open part:
  those thresholds date from 1977 and were built for American cities.
  Questions to check: are there newer studies, how do they account for jobs
  and not just homes, and what do they say about light rail and bus rapid
  transit, which matter for corridors like the Interborough Express.
- **What does it cost to build a train, and the alternatives?** Per mile or
  per kilometer, by type: deep tunnel, cut-and-cover, elevated, surface light
  rail on an existing right of way, bus rapid transit, and ferry. New York's
  tunneling costs are among the highest in the world, and *why* is part of
  the question. The Transit Costs Project at NYU's Marron Institute has
  compared construction costs across countries and is the first source to
  check. Operating cost per rider matters as much as construction cost for
  ferries and buses, as Phase 7 already warns.
- **How have other cities tackled this?** Examples worth checking: how
  London, Paris (the Grand Paris Express, a ring line like the Interborough
  Express) and Madrid built new rail cheaply or quickly; how other cities
  decide where rail goes next; and whether any published a map like this one
  and what they did with it.
- **How do projects get funding and public support?** Federal Capital
  Investment Grants, the program behind *New Starts* (see the criteria under
  *Validating against official plans*); the MTA capital program; congestion
  pricing revenue; and value capture, where development near a new station
  helps pay for it. On public support: what made recent projects pass or
  stall, how cost overruns change support, and how a map like this one could
  be used in that conversation without overstating what it shows.

## Suggested order

The score exists, it is measured against something outside itself, and the
measurement gates the build. That changes what "next" means: every item below
can be judged by whether it moves the alignment number, and **eight of them
have now been measured as moving it the wrong way** — two from phases 2 and
3, six from Phase 4. That is not a run of bad luck; it is what the test is for.

Read that sentence with the 2026-10-11 revision in mind, though. "Moves the
alignment number" is no longer the same as "is worth doing", because the
proposed lines are not optimal routes — they are need filtered through cost,
politics and available rights-of-way, so agreement is evidence a score is real
and *more* agreement is not better. The test's second job is now to say where
the map and the agencies disagree and let someone ask why. See "What the
alignment test is for" above.

**Revised again 2026-10-12, after Phase 4's completed entries were deleted
rather than struck.** Everything that phase finished has left this file: the
Staten Island Ferry, cluster-level precision, the detour's diverging ramp and
hub granularity. A plan of record is for what is still to be decided, and a
list of things already done is the part of it that goes stale without anyone
noticing — the published reasoning for each now lives where it is acted on,
in `CLAUDE.md` and in the code, and the argument for each lives in the commit
that closed it. **Two Phase 4 leftovers remain**, and they are the real ones:
hyperpath routing for the local/express wait, and payload headroom, which was
deferred by raising the budget once and is dated as the loan it is.

**The rail lines are first** — revised 2026-10-11, displacing step-free
access, and now with three reasons rather than one. The map has a finding that
is only legible as a shape with the network drawn: the largest detour block
and a large under-service block are the same 44 tracts. It is the cheapest way
to see whether a corridor has a right-of-way, which is the open question the
disagreement table just raised about northeastern Queens. And four of the
eight proposed corridors *are* existing rights-of-way in the OSM extract, so
drawing the network is also most of the work of digitizing them if that ever
becomes worth doing.

15.2 KB on the wire, geometry already parsed in every feed, no new source and
no new model, and it is the only item in this file that changes what
the map *argues* rather than what it measures: the contiguous band stops
reading as scattered polygons and starts reading as the space between the
lines. A reader cannot currently see that without already knowing the network.

**It is also the last item in this queue that does not touch the headroom
loan**, which is the part worth not re-deriving every time this order is read.
Rail lines ship as their own payload and add no per-tract field, so they spend
none of the 420 KB budget — measured 2026-10-12, `tracts.geojson` sits at
350.1 KB, about four more property-borne fields of room. Both items behind
them want a field. So the columnar sidecar is not a separate decision to make
later; it fires on whichever of step-free access and ridership lands first,
and the only question still open is which one that is.

**Then step-free access**, still the biggest finding available for the least
new machinery — the subway-entrance table is fetched and verified (2,120 rows,
116 step-free), the swap is a source-set filter on the Dijkstra, and the result
needs no new model. But build it as the *lens plus station table* that Phase 6
now specifies, not as a term in the score: 60.4% of the city's population has a
nearest station that excludes them, and the per-station version of that is the
output the data is good for. Note the deflating part before quoting any of it —
the distribution is flat, 252 stations deep, and no short list of elevators
fixes it.

**Then ridership and crowding**, because it is the last piece of the
service-quality axis. Note that its old justification is dead: this said the
hub list depended on it, and Phase 4 settled the hub list with jobs instead,
precisely because ridership is circular — the subway is radial, so its
busiest stations are the radial ones. Ridership keeps a narrower job: a sanity
check that a jobs-derived hub is reachable today, and the numerator for
crowding. Read the three measured obstacles first — it is a server-side
aggregation against 167 million rows, it joins on `station_complex_id`
rather than `gtfs_stop_id`, and train capacity is in none of the data.

**Then the columnar sidecar for the tract payload**, which is now the oldest
unpaid debt in this file and the one that blocks fields rather than ideas. The
budget was raised to 420 KB on 2026-10-11 instead; the measured fix is to move
the numeric columns out of `tracts.geojson` into parallel arrays aligned to an
explicit `geoids` list, the way `hubs.json` already does, at ~2.5x less per
column. Step-free access and ridership both want a field, so whichever lands
first should carry this.

Orthogonal to all of that, and pickable whenever: **running the first
deploy** — not building one. That changed on 2026-10-12 and this paragraph
said otherwise until then: S3, CloudFront, Terraform and the Actions workflow
all exist, so what is left is one button press and whatever a Linux runner
says about a pipeline that has only ever run on a laptop. The *reader* problem
is unchanged until that happens — nobody outside this checkout can see any of
this — but it is now a smaller item with a known shape, and `DEPLOY.md` has
the order. Beside it, the **small no-new-data items** — the per-class filter (the
standard-based half shipped 2026-10-11), the collapsible panels, the legend
width. The class filter is worth doing before the zoning
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
the resolution belongs on a one-axis ramp (6 steps as of 2026-10-11, down
from 7 — see below) rather than in more cells; and **anything that adds a
term to the score without a row in the
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
