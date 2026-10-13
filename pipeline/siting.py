"""Stage 9: where would one more station help most?

Phase 7's first question, and the architecture makes it cheap. For each
candidate site on a lattice, put one hypothetical station there and ask what
changes. Two things are reported, and the second is the one to rank by:

* **people moved inside the standard** -- how many residents go from past
  `C.ACCESS_FLOOR_MINUTES` of walking to inside it;
* **excess person-minutes removed** -- the same arithmetic as the shipped
  under-service score, so a site's benefit is quoted in the unit the rest of
  this project is measured in rather than in a new one.

Three things keep it fast enough to run on a laptop. A new station can only
change an answer within `C.SITING_REACH_METERS` of itself, so every pass is a
*bounded* Dijkstra that stops there -- 12 ms each rather than a second. The
existing nearest-station distance is already on disk, so the comparison is
against a vector we have rather than a recomputation. And only the tracts
holding an affected sample point need a new median.

What this is not: a line. It ranks points, and a line is a connected sequence
of them -- see Phase 7 in ROADMAP.md for why that is a different and much
harder problem, and why the honest output at this level is an ordering rather
than a cost per rider.

Run:  python -m pipeline.siting [--force]
"""

from __future__ import annotations

import argparse
import time
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

import config as C
from pipeline.aggregate import load_geographies, sample_points
from pipeline.distance import compute as compute_dist
from pipeline.graphutil import load_graph

warnings.simplefilter("ignore")

# How many candidates to solve per Dijkstra call. The output is one float64
# row per source over every node in the graph, so 32 rows is about 470 MB
# transient -- batching further buys nothing and starts to swap.
BATCH = 32


def _samples(gdf, g, serviceable):
    """Every 25 m sample point, with the node it snaps to and its offset.

    The same lattice `aggregate.geography_distance` measures tracts on, which
    is what makes the baseline here comparable to the published walk time
    rather than merely similar to it. `_check_baseline` proves that.
    """
    keep = np.flatnonzero(serviceable)
    tree = cKDTree(np.column_stack([g.x[keep], g.y[keep]]))
    pts = sample_points(gdf, C.TRACT_SAMPLE_SPACING_METERS)
    off, loc = tree.query(
        np.column_stack([pts.geometry.x, pts.geometry.y]), k=1)
    node = keep[loc]
    # A sample with nothing walkable nearby is dropped rather than carried as
    # infinite: no station anywhere rescues a point that cannot reach the
    # network, so it is not a site's fault and not a site's credit.
    ok = off <= C.MAX_SNAP_METERS
    print(f"  samples {int(ok.sum()):,} of {len(pts):,} within "
          f"{C.MAX_SNAP_METERS} m of the network")
    return (np.asarray(pts["geoid"])[ok], node[ok], off[ok])


def _by_tract(geoids, order_index):
    """Samples grouped by tract: which tract each is in, plus slice bounds.

    `code[i]` is the tract of sample `i`, and `order[starts[t]:ends[t]]` are
    the samples of tract `t`. Both are needed and neither derives cheaply
    from the other inside the candidate loop, which runs thousands of times.
    """
    code = pd.Series(geoids).map(lambda k: order_index.get(k, -1)).to_numpy()
    keep = code >= 0
    code = code[keep]
    order = np.argsort(code, kind="stable")
    code_sorted = code[order]
    starts = np.searchsorted(code_sorted, np.arange(len(order_index)), "left")
    ends = np.searchsorted(code_sorted, np.arange(len(order_index)), "right")
    return keep, code, order, starts, ends


def _node_index(node, n_nodes):
    """node -> the samples snapped to it, as a CSR-style pair of arrays."""
    order = np.argsort(node, kind="stable")
    node_sorted = node[order]
    starts = np.searchsorted(node_sorted, np.arange(n_nodes), "left")
    ends = np.searchsorted(node_sorted, np.arange(n_nodes), "right")
    return order, starts, ends


def _medians(base, order, starts, ends):
    """Per-tract median of `base` over each tract's samples, NaN where none."""
    out = np.full(len(starts), np.nan)
    vals = base[order]
    for i, (a, b) in enumerate(zip(starts, ends)):
        if b <= a:
            continue
        v = vals[a:b]
        v = v[np.isfinite(v)]
        if v.size:
            out[i] = np.median(v)
    return out


def _check_baseline(df, med_m) -> None:
    """The gate: this stage's baseline must be the published walk time.

    Not "close to" -- the same lattice, the same snapping rule and the same
    median, so the two should agree to floating point. If they do not, the
    scenario numbers below are measuring a different city from the one on the
    map, and a ranking of sites computed against the wrong baseline would
    look perfectly reasonable.
    """
    pub = np.asarray(df["walk_m"], dtype=float)
    both = np.isfinite(pub) & np.isfinite(med_m)
    diff = np.abs(pub[both] - med_m[both])
    worst = float(diff.max()) if diff.size else 0.0
    n_mismatch = int((~both & (np.isfinite(pub) | np.isfinite(med_m))).sum())
    print(f"  baseline matches the published walk distance on "
          f"{int(both.sum())}/{len(df)} {C.GEO_LEVEL}s, worst difference "
          f"{worst:.3f} m")
    # A handful of tracts are measured by `aggregate`'s finer refinement
    # lattice or by a representative point, which this stage does not repeat;
    # those are the expected mismatches and they are named rather than
    # tolerated silently.
    if n_mismatch:
        print(f"          {n_mismatch} {C.GEO_LEVEL}s have one side only "
              f"(refined or fallback-measured upstream); they are excluded")
    if worst > 1.0:
        raise SystemExit(
            f"Siting baseline differs from the published walk distance by "
            f"{worst:.1f} m. The two are built from the same lattice and the "
            f"same snapping rule, so any difference means one of them "
            f"changed -- fix that before reading any site ranking.")


def compute(force: bool = False):
    out = C.P_SITING
    if out.exists() and not force:
        print(f"  cached  {out.name}")
        return pd.read_parquet(out)

    t0 = time.time()
    g = load_graph(C.P_GRAPH)
    got = compute_dist(force=False)
    dist, serviceable = got[0], got[2]
    # The node count from the coordinate array rather than `csr.shape`,
    # which the scipy stubs type as optional.
    n = int(g.x.size)

    gdf = load_geographies()
    stats_df = gpd.read_parquet(C.P_GEO_STATS)[["geoid", "walk_m", "pop"]]
    gdf = gpd.GeoDataFrame(
        gdf.merge(stats_df, on="geoid", how="left", suffixes=("", "_s")),
        geometry="geometry", crs=C.CRS_PROJ)

    geoids, node, off = _samples(gdf, g, serviceable)
    index = {k: i for i, k in enumerate(gdf["geoid"].tolist())}
    keep, s_tract, t_order, t_start, t_end = _by_tract(geoids, index)
    node, off = node[keep], off[keep]
    base = dist[node] + off

    med_base_m = _medians(base, t_order, t_start, t_end)
    _check_baseline(gdf, med_base_m)

    pop = np.asarray(gdf["pop"], dtype=float)
    floor_m = C.ACCESS_FLOOR_MINUTES * 60.0 * C.WALK_SPEED_MPS
    excess_base = np.maximum(med_base_m - floor_m, 0.0)
    excess_base = np.where(np.isfinite(excess_base), excess_base, 0.0)
    served_base = med_base_m <= floor_m

    n_order, n_start, n_end = _node_index(node, n)

    # --- candidate sites ---------------------------------------------------
    # The lattice is clipped to the geographies, so a candidate always sits on
    # land somebody lives on or beside, and then snapped to the walk network
    # the way a real station is. Duplicates after snapping are dropped: two
    # lattice points either side of a park resolve to the same door.
    cand_pts = sample_points(gdf, C.SITING_SPACING_METERS)
    sizes = np.bincount(g.component, minlength=g.component.max() + 1)
    substantive = np.flatnonzero(sizes[g.component] >= C.MIN_COMPONENT_NODES)
    tree = cKDTree(np.column_stack([g.x[substantive], g.y[substantive]]))
    c_off, c_loc = tree.query(
        np.column_stack([cand_pts.geometry.x, cand_pts.geometry.y]), k=1)
    cand = substantive[c_loc][c_off <= C.MAX_SNAP_METERS]
    cand = np.unique(cand)
    print(f"  sites   {len(cand):,} candidates at "
          f"{C.SITING_SPACING_METERS} m spacing, snapped and deduplicated")

    moved = np.zeros(len(cand))
    cut = np.zeros(len(cand))
    touched = np.zeros(len(cand), dtype=np.int32)

    new_base = base.copy()
    t1 = time.time()
    for b0 in range(0, len(cand), BATCH):
        chunk = cand[b0:b0 + BATCH]
        d = dijkstra(g.csr, indices=chunk, directed=False,
                     limit=C.SITING_REACH_METERS)
        for j in range(len(chunk)):
            dj = d[j]
            reached = np.flatnonzero(np.isfinite(dj))
            if reached.size == 0:
                continue
            # The samples snapped to any reached node, and the walk each one
            # would have to a station at this candidate.
            sel = np.concatenate([n_order[n_start[r]:n_end[r]]
                                  for r in reached]) if reached.size else []
            if len(sel) == 0:
                continue
            sel = np.asarray(sel)
            alt = dj[node[sel]] + off[sel]
            better = alt < base[sel]
            if not better.any():
                continue
            sel, alt = sel[better], alt[better]

            # Only the tracts holding an improved sample can move, so only
            # their medians are recomputed. `codes` maps a sample back to the
            # tract it sits in.
            # Write into one scratch array and undo the writes afterwards,
            # rather than copying 1.25M floats per candidate -- that copy is
            # 10 MB a time and this loop runs thousands of times.
            new_base[sel] = alt
            hit = np.unique(s_tract[sel])
            tot_moved = 0.0
            tot_cut = 0.0
            for ti in hit:
                a, bb = t_start[ti], t_end[ti]
                v = new_base[t_order[a:bb]]
                v = v[np.isfinite(v)]
                if v.size == 0:
                    continue
                m_new = float(np.median(v))
                ex_new = max(m_new - floor_m, 0.0)
                p = pop[ti]
                if not np.isfinite(p) or p <= 0:
                    continue
                tot_cut += p * max(excess_base[ti] - ex_new, 0.0)
                if not served_base[ti] and m_new <= floor_m:
                    tot_moved += p
            new_base[sel] = base[sel]
            moved[b0 + j] = tot_moved
            cut[b0 + j] = tot_cut
            touched[b0 + j] = len(hit)
        done = min(b0 + BATCH, len(cand))
        if b0 % (BATCH * 20) == 0 or done == len(cand):
            rate = done / max(time.time() - t1, 1e-9)
            print(f"          {done:,}/{len(cand):,} sites "
                  f"({rate:.0f}/s, {(len(cand) - done) / rate:.0f}s left)")

    # Person-minutes rather than person-meters, which is the score's unit.
    cut_min = cut / (60.0 * C.WALK_SPEED_MPS)
    lon, lat = _to_geo(g.x[cand], g.y[cand])
    res = pd.DataFrame({
        "node": cand, "lon": lon, "lat": lat,
        "pop_moved": moved, "excess_min_cut": cut_min,
        "n_geographies": touched,
    })
    # Name each site by the geography it sits in, so the ranked list reads as
    # places rather than as coordinates. A site snapped to the network can
    # land just outside every polygon, which is why this is a left join.
    sites = gpd.GeoDataFrame(
        res, geometry=gpd.points_from_xy(g.x[cand], g.y[cand]),
        crs=C.CRS_PROJ)
    # Re-wrapped rather than sliced in place: the stubs type a DataFrame
    # column selection as Series | DataFrame, which `sjoin` will not take.
    names_gdf = gpd.GeoDataFrame(
        pd.DataFrame({"boroname": gdf["boroname"], "ntaname": gdf["ntaname"]}),
        geometry=gdf.geometry, crs=C.CRS_PROJ)
    named = gpd.sjoin(sites, names_gdf, how="left", predicate="within")
    named = named.drop_duplicates(subset="node").drop(
        columns=[c for c in ("index_right", "geometry") if c in named.columns])
    res = pd.DataFrame(named).sort_values(
        "excess_min_cut", ascending=False).reset_index(drop=True)
    res.to_parquet(out)
    print(f"  saved   {out.name} in {time.time() - t0:.0f}s")
    return res


def _to_geo(x, y):
    pts = gpd.GeoSeries(gpd.points_from_xy(x, y), crs=C.CRS_PROJ).to_crs(
        C.CRS_GEO)
    return pts.x.to_numpy(), pts.y.to_numpy()


def _spread(res: pd.DataFrame, x, y) -> pd.DataFrame:
    """The ranked list with near-duplicates suppressed.

    Walk down the ranking and keep a site only where it is at least
    `C.SITING_MIN_SEPARATION_METERS` from every site already kept, so no two
    entries have overlapping walksheds. Straight-line distance is right here
    rather than network distance: the test is whether two hypothetical
    stations would serve the same people, and two points 1.6 km apart on
    opposite sides of a creek are further apart on the network, never closer.
    """
    kept_i, kx, ky = [], [], []
    minsep = C.SITING_MIN_SEPARATION_METERS
    for i in range(len(res)):
        if kx:
            dx = np.asarray(kx) - x[i]
            dy = np.asarray(ky) - y[i]
            if float(np.min(dx * dx + dy * dy)) < minsep * minsep:
                continue
        kept_i.append(i)
        kx.append(x[i])
        ky.append(y[i])
        if len(kept_i) >= C.SITING_TABLE_ROWS:
            break
    return res.iloc[kept_i]


def summarize(res: pd.DataFrame, x=None, y=None) -> dict:
    if x is None or y is None:
        pts = gpd.GeoSeries(
            gpd.points_from_xy(res["lon"], res["lat"]), crs=C.CRS_GEO
        ).to_crs(C.CRS_PROJ)
        x, y = pts.x.to_numpy(), pts.y.to_numpy()
    top = _spread(res, x, y)
    cutm = np.asarray(res["excess_min_cut"], dtype=float)
    movd = np.asarray(res["pop_moved"], dtype=float)
    print(f"\n  Best sites for one more station, by excess person-minutes "
          f"removed:")
    for _i, r in top.head(10).iterrows():
        where = str(r.get("ntaname") or "")[:34]
        print(f"    {r['excess_min_cut'] / 1e3:8.1f}k person-min  "
              f"{r['pop_moved']:7,.0f} moved inside "
              f"{C.ACCESS_FLOOR_MINUTES:.0f} min  {where:34} "
              f"({r['lat']:.4f}, {r['lon']:.4f})")
    return {
        "spacing_m": C.SITING_SPACING_METERS,
        "reach_m": C.SITING_REACH_METERS,
        "n_sites": int(len(res)),
        "min_separation_m": C.SITING_MIN_SEPARATION_METERS,
        "floor_minutes": C.ACCESS_FLOOR_MINUTES,
        # np.asarray before the reductions: the stubs type a Series
        # reduction as Series, which float() will not take.
        "best_excess_min_cut": float(np.asarray(cutm).max()),
        "best_pop_moved": float(np.asarray(movd).max()),
        "median_excess_min_cut": float(np.median(np.asarray(cutm))),
        "n_sites_moving_nobody": int((np.asarray(movd) <= 0).sum()),
        # The argument for ranking in person-minutes rather than in people
        # moved: the best site that moves *nobody* inside the standard still
        # removes this much walking, because it cuts a 25-minute walk to 14
        # without reaching 10. People-moved scores it at zero.
        "best_cut_moving_nobody": float(
            np.asarray(cutm)[np.asarray(movd) <= 0].max())
        if (np.asarray(movd) <= 0).any() else 0.0,
        "sites": [
            {"where": str(r.get("ntaname") or ""),
             "boro": str(r.get("boroname") or ""),
             "lat": round(float(r["lat"]), 5),
             "lon": round(float(r["lon"]), 5),
             "pop_moved": int(r["pop_moved"]),
             "excess_min_cut": int(round(float(r["excess_min_cut"]))),
             "n_geographies": int(r["n_geographies"])}
            for _i, r in top.iterrows()],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Marginal benefit of one more station, per candidate site.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    res = compute(args.force)
    summarize(res)
    print("\nStage 9 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
