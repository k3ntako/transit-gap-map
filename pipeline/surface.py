"""Stage 6: continuous walk-time raster + network walkshed polygons.

The raster is built uniform in Web Mercator (EPSG:3857), because that is the
space a MapLibre `image` source interpolates between its four corners -- a grid
laid out in lon/lat or UTM would land slightly skewed.

Walksheds are traced from a finer grid of the same network distances, so they
follow the walk network and collapse at rivers, rail cuts and expressways
instead of ballooning into circles the way a simple buffer does.

Run:  python -m pipeline.surface [--force]
"""

from __future__ import annotations

import argparse
import json
import warnings

import geopandas as gpd
import numpy as np
import shapely
from PIL import Image
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely.geometry import box

import config as C
from pipeline.distance import compute as compute_dist
from pipeline.graphutil import load_graph
from pipeline.palette import hex_to_rgb

warnings.simplefilter("ignore")

MERCATOR = "EPSG:3857"


def _land():
    boros = gpd.read_file(C.P_BOROS)
    return boros.to_crs(MERCATOR).geometry.union_all()


def _grid(resolution_m: float, land_merc):
    """Cell-center coordinates of a Mercator-uniform grid over the land mask.

    EPSG:3857 units are meters only at the equator, so the requested ground
    resolution is divided by cos(latitude) to come out right at NYC's latitude.
    """
    minx, miny, maxx, maxy = land_merc.bounds
    to_geo = Transformer.from_crs(MERCATOR, C.CRS_GEO, always_xy=True)
    _, lat_mid = to_geo.transform((minx + maxx) / 2, (miny + maxy) / 2)
    step = resolution_m / np.cos(np.radians(lat_mid))

    xs = np.arange(minx, maxx + step, step)
    ys = np.arange(miny, maxy + step, step)
    return xs, ys, step


def _node_tree(serviceable):
    """KD-tree over the projected coords of nodes that can actually reach a station."""
    g = load_graph(C.P_GRAPH)
    x, y = g.x, g.y
    idx = np.flatnonzero(serviceable)
    return cKDTree(np.column_stack([x[idx], y[idx]])), idx


def distance_field(resolution_m: float, dist, serviceable, land_merc):
    """Walk minutes on a Mercator-uniform grid; NaN outside land or out of range."""
    xs, ys, step = _grid(resolution_m, land_merc)
    gx, gy = np.meshgrid(xs, ys)
    flat_x, flat_y = gx.ravel(), gy.ravel()

    # Only cells on land get a value; everything else stays transparent.
    inside = shapely.contains_xy(land_merc, flat_x, flat_y)

    to_proj = Transformer.from_crs(MERCATOR, C.CRS_PROJ, always_xy=True)
    px, py = to_proj.transform(flat_x[inside], flat_y[inside])

    tree, idx = _node_tree(serviceable)
    off, loc = tree.query(np.column_stack([px, py]), k=1)
    vals = dist[idx[loc]] + off
    vals[off > C.SURFACE_MAX_SNAP_M] = np.nan

    out = np.full(flat_x.shape, np.nan)
    out[inside] = vals
    grid = C.minutes(out.reshape(gy.shape))

    print(f"  grid    {grid.shape[1]}x{grid.shape[0]} at ~{resolution_m:.0f} m "
          f"({inside.sum():,} land cells, "
          f"{np.isfinite(grid).sum():,} with a value)")
    return grid, xs, ys, step


def ramp_lut(ramp: list[str], vmax: float, n: int = 256):
    """256-entry RGB lookup table interpolated through a hex ramp."""
    cols = np.array([hex_to_rgb(h) for h in ramp])
    pos = np.linspace(0, 1, len(cols))
    t = np.linspace(0, 1, n)
    lut = np.stack([np.interp(t, pos, cols[:, k]) for k in range(3)], axis=1)
    return (lut * 255).astype(np.uint8)


def render_png(grid, ramp, vmax, path):
    """Color-ramped indexed PNG, north-up, transparent where there is no value.

    Indexed rather than RGBA because the image already *is* a palette image:
    every pixel is one entry of `ramp_lut`, so there were never more than 256
    distinct colors to store. Writing it as truecolor cost 2.2x the bytes for
    no additional information (137 KB -> 62 KB at 100 m).

    Index 255 is reserved for "no value" and is what the tRNS chunk points at,
    so the ramp gets 255 entries instead of 256 -- a difference of under half
    of one ramp step, invisible against a 30-minute scale.

    Opacity is deliberately *not* baked in. It belongs to the layer, and was
    being applied in both places: 0.82 here times 0.85 in app.js, for an
    effective 0.697 that nobody chose. app.js now owns it outright, which is
    also what makes the alpha channel binary and the image indexable.
    """
    lut = ramp_lut(ramp, vmax, n=255)
    norm = np.clip(grid / vmax, 0, 1)

    idx = np.full(grid.shape, 255, dtype=np.uint8)
    has_value = np.isfinite(grid)
    idx[has_value] = (norm[has_value] * 254).astype(np.uint8)

    palette = np.zeros((256, 3), dtype=np.uint8)
    palette[:255] = lut

    # Grid rows run south -> north; PNG rows run top -> bottom.
    im = Image.fromarray(idx[::-1], mode="P")
    im.putpalette(palette.reshape(-1).tolist())
    im.save(path, optimize=True, transparency=255)
    kb = path.stat().st_size / 1024
    print(f"  saved   {path.name} ({kb:.0f} KB, {grid.shape[1]}x{grid.shape[0]})")
    return kb


def rect_union(mask, xs, ys, step):
    """Dissolve a boolean grid into a polygon via per-row run-length rectangles.

    Unioning one box per cell would mean hundreds of thousands of polygons;
    merging each row's consecutive cells first cuts that by one to two orders
    of magnitude and the result is identical.
    """
    boxes = []
    for r in range(mask.shape[0]):
        row = mask[r]
        if not row.any():
            continue
        d = np.diff(np.concatenate([[0], row.view(np.int8), [0]]))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        y0 = ys[r]
        for s, e in zip(starts, ends):
            boxes.append(box(xs[s], y0, xs[e - 1] + step, y0 + step))
    if not boxes:
        return None
    return shapely.union_all(boxes)


def walksheds(dist, serviceable, land_merc):
    """One polygon per walk-time threshold, traced from the network distances."""
    grid, xs, ys, step = distance_field(
        C.WALKSHED_RESOLUTION_M, dist, serviceable, land_merc)

    feats = []
    for t in C.WALK_THRESHOLDS_MIN:
        mask = np.isfinite(grid) & (grid <= t)
        geom = rect_union(mask, xs, ys, step)
        if geom is None:
            continue
        # Simplify in Mercator meters. 40 m sheds the grid's stair-steps and
        # keeps the payload small while preserving the barrier detail that is
        # the whole reason to draw network walksheds instead of buffers.
        geom = geom.simplify(40, preserve_topology=True)
        feats.append({"threshold_min": t, "geometry": geom})
        parts = len(geom.geoms) if hasattr(geom, "geoms") else 1
        print(f"  walkshed {t:>2} min: {parts} part(s)")

    gdf = gpd.GeoDataFrame(feats, geometry="geometry", crs=MERCATOR)
    # Area must be measured in a CRS whose units are true meters: EPSG:3857
    # units are meters only at the equator and overstate area here by ~1/cos^2
    # (about 1.74x at this latitude).
    gdf["area_km2"] = (gdf.to_crs(C.CRS_PROJ).geometry.area / 1e6).round(1)
    for _, r in gdf.iterrows():
        print(f"           {r['threshold_min']:>2} min walkshed: "
              f"{r['area_km2']:6.1f} km2 of land")

    gdf = gdf.to_crs(C.CRS_GEO)
    gdf["geometry"] = shapely.set_precision(gdf.geometry.values, 1e-5)
    # Draw the widest ring first so the tighter ones paint on top.
    return gdf.sort_values("threshold_min", ascending=False)


def run(force: bool = False):
    import pipeline.palette as P

    pal = json.loads((C.WEB_DATA / "palette.json").read_text()) \
        if (C.WEB_DATA / "palette.json").exists() else P.build()

    dist, _nearest, serviceable = compute_dist(force=False, subway_only=False)
    land = _land()

    vmax = float(C.WALK_THRESHOLDS_MIN[-1] * 2)   # 30 min caps the ramp
    print(f"Surface raster (ramp caps at {vmax:.0f} min):")
    grid, xs, ys, step = distance_field(
        C.SURFACE_RESOLUTION_M, dist, serviceable, land)

    meta = {}
    for mode in ("light", "dark"):
        ramp = pal["ramps"]["walk"] if mode == "light" else pal["ramps"]["walk"][::-1]
        path = C.WEB_DATA / f"walk_surface_{mode}.png"
        kb = render_png(grid, ramp, vmax, path)
        if kb > C.BUDGET_SURFACE_KB:
            print(f"  WARNING {path.name} is {kb:.0f} KB, over the "
                  f"{C.BUDGET_SURFACE_KB:.0f} KB budget")
        meta[mode] = path.name

    # Four corners in lon/lat: TL, TR, BR, BL (the order MapLibre expects).
    to_geo = Transformer.from_crs(MERCATOR, C.CRS_GEO, always_xy=True)
    x0, x1 = float(xs[0]), float(xs[-1] + step)
    y0, y1 = float(ys[0]), float(ys[-1] + step)
    corners = [to_geo.transform(x0, y1), to_geo.transform(x1, y1),
               to_geo.transform(x1, y0), to_geo.transform(x0, y0)]

    (C.WEB_DATA / "walk_surface.json").write_text(json.dumps({
        "images": meta,
        "coordinates": [[round(a, 6), round(b, 6)] for a, b in corners],
        "vmax_min": vmax,
        "resolution_m": C.SURFACE_RESOLUTION_M,
    }, indent=2))
    print("  saved   walk_surface.json")

    print("\nWalkshed polygons:")
    sheds = walksheds(dist, serviceable, land)
    out = C.WEB_DATA / "walksheds.geojson"
    sheds.to_file(out, driver="GeoJSON", coordinate_precision=C.COORD_PRECISION)
    kb = out.stat().st_size / 1024
    print(f"  saved   {out.name} ({kb:.0f} KB)")

    land_km2 = gpd.read_file(C.P_BOROS).to_crs(C.CRS_PROJ).geometry.union_all().area / 1e6
    print(f"\n  NYC land area: {land_km2:,.0f} km2")
    for _, r in sheds.sort_values("threshold_min").iterrows():
        print(f"    {r['threshold_min']:>2} min walkshed covers "
              f"{100 * r['area_km2'] / land_km2:4.1f}% of the city's land")
    return grid


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Walk-time raster and walksheds.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    run(args.force)
    print("\nStage 6 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
