"""Stage 8: emit the compact payloads the web map loads.

The per-geography data ships as two files rather than one: `tracts.geojson`
carries the geometry and the `geoid` that joins to it, and `tract_cols.json`
carries every column as a parallel array. That split is measured rather than
stylistic -- gzip's window cannot span 2,325 interleaved features, so a
column costs about 2.5x more as a per-feature property than as a contiguous
array -- and `C.BUDGET_TRACTS_WIRE_KB` is asserted against the pair.

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
    "subway_walk_min": "swalk", "stepfree_min": "sf", "bivariate": "bi",
    "bike_min": "bike",
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
    # Phase 5. The quadrant that crosses the first two is *not* here: it is
    # `(nveh > median) + 2 * (pov > median)` and both medians ship in
    # stats.json, so the browser derives it the way it derives the score.
    "transit_commute_pct": "tcom", "under_18_pct": "u18",
    "over_65_pct": "o65", "disability_pct": "dis",
    "need_unreliable": "moeflag",
    "cluster_tracts": "clu", "cluster_pop": "clupop",
    "cluster_id": "cluid",
    # Phase 4, in the three tightest encodings that still say something.
    # `jobs_share` ships in place of the raw reachable count because a
    # percentage with one decimal is 4.3 KB where a seven-digit count is 4.8,
    # and the absolute figure is the share times the city total, which
    # stats.json carries. The per-hub times are not here at all -- eight more
    # columns would be 100 KB against 45 of budget -- they go in hubs.json.
    #
    # The tract's *own* job count was shipped in a first pass and removed:
    # four fields cost 51 KB in this file against 19 KB measured standalone,
    # because gzip's window cannot span 2,325 interleaved features, and the
    # reachable number is the finding while the local one is only context.
    "jobs_share": "j45", "jobs_time": "jt10",
    "hub_min": "hub", "detour": "det",
}

# These columns hold a small number of long, highly repeated strings -- NTA
# names run ~30 characters and recur across every tract in the neighborhood.
# Storing an integer index per feature and the string table once in stats.json
# cuts roughly 200 KB from the payload, which buys back the geometry fidelity
# that hitting the budget by simplification alone would have cost.
INTERNED = ("boro", "nta", "st", "sroutes", "smode", "sst")

# Fields that may legitimately be absent, with the reason. `bike_min` needs
# the cycling graph, which is a second parse of the 153 MB extract -- a
# checkout that has not run `make network-bike` should still build everything
# else rather than failing here, and `app.js` already draws the layer only
# when the field is present.
OPTIONAL = {"bike_min": "the cycling graph (make network-bike)"}

SCHEMA_DOC = {
    "geoid": "11-digit census GEOID",
    "boro": "borough name",
    "nta": "Neighborhood Tabulation Area name",
    "pop": "total population",
    "dens": "population per square km of land",
    "walk": "median network walk time to the nearest train station, minutes",
    "swalk": "same, counting subway + Staten Island Railway only",
    "bike": "minutes to the nearest station by bike, over the OpenStreetMap "
            "cycling network at 3.6 m/s. Every street, not the protected "
            "network only, so read it as an upper bound on what cycling "
            "reaches; and it assumes somewhere to leave the bike, which at "
            "outer stations is the binding constraint",
    "sf": "same, counting only the 219 stations with a step-free route built "
          "as of step_free.as_of -- built, not working today, since live "
          "elevator outages are not in this. null where no step-free station "
          "is reachable on foot at all",
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
    "tcom": "percent of workers who commute by public transit (ACS B08301). "
            "Partly an outcome of good service rather than a measure of "
            "need, which is why it is a tooltip line and not a score term",
    "u18": "percent of residents under 18 (B09001)",
    "o65": "percent of residents 65 and over (B09020)",
    "dis": "percent with a disability, against B18101's civilian "
           "noninstitutionalized universe, which is smaller than the "
           "population",
    "moeflag": "true where the zero-vehicle or poverty share has a "
               "coefficient of variation over 0.30 -- imprecise relative to "
               "its own size, which is not the same as wrong. Flagged rather "
               "than dropped, and it reaches no published figure",
    "clu": "tracts in this tract's contiguous block beyond the 10-minute "
           "standard, 0 if it is inside the standard",
    "clupop": "population of that block",
    "cluid": "which contiguous block, 0 = the most populous, -1 = not past "
             "the standard. Stable between builds because the ids are ranked "
             "by population rather than by row order",
    "j45": "percent of the city's jobs reachable in 45 minutes by rail, the "
           "Staten Island Ferry and on foot; multiply by "
           "accessibility.jobs_total for a count",
    "jt10": "minutes until a tenth of the city's jobs are reachable, to the "
            "nearest minute; null where the reachable set never holds that "
            "many. The uncensored companion to j45: a fixed time budget "
            "saturates near zero for the 1,062 geographies that cannot reach "
            "that many jobs inside it, and this one does not",
    "hub": "minutes to the nearest of the eight job centers, by rail or "
           "ferry, to the nearest minute; null where no path reaches one",
    "det": "detour factor: transit time over straight-line time at the "
           "measured citywide median speed, so 1.0 is a typical trip",
}


def _column(s) -> list:
    # Unannotated `s` on purpose: `out[col]` is typed Series | DataFrame by the
    # stubs, and the cast to satisfy that would be noise around a one-column
    # read. See the pyright baseline note in CLAUDE.md.
    """One column as a JSON array: NaN to null, and no float that is an int.

    `10.0` costs two more bytes per geography than `10` and says the same
    thing to a browser, which over 2,325 geographies and a dozen float
    columns is real. Infinity is null for the same reason NaN is -- JSON has
    no spelling for it and no paint expression can read it.
    """
    if pd.api.types.is_bool_dtype(s):
        return [bool(v) for v in s]
    if pd.api.types.is_integer_dtype(s):
        return [int(v) for v in s]
    if pd.api.types.is_float_dtype(s):
        out = []
        for v in s:
            f = float(v)
            if not np.isfinite(f):
                out.append(None)
            else:
                out.append(int(f) if f.is_integer() else f)
        return out
    return [None if pd.isna(v) else str(v) for v in s]


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

    fields = dict(FIELDS)
    for col, why in OPTIONAL.items():
        if col not in g.columns:
            print(f"  skipped {FIELDS[col]}: no {col} -- needs {why}")
            fields.pop(col, None)
    out = g[["geometry"] + list(fields)].rename(columns=fields)
    # One decimal everywhere. Shipping these at float64 precision -- which is
    # what happens if a new numeric field is simply not added to this tuple --
    # cost 61 KB gzipped for four fields, 200x the 0.3 KB a rounded field costs
    # and most of the way through the payload budget. The map renders to one
    # decimal; 10.81292377475531 is 15 digits of nothing.
    # float64 before rounding, every time: a float32 column rounds to
    # 0.8500000238418579 rather than to 0.85, and the sidecar writes the
    # digits a GeoJSON driver used to hide. Same trap as `j45` below.
    def rounded(col, places):
        return (out[col].astype("float64")
                .replace([np.inf, -np.inf], np.nan).round(places))

    for col in ("walk", "swalk", "sf", "bike", "wlo", "whi", "acc", "wait",
                "stph", "nveh", "pov", "tcom", "u18", "o65", "dis"):
        if col in out.columns:
            out[col] = rounded(col, 1)
    # Two decimals, because the detour factor's middle 80% is 0.79 to 1.28 --
    # rounding it to one would quantize the layer into four values. Three
    # decimals costs 8.0 KB against 4.6 and shows nothing.
    out["det"] = rounded("det", 2)
    # Whole minutes to a hub: 4.5 KB at one decimal against 2.9 at none, for a
    # number nobody reads to the tenth of a minute over a 40-minute trip.
    out["hub"] = rounded("hub", 0)
    # Whole minutes: the field spans 11 to 132, so a decimal place would be
    # three characters per feature to say nothing a reader can act on.
    out["jt10"] = rounded("jt10", 0)
    # float64 before rounding: the column arrives as float32, where 100 * 0.758
    # is 75.800003 and `.round(1)` leaves it that way -- seven digits per
    # feature of pure float noise, which the budget pays for.
    out["j45"] = (100.0 * out["j45"].astype("float64")).round(1)
    for col in ("clu", "clupop"):
        out[col] = out[col].fillna(0).astype("int32")
    out["cluid"] = out["cluid"].fillna(-1).astype("int16")
    out["dens"] = rounded("dens", 0)
    out["pop"] = out["pop"].round(0).astype("int64")
    for col in ("bi", "dc", "wc"):
        out[col] = out[col].fillna(-1).astype("int16")
    out["noacc"] = out["noacc"].fillna(False).astype(bool)
    out["moeflag"] = out["moeflag"].fillna(False).astype(bool)

    lookups = {}
    for col in INTERNED:
        vals = out[col].fillna("").astype(str)
        table = sorted(vals.unique())
        code = {v: i for i, v in enumerate(table)}
        out[col] = vals.map(code).astype("int32")
        lookups[col] = table

    # --- the split: geometry in one file, every column in the other --------
    # The payload debt ROADMAP.md carried since Phase 4, paid on the way in
    # with the step-free field rather than after it. A column costs roughly
    # 2.5x more as a per-feature property than as a parallel array, because
    # gzip's window cannot span 2,325 interleaved features -- so
    # `tracts.geojson` keeps the geometry and the join key and nothing else,
    # and every other column ships as an array in `tract_cols.json`, aligned
    # to an explicit `geoids` list. `app.js:attachCols()` joins them onto the
    # features before `addSource`, which is where the properties the paint
    # expressions read have to exist.
    #
    # Explicit geoids rather than feature order: aligning two files by
    # position is the coupling that breaks silently the first time a geography
    # is dropped, and the join is a Map lookup either way.
    data_cols = [c for c in out.columns if c not in ("geometry", "geoid")]
    sidecar = {
        "geoids": out["geoid"].astype(str).tolist(),
        "interned": list(INTERNED),
        "cols": {c: _column(out[c]) for c in data_cols},
    }
    cols_path = C.WEB_DATA / f"{C.GEO_LEVEL}_cols.json"
    cols_path.write_text(json.dumps(sidecar, separators=(",", ":")))
    cols_wire = C.wire_kb(cols_path)
    print(f"  saved   {cols_path.name} "
          f"({cols_path.stat().st_size / 1024:.0f} KB on disk, "
          f"{cols_wire:.0f} KB gzipped, {len(data_cols)} columns x "
          f"{len(sidecar['geoids'])} {C.GEO_LEVEL}s)")

    path = C.WEB_DATA / f"{C.GEO_LEVEL}s.geojson"
    out[["geometry", "geoid"]].to_file(
        path, driver="GeoJSON", coordinate_precision=C.COORD_PRECISION)
    mb = path.stat().st_size / 1e6
    wire_kb = C.wire_kb(path)
    saved = sum(len(v) for v in lookups.values())
    print(f"  saved   {path.name} ({mb:.2f} MB on disk, {wire_kb:.0f} KB "
          f"gzipped, {len(out)} features; {len(INTERNED)} fields interned into "
          f"{saved} unique strings)")
    total = wire_kb + cols_wire
    print(f"  wire    {total:.0f} KB gzipped for both per-{C.GEO_LEVEL} "
          f"payloads, against a {C.BUDGET_TRACTS_WIRE_KB:.0f} KB budget")
    if total > C.BUDGET_TRACTS_WIRE_KB:
        raise SystemExit(
            f"{path.name} plus {cols_path.name} is {total:.0f} KB gzipped, "
            f"over the {C.BUDGET_TRACTS_WIRE_KB:.0f} KB budget. The budget "
            f"covers both files because a column can move between them "
            f"without a reader downloading one byte less. A new numeric "
            f"field costs ~2 KB in the sidecar and an unrounded one ~6 KB, "
            f"so check the rounding tuple above first; past that, raise "
            f"SIMPLIFY_TOLERANCE_METERS, or check that a new string column "
            f"joined INTERNED rather than shipping {len(out)} copies of "
            f"itself.")
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


def build_hubs(df, stats: dict) -> dict:
    """Per-hub travel times, in their own payload.

    Eight more columns in tracts.geojson would be about 40 KB gzipped against
    45 KB of remaining budget, and they are only read when a reader switches
    hubs. So they ship as parallel arrays aligned to an explicit `geoids`
    list -- explicit because aligning by feature order to another file is the
    kind of coupling that breaks silently the first time a tract is dropped.
    """
    cols = stats.pop("hub_columns", [])
    hubs = stats.get("accessibility", {}).get("hubs", [])
    if not cols or not hubs:
        return {}
    payload = {
        "budget_min": C.JOBS_BUDGET_MINUTES,
        "walk_min": C.HUB_WALK_MINUTES,
        "hubs": [{k: h[k] for k in ("name", "mode", "borough", "lat", "lon",
                                    "jobs_near")} for h in hubs],
        "geoids": df["geoid"].astype(str).tolist(),
        # One array per hub, minutes, one decimal, null where no path
        # reaches it. Rounded here for the same reason every other float in
        # this stage is: unrounded float64 is 15 digits of nothing.
        "minutes": [[None if not np.isfinite(v) else round(float(v), 1)
                     for v in df[c].to_numpy()] for c in cols],
    }
    path = C.WEB_DATA / "hubs.json"
    path.write_text(json.dumps(payload, separators=(",", ":")))
    wire = C.wire_kb(path)
    print(f"  saved   {path.name} ({path.stat().st_size / 1024:.0f} KB on "
          f"disk, {wire:.0f} KB gzipped, {len(cols)} hubs x "
          f"{len(payload['geoids'])} {C.GEO_LEVEL}s)")
    if wire > C.BUDGET_HUBS_WIRE_KB:
        raise SystemExit(
            f"{path.name} is {wire:.0f} KB gzipped, over the "
            f"{C.BUDGET_HUBS_WIRE_KB:.0f} KB budget. This grows linearly in "
            f"C.N_HUBS, so cut hubs before raising the budget -- a hub "
            f"switcher with twenty entries is not a switcher.")
    return {"n_hubs": len(cols), "wire_kb": round(wire, 1)}


def build_siting() -> dict:
    """The marginal-benefit surface as points, plus its ranked list.

    One hypothetical station per candidate site. It ships as points rather
    than as a raster because it *is* discrete -- a lattice of hypotheses, not
    a continuous field -- and drawing it as a smooth surface would imply a
    resolution the method does not have.
    """
    from pipeline.siting import compute as compute_siting, summarize
    res = compute_siting(force=False)
    stats = summarize(res)
    gdf = gpd.GeoDataFrame(
        pd.DataFrame({
            # Person-minutes to the nearest hundred and people to the nearest
            # ten: the lattice spacing is 400 m, so the third digit of either
            # is an artifact of where the lattice happened to land.
            "cut": (res["excess_min_cut"] / 100).round(0).astype("int32") * 100,
            "moved": (res["pop_moved"] / 10).round(0).astype("int32") * 10,
        }),
        geometry=gpd.points_from_xy(res["lon"], res["lat"]), crs=C.CRS_GEO)
    path = C.WEB_DATA / "siting.geojson"
    # Four decimals, not the usual five: these are lattice points 400 m
    # apart, so a tenth of a meter of precision on a hypothesis is 2,325
    # wasted characters. The count of geographies each site touches is left
    # out for the same reason -- it is in the ranked list, where it is read.
    gdf.to_file(path, driver="GeoJSON", coordinate_precision=4)
    wire = C.wire_kb(path)
    print(f"  saved   {path.name} ({path.stat().st_size / 1024:.0f} KB on "
          f"disk, {wire:.0f} KB gzipped, {len(gdf)} candidate sites)")
    if wire > C.BUDGET_SITING_WIRE_KB:
        raise SystemExit(
            f"{path.name} is {wire:.0f} KB gzipped, over the "
            f"{C.BUDGET_SITING_WIRE_KB:.0f} KB budget. This grows as the "
            f"inverse square of SITING_SPACING_METERS, so coarsen the "
            f"lattice rather than raising the budget -- the surface is "
            f"already smooth at 400 m.")
    stats["wire_kb"] = round(wire, 1)
    return stats


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
            f"area on the map is a census {C.GEO_LEVEL}, and its walk time is "
            "the median over a "
            f"{C.TRACT_SAMPLE_SPACING_METERS} m grid of sample points inside it. "
            "Density uses land area, with water excluded."),
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
    from pipeline.verify import (ALIGNMENT_CLUSTER_MARGIN,
                                 ALIGNMENT_MIN_PRECISION, CLUSTER_MIN_TRACTS,
                                 CLUSTER_PROBE_K, SHIPPED_SCORE, alignment)
    hits = alignment(df, verbose=False)
    base = round(hits["_baseline"][25], 3)
    stats["alignment"] = {
        "shipped": SHIPPED_SCORE,
        "baseline": base,
        "floor": ALIGNMENT_MIN_PRECISION,
        "precision": {k: {str(d): round(v, 3) for d, v in h.items()}
                      for k, h in hits.items() if not k.startswith("_")},
        "robust": hits["_robust"],
        # The clumped measure, which is what actually gates the build: a rail
        # line serves a strip of neighborhoods rather than a tract. Its floor
        # is the baseline plus a margin and never the shipped score's own
        # number, because a floor set near that would make agreement with the
        # agencies a target rather than a check. `disagree` is the per-block
        # table, and it is the output worth reading -- a block the score ranks
        # badly that nobody has proposed anything for is not an error.
        "clump": {k: {"precision": (None if not np.isfinite(v["precision"])
                                    else round(v["precision"], 3)),
                      "n_blocks": v["n_blocks"], "n_tracts": v["n_tracts"],
                      "largest": v["largest"]}
                  for k, v in hits["_clump"].items()},
        "clump_rule": {"probe_k": CLUSTER_PROBE_K,
                       "min_tracts": CLUSTER_MIN_TRACTS,
                       "margin": ALIGNMENT_CLUSTER_MARGIN,
                       "floor": round(base + ALIGNMENT_CLUSTER_MARGIN, 3)},
        "disagree": hits["_disagree"],
    }

    print("\nWeb payloads:")
    _mb, lookups = build_geographies(df)
    build_stations()
    stats["bus_ftn"] = build_bus_ftn()
    stats["hubs_payload"] = build_hubs(df, stats)
    stats["siting"] = build_siting()
    build_stats(stats, df, lookups)
    print("\nStage 8 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
