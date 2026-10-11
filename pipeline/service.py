"""Stage 3: scheduled service level at every station, from the GTFS feeds.

A station is not a binary. A Staten Island Railway stop with a 30-minute
headway and Times Square both counted as "a station" in the walk-only model,
which is the single largest thing that model got wrong. This stage attaches
trains per hour to each station so the next one can charge for the wait.

No routing engine and no graph: this is a key join and a group-by. The subway
station table carries `gtfs_stop_id` and the commuter-rail table's `code` is
the GTFS `stop_code`, so nothing here matches on names.

Three things in here are easy to get wrong and are the reason it is a stage of
its own rather than a helper:

1. **`stop_times` references platforms, not stations.** The subway feed has 496
   parent stations (`location_type=1`) and 992 directional children (`101N`,
   `101S`); every row in `stop_times` points at a child. Aggregating without
   resolving `parent_station` first gives you per-platform frequency, which is
   half the trains at every station in the city.
2. **Service-day hours run past 24.** A train stopping at 3am under the
   previous day's service is recorded as hour 27, and the subway feed goes to
   28. See `C.SERVICE_WINDOWS` for why `hour % 24` is the right normalization
   and not an approximation.
3. **A scheduled stop is not a boardable stop.** The commuter-rail feeds mark
   drop-off-only stops with `pickup_type=1` -- a Grand Central-bound train that
   opens its doors at Harlem-125 St but will not let you on. Counting those
   overstates inbound service at exactly the stations where it matters.

Run:  python -m pipeline.service [--force]
"""

from __future__ import annotations

import argparse
import datetime as dt
import zipfile

import numpy as np
import pandas as pd

import config as C
from pipeline.stations import load_stations

DAY_COLS = ("monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday")


def _read(zf: zipfile.ZipFile, name: str, **kw) -> pd.DataFrame:
    """Read one GTFS table as strings; missing optional tables come back empty."""
    if name not in zf.namelist():
        return pd.DataFrame()
    with zf.open(name) as fh:
        return pd.read_csv(fh, dtype=str, **kw)


def feed_window(zf: zipfile.ZipFile) -> tuple[str, str]:
    """The date range a feed actually covers.

    `calendar.txt` is optional in GTFS and both commuter-rail feeds omit it,
    listing every service date in `calendar_dates.txt` instead. Taking the
    range from whichever table exists is what lets one code path serve both.

    The two tables **union** here rather than intersect. An exception date sits
    inside the weekly pattern's range almost always, and narrowing to the
    overlap read the subway feed -- which covers May through October and patches
    six dates in July and September -- as a two-month feed, which then failed to
    overlap the commuter-rail feeds at all.
    """
    lo, hi = [], []
    cal = _read(zf, "calendar.txt")
    if not cal.empty:
        lo.append(cal["start_date"].min())
        hi.append(cal["end_date"].max())
    cd = _read(zf, "calendar_dates.txt")
    if not cd.empty:
        add = cd.loc[cd["exception_type"] == "1", "date"]
        if not add.empty:
            lo.append(add.min())
            hi.append(add.max())
    if not lo:
        raise SystemExit("a GTFS feed has neither calendar.txt nor calendar_dates.txt")
    return min(lo), max(hi)


def active_services(zf: zipfile.ZipFile, date: dt.date) -> set[str]:
    """Service IDs running on `date`, by the standard GTFS resolution order.

    `calendar.txt` gives the weekly pattern inside a date range; then
    `calendar_dates.txt` overrides it per date, adding service (type 1) or
    removing it (type 2). Exceptions are applied after the pattern because
    that is the order the spec defines, and the subway feed relies on it: it
    removes Weekday service on Labor Day and adds Sunday service instead.
    """
    stamp = date.strftime("%Y%m%d")
    ids: set[str] = set()

    cal = _read(zf, "calendar.txt")
    if not cal.empty:
        col = DAY_COLS[date.weekday()]
        on = (cal[col] == "1") & (cal["start_date"] <= stamp) & (cal["end_date"] >= stamp)
        ids |= set(cal.loc[on, "service_id"])

    cd = _read(zf, "calendar_dates.txt")
    if not cd.empty:
        today = cd[cd["date"] == stamp]
        ids |= set(today.loc[today["exception_type"] == "1", "service_id"])
        ids -= set(today.loc[today["exception_type"] == "2", "service_id"])
    return ids


def _stop_to_station(zf: zipfile.ZipFile, mode: str,
                     keys: pd.Series) -> dict[str, str]:
    """Map every GTFS `stop_id` to one of our `station_key`s.

    Two different joins, because the two kinds of feed are built differently.
    The subway feed is hierarchical, so a platform resolves through
    `parent_station` -- explicitly, rather than by stripping the N/S suffix,
    which is a naming convention and not a guarantee. The commuter-rail feeds
    are flat and carry the public `stop_code` that the station table calls
    `code`.
    """
    stops: pd.DataFrame = _read(zf, "stops.txt")
    stop_id: list[str] = stops["stop_id"].tolist()
    if mode == "subway":
        parent: list[str] = stops["parent_station"].fillna("").tolist()
        wanted = set(keys.str.replace("sub-", "", regex=False).tolist())
        resolved = [p or s for s, p in zip(stop_id, parent)]
        return {s: f"sub-{p}" for s, p in zip(stop_id, resolved) if p in wanted}
    code: list[str] = stops["stop_code"].fillna("").str.strip().tolist()
    by_code = dict(zip(keys.str.split("-").str[-1].tolist(), keys.tolist()))
    return {s: by_code[c] for s, c in zip(stop_id, code) if c in by_code}


def departures(zf: zipfile.ZipFile, mode: str, keys: pd.Series,
               services: set[str]) -> pd.DataFrame:
    """One row per boardable departure: station_key, direction, service hour.

    Returned as hours rather than counts so one pass over `stop_times` serves
    every window, including windows added later.
    """
    empty = pd.DataFrame({"station_key": [], "direction_id": [], "hour": [],
                          "minute": []})
    trips: pd.DataFrame = _read(zf, "trips.txt")
    running: pd.DataFrame = trips.loc[trips["service_id"].isin(list(services))]
    if running.empty:
        return empty

    cols = ["trip_id", "stop_id", "departure_time"]
    probe: pd.DataFrame = _read(zf, "stop_times.txt", nrows=1)
    if "pickup_type" in probe.columns:
        cols.append("pickup_type")
    stt: pd.DataFrame = _read(zf, "stop_times.txt", usecols=cols)

    keep = stt["trip_id"].isin(list(running["trip_id"])) & stt["departure_time"].notna()
    # pickup_type 1 is "no pickup available": the train stops but will not
    # board you, so it is not service from this station's point of view.
    if "pickup_type" in stt.columns:
        keep &= stt["pickup_type"].fillna("0") != "1"
    boardable: pd.DataFrame = stt.loc[keep]

    lookup = _stop_to_station(zf, mode, keys)
    station = boardable["stop_id"].map(lambda s: lookup.get(s))
    at_station: pd.DataFrame = boardable.loc[station.notna()]
    if at_station.empty:
        return empty

    times = at_station["departure_time"]
    dir_of = dict(zip(running["trip_id"].tolist(),
                      running["direction_id"].tolist()
                      if "direction_id" in running.columns
                      else ["0"] * len(running)))
    direction = at_station["trip_id"].map(lambda i: dir_of.get(i, "0"))

    return pd.DataFrame({
        # Service-day hour, then wrapped: see C.SERVICE_WINDOWS.
        "station_key": station.loc[station.notna()].tolist(),
        "direction_id": direction.tolist(),
        "hour": (times.str.slice(0, 2).astype(int) % 24).tolist(),
        "minute": (times.str.slice(0, 2).astype(int) * 60
                   + times.str.slice(3, 5).astype(int)).tolist(),
    })


def _longest_gap(hours: pd.Series) -> int:
    """Longest run of consecutive clock hours with no boardable departure.

    This replaced last-train-minus-first-train, which measured nothing. Every
    mode in the city runs a train shortly after midnight and another shortly
    before, so a span read 23.6 h for the LIRR, 24.1 h for Metro-North and
    25.4 h for the subway -- ranking Metro-North, which shuts overnight,
    above the LIRR, which does not. The gap separates them immediately:
    Metro-North's is around five hours and the subway's is zero.

    Measured on the 24-hour clock and wrapped, so a station whose last train
    is at 1am and whose first is at 5am has a 3-hour gap rather than a
    20-hour one.
    """
    present = sorted(set(int(h) for h in hours))
    if len(present) >= 24:
        return 0
    best = 0
    for i, h in enumerate(present):
        nxt = present[(i + 1) % len(present)]
        gap = (nxt - h - 1) % 24
        best = max(best, gap)
    return best


def _window_tph(dep: pd.DataFrame, lo: int, hi: int) -> pd.DataFrame:
    """Trains per hour **per direction** in [lo, hi), per station.

    Per direction is the number a rider experiences. A station with 20 trains
    an hour, 10 each way, offers a 6-minute headway to someone who wants to go
    somewhere -- not 3. Dividing the total by the number of directions that
    actually have service handles the terminals correctly: at a one-way
    terminal the divisor is 1, so its single direction is not halved.
    """
    w = dep[(dep["hour"] >= lo) & (dep["hour"] < hi)]
    if w.empty:
        return pd.DataFrame({"tph": [], "n_dirs": []},
                            index=pd.Index([], name="station_key"))
    g = w.groupby("station_key")
    total = g.size() / (hi - lo)
    n_dirs = g["direction_id"].nunique()
    return pd.DataFrame({"tph": total / n_dirs, "n_dirs": n_dirs})


def wait_minutes(tph: pd.Series) -> pd.Series:
    """Expected platform wait, in minutes, from trains per hour per direction.

    `headway / 2` is the turn-up-and-go approximation, capped per
    `C.WAIT_CAP_MINUTES`. No service in the window gives NaN rather than
    infinity, because the caller's decision there is to drop the station, not
    to charge it a very large number.
    """
    with np.errstate(divide="ignore"):
        headway = np.where(tph > 0, 60.0 / tph.to_numpy(), np.nan)
    return pd.Series(np.minimum(headway / 2.0, C.WAIT_CAP_MINUTES),
                     index=tph.index)


def pick_dates(windows: dict[str, tuple[str, str]]) -> tuple[dt.date, dt.date]:
    """A representative weekday and Saturday inside every feed's date range.

    The three feeds do not cover the same dates -- the subway feed is a
    long-term supplement ending in October while Metro-North's runs into the
    next May -- so the date has to be in the intersection or one feed
    contributes no trips at all and its stations silently read as closed.

    Wednesday, because Monday and Friday carry holiday service often enough to
    matter and the middle of the week is the least exceptional day in any
    schedule.
    """
    lo = max(dt.datetime.strptime(a, "%Y%m%d").date() for a, _ in windows.values())
    hi = min(dt.datetime.strptime(b, "%Y%m%d").date() for _, b in windows.values())
    if lo > hi:
        raise SystemExit(
            f"The GTFS feeds do not overlap: latest start {lo}, earliest end {hi}. "
            f"One feed is stale -- re-run `python -m pipeline.fetch --force`.")

    # Start a few days in: the first day of a feed is often a partial
    # transition day, and leave the range's last day alone for the same reason.
    start = min(lo + dt.timedelta(days=3), hi)
    picks = {}
    for want, label in ((2, "weekday"), (5, "saturday")):
        d = start
        while d <= hi and d.weekday() != want:
            d += dt.timedelta(days=1)
        if d > hi:
            raise SystemExit(
                f"No {label} falls inside the feeds' common range {lo}..{hi}.")
        picks[label] = d
    return picks["weekday"], picks["saturday"]


def compute(force: bool = False) -> pd.DataFrame:
    """Per-station scheduled service, one row per station_key."""
    if C.P_SERVICE.exists() and not force:
        print(f"  cached  {C.P_SERVICE.name}")
        return pd.read_parquet(C.P_SERVICE)

    st = load_stations(verbose=False)
    zfs = {m: zipfile.ZipFile(p) for m, p in C.P_GTFS.items()}
    windows = {m: feed_window(z) for m, z in zfs.items()}
    for m, (a, b) in windows.items():
        print(f"  feed    {m:7} covers {a} .. {b}")

    weekday, saturday = pick_dates(windows)
    print(f"  sampled {weekday} ({weekday:%A}) and {saturday} ({saturday:%A})")

    # A feed that patches the sampled date is a feed whose "typical" day is not
    # typical. Worth saying out loud rather than discovering in the numbers.
    for m, z in zfs.items():
        cd = _read(z, "calendar_dates.txt")
        if cd.empty:
            continue
        for d in (weekday, saturday):
            n = (cd["date"] == d.strftime("%Y%m%d")).sum()
            base = not _read(z, "calendar.txt").empty
            if n and base:
                print(f"  NOTE    {m} has {n} calendar_dates exception(s) on {d}")

    # Modes in the station table map onto feeds; SIR rides in the subway feed.
    feed_of = {"subway": "subway", "sir": "subway", "lirr": "lirr", "mnr": "mnr"}
    parts = []
    for mode, zf in zfs.items():
        keys = st.loc[st["mode"].map(lambda m: feed_of.get(m)) == mode,
                      "station_key"]
        day = {}
        for label, date in (("weekday", weekday), ("saturday", saturday)):
            svc = active_services(zf, date)
            dep = departures(zf, mode, keys, svc)
            day[label] = dep
            print(f"  {mode:7} {label:8} {len(svc):3} service(s), "
                  f"{len(dep):7,} boardable departures at {dep['station_key'].nunique()} stations")

        cols = {}
        for name, (lo, hi) in C.SERVICE_WINDOWS.items():
            w = _window_tph(day["weekday"], lo, hi)
            cols[f"tph_{name}"] = w["tph"]
            if name == C.WAIT_WINDOW:
                cols["n_dirs"] = w["n_dirs"]
        slo, shi = C.SERVICE_SATURDAY_WINDOW
        cols["tph_saturday"] = _window_tph(day["saturday"], slo, shi)["tph"]

        # Span of service on the sampled weekday, in service-day minutes, so a
        # last train at 1:30am reads as 1530 rather than wrapping to 90 and
        # making the span look negative.
        wd = day["weekday"]
        if not wd.empty:
            g = wd.groupby("station_key")["minute"]
            cols["first_train_min"] = g.min()
            cols["last_train_min"] = g.max()
            cols["hours_served"] = wd.groupby("station_key")["hour"].nunique()
            cols["night_gap_hours"] = wd.groupby("station_key")["hour"].apply(_longest_gap)

        frame = pd.DataFrame(cols).reindex(keys.to_numpy())
        frame.index.name = "station_key"
        parts.append(frame)

    out = pd.concat(parts)
    for c in out.columns:
        if c.startswith("tph_"):
            out[c] = out[c].fillna(0.0)

    out["headway_min"] = np.where(out[f"tph_{C.WAIT_WINDOW}"] > 0,
                                  60.0 / out[f"tph_{C.WAIT_WINDOW}"], np.nan)
    out["wait_min"] = wait_minutes(out.loc[:, f"tph_{C.WAIT_WINDOW}"])

    meta = st.set_index("station_key").loc[:, ["name", "mode"]]
    out = out.join(meta)
    _report(out)
    out.to_parquet(C.P_SERVICE)
    print(f"  saved   {C.P_SERVICE.name} ({len(out)} stations)")
    return out


def _report(out: pd.DataFrame) -> None:
    key = f"tph_{C.WAIT_WINDOW}"
    print(f"\n  AM peak trains/hour/direction, by mode:")
    for mode, g in out.groupby("mode"):
        served = g[g[key] > 0]
        print(f"    {mode:7} {len(g):4} stations, {len(served):4} with "
              f"{C.WAIT_WINDOW} service; "
              f"median {served.loc[:, key].median():5.1f} tph, "
              f"median wait {served.loc[:, 'wait_min'].median():4.1f} min")

    dead = out[out[key] <= 0]
    if len(dead):
        print(f"  {len(dead)} station(s) with no {C.WAIT_WINDOW} service "
              f"(dropped from the weighted source set):")
        for nm, m in zip(dead.loc[:, "name"].head(10),
                         dead.loc[:, "mode"].head(10)):
            print(f"            {m:6} {nm}")

    night = out[out["tph_night"] > 0]
    print(f"  overnight (3-4am) service, and the longest no-service gap:")
    for mode, g in out.groupby("mode"):
        print(f"    {mode:7} {(g['tph_night'] > 0).sum():4}/{len(g):<4} "
              f"({100 * (g['tph_night'] > 0).mean():5.1f}%) have 3am trains; "
              f"median gap {g['night_gap_hours'].median():4.1f} h, "
              f"{g['hours_served'].median():4.1f} of 24 hours served")
    print(f"  total {len(night)}/{len(out)} stations with 3am service")

    worst = out.loc[out[key] > 0].nlargest(8, columns="headway_min")
    print("  longest AM peak headways:")
    for _, r in worst.iterrows():
        print(f"    {r['headway_min']:6.1f} min  {r['mode']:6} {r['name'][:38]:38} "
              f"wait {r['wait_min']:4.1f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scheduled service level per station.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    print("Station service levels:")
    compute(args.force)
    print("\nStage 3 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
