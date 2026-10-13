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
    # The ferry. 25 minutes of crossing on every trip in the feed, plus an
    # expected wait of half the 8-9am headway (four sailings an hour, so 7.5),
    # is 32.5 -- and this band fails if either half is dropped: lose the
    # crossing time and it reads ~8, lose the wait and it reads 25.
    ("ferry-SIF-stgeorge", "ferry-SIF-whitehall", 26, 41,
     "St George -> Whitehall (ferry)"),
    # And the interchange at each end, which is what actually connects Staten
    # Island to the system. Fails if either 130 m walk stops being inferred.
    ("sub-S31", "sub-418", 40, 75, "St George SIR -> Fulton St (via ferry)"),
]

# **This used to assert the opposite.** Until 2026-10-11 the graph was rail
# only, so St George -> Atlantic Av had to come back *unreachable*, and the
# assertion existed to guarantee that Staten Island reaching 18,000 of the
# city's 4.47M jobs was the stated consequence of a rail-only graph rather
# than a bug. It also existed to make exactly this decision deliberate: the
# comment said adding a ferry edge should fail the test and make someone
# decide. It did, and the decision was to add it -- see config.GTFS_FERRY_FEED
# for why a ferry belongs in a graph that excludes buses, and ROADMAP.md for
# what it changed. The pair is kept, with the sign flipped: it is now the
# check that the connection did not quietly disappear again.
ISLAND_LINK = ("sub-S31", "sub-235", 45, 80,
               "St George -> Atlantic Av (was asserted unreachable)")

# A graph that quietly lost its transfer edges still routes within each line
# and would drop far below this. Raised from 0.85-0.95 when the ferry edge
# took the measured share from 89.2% to 94.6%: with Staten Island attached
# there is no longer a disconnected island of any size in the graph, so the
# residual is end-of-line commuter-rail pairs that genuinely have no path.
REACHABLE_PAIR_BAND = (0.90, 0.98)


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

    # A source's own location must come back at ~0, which also proves the
    # virtual-source wiring and the snap-offset bookkeeping are right. The
    # sources are the station entrances now, so that is what this measures:
    # since 2026-10-12 a station *point* is no longer a source where the
    # entrance table covers it, and measuring from one legitimately returns
    # the walk to the nearest door rather than zero.
    from pipeline.stations import load_entrances
    ent = load_entrances(verbose=False) if C.USE_STATION_ENTRANCES else None
    if ent is not None and len(ent):
        enet, _el, _ = measure(ent["lon"].to_numpy(), ent["lat"].to_numpy(),
                               dist, keep, tree, st_tree, to_proj)
        print(f"\nEntrances measured from their own coordinates: "
              f"median {np.median(enet):.0f} m, "
              f"p95 {np.percentile(enet, 95):.0f} m, max {enet.max():.0f} m")
        if np.median(enet) > 60:
            failures.append(
                f"entrances median self-distance {np.median(enet):.0f} m is "
                f"too high")
    snet, _sline, _ = measure(st["lon"].to_numpy(), st["lat"].to_numpy(),
                              dist, keep, tree, st_tree, to_proj)
    # Reported, not gated. This is now a real quantity rather than a tautology
    # -- how far a station's labeled point is from the nearest way in, along
    # the network -- and the old model treated it as zero for everybody. The
    # tail is the interesting part: a point dropped in a park or on the far
    # side of a cut reads in the hundreds of meters.
    worst = int(np.argmax(snet))
    print(f"Station points, by the walk to their own nearest door: "
          f"median {np.median(snet):.0f} m, p95 {np.percentile(snet, 95):.0f} m, "
          f"max {snet.max():.0f} m at {st['name'].to_numpy()[worst]}")

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
        cl = hits["_clump"][SHIPPED_SCORE]["precision"]
        floor = base + ALIGNMENT_CLUSTER_MARGIN
        print(f"\n  shipped score ({SHIPPED_SCORE}) hits "
              f"{100 * got:.0f}% at worst-25 per {C.GEO_LEVEL} and "
              f"{100 * cl:.0f}% over its clumps,\n  against a "
              f"{100 * base:.0f}% baseline. The clump figure is the one that "
              f"gates, at\n  baseline + {100 * ALIGNMENT_CLUSTER_MARGIN:.0f} "
              f"= {100 * floor:.0f}%: a rail line serves a strip of "
              f"neighborhoods, not a\n  {C.GEO_LEVEL}, and the floor is set "
              f"from the baseline rather than from how well\n  this score "
              f"does, so the gate asks whether the score is noise and not "
              f"whether\n  it agrees with the agencies.")
        if not np.isfinite(cl) or cl < floor:
            failures.append(
                f"the shipped score's clumped hit rate is "
                f"{100 * cl:.0f}%, under the {100 * floor:.0f}% floor "
                f"(baseline {100 * base:.0f}% + "
                f"{100 * ALIGNMENT_CLUSTER_MARGIN:.0f}). A score at the "
                f"baseline has no predictive value; check what changed in "
                f"aggregate.add_score or in the need terms. A non-finite "
                f"value instead means the worst {CLUSTER_PROBE_K} "
                f"{C.GEO_LEVEL}s formed no contiguous block of "
                f"{CLUSTER_MIN_TRACTS}, which is itself the finding.")

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

    a, b, lo, hi, label = ISLAND_LINK
    i, j = idx.get(a), idx.get(b)
    v = float(ride[i, j]) if i is not None and j is not None else float("inf")
    ok = lo <= v <= hi
    print(f"  {label[:52]:52} {v:8.1f}  {'PASS' if ok else 'FAIL'} "
          f"(expected {lo}-{hi})")
    if not ok:
        bad.append(f"{label}: {v:.1f} min outside the {lo}-{hi} band. This "
                   f"pair was unreachable before the ferry edge, so an inf "
                   f"here means the ferry feed or one of its two 130 m "
                   f"interchanges is gone.")

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
#
# **This is reported, not gated, as of 2026-10-11.** See the cluster measure
# below for which number fails the build and why the per-tract one stopped.
ALIGNMENT_MIN_PRECISION = 0.40

# --- the same test, with a clump as the unit ---------------------------------
# A rail line serves a strip of neighborhoods, not a tract, and the measure
# above counts tracts one at a time. So a score that scatters its worst tracts
# across five boroughs can tie a score that puts all of them in one solid
# band, even though only the second describes something a line could fix.
# Phase 4 made that overdue rather than merely untidy: sixteen candidate
# scores are now compared this way and six of them arrived in one commit.
#
# The fix is to ignore the singletons. Take each score's worst
# CLUSTER_PROBE_K tracts, group them into contiguous blocks over the same
# land adjacency `aggregate.add_clusters` uses, keep the blocks of at least
# CLUSTER_MIN_TRACTS, and measure the corridor hit rate over *those* tracts
# only. The statistic stays directly comparable to the per-tract one --
# it is a share of tracts against the same baseline -- while the unit being
# judged is a clump.
#
# What is deliberately *not* done here: digitizing the eight corridors as
# geometry. Four of them follow rights-of-way that are already in the OSM
# extract (the Bay Ridge Branch, the Rockaway Beach Branch, the North Shore
# right-of-way, the Hell Gate Line) and four have no published alignment at
# all, so half the work would be drawing lines by hand and the test would
# partly be measuring the drawing. ROADMAP.md carries the decision.
CLUSTER_PROBE_K = 100
CLUSTER_MIN_TRACTS = 3

# **What this gate is for, and what it is deliberately not for.** Official
# plans are not need: they are need filtered through cost, politics, and
# whether an abandoned right-of-way happens to be lying around. Aiming the
# score at them would import all of that, so the floor here is set from the
# *baseline* -- the share of all tracts that sit in some corridor -- plus a
# margin, and never from how well the shipped score currently does. The gate
# answers "is this score noise?" and refuses to answer "does this score agree
# with the MTA?", because a floor set near the shipped score's own number is
# precisely the mechanism that would turn agreement into a target. Measured,
# the shipped score clears this by a wide margin, and the interesting output
# is the disagreement table underneath it rather than the pass.
ALIGNMENT_CLUSTER_MARGIN = 0.10

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
        # What shipped until 2026-10-10: a geometric mean of two percentile
        # ranks. Named "original" and not "shipped", because SHIPPED_SCORE is
        # `pop x excess walk` and a table with two rows claiming to be the
        # shipped one is a table nobody can read -- least of all now that the
        # clump column puts this row last at 12%.
        "ranks (original)": (rd * rw) ** 0.5,
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
    out = {
        "pop x jobs out of reach": d["pop"] * jobs_short,
        "need x jobs out of reach": need * jobs_short,
        "pop x need x jobs oor": d["pop"] * need * jobs_short,
        "pop x hub minutes": d["pop"] * hub,
        "pop x excess hub min": d["pop"] * np.clip(hub - 30.0, 0, None),
        "pop x detour": d["pop"] * detour,
    }
    if "jobs_time" not in d.columns:
        return out
    # The same jobs data with the axes swapped -- fix the jobs, measure the
    # time -- and it is in the table for a specific reason. The three
    # `jobs out of reach` rows above are built on a *censored* field: 1,062
    # of 2,325 tracts cannot reach a tenth of the city's jobs inside the
    # budget at all, so their values saturate near zero and stop ordering.
    # The prediction Phase 4 recorded -- that jobs accessibility would lower
    # alignment -- held, but the mechanism it inferred was the factor's
    # dynamic range, and the censoring is what was crushing the range.
    # Uncensor it and the same data goes from 16% to 56%. `config` carries
    # the numbers. Infinite values are capped at the worst finite one for the
    # same reason hub minutes are: those tracts are the worst cases, not
    # missing data.
    jt = d["jobs_time"].to_numpy(dtype=float)
    cap = np.nanmax(np.where(np.isfinite(jt), jt, np.nan))
    jt = np.where(np.isfinite(jt), jt, cap)
    out["pop x time to jobs"] = d["pop"] * jt
    out["pop x excess time to jobs"] = d["pop"] * np.clip(
        jt - C.JOBS_BUDGET_MINUTES, 0, None)
    # The only candidate in the table with no population term at all. It is
    # here because it scores like the shipped score without one, which is
    # the single most surprising row the test has produced: Spearman -0.22
    # against population, so it is not a population ranking in disguise.
    out["time to jobs alone"] = pd.Series(jt, index=d.index)
    return out


def _adjacency(scored: pd.DataFrame, crs):
    """Land adjacency over the scored geographies, as a sparse matrix.

    The same relation `aggregate.add_clusters` uses, and for the same reason:
    the DCP polygons exclude water, so tracts facing each other across the
    East River do not touch and no bridge edges are invented. One sjoin over
    the whole set here, rather than per score, because every score reuses it.
    """
    from scipy.sparse import coo_matrix

    geom = gpd.GeoSeries(scored["geometry"].to_numpy(), crs=crs)
    sub = gpd.GeoDataFrame(geometry=geom.reset_index(drop=True))
    pairs = gpd.sjoin(sub, sub, how="inner", predicate="touches")
    r = pairs.index.to_numpy()
    c = pairs["index_right"].to_numpy()
    n = len(sub)
    return coo_matrix((np.ones(len(r)), (r, c)), shape=(n, n)).tocsr()


def _clumps(adj, order: np.ndarray, k: int, min_tracts: int):
    """The contiguous blocks among a score's worst `k` geographies.

    Returns the selected rows that sit in a block of at least `min_tracts`,
    and the block sizes. A block below that size is a complaint rather than a
    project -- a tract ringed by well-served ones is a bus-stop problem, a
    park, or a measurement artifact -- and the whole point of this measure is
    to stop counting those as evidence for or against a score.
    """
    from scipy.sparse.csgraph import connected_components

    sel = order[:k]
    n_comp, labels = connected_components(adj[sel][:, sel], directed=False)
    sizes = np.bincount(labels, minlength=n_comp)
    big = np.flatnonzero(sizes >= min_tracts)
    keep = np.isin(labels, big)
    return sel[keep], labels[keep], sizes[big]


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
        print(f"\n  {'score':<24}{'worst 25':>9}{'worst 50':>9}"
              f"{'worst 100':>9}{'clumped':>9}{'blocks':>8}{'largest':>8}")
        print("  " + "-" * 76)
        print(f"  the last three columns are the same test with a clump as "
              f"the unit: of this\n  score's worst {CLUSTER_PROBE_K} "
              f"{C.GEO_LEVEL}s, the ones in a contiguous block of "
              f"{CLUSTER_MIN_TRACTS}+,\n  how many sit in a proposed "
              f"corridor -- and how many such blocks there are.")

    adj = _adjacency(scored, getattr(df, "crs", None))
    orders = {}
    out = {}
    clump = {}
    for name, s in cands.items():
        v = s.to_numpy()
        order = np.argsort(-np.where(np.isfinite(v), v, -np.inf))
        orders[name] = order
        hits = {k: float(inside[order[:k]].mean()) for k in (25, 50, 100)}
        out[name] = hits
        rows, _lab, sizes = _clumps(adj, order, CLUSTER_PROBE_K,
                                   CLUSTER_MIN_TRACTS)
        clump[name] = {
            "precision": (float(inside[rows].mean()) if rows.size
                          else float("nan")),
            "n_blocks": int(sizes.size),
            "n_tracts": int(rows.size),
            "largest": int(sizes.max()) if sizes.size else 0,
        }
        if verbose:
            c = clump[name]
            pc = "-" if rows.size == 0 else f"{100 * c['precision']:.0f}%"
            print(f"  {name:<24}" + "".join(
                f"{100 * hits[k]:>9.0f}%" for k in (25, 50, 100))
                + f"{pc:>9}{c['n_blocks']:>8}{c['largest']:>8}")
    if verbose:
        print(f"  {'baseline (random)':<24}" + f"{100 * base:>9.0f}%" * 3
              + f"{100 * base:>8.0f}%")
    out["_baseline"] = {k: float(base) for k in (25, 50, 100)}
    out["_clump"] = clump

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

    # --- where we disagree with the agencies, and roughly why ---------------
    # This is the output the cluster measure exists for. An agency's plan is
    # need filtered through cost, politics, and whether a right-of-way is
    # lying around, so a block the score ranks badly and nobody has proposed
    # anything for is not a failure of the score -- it is the thing worth
    # looking at. Printing it per block, with the overlap, is what turns the
    # hit rate from a target into a diagnostic.
    rows, labels, sizes = _clumps(adj, orders[SHIPPED_SCORE],
                                  CLUSTER_PROBE_K, CLUSTER_MIN_TRACTS)
    out["_disagree"] = []
    if verbose and rows.size:
        print(f"\n  The shipped score's worst {CLUSTER_PROBE_K} "
              f"{C.GEO_LEVEL}s form {sizes.size} blocks of "
              f"{CLUSTER_MIN_TRACTS}+ ({rows.size} {C.GEO_LEVEL}s;\n  the "
              f"other {CLUSTER_PROBE_K - rows.size} are scattered and are "
              f"left out). Per block, overlap with a proposed corridor:")
    seen = sorted(set(labels.tolist()),
                  key=lambda b: -int((labels == b).sum()))
    for b in seen:
        m = rows[labels == b]
        ov = float(inside[m].mean())
        where = (scored.iloc[m]["ntaname"].value_counts().head(3)
                 .index.tolist())
        boro = str(scored.iloc[m]["boroname"].mode().iat[0])
        rec = {"n_tracts": int(m.size),
               "pop": int(scored.iloc[m]["pop"].sum()),
               "overlap": round(ov, 3), "boro": boro,
               "where": [str(w) for w in where]}
        out["_disagree"].append(rec)
        if verbose:
            print(f"    {m.size:3} {C.GEO_LEVEL}s  {rec['pop']:8,} people  "
                  f"corridor overlap {100 * ov:3.0f}%  {boro[:13]:13} "
                  f"{', '.join(str(w)[:20] for w in where)}")
    if verbose:
        unplanned = [r for r in out["_disagree"] if r["overlap"] < 0.25]
        if unplanned:
            pop = sum(r["pop"] for r in unplanned)
            print(f"\n  {len(unplanned)} of those blocks sit mostly outside "
                  f"any proposed corridor ({pop:,} people).\n  That is the "
                  f"disagreement, and it is not a defect in either measure: "
                  f"absence of a\n  plan is not absence of need, and this "
                  f"test never penalizes a score for\n  ranking an "
                  f"unplanned {C.GEO_LEVEL} highly.")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
