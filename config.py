"""Single source of truth for paths, CRS, data sources and analysis parameters."""

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
WALK_THRESHOLDS_MIN = (5, 10, 15)
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

# Payload budgets, asserted in build.py so regressions can't slip through.
BUDGET_TRACTS_MB = 2.0
BUDGET_SURFACE_KB = 500.0

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

ATTRIBUTION = {
    "stations": "MTA / NY State Open Data (data.ny.gov)",
    "boundaries": "NYC Dept. of City Planning (NYC Open Data)",
    "population": f"US Census Bureau, {CENSUS_DATASET}",
    "network": "OpenStreetMap contributors (ODbL), via BBBike extract",
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

P_GRAPH = PROCESSED / "walk_graph.npz"
P_STATIONS = PROCESSED / "stations.parquet"
P_NODE_DIST = PROCESSED / "node_distance.npz"
P_GEO_STATS = PROCESSED / f"{GEO_LEVEL}_stats.parquet"


def minutes(meters):
    """Meters of walking -> minutes, elementwise-safe for numpy arrays."""
    return meters / WALK_SPEED_MPS / 60.0

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
}
CACHE_META = RAW / ".http_cache.json"

HTTP_RETRIES = 3           # on 429 / 5xx, honoring Retry-After when sent
HTTP_BACKOFF_BASE = 2.0    # seconds; doubles each attempt
