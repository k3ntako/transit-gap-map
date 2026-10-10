"""Stage 7: emit the compact payloads the web map loads.

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
}

# These columns hold a small number of long, highly repeated strings -- NTA
# names run ~30 characters and recur across every tract in the neighborhood.
# Storing an integer index per feature and the string table once in stats.json
# cuts roughly 200 KB from the payload, which buys back the geometry fidelity
# that hitting the budget by simplification alone would have cost.
INTERNED = ("boro", "nta", "st", "sroutes", "smode")

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
        g.geometry.values, tolerance=C.SIMPLIFY_TOLERANCE_M, simplify_boundary=True)
    g = g.set_geometry(gpd.GeoSeries(simplified, crs=C.CRS_PROJ))

    invalid = ~g.geometry.is_valid
    if invalid.any():
        print(f"  repairing {invalid.sum()} geometries invalidated by simplification")
        g.loc[invalid, "geometry"] = g.loc[invalid, "geometry"].make_valid()

    g = g.to_crs(C.CRS_GEO)

    out = g[["geometry"] + list(FIELDS)].rename(columns=FIELDS)
    for col in ("walk", "swalk", "wlo", "whi"):
        out[col] = out[col].replace([np.inf, -np.inf], np.nan).round(1)
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
    saved = sum(len(v) for v in lookups.values())
    print(f"  saved   {path.name} ({mb:.2f} MB, {len(out)} features; "
          f"{len(INTERNED)} fields interned into {saved} unique strings)")
    if mb > C.BUDGET_TRACTS_MB:
        raise SystemExit(
            f"{path.name} is {mb:.2f} MB, over the {C.BUDGET_TRACTS_MB} MB budget. "
            f"Raise SIMPLIFY_TOLERANCE_M or drop fields.")
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
        "thresholds_min": list(C.WALK_THRESHOLDS_MIN),
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
            f"{C.TRACT_SAMPLE_SPACING_M} m grid of sample points inside its own "
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

    print("\nWeb payloads:")
    _mb, lookups = build_geographies(df)
    build_stations()
    build_stats(stats, df, lookups)
    print("\nStage 7 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
