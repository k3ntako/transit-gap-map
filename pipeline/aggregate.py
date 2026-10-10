"""Stage 4: turn per-node network distances into per-geography statistics.

Each census geography gets its walk distance from a grid of sample points
clipped to its own polygon, reduced with the median. A single centroid would
misrepresent the long, irregular tracts common in Queens and Staten Island --
and would sometimes land in water or a park.

Run:  python -m pipeline.aggregate [--force]
"""

from __future__ import annotations

import argparse
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

import config as C
from pipeline.distance import compute as compute_dist
from pipeline.graphutil import load_graph

warnings.simplefilter("ignore")


# --- geography + population -------------------------------------------------

def load_geographies() -> gpd.GeoDataFrame:
    for path, hint in ((C.P_GEOMS, "python -m pipeline.fetch"),
                       (C.P_CENSUS, "python -m pipeline.fetch")):
        if not path.exists():
            raise SystemExit(
                f"Missing input: {path.relative_to(C.ROOT)}\n\n"
                f"  Run:  {hint}\n"
                f"  (the Census step needs CENSUS_API_KEY in .env -- free key at\n"
                f"   {C.CENSUS_KEY_SIGNUP})")
    gdf = gpd.read_file(C.P_GEOMS).to_crs(C.CRS_PROJ)
    gdf.columns = [c.lower() for c in gdf.columns]
    if "geoid" not in gdf.columns:
        raise SystemExit(f"Expected a 'geoid' column; got {list(gdf.columns)}")

    gdf = gdf[["geoid", "boroname", "ntaname", "geometry"]].copy()
    gdf["geoid"] = gdf["geoid"].astype(str).str.zfill(11)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]

    # The DCP "water areas excluded" layer is used deliberately, so polygon
    # area is land area -- the right denominator for population density.
    # Recomputed here rather than trusting the published shape_area field,
    # whose units (square feet, state plane) are easy to get wrong.
    gdf["land_area_km2"] = gdf.geometry.area / 1e6

    pop = pd.read_csv(C.P_CENSUS, dtype={"geoid": str})
    pop["geoid"] = pop["geoid"].str.zfill(11)

    merged = gdf.merge(pop[["geoid", "pop", "pop_moe"]].drop_duplicates("geoid"),
                       on="geoid", how="left")

    missing = merged["pop"].isna().sum()
    if missing:
        print(f"  WARNING {missing} geographies had no Census match; treating as 0 pop")
        ex = merged.loc[merged['pop'].isna(), 'geoid'].head(5).tolist()
        print(f"            e.g. {ex}")
    merged["pop"] = merged["pop"].fillna(0).astype(float)

    total = merged["pop"].sum()
    print(f"  joined  {len(merged)} {C.GEO_LEVEL}s, total population {total:,.0f}")
    if not 8_000_000 <= total <= 9_500_000:
        print(f"  WARNING total population {total:,.0f} outside expected 8.0-9.5M")

    merged["density_km2"] = np.where(
        merged["land_area_km2"] > 0,
        merged["pop"] / merged["land_area_km2"].replace(0, np.nan), 0.0)
    merged["is_populated"] = merged["pop"] > 0
    print(f"  populated: {merged['is_populated'].sum()} / {len(merged)} "
          f"({(~merged['is_populated']).sum()} parks, airports, cemeteries etc.)")
    return merged


# --- intra-geography sampling ----------------------------------------------

def sample_points(gdf: gpd.GeoDataFrame, spacing: float) -> gpd.GeoDataFrame:
    """A regular lattice over the study area, tagged with the polygon it lies in.

    Built once globally and spatially joined, which is far cheaper than
    generating a separate grid per polygon.
    """
    minx, miny, maxx, maxy = gdf.total_bounds
    xs = np.arange(minx + spacing / 2, maxx, spacing)
    ys = np.arange(miny + spacing / 2, maxy, spacing)
    gx, gy = np.meshgrid(xs, ys)
    pts = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(gx.ravel(), gy.ravel()), crs=C.CRS_PROJ)
    print(f"  lattice {len(pts):,} points at {spacing:.0f} m spacing")

    joined = gpd.sjoin(pts, gdf[["geoid", "geometry"]], how="inner", predicate="within")
    print(f"  inside  {len(joined):,} points fall within a {C.GEO_LEVEL}")
    return joined


def _refine(small: gpd.GeoDataFrame, spacing: float, measure) -> dict:
    """Per-polygon fine lattice, for geographies the global grid missed.

    Each polygon gets points on its own bounding box at `spacing`, kept where
    they fall inside it. Run only for a handful of polygons, so the per-polygon
    loop costs nothing.
    """
    found = {}
    for geoid, geom in zip(small["geoid"], small.geometry):
        minx, miny, maxx, maxy = geom.bounds
        xs = np.arange(minx, maxx + spacing, spacing)
        ys = np.arange(miny, maxy + spacing, spacing)
        gx, gy = np.meshgrid(xs, ys)
        pts = gpd.GeoDataFrame(
            geometry=gpd.points_from_xy(gx.ravel(), gy.ravel()), crs=small.crs)
        inside = pts[pts.geometry.within(geom)]
        if inside.empty:
            continue
        vals = measure(inside)
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            continue
        found[geoid] = (float(np.median(vals)), float(vals.min()),
                        float(vals.max()), int(vals.size))
    return found


def geography_distance(gdf, dist, serviceable, spacing):
    """Median network walk distance per geography, from sampled points.

    Samples snap only to nodes in a station-bearing component. Snapping to any
    node would let a point attach to an isolated park path or pier and come
    back as having no transit access, when the street it actually sits on is
    a few meters away.
    """
    g = load_graph(C.P_GRAPH)
    x, y = g.x, g.y
    keep = np.flatnonzero(serviceable)
    tree = cKDTree(np.column_stack([x[keep], y[keep]]))

    samples = sample_points(gdf, spacing)

    def measure(points_gdf):
        off, loc = tree.query(
            np.column_stack([points_gdf.geometry.x, points_gdf.geometry.y]), k=1)
        total = dist[keep[loc]] + off    # walk to the network, then along it
        total[off > C.MAX_SNAP_M] = np.inf   # nothing walkable nearby
        return total

    samples = samples.assign(walk_m=measure(samples))
    ok = samples[np.isfinite(samples["walk_m"])]

    agg = ok.groupby("geoid")["walk_m"].agg(
        walk_m="median", walk_m_min="min", walk_m_max="max", n_samples="size")

    out = gdf[["geoid"]].merge(agg, on="geoid", how="left")

    # Polygons too small or too slivered to catch a lattice point get a finer
    # lattice of their own. A single representative point is not good enough:
    # for a multi-part tract it can land on the wrong part -- the tract holding
    # both the Battery and Governors Island resolves to a ferry-only island and
    # reports no walkable rail access, when most of it is beside Bowling Green.
    gap = out["walk_m"].isna()
    if gap.any():
        print(f"  refining {gap.sum()} {C.GEO_LEVEL}s too small for the lattice")
        fine = gdf.loc[gap.to_numpy(), ["geoid", "geometry"]].copy()
        got = _refine(fine, spacing / 5, measure)
        for geoid, (med, lo, hi, n) in got.items():
            m = out["geoid"] == geoid
            out.loc[m, ["walk_m", "walk_m_min", "walk_m_max", "n_samples"]] = \
                [med, lo, hi, n]
        still = out["walk_m"].isna()
        if still.any():
            print(f"  fallback {still.sum()} {C.GEO_LEVEL}s -> representative point")
            reps = gdf.loc[still.to_numpy()].copy()
            reps["geometry"] = reps.geometry.representative_point()
            vals = measure(reps)
            out.loc[still, "walk_m"] = vals
            out.loc[still, "walk_m_min"] = vals
            out.loc[still, "walk_m_max"] = vals
            out.loc[still, "n_samples"] = 0

    unreachable = ~np.isfinite(out["walk_m"])
    if unreachable.any():
        print(f"  WARNING {unreachable.sum()} {C.GEO_LEVEL}s unreachable from any station")
    return out


def attach_nearest_station(df, dist, nearest, serviceable):
    """Name the station serving each geography, for the map's hover tooltip.

    Taken at the representative point rather than by a vote across the sample
    grid: it should answer "which station would I use from here", and a single
    interior point is both the cheaper and the more interpretable answer.
    """
    import pandas as _pd

    st = _pd.read_parquet(C.P_STATIONS)
    g = load_graph(C.P_GRAPH)
    x, y = g.x, g.y
    keep = np.flatnonzero(serviceable)
    tree = cKDTree(np.column_stack([x[keep], y[keep]]))

    reps = df.geometry.representative_point()
    _off, loc = tree.query(np.column_stack([reps.x, reps.y]), k=1)
    sidx = nearest[keep[loc]]

    ok = (sidx >= 0) & np.isfinite(df["walk_m"].to_numpy())
    pick = np.clip(sidx, 0, len(st) - 1)
    # Blank the name where the geography has no walkable access at all: the
    # representative point still snaps to *some* node, and reporting that as
    # "your nearest station" would be wrong. Ellis and Liberty Islands are the
    # real case here -- ferry-only, and uninhabited in the census.
    df["near_station"] = np.where(ok, st["name"].to_numpy()[pick], "")
    df["near_station_mode"] = np.where(ok, st["mode"].to_numpy()[pick], "")
    df["near_station_routes"] = np.where(ok, st["routes"].to_numpy()[pick], "")
    df["no_walk_access"] = ~np.isfinite(df["walk_m"].to_numpy())
    n_na = int(df["no_walk_access"].sum())
    print(f"  nearest station named for {int(ok.sum())}/{len(df)} {C.GEO_LEVEL}s"
          + (f"; {n_na} with no walkable rail access "
             f"(pop {int(df.loc[df['no_walk_access'], 'pop'].sum()):,})" if n_na else ""))
    return df


# --- classification ---------------------------------------------------------

def add_classes(df: pd.DataFrame) -> dict:
    """3x3 bivariate classes from terciles of density and walk time.

    Breakpoints come from populated geographies only: including the parks and
    airports would drag the density terciles toward zero and mislabel ordinary
    residential blocks as dense.
    """
    pop_mask = df["is_populated"] & np.isfinite(df["walk_min"])
    dens = df.loc[pop_mask, "density_km2"]
    walk = df.loc[pop_mask, "walk_min"]

    d_breaks = [float(dens.quantile(q)) for q in (1 / 3, 2 / 3)]
    w_breaks = [float(walk.quantile(q)) for q in (1 / 3, 2 / 3)]

    df["density_class"] = np.digitize(df["density_km2"], d_breaks).clip(0, 2)
    df["walk_class"] = np.digitize(df["walk_min"], w_breaks).clip(0, 2)
    # 0..8, row = density tercile, column = walk tercile.
    df["bivariate"] = df["density_class"] * 3 + df["walk_class"]
    df.loc[~pop_mask, ["density_class", "walk_class", "bivariate"]] = -1

    print(f"  density terciles (per km2): {d_breaks[0]:,.0f} / {d_breaks[1]:,.0f}")
    print(f"  walk-time terciles (min):   {w_breaks[0]:.1f} / {w_breaks[1]:.1f}")
    return {"density": d_breaks, "walk_min": w_breaks}


def straight_line_benchmark(df) -> float:
    """Population share within a half mile of a subway/SIR station, measured
    as the crow flies.

    Reported next to the network figure rather than against an external
    published number. Published shares vary with method and are rarely
    specific about it, whereas this pair is computed here from the same
    population and the same stations, so the only thing that differs is
    straight-line versus routed -- which is the comparison worth showing. It
    also self-checks: the network share can never exceed this one.
    """
    from scipy.spatial import cKDTree as _KD
    from pyproj import Transformer as _T

    st = pd.read_parquet(C.P_STATIONS)
    sub = st[st["mode"].isin(("subway", "sir"))]
    tp = _T.from_crs(C.CRS_GEO, C.CRS_PROJ, always_xy=True)
    sx, sy = tp.transform(sub["lon"].to_numpy(), sub["lat"].to_numpy())
    tree = _KD(np.column_stack([sx, sy]))

    reps = df.geometry.representative_point()
    straight, _ = tree.query(np.column_stack([reps.x, reps.y]), k=1)

    m = (df["is_populated"] & np.isfinite(df["subway_walk_m"])).to_numpy()
    pop = df.loc[m, "pop"].to_numpy()
    return float(pop[straight[m] <= C.HALF_MILE_M].sum() / pop.sum() * 100)


def headline_stats(df, subway_walk_min) -> dict:
    """Population-weighted access figures."""
    m = df["is_populated"] & np.isfinite(df["walk_min"])
    pop = df.loc[m, "pop"].to_numpy()
    walk = df.loc[m, "walk_min"].to_numpy()
    total = pop.sum()

    within = {str(t): float(pop[walk <= t].sum() / total * 100)
              for t in C.WALK_THRESHOLDS_MIN}

    # Benchmark: the commonly cited figure is subway-only, within a half mile.
    sm = df["is_populated"] & np.isfinite(df["subway_walk_min"])
    spop = df.loc[sm, "pop"].to_numpy()
    shalf = C.minutes(C.HALF_MILE_M)
    half_mile_subway = float(
        spop[df.loc[sm, "subway_walk_min"].to_numpy() <= shalf].sum() / spop.sum() * 100)

    by_boro = (df[m].groupby("boroname")
               .apply(lambda g: pd.Series({
                   "pop": g["pop"].sum(),
                   "mean_walk_min": np.average(g["walk_min"], weights=g["pop"]),
                   "pct_within_10": g.loc[g["walk_min"] <= 10, "pop"].sum() / g["pop"].sum() * 100,
               }), include_groups=False)
               .round(2).reset_index().to_dict("records"))

    straight = straight_line_benchmark(df)

    stats = {
        "total_population": int(total),
        "n_geographies": int(len(df)),
        "n_populated": int(df["is_populated"].sum()),
        "pct_within_min": {k: round(v, 1) for k, v in within.items()},
        "mean_walk_min": round(float(np.average(walk, weights=pop)), 2),
        "median_walk_min": round(float(np.median(walk)), 2),
        "pct_within_half_mile_subway_only": round(half_mile_subway, 1),
        "pct_within_half_mile_subway_straight": round(straight, 1),
        "half_mile_detour_penalty": round(straight - half_mile_subway, 1),
        "by_borough": by_boro,
    }

    print()
    print("  Population-weighted access to any train station:")
    for t, v in within.items():
        print(f"    within {t:>2} min walk : {v:5.1f}%")
    print(f"    mean walk time     : {stats['mean_walk_min']:.1f} min")
    print(f"  Within a half mile of a subway/SIR station:")
    print(f"    straight-line      : {straight:5.1f}%")
    print(f"    walking network    : {half_mile_subway:5.1f}%"
          f"   ({straight - half_mile_subway:.1f} points lower)")
    # The network share can never beat the straight-line share: routed distance
    # is always >= the crow-flies distance. If this trips, something is wrong
    # with the projection, the join or the snapping.
    if half_mile_subway > straight + 0.1:
        print("    WARNING network share exceeds straight-line -- impossible; "
              "check projection, join and snapping")
    if not 35 <= half_mile_subway <= 85:
        print("    WARNING outside a plausible range for NYC")
    return stats


def run(force: bool = False):
    print("Geography + population:")
    gdf = load_geographies()

    print("Network distance (all rail modes):")
    dist_all, nearest_all, serv_all = compute_dist(force=False, subway_only=False)
    d_all = geography_distance(gdf, dist_all, serv_all, C.TRACT_SAMPLE_SPACING_M)

    print("Network distance (subway + SIR only):")
    dist_sub, _n, serv_sub = compute_dist(force=False, subway_only=True)
    d_sub = geography_distance(gdf, dist_sub, serv_sub, C.TRACT_SAMPLE_SPACING_M)

    df = gdf.merge(d_all, on="geoid", how="left")
    df["subway_walk_m"] = d_sub["walk_m"].to_numpy()
    df["walk_min"] = C.minutes(df["walk_m"])
    df["subway_walk_min"] = C.minutes(df["subway_walk_m"])
    df = attach_nearest_station(df, dist_all, nearest_all, serv_all)

    print("Classification:")
    breaks = add_classes(df)
    stats = headline_stats(df, df["subway_walk_min"])
    stats["breaks"] = breaks

    # Highest-density geographies with the worst access -- the headline finding.
    deserts = (df[df["is_populated"] & (df["density_class"] == 2)]
               .nlargest(20, "walk_min")
               [["geoid", "boroname", "ntaname", "pop", "density_km2", "walk_min"]])
    stats["transit_deserts"] = deserts.round(1).to_dict("records")
    print("\n  Dense but poorly served (top 5 of 20):")
    for r in stats["transit_deserts"][:5]:
        print(f"    {r['walk_min']:5.1f} min  {r['density_km2']:8,.0f}/km2  "
              f"{str(r['ntaname'])[:34]:34} ({r['boroname']})")

    gpd.GeoDataFrame(df, geometry="geometry", crs=C.CRS_PROJ).to_parquet(C.P_GEO_STATS)
    print(f"\n  saved   {C.P_GEO_STATS.name}")
    return df, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate distances to geographies.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    run(args.force)
    print("\nStage 4 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
