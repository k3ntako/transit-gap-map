"""Load and normalize the station tables into one schema."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

import config as C

BOROUGH_CODES = {"Bk": "Brooklyn", "Bx": "Bronx", "M": "Manhattan",
                 "Q": "Queens", "SI": "Staten Island"}

MODE_LABEL = {"subway": "Subway", "sir": "Staten Island Railway",
              "lirr": "LIRR", "mnr": "Metro-North"}


def _subway() -> pd.DataFrame:
    df = pd.read_csv(C.P_SUBWAY)
    out = pd.DataFrame({
        "station_key": "sub-" + df["gtfs_stop_id"].astype(str),
        "complex_id": df["complex_id"].astype(str),
        "name": df["stop_name"].astype(str).str.strip(),
        "mode": df["division"].map(lambda d: "sir" if d == "SIR" else "subway"),
        "routes": df["daytime_routes"].fillna("").astype(str).str.strip(),
        "borough": df["borough"].map(BOROUGH_CODES).fillna(df["borough"]),
        "ada": df["ada"].fillna(0).astype(str).str.strip().isin(["1", "2", "1.0", "2.0"]),
        "lat": pd.to_numeric(df["gtfs_latitude"], errors="coerce"),
        "lon": pd.to_numeric(df["gtfs_longitude"], errors="coerce"),
    })
    return out


def _rail() -> pd.DataFrame:
    df = pd.read_csv(C.P_RAIL)
    out = pd.DataFrame({
        "station_key": "rail-" + df["railroad"].astype(str) + "-" + df["code"].astype(str),
        "complex_id": "",
        "name": df["station_name"].astype(str).str.strip(),
        "mode": df["railroad"].astype(str).str.lower(),
        "routes": df["branch"].fillna("").astype(str).str.strip(),
        "borough": "",
        "ada": df["accessibility"].fillna("").astype(str).str.upper().eq("FULL"),
        "lat": pd.to_numeric(df["latitude"], errors="coerce"),
        "lon": pd.to_numeric(df["longitude"], errors="coerce"),
    })
    return out


def load_stations(verbose: bool = True) -> gpd.GeoDataFrame:
    """All subway + SIR stations, plus commuter-rail stations near enough to NYC.

    Commuter rail is filtered geographically: the published table covers the
    whole LIRR/Metro-North system out to Suffolk and Dutchess counties, and
    only the stations in or just beyond the city are relevant here.
    """
    sub, rail = _subway(), _rail()
    both = pd.concat([sub, rail], ignore_index=True)
    before = len(both)
    both = both.dropna(subset=["lat", "lon"])
    if verbose and len(both) < before:
        print(f"  dropped {before - len(both)} stations with no coordinates")

    gdf = gpd.GeoDataFrame(
        both,
        geometry=[Point(xy) for xy in zip(both["lon"], both["lat"])],
        crs=C.CRS_GEO,
    ).to_crs(C.CRS_PROJ)

    boros = gpd.read_file(C.P_BOROS).to_crs(C.CRS_PROJ)
    area = boros.geometry.union_all().buffer(C.STATION_BUFFER_M)

    in_area = gdf.geometry.within(area)
    is_rail = gdf["mode"].isin(["lirr", "mnr"])
    # Subway/SIR are all inside NYC by definition; never filter them out.
    keep = (~is_rail) | in_area
    dropped = gdf.loc[is_rail & ~in_area]

    gdf = gdf.loc[keep].reset_index(drop=True)
    if verbose:
        print(f"  stations: {len(gdf)} kept "
              f"({(~is_rail).sum()} subway/SIR + {(is_rail & in_area).sum()} commuter rail; "
              f"{len(dropped)} out-of-area rail stations dropped)")
        for m, n in gdf["mode"].value_counts().items():
            print(f"            {MODE_LABEL.get(m, m):24} {n:4}")
    return gdf
