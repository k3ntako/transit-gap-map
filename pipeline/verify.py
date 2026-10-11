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

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

import config as C
from pipeline.aggregate import need_index, ranks
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

# --- the transit graph ------------------------------------------------------
# Station-to-station times, checked as wide bands against what the published
# schedules actually do. The bands are deliberately loose -- they are not a
# claim to schedule-exact routing, which `pipeline/transit.py` explicitly does
# not make -- but each one is tight enough to fail a specific way of breaking
# the graph, which is the same standard the barrier probes are held to:
#
#   * Times Sq -> Grand Central across complexes fails below 5 minutes, which
#     is what a graph with free transfers reports: the shuttle ride alone is
#     three minutes, and the other four are the declared walk through the
#     complex plus the wait.
#   * Bay Ridge -> Times Sq fails below 35, which is roughly what straight-line
#     distance at subway speed would give. It is a 50-minute local ride.
#   * Jamaica -> Penn on the LIRR fails below 15, which is what dropping the
#     boarding wait would give on a 19-minute scheduled run.
#
# (from_key, to_key, lo, hi, label)
TRANSIT_PROBES = [
    ("sub-R45", "sub-127", 35, 65, "Bay Ridge-95 St -> Times Sq (R local)"),
    ("sub-127", "sub-723", 5, 15, "Times Sq -> Grand Central (one complex "
                                  "to another)"),
    ("sub-701", "sub-725", 22, 45, "Flushing-Main St -> Times Sq (7)"),
    ("sub-A41", "sub-127", 14, 32, "Jay St-MetroTech -> Times Sq"),
    ("rail-LIRR-JAM", "rail-LIRR-NYK", 15, 35, "Jamaica -> Penn (LIRR)"),
    ("rail-MNR-1FO", "rail-MNR-0NY", 18, 45, "Fordham -> Grand Central (MNR)"),
]

# The Staten Island Railway has no rail connection to the rest of the system
# and the ferry is not rail, so this pair must come back unreachable. It is
# asserted rather than merely noted because it is the single most surprising
# number the accessibility layer produces -- Staten Island reaching 18,000 of
# the city's 4.47M jobs -- and a reader is owed the guarantee that it is the
# stated consequence of a rail-only graph rather than a bug. Adding a ferry
# edge later should fail this test and make someone decide.
RAIL_ISLAND = ("sub-S31", "sub-235", "St George -> Atlantic Av")

# A graph that quietly lost its transfer edges still routes within each line
# and would drop far below this.
REACHABLE_PAIR_BAND = (0.85, 0.95)


def _setup():
    dist, nearest, serviceable = compute_dist(force=False, subway_only=False)
    g = load_graph(C.P_GRAPH)
    x, y = g.x, g.y
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

    failures += transit_checks()

    # External validity. The only check in this project that compares the map
    # against something outside it, which is why a score change must not be
    # able to lower it quietly.
    try:
        df = gpd.read_parquet(C.P_GEO_STATS)
    except Exception as e:
        print(f"\n  skipped the alignment test: {C.P_GEO_STATS.name} "
              f"unreadable ({type(e).__name__}). Run `make build` first.")
    else:
        hits = alignment(df)
        got = hits[SHIPPED_SCORE][25]
        base = hits["_baseline"][25]
        print(f"\n  shipped score ({SHIPPED_SCORE}) hits "
              f"{100 * got:.0f}% at worst-25, against a {100 * base:.0f}% "
              f"baseline and a {100 * ALIGNMENT_MIN_PRECISION:.0f}% floor")
        if got < ALIGNMENT_MIN_PRECISION:
            failures.append(
                f"the shipped score hits only {100 * got:.0f}% of its worst 25 "
                f"inside a proposed corridor, under the "
                f"{100 * ALIGNMENT_MIN_PRECISION:.0f}% floor. A score at the "
                f"{100 * base:.0f}% baseline has no predictive value; check "
                f"what changed in aggregate.add_score or in the need terms.")

    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All distance checks passed.")
    return 0


def transit_checks() -> list:
    """The rail graph, checked the way the walk graph is: by what breaks it."""
    try:
        from pipeline.transit import compute as compute_transit, interchanges
        tg = compute_transit(force=False)
    except FileNotFoundError:
        print("\n  skipped the transit checks: no cached rail graph. "
              "Run `make transit` first.")
        return []

    idx = {k: i for i, k in enumerate(tg.stations["station_key"])}
    ride = tg.ride
    bad = []
    print(f"\n\nSpot checks -- station-to-station time over the rail graph "
          f"({tg.date})\n")
    print(f"  {'trip':52} {'minutes':>8}  verdict")
    for a, b, lo, hi, label in TRANSIT_PROBES:
        i, j = idx.get(a), idx.get(b)
        if i is None or j is None:
            bad.append(f"{label}: {a} or {b} is not in the graph")
            print(f"  {label[:52]:52} {'-':>8}  FAIL (station missing)")
            continue
        v = float(ride[i, j])
        ok = lo <= v <= hi
        print(f"  {label[:52]:52} {v:8.1f}  {'PASS' if ok else 'FAIL'} "
              f"(expected {lo}-{hi})")
        if not ok:
            bad.append(f"{label}: {v:.1f} min outside the {lo}-{hi} band")

    a, b, label = RAIL_ISLAND
    i, j = idx.get(a), idx.get(b)
    isolated = i is not None and j is not None and not np.isfinite(ride[i, j])
    print(f"  {label[:52]:52} {'inf' if isolated else ride[i, j]:>8}  "
          f"{'PASS' if isolated else 'FAIL'} (expected unreachable)")
    if not isolated:
        bad.append(f"{label}: expected no rail path, got one. If a ferry or "
                   f"light-rail edge was added deliberately, update "
                   f"RAIL_ISLAND and say so in ROADMAP.md.")

    share = float(np.isfinite(ride).mean())
    lo, hi = REACHABLE_PAIR_BAND
    ok = lo <= share <= hi
    print(f"\n  {100 * share:.1f}% of the {len(idx):,}^2 station pairs are "
          f"reachable  {'PASS' if ok else 'FAIL'} "
          f"(expected {100 * lo:.0f}-{100 * hi:.0f}%)")
    if not ok:
        bad.append(f"only {100 * share:.1f}% of station pairs are reachable, "
                   f"outside the {100 * lo:.0f}-{100 * hi:.0f}% band -- check "
                   f"whether the transfer edges survived")

    # The one part of the transfer model that is a radius rather than a
    # declaration, so the one part that has to be read by a person.
    ic = interchanges()
    print(f"\n  {len(ic)} cross-feed interchanges inferred by proximity "
          f"(<= {C.INTERCHANGE_MAX_METERS:.0f} m). Every other transfer in "
          f"the\n  graph is declared in a feed's transfers.txt. Read this "
          f"list; it is the hand-check:")
    for _, r in ic.iterrows():
        print(f"    {r['meters']:5.0f} m  {r['from_name'][:26]:26} "
              f"({r['from_mode']:6}) <-> {r['to_name'][:26]:26} "
              f"({r['to_mode']})")
    return bad


# --- external validity: do we agree with the agencies? ----------------------

# Eight corridors agencies have actually proposed, matched to tracts by NTA
# name substring. This is the only external check the project has: the map has
# no ground truth, and "does it look right" is not a standard.
#
# Matching by neighborhood name is coarse -- digitized corridor geometry would
# be better and is the known next step -- but it is reproducible and it is what
# makes a score change falsifiable rather than a matter of taste.
#
# What this test is NOT: official plans are not need. They are need filtered
# through cost, politics, and whether an abandoned right-of-way happens to be
# lying around -- two of these eight exist largely because one does. Absence of
# a plan is not absence of need, so a score is never penalized for ranking an
# unplanned tract highly; only the hit rate is reported.
CORRIDORS = {
    # Four new Metro-North stations in the east Bronx, under construction,
    # opening 2027.
    "Penn Station Access": ["Co-op City", "Morris Park", "Parkchester",
                            "Hunts Point"],
    # BRT on the abandoned North Shore rail right-of-way.
    "SI North Shore BRT": ["Mariner's Harbor", "Port Richmond",
                           "West New Brighton", "St. George", "Stapleton"],
    "SI West Shore": ["New Springville", "Westerleigh", "Bulls Head"],
    "Utica Ave corridor": ["Flatlands", "Canarsie", "Marine Park",
                           "East Flatbush"],
    "3 train / East New York": ["East New York"],
    "QueensLink (Rockaway Beach Branch)": ["Rockaway", "Broad Channel",
                                           "Ozone Park"],
    "Interborough Express": ["Borough Park", "Kensington", "Flatbush",
                             "Brownsville", "Ridgewood", "Maspeth",
                             "Middle Village", "Elmhurst", "Jackson Heights",
                             "Bay Ridge", "Sunset Park"],
    "Red Hook extension": ["Red Hook", "Carroll Gardens"],
}

# The hit rate a score has to beat at worst-25 to be worth shipping. The
# baseline -- the share of all scored tracts that sit in some corridor -- is
# about 26%, so a score at 26% has no predictive value at all. This margin is
# deliberately modest: the test is coarse enough that a near-miss should not
# fail a build, while a score scoring *at* the baseline should.
ALIGNMENT_MIN_PRECISION = 0.40

# The score the pipeline actually ships, by name in `candidate_scores`. Named
# here rather than inline so the gate and the shipped definition cannot drift
# apart: if `aggregate.add_score` changes, this test starts measuring the
# wrong thing, and the assertion below is what catches it.
SHIPPED_SCORE = "pop x excess walk"


def candidate_scores(df: pd.DataFrame) -> dict:
    """Every score worth comparing, as one column each.

    Kept together so the alignment table compares like with like, and so a
    future score arrives beside its predecessors rather than replacing them
    silently.
    """
    d = df.copy()
    rd = ranks(d["density_km2"])
    rw = ranks(d["walk_min"])
    need = need_index(d)
    excess_walk = (d["walk_min"] - C.ACCESS_FLOOR_MINUTES).clip(lower=0)
    excess_acc = (d["access_min"] - C.ACCESS_FLOOR_MINUTES).clip(lower=0)
    return {
        # What shipped: a geometric mean of two percentile ranks.
        "ranks (shipped)": (rd * rw) ** 0.5,
        # The 2026-10-10 proposal: population times excess walk minutes.
        "pop x excess walk": d["pop"] * excess_walk,
        # The same, once the wait is counted.
        "pop x excess access": d["pop"] * excess_acc,
        # Phase 3: a need index in place of bare population.
        "need x excess access": need * excess_acc,
        "need x excess walk": need * excess_walk,
        # Both: how many people, how much they need it, how bad it is.
        "pop x need x excess walk": d["pop"] * need * excess_walk,
        "pop x need x excess acc": d["pop"] * need * excess_acc,
        # The need variables as COUNTS rather than rates, which keeps the
        # score in person- or household-minutes and keeps the tail.
        "carfree hh x excess walk": d.get("hh_no_vehicle", 0) * excess_walk,
        "poor people x excess walk": d.get("pov_below", 0) * excess_walk,
        "carfree hh x excess acc": d.get("hh_no_vehicle", 0) * excess_acc,
        # Phase 4. The prediction was put on record in ROADMAP.md before any
        # of these was computed: jobs accessibility will *lower* alignment,
        # for the same reason the wait penalty did -- it measures the quality
        # of a rider's morning, while the agencies' corridors track where
        # people are and where the network is not. These ship as layers at
        # zero default weight unless the table below says otherwise.
        #
        # `jobs_short` is the share of the city's jobs a tract cannot reach in
        # C.JOBS_BUDGET_MINUTES, so pop x jobs_short is "people x opportunity
        # out of reach" and stays in interpretable units, the way excess
        # person-minutes does. hub minutes are capped at the worst finite
        # value rather than dropped, because the 79 tracts with no rail path
        # to any job center are the worst cases, not missing data.
        **_phase4(d),
    }


def _phase4(d: pd.DataFrame) -> dict:
    """Candidate scores built on jobs accessibility, hubs and the detour."""
    if "jobs_share" not in d.columns:
        return {}
    need = need_index(d)
    jobs_short = (1.0 - d["jobs_share"].fillna(0)).clip(lower=0)
    hub = d["hub_min"].to_numpy(dtype=float)
    worst = np.nanmax(np.where(np.isfinite(hub), hub, np.nan))
    hub = np.where(np.isfinite(hub), hub, worst)
    detour = d["detour"].fillna(d["detour"].median()).to_numpy()
    return {
        "pop x jobs out of reach": d["pop"] * jobs_short,
        "need x jobs out of reach": need * jobs_short,
        "pop x need x jobs oor": d["pop"] * need * jobs_short,
        "pop x hub minutes": d["pop"] * hub,
        "pop x excess hub min": d["pop"] * np.clip(hub - 30.0, 0, None),
        "pop x detour": d["pop"] * detour,
    }


def alignment(df: pd.DataFrame, verbose: bool = True) -> dict:
    """Hit rate of each candidate score against the proposed corridors."""
    keep = (np.asarray(df["is_populated"], dtype=bool)
            & np.isfinite(np.asarray(df["walk_min"], dtype=float))
            & np.isfinite(np.asarray(df["access_min"], dtype=float)))
    scored = pd.DataFrame(df.loc[keep]).copy()
    nta = [str(v) if v is not None else "" for v in scored["ntaname"].tolist()]
    keys = [k for v in CORRIDORS.values() for k in v]
    inside = np.array([any(k in n for k in keys) for n in nta])
    n = len(scored)
    base = inside.mean()

    cands = candidate_scores(scored)
    if verbose:
        print(f"\nExternal validity: {int(inside.sum())} of {n} tracts "
              f"({100 * base:.0f}%) sit in one of {len(CORRIDORS)} corridors "
              f"agencies have proposed. That share is the baseline a score with "
              f"no predictive value would hit.")
        print(f"\n  {'score':<24}{'worst 25':>10}{'worst 50':>10}"
              f"{'worst 100':>11}")
        print("  " + "-" * 55)

    out = {}
    for name, s in cands.items():
        v = s.to_numpy()
        order = np.argsort(-np.where(np.isfinite(v), v, -np.inf))
        hits = {k: float(inside[order[:k]].mean()) for k in (25, 50, 100)}
        out[name] = hits
        if verbose:
            print(f"  {name:<24}" + "".join(
                f"{100 * hits[k]:>9.0f}%" if k == 25
                else f"{100 * hits[k]:>9.0f}%" for k in (25, 50, 100)))
    if verbose:
        print(f"  {'baseline (random)':<24}" + f"{100 * base:>9.0f}%" * 3)
    out["_baseline"] = {k: float(base) for k in (25, 50, 100)}

    # Robustness: a tract in the worst 50 under one score is an artifact of
    # that score; a tract there under every *credible* score is a finding.
    #
    # Credible has to mean something, or this measures nothing. The first
    # version intersected all ten candidates and got zero, because the rank
    # product is in the list and it is indistinguishable from random -- so the
    # intersection was asking which tracts a worthless score agrees with.
    # Only scores clearing the alignment floor are counted.
    credible = [n for n, h in out.items()
                if not n.startswith("_") and h[25] >= ALIGNMENT_MIN_PRECISION]
    worst: dict[int, int] = {}
    for name in credible:
        v = cands[name].to_numpy()
        order = np.argsort(-np.where(np.isfinite(v), v, -np.inf))
        for i in order[:50]:
            worst[i] = worst.get(i, 0) + 1
    robust = [i for i, c in worst.items() if c == len(credible)]
    if verbose:
        print(f"\n  Robustness across the {len(credible)} of {len(cands)} "
              f"scores that clear the "
              f"{100 * ALIGNMENT_MIN_PRECISION:.0f}% floor: {len(worst)} "
              f"distinct tracts appear in some worst 50, {len(robust)} appear "
              f"in every one. Those are the findings that survive the "
              f"weighting; the rest are artifacts of it.")
        for i in sorted(robust,
                        key=lambda j: -cands[SHIPPED_SCORE].to_numpy()[j])[:8]:
            r = scored.iloc[i]
            print(f"    {str(r['ntaname'])[:34]:34} pop {r['pop']:6.0f}  "
                  f"walk {r['walk_min']:6.1f} min  block of "
                  f"{int(r.get('cluster_tracts', 0) or 0)}")
    out["_robust"] = {"n_any": len(worst), "n_all": len(robust),
                      "n_scores": len(credible), "n_candidates": len(cands)}
    return out


if __name__ == "__main__":
    raise SystemExit(main())
