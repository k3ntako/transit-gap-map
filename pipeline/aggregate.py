"""Stage 5: turn per-node network distances into per-geography statistics.

Each census geography gets its walk distance from a grid of sample points
clipped to its own polygon, reduced with the median. A single centroid would
misrepresent the long, irregular tracts common in Queens and Staten Island --
and would sometimes land in water or a park.

Run:  python -m pipeline.aggregate [--force]
"""

from __future__ import annotations

import argparse
import warnings
from datetime import datetime, timezone

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

import config as C
from pipeline.distance import compute as compute_dist
from pipeline.graphutil import load_graph

warnings.simplefilter("ignore")


# --- geography + population -------------------------------------------------

def _share(df, num: str, den: str):
    """num/den as a percentage, NaN where the denominator is zero or absent.

    Zero denominator is "no households here", not "0% of them", and a tract
    with no households should carry no rate at all -- a park reported as 0%
    zero-vehicle would rank as the least transit-dependent place in the city
    rather than as having no answer.
    """
    if num not in df.columns or den not in df.columns:
        return pd.Series(np.nan, index=df.index)
    # np.asarray rather than pd.to_numeric: the latter is typed as a union
    # including scalars, so every method called on its result multiplies into
    # nine pyright errors apiece.
    d = np.asarray(df[den], dtype=float)
    n = np.asarray(df[num], dtype=float)
    ok = d > 0
    out = np.full(d.shape, np.nan)
    np.divide(100.0 * n, d, out=out, where=ok)
    return pd.Series(out, index=df.index)


def _share_moe(df, num: str, den: str):
    """The margin of error on num/den, as percentage points.

    The Census Bureau's formula for a derived proportion, which is the one to
    use here rather than the ratio formula: the numerator is a subset of the
    denominator, so the two are correlated and the ratio formula would
    overstate the margin.

        MOE(p) = (1/den) * sqrt(MOE(num)^2 - p^2 * MOE(den)^2)

    The term under the root can come out negative on a small area, in which
    case the Bureau's instruction is to fall back to the ratio formula, whose
    plus sign cannot. Both appear below, and the fallback is counted.
    """
    cols = (num, den, f"{num}_moe", f"{den}_moe")
    if any(c not in df.columns for c in cols):
        return pd.Series(np.nan, index=df.index), 0
    n = np.asarray(df[num], dtype=float)
    d = np.asarray(df[den], dtype=float)
    mn = np.asarray(df[f"{num}_moe"], dtype=float)
    md = np.asarray(df[f"{den}_moe"], dtype=float)
    ok = (d > 0) & np.isfinite(mn) & np.isfinite(md)
    p = np.divide(n, d, out=np.full(d.shape, np.nan), where=ok)
    inner = mn ** 2 - (p ** 2) * md ** 2
    fell_back = int(np.sum(ok & ~(inner >= 0)))
    inner = np.where(inner >= 0, inner, mn ** 2 + (p ** 2) * md ** 2)
    out = np.full(d.shape, np.nan)
    np.divide(100.0 * np.sqrt(np.maximum(inner, 0.0)), d, out=out, where=ok)
    return pd.Series(out, index=df.index), fell_back


def _cv(est, moe):
    """Coefficient of variation: the standard error as a share of the estimate.

    ACS publishes a 90% margin, so the standard error is MOE / 1.645. This is
    the Bureau's own reliability measure and the reason the margins are
    fetched at all -- an estimate is shown or flagged on this number rather
    than on whether it looks plausible.
    """
    e = np.asarray(est, dtype=float)
    m = np.asarray(moe, dtype=float)
    ok = np.isfinite(e) & np.isfinite(m) & (e > 0)
    out = np.full(e.shape, np.nan)
    np.divide(m / 1.645, e, out=out, where=ok)
    return out


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

    want = ["geoid", "pop", "pop_moe"] + [
        c for c in list(C.CENSUS_EXTRA_VARS) + list(C.CENSUS_EXTRA_MOE_VARS)
        if c in pop.columns]
    merged = gdf.merge(pop[want].drop_duplicates("geoid"),
                       on="geoid", how="left")

    missing = merged["pop"].isna().sum()
    if missing:
        print(f"  WARNING {missing} geographies had no Census match; treating as 0 pop")
        ex = merged.loc[merged['pop'].isna(), 'geoid'].head(5).tolist()
        print(f"            e.g. {ex}")
    merged["pop"] = merged["pop"].fillna(0).astype(float)

    # Shares, each against its own denominator. B17001's universe is
    # "population for whom poverty status is determined", which is smaller
    # than the tract population -- using `pop` instead would understate every
    # poverty rate in the city, by more in tracts with group quarters.
    merged["no_vehicle_pct"] = _share(merged, "hh_no_vehicle", "hh_total")
    merged["poverty_pct"] = _share(merged, "pov_below", "pov_universe")

    # Phase 5. Each share against its own universe, and each with its margin,
    # because at tract level an ACS share without one is a number pretending
    # to be a fact. `dis_*` is twelve cells of B18101 -- six age bands by two
    # sexes -- so its count is a sum and its margin is the root of the sum of
    # squares, which is the Bureau's rule for a sum of estimates.
    dis_cols = [c for c in merged.columns
                if c.startswith("dis_") and not c.endswith("_moe")
                and c != "dis_universe"]
    if dis_cols:
        merged["dis_count"] = sum(
            np.asarray(merged[c], dtype=float) for c in dis_cols)
        merged["dis_count_moe"] = np.sqrt(sum(
            np.asarray(merged[f"{c}_moe"], dtype=float) ** 2
            for c in dis_cols))

    fallbacks = 0
    shares = {
        "no_vehicle_pct": ("hh_no_vehicle", "hh_total"),
        "poverty_pct": ("pov_below", "pov_universe"),
        "transit_commute_pct": ("commute_transit", "commute_total"),
        "under_18_pct": ("pop_under_18", "pop"),
        "over_65_pct": ("pop_over_65", "pop"),
        "disability_pct": ("dis_count", "dis_universe"),
    }
    for name, (num, den) in shares.items():
        if name not in merged.columns:
            merged[name] = _share(merged, num, den)
        moe, fb = _share_moe(merged, num, den)
        merged[f"{name}_moe"] = moe
        fallbacks += fb

    # One reliability flag rather than six: the worst coefficient of variation
    # among the shares the map actually shows. A geography over the threshold
    # is flagged and not dropped -- the estimate is still the best available,
    # and hiding it would quietly replace "uncertain" with "zero".
    cvs = np.vstack([_cv(merged[k], merged[f"{k}_moe"])
                     for k in ("no_vehicle_pct", "poverty_pct")])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        merged["need_cv"] = np.nanmax(cvs, axis=0)
    merged["need_unreliable"] = merged["need_cv"] > C.CENSUS_CV_UNRELIABLE

    if "median_income" in merged.columns:
        inc = np.asarray(merged["median_income"], dtype=float)
        # ACS reports a missing median as 0 here; the fetch step already
        # blanks the -666666666 annotation sentinel.
        merged["median_income"] = np.where(inc > 0, inc, np.nan)
    else:
        merged["median_income"] = np.nan

    nv = merged.loc[merged["pop"] > 0, "no_vehicle_pct"]
    pv = merged.loc[merged["pop"] > 0, "poverty_pct"]
    print(f"  need    zero-vehicle households: median {nv.median():.1f}% "
          f"(p10 {nv.quantile(.1):.1f}%, p90 {nv.quantile(.9):.1f}%); "
          f"{int(nv.isna().sum())} with no denominator")
    print(f"          below poverty:           median {pv.median():.1f}% "
          f"(p10 {pv.quantile(.1):.1f}%, p90 {pv.quantile(.9):.1f}%); "
          f"{int(pv.isna().sum())} with no denominator")
    for k, label in (("transit_commute_pct", "commute by transit"),
                     ("under_18_pct", "under 18"),
                     ("over_65_pct", "65 and over"),
                     ("disability_pct", "with a disability")):
        v = merged.loc[merged["pop"] > 0, k]
        mo = merged.loc[merged["pop"] > 0, f"{k}_moe"]
        print(f"          {label:24} median {v.median():.1f}% "
              f"(p10 {v.quantile(.1):.1f}%, p90 {v.quantile(.9):.1f}%), "
              f"median margin +/-{mo.median():.1f} pts")
    npop = int((merged["pop"] > 0).sum())
    pop_only = merged["pop"] > 0
    for k, label in (("no_vehicle_pct", "zero-vehicle"),
                     ("poverty_pct", "poverty")):
        cv = _cv(merged[k], merged[f"{k}_moe"])[pop_only.to_numpy()]
        over = int(np.nansum(cv > C.CENSUS_CV_UNRELIABLE))
        print(f"  margins {label:13} coefficient of variation over "
              f"{C.CENSUS_CV_UNRELIABLE:.2f} in {over} of {npop} populated "
              f"{C.GEO_LEVEL}s ({100 * over / npop:.1f}%)")
    unrel = int(merged.loc[pop_only, "need_unreliable"].sum())
    # Read this as "imprecise relative to its own size", which is not the
    # same as wrong: a tract at 2% poverty +/- 1.5 points is over the
    # threshold and still clearly a low-poverty tract. It matters when a
    # geography is being *ranked* by the share, which is what the equity
    # weight does -- and that weight defaults to zero.
    print(f"          either one over it in {unrel} ({100 * unrel / npop:.1f}%)"
          f", which are flagged rather than dropped")
    if fallbacks:
        print(f"          {fallbacks} share margins fell back to the ratio "
              f"formula, where the proportion formula went negative")

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
        total[off > C.MAX_SNAP_METERS] = np.inf   # nothing walkable nearby
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


def _station_at_reps(df, nearest, serviceable):
    """The station index each geography's representative point resolves to.

    Shared by both attach_* functions, which were each building their own
    KD-tree over the same node coordinates. Taken at the representative point
    rather than by a vote across the sample grid: it should answer "which
    station would I use from here", and a single interior point is both the
    cheaper and the more interpretable answer.
    """
    g = load_graph(C.P_GRAPH)
    keep = np.flatnonzero(serviceable)
    tree = cKDTree(np.column_stack([g.x[keep], g.y[keep]]))
    reps = df.geometry.representative_point()
    _off, loc = tree.query(np.column_stack([reps.x, reps.y]), k=1)
    return nearest[keep[loc]]


def attach_nearest_station(df, dist, nearest, serviceable):
    """Name the station nearest to each geography, for the hover tooltip."""
    st = pd.read_parquet(C.P_STATIONS)
    sidx = _station_at_reps(df, nearest, serviceable)

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


def attach_served_station(df, nearest, serviceable):
    """Which station the access-cost pass chooses, and its frequency.

    Worth carrying separately from `near_station` because the two disagree for
    a quarter of the city's walk nodes: the frequency-weighted pass often
    prefers a further station with more trains, and "the station you would
    actually use" is a different answer from "the closest one". Showing both is
    what makes the wait term legible rather than mysterious.
    """
    st = pd.read_parquet(C.P_STATIONS)
    svc = pd.read_parquet(C.P_SERVICE)
    sidx = _station_at_reps(df, nearest, serviceable)

    # The weighted pass drops stations with no service in the window, so its
    # station indices address a *shorter* table than stations.parquet. Names
    # come back through station_key, which is stable across both.
    usable = st["station_key"].isin(
        svc.index[svc["wait_min"].notna()].tolist()).to_numpy()
    sub = st.loc[usable].reset_index(drop=True)

    acc = np.asarray(df["access_min"], dtype=float)
    ok = (sidx >= 0) & np.isfinite(acc)
    pick = np.clip(sidx, 0, len(sub) - 1)
    keys = sub["station_key"].to_numpy()[pick]
    tph = svc[f"tph_{C.WAIT_WINDOW}"].reindex(keys).to_numpy()

    df["served_station"] = np.where(ok, sub["name"].to_numpy()[pick], "")
    df["served_tph"] = np.where(ok, tph, np.nan)
    changed = int((ok & (df["served_station"].to_numpy()
                         != df["near_station"].to_numpy())).sum())
    print(f"  access-cost station named for {int(ok.sum())}/{len(df)} "
          f"{C.GEO_LEVEL}s; {changed} differ from the nearest station")
    return df


def margin_stats(df) -> dict:
    """What the survey margins are, in the units the page quotes them in.

    Shipped so the method page can say how uncertain these shares are without
    a figure written in prose, which is the rule for every number on every
    page here.
    """
    pop = df["is_populated"]
    keys = {"nveh": "no_vehicle_pct", "pov": "poverty_pct",
            "tcom": "transit_commute_pct", "u18": "under_18_pct",
            "o65": "over_65_pct", "dis": "disability_pct"}
    median_moe = {}
    for short, col in keys.items():
        mc = f"{col}_moe"
        if mc in df.columns:
            median_moe[short] = round(float(df.loc[pop, mc].median()), 1)
    n = int(pop.sum())
    unrel = int(df.loc[pop, "need_unreliable"].sum()) if \
        "need_unreliable" in df.columns else 0
    return {
        "cv_threshold": C.CENSUS_CV_UNRELIABLE,
        "median_moe": median_moe,
        "n_unreliable": unrel,
        "pct_unreliable": round(100 * unrel / n, 1) if n else None,
    }


def need_cross_stats(df) -> dict:
    """Carless *crossed with* poverty, which is the point of having both.

    Zero-vehicle share alone is ambiguous and quietly circular: the Upper West
    Side has very few cars *because* the transit is excellent, so ranking need
    by carlessness alone scores the best-served neighborhoods in the city as
    the most dependent. Crossed with poverty it separates **carless by
    choice** from **carless without means**, and the second is the strongest
    need signal available in Phase 5.

    Nothing new is shipped for it. The quadrant is
    `(no_vehicle_pct > median) + 2 * (poverty_pct > median)`, both columns are
    already in the payload, and the two medians go into stats.json -- so the
    browser derives the quadrant the way it already derives the score. A
    column costs about 2 KB to save a comparison.
    """
    pop = df["is_populated"].to_numpy()
    nv = np.asarray(df["no_vehicle_pct"], dtype=float)
    pv = np.asarray(df["poverty_pct"], dtype=float)
    ok = pop & np.isfinite(nv) & np.isfinite(pv)
    m_nv = float(np.median(nv[ok]))
    m_pv = float(np.median(pv[ok]))
    quad = (nv > m_nv).astype(int) + 2 * (pv > m_pv).astype(int)

    # Capitalized because these are table row labels on the method page;
    # `app.js:NEED_QUADRANTS` must stay in this order, which the arithmetic
    # above fixes.
    names = ["Car-owning, better off", "Carless by choice",
             "Poorer, car-owning", "Carless without means"]
    out = {"median_no_vehicle_pct": round(m_nv, 1),
           "median_poverty_pct": round(m_pv, 1),
           "quadrants": []}
    excess = (df["walk_min"] - C.ACCESS_FLOOR_MINUTES).clip(lower=0).to_numpy()
    for q, name in enumerate(names):
        sel = ok & (quad == q)
        qpop = float(df.loc[sel, "pop"].sum())
        past = float(df.loc[sel & (excess > 0), "pop"].sum())
        out["quadrants"].append({
            "q": q, "name": name,
            "n_geographies": int(sel.sum()),
            "pop": int(qpop),
            # The number the crossing exists to produce: of the people in
            # this quadrant, how many are also past the walking standard.
            "pop_past_standard": int(past),
            "pct_past_standard": round(100 * past / qpop, 1) if qpop else None,
            "median_walk_min": round(float(
                df.loc[sel, "walk_min"].median()), 1),
        })
    print(f"  crossing zero-vehicle median {m_nv:.1f}%, poverty median "
          f"{m_pv:.1f}%; four quadrants, population past the "
          f"{C.ACCESS_FLOOR_MINUTES:.0f}-minute standard in each:")
    for r in out["quadrants"]:
        print(f"    {r['name']:24} {r['pop']:>9,} people, "
              f"{r['pct_past_standard']:>5}% past it, median walk "
              f"{r['median_walk_min']:>5} min")
    return out


def stepfree_stats(df) -> dict:
    """Two different findings about step-free access, kept apart on purpose.

    The first is **whose nearest station excludes them** -- a count over
    `near_station`, which is cheap and is the figure a reader understands.
    The second is **how much further a step-free rider has to travel**, which
    needs the filtered Dijkstra, because a rider's real nearest step-free
    station may be a mile past the one they cannot use. Reporting the first as
    if it were the second is the mistake this function is shaped to avoid.

    It also builds the elevator-priority station table, which is the output
    this data is actually good for -- and the table's own finding is
    deflating: the distribution is flat, so there is no short list of
    elevators that fixes this. A "top 10 stations" headline would be
    misleading, and the honest claim is that the gap is broad.
    """
    st = pd.read_parquet(C.P_STATIONS)
    ada_of = dict(zip(st["name"].tolist(), st["ada"].astype(bool).tolist()))

    m = (df["is_populated"] & (df["near_station"] != "")).to_numpy()
    total = float(df.loc[m, "pop"].sum())
    near_ada = df.loc[m, "near_station"].map(lambda k: ada_of.get(k, False))
    excluded = ~near_ada.to_numpy()
    pop_excluded = float(df.loc[m, "pop"].to_numpy()[excluded].sum())

    # Per station, over the geographies whose nearest station it is and which
    # is not step-free. Ranked by population, and the ranking is reported with
    # the share the top of it accounts for, because that share is the finding.
    sub = df.loc[m].loc[excluded]
    per = (sub.groupby("near_station")
           .agg(pop=("pop", "sum"), n_geographies=("geoid", "size"))
           .sort_values("pop", ascending=False))
    mode_of = dict(zip(st["name"].tolist(), st["mode"].tolist()))
    # `v["pop"]` and not `v.pop`: on a Series the attribute is the method.
    table = [{"station": k, "mode": mode_of.get(k, ""),
              "pop": int(v["pop"]), "n_geographies": int(v["n_geographies"])}
             for k, v in per.head(C.STEPFREE_TABLE_ROWS).iterrows()]
    top15 = float(per["pop"].head(15).sum())

    out = {
        # "Step-free by design", not "working today": the source flag means a
        # route was built, and MTA publishes live outages a static map cannot
        # represent. The date is the station table's fetch time rather than
        # prose, so it cannot go stale silently.
        "as_of": datetime.fromtimestamp(
            C.P_SUBWAY.stat().st_mtime, timezone.utc).date().isoformat(),
        "n_stations": int(len(st)),
        # `np.asarray` rather than `.astype(bool).sum()`: the stubs type the
        # latter as Series, which `int()` will not take. The pattern is the
        # one pipeline/transit.py uses to stay off the pyright baseline.
        "n_step_free": int(np.asarray(st["ada"], dtype=bool).sum()),
        # [step-free, total] per mode. LIRR reads 33 of 40 and the subway
        # 168 of 475, which is most of why a citywide average is not the
        # number to quote.
        "by_mode": {str(mode): [int(np.asarray(g["ada"], dtype=bool).sum()),
                                int(len(g))]
                    for mode, g in st.groupby("mode")},
        "pop_nearest_not_step_free": int(pop_excluded),
        "pct_nearest_not_step_free": round(100 * pop_excluded / total, 1),
        # The denominator for "no short list of elevators fixes this": how
        # many distinct stations that are *not* step-free are somebody's
        # nearest. The count over all stations (388) answers a different
        # question and would flatter this one.
        "n_stations_nearest_to_someone": int(len(per)),
        "n_stations_nearest_to_anyone": int(
            df.loc[m, "near_station"].nunique()),
        "top15_share": round(100 * top15 / pop_excluded, 1),
        "stations": table,
    }

    # And the travel-cost half, which is the one the filtered pass buys.
    if "stepfree_min" in df.columns:
        sm = (df["is_populated"] & np.isfinite(df["stepfree_min"])
              & np.isfinite(df["walk_min"])).to_numpy()
        pen = (df["stepfree_min"] - df["walk_min"]).to_numpy()[sm]
        wpop = df.loc[sm, "pop"].to_numpy()
        out.update({
            "median_walk_min": round(float(
                np.median(df.loc[sm, "walk_min"].to_numpy())), 1),
            "median_step_free_min": round(float(
                np.median(df.loc[sm, "stepfree_min"].to_numpy())), 1),
            # The median of the per-geography differences, not the
            # difference of the two medians above -- those are 8.4 and 14.4,
            # and only this one is "the typical area's extra walk". Same
            # distinction as median_penalty_min in the commuter-rail block.
            "median_penalty_min": round(float(np.median(pen)), 1),
            "pop_no_step_free_access": int(
                df.loc[df["is_populated"]
                       & ~np.isfinite(df["stepfree_min"]), "pop"].sum()),
            # Beyond the project's own standard, counted twice: once as the
            # city is, once as a rider who cannot use stairs sees it.
            "pct_beyond_standard": round(float(
                wpop[df.loc[sm, "walk_min"].to_numpy()
                     > C.ACCESS_FLOOR_MINUTES].sum() / wpop.sum() * 100), 1),
            "pct_beyond_standard_step_free": round(float(
                wpop[df.loc[sm, "stepfree_min"].to_numpy()
                     > C.ACCESS_FLOOR_MINUTES].sum() / wpop.sum() * 100), 1),
        })

    print(f"  step-free: {out['n_step_free']}/{out['n_stations']} stations; "
          f"{out['pop_nearest_not_step_free']:,} people "
          f"({out['pct_nearest_not_step_free']}%) have a nearest station that "
          f"is not")
    print(f"             flat distribution: "
          f"{out['n_stations_nearest_to_someone']} stations without a "
          f"step-free route are some geography's nearest, and the worst 15 "
          f"account for {out['top15_share']}% of the affected population")
    if "median_penalty_min" in out:
        print(f"             median walk {out['median_walk_min']} min, to a "
              f"step-free station {out['median_step_free_min']} min "
              f"(+{out['median_penalty_min']} at the median geography)")
        print(f"             past the {C.ACCESS_FLOOR_MINUTES:.0f}-minute "
              f"standard: {out['pct_beyond_standard']}% of people, "
              f"{out['pct_beyond_standard_step_free']}% if the station has to "
              f"be step-free")
    return out


def ranks(s) -> pd.Series:
    """Percentile rank in 0..1, ties averaged."""
    return pd.Series(s).rank(pct=True, method="average")


def need_index(df: pd.DataFrame, weights: dict | None = None) -> pd.Series:
    """How much the people in each tract need transit, as a 0..1 index.

    A weighted mean of percentile ranks, which is the right treatment here and
    the wrong one for the deficit side. These three distributions are bounded
    shares and a skewed density; ranking them costs nothing, because none of
    them has a tail that *is* the finding. Walk time does, which is why it
    stays in minutes -- see C.ACCESS_FLOOR_MINUTES.

    A tract missing a denominator (no households, no poverty universe) falls
    back to the terms it does have rather than scoring zero, because zero here
    would read as "nobody needs transit" when it means "nobody lives here".
    """
    w = dict(C.NEED_WEIGHTS if weights is None else weights)
    cols = {"no_vehicle": "no_vehicle_pct", "poverty": "poverty_pct",
            "density": "density_km2"}
    num = pd.Series(0.0, index=df.index)
    den = pd.Series(0.0, index=df.index)
    for key, col in cols.items():
        if col not in df.columns or key not in w:
            continue
        r = ranks(df[col])
        ok = r.notna()
        num = num.add((r * w[key]).where(ok, 0.0), fill_value=0.0)
        den = den.add(pd.Series(float(w[key]), index=df.index).where(ok, 0.0),
                      fill_value=0.0)
    return (num / den.where(den > 0)).astype(float)


def add_score(df: pd.DataFrame) -> dict:
    """Excess person-minutes, plus the terms the browser needs to re-weight it.

    `score = pop x max(walk_min - ACCESS_FLOOR_MINUTES, 0)` -- real units, one
    research-anchored constant, nothing normalized and nothing weighted.
    Chosen by measurement, not by argument: `verify.py` scores nine candidates
    against the eight corridors agencies have actually proposed, and this one
    hits 80% of its worst 25 inside a corridor against a 26% baseline. Nothing
    beat it. Two results from that test are worth carrying here because they
    are counter-intuitive:

    * **The wait penalty makes it worse.** Substituting access cost for walk
      time drops the hit rate at every depth (80 -> 76, 58 -> 52, 43 -> 41).
      Access cost is the better measure of what a rider experiences and the
      worse predictor of where an agency builds, which is coherent: a
      frequency problem is solved by running more trains on track that already
      exists, not by a new line.
    * **The equity variables make it worse too.** Car-free households and
      people below poverty, as counts or as rates, in a product or in a
      weighted blend, all lower it -- carfree households alone to 56%. They
      stay in the payload and in the tooltip because they are well justified
      on their own terms (the MTA defines its own Equity Areas by exactly the
      zero-vehicle variable) and because the reader can weight them in. They
      are not in the default score, and the measurement is why.
    """
    excess = (df["walk_min"] - C.ACCESS_FLOOR_MINUTES).clip(lower=0)
    scored = df["is_populated"] & np.isfinite(df["walk_min"])
    df["excess_min"] = excess.where(scored)
    df["score"] = (df["pop"] * excess).where(scored)
    df["need_index"] = need_index(df).where(scored)

    tot = float(df.loc[scored, "score"].sum())
    beyond = df.loc[scored & (excess > 0), "pop"].sum()
    n_zero = int((scored & (excess <= 0)).sum())
    print(f"  excess  {tot / 1e6:.1f}M person-minutes per one-way trip; "
          f"{beyond:,.0f} people beyond the "
          f"{C.ACCESS_FLOOR_MINUTES:.0f}-minute standard")
    print(f"          {n_zero} of {int(scored.sum())} tracts "
          f"({100 * n_zero / scored.sum():.1f}%) score exactly zero, being "
          f"inside it")
    return {
        "total_excess_person_minutes": int(tot),
        "population_beyond_standard": int(beyond),
        "floor_minutes": C.ACCESS_FLOOR_MINUTES,
        "n_at_standard": n_zero,
        "n_scored": int(scored.sum()),
    }


def add_clusters(gdf: gpd.GeoDataFrame, df: pd.DataFrame) -> dict:
    """Contiguous runs of tracts beyond the standard, and their population.

    A line serves a corridor, not a tract. Forty bad tracts in a row are a
    transit project; one ringed by good ones is a bus-stop problem, a park, or
    a measurement artifact -- and ranked independently the two are
    indistinguishable. This is the cheapest thing in the roadmap that changes
    what the map *says* rather than how it looks.

    Adjacency comes straight from the DCP polygons, which are water-excluded,
    so tracts facing each other across the East River do not touch. Contiguity
    is therefore already *land* contiguity, which is the right default for a
    walking measure -- no bridge edges are added, and that is a decision rather
    than an omission: a bridge is not a walk most people would make.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    beyond = (df["excess_min"].fillna(0) > 0).to_numpy()
    idx = np.flatnonzero(beyond)
    if idx.size == 0:
        df["cluster_tracts"] = 0
        df["cluster_pop"] = 0
        df["cluster_id"] = -1
        return {}

    # Reset the index first: sjoin reports both sides by index *label*, and a
    # positional lookup against a filtered frame's labels is an IndexError
    # waiting for the first tract past row 923.
    geom = gpd.GeoSeries(df["geometry"].to_numpy()[idx], crs=gdf.crs)
    sub = gpd.GeoDataFrame(geometry=geom.reset_index(drop=True))
    pairs = gpd.sjoin(sub, sub, how="inner", predicate="touches")
    r = pairs.index.to_numpy()
    c = pairs["index_right"].to_numpy()
    adj = coo_matrix((np.ones(len(r)), (r, c)), shape=(len(sub), len(sub)))
    n_comp, labels = connected_components(adj, directed=False)

    pop = df["pop"].to_numpy()[idx]
    sizes = np.bincount(labels, minlength=n_comp)
    pops = np.bincount(labels, weights=pop, minlength=n_comp)

    df["cluster_tracts"] = 0
    df["cluster_pop"] = 0
    df["cluster_id"] = -1
    df.iloc[idx, df.columns.get_loc("cluster_tracts")] = sizes[labels]
    df.iloc[idx, df.columns.get_loc("cluster_pop")] = pops[labels].astype(int)

    order = np.argsort(-pops)
    # A stable, meaningful id: 0 is the most populous block, and -1 is "not
    # past the standard at all". `connected_components` numbers its labels by
    # discovery order, which is row order, which would reshuffle the whole
    # column the next time a tract crosses the threshold. Ranking by
    # population instead makes the id comparable between builds and makes it
    # directly useful to the map, where the legend is "the biggest blocks".
    rank = np.empty(n_comp, dtype=np.int64)
    rank[order] = np.arange(n_comp)
    df.iloc[idx, df.columns.get_loc("cluster_id")] = rank[labels]
    print(f"  clusters {n_comp} contiguous blocks of tracts beyond the "
          f"{C.ACCESS_FLOOR_MINUTES:.0f}-minute standard "
          f"({len(idx)} tracts in all):")
    top = []
    for k in order[:5]:
        members = idx[labels == k]
        names = (df.iloc[members]["ntaname"].value_counts().head(3)
                 .index.tolist())
        print(f"    {int(sizes[k]):4} tracts, {int(pops[k]):9,} people  "
              f"{', '.join(str(n)[:24] for n in names)}")
        top.append({"n_tracts": int(sizes[k]), "pop": int(pops[k]),
                    "where": [str(n) for n in names]})
    singles = int((sizes == 1).sum())
    print(f"    {singles} of the {n_comp} blocks are a single isolated tract, "
          f"which is a complaint rather than a project")
    return {"n_clusters": int(n_comp), "n_tracts_beyond": int(len(idx)),
            "largest": top, "n_single_tract_clusters": singles}


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
    # Access terciles ship as breakpoints only, with no per-tract class column
    # to go with them: `acc` is already in the payload, so the browser can band
    # it with a `step` expression for free rather than paying 5 KB for a second
    # copy of information it can derive.
    acc = df.loc[df["is_populated"] & np.isfinite(df["access_min"]), "access_min"]
    a_breaks = [float(acc.quantile(q)) for q in (1 / 3, 2 / 3)]

    df["density_class"] = np.digitize(df["density_km2"], d_breaks).clip(0, 2)
    df["walk_class"] = np.digitize(df["walk_min"], w_breaks).clip(0, 2)
    # 0..8, row = density tercile, column = walk tercile.
    df["bivariate"] = df["density_class"] * 3 + df["walk_class"]
    df.loc[~pop_mask, ["density_class", "walk_class", "bivariate"]] = -1

    print(f"  density terciles (per km2): {d_breaks[0]:,.0f} / {d_breaks[1]:,.0f}")
    print(f"  walk-time terciles (min):   {w_breaks[0]:.1f} / {w_breaks[1]:.1f}")
    print(f"  access-cost terciles (min): {a_breaks[0]:.1f} / {a_breaks[1]:.1f}")
    return {"density": d_breaks, "walk_min": w_breaks, "access_min": a_breaks}


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
    return float(pop[straight[m] <= C.HALF_MILE_METERS].sum() / pop.sum() * 100)


def headline_stats(df, subway_walk_min) -> dict:
    """Population-weighted access figures."""
    m = df["is_populated"] & np.isfinite(df["walk_min"])
    pop = df.loc[m, "pop"].to_numpy()
    walk = df.loc[m, "walk_min"].to_numpy()
    total = pop.sum()

    within = {str(t): float(pop[walk <= t].sum() / total * 100)
              for t in C.WALK_THRESHOLDS_MINUTES}

    # The same thresholds against walk + wait. Published "within N minutes"
    # figures are always walk-only, so these are not comparable to anything
    # external -- which is the point: the gap between the two pairs is what
    # frequency costs, in the only unit a reader already understands.
    am = df["is_populated"] & np.isfinite(df["access_min"])
    apop = df.loc[am, "pop"].to_numpy()
    acc = df.loc[am, "access_min"].to_numpy()
    within_access = {str(t): float(apop[acc <= t].sum() / apop.sum() * 100)
                     for t in C.WALK_THRESHOLDS_MINUTES}

    # Benchmark: the commonly cited figure is subway-only, within a half mile.
    sm = df["is_populated"] & np.isfinite(df["subway_walk_min"])
    spop = df.loc[sm, "pop"].to_numpy()
    swalk = df.loc[sm, "subway_walk_min"].to_numpy()
    shalf = C.minutes(C.HALF_MILE_METERS)
    half_mile_subway = float(spop[swalk <= shalf].sum() / spop.sum() * 100)

    # The same thresholds counting subway + SIR only. This is the honest
    # sensitivity test on the headline, not a second benchmark: LIRR and
    # Metro-North charge a separate, zone-based fare that an unlimited
    # MetroCard does not cover, so every figure above quietly treats a
    # commuter-rail ticket as equivalent to a subway swipe. The gap between
    # the two sets is the size of that assumption.
    within_subway = {str(t): float(spop[swalk <= t].sum() / spop.sum() * 100)
                     for t in C.WALK_THRESHOLDS_MINUTES}

    # And who the assumption is actually about.
    rail_near = df["near_station_mode"].isin(("lirr", "mnr")) & m
    rpop = float(df.loc[rail_near, "pop"].sum())
    rail_dep = {
        "population": int(rpop),
        "pct_of_population": round(100 * rpop / total, 1),
        "n_geographies": int(rail_near.sum()),
        # For these tracts, what discounting commuter rail would cost them.
        "median_walk_min": round(float(df.loc[rail_near, "walk_min"].median()), 1),
        "median_subway_walk_min": round(
            float(df.loc[rail_near, "subway_walk_min"].median()), 1),
        "by_borough": (df[rail_near].groupby("boroname")["pop"].sum()
                       .round(0).astype(int).to_dict()),
        # The median of the per-tract differences, not the difference of the
        # two medians above -- those are 20.1 and 26.4 minutes respectively,
        # and only the first is the thing the sentence claims.
        "median_penalty_min": round(float(
            (df.loc[rail_near, "subway_walk_min"]
             - df.loc[rail_near, "walk_min"]).median()), 1),
    }

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
        "pct_within_min_access": {k: round(v, 1) for k, v in within_access.items()},
        "mean_walk_min": round(float(np.average(walk, weights=pop)), 2),
        "median_walk_min": round(float(np.median(walk)), 2),
        "mean_access_min": round(float(np.average(acc, weights=apop)), 2),
        "median_access_min": round(float(np.median(acc)), 2),
        "mean_wait_min": round(float(np.average(
            df.loc[am, "wait_min"].to_numpy(), weights=apop)), 2),
        "pct_within_min_subway": {k: round(v, 1) for k, v in within_subway.items()},
        "commuter_rail_dependence": rail_dep,
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
    print("  Population-weighted access cost (walk + expected wait):")
    for t, v in within_access.items():
        print(f"    within {t:>2} min       : {v:5.1f}%"
              f"   ({v - within[t]:+5.1f} points vs walking alone)")
    print(f"    mean access cost   : {stats['mean_access_min']:.1f} min"
          f"   (of which {stats['mean_wait_min']:.1f} min is waiting)")
    print("  Counting subway + SIR only (commuter rail charges its own fare):")
    for t, v in within_subway.items():
        print(f"    within {t:>2} min walk : {v:5.1f}%"
              f"   ({v - within[t]:+5.1f} points vs all rail modes)")
    print(f"  Commuter-rail dependence: {rail_dep['population']:,} people "
          f"({rail_dep['pct_of_population']:.1f}%) in "
          f"{rail_dep['n_geographies']} {C.GEO_LEVEL}s have an LIRR or "
          f"Metro-North station as their nearest train")
    print(f"    median walk to it          : "
          f"{rail_dep['median_walk_min']:5.1f} min")
    print(f"    median walk to a subway    : "
          f"{rail_dep['median_subway_walk_min']:5.1f} min")
    print(f"    median per-tract penalty   : "
          f"{rail_dep['median_penalty_min']:5.1f} min if it does not count")
    # Both framings are true and they point opposite ways, so neither should
    # be quoted alone: the citywide share barely moves because these tracts
    # were mostly outside the 10-minute band either way, while for the people
    # in them the penalty is twenty minutes each way.
    for b, v in sorted(rail_dep["by_borough"].items(),
                       key=lambda kv: -kv[1]):
        print(f"    {b:14} {v:8,}")
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
    d_all = geography_distance(gdf, dist_all, serv_all, C.TRACT_SAMPLE_SPACING_METERS)

    print("Network distance (subway + SIR only):")
    dist_sub, _n, serv_sub = compute_dist(force=False, subway_only=True)
    d_sub = geography_distance(gdf, dist_sub, serv_sub, C.TRACT_SAMPLE_SPACING_METERS)

    print("Access cost (walk + expected wait):")
    dist_acc, nearest_acc, serv_acc = compute_dist(
        force=False, subway_only=False, weighted=True)
    d_acc = geography_distance(gdf, dist_acc, serv_acc,
                               C.TRACT_SAMPLE_SPACING_METERS)

    print("Network distance (step-free stations only):")
    dist_sf, _nsf, serv_sf = compute_dist(
        force=False, subway_only=False, step_free=True)
    d_sf = geography_distance(gdf, dist_sf, serv_sf,
                              C.TRACT_SAMPLE_SPACING_METERS)

    df = gdf.merge(d_all, on="geoid", how="left")
    df["subway_walk_m"] = d_sub["walk_m"].to_numpy()
    df["access_m"] = d_acc["walk_m"].to_numpy()
    df["stepfree_m"] = d_sf["walk_m"].to_numpy()
    df["walk_min"] = C.minutes(df["walk_m"])
    df["subway_walk_min"] = C.minutes(df["subway_walk_m"])
    df["access_min"] = C.minutes(df["access_m"])
    df["stepfree_min"] = C.minutes(df["stepfree_m"])
    # The two medians are taken over the same sample grid but reduced
    # independently, so this is the difference of medians and not the median of
    # differences -- and it can go slightly negative where the weighted pass
    # sends a tract's samples to a different station. Clipped at zero because a
    # negative wait is not a thing a reader should ever be shown.
    df["wait_min"] = (df["access_min"] - df["walk_min"]).clip(lower=0)
    df = attach_nearest_station(df, dist_all, nearest_all, serv_all)
    df = attach_served_station(df, nearest_acc, serv_acc)

    # Phase 4. Imported here rather than at module scope because
    # accessibility.py reuses this module's geography loader and sample grid,
    # so a top-level import would be a cycle. Everything it needs is cached by
    # the time this runs, and its own cache means the normal build reads one
    # parquet rather than recomputing a 2,325-square travel-time matrix.
    print("Accessibility (jobs reachable, hubs, detour):")
    from pipeline.accessibility import compute as compute_access
    acc, acc_stats = compute_access(force=force)
    hub_cols = [c for c in acc.columns if c.startswith("hub_")
                and c != "hub_min"]
    df = df.merge(acc, on="geoid", how="left")

    print("Under-service score:")
    score_stats = add_score(df)
    cluster_stats = add_clusters(gdf, df)

    print("Step-free access:")
    stepfree = stepfree_stats(df)

    print("Who lives there:")
    cross = need_cross_stats(df)
    margins = margin_stats(df)

    print("Classification:")
    breaks = add_classes(df)
    stats = headline_stats(df, df["subway_walk_min"])
    stats["breaks"] = breaks
    stats["score"] = score_stats
    stats["clusters"] = cluster_stats
    stats["step_free"] = stepfree
    stats["need_cross"] = cross
    stats["margins"] = margins
    if acc_stats:
        stats["accessibility"] = acc_stats
    stats["hub_columns"] = hub_cols

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
    print("\nStage 5 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
