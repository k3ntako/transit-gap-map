"""Stage 4b: a frequency-based static transit graph over rail, and the
station-to-station travel-time matrix it produces.

Distance to the nearest station treats a station as a destination; it is
really an on-ramp. Eastern Queens is a short walk from an LIRR stop that runs
hourly, and Bay Ridge is a short walk from the R, which is a long local ride
to Midtown. Only time-to-somewhere separates those, and this stage is what
makes time-to-somewhere computable.

**Rail plus the Staten Island Ferry; no buses.** Buses are in neither this
graph nor the score, and the reason is about what the map claims rather than
what data exists: bus service is what the city provides *instead of* rail in
precisely the places this map exists to find, so letting a bus close the gap
would hide the finding by construction. The frequent-bus-network layer keeps
the job of showing what runs today, drawn on top of the score rather than
netted off it.

The ferry is the one exception and it is not a softening of that rule, it is
the rule applied: the Staten Island Ferry substitutes for nothing. It is the
only fixed-guideway trunk link across the harbor, on its own right of way at
a published headway, and no proposal replaces it. Leaving it out does not
produce a conservative number, it produces a false one -- the rail-only graph
answered "how does a Staten Islander reach a job" with 18,000 of 4.47M for
495,000 people, which is true of rail and false of transit. The operative
line is dedicated right-of-way trunk service against street-running service
sharing its lanes with traffic; `config.GTFS_FERRY_FEED` carries the argument
and the measured feed facts, and `verify.py` probes the crossing so the edge
cannot drift.

Four things in here decide whether the numbers mean anything:

1. **Nodes are (station, route, direction), not stations.** This is a
   correctness fix, not an optimization. With one node per station, every
   route serving it shares that node, so a shortest path changes vehicles at
   no cost -- transfers become free and the graph claims a connectivity the
   city does not have, which is the opposite of what this measures.
2. **Changing vehicles costs time, and the time is declared.** The subway
   feed ships 463 same-parent transfers (median 180 s) and 150
   between-station ones (median 180 s, max 420 s), so the subway interchange
   set is a file rather than a guess. LIRR and Metro-North declare same-stop
   transfers only, and as *trip-to-trip* timed transfers that a static graph
   cannot use, so they fall back to `C.TRANSFER_PENALTY_MINUTES` -- which is
   itself the subway's declared median rather than an invention.
3. **Arrival and departure are separate nodes.** Otherwise the in-station
   change time would be charged to a rider who merely boards at that station,
   or to one who ends their trip there. Access edges enter at the departure
   node and egress edges leave from the arrival node, so the transfer penalty
   is paid only by riders who actually transfer.
4. **A scheduled stop is not a boardable stop**, exactly as in stage 3: the
   commuter-rail feeds mark drop-off-only stops with `pickup_type=1`, and
   counting those as service would overstate boarding frequency at the
   stations where it matters most.

Honest limitation, to be repeated wherever the output is shown: a static graph
with expected waits approximates a typical weekday peak. It cannot represent
departure-time effects -- a just-missed connection, a timed transfer that
holds. If that becomes the binding constraint, a RAPTOR over `stop_times` is
the upgrade, and a project of its own.

Run:  python -m pipeline.transit [--force]
"""

from __future__ import annotations

import argparse
import time
import zipfile
from typing import NamedTuple

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import Point

import config as C
from pipeline.service import _read, active_services, feed_window, pick_dates

# Which station_key prefix each feed produces, matching pipeline/stations.py so
# the keys join straight onto stations.parquet and station_service.parquet.
RAIL_AGENCY = {"lirr": "LIRR", "mnr": "MNR"}

# The ferry has no row in the station table -- it is not rail, and stage 3
# does not measure it -- so its keys are this stage's own and are prefixed
# distinctly rather than borrowed from either scheme above.
FERRY_MODE = "siferry"


def _prefix(mode: str) -> str:
    """The station_key prefix for one feed's stop ids."""
    if mode == "subway":
        return "sub-"
    if mode == FERRY_MODE:
        return "ferry-SIF-"
    return f"rail-{RAIL_AGENCY[mode]}-"

# A single consecutive-stop runtime longer than this is a data error, not a
# slow train: the longest real gap on the system is Jamaica to Penn Station on
# an express, well under 30 minutes.
MAX_RIDE_SECONDS = 3600


class TransitGraph(NamedTuple):
    """The rail graph's outputs: a station table and a station-to-station time.

    `ride[s, t]` is minutes from boarding at station s to arriving at station
    t, so it already contains the first boarding wait and every transfer on
    the way. The diagonal is zero: "ride from s to s" is the no-train path,
    which keeps a walk-in-walk-out trip through one station a legal route
    rather than an infinite one.
    """

    stations: pd.DataFrame     # station_key index order == matrix order
    ride: np.ndarray           # (S, S) float32 minutes
    date: str


def _station_ids(zf: zipfile.ZipFile, mode: str) -> pd.DataFrame:
    """Every stop in one feed, resolved to a station and its coordinates.

    The subway feed is hierarchical, so a platform resolves through
    `parent_station` -- explicitly, not by stripping the N/S suffix, which is
    a naming convention and not a guarantee. The commuter-rail feeds are flat
    and carry the public `stop_code` the station table calls `code`.

    Unlike stage 3 this keeps stops that are *not* in our station table: a
    Nassau County LIRR stop is not a place anyone in New York walks to, but a
    Penn-bound train passes through it, and dropping it would break the ride
    edges that pass through. Whether a station is reachable on foot from the
    city is settled later by the walk budget, which is the honest place for it.
    """
    stops: pd.DataFrame = _read(zf, "stops.txt")
    lat = pd.to_numeric(stops["stop_lat"], errors="coerce")
    lon = pd.to_numeric(stops["stop_lon"], errors="coerce")
    sid = stops["stop_id"].astype(str)

    if mode == "subway":
        parent = stops["parent_station"].fillna("").astype(str)
        root = [p or s for s, p in zip(sid, parent)]
        key = ["sub-" + r for r in root]
    elif mode == FERRY_MODE:
        # Two stops, flat, no parent hierarchy and no stop_code worth using:
        # the ids are already the public names of the two terminals.
        key = [_prefix(mode) + s for s in sid]
    else:
        agency = RAIL_AGENCY[mode]
        has_code = "stop_code" in stops.columns
        raw = stops["stop_code"] if has_code else pd.Series([""] * len(stops))
        code = raw.fillna("").astype(str).str.strip()
        key = [f"rail-{agency}-{c}" if c else f"rail-{agency}-x{s}"
               for s, c in zip(sid, code)]

    out = pd.DataFrame({"stop_id": sid, "station_key": key,
                        "stop_name": stops["stop_name"].astype(str).str.strip(),
                        "lat": lat, "lon": lon, "mode": mode})
    # The station's position is its parent's own row where the feed has one,
    # and the mean of its platforms otherwise. Taking a single platform would
    # offset a station by the length of its platform, which at 400 m matters
    # against C.INTERCHANGE_MAX_METERS.
    return out


def _seconds(s: pd.Series) -> pd.Series:
    """GTFS HH:MM:SS -> seconds past midnight, with hours past 24 intact.

    Not taken modulo anything: this measures a *duration* between two stops on
    one trip, so a trip crossing the service-day boundary must keep its hours
    monotone or every runtime on it comes back negative.
    """
    v = s.astype(str).str.strip().str.rjust(8, "0")

    def part(a: int, b: int) -> np.ndarray:
        return np.asarray(pd.to_numeric(v.str.slice(a, b), errors="coerce"),
                          dtype="float64")

    return pd.Series(part(0, 2) * 3600 + part(3, 5) * 60 + part(6, 8),
                     index=s.index)


def _feed_legs(mode: str, zf: zipfile.ZipFile, date) -> tuple:
    """One feed's ride legs on `date`, and how often each is boardable.

    A **leg** is one consecutive-stop hop on one route in one direction:
    (route, direction, from, to). It is the unit of this graph, and it is a
    refinement of the roadmap's "(stop, route, direction)" node that the data
    forced -- see `compute` for why the next stop has to be part of the key.

    `n_dep` counts boardable departures inside `C.TRANSIT_WINDOW`, so it is
    what sets the wait; `secs` is the median scheduled runtime over the day's
    trips, so a leg that runs twice at 8am is still timed from every trip that
    runs it.
    """
    stations = _station_ids(zf, mode)
    key_of = dict(zip(stations["stop_id"], stations["station_key"]))

    trips: pd.DataFrame = _read(zf, "trips.txt")
    svc = active_services(zf, date)
    run = trips.loc[trips["service_id"].isin(list(svc))].copy()
    if run.empty:
        raise SystemExit(f"{mode}: no services active on {date}")
    if "direction_id" not in run.columns:
        run["direction_id"] = "0"
    run["direction_id"] = run["direction_id"].fillna("0")

    cols = ["trip_id", "stop_id", "arrival_time", "departure_time",
            "stop_sequence"]
    probe: pd.DataFrame = _read(zf, "stop_times.txt", nrows=1)
    has_pickup = "pickup_type" in probe.columns
    if has_pickup:
        cols.append("pickup_type")
    stt: pd.DataFrame = _read(zf, "stop_times.txt", usecols=cols)

    stt = stt.loc[stt["trip_id"].isin(list(run["trip_id"]))].copy()
    stt["station_key"] = stt["stop_id"].map(lambda s: key_of.get(s))
    stt = stt.loc[stt["station_key"].notna()]
    stt["seq"] = pd.to_numeric(stt["stop_sequence"], errors="coerce")
    arr = pd.Series(stt["arrival_time"]).fillna(stt["departure_time"])
    dep = pd.Series(stt["departure_time"]).fillna(stt["arrival_time"])
    stt["arr"] = _seconds(pd.Series(arr, index=stt.index))
    stt["dep"] = _seconds(pd.Series(dep, index=stt.index))
    stt = stt.sort_values(["trip_id", "seq"], kind="stable")
    stt = stt.join(run.set_index("trip_id")[["route_id", "direction_id"]],
                   on="trip_id")

    nxt_key = stt["station_key"].shift(-1)
    nxt_arr = stt["arr"].shift(-1)
    same_trip = stt["trip_id"].shift(-1) == stt["trip_id"]
    dt = nxt_arr - stt["dep"]
    ok = (same_trip & dt.notna() & (dt > 0) & (dt <= MAX_RIDE_SECONDS)
          & (nxt_key != stt["station_key"]))
    # pickup_type 1 is "no pickup available": the train stops but will not
    # board you, so this hop exists as a ride but not as a boarding chance.
    boardable = (stt["pickup_type"].fillna("0") != "1") if has_pickup \
        else pd.Series(True, index=stt.index)
    lo, hi = C.SERVICE_WINDOWS[C.TRANSIT_WINDOW]
    hour = (stt["dep"] // 3600) % 24
    in_win = boardable & (hour >= lo) & (hour < hi)

    hops = pd.DataFrame({
        "route_id": stt.loc[ok, "route_id"].to_numpy(),
        "direction_id": stt.loc[ok, "direction_id"].to_numpy(),
        "from_key": stt.loc[ok, "station_key"].to_numpy(),
        "to_key": nxt_key[ok].to_numpy(),
        "secs": dt[ok].to_numpy(),
        "board": in_win[ok].to_numpy(),
    })
    keys = ["route_id", "direction_id", "from_key", "to_key"]
    legs = pd.DataFrame(hops.groupby(keys, as_index=False).agg(
        secs=("secs", "median"), n_trips=("secs", "size"),
        n_dep=("board", "sum")))
    legs["mode"] = mode

    print(f"  {mode:7} {date}  {len(run):5,} trips -> {len(legs):5,} legs over "
          f"{legs['from_key'].nunique():4} stations; "
          f"{int((legs['n_dep'] > 0).sum()):5,} boardable in "
          f"{C.TRANSIT_WINDOW} ({lo}-{hi})")
    return legs, stations


def _transfers(zfs: dict, stations: pd.DataFrame) -> pd.DataFrame:
    """Declared transfers, plus the cross-feed interchanges nobody declares.

    Inside a feed, `transfers.txt` is authoritative and is used as given --
    including its silences. Two nearby subway stations with no declared
    transfer are not an omission: they are two stations between which there is
    no free change, and inventing one would hand the rider a connection the
    turnstiles do not.

    Across feeds nothing is declared at all, so the dozen rail-to-commuter-rail
    interchanges -- Jamaica, Atlantic Terminal, Woodside, Fordham and a few
    more -- are the one place a radius is still needed. They are charged the
    straight-line walk at `C.WALK_SPEED_MPS`, floored at the same in-station
    change time, and the **fare** of leaving one system for another is not
    modeled. `pipeline/verify.py` prints the pairs so the list stays
    hand-checkable rather than growing silently.
    """
    pos = stations.set_index("station_key")
    rows = []
    for mode, zf in zfs.items():
        t = _read(zf, "transfers.txt")
        if t.empty or "min_transfer_time" not in t.columns:
            continue
        pre = _prefix(mode)
        mt = pd.Series(np.asarray(
            pd.to_numeric(t["min_transfer_time"], errors="coerce"),
            dtype="float64"), index=t.index)
        keep = mt.notna()
        if mode not in ("subway", FERRY_MODE):
            # LIRR and Metro-North declare trip-to-trip timed transfers, which
            # a frequency graph cannot represent. Only the handful of rows
            # carrying a plain minimum time are usable.
            keep &= t["from_stop_id"] == t["to_stop_id"]
        fr, to = t.loc[keep, "from_stop_id"], t.loc[keep, "to_stop_id"]
        for a, b, m in zip(fr, to, mt[keep]):
            ka, kb = pre + str(a), pre + str(b)
            if ka in pos.index and kb in pos.index:
                rows.append((ka, kb, float(m) / 60.0, "declared"))

    cols = ["from_key", "to_key", "minutes", "source"]
    declared = pd.DataFrame(rows, columns=cols)
    known = set(zip(declared["from_key"], declared["to_key"]))

    g = gpd.GeoDataFrame(
        pos.reset_index(),
        geometry=[Point(xy) for xy in zip(pos["lon"], pos["lat"])],
        crs=C.CRS_GEO).to_crs(C.CRS_PROJ)
    xy = np.column_stack([g.geometry.x, g.geometry.y])
    near = cKDTree(xy).query_pairs(C.INTERCHANGE_MAX_METERS,
                                   output_type="ndarray")
    keys, modes = g["station_key"].to_numpy(), g["mode"].to_numpy()

    extra = []
    for i, j in near:
        if modes[i] == modes[j]:
            continue           # inside a feed, transfers.txt has the last word
        d = float(np.hypot(*(xy[i] - xy[j])))
        mins = max(d / C.WALK_SPEED_MPS / 60.0, C.TRANSFER_PENALTY_MINUTES)
        for a, b in ((i, j), (j, i)):
            if (keys[a], keys[b]) not in known:
                extra.append((keys[a], keys[b], mins, "interchange"))

    out = pd.concat([declared, pd.DataFrame(extra, columns=cols)],
                    ignore_index=True)
    # Keep the cheapest claim for any pair declared more than once.
    out = (out.sort_values("minutes", kind="stable")
           .drop_duplicates(["from_key", "to_key"], keep="first"))
    n_self = int((out["from_key"] == out["to_key"]).sum())
    n_inter = int((out["source"] == "interchange").sum())
    print(f"  transfer {len(out):,} edges: {n_self} in-station, "
          f"{len(out) - n_self} between stations ({n_inter} cross-feed by "
          f"proximity <= {C.INTERCHANGE_MAX_METERS:.0f} m)")
    return out


def interchanges(graph: TransitGraph | None = None) -> pd.DataFrame:
    """The cross-feed interchange list, for hand-checking.

    Separate from `_transfers` so `verify.py` can print it without rebuilding
    the graph: this is the one part of the transfer model that is a radius
    rather than a declaration, so it is the one part that has to be read by a
    person.
    """
    st = pd.read_parquet(C.P_TRANSIT_STATIONS)
    g = gpd.GeoDataFrame(
        st, geometry=[Point(xy) for xy in zip(st["lon"], st["lat"])],
        crs=C.CRS_GEO).to_crs(C.CRS_PROJ)
    xy = np.column_stack([g.geometry.x, g.geometry.y])
    near = cKDTree(xy).query_pairs(C.INTERCHANGE_MAX_METERS,
                                   output_type="ndarray")
    rows = []
    for i, j in near:
        if g["mode"].iat[i] == g["mode"].iat[j]:
            continue
        d = float(np.hypot(*(xy[i] - xy[j])))
        rows.append((g["name"].iat[i], g["mode"].iat[i], g["name"].iat[j],
                     g["mode"].iat[j], d,
                     max(d / C.WALK_SPEED_MPS / 60.0,
                         C.TRANSFER_PENALTY_MINUTES)))
    return pd.DataFrame(rows, columns=["from_name", "from_mode", "to_name",
                                       "to_mode", "meters", "minutes"]) \
        .sort_values("meters").reset_index(drop=True)


def compute(force: bool = False) -> TransitGraph:
    """Build the rail graph and solve it for every station pair.

    **Nodes are legs, not stations and not routes.** The roadmap specifies
    `(stop, route, direction)` and gives the reason: with one node per stop,
    every route serving it shares that node, so a shortest path changes
    vehicles at no cost and the graph claims a connectivity the city does not
    have. That argument is right and this keeps it. What the data then forced
    is one more field in the key -- the **next stop** -- because of where the
    boarding wait comes from.

    The wait is `headway / 2`, which is the headway of the service a rider is
    waiting for. Taken per route, that is wrong wherever several routes at one
    station go to the same place next: Metro-North at Fordham runs two
    inbound trains an hour on the Harlem line and two on the New Haven line,
    both to Grand Central, and charging each one a 15-minute wait tells a
    Grand Central-bound rider to wait twice as long as they do. Taken per
    station it is wrong the other way -- at Union Square it would hand an
    L-train rider the combined headway of eight routes. Measured on this
    feed, the per-route rule overstates 856 subway and 118 commuter-rail
    waits, by up to 14 minutes at Jamaica, and a transitive "shares any next
    stop" grouping understates Jamaica's wait as 0.8 minutes, which is
    absurd.

    Keying the leg on its next stop resolves both: the wait charged is the
    combined headway of **every service that makes the same next stop**, which
    is the Chriqui-Robillard common-lines result applied one hop at a time.
    At Fordham that is the four inbound trains an hour; at Union Square the L
    stays its own group because its next stop is nobody else's. Staying on
    the same train is then free, because continuing to the following leg of
    the same route and direction is a zero-wait edge, and only *changing*
    vehicles passes through the arrival -> departure edge that carries the
    in-station time.

    The residual bias, stated rather than fixed: a rider who would accept
    either the local or the express still pays the wait for whichever group
    they board, because resolving that properly needs hyperpath routing rather
    than a shortest path.
    """
    if C.P_RIDE_TIME.exists() and not force:
        print(f"  cached  {C.P_RIDE_TIME.name}")
        z = np.load(C.P_RIDE_TIME, allow_pickle=False)
        st = pd.read_parquet(C.P_TRANSIT_STATIONS)
        return TransitGraph(st, z["ride"], str(z["date"]))

    zfs = {m: zipfile.ZipFile(p) for m, p in C.P_GTFS.items()}
    zfs[FERRY_MODE] = zipfile.ZipFile(C.P_GTFS_FERRY)
    windows = {m: feed_window(z) for m, z in zfs.items()}
    weekday, _sat = pick_dates(windows)
    print(f"  sampled {weekday} ({weekday:%A}), the same day stage 3 uses")

    parts = [_feed_legs(m, z, weekday) for m, z in zfs.items()]
    legs = pd.concat([p[0] for p in parts], ignore_index=True)
    allstops = pd.concat([p[1] for p in parts], ignore_index=True)

    # One row per station. Platforms collapse to their parent, so the position
    # is the mean over the platforms sharing a key -- a station is not one of
    # its own platform ends, and at C.INTERCHANGE_MAX_METERS that matters.
    stations = (allstops.groupby("station_key", as_index=False)
                .agg(name=("stop_name", "first"), mode=("mode", "first"),
                     lat=("lat", "mean"), lon=("lon", "mean")))
    served = set(legs["from_key"]) | set(legs["to_key"])
    stations = (stations.loc[stations["station_key"].isin(list(served))]
                .sort_values("station_key", kind="stable")
                .reset_index(drop=True))
    idx = {k: i for i, k in enumerate(stations["station_key"])}
    S = len(stations)
    counts = stations["mode"].value_counts().to_dict()
    print(f"  stations {S:,} with service today: "
          + ", ".join(f"{v} {k}" for k, v in counts.items()))

    legs = legs.loc[legs["from_key"].isin(idx) & legs["to_key"].isin(idx)] \
        .reset_index(drop=True)
    L = len(legs)

    # The common-lines grouping: combined boardings per (from, direction, to).
    grp = (legs.groupby(["from_key", "direction_id", "to_key"])["n_dep"]
           .transform("sum"))
    lo, hi = C.SERVICE_WINDOWS[C.TRANSIT_WINDOW]
    tph = grp.to_numpy() / (hi - lo)
    with np.errstate(divide="ignore"):
        half = np.minimum(60.0 / tph / 2.0, C.WAIT_CAP_MINUTES)
    wait = np.where(tph > 0, half, np.inf)
    n_group = legs.groupby(["from_key", "direction_id", "to_key"]).ngroups
    # A leg may be boarded only if *it* runs in the window, even though the
    # wait it is charged is its whole group's. Otherwise a 6am express whose
    # 8am local shares a next stop would inherit the local's wait and then be
    # ridden the length of its own pattern -- a trip that does not exist at
    # that hour. The group fixes the wait; it does not conjure the service.
    boardable = np.isfinite(wait) & (legs["n_dep"].to_numpy() > 0)
    runs = int((legs["n_dep"] > 0).sum())
    own = legs["n_dep"].to_numpy()[boardable] / (hi - lo)
    own_wait = np.minimum(60.0 / own / 2.0, C.WAIT_CAP_MINUTES)
    print(f"  legs     {L:,} (route, direction, from, to) nodes in {n_group:,} "
          f"common-line groups; {runs:,} run in the window (median wait "
          f"{np.median(wait[boardable]):.1f} min, against "
          f"{np.median(own_wait):.1f} per route alone)")

    xfer = _transfers(zfs, stations)

    # --- node layout -------------------------------------------------------
    # 0..S-1       arrival at a station
    # S..2S-1      departure from a station
    # 2S..2S+L-1   riding one leg
    N = 2 * S + L
    fr = legs["from_key"].map(idx).to_numpy()
    to = legs["to_key"].map(idx).to_numpy()
    mins = legs["secs"].to_numpy() / 60.0

    u: list = []
    v: list = []
    w: list = []

    def edge(a, b, cost) -> None:
        u.append(a)
        v.append(b)
        w.append(cost)

    # Boarding: pay the common-line wait for this leg's group.
    b = np.flatnonzero(boardable)
    edge(S + fr[b], 2 * S + b, wait[b])
    # Alighting: ride the leg and get off.
    edge(2 * S + np.arange(L), to, mins)
    # In-station change, declared where a feed declares it.
    self_t = np.full(S, C.TRANSFER_PENALTY_MINUTES)
    same = xfer["from_key"].to_numpy() == xfer["to_key"].to_numpy()
    for a, m in zip(xfer.loc[same, "from_key"], xfer.loc[same, "minutes"]):
        self_t[idx[a]] = m
    edge(np.arange(S), S + np.arange(S), self_t)
    # Between-station transfers, and the same walk taken *before* boarding
    # anything. Both are needed, and the second is easy to miss: the departure
    # node can otherwise only be left by boarding a train, so a rider standing
    # on the Times Square 1/2/3 platform who wants the Grand Central shuttle
    # was routed onto a train, around and back -- 15.9 minutes for a walk
    # across one complex and a 3-minute ride. Charging the same declared time
    # on a departure -> departure edge fixes it without making any transfer
    # free: a rider who has ridden still arrives at the arrival node and still
    # pays the in-station change to leave it.
    x = xfer.loc[~same]
    xf = x["from_key"].map(idx).to_numpy()
    xt = x["to_key"].map(idx).to_numpy()
    edge(xf, S + xt, x["minutes"].to_numpy())
    edge(S + xf, S + xt, x["minutes"].to_numpy())

    # Staying on the train: leg -> the following leg of the same route and
    # direction. Free of any wait, which is the whole point of separating it
    # from boarding.
    onward = legs.reset_index().groupby(
        ["route_id", "direction_id", "from_key"])["index"].apply(list)
    cu, cv, cw = [], [], []
    cols4 = zip(legs["route_id"], legs["direction_id"], legs["to_key"], mins)
    for e, (r, d, nk, m) in enumerate(cols4):
        for f in onward.get((r, d, nk), ()):
            cu.append(2 * S + e)
            cv.append(2 * S + f)
            cw.append(m)
    edge(np.asarray(cu, dtype=np.int64), np.asarray(cv, dtype=np.int64),
         np.asarray(cw, dtype=np.float64))

    g = csr_matrix((np.concatenate(w), (np.concatenate(u), np.concatenate(v))),
                   shape=(N, N))
    print(f"  edges    {int(boardable.sum()):,} board, {L:,} alight, "
          f"{len(cu):,} stay-on, {len(xfer):,} transfer + {len(x):,} walked "
          f"before boarding -> {g.nnz:,} directed over {N:,} nodes")

    t0 = time.time()
    dist = dijkstra(g, directed=True, indices=np.arange(S, 2 * S))
    ride = np.ascontiguousarray(dist[:, :S], dtype=np.float32)
    np.fill_diagonal(ride, 0.0)
    secs = time.time() - t0

    reach = np.isfinite(ride)
    print(f"  dijkstra {secs * 1000:.0f} ms for all {S:,} sources")
    print(f"  reached  {100 * reach.mean():.1f}% of station pairs; median "
          f"{np.median(ride[reach]):.1f} min, p90 "
          f"{np.percentile(ride[reach], 90):.1f} min, max "
          f"{ride[reach].max():.1f} min")

    stations.to_parquet(C.P_TRANSIT_STATIONS, index=False)
    np.savez_compressed(C.P_RIDE_TIME, ride=ride, date=str(weekday))
    print(f"  saved   {C.P_RIDE_TIME.name}, {C.P_TRANSIT_STATIONS.name}")
    return TransitGraph(stations, ride, str(weekday))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rail transit graph and its "
                                             "station-to-station times.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    print("Rail transit graph:")
    compute(args.force)
    print("\nStage 4b complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
