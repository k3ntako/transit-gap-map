"""Single source of truth for paths, CRS, data sources and analysis parameters."""

import gzip
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
WEB_DATA = ROOT / "web" / "data"

for _d in (RAW, PROCESSED, WEB_DATA):
    _d.mkdir(parents=True, exist_ok=True)

# --- Coordinate systems -----------------------------------------------------
# EPSG:26918 = UTM zone 18N, NAD83, units = meters. Covers all five boroughs,
# so lengths and areas come out in true meters without further conversion.
CRS_PROJ = "EPSG:26918"
CRS_GEO = "EPSG:4326"

# --- Analysis parameters ----------------------------------------------------
WALK_SPEED_MPS = 1.35          # ~3.0 mph, standard pedestrian planning speed
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
# The headroom is real but finite: 305 KB against 350 is about nine more fields,
# not ninety. What these numbers catch immediately is the three things that
# genuinely blow the payload up -- finer geometry, a string column that forgot
# to join INTERNED, and switching GEO_LEVEL to block groups. And note that a new
# float field must be added to the rounding tuple in build.py: unrounded float64
# costs 15 KB gzipped per field instead of 5.
BUDGET_TRACTS_WIRE_KB = 350.0      # now 305 KB
BUDGET_WALKSHEDS_WIRE_KB = 150.0   # now 111 KB

# The surface raster is budgeted raw: a PNG is already compressed, so gzip
# cannot move it (132,016 bytes from 132,428 -- 0.3%).
BUDGET_SURFACE_KB = 120.0          # now 62 KB

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

ATTRIBUTION = {
    "stations": "MTA / NY State Open Data (data.ny.gov)",
    "boundaries": "NYC Dept. of City Planning (NYC Open Data)",
    "population": f"US Census Bureau, {CENSUS_DATASET}",
    "network": "OpenStreetMap contributors (ODbL), via BBBike extract",
    "schedule": "MTA GTFS feeds (subway, LIRR, Metro-North)",
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

P_GRAPH = PROCESSED / "walk_graph.npz"
P_STATIONS = PROCESSED / "stations.parquet"
P_NODE_DIST = PROCESSED / "node_distance.npz"
P_GEO_STATS = PROCESSED / f"{GEO_LEVEL}_stats.parquet"
P_SERVICE = PROCESSED / "station_service.parquet"


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
}
CACHE_META = RAW / ".http_cache.json"

HTTP_RETRIES = 3           # on 429 / 5xx, honoring Retry-After when sent
HTTP_BACKOFF_BASE = 2.0    # seconds; doubles each attempt
