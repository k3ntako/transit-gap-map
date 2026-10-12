"""Stage 4c: how many jobs each geography can reach, and how far off-straight.

This is the metric that dissolves the hub question. "X% of New Yorkers live
within a 10-minute walk of a train" says nothing about whether the train goes
anywhere useful, and "minutes to Times Square" assumes the thing the subway is
worst at -- it is a radial system, so naming one center scores a lateral trip
as a good one. Cumulative opportunity needs no chosen center: every geography
in the city is a destination, weighted by the jobs actually in it.

Three outputs, all from one travel-time matrix:

1. **Jobs within `C.JOBS_BUDGET_MINUTES`** -- the headline accessibility
   number, and the arbiter for everything else here.
2. **Minutes to the nearest job center**, where the centers are picked by
   jobs rather than by ridership. Ridership was tried as the arbiter and is
   circular: the subway is radial, so its busiest stations are the radial
   ones, and using them to choose *secondary* hubs selects for exactly the
   bias secondary hubs exist to correct. ROADMAP.md Phase 4 has the ranks
   that broke the rule.
3. **The detour factor** -- transit time over straight-line time at a
   reference speed. This project already relies on that ratio: `verify.py`
   requires an inflated network-to-straight-line ratio at the barrier probes,
   and it is the check that proves the walk routing is real. The same ratio
   over the transit graph is what turns "Flushing to Kew Gardens is annoying"
   into a number.

**The travel time has three legs and each is measured its own way.** Access
and egress are walk-graph distances reduced to a per-geography median over
the same 25 m sample grid the rest of the pipeline uses, so a station is
reached from the *typical* point in a tract rather than its nearest corner.
The middle leg is `pipeline/transit.py`'s station-to-station matrix, which
already carries the boarding wait and every transfer. And walking the whole
way is kept as a competing option, because excluding it would understate
Manhattan badly -- most of the city's jobs are within a walk of each other.

Run:  python -m pipeline.accessibility [--force]
"""

from __future__ import annotations

import argparse
import json
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

import config as C
from pipeline.aggregate import load_geographies, sample_points
from pipeline.distance import compute as compute_dist
from pipeline.graphutil import load_graph
from pipeline.transit import compute as compute_transit


def _padded_median(tract: np.ndarray, vals: np.ndarray, counts: np.ndarray,
                   n_tracts: int) -> np.ndarray:
    """Per-geography median of `vals`, padded out to `counts` with inf.

    The padding is the whole point. `vals` holds only the sample points a
    bounded search actually reached, so a naive median over them would answer
    "how far is it from the reachable part of this tract", which for a tract
    with one corner in range is a flattering lie. Padding to the tract's full
    sample count means a tract more than half of which is out of range comes
    back as unreachable, which is what it is.

    Vectorized rather than a group-by because it runs once per station: the
    rows are sorted by (tract, value) and the two middle order statistics are
    then a pair of index lookups.
    """
    o = np.lexsort((vals, tract))
    tr, v = tract[o], vals[o]
    uniq, start, nfin = np.unique(tr, return_index=True, return_counts=True)
    tot = counts[uniq]
    p1, p2 = (tot - 1) // 2, tot // 2
    out = np.full(n_tracts, np.inf, dtype=np.float32)
    # If the upper middle position falls past the reached samples, one of the
    # two values being averaged is inf, so the median is inf.
    ok = p2 < nfin
    if ok.any():
        lo = v[start[ok] + p1[ok]]
        hi = v[start[ok] + p2[ok]]
        out[uniq[ok]] = (lo + hi) / 2.0
    return out


class _Samples:
    """The 25 m sample grid, indexed for repeated per-station reductions.

    Sorting the samples by graph node once turns every later lookup from a
    1.2M-element gather into a pair of `searchsorted` calls against the few
    thousand nodes a bounded walk actually reaches -- which matters because
    this is done once per station -- 735 of them -- and 2,325 more times for
    the walk-only
    legs.
    """

    def __init__(self, gdf: gpd.GeoDataFrame, serviceable: np.ndarray):
        g = load_graph(C.P_GRAPH)
        keep = np.flatnonzero(serviceable)
        tree = cKDTree(np.column_stack([g.x[keep], g.y[keep]]))

        pts = sample_points(gdf, C.TRACT_SAMPLE_SPACING_METERS)
        off, loc = tree.query(
            np.column_stack([pts.geometry.x, pts.geometry.y]), k=1)
        far = off > C.MAX_SNAP_METERS
        if far.any():
            print(f"  dropped {int(far.sum()):,} samples further than "
                  f"{C.MAX_SNAP_METERS} m from any serviceable node")

        row_of = {gid: i for i, gid in enumerate(gdf["geoid"])}
        tract = pts["geoid"].map(lambda g: row_of.get(g)).to_numpy()
        node = keep[loc]
        ok = ~far & np.isfinite(tract)

        self.n_tracts = len(gdf)
        self.node = node[ok].astype(np.int64)
        self.tract = tract[ok].astype(np.int64)
        self.off = off[ok].astype(np.float32)
        self.counts = np.bincount(self.tract, minlength=self.n_tracts)
        # Sorted by node, so a reached node set maps straight to its samples.
        self.order = np.argsort(self.node, kind="stable")
        self.node_sorted = self.node[self.order]
        empty = int((self.counts == 0).sum())
        print(f"  samples {len(self.node):,} snapped to serviceable nodes; "
              f"{self.n_tracts - empty:,} of {self.n_tracts:,} "
              f"{C.GEO_LEVEL}s carry at least one")

    def reduce(self, dist_m: np.ndarray) -> np.ndarray:
        """A per-node distance surface -> per-geography median walk minutes."""
        reached = np.flatnonzero(np.isfinite(dist_m))
        if reached.size == 0:
            return np.full(self.n_tracts, np.inf, dtype=np.float32)
        lo = np.searchsorted(self.node_sorted, reached, side="left")
        hi = np.searchsorted(self.node_sorted, reached, side="right")
        cnt = hi - lo
        total = int(cnt.sum())
        if total == 0:
            return np.full(self.n_tracts, np.inf, dtype=np.float32)
        starts = np.repeat(lo, cnt)
        within = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        sel = self.order[starts + within]
        vals = (dist_m[self.node[sel]] + self.off[sel]).astype(np.float32)
        return _padded_median(self.tract[sel], C.minutes(vals), self.counts,
                              self.n_tracts)


def _station_walk(gdf, samples: _Samples, stations: pd.DataFrame,
                  serviceable: np.ndarray) -> np.ndarray:
    """W[s, i]: median walk minutes between station s and geography i.

    One bounded Dijkstra per station, which is also what makes this symmetric:
    the walk graph is undirected, so the same matrix serves the access leg and
    the egress leg, and the measure on the two ends of a trip is identical.
    Bounding at `C.TRANSIT_ACCESS_MINUTES` is what keeps it cheap -- a
    full-city pass is 64 ms and a 15-minute one is 3 ms.
    """
    g = load_graph(C.P_GRAPH)
    keep = np.flatnonzero(serviceable)
    tree = cKDTree(np.column_stack([g.x[keep], g.y[keep]]))

    pts = gpd.GeoSeries(gpd.points_from_xy(stations["lon"], stations["lat"]),
                        crs=C.CRS_GEO).to_crs(C.CRS_PROJ)
    offs, loc = tree.query(np.column_stack([pts.x, pts.y]), k=1)
    snode = keep[loc]

    limit = C.TRANSIT_ACCESS_MINUTES * 60.0 * C.WALK_SPEED_MPS
    W = np.full((len(stations), samples.n_tracts), np.inf, dtype=np.float32)
    t0 = time.time()
    n_far = 0
    for s in range(len(stations)):
        # A station further off the mapped network than the whole walk budget
        # has already spent it getting to the network, so there is nothing
        # left to walk and no geography it can serve.
        if offs[s] >= limit:
            n_far += 1
            continue
        d = dijkstra(g.csr, indices=snode[s], limit=limit - offs[s],
                     min_only=True)
        W[s] = samples.reduce(d + offs[s])
    reach = np.isfinite(W)
    bands = [10.0, 15.0, 20.0, 30.0]
    near = [int((W.min(axis=0) <= b).sum()) for b in bands]
    if n_far:
        print(f"  skipped {n_far} station(s) snapping further than the "
              f"{limit:.0f} m walk budget from the network")
    print(f"  access  {len(stations)} stations x {samples.n_tracts:,} "
          f"{C.GEO_LEVEL}s in {time.time() - t0:.1f}s; "
          f"{reach.sum():,} pairs within {C.TRANSIT_ACCESS_MINUTES:.0f} min "
          f"({reach.any(0).sum():,} {C.GEO_LEVEL}s reach a station, "
          f"median {np.median(reach.sum(0)[reach.any(0)]):.0f} stations each)")
    print("          nearest station within "
          + ", ".join(f"{b:.0f} min: {n:,}" for b, n in zip(bands, near))
          + f" of {samples.n_tracts:,}")
    return W


def _walk_only(gdf, samples: _Samples, serviceable: np.ndarray) -> np.ndarray:
    """Tract-to-tract time on foot, bounded at the accessibility budget.

    Kept as a competing option because leaving it out would understate
    Manhattan badly: a tract in the Financial District can walk to several
    hundred thousand jobs without touching a train, and a metric that routed
    it onto the subway to get next door would report the opposite of the
    truth.

    Measured from each geography's representative point rather than its sample
    median, because this is one pass *per origin* rather than per station and
    the sample grid cannot be a source set without turning the median into a
    minimum. The destination end still reduces by the sample median, so the
    asymmetry is confined to the origin, and it binds only on trips short
    enough to walk -- where the whole leg is a few minutes either way.
    """
    g = load_graph(C.P_GRAPH)
    keep = np.flatnonzero(serviceable)
    tree = cKDTree(np.column_stack([g.x[keep], g.y[keep]]))
    reps = gdf.geometry.representative_point()
    offs, loc = tree.query(np.column_stack([reps.x, reps.y]), k=1)
    snode = keep[loc]

    limit = C.JOBS_BUDGET_MINUTES * 60.0 * C.WALK_SPEED_MPS
    n = len(gdf)
    out = np.full((n, n), np.inf, dtype=np.float32)
    t0 = time.time()
    for i in range(n):
        if offs[i] >= limit:
            continue
        d = dijkstra(g.csr, indices=snode[i], limit=limit - offs[i],
                     min_only=True)
        out[i] = samples.reduce(d + offs[i])
    pairs = int(np.isfinite(out).sum())
    print(f"  on foot {n:,} origins in {time.time() - t0:.1f}s; "
          f"{pairs:,} pairs walkable within "
          f"{C.JOBS_BUDGET_MINUTES:.0f} min "
          f"({pairs / n:.0f} per {C.GEO_LEVEL} on average)")
    return out


def _min_plus(A: np.ndarray, B: np.ndarray, budget_mb: float = 128.0):
    """out[i, j] = min_k (A[i, k] + B[k, j]), chunked to a memory budget.

    The shortest-path composition this needs is a min-plus product, which
    numpy has no primitive for, so it is broadcast in blocks of origins. The
    whole reason Phase 4 is affordable is that these matrices are small: the
    rail graph has 735 stations, not 14,608 stops, because buses are not in
    it.
    """
    n, k = A.shape
    m = B.shape[1]
    chunk = max(1, int(budget_mb * 1e6 / (4.0 * k * m)))
    out = np.empty((n, m), dtype=np.float32)
    for i in range(0, n, chunk):
        a = A[i:i + chunk]
        out[i:i + chunk] = (a[:, :, None] + B[None, :, :]).min(axis=1)
    return out


def pick_hubs(W: np.ndarray, jobs: np.ndarray,
              stations: pd.DataFrame) -> pd.DataFrame:
    """Job centers, by greedy maximum coverage of jobs within a short walk.

    Ranking stations by jobs nearby and taking the top six does not work, and
    the reason is the same defect that made 125 St look like four stations in
    the ridership data: the top of that ranking is *one* job center sampled
    repeatedly, so the list comes back as six Midtown platforms. Forcing a
    minimum spacing was tried instead and is worse -- at 3 km it eliminates
    Grand Central and Penn Station for being near Bryant Park, and backfills
    the list with 116 St-Columbia University, which is not a job center.

    Greedy maximum coverage fixes the repetition with no distance parameter at
    all: pick the station reaching the most jobs, then the station reaching the
    most jobs *nobody has reached yet*, and repeat. The marginal column below
    is what makes that auditable -- a hub whose marginal contribution is small
    is really part of the previous one.

    It is still not enough on its own, and this is the honest part: run
    unconstrained, it returns six Manhattan stations, because the 10-minute
    walk circles around Bryant Park, 5 Av/53 St, 28 St, Fulton St and Union
    Square barely overlap and each holds more jobs than anything outside the
    borough. That is a correct answer to "where are the jobs" and a useless
    answer to "how far is your nearest center". So one hub is taken per
    borough first -- St George is Staten Island's center at a fraction of
    Midtown's jobs, which is exactly the case the ridership rule got wrong --
    and the remaining slots go to marginal coverage. The borough rule is a
    presentation choice, stated here rather than discovered.
    """
    reach = (W <= C.HUB_WALK_MINUTES)
    picked: list[int] = []
    marginal: list[float] = []
    covered = np.zeros(W.shape[1], dtype=bool)

    boro = _station_borough(stations)      # one point-in-polygon pass, reused
    for b in sorted(set(boro) - {""}):
        in_b = np.flatnonzero(boro == b)
        gain = (reach[in_b] & ~covered).astype(np.float32) @ jobs
        if gain.size == 0 or gain.max() <= 0:
            continue
        s = int(in_b[int(np.argmax(gain))])
        picked.append(s)
        marginal.append(float(gain.max()))
        covered |= reach[s]

    while len(picked) < C.N_HUBS:
        gain = (reach & ~covered).astype(np.float32) @ jobs
        gain[picked] = -1.0
        s = int(np.argmax(gain))
        if gain[s] <= 0:
            break
        picked.append(s)
        marginal.append(float(gain[s]))
        covered |= reach[s]

    total = reach[picked].astype(np.float32) @ jobs
    out = stations.iloc[picked][["station_key", "name", "mode", "lat",
                                 "lon"]].copy()
    # The transit graph's `mode` is the feed a station came from, so every
    # Staten Island Railway stop reads as "subway" -- SIR rides in the subway
    # feed. stations.parquet is where the two are separated, so the label
    # comes from there and falls back to the feed name for the stops outside
    # the city that stations.parquet does not carry.
    label = pd.read_parquet(C.P_STATIONS).set_index("station_key")["mode"]
    out["mode"] = out["station_key"].map(label).fillna(out["mode"])
    out = out.assign(jobs_near=total.astype("int64"),
                     jobs_marginal=np.asarray(marginal).astype("int64"),
                     borough=boro[picked],
                     station_row=picked).reset_index(drop=True)
    print(f"  hubs    {len(out)} job centers, by jobs within "
          f"{C.HUB_WALK_MINUTES:.0f} min on foot "
          f"({100 * jobs[covered].sum() / jobs.sum():.0f}% of all jobs "
          f"covered):")
    for _, r in out.iterrows():
        print(f"            {r['jobs_near']:9,} jobs "
              f"(+{r['jobs_marginal']:8,} new)  {r['name'][:30]:30} "
              f"{str(r['borough'])[:13]:13} ({r['mode']})")
    return out


def _station_borough(stations: pd.DataFrame) -> np.ndarray:
    """Which borough each station sits in, by point-in-polygon.

    The station table in the transit graph comes from the GTFS feeds, which
    carry no borough, and the out-of-city commuter-rail stops must come back
    empty rather than being nearest-matched into a borough they are not in.
    """
    boros = gpd.read_file(C.P_BOROS).to_crs(C.CRS_PROJ)
    pts = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(stations["lon"], stations["lat"]),
        crs=C.CRS_GEO).to_crs(C.CRS_PROJ)
    name = next(c for c in ("boro_name", "BoroName", "boroname")
                if c in boros.columns)
    cols = gpd.GeoDataFrame(boros[[name, "geometry"]], geometry="geometry")
    joined = gpd.sjoin(pts, cols, how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    return joined[name].fillna("").to_numpy()


def _contiguous(gdf: gpd.GeoDataFrame, mask: np.ndarray) -> tuple:
    """Sizes of the contiguous blocks formed by the tracts in `mask`.

    Land adjacency, from the water-excluded DCP polygons, exactly as
    `aggregate.add_clusters` uses it: no bridge edges, so tracts across the
    East River do not touch.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return 0, np.array([]), np.array([]), idx
    geom = gpd.GeoSeries(gdf["geometry"].to_numpy()[idx], crs=gdf.crs)
    sub = gpd.GeoDataFrame(geometry=geom.reset_index(drop=True))
    pairs = gpd.sjoin(sub, sub, how="inner", predicate="touches")
    adj = coo_matrix((np.ones(len(pairs)),
                      (pairs.index.to_numpy(),
                       pairs["index_right"].to_numpy())),
                     shape=(len(sub), len(sub)))
    n, labels = connected_components(adj, directed=False)
    return n, np.bincount(labels, minlength=n), labels, idx


def compute(force: bool = False) -> tuple[pd.DataFrame, dict]:
    """Per-geography accessibility, and the statistics that describe it."""
    stats_path = C.P_ACCESS.with_suffix(".json")
    if C.P_ACCESS.exists() and stats_path.exists() and not force:
        print(f"  cached  {C.P_ACCESS.name}")
        # The statistics are cached beside the table rather than recomputed or
        # dropped. Returning an empty dict here is what silently stopped
        # hubs.json from being written at all: build.py keys the hub payload
        # off these stats, so a cache that forgot them produced a clean build
        # with one payload missing.
        return pd.read_parquet(C.P_ACCESS), json.loads(stats_path.read_text())

    gdf = load_geographies()
    jobs_tbl = pd.read_parquet(C.P_JOBS)
    jobs = (gdf[["geoid"]].merge(jobs_tbl, on="geoid", how="left")["jobs"]
            .fillna(0).to_numpy(dtype=np.float32))
    print(f"  jobs    {jobs.sum():,.0f} in {int((jobs > 0).sum()):,} of "
          f"{len(gdf):,} {C.GEO_LEVEL}s; the busiest holds "
          f"{jobs.max():,.0f} ({100 * jobs.max() / jobs.sum():.1f}%)")

    _d, _n, serviceable = compute_dist(force=False, subway_only=False)
    tg = compute_transit(force=False)
    samples = _Samples(gdf, serviceable)

    W = _station_walk(gdf, samples, tg.stations, serviceable)
    walk = _walk_only(gdf, samples, serviceable)

    t0 = time.time()
    arrive = _min_plus(W.T, tg.ride)            # geography -> station
    transit = _min_plus(arrive, W)              # geography -> geography
    total = np.minimum(transit, walk)
    print(f"  solved  {len(gdf):,}x{len(gdf):,} travel times in "
          f"{time.time() - t0:.1f}s "
          f"({100 * np.isfinite(total).mean():.1f}% of pairs reachable; "
          f"walking wins {100 * (walk < transit).mean():.1f}% of them)")

    within = total <= C.JOBS_BUDGET_MINUTES
    jobs_reach = (within.astype(np.float32) @ jobs)
    share = jobs_reach / jobs.sum()
    print(f"  jobs{C.JOBS_BUDGET_MINUTES:.0f}  median "
          f"{np.median(jobs_reach):,.0f} jobs "
          f"({100 * np.median(share):.1f}% of the city's), "
          f"p10 {np.percentile(jobs_reach, 10):,.0f}, "
          f"p90 {np.percentile(jobs_reach, 90):,.0f}")

    hubs = pick_hubs(W, jobs, tg.stations)
    hub_min = arrive[:, hubs["station_row"].to_numpy()]
    # inf already means "no rail path", so a plain min is correct and a
    # nan-aware one would only reintroduce the sentinel it replaced.
    nearest_hub = hub_min.min(axis=1)

    # --- detour factor ----------------------------------------------------
    reps = gdf.geometry.representative_point()
    xy = np.column_stack([reps.x, reps.y])
    straight_km = np.hypot(xy[:, None, 0] - xy[None, :, 0],
                           xy[:, None, 1] - xy[None, :, 1]) / 1000.0
    ref_min = straight_km / C.DETOUR_REFERENCE_SPEED_KMH * 60.0
    band = ((straight_km >= C.DETOUR_MIN_KM) & (straight_km <= C.DETOUR_MAX_KM)
            & np.isfinite(transit))
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(band, transit / ref_min, np.nan)
        kmh = np.where(band, straight_km / (transit / 60.0), np.nan)
    detour = np.nanmedian(ratio, axis=1)
    measured = float(np.nanmedian(kmh))
    print(f"  detour  {C.DETOUR_MIN_KM:.0f}-{C.DETOUR_MAX_KM:.0f} km pairs: "
          f"median factor {np.nanmedian(detour):.2f}, "
          f"p10 {np.nanpercentile(detour, 10):.2f}, "
          f"p90 {np.nanpercentile(detour, 90):.2f}")
    print(f"          measured median straight-line speed {measured:.1f} km/h "
          f"against the {C.DETOUR_REFERENCE_SPEED_KMH:.1f} km/h reference")
    # The reference is a fixed constant so the field means the same thing
    # across builds, but it came from this measurement, so a large drift means
    # the constant has stopped describing the network rather than that the
    # network got better.
    drift = abs(measured - C.DETOUR_REFERENCE_SPEED_KMH) / \
        C.DETOUR_REFERENCE_SPEED_KMH
    if drift > 0.2:
        print(f"  WARNING the measured speed is {100 * drift:.0f}% off "
              f"DETOUR_REFERENCE_SPEED_KMH; the detour factor is still "
              f"internally consistent but is no longer centered on 1.0, so "
              f"re-derive the constant before quoting it")

    out = pd.DataFrame({
        "geoid": gdf["geoid"].to_numpy(),
        "jobs": jobs.astype("int64"),
        "jobs_reach": jobs_reach.astype("int64"),
        "jobs_share": share.astype("float32"),
        "hub_min": nearest_hub.astype("float32"),
        "detour": detour.astype("float32"),
    })
    for row in range(len(hubs)):
        out[f"hub_{row}_min"] = hub_min[:, row].astype("float32")
    out.to_parquet(C.P_ACCESS, index=False)
    np.savez_compressed(C.P_TRACT_TIME, total=total.astype(np.float32),
                        transit=transit.astype(np.float32))
    print(f"  saved   {C.P_ACCESS.name}, {C.P_TRACT_TIME.name}")

    stats = _report(gdf, out, hubs, jobs, measured, total)
    stats_path.write_text(json.dumps(stats, indent=2, default=str))
    return out, stats


def _report(gdf, out, hubs, jobs, measured_kmh, total) -> dict:
    """The findings, printed and returned for stats.json."""
    pop = gdf["pop"].to_numpy(dtype=np.float64)
    reach = out["jobs_reach"].to_numpy(dtype=np.float64)
    weighted = float((reach * pop).sum() / pop.sum()) if pop.sum() else 0.0
    print(f"\n  The population-weighted average New Yorker can reach "
          f"{weighted:,.0f} jobs")
    print(f"  in {C.JOBS_BUDGET_MINUTES:.0f} minutes by rail -- "
          f"{100 * weighted / jobs.sum():.1f}% of the city's "
          f"{jobs.sum():,.0f}.")

    worst = out.assign(boro=gdf["boroname"].to_numpy(),
                       nta=gdf["ntaname"].to_numpy(),
                       pop=pop).query("pop > 500").nsmallest(8, "jobs_reach")
    print("\n  Fewest jobs reachable, among tracts with people in them:")
    for _, r in worst.iterrows():
        print(f"    {r['jobs_reach']:9,.0f} jobs  {str(r['nta'])[:30]:30} "
              f"({r['boro']})")

    by_boro = (out.assign(boro=gdf["boroname"].to_numpy(), pop=pop)
               .groupby("boro")
               .apply(lambda g: pd.Series({
                   "jobs_reach": (g["jobs_reach"] * g["pop"]).sum()
                   / max(g["pop"].sum(), 1),
                   "detour": g["detour"].median(),
                   "hub_min": g["hub_min"].replace(np.inf, np.nan).median()}),
                      include_groups=False))
    print("\n  By borough, population-weighted:")
    for b, r in by_boro.iterrows():
        print(f"    {str(b):14} {r['jobs_reach']:9,.0f} jobs  "
              f"detour {r['detour']:4.2f}  nearest hub "
              f"{r['hub_min']:5.1f} min")

    # The pre-registered test: are the worst detours a ring, or scattered?
    thresh = float(np.nanpercentile(out["detour"], 90))
    mask = (out["detour"].to_numpy() >= thresh) & np.isfinite(
        out["detour"].to_numpy())
    n_blocks, sizes, labels, idx = _contiguous(gdf, mask)
    big = int(sizes.max()) if sizes.size else 0
    print(f"\n  Detour ring test (pre-registered in ROADMAP.md): the worst "
          f"decile is\n  {int(mask.sum())} tracts in {n_blocks} contiguous "
          f"blocks; the largest holds {big} of them "
          f"({100 * big / max(int(mask.sum()), 1):.0f}%).")
    if sizes.size:
        # Name the largest block by the neighborhoods it covers, the same way
        # add_clusters names its clusters: a block is only a finding if you
        # can say where it is.
        members = idx[labels == int(np.argmax(sizes))]
        where = (gdf.iloc[members]["ntaname"].value_counts().head(4)
                 .index.tolist())
        print(f"    the largest block spans "
              f"{', '.join(str(w)[:22] for w in where)}")
        # Two independent measures agreeing is worth saying out loud: the
        # under-service score is a walk-and-density measure that knows nothing
        # about travel time, and the detour factor is a travel-time measure
        # that knows nothing about population.
        try:
            prev = pd.read_parquet(C.P_GEO_STATS)[["geoid", "cluster_tracts"]]
            band = set(prev.loc[prev["cluster_tracts"] >= 100, "geoid"])
        except (FileNotFoundError, KeyError):
            band = set()
        if band:
            hit = sum(1 for gid in gdf.iloc[members]["geoid"] if gid in band)
            # Not "the largest" block: `band` is every under-service block
            # past 100 tracts, and there are two of them -- 430 tracts across
            # Brooklyn and Queens, and 105 on Staten Island. That distinction
            # was invisible while the largest detour block was in Brooklyn
            # and became load-bearing the moment the ferry edge moved it.
            print(f"    {hit} of those {len(members)} tracts also sit in a "
                  f"contiguous under-service\n    block past 100 tracts, "
                  f"which is a separate measure over separate inputs")

    iso = ~np.isfinite(out["hub_min"].to_numpy())
    isolated = int(iso.sum())
    iso_boro: dict = {}
    if isolated:
        by = (pd.DataFrame({"boro": gdf["boroname"].to_numpy(), "pop": pop})
              .loc[iso].groupby("boro")["pop"].agg(["size", "sum"]))
        iso_boro = {str(b): {"tracts": int(r["size"]), "pop": int(r["sum"])}
                    for b, r in by.iterrows()}
        print(f"\n  {isolated} {C.GEO_LEVEL}s ({int(pop[iso].sum()):,} people) "
              f"can reach no job center by rail:")
        for b, r in by.sort_values("sum", ascending=False).iterrows():
            print(f"    {str(b):14} {int(r['size']):4} {C.GEO_LEVEL}s, "
                  f"{int(r['sum']):9,} people")
        print(f"  This is distance to a station, not isolation from the "
              f"network: the ferry edge\n  connects Staten Island, and what "
              f"these {C.GEO_LEVEL}s lack is a station inside the\n  walk "
              f"budget at all. The crossing itself is "
              f"{C.FERRY_CROSSING_MINUTES:.0f} minutes on every one\n  of "
              f"the feed's trips, so reaching the network and reaching a job "
              f"center\n  are different questions on Staten Island.")

    return {
        "jobs_total": int(jobs.sum()),
        "budget_min": C.JOBS_BUDGET_MINUTES,
        "weighted_jobs_reach": round(weighted),
        "weighted_share": round(weighted / float(jobs.sum()), 4),
        "median_jobs_reach": int(np.median(reach)),
        "detour_reference_kmh": C.DETOUR_REFERENCE_SPEED_KMH,
        "detour_measured_kmh": round(measured_kmh, 1),
        "detour_median": round(float(np.nanmedian(out["detour"])), 3),
        "detour_ring": {"threshold": round(thresh, 3),
                        "n_tracts": int(mask.sum()),
                        "n_blocks": int(n_blocks),
                        "largest_block": big},
        "rail_isolated_tracts": isolated,
        "rail_isolated_by_borough": iso_boro,
        "hubs": hubs.drop(columns="station_row").to_dict("records"),
        "by_borough": {str(k): {kk: round(float(vv), 2)
                                for kk, vv in v.items()}
                       for k, v in by_boro.to_dict("index").items()},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Jobs reachable, hubs, detour.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    print("Accessibility:")
    compute(args.force)
    print("\nStage 4c complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
