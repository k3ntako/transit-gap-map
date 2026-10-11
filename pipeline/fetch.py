"""Stage 1: download every input to data/raw/, with an on-disk cache.

Run directly:  python -m pipeline.fetch [--force]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

import config as C

# A descriptive User-Agent is sent on every request; several of these providers
# ask for one, and it lets them identify (and contact) a misbehaving client
# rather than just blocking an anonymous IP.
UA = {"User-Agent": "transit-gap-map/1.0 (transit access analysis; +https://github.com/k3ntako/transit-gap-map)"}


class FetchError(RuntimeError):
    pass


def _brief(e: Exception) -> str:
    """One readable line from a requests exception.

    urllib3 nests the real cause several layers deep and stringifies into a
    paragraph; the useful part is the exception type and the final message.
    """
    kind = type(e).__name__
    msg = str(e)
    for marker in ("Caused by ", "Max retries exceeded with url: "):
        if marker in msg:
            msg = msg.split(marker, 1)[1]
    msg = msg.strip().strip("()").split("\n")[0]
    return f"{kind}: {msg[:110]}" if msg else kind


def _load_meta() -> dict:
    try:
        return json.loads(C.CACHE_META.read_text())
    except Exception:
        return {}


def _save_meta(meta: dict) -> None:
    try:
        C.CACHE_META.write_text(json.dumps(meta, indent=2, sort_keys=True))
    except OSError:
        pass          # a cache that cannot be written is not worth failing over


def _ttl(kind: str) -> float:
    return C.CACHE_TTL_DAYS.get(kind, 7) * 86400


def _is_fresh(path: Path, force: bool, ttl: float) -> bool:
    if force or not path.exists() or path.stat().st_size == 0:
        return False
    return (time.time() - path.stat().st_mtime) < ttl


def _request(url: str, headers: dict, stream: bool = True):
    """GET with retries on 429/5xx, honoring Retry-After.

    Backing off rather than hammering is the difference between a transient
    error and getting an IP blocked.
    """
    last = None
    for attempt in range(C.HTTP_RETRIES):
        r = requests.get(url, headers=headers, stream=stream, timeout=(30, 300))
        if r.status_code < 429 and r.status_code not in (500, 502, 503, 504):
            return r
        last = r
        if attempt == C.HTTP_RETRIES - 1:
            break
        wait = float(r.headers.get("retry-after") or 0) or \
            C.HTTP_BACKOFF_BASE * (2 ** attempt)
        print(f"          HTTP {r.status_code}; retrying in {wait:.0f}s "
              f"({attempt + 2}/{C.HTTP_RETRIES})")
        r.close()
        time.sleep(wait)
    return last


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}GB"


def download(url: str, dest: Path, force: bool = False, kind: str = "stations",
             stream_threshold: int = 5_000_000) -> Path:
    """Download `url` to `dest`, doing as little network work as possible.

    Three layers, cheapest first:
      1. Inside the freshness window, no request is made at all.
      2. Past it, a conditional request (If-None-Match / If-Modified-Since) lets
         the server answer 304 Not Modified -- a few hundred bytes instead of
         re-transferring the file. This matters most for the 153 MB OSM extract,
         which would otherwise be pulled again in full just because a week
         elapsed, even though nothing we care about changed.
      3. If the request fails and a cached copy exists, that copy is used and
         the failure is reported as a warning. Being unable to reach a provider
         should not throw away a perfectly usable file.

    Writes to a .part file and renames on success, so an interrupted transfer
    never leaves a truncated file that a later run would trust as cached.
    """
    ttl = _ttl(kind)
    if _is_fresh(dest, force, ttl):
        age_h = (time.time() - dest.stat().st_mtime) / 3600
        print(f"  cached  {dest.name} ({_human(dest.stat().st_size)}, "
              f"{age_h:.0f}h old, no request made)")
        return dest

    meta = _load_meta()
    entry = meta.get(url, {})
    headers = dict(UA)
    if dest.exists() and not force:
        # Send ONE validator, preferring Last-Modified. RFC 9110 says a server
        # receiving If-None-Match must ignore If-Modified-Since, and Socrata's
        # ETag varies with the response's content-encoding -- so sending both
        # means the ETag never matches, the date is ignored, and we get a full
        # 200 instead of the 304 the date alone would have produced. Measured:
        # If-Modified-Since alone -> 304; either case involving the ETag -> 200.
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]
        elif entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]

    try:
        r = _request(url, headers)
        if r is None:
            raise requests.RequestException("no response")

        if r.status_code == 304:
            r.close()
            dest.touch()          # restart the freshness window
            print(f"  304     {dest.name} unchanged on the server "
                  f"({_human(dest.stat().st_size)} kept)")
            entry["checked_at"] = int(time.time())
            meta[url] = entry
            _save_meta(meta)
            return dest

        r.raise_for_status()
        print(f"  GET     {dest.name} <- {url[:70]}")
        tmp = dest.with_suffix(dest.suffix + ".part")
        total = int(r.headers.get("content-length") or 0)
        done = 0
        last = 0.0
        with r, open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
                done += len(chunk)
                if total > stream_threshold and time.time() - last > 0.5:
                    print(f"\r          {100 * done / total:5.1f}%  "
                          f"{_human(done)} / {_human(total)}", end="", flush=True)
                    last = time.time()
        if total > stream_threshold:
            print(f"\r          100.0%  {_human(done)} / {_human(total)}        ")

        if done == 0:
            tmp.unlink(missing_ok=True)
            raise FetchError(f"{url} returned an empty body")
        tmp.replace(dest)

        meta[url] = {"etag": r.headers.get("etag"),
                     "last_modified": r.headers.get("last-modified"),
                     "checked_at": int(time.time()),
                     "bytes": dest.stat().st_size}
        _save_meta(meta)
        print(f"  saved   {dest.name} ({_human(dest.stat().st_size)})")
        return dest

    except (requests.RequestException, FetchError) as e:
        if dest.exists() and dest.stat().st_size > 0 and not force:
            print(f"  WARNING could not refresh {dest.name} ({_brief(e)}); using "
                  f"the cached copy ({_human(dest.stat().st_size)}, "
                  f"{(time.time() - dest.stat().st_mtime) / 86400:.0f}d old)")
            return dest
        raise FetchError(f"{url}: {_brief(e)}") from e


# --- Census -----------------------------------------------------------------

def _census_key() -> str:
    load_dotenv(C.ROOT / ".env")
    key = (os.environ.get("CENSUS_API_KEY") or "").strip()
    if not key:
        raise FetchError(
            "No CENSUS_API_KEY found.\n\n"
            "  The Census API now requires a key for every request.\n"
            f"  Get one free (instant): {C.CENSUS_KEY_SIGNUP}\n"
            f"  Then:  cp .env.example .env   and paste the key into it.\n"
        )
    return key


def fetch_census(force: bool = False) -> Path:
    """Population per census geography for the five boroughs.

    One request per county. Guards against the Census "Missing Key" page,
    which is served as HTML with HTTP 200 -- so status alone cannot detect it.
    """
    dest = C.P_CENSUS
    if _is_fresh(dest, force, _ttl("census")):
        age_d = (time.time() - dest.stat().st_mtime) / 86400
        print(f"  cached  {dest.name} ({age_d:.0f}d old, no request made)")
        return dest

    key = _census_key()
    geo = "tract" if C.GEO_LEVEL == "tract" else "block group"
    extra = list(C.CENSUS_EXTRA_VARS.items())
    cols = ([C.CENSUS_POP_VAR] + ([C.CENSUS_MOE_VAR] if C.CENSUS_MOE_VAR else [])
            + [v for _k, v in extra])
    rows: list[dict] = []

    for county, name in C.COUNTY_FIPS.items():
        url = f"https://api.census.gov/data/{C.CENSUS_DATASET}"
        params = {
            "get": ",".join(["NAME"] + cols),
            "for": f"{geo}:*",
            "in": f"state:{C.STATE_FIPS} county:{county}",
            "key": key,
        }
        try:
            r = requests.get(url, params=params, headers=UA, timeout=(30, 120))
        except requests.RequestException as e:
            if dest.exists() and not force:
                print(f"  WARNING Census unreachable ({e}); using cached {dest.name}")
                return dest
            raise FetchError(f"Census request failed: {e}") from e
        body = r.text.lstrip()

        if body.startswith("<"):
            hint = ""
            if "Missing Key" in body or "Invalid Key" in body:
                hint = (f"\n  The API rejected the key. Check CENSUS_API_KEY in .env, "
                        f"or request a new one:\n  {C.CENSUS_KEY_SIGNUP}")
            raise FetchError(
                f"Census API returned HTML, not JSON, for {name} "
                f"(HTTP {r.status_code}).{hint}"
            )
        r.raise_for_status()
        data = r.json()
        header, body_rows = data[0], data[1:]
        idx = {k: i for i, k in enumerate(header)}

        for row in body_rows:
            geoid = (row[idx["state"]] + row[idx["county"]] + row[idx["tract"]]
                     + (row[idx["block group"]] if C.GEO_LEVEL == "bg" else ""))
            def num(var):
                v = row[idx[var]]
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    return ""
                # Census uses large negatives as null/annotation sentinels.
                return "" if f <= -666666 else int(f)
            rec = {"geoid": geoid, "borough": name, "pop": num(C.CENSUS_POP_VAR)}
            if C.CENSUS_MOE_VAR:
                rec["pop_moe"] = num(C.CENSUS_MOE_VAR)
            for k, v in extra:
                rec[k] = num(v)
            rows.append(rec)
        print(f"  census  {name:14} {len(body_rows):5} {geo}s")

    if not rows:
        raise FetchError("Census returned no rows")

    with open(dest, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    total = sum(r["pop"] for r in rows if r["pop"] != "")
    print(f"  saved   {dest.name}  ({len(rows)} rows, total pop {total:,})")
    if not 8_000_000 <= total <= 9_500_000:
        print(f"  WARNING NYC population {total:,} is outside the expected 8.0-9.5M range")
    return dest


# --- Jobs (LEHD LODES) ------------------------------------------------------

def jobs_by_geography(force: bool = False) -> Path:
    """Workplace jobs per census geography, from the LODES WAC file.

    LODES keys every row on the 15-digit census *block* where the job is, so
    the tract GEOID is a string slice and no crosswalk file is involved. Only
    the five county FIPS codes are kept, which drops the rest of New York
    State -- 94% of the file -- before anything is summed.

    This is the destination side of the accessibility metric: not a hub we
    picked, but every geography in the city weighted by the jobs actually in
    it. The consequence to state is that jobs *outside* the five boroughs are
    not counted, so a Nassau-bound commuter from eastern Queens contributes
    nothing. The published figure is therefore "jobs in New York City
    reachable in 45 minutes", which is the question the map is about.
    """
    if _is_fresh(C.P_JOBS, force, _ttl("jobs")) and C.P_JOBS.exists():
        print(f"  cached  {C.P_JOBS.name} (no request made)")
        return C.P_JOBS

    src = download(C.LODES_WAC_URL, C.P_LODES, force, kind="jobs")

    import pandas as pd
    wanted = {C.STATE_FIPS + c for c in C.COUNTY_FIPS}
    rows = pd.read_csv(src, compression="gzip", usecols=["w_geocode", "C000"],
                       dtype={"w_geocode": str})
    n_state = len(rows)
    rows = rows.loc[rows["w_geocode"].str.slice(0, 5).isin(wanted)]
    n_blocks = len(rows)

    glen = 11 if C.GEO_LEVEL == "tract" else 12
    rows = rows.assign(geoid=rows["w_geocode"].str.slice(0, glen))
    jobs = (rows.groupby("geoid", as_index=False)["C000"].sum()
            .rename(columns={"C000": "jobs"}))
    jobs["jobs"] = jobs["jobs"].astype("int64")
    jobs.to_parquet(C.P_JOBS, index=False)

    total = int(jobs["jobs"].sum())
    print(f"  jobs    {n_blocks:,} NYC blocks of {n_state:,} statewide -> "
          f"{len(jobs):,} {C.GEO_LEVEL}s, {total:,} jobs")
    # NYC private + public employment runs around 4.7M in this period. A total
    # far outside that band means the segment, the year or the county filter
    # moved, which would quietly rescale every accessibility number downstream.
    if not 3_500_000 <= total <= 5_500_000:
        print(f"  WARNING {total:,} jobs is outside the expected 3.5-5.5M band "
              f"for NYC; check LODES_YEAR and the WAC segment")
    print(f"  saved   {C.P_JOBS.name}")
    return C.P_JOBS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch all raw inputs.")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    ap.add_argument("--skip-pbf", action="store_true", help="skip the 153 MB OSM extract")
    ap.add_argument("--skip-bus", action="store_true", help="skip the 62 MB bus feeds")
    args = ap.parse_args(argv)

    geoms_url = C.NYC_TRACTS_GEOJSON if C.GEO_LEVEL == "tract" else C.NYC_BG_GEOJSON

    try:
        print("Stations:")
        download(C.SOC_SUBWAY, C.P_SUBWAY, args.force, kind="stations")
        download(C.SOC_RAIL, C.P_RAIL, args.force, kind="stations")
        print("Boundaries:")
        download(geoms_url, C.P_GEOMS, args.force, kind="boundaries")
        download(C.NYC_BOROUGHS_GEOJSON, C.P_BOROS, args.force, kind="boundaries")
        print("Schedules:")
        for mode, url in C.GTFS_FEEDS.items():
            download(url, C.P_GTFS[mode], args.force, kind="gtfs")
        if args.skip_bus:
            print("Bus schedules:\n  skipped (--skip-bus)")
        else:
            print("Bus schedules (62 MB across six feeds):")
            for mode, url in C.GTFS_BUS_FEEDS.items():
                download(url, C.P_GTFS_BUS[mode], args.force, kind="gtfs")
        print("Population:")
        fetch_census(args.force)
        print("Jobs (LEHD LODES8 WAC, ~2.7 MB gzipped):")
        jobs_by_geography(args.force)
        if args.skip_pbf:
            print("Walk network:\n  skipped (--skip-pbf)")
        else:
            print("Walk network:")
            download(C.OSM_PBF_URL, C.P_PBF, args.force, kind="osm")
    except FetchError as e:
        sys.stdout.flush()
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1

    print("\nStage 1 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
