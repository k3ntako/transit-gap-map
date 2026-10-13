"""Stage 4: network walking distance from every graph node to the nearest station.

One multi-source Dijkstra pass answers the whole city at once. Each station gets
a virtual source node joined to its snapped graph node by the snap offset, so a
station sitting off the mapped network pays for that gap -- and the returned
`sources` array still identifies which station is nearest.

The `step_free` variant restricts the source set to the 219 stations with a
step-free route built, which is the city as a rider who cannot use stairs sees
it. It is a lens and not a term in the score: the score ranks geographies and
its intervention is new service, while this ranks stations and its
intervention is an elevator. See Phase 6 in ROADMAP.md.

The `weighted` variant adds each station's expected wait to its virtual
source cost, converted to meters at the walk speed. That is the conceptual
change in Phase 2: a frequency-weighted station set is what distinguishes a
30-minute-headway SIR stop from Times Square, and the architecture already
supported it -- charging for the wait is the same move as charging for the snap
offset, in the same currency, on the same edge. It is a separate output rather
than a replacement, because the pure-walk pass is what the published half-mile
figures measure and those must stay comparable.

NYC's pedestrian network is genuinely disconnected (Staten Island has no
walkable link to the other boroughs), so this stage works per component rather
than assuming one network, and publishes a `serviceable` mask: the nodes that
sit in a component actually containing a station. Stage 5 snaps only to those,
which keeps a sample point from attaching to an isolated park path and being
reported as having no transit access at all.

Run:  python -m pipeline.distance [--force]
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix, csr_matrix, hstack, vstack
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

import config as C
from pipeline.graphutil import load_graph
from pipeline.service import compute as compute_service
from pipeline.stations import load_stations

SUBWAY_MODES = ("subway", "sir")


def cache_path(subway_only: bool, weighted: bool = False,
               step_free: bool = False):
    stem = C.P_NODE_DIST.stem
    if subway_only:
        stem += "_subway"
    if weighted:
        stem += "_weighted"
    if step_free:
        stem += "_stepfree"
    return C.P_NODE_DIST.with_name(stem + ".npz")


def compute(force: bool = False, subway_only: bool = False,
            weighted: bool = False, step_free: bool = False):
    """Per-node cost to the nearest station.

    `subway_only` restricts sources to subway + SIR, which is what published
    "share of New Yorkers within a half mile of the subway" figures measure,
    so it gives an apples-to-apples benchmark in stage 5.

    `weighted` adds each station's expected wait to its source cost, so the
    returned number is generalized access cost rather than walking distance.
    The unit stays meters -- minutes times the walk speed -- which keeps one
    currency on the graph and means `C.minutes()` converts either variant.

    `step_free` keeps only stations with a step-free route built. The flag
    means built, not working today -- MTA publishes live elevator outages and
    a static map cannot say "out until April" -- so everything downstream
    labels it by the fetch date rather than as current.

    Returns (dist_m, nearest_station_idx, serviceable_mask).
    """
    out = cache_path(subway_only, weighted, step_free)
    if out.exists() and not force:
        print(f"  cached  {out.name}")
        z = np.load(out, allow_pickle=True)
        return z["dist"], z["nearest"], z["serviceable"]

    g = load_graph(C.P_GRAPH)
    csr, x, y, comp = g.csr, g.x, g.y, g.component
    n = csr.shape[0]
    print(f"  graph   {n:,} nodes / {csr.nnz // 2:,} undirected edges, "
          f"{comp.max() + 1:,} components")

    st = load_stations(
        verbose=not subway_only and not weighted and not step_free)
    if subway_only:
        st = st.loc[st["mode"].isin(SUBWAY_MODES)].reset_index(drop=True)
        print(f"  subway-only variant: {len(st)} stations")
    if step_free:
        before = len(st)
        st = st.loc[st["ada"].astype(bool)].reset_index(drop=True)
        print(f"  step-free variant: {len(st)} of {before} stations have a "
              f"step-free route built")
        # Not `for m, n in ...`: `m` and `n` are the station and node counts
        # the virtual-source matrix below is shaped by.
        for mode, count in st["mode"].value_counts().items():
            print(f"            {mode:10} {count:4}")

    wait_m = np.zeros(len(st))
    if weighted:
        svc = compute_service(force=False)
        wait_of = dict(zip(svc.index.tolist(), svc["wait_min"].tolist()))
        wait = pd.Series([wait_of.get(k) for k in st["station_key"]],
                         dtype="float64")
        # A station with no service in the window cannot be used then, so it
        # is removed from the source set rather than charged a large finite
        # cost. See C.WAIT_CAP_MINUTES for why the cap is not the answer here.
        usable = wait.notna().to_numpy()
        if not usable.all():
            gone = st.loc[~usable, "name"].tolist()
            print(f"  dropped {len(gone)} station(s) with no {C.WAIT_WINDOW} "
                  f"service: {', '.join(gone)}")
        st = st.loc[usable].reset_index(drop=True)
        wait_min = wait.to_numpy()[usable]
        wait_m = wait_min * 60.0 * C.WALK_SPEED_MPS
        print(f"  weighted: {len(st)} stations, wait {wait_min.min():.1f}-"
              f"{wait_min.max():.1f} min (median {np.median(wait_min):.1f}), "
              f"worth {np.median(wait_m):.0f} m of walking at the median")

    # Snap stations only onto substantive networks, so a station mapped on a
    # stray footway fragment doesn't become an island serving nobody.
    sizes = np.bincount(comp, minlength=comp.max() + 1)
    substantive = sizes[comp] >= C.MIN_COMPONENT_NODES
    sub_idx = np.flatnonzero(substantive)
    tree_st = cKDTree(np.column_stack([x[sub_idx], y[sub_idx]]))

    offs, loc = tree_st.query(
        np.column_stack([st.geometry.x.to_numpy(), st.geometry.y.to_numpy()]), k=1)
    snode = sub_idx[loc]

    far = offs > C.SNAP_WARN_METERS
    if far.any():
        print(f"  WARNING {far.sum()} station(s) snapped >{C.SNAP_WARN_METERS} m from the "
              f"walk network (max {offs.max():.0f} m):")
        for nm, d in zip(st.loc[far, "name"].head(8), offs[far][:8]):
            print(f"            {d:6.0f} m  {nm}")
    print(f"  snapped {len(st)} stations (median offset {np.median(offs):.0f} m, "
          f"p95 {np.percentile(offs, 95):.0f} m)")

    # Components that actually contain a station.
    served = np.zeros(sizes.shape[0], dtype=bool)
    served[comp[snode]] = True
    serviceable = served[comp]
    print(f"  serviceable: {serviceable.sum():,}/{n:,} nodes "
          f"({100 * serviceable.mean():.1f}%) in {served.sum():,} station-bearing "
          f"components")

    # --- extend the graph with one virtual source per station --------------
    m = len(st)
    # The virtual source sits at cost 0 and this edge carries everything it
    # takes to be on a train: the walk to the mapped network, plus the wait
    # once there. Both are already in meters.
    source_cost = np.maximum(offs + wait_m, 1e-6)
    link = coo_matrix((source_cost, (np.arange(m), snode)), shape=(m, n))
    top = hstack([csr, link.T.tocsr()], format="csr")
    bot = hstack([link.tocsr(), csr_matrix((m, m))], format="csr")
    ext = vstack([top, bot], format="csr")

    t0 = time.time()
    dist, _pred, sources = dijkstra(
        ext, indices=np.arange(n, n + m), min_only=True, return_predecessors=True)
    print(f"  dijkstra {time.time() - t0:.1f}s over {ext.shape[0]:,} nodes")

    dist = dist[:n]
    nearest = sources[:n] - n          # virtual index -> station row index
    nearest[(nearest < 0) | ~np.isfinite(dist)] = -1

    reach = np.isfinite(dist)
    print(f"  reached {reach.sum():,}/{n:,} nodes ({100 * reach.mean():.2f}%); "
          f"of serviceable nodes {100 * reach[serviceable].mean():.2f}%")
    d = dist[reach]
    label = "access cost" if weighted else "walk distance"
    print(f"  {label}: median {np.median(d):.0f} m, "
          f"p90 {np.percentile(d, 90):.0f} m, max {d.max():.0f} m "
          f"({C.minutes(np.median(d)):.1f} min median)")

    np.savez_compressed(out, dist=dist, nearest=nearest, serviceable=serviceable)
    saved = [out.name]
    if not subway_only and not weighted and not step_free:
        st_geo = st.to_crs(C.CRS_GEO)
        st.drop(columns="geometry").assign(
            snap_offset_m=offs,
            lon=st_geo.geometry.x.to_numpy(),
            lat=st_geo.geometry.y.to_numpy(),
        ).to_parquet(C.P_STATIONS)
        saved.append(C.P_STATIONS.name)
    print(f"  saved   {', '.join(saved)}")
    return dist, nearest, serviceable


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-node distance to nearest station.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    print("All rail modes, walking only:")
    compute(args.force, subway_only=False)
    print("\nSubway + SIR only (for the published-benchmark cross-check):")
    compute(args.force, subway_only=True)
    print("\nAll rail modes, plus the expected wait at each station:")
    compute(args.force, subway_only=False, weighted=True)
    print("\nStep-free stations only (the accessibility lens):")
    compute(args.force, subway_only=False, step_free=True)
    print("\nStage 4 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
