"""Stage 2: OSM .pbf -> cached undirected walking graph.

Deliberately avoids OSMnx's Overpass download path (unreliable on some
networks) and any routing server. Output is a plain CSR adjacency plus node
coordinates, which stages 3-5 reload in under a second.

Run:  python -m pipeline.network [--force]
"""

from __future__ import annotations

import argparse
import time
import warnings

import geopandas as gpd
import numpy as np
from scipy.sparse.csgraph import connected_components

import config as C
from pipeline.graphutil import build_undirected_csr, save_graph

warnings.simplefilter("ignore")


def study_area_bbox():
    """Bounding box of the five boroughs, buffered outward.

    The buffer keeps routing honest near the city line and lets a station just
    outside NYC serve the blocks it genuinely serves.
    """
    boros = gpd.read_file(C.P_BOROS).to_crs(C.CRS_PROJ)
    buffered = boros.geometry.union_all().buffer(C.STATION_BUFFER_M)
    west = gpd.GeoSeries([buffered], crs=C.CRS_PROJ).to_crs(C.CRS_GEO)
    minx, miny, maxx, maxy = west.total_bounds
    return [float(minx), float(miny), float(maxx), float(maxy)]


def build(force: bool = False) -> None:
    if C.P_GRAPH.exists() and not force:
        print(f"  cached  {C.P_GRAPH.name}")
        return
    if not C.P_PBF.exists():
        raise SystemExit(f"Missing {C.P_PBF}. Run: python -m pipeline.fetch")

    from pyrosm import OSM

    bbox = study_area_bbox()
    print(f"  bbox    {[round(v, 4) for v in bbox]}")

    t0 = time.time()
    print("  parsing walking network from .pbf (this takes a minute or two)")
    osm = OSM(str(C.P_PBF), bounding_box=bbox)
    nodes, edges = osm.get_network(network_type="walking", nodes=True)
    print(f"  parsed  {len(nodes):,} nodes / {len(edges):,} edges "
          f"in {time.time() - t0:.0f}s")

    # --- node table -> contiguous indices ---------------------------------
    nodes = nodes.drop_duplicates(subset="id").reset_index(drop=True)
    nodes_p = nodes.to_crs(C.CRS_PROJ)
    osmid = nodes["id"].to_numpy(dtype=np.int64)
    x = nodes_p.geometry.x.to_numpy()
    y = nodes_p.geometry.y.to_numpy()

    order = np.argsort(osmid)
    osmid_sorted = osmid[order]

    def to_index(ids):
        ids = np.asarray(ids, dtype=np.int64)
        pos = np.searchsorted(osmid_sorted, ids)
        pos = np.clip(pos, 0, len(osmid_sorted) - 1)
        ok = osmid_sorted[pos] == ids
        return order[pos], ok

    # --- edge lengths in meters -------------------------------------------
    # Measured from the projected geometry, so curved streets are not
    # understated the way a straight u->v distance would understate them.
    edges_p = edges.to_crs(C.CRS_PROJ)
    length = edges_p.geometry.length.to_numpy()

    ui, ok_u = to_index(edges["u"])
    vi, ok_v = to_index(edges["v"])
    good = ok_u & ok_v & np.isfinite(length) & (length > 0)
    dropped = int((~good).sum())
    if dropped:
        print(f"  dropped {dropped:,} edges with unresolved endpoints or bad length")

    csr = build_undirected_csr(ui[good], vi[good], length[good], len(osmid))
    print(f"  graph   {csr.shape[0]:,} nodes / {csr.nnz // 2:,} undirected edges")

    # --- connected components ---------------------------------------------
    # Every component is KEPT. NYC's pedestrian network is genuinely
    # disconnected: Staten Island has no walkable link to the other boroughs
    # (the Verrazzano carries no footpath), so "largest component" would delete
    # ~500k residents and all 21 Staten Island Railway stations. Stage 3
    # restricts snapping to components that actually contain a station, which
    # handles both the real islands and the thousands of stray footpath
    # fragments without discarding anyone.
    n_comp, labels = connected_components(csr, directed=False)
    sizes = np.bincount(labels)
    order_big = np.argsort(sizes)[::-1]
    print(f"  components: {n_comp:,} (all kept)")
    for rank, comp in enumerate(order_big[:6]):
        print(f"            #{rank + 1}: {sizes[comp]:>9,} nodes "
              f"({100 * sizes[comp] / len(labels):5.2f}%)")
    tiny = int((sizes < 50).sum())
    print(f"            {tiny:,} components under 50 nodes "
          f"({sizes[sizes < 50].sum():,} nodes total)")

    save_graph(C.P_GRAPH, csr.tocsr(), x, y, osmid, labels)
    size_mb = C.P_GRAPH.stat().st_size / 1e6
    print(f"  saved   {C.P_GRAPH.name} ({size_mb:.1f} MB) in {time.time() - t0:.0f}s total")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the cached walking graph.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    build(args.force)
    print("\nStage 2 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
