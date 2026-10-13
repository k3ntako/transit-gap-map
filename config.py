"""Single source of truth for paths, CRS, data sources and analysis parameters."""

import gzip
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
WEB = ROOT / "web"
WEB_DATA = WEB / "data"

for _d in (RAW, PROCESSED, WEB_DATA):
    _d.mkdir(parents=True, exist_ok=True)

# --- Coordinate systems -----------------------------------------------------
# EPSG:26918 = UTM zone 18N, NAD83, units = meters. Covers all five boroughs,
# so lengths and areas come out in true meters without further conversion.
CRS_PROJ = "EPSG:26918"
CRS_GEO = "EPSG:4326"

# --- Analysis parameters ----------------------------------------------------
WALK_SPEED_MPS = 1.35          # ~3.0 mph, standard pedestrian planning speed
# Cycling, for the bike variant of the same pass. 3.6 m/s is 13 km/h, inside
# the 12-16 km/h range urban cycling is normally planned at and close to the
# average trip speed bike-share operators report for a classic bike.
#
# The thing worth knowing about this number: **it cannot change which areas
# the bike layer ranks worst.** Bike minutes are bike distance divided by a
# constant, so the ordering is the distance ordering whatever the speed is.
# What the speed moves is only where a fixed threshold falls -- "within 10
# minutes by bike" -- and the comparisons this layer exists to make are
# ratios against the walk, which are also speed-free in their shape.
BIKE_SPEED_MPS = 3.6
WALK_THRESHOLDS_MINUTES = (5, 10, 15)
HALF_MILE_METERS = 804.672     # for the published-benchmark cross-check

# How far beyond the city line to extend the walk network and station search.
# Without this, routing near the border is artificially truncated and a station
# just outside NYC (Yonkers, Nassau) wrongly appears to serve nobody.
STATION_BUFFER_METERS = 2500

# Intra-tract sample grid. Measured convergence: the per-tract median walk
# time converges linearly in this spacing, and the error against a 15 m
# reference run is 0.211 min at 75 m, 0.118 at 50, 0.072 at 35 and 0.050 at
# 25. Below ~35 m that is finer than the model deserves -- a single 1.35 m/s
# walk speed for everyone -- so 25 m is the end of the useful range, not a
# step toward a finer one. It costs 2.4 s of build time and takes the
# smallest tracts from 21-34 samples to 212-292, which is the real gain:
# a median over 21 points was the weakest measurement in the dataset.
TRACT_SAMPLE_SPACING_METERS = 25
SURFACE_RESOLUTION_METERS = 100  # continuous distance raster cell size
SIMPLIFY_TOLERANCE_METERS = 15   # tract geometry simplification, projected
COORD_PRECISION = 5            # output GeoJSON decimal places (~1 m)

SNAP_WARN_METERS = 250         # warn if a station snaps further than this
MAX_SNAP_METERS = 1500         # beyond this, treat a sample as unreachable

# --- Service quality (GTFS) -------------------------------------------------
# Hour windows, in service-day hours, counted as [start, end). Hours are taken
# modulo 24 before matching, which is what makes the night window work: GTFS
# records a trip departing at 3am under the *previous* service day as hour 27,
# so the subway feed's departures run 0 through 28. Taking hour % 24 unions
# hour 3 with hour 27 and is exactly the set of trains a rider standing on the
# platform at 3am could board. For the 8am window it is a no-op -- no feed
# records hour 32.
SERVICE_WINDOWS = {
    "am_peak": (8, 9),     # the headline number; the design hour in planning practice
    "midday": (12, 13),
    "evening": (20, 21),
    "night": (3, 4),       # the subway runs 24/7 and most of the rest does not
}
SERVICE_SATURDAY_WINDOW = (14, 15)

# Expected wait, given a headway, is headway/2 only for turn-up-and-go service.
# Past roughly a 30-minute headway nobody arrives at random -- they read a
# timetable -- so the cost stops being time on a platform and becomes schedule
# inconvenience, which this model does not claim to measure. The cap is half of
# that 30-minute headway: the point where the approximation stops being true is
# the point where it stops being applied.
WAIT_CAP_MINUTES = 15.0

# Which window's headway becomes the access penalty. AM peak is the design hour
# and the best-served hour, so this is the *conservative* choice: it penalizes a
# station as little as the schedule allows anywhere in the day.
WAIT_WINDOW = "am_peak"

# A station with no scheduled trains at all in WAIT_WINDOW is dropped from the
# frequency-weighted source set rather than given the cap. Belmont Park runs
# only on race days and Greenport a handful of trips; treating either as "a
# 15-minute wait away" would be a worse lie than treating it as closed, which
# at 8am on a Wednesday it is. The pure-walk pass still counts it, so nothing
# the published walk figures measure is affected.

# Payload budgets, asserted at the end of the stage that writes each file so
# regressions can't slip through.
#
# The GeoJSON budgets are **gzipped** sizes, because that is what a reader
# downloads: tracts.geojson is 1.86 MB on disk and 305 KB on the wire. Measured
# at compression level 6, which is what a CDN actually serves -- level 9 is only
# 2% smaller, so the number does not flatter itself.
#
# The reason is simply that the wire number is the one a reader experiences, so
# it is the one worth stating and policing. An earlier version of this comment
# claimed more than that -- that a new numeric field costs 28 KB raw but only
# 0.3 KB gzipped, so a disk budget would fail the build over growth nobody
# feels. **That measurement was wrong.** Re-measured properly when Phase 2 added
# four fields: 24.4 KB raw and 5.1 KB gzipped each, and the added bytes compress
# at 4.8x against the whole file's 5.1x. Field data compresses like everything
# else, not for free -- even a verbatim duplicate of an existing column costs
# 7.4 KB, because gzip's window cannot see across 2,325 interleaved features. So
# raw and wire move together and either basis would fire at about the same time;
# the wire one is just the honest unit.
#
# What these numbers catch immediately is the three things that genuinely blow
# the payload up -- finer geometry, a string column that forgot to join
# INTERNED, and switching GEO_LEVEL to block groups. And note that a new float
# field must be added to the rounding tuple in build.py: unrounded float64
# costs 15 KB gzipped per field instead of 5.
#
# **Raised from 350 to 420 on 2026-10-11, deliberately and once** -- and the
# loan that raise took was repaid on 2026-10-12, which is what the figures
# below are now measuring. Phase 4 took the file from 305 KB to 343 against a
# 350 budget, leaving room for no further field at all. The fix deferred then
# was to move the columns into a parallel-array sidecar, measured at ~2.5x
# cheaper per column than a per-feature property because gzip's window cannot
# span 2,325 interleaved features. Step-free access wanted the next field, so
# it came with that refactor: `tracts.geojson` is geometry plus `geoid`, every
# column is an array in `tract_cols.json`, and `app.js:attachCols()` joins
# them before `addSource`.
#
# **The budget now covers both files**, because a column moving between them
# saves a reader nothing, and this is the number to keep honest. Measured
# 2026-10-12: 216 KB of geometry plus 76 KB of 29 columns = 292 KB against
# 420, where the same columns interleaved were 350. A sidecar column costs
# about 2 KB gzipped against the ~5 KB it cost as a property, so the headroom
# is now roughly sixty columns rather than four -- which makes geometry
# fidelity, not field count, the thing this budget is really guarding.
BUDGET_TRACTS_WIRE_KB = 420.0      # geometry + sidecar together; see above
BUDGET_WALKSHEDS_WIRE_KB = 150.0   # now 111 KB

# The surface raster is budgeted raw: a PNG is already compressed, so gzip
# cannot move it (132,016 bytes from 132,428 -- 0.3%).
BUDGET_SURFACE_KB = 120.0          # now 62 KB

# --- The under-service score ------------------------------------------------
# The floor below which there is no gap to measure. Ten minutes is where
# planning convention and observed behavior agree: the half-mile rail standard
# is 800 m, which at WALK_SPEED_MPS is 9.9 minutes, and El-Geneidy et al.
# measured 16,014 home-based transit trips in Montreal along the street network
# and put the 85th percentile walk at 873 m to a metro -- 10.8 minutes here.
# https://www.tram.mcgill.ca/Research/Publications/Transit_service_area.pdf
#
# It is a FLOOR, not a cap, and that distinction is the whole finding. Capping
# walk time was measured and does not work: at a 20-minute cap a 104-minute
# walk scores the same as an 18-minute one, 17% of tracts tie at the ceiling,
# and the tie is then broken by density alone -- which is the defect the cap
# was supposed to fix. Percentile-ranking the walk does the same damage more
# quietly: ten times the walk is 1.4 times the rank.
ACCESS_FLOOR_MINUTES = 10.0

# How the need side combines, by default. The browser owns these at runtime --
# they are sliders, and the whole point is that a reader who disagrees can
# move them -- so these are the defaults and the values `verify.py` scores
# against, not a fixed definition.
#
# Zero-vehicle households carry the most weight because they are the only term
# with an agency definition behind them: the MTA characterizes its Equity Areas
# by exactly this variable. Density carries the least because it has
# effectively saturated in New York -- at the Pushkarev and Zupan heavy-rail
# threshold of ~4,100 people/km2, only 5.4% of NYC tracts fall below it, so
# density cannot be what separates one tract from another here.
NEED_WEIGHTS = {"no_vehicle": 0.4, "poverty": 0.35, "density": 0.25}

# --- Geography --------------------------------------------------------------
STATE_FIPS = "36"
# Bronx, Kings (Brooklyn), New York (Manhattan), Queens, Richmond (Staten Island)
COUNTY_FIPS = {
    "005": "Bronx",
    "047": "Brooklyn",
    "061": "Manhattan",
    "081": "Queens",
    "085": "Staten Island",
}

# Which census geography to analyze: "tract" (~2,325) or "bg" (block group, ~6,500).
# Block groups give finer density detail but noisier ACS estimates.
GEO_LEVEL = "tract"

# --- Population source ------------------------------------------------------
# ACS 2023 5-year is the default: most current, and its geography matches the
# 2020 tract boundaries we use. Switch to the decennial for exact counts:
#   CENSUS_DATASET = "2020/dec/pl"; CENSUS_POP_VAR = "P1_001N"; CENSUS_MOE_VAR = None
CENSUS_DATASET = "2023/acs/acs5"
CENSUS_POP_VAR = "B01003_001E"
CENSUS_MOE_VAR = "B01003_001M"
CENSUS_KEY_SIGNUP = "https://api.census.gov/data/key_signup.html"

# Who needs transit, beyond "how many people live here". Each is a count that
# has to be divided by its own denominator -- the universes differ, and
# B17001's is "population for whom poverty status is determined", which is
# smaller than the tract population and not interchangeable with it.
#
# Zero-vehicle households first on purpose: it is the variable the MTA itself
# uses to characterize its Equity Areas (65% zero-vehicle inside against 29%
# outside), which makes it the one need term with an agency definition behind
# it rather than an argument.
CENSUS_EXTRA_VARS = {
    "hh_total": "B08201_001E",     # households, the denominator for below
    "hh_no_vehicle": "B08201_002E",
    "pov_universe": "B17001_001E",  # NOT the tract population
    "pov_below": "B17001_002E",
    "median_income": "B19013_001E",
    # Added 2026-10-12, Phase 5. Each is a count with its own denominator,
    # and each arrives with its margin of error, because an ACS estimate for
    # an area this small without one is a number pretending to be a fact --
    # measured on Bronx tract 000400, the median-income MOE is +/- $40,861.
    #
    # Revealed demand, and the caution is in the name: it is partly an
    # *outcome* of good service, so it belongs in a tooltip and not in a
    # need term. Ranking need by it would score the best-served
    # neighborhoods as the most needy, which is the same circularity the
    # zero-vehicle note below warns about.
    "commute_total": "B08301_001E",
    "commute_transit": "B08301_010E",
    # Age, where dependence on transit is highest at both ends. Single
    # variables on purpose: B01001 would need 20 cells summed and 20 margins
    # propagated to say the same thing.
    "pop_under_18": "B09001_001E",
    "pop_over_65": "B09020_001E",
    # Disability, which is what makes the step-free layer answerable from the
    # demand side rather than only the supply side. B18101 is sex by age by
    # disability status, so "with a disability" is twelve cells -- six age
    # bands, two sexes -- and the sum needs all twelve plus the universe.
    # Note the universe is the *civilian noninstitutionalized* population,
    # which is smaller than the tract population and not interchangeable
    # with it.
    "dis_universe": "B18101_001E",
    **{f"dis_{i}": f"B18101_{i:03d}E" for i in
       (4, 7, 10, 13, 16, 19, 23, 26, 29, 32, 35, 38)},
}

# Every estimate above, with its margin of error. Generated rather than
# listed so a variable cannot be added without its MOE: that pairing is the
# whole reason the margins are here.
CENSUS_EXTRA_MOE_VARS = {
    f"{k}_moe": v[:-1] + "M" for k, v in CENSUS_EXTRA_VARS.items()
}

# When an estimate is too uncertain to show. The Census Bureau's own
# convention is the coefficient of variation -- MOE / 1.645 / estimate, the
# standard error as a share of the estimate -- and 0.30 is the threshold
# agencies commonly use to call an ACS figure unreliable. Nothing is dropped
# at this threshold; the geography is flagged, the map can gray it, and the
# tooltip says why. It deliberately does not reach the score, which uses
# population and walk time and neither of these shares.
CENSUS_CV_UNRELIABLE = 0.30

# --- Data sources (all verified reachable 2026-10-09) -----------------------
SOC_SUBWAY = "https://data.ny.gov/resource/39hk-dx4f.csv?$limit=1000"
SOC_RAIL = "https://data.ny.gov/resource/wxmd-5cpm.csv?$limit=1000"
NYC_TRACTS_GEOJSON = (
    "https://data.cityofnewyork.us/api/geospatial/63ge-mke6?method=export&format=GeoJSON"
)
NYC_BG_GEOJSON = (
    "https://data.cityofnewyork.us/api/geospatial/jtvp-jhwa?method=export&format=GeoJSON"
)
NYC_BOROUGHS_GEOJSON = (
    "https://data.cityofnewyork.us/api/geospatial/gthc-hcne?method=export&format=GeoJSON"
)
# NYC-scoped OSM extract (~153 MB). Much smaller than Geofabrik's statewide NY
# file, and avoids Overpass, which is unreliable from some networks.
OSM_PBF_URL = "https://download.bbbike.org/osm/bbbike/NewYork/NewYork.osm.pbf"

# Scheduled-service feeds. The subway station table carries `gtfs_stop_id`
# directly and the commuter-rail table's `code` is the GTFS `stop_code`, so all
# three joins are key joins -- measured at 496/496 subway parents, 40/40 LIRR
# and 21/21 Metro-North. No name matching anywhere.
GTFS_FEEDS = {
    "subway": "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip",
    "lirr": "https://rrgtfsfeeds.s3.amazonaws.com/gtfslirr.zip",
    "mnr": "https://rrgtfsfeeds.s3.amazonaws.com/gtfsmnr.zip",
}

# The Staten Island Ferry, and it is a separate entry rather than a fourth
# member of GTFS_FEEDS on purpose: stage 3 attaches scheduled service to rows
# of the *station* table, which has no ferry terminals in it, so a feed added
# above would be joined against nothing. Only the transit graph reads this.
#
# **Why a ferry is in a graph that excludes buses.** The bus exclusion is not
# about vehicles, it is about substitution: a bus route is what the city runs
# *instead of* rail in precisely the places this map exists to find, so
# crediting it would hide the finding by construction. The Staten Island Ferry
# substitutes for nothing. It is the only fixed-guideway trunk link across the
# harbor, it runs on its own right of way at a published headway, no proposal
# replaces it, and the alternative to including it is a published claim that
# 495,000 people reach 18,000 of the city's 4.47M jobs -- which is true of
# rail and false of transit. The operative line is dedicated right-of-way
# trunk service against street-running service that shares its lanes with
# traffic, and the ferry is plainly on the first side of it.
#
# Measured from the feed rather than assumed: 416 trips, all of them exactly
# two stops and exactly a **25-minute** crossing, 4 departures an hour each
# way in the 8-9am window and 2 an hour off-peak, fare-free, and
# `direction_id` is empty on every trip -- direction lives in `shape_id`.
# The empty direction is harmless here because a leg is keyed on its from-
# and to-station as well, which separates the two directions anyway.
#
# Sourced through NYC Open Data rather than the nyc.gov path the dataset's own
# metadata advertises, because that one answers 403 to anything without a
# browser user agent. Dataset b57i-ri22, published by NYC DOT; the feed on it
# declares 2026-01-01 to 2028-01-17, so it covers the window stage 4b samples.
GTFS_FERRY_FEED = ("siferry",
                   "https://data.cityofnewyork.us/download/b57i-ri22/"
                   "application%2Fx-zip-compressed")

# The crossing, read off the feed rather than set here: every one of its 416
# trips is exactly 25 minutes, so this is a published fact and not a tunable.
# It is in config only because two stages quote it in their output and one
# probes it, and a number quoted in three places belongs in one.
#
# Worth keeping in view when reading any Staten Island figure: 25 minutes of
# crossing plus a 7.5-minute expected wait at the 8-9am headway is 32.5
# minutes before anyone has walked anywhere, against a 45-minute budget. The
# ferry therefore connects Staten Island to the network without bringing
# Manhattan's jobs inside the budget -- measured, the cheapest such trip is
# 8.5 min of walking plus 32.5 plus 5.4 at the far end, which is 46.4.
FERRY_CROSSING_MINUTES = 25.0

# Workplace-area job counts: LEHD LODES8, WAC segment S000 (all jobs), job
# type JT00 (all jobs). Keyed on the 15-digit census *block* of the
# workplace, so the first 11 characters are the tract GEOID -- which is why no
# crosswalk file is needed. 2022 is the latest year published for New York.
#
# This is the one new data source Phase 4 needs. It is what makes the
# accessibility metric destination-free: instead of naming a hub, every tract
# in the city is a destination weighted by the jobs actually in it.
LODES_YEAR = 2022
LODES_WAC_URL = (
    f"https://lehd.ces.census.gov/data/lodes/LODES8/ny/wac/"
    f"ny_wac_S000_JT00_{LODES_YEAR}.csv.gz"
)

# Bus feeds, 62 MB across six archives. The names look like boroughs and are
# not: measured, `gtfs_q` carries Brooklyn routes and Bronx stops, and all five
# NYCT feeds ship the identical 307-route `routes.txt`. What is actually
# partitioned is trips -- zero trip_id appears in two feeds -- so the six union
# cleanly and stops have to be deduplicated by `stop_id` because they do
# overlap (239 between `gtfs_b` and `gtfs_busco`).
GTFS_BUS_FEEDS = {
    k: f"https://rrgtfsfeeds.s3.amazonaws.com/gtfs_{k}.zip"
    for k in ("b", "m", "q", "bx", "si", "busco")
}

# The Frequent Transit Network, with two decisions that both turned out to
# matter more than the threshold itself.
#
# Per *route*, not per stop. Three routes an hour each, all going somewhere
# different, is not twenty-minute service, and summing them at the stop would
# claim it was. Measured: the combined-frequency definition admits 979 more
# stops than the per-route one.
#
# And across *every* window, not just the peak. Measuring 8-9am alone admitted
# 8,027 of 12,876 stops -- 62% -- which is the "covers the whole city and tells
# you nothing" layer this was supposed to avoid. Almost every NYC bus route is
# frequent at rush hour; the thing a rider can actually rely on is a route that
# is still frequent at noon and at 8pm, which is what "turn up and go" means
# and what the planning term has always meant.
FTN_MAX_HEADWAY_MINUTES = 10.0
FTN_WINDOWS = ("am_peak", "midday", "evening")

# Bus stops are the one layer that could cover the whole city and say nothing:
# there are roughly 15,000 of them. Only the frequent ones ship, and this
# budget is what keeps that promise honest.
BUDGET_BUS_FTN_WIRE_KB = 120.0

# --- Phase 4: travel time over the rail network -----------------------------
# The transit graph is **rail only** -- subway, SIR, LIRR, Metro-North -- and
# buses are in neither it nor the score. The reason is about what the map
# claims rather than what data exists: bus service is what the city provides
# *instead of* rail in precisely the places this map exists to find, so
# letting a bus close the gap would hide the finding by construction. Buses
# keep the role they already have, the FTN layer drawn on top of the score.
# ROADMAP.md Phase 4 carries the full argument.
#
# Nodes are (station, route, direction), not stations. This is a correctness
# fix, not an optimization: with one node per station every route serving it
# shares that node, so a shortest path can change vehicles at no cost and the
# graph quietly claims a connectivity the city does not have -- the precise
# opposite of what this measures. Route-expanding forces a change of vehicle
# through an edge that carries the wait.
TRANSIT_WINDOW = WAIT_WINDOW       # which hour's headways set the boarding wait

# The cumulative-opportunity budget. Forty-five minutes is the usual
# commute-tolerance threshold in the accessibility literature, and it is a
# far weaker choice than naming a destination: measured here, the map's shape
# rescales rather than reorders between 30 and 60 minutes -- Spearman 0.967
# at 30 and 0.983 at 60 against the 45-minute ranking.
#
# **Read that alongside JOBS_THRESHOLD_SHARE below, because this constant has
# one serious defect and it is not arbitrariness.** A fixed time budget
# *right-censors*: every geography that cannot reach much of anything inside
# it piles up near zero and stops being distinguishable. Measured, 1,062 of
# 2,325 tracts -- 46% of the city -- need more than 45 minutes to reach even
# a tenth of the city's jobs, so for nearly half the map this field is
# reporting the censored tail rather than the trip. Breezy Point reads 0.13%
# and Arden Heights 0.12%, which looks like a tie and hides a 35-minute
# difference in what those trips actually cost.
#
# The threshold also sits at the *median* of the quantity it thresholds (the
# median time to reach a tenth of the city's jobs is 42.6 minutes), which is
# the worst place to cut if the far side is what you care about -- and the
# far side is the whole subject of this project.
#
# So this stays, because cumulative opportunity at a fixed budget is the
# comparable measure every peer analysis publishes and it answers a real
# question (how much opportunity is *available* near a home). It is no longer
# the primary one.
JOBS_BUDGET_MINUTES = 45.0

# The same data with the axes swapped, and the primary accessibility measure
# as of 2026-10-11: fix the *jobs* and measure the *time*. "How long until a
# resident can reach a tenth of the city's jobs" has no ceiling, so it keeps
# discriminating exactly where the budget above saturates -- Breezy Point and
# Arden Heights separate into 86 and 121 minutes.
#
# A tenth (about 447,000 jobs) is round rather than derived, and measurement
# says the exact figure does not matter: the per-tract ranking holds at
# Spearman 0.968 to 0.991 across thresholds from 2% to 25%. That is the
# difference between this threshold and the time budget -- a *vertical*
# threshold rescales the answer, a horizontal one truncates it.
#
# It earned the promotion on the project's own test rather than on taste.
# `pop x jobs out of reach` hits 16% at worst-25, *below* the 26% random
# baseline; the uncensored version hits 56%, and unweighted "time to a tenth
# of the jobs" hits 60% at worst-25 and 51% clumped, which ties the shipped
# under-service score at 52% -- with no population term and a Spearman of
# -0.22 against population, so it is not a population ranking in disguise.
# Phase 4 recorded "jobs accessibility is a weak predictor" on the strength
# of that 16%. The dynamic-range rule it inferred was right; the diagnosis
# was wrong. The cutoff was the weak part, not the data.
JOBS_THRESHOLD_SHARE = 0.10

# How far a rider will walk to reach a train, and the same budget on the far
# end. **There is deliberately no separate cap: the total budget is the only
# budget.** A 30-minute walk and a 15-minute ride is a real 45-minute trip,
# and the first version of this capped the walk at 15 minutes, which told
# 625 tracts -- 27% of the city, and precisely the under-served ones this map
# exists to find -- that they had no rail access at all. That is a truncation
# manufacturing the project's own thesis, which is the one bias worth being
# paranoid about here.
#
# The cap is unnecessary as well as harmful, because the budget is
# self-limiting: a 40-minute walk to a station leaves five minutes of riding
# and reaches almost nothing, so long access walks drop out of the result on
# their own rather than by fiat.
TRANSIT_ACCESS_MINUTES = JOBS_BUDGET_MINUTES

# In-station change time where no feed declares one. The subway feed declares
# 463 same-parent transfers whose median is exactly 180 s, so this is that
# median rather than a guess -- it is what MTA itself publishes for the
# stations it bothers to describe, applied to the ones it does not (every LIRR
# and Metro-North stop: both feeds declare same-stop transfers only, and as
# trip-to-trip timed transfers that a static graph cannot use).
TRANSFER_PENALTY_MINUTES = 3.0

# Rail-to-commuter-rail interchanges that no feed declares -- Jamaica,
# Atlantic Terminal, Woodside and a handful more. A radius is needed only for
# these, because transfers.txt is authoritative everywhere else. It is a
# short, hand-checkable list at 400 m, which is why the radius stopped being
# a tunable worth converging: at 150 m the rail graph has 807 candidate pairs
# and at 600 m it has 3,211, but the declared file settles all but a dozen.
INTERCHANGE_MAX_METERS = 400.0

# Jobs within a short walk of a station, which is how the hub list is picked.
# Jobs rather than ridership, because ridership is circular -- the subway is
# radial, so the busiest stations are the radial ones, and using them to pick
# *secondary* hubs selects for exactly the bias secondary hubs exist to
# correct. ROADMAP.md Phase 4 has the ranks that broke the ridership rule.
HUB_WALK_MINUTES = 10.0

# One hub per borough first, then the rest by how many jobs they add that no
# hub already reaches. The borough rule is the presentational half and it has
# to be stated rather than discovered, because New York's jobs are so
# Manhattan-concentrated that an unconstrained jobs ranking returns six
# Manhattan platforms -- measured: Bryant Park, 5 Av/53 St, 28 St, Fulton St,
# Grand Central and Union Square, whose 10-minute walk circles barely overlap.
# That list is a correct answer to "where are the jobs" and a useless answer
# to "how far is your nearest center", which is the question hubs exist for.
#
# ROADMAP.md records that the hub list is a presentation choice rather than a
# modeling commitment; this is where the choice is made. Eight leaves three
# slots for the centers the marginal rule finds on its own after each borough
# has one.
N_HUBS = 8

# The detour factor is transit time over straight-line time at a reference
# speed, so the reference is what 1.0 means. **Measured, not assumed:** the
# median 3-15 km rail trip in New York covers its straight line at 9.2 km/h,
# against a roughly 30 km/h in-vehicle subway speed -- the gap being the wait,
# the stops and the detour itself. Anchoring on the measured value makes the
# field read as "how much worse than a typical New York rail trip", which is
# what a reader comparing tracts wants; the absolute 9.2 km/h goes into
# stats.json as a finding in its own right rather than being hidden in a
# denominator. accessibility.py warns if the measurement drifts off this.
DETOUR_REFERENCE_SPEED_KMH = 9.0
DETOUR_MAX_KM = 15.0           # pairs further apart are not lateral trips
# And a floor, because the ratio explodes on short pairs: at 1 km the
# boarding wait alone is most of the trip, so every pair that close reads as
# a bad detour whatever the network does. Three kilometers is where the ride
# starts to dominate the wait.
DETOUR_MIN_KM = 3.0

# How many stations the elevator-priority table carries. Deliberately longer
# than a top ten: measured, 252 stations are some geography's nearest and the
# worst 15 account for only 17% of the affected population, so a short list
# would read as "fix these and the problem is handled" when the finding is
# that the distribution is flat.
STEPFREE_TABLE_ROWS = 40

# --- Where would one more station help most (Phase 7) ----------------------
# Candidate sites on a lattice, one hypothetical station at a time. The
# spacing is a resolution choice and not a free parameter: at 400 m a
# candidate and its neighbor have walksheds that overlap by most of their
# area, so the surface is smooth and the ranking is stable; much finer buys
# duplicate answers at linear cost, and much coarser can miss a gap entirely,
# since a 10-minute walkshed is only about 810 m of network distance across.
SITING_SPACING_METERS = 400
# How far a new station can reach before it cannot change anyone's answer:
# ACCESS_FLOOR_MINUTES of walking, in meters. Every bounded pass stops here,
# which is what makes this stage seconds rather than hours -- a sample point
# further than this cannot be brought inside the standard by a station at the
# candidate, whatever the rest of the network looks like.
SITING_REACH_METERS = ACCESS_FLOOR_MINUTES * 60.0 * WALK_SPEED_MPS
# How many sites the payload and the page carry. The surface itself ships in
# full; this is the length of the ranked list.
SITING_TABLE_ROWS = 25
# The ranked list keeps only sites whose walksheds do not overlap, which is
# twice the reach apart. Without it the top ten is the same neighborhood ten
# times: adjacent candidates serve almost the same people, so they score
# almost the same, and a list of near-duplicates reads as ten findings when
# it is one. The full surface still ships unsuppressed -- this governs the
# list, not the map.
SITING_MIN_SEPARATION_METERS = 2.0 * SITING_REACH_METERS
BUDGET_SITING_WIRE_KB = 60.0

BUDGET_HUBS_WIRE_KB = 60.0

ATTRIBUTION = {
    "stations": "MTA / NY State Open Data (data.ny.gov)",
    "boundaries": "NYC Dept. of City Planning (NYC Open Data)",
    "population": f"US Census Bureau, {CENSUS_DATASET}",
    "network": "OpenStreetMap contributors (ODbL), via BBBike extract",
    "schedule": "MTA GTFS feeds (subway, LIRR, Metro-North, bus)",
    "jobs": f"US Census Bureau, LEHD LODES8 WAC {LODES_YEAR}",
}

# AGPL-3.0 section 13 requires a deployed instance to "prominently offer all
# users interacting with it remotely" a way to get the corresponding source.
# Set this to the public URL of this repository before deploying; the map shows
# a source link only when it is non-empty, so a local run stays uncluttered.
SOURCE_URL = "https://github.com/k3ntako/transit-gap-map"

# --- Derived file paths -----------------------------------------------------
P_SUBWAY = RAW / "subway_stations.csv"
P_RAIL = RAW / "rail_stations.csv"
P_GEOMS = RAW / f"nyc_{GEO_LEVEL}.geojson"
P_BOROS = RAW / "nyc_boroughs.geojson"
P_PBF = RAW / "NewYork.osm.pbf"
P_CENSUS = RAW / f"census_{GEO_LEVEL}_pop.csv"

P_GTFS = {k: RAW / f"gtfs_{k}.zip" for k in GTFS_FEEDS}
P_GTFS_FERRY = RAW / f"gtfs_{GTFS_FERRY_FEED[0]}.zip"
P_GTFS_BUS = {k: RAW / f"gtfs_bus_{k}.zip" for k in GTFS_BUS_FEEDS}

P_GRAPH = PROCESSED / "walk_graph.npz"
P_BIKE_GRAPH = PROCESSED / "bike_graph.npz"
P_STATIONS = PROCESSED / "stations.parquet"
P_NODE_DIST = PROCESSED / "node_distance.npz"
P_GEO_STATS = PROCESSED / f"{GEO_LEVEL}_stats.parquet"
P_SITING = PROCESSED / "siting.parquet"
P_SERVICE = PROCESSED / "station_service.parquet"
P_BUS_STOPS = PROCESSED / "bus_stop_service.parquet"
P_LODES = RAW / f"lodes_wac_ny_{LODES_YEAR}.csv.gz"
P_JOBS = PROCESSED / f"jobs_{GEO_LEVEL}.parquet"
P_TRANSIT_STATIONS = PROCESSED / "transit_graph.parquet"
P_RIDE_TIME = PROCESSED / "rail_station_time.npz"
P_TRACT_TIME = PROCESSED / f"{GEO_LEVEL}_transit_time.npz"
P_ACCESS = PROCESSED / f"{GEO_LEVEL}_accessibility.parquet"


def bike_minutes(meters):
    """Meters on the cycling network to minutes at BIKE_SPEED_MPS."""
    import numpy as _np
    return _np.asarray(meters, dtype=float) / BIKE_SPEED_MPS / 60.0


def minutes(meters):
    """Meters of walking -> minutes, elementwise-safe for numpy arrays."""
    return meters / WALK_SPEED_MPS / 60.0


def wire_kb(path) -> float:
    """What a reader actually downloads: the gzipped size, in KB.

    Level 6 rather than 9 because that is what a CDN serves. Compressing the
    whole file on every build is affordable -- single-digit milliseconds on
    payloads this size -- and the alternative, trusting a ratio, is how a
    budget quietly stops measuring anything.
    """
    return len(gzip.compress(Path(path).read_bytes(), 6)) / 1024

# A station must attach to a network of at least this many nodes. Stops this
# from happening: a station entrance mapped on a stray footway fragment becomes
# its own "network", serving only itself.
MIN_COMPONENT_NODES = 200

# The surface raster and walksheds take their value from the nearest network
# node, so this cap is tighter than MAX_SNAP_METERS: without it a cell on the
# far bank of a creek could inherit a node's distance from across the water.
SURFACE_MAX_SNAP_METERS = 150
WALKSHED_RESOLUTION_METERS = 50

# --- HTTP cache -------------------------------------------------------------
# Per-source freshness windows, in days. Within the window nothing touches the
# network at all; past it a conditional request asks the server whether the file
# actually changed, so a 304 costs a few hundred bytes instead of re-downloading.
# Tuned to how often each source really moves: the station lists get edited now
# and then, DCP boundaries are near-static, ACS is annual, and the OSM extract
# is rebuilt weekly but matters little for walk-network purposes.
CACHE_TTL_DAYS = {
    "stations": 7,
    "boundaries": 30,
    "census": 30,
    "osm": 30,
    "gtfs": 7,
    "jobs": 365,        # LODES is annual and 2022 is final
}
CACHE_META = RAW / ".http_cache.json"

HTTP_RETRIES = 3           # on 429 / 5xx, honoring Retry-After when sent
HTTP_BACKOFF_BASE = 2.0    # seconds; doubles each attempt
