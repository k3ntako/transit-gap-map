"""Stage 3: network walking distance from every graph node to the nearest station.

One multi-source Dijkstra pass answers the whole city at once. Each station gets
a virtual source node joined to its snapped graph node by the snap offset, so a
station sitting off the mapped network pays for that gap -- and the returned
`sources` array still identifies which station is nearest.

NYC's pedestrian network is genuinely disconnected (Staten Island has no
walkable link to the other boroughs), so this stage works per component rather
than assuming one network, and publishes a `serviceable` mask: the nodes that
sit in a component actually containing a station. Stage 4 snaps only to those,
which keeps a sample point from attaching to an isolated park path and being
reported as having no transit access at all.

Run:  python -m pipeline.distance [--force]
"""

from __future__ import annotations

import argparse
import time

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix, hstack, vstack
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

import config as C
from pipeline.graphutil import load_graph
from pipeline.stations import load_stations

SUBWAY_MODES = ("subway", "sir")


def cache_path(subway_only: bool):
    if not subway_only:
        return C.P_NODE_DIST
    return C.P_NODE_DIST.with_name(C.P_NODE_DIST.stem + "_subway.npz")


def compute(force: bool = False, subway_only: bool = False):
    """Per-node distance to the nearest station.

    `subway_only` restricts sources to subway + SIR, which is what published
    "share of New Yorkers within a half mile of the subway" figures measure,
    so it gives an apples-to-apples benchmark in stage 4.

    Returns (dist_m, nearest_station_idx, serviceable_mask).
    """
    out = cache_path(subway_only)
    if out.exists() and not force:
        print(f"  cached  {out.name}")
        z = np.load(out, allow_pickle=True)
        return z["dist"], z["nearest"], z["serviceable"]

    g = load_graph(C.P_GRAPH)
    csr, x, y, comp = g.csr, g.x, g.y, g.component
    n = csr.shape[0]
    print(f"  graph   {n:,} nodes / {csr.nnz // 2:,} undirected edges, "
          f"{comp.max() + 1:,} components")

    st = load_stations(verbose=not subway_only)
    if subway_only:
        st = st.loc[st["mode"].isin(SUBWAY_MODES)].reset_index(drop=True)
        print(f"  subway-only variant: {len(st)} stations")

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
    link = coo_matrix((np.maximum(offs, 1e-6), (np.arange(m), snode)), shape=(m, n))
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
    print(f"  walk distance: median {np.median(d):.0f} m, "
          f"p90 {np.percentile(d, 90):.0f} m, max {d.max():.0f} m")

    np.savez_compressed(out, dist=dist, nearest=nearest, serviceable=serviceable)
    saved = [out.name]
    if not subway_only:
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
    print("All rail modes:")
    compute(args.force, subway_only=False)
    print("\nSubway + SIR only (for the published-benchmark cross-check):")
    compute(args.force, subway_only=True)
    print("\nStage 3 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
