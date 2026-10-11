"""Stage 8: emit the compact payloads the web map loads.

Run:  python -m pipeline.build
"""

from __future__ import annotations

import argparse
import json
import warnings
from datetime import datetime, timezone

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

import config as C
from pipeline.aggregate import run as aggregate_run
from pipeline.stations import MODE_LABEL

warnings.simplefilter("ignore")

# Short property names: each one repeats once per feature, so with ~2,300
# features the saving is real. Documented in stats.json under "schema".
FIELDS = {
    "geoid": "geoid", "boroname": "boro", "ntaname": "nta",
    "pop": "pop", "density_km2": "dens", "walk_min": "walk",
    "subway_walk_min": "swalk", "bivariate": "bi",
    "density_class": "dc", "walk_class": "wc",
    "near_station": "st", "near_station_routes": "sroutes",
    "near_station_mode": "smode", "walk_min_lo": "wlo", "walk_min_hi": "whi",
    "no_walk_access": "noacc",
    "access_min": "acc", "wait_min": "wait",
    "served_station": "sst", "served_tph": "stph",
    # The score's inputs, not the score. `excess` is max(walk - 10, 0) and
    # `score` is pop x excess, both derivable in the browser from fields that
    # already ship, so neither is sent: at ~5 KB gzipped per field, shipping a
    # derivable column is 5 KB spent to save a multiplication.
    "no_vehicle_pct": "nveh", "poverty_pct": "pov",
    "cluster_tracts": "clu", "cluster_pop": "clupop",
}

# These columns hold a small number of long, highly repeated strings -- NTA
# names run ~30 characters and recur across every tract in the neighborhood.
# Storing an integer index per feature and the string table once in stats.json
# cuts roughly 200 KB from the payload, which buys back the geometry fidelity
# that hitting the budget by simplification alone would have cost.
INTERNED = ("boro", "nta", "st", "sroutes", "smode", "sst")

SCHEMA_DOC = {
    "geoid": "11-digit census GEOID",
    "boro": "borough name",
    "nta": "Neighborhood Tabulation Area name",
    "pop": "total population",
    "dens": "population per square km of land",
    "walk": "median network walk time to the nearest train station, minutes",
    "swalk": "same, counting subway + Staten Island Railway only",
    "bi": "bivariate class 0-8 (density tercile * 3 + walk tercile), -1 if unpopulated",
    "dc": "density tercile 0-2, -1 if unpopulated",
    "wc": "walk-time tercile 0-2, -1 if unpopulated",
    "st": "name of the nearest station",
    "sroutes": "routes or branch serving it",
    "smode": "subway | sir | lirr | mnr",
    "wlo": "shortest walk time among the tract's sample points",
    "whi": "longest walk time among the tract's sample points",
    "noacc": "true where no station is reachable on foot at all (ferry-only islands)",
    "acc": "median access cost: walk time plus the expected wait, minutes",
    "wait": "the wait part of acc, minutes (acc - walk, floored at 0)",
    "sst": "name of the station the access-cost measure picks, which for 224 "
           "tracts is not the nearest one",
    "stph": "trains per hour per direction at that station, weekday 8-9am",
    "nveh": "percent of households with no vehicle available (ACS B08201)",
    "pov": "percent below the poverty line, against B17001's own universe",
    "clu": "tracts in this tract's contiguous block beyond the 10-minute "
           "standard, 0 if it is inside the standard",
    "clupop": "population of that block",
}


def build_geographies(df: gpd.GeoDataFrame) -> int:
    """Simplified, trimmed GeoJSON of the census geographies."""
    g = df.copy()
    g["walk_min_lo"] = C.minutes(g["walk_m_min"])
    g["walk_min_hi"] = C.minutes(g["walk_m_max"])

    # Coverage-aware simplification: shared boundaries stay identical, so
    # neighboring tracts do not pull apart into slivers the way independent
    # per-polygon simplification would make them.
    simplified = shapely.coverage_simplify(
        g.geometry.values, tolerance=C.SIMPLIFY_TOLERANCE_METERS, simplify_boundary=True)
    g = g.set_geometry(gpd.GeoSeries(simplified, crs=C.CRS_PROJ))

    invalid = ~g.geometry.is_valid
    if invalid.any():
        print(f"  repairing {invalid.sum()} geometries invalidated by simplification")
        g.loc[invalid, "geometry"] = g.loc[invalid, "geometry"].make_valid()

    g = g.to_crs(C.CRS_GEO)

    out = g[["geometry"] + list(FIELDS)].rename(columns=FIELDS)
    # One decimal everywhere. Shipping these at float64 precision -- which is
    # what happens if a new numeric field is simply not added to this tuple --
    # cost 61 KB gzipped for four fields, 200x the 0.3 KB a rounded field costs
    # and most of the way through the payload budget. The map renders to one
    # decimal; 10.81292377475531 is 15 digits of nothing.
    for col in ("walk", "swalk", "wlo", "whi", "acc", "wait", "stph",
                "nveh", "pov"):
        out[col] = out[col].replace([np.inf, -np.inf], np.nan).round(1)
    for col in ("clu", "clupop"):
        out[col] = out[col].fillna(0).astype("int32")
    out["dens"] = out["dens"].replace([np.inf, -np.inf], np.nan).round(0)
    out["pop"] = out["pop"].round(0).astype("int64")
    for col in ("bi", "dc", "wc"):
        out[col] = out[col].fillna(-1).astype("int16")
    out["noacc"] = out["noacc"].fillna(False).astype(bool)

    lookups = {}
    for col in INTERNED:
        vals = out[col].fillna("").astype(str)
        table = sorted(vals.unique())
        code = {v: i for i, v in enumerate(table)}
        out[col] = vals.map(code).astype("int32")
        lookups[col] = table

    path = C.WEB_DATA / f"{C.GEO_LEVEL}s.geojson"
    out.to_file(path, driver="GeoJSON", coordinate_precision=C.COORD_PRECISION)
    mb = path.stat().st_size / 1e6
    wire_kb = C.wire_kb(path)
    saved = sum(len(v) for v in lookups.values())
    print(f"  saved   {path.name} ({mb:.2f} MB on disk, {wire_kb:.0f} KB "
          f"gzipped, {len(out)} features; {len(INTERNED)} fields interned into "
          f"{saved} unique strings)")
    if wire_kb > C.BUDGET_TRACTS_WIRE_KB:
        raise SystemExit(
            f"{path.name} is {wire_kb:.0f} KB gzipped, over the "
            f"{C.BUDGET_TRACTS_WIRE_KB:.0f} KB budget. A new numeric field costs "
            f"~5 KB here and an unrounded one ~15 KB, so check the rounding "
            f"tuple above first; past that, raise SIMPLIFY_TOLERANCE_METERS, or "
            f"check that a new string column joined INTERNED rather than "
            f"shipping {len(out)} copies of itself.")
    return mb, lookups


def build_stations() -> None:
    st = pd.read_parquet(C.P_STATIONS)
    gdf = gpd.GeoDataFrame(
        st[["name", "mode", "routes", "borough", "ada", "complex_id"]],
        geometry=gpd.points_from_xy(st["lon"], st["lat"]), crs=C.CRS_GEO)
    gdf["mode_label"] = gdf["mode"].map(MODE_LABEL)
    gdf["ada"] = gdf["ada"].astype(bool)
    path = C.WEB_DATA / "stations.geojson"
    gdf.to_file(path, driver="GeoJSON", coordinate_precision=C.COORD_PRECISION)
    print(f"  saved   {path.name} ({path.stat().st_size / 1024:.0f} KB, {len(gdf)} stations)")


def build_bus_ftn() -> dict:
    """The Frequent Transit Network as points, and only those points.

    All 12,942 bus stops would be a layer covering every street in the city.
    The 2,368 that hold a 10-minute headway all day are a network with a
    shape, which is the only version worth drawing. `C.FTN_WINDOWS` carries
    the argument.
    """
    bus = pd.read_parquet(C.P_BUS_STOPS)
    ftn = bus.loc[bus["is_ftn"]].copy()
    gdf = gpd.GeoDataFrame(
        pd.DataFrame({
            "name": ftn["stop_name"].astype(str).str.title(),
            "route": ftn["best_route"].astype(str),
            "tph": ftn["best_tph"].round(1),
            "headway": ftn["best_headway_min"].round(1),
            "nroutes": ftn["n_routes"].astype("int16"),
        }),
        geometry=gpd.points_from_xy(ftn["stop_lon"], ftn["stop_lat"]),
        crs=C.CRS_GEO)
    path = C.WEB_DATA / "bus_ftn.geojson"
    gdf.to_file(path, driver="GeoJSON", coordinate_precision=C.COORD_PRECISION)
    kb = path.stat().st_size / 1024
    wire_kb = C.wire_kb(path)
    print(f"  saved   {path.name} ({kb:.0f} KB on disk, {wire_kb:.0f} KB "
          f"gzipped, {len(gdf)} of {len(bus)} bus stops)")
    if wire_kb > C.BUDGET_BUS_FTN_WIRE_KB:
        raise SystemExit(
            f"{path.name} is {wire_kb:.0f} KB gzipped, over the "
            f"{C.BUDGET_BUS_FTN_WIRE_KB:.0f} KB budget. This layer exists only "
            f"because it is a selective one: check FTN_MAX_HEADWAY_MINUTES and "
            f"FTN_WINDOWS before raising the budget, because a bus-stop layer "
            f"that covers the city is the thing this was built to avoid.")
    return {
        "n_stops": int(len(bus)),
        "n_ftn": int(len(gdf)),
        "max_headway_min": C.FTN_MAX_HEADWAY_MINUTES,
        "windows": list(C.FTN_WINDOWS),
        "n_ftn_peak_only": int((bus["peak_tph"]
                                >= 60.0 / C.FTN_MAX_HEADWAY_MINUTES).sum()),
    }


def build_stats(stats: dict, df, lookups: dict) -> None:
    sheds = C.WEB_DATA / "walksheds.geojson"
    if sheds.exists():
        s = json.loads(sheds.read_text())
        stats["walkshed_area_km2"] = {
            str(f["properties"]["threshold_min"]): f["properties"]["area_km2"]
            for f in s["features"]}

    stats["lookups"] = lookups
    stats.update({
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "geo_level": C.GEO_LEVEL,
        "walk_speed_mps": C.WALK_SPEED_MPS,
        "thresholds_min": list(C.WALK_THRESHOLDS_MINUTES),
        "census_dataset": C.CENSUS_DATASET,
        "attribution": C.ATTRIBUTION,
        "license": "AGPL-3.0-or-later",
        "source_url": C.SOURCE_URL,
        "schema": SCHEMA_DOC,
        "method": (
            "Walk times are shortest-path distances along the OpenStreetMap "
            "pedestrian network, not straight lines, computed with a single "
            "multi-source Dijkstra pass from every station at once. Each "
            f"{C.GEO_LEVEL} is measured as the median over a "
            f"{C.TRACT_SAMPLE_SPACING_METERS} m grid of sample points inside its own "
            "polygon. Density uses land area, with water excluded."),
    })
    path = C.WEB_DATA / "stats.json"
    path.write_text(json.dumps(stats, indent=2, default=str))
    print(f"  saved   {path.name} ({path.stat().st_size / 1024:.0f} KB)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Emit web payloads.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    import pipeline.palette as P
    P.main()

    df, stats = aggregate_run(args.force)

    # The alignment numbers go into the payload so the map can state its own
    # external validity rather than leaving it in a terminal nobody reads.
    # `verify.py` owns the test; this only embeds the result.
    from pipeline.verify import ALIGNMENT_MIN_PRECISION, SHIPPED_SCORE, alignment
    hits = alignment(df, verbose=False)
    stats["alignment"] = {
        "shipped": SHIPPED_SCORE,
        "baseline": round(hits["_baseline"][25], 3),
        "floor": ALIGNMENT_MIN_PRECISION,
        "precision": {k: {str(d): round(v, 3) for d, v in h.items()}
                      for k, h in hits.items() if not k.startswith("_")},
        "robust": hits["_robust"],
    }

    print("\nWeb payloads:")
    _mb, lookups = build_geographies(df)
    build_stations()
    stats["bus_ftn"] = build_bus_ftn()
    build_stats(stats, df, lookups)
    print("\nStage 8 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
