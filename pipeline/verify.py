"""Correctness checks for the distance computation.

The headline check is the barrier test. Straight-line distance gets NYC badly
wrong because the city is cut up by water and expressways, so for each probe we
compare network distance against the straight-line distance to the nearest
station and look at the ratio. A detour factor near 1 means open street grid;
a large factor means the walk has to go around something. If the barrier probes
did not show inflated ratios, the pipeline would not actually be routing.

Run:  python -m pipeline.verify
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

import config as C
from pipeline.distance import compute as compute_dist
from pipeline.graphutil import load_graph

warnings.simplefilter("ignore")

# (label, lon, lat, expectation) -- expectation is checked loosely, as a band.
PROBES = [
    # Should be effectively at a station.
    ("Grand Central Terminal",  -73.97720, 40.75270, "at_station"),
    ("Times Sq-42 St",          -73.98550, 40.75800, "at_station"),
    ("Atlantic Av-Barclays",    -73.97690, 40.68430, "at_station"),
    ("St George, SI (SIR)",     -74.07360, 40.64330, "at_station"),
    # Dense core: a short walk essentially everywhere.
    ("Midtown (6 Av / 50 St)",  -73.98250, 40.75950, "near"),
    ("Lower East Side",         -73.98680, 40.71790, "near"),
    # Known thin-coverage areas.
    ("Breezy Point, Queens",    -73.92650, 40.55600, "far"),
    ("Charleston, SI",          -74.23060, 40.53190, "far"),
    ("Bergen Beach, Brooklyn",  -73.90670, 40.61810, "far"),
    ("Country Club, Bronx",     -73.81800, 40.84100, "far"),
]

# Places where the short straight-line route crosses water the walk cannot.
BARRIERS = [
    ("Greenpoint @ Newtown Creek",  -73.94120, 40.73560),
    ("Hunts Point, Bronx",          -73.88100, 40.80700),
    ("Broad Channel shore",         -73.81500, 40.61400),
    ("Coney Island Creek",          -73.99400, 40.58200),
]

BANDS = {"at_station": (0, 400), "near": (0, 900), "far": (1200, 60000)}


def _setup():
    dist, nearest, serviceable = compute_dist(force=False, subway_only=False)
    _csr, x, y, _osmid = load_graph(C.P_GRAPH)
    keep = np.flatnonzero(serviceable)
    tree = cKDTree(np.column_stack([x[keep], y[keep]]))
    st = pd.read_parquet(C.P_STATIONS)
    to_proj = Transformer.from_crs(C.CRS_GEO, C.CRS_PROJ, always_xy=True)
    sx, sy = to_proj.transform(st["lon"].to_numpy(), st["lat"].to_numpy())
    st_tree = cKDTree(np.column_stack([sx, sy]))
    return dist, nearest, keep, tree, st, st_tree, to_proj


def measure(lons, lats, dist, keep, tree, st_tree, to_proj):
    px, py = to_proj.transform(np.asarray(lons), np.asarray(lats))
    pts = np.column_stack([px, py])
    off, loc = tree.query(pts, k=1)
    network = dist[keep[loc]] + off
    straight, _ = st_tree.query(pts, k=1)
    return network, straight, keep[loc]


def main() -> int:
    dist, nearest, keep, tree, st, st_tree, to_proj = _setup()
    failures = []

    print("Spot checks -- network walk distance to the nearest station\n")
    print(f"  {'location':30} {'network':>9} {'straight':>9} {'detour':>7}  "
          f"{'nearest station':24} verdict")
    lons = [p[1] for p in PROBES]
    lats = [p[2] for p in PROBES]
    net, line, nodes = measure(lons, lats, dist, keep, tree, st_tree, to_proj)

    for (label, _lo, _la, kind), n, s, nd in zip(PROBES, net, line, nodes):
        lo, hi = BANDS[kind]
        ok = lo <= n <= hi
        sidx = nearest[nd]
        sname = st["name"].iloc[sidx] if sidx >= 0 else "(none)"
        print(f"  {label:30} {n:7.0f} m {s:7.0f} m {n / max(s, 1):6.2f}x  "
              f"{str(sname)[:24]:24} {'PASS' if ok else 'FAIL'} "
              f"(expected {kind}, {lo}-{hi} m)")
        if not ok:
            failures.append(f"{label}: {n:.0f} m outside {kind} band {lo}-{hi}")

    print("\nBarrier test -- the walk must go around water, not across it")
    print("  (the gate is the mean detour factor; a single probe can sit on an\n"
          "   open route and legitimately show little detour)\n")
    print(f"  {'location':30} {'network':>9} {'straight':>9} {'detour':>7}")
    blons = [b[1] for b in BARRIERS]
    blats = [b[2] for b in BARRIERS]
    bnet, bline, _ = measure(blons, blats, dist, keep, tree, st_tree, to_proj)
    detours = []
    for (label, _lo, _la), n, s in zip(BARRIERS, bnet, bline):
        ratio = n / max(s, 1)
        detours.append(ratio)
        print(f"  {label:30} {n:7.0f} m {s:7.0f} m {ratio:6.2f}x")
    print(f"  {'mean detour factor':30} {'':9} {'':9} {np.mean(detours):6.2f}x  "
          f"{'PASS' if np.mean(detours) >= 1.5 else 'FAIL'} (need >=1.50x)")
    if np.mean(detours) < 1.5:
        failures.append(
            f"mean barrier detour factor {np.mean(detours):.2f} is too low -- "
            "distances may not be following the network")

    # A station's own location must come back at ~0, which also proves the
    # virtual-source wiring and the snap-offset bookkeeping are right.
    snet, _sline, _ = measure(st["lon"].to_numpy(), st["lat"].to_numpy(),
                              dist, keep, tree, st_tree, to_proj)
    print(f"\nStations measured from their own coordinates: "
          f"median {np.median(snet):.0f} m, p95 {np.percentile(snet, 95):.0f} m, "
          f"max {snet.max():.0f} m")
    if np.median(snet) > 60:
        failures.append(f"stations median self-distance {np.median(snet):.0f} m is too high")

    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All distance checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
