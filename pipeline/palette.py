"""Stage 6: color system for the map, written to web/data/palette.json.

Both the raster surface (stage 7) and the web map read that file, so there is
exactly one definition of every color.

Follows the dataviz skill's reference palette:
  - density   -> blue, the default sequential hue (documented steps)
  - walk time -> the second sequential context, derived from the palette's
                 orange/red categorical anchor in OKLCH
  - bivariate -> an OKLCH field, not a corner blend: lightness moves with both
                 variables, while warm chroma is scaled by density so distance
                 from a train only shouts where there are people to be stranded
"""

from __future__ import annotations

import json

import numpy as np

import config as C

# Documented blue sequential ramp (dataviz references/palette.md)
BLUE = {
    100: "#cde2fb", 150: "#b7d3f6", 200: "#9ec5f4", 250: "#86b6ef",
    300: "#6da7ec", 350: "#5598e7", 400: "#3987e5", 450: "#2a78d6",
    500: "#256abf", 550: "#1c5cab", 600: "#184f95", 650: "#104281",
    700: "#0d366b",
}
# Categorical anchors from the same reference
ANCHOR_ORANGE = "#eb6834"
ANCHOR_AQUA = "#1baf7a"     # categorical slot 3: distinct from both data ramps
# Categorical slot 7, for the walk-access gap score. Blue already means density
# and warm already means walk time, so a score drawn in either would read as
# "that variable again" instead of "the two combined". Violet is also where the
# two axes mix, which is what the score is.
ANCHOR_VIOLET = "#6250d6"

SURFACE_LIGHT = "#fcfcfb"
SURFACE_DARK = "#1a1a19"

# --- one palette for both modes, and what it costs -------------------------
# Revised 2026-10-11. Every ordered ramp used to be stepped twice, once per
# surface, so that the well-served end receded toward the page in both modes.
# That preserved *emphasis* and inverted *direction*: dark meant under-served
# on a light basemap and light meant under-served on a dark one, so the map
# changed meaning when the reader changed theme. Direction won.
#
# The constraint is arithmetic. A color clearing CONTRAST_FLOOR against both
# #fcfcfb and #1a1a19 has to sit in a middle band of lightness -- measured on
# these hues, OKLCH L roughly 0.42 to 0.78 -- because "away from the surface"
# points in opposite directions on the two surfaces. So one ramp means giving
# up the deep ends: the violet score ramp ran to L 0.338 and now stops at
# 0.435, and its darkest step reads 2.01:1 against the dark basemap instead of
# the 12.5:1 the inverted dark ramp gave it. The finding is clearly visible in
# dark mode rather than loud. That is the trade, stated rather than hidden.
#
# Three consequences worth knowing before editing any of this:
#
#   1. **The score ramp is 6 steps, not 7.** Seven steps inside the band are
#      0.055 of lightness apart, under the 0.06 an ordered ramp needs; six are
#      0.067 apart. The gate is the project's credibility mechanism, so the
#      step count gave way rather than the gate.
#   2. **The bivariate matrix is still stepped per mode.** It is the only
#      layer where lightness carries *two* variables, so it needs twice the
#      range. Searched: inside the band its best achievable
#      `among_populated_delta_e` is 12.3 against a 15.0 floor, and it needs
#      the dual-surface floor relaxed to 1.2:1 before it passes -- which is
#      where a cell genuinely starts dissolving into the page. FIELD keeps
#      both entries for that measured reason.
#   3. **Chrome stays per mode.** `INK`, `station` and `unpopulated` are
#      surface-matched by definition: the unpopulated color has to *be* near
#      the page or it stops reading as "no data". Only the data ramps are
#      shared.
CONTRAST_FLOOR = 2.0

# The opacity `app.js:fillOpacity()` draws the choropleth at, and it has to
# match -- the same standing requirement as SEQ_STEPS, and for a sharper
# reason. `audit_ordinal` checks CONTRAST_FLOOR on a ramp's own hex, so any
# transparency on the map means the gate is measuring a color the reader never
# sees. Measured at the 0.78 this shipped with until 2026-10-12, composited
# over dark-matter's land: every ramp's darkest step reached the screen at
# 1.76-1.78:1 against this 2.0 floor, and the shortfall landed on the
# under-served end, because that is the end nearest the page in dark mode. A
# swept band confirms the gate was not merely mis-reported but unmeetable --
# at 0.78 the band holds four steps of 0.06 lightness, not six.
#
# Lowering this is therefore a palette decision, not a styling one: it narrows
# the band every shared ramp is cut from. The gate below composites at this
# value so the two cannot drift apart again silently.
MAP_FILL_ALPHA = 1.0

# Steps in a ramp the reader sees as discrete bands. Six, not seven, and the
# reason is the band: seven steps inside it are 0.055 of lightness apart and
# an ordered ramp needs 0.06, so the step count gave way rather than the gate.
# `app.js:SEQ_STEPS` must match -- it samples these, and sampling 7 out of a
# 6-step ramp would repeat a color and lose a band silently.
GAP_STEPS = 6
SEQ_STEPS = 6

# The raster ramp is interpolated into a continuous image rather than read as
# bands, so the per-step lightness gate does not apply to it and it keeps a
# fine-grained family. It is still held to the dual-surface contrast floor,
# because it is drawn over the same two basemaps as everything else.
SURFACE_RAMP_STEPS = 13


def dual_band(anchor: str, floor: float = CONTRAST_FLOOR,
              step: float = 0.005) -> tuple[float, float]:
    """The OKLCH lightness range at `anchor`'s hue that stays visible on both
    surfaces. Returned light-end-first, which is the served -> under-served
    direction every ordered ramp now runs in.

    Solved by scan rather than algebra because the chroma a hue can carry
    varies with lightness, and `_fit_chroma` is what decides it -- so the
    luminance of "the violet at L" is not a closed form.
    """
    _, c, h = oklab_to_lch(rgb_to_oklab(hex_to_rgb(anchor)))
    ok = []
    for L in np.arange(0.15, 0.99, step):
        hx = rgb_to_hex(_fit_chroma(float(L), c, h, tol=0.002, tries=240))
        if (contrast(hx, SURFACE_LIGHT) >= floor
                and contrast(hx, SURFACE_DARK) >= floor):
            ok.append(float(L))
    if not ok:
        raise AssertionError(
            f"no lightness at {anchor}'s hue clears {floor}:1 against both "
            f"surfaces; the band is empty and a shared ramp is impossible")
    return max(ok), min(ok)


def banded(anchor: str, n: int) -> list[str]:
    """`n` steps of one hue, served (light) -> under-served (dark), inside the
    band that works on both surfaces."""
    lo, hi = dual_band(anchor)
    return ordinal_ramp(anchor, lo, hi, n)

INK = {
    "light": {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781",
              "grid": "#e1e0d9", "axis": "#c3c2b7", "surface": SURFACE_LIGHT,
              "plane": "#f9f9f7", "border": "rgba(11,11,11,0.10)"},
    "dark": {"primary": "#ffffff", "secondary": "#c3c2b7", "muted": "#898781",
             "grid": "#2c2c2a", "axis": "#383835", "surface": SURFACE_DARK,
             "plane": "#0d0d0d", "border": "rgba(255,255,255,0.10)"},
}

# --- color space conversions (sRGB <-> OKLab) ------------------------------

_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])


def hex_to_rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=float) / 255.0


def rgb_to_hex(rgb) -> str:
    v = np.clip(np.asarray(rgb), 0, 1)
    return "#" + "".join(f"{int(round(c * 255)):02x}" for c in v)


def _lin(c):
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _unlin(c):
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055)


def rgb_to_oklab(rgb) -> np.ndarray:
    lms = _M1 @ _lin(rgb)
    return _M2 @ np.cbrt(lms)


def oklab_to_rgb(lab) -> np.ndarray:
    lms = np.linalg.solve(_M2, np.asarray(lab, dtype=float)) ** 3
    return np.clip(_unlin(np.linalg.solve(_M1, lms)), 0, 1)


def oklab_to_lch(lab):
    L, a, b = lab
    return np.array([L, np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360])


def lch_to_oklab(lch):
    L, Cc, h = lch
    r = np.radians(h)
    return np.array([L, Cc * np.cos(r), Cc * np.sin(r)])


def lightness(h: str) -> float:
    return float(rgb_to_oklab(hex_to_rgb(h))[0])


# --- ramp construction ------------------------------------------------------

def _fit_chroma(L: float, target_C: float, hue: float,
                tol: float = 0.012, tries: int = 40):
    """The most chroma `hue` can carry at lightness `L` before sRGB clipping
    drags the lightness off target by more than `tol`.

    The default tolerance is loose enough to matter: it lets a step land up to
    0.012 off its target lightness, which is a sixth of an ordinal step, so
    ordinal_ramp() asks for a tighter fit and more attempts. derive_ramp()
    keeps the original numbers, because its output is already published.
    """
    for scale in np.linspace(1.0, 0.25, tries):
        cand = oklab_to_rgb(lch_to_oklab([L, target_C * scale, hue]))
        if abs(rgb_to_oklab(cand)[0] - L) < tol:
            return cand
    return oklab_to_rgb(lch_to_oklab([L, target_C * 0.3, hue]))


def derive_ramp(anchor: str, reference: list[str]) -> list[str]:
    """A single-hue ramp at the anchor's hue, matching `reference`'s lightness
    profile. Chroma is capped so each step stays inside sRGB after conversion.
    """
    _, c_anchor, h_anchor = oklab_to_lch(rgb_to_oklab(hex_to_rgb(anchor)))
    out = []
    for ref in reference:
        L_ref, C_ref, _ = oklab_to_lch(rgb_to_oklab(hex_to_rgb(ref)))
        # Keep the anchor's hue; follow the reference ramp's lightness, and take
        # the chroma the hue can actually carry at that lightness.
        target_C = min(c_anchor * (C_ref / max(C_ref, 1e-6)), c_anchor)
        out.append(rgb_to_hex(_fit_chroma(L_ref, target_C, h_anchor)))
    return out


def ordinal_ramp(anchor: str, l_near: float, l_far: float, n: int) -> list[str]:
    """`n` steps of one hue at evenly spaced lightness, l_near -> l_far.

    derive_ramp() follows a reference ramp's lightness profile, which is right
    for a 13-step family and wrong once it is subsampled: 7 of those 11 steps
    gave lightness deltas alternating 0.095 / 0.041, half of them under the
    0.06 an ordinal ramp needs. Here the even spacing *is* the encoding, so the
    steps are generated at even lightness rather than picked from a list.
    """
    _, c_anchor, h_anchor = oklab_to_lch(rgb_to_oklab(hex_to_rgb(anchor)))
    return [rgb_to_hex(_fit_chroma(float(L), c_anchor, h_anchor,
                                   tol=0.002, tries=240))
            for L in np.linspace(l_near, l_far, n)]


def contrast(a: str, b: str) -> float:
    """WCAG relative contrast ratio. The gap ramp's near-surface step is gated
    on this and not on perceptual distance: delta E says two colors differ from
    each other, contrast says one of them is still visible against the page.
    """
    def lum(h: str) -> float:
        r, g, bl = _lin(hex_to_rgb(h))
        return float(0.2126 * r + 0.7152 * g + 0.0722 * bl)
    lo, hi = sorted((lum(a), lum(b)))
    return (hi + 0.05) / (lo + 0.05)


# --- bivariate color field -------------------------------------------------

# Why a field and not a bilinear blend of four corner anchors: under bilinear,
# the sparse+far cell IS a corner anchor, so the only way to quiet it is to
# desaturate that anchor -- which shrinks the whole matrix's gamut and drags
# the center cell toward its neighbors. The measured ceiling for that approach
# is 14.2 among the populated cells, under the 15 floor, across every corner
# configuration searched. The previous matrix cleared 15 only by leaving the
# loudest color (C=0.175) on sparse+far: 491 tracts of nearly empty land,
# while the finding the map exists to show -- the 51 dense+far tracts -- sat at
# C=0.073, less than half. Blending opposing hues for the high-high corner is
# what killed it, since averaging blue and warm in OKLab cancels a and b, and
# no chroma boost afterwards recovers what the average threw away.
#
# The field states the semantics directly instead:
#   lightness    moves with BOTH variables, away from the surface color
#   blue chroma  rides the density axis and fades as walk time rises
#   warm chroma  rides the walk-time axis, SCALED BY DENSITY, so distance from
#                a train only shouts where there are people to be stranded
#
# Parameters come from constrained optimization (scipy differential_evolution)
# maximizing the weakest pair among the populated cells, subject to monotone
# lightness, a >=2x chroma ratio between the headline cell and sparse+far, and
# red staying the most saturated hue on the map. audit() re-checks all of it.
# aL and bL were then nudged off the raw optimum to maximize the margin on the
# tightest gate: the density and walk-time lightness weights trade sparse-row
# separation against separation among the populated cells, so sitting exactly
# on a floor would make the gates fail on any later rounding change.
FIELD = {
    "light": dict(L0=0.9600, aL=0.3550, bL=0.1550, bamp=1.3629,
                  Cb=0.1250, Cw=0.2196, wfloor=0.2504,
                  hb=245.0, hw=52.320, pb=0.5002, pw=1.0546, rising=False),
    # Not an inversion of the light field: on a dark basemap the low-low class
    # is the one that must recede, so lightness RISES toward the headline
    # corner while the ordering of both variables stays the same.
    "dark": dict(L0=0.2069, aL=0.3400, bL=0.1620, bamp=1.4132,
                 Cb=0.1255, Cw=0.2600, wfloor=0.2115,
                 hb=245.0, hw=54.996, pb=0.5000, pw=1.1274, rising=True),
}


def bivariate_field(mode: str = "light", levels: int = 3) -> list[list[str]]:
    """Rows = density tercile (low -> high), cols = walk time (near -> far)."""
    p = FIELD[mode]
    sign = 1.0 if p["rising"] else -1.0
    hb, hw = np.radians(p["hb"]), np.radians(p["hw"])
    matrix = []
    for i in range(levels):
        row = []
        for j in range(levels):
            u = i / (levels - 1)          # density
            v = j / (levels - 1)          # walk time
            L = p["L0"] + sign * (p["aL"] * u + p["bL"] * v * (1 + p["bamp"] * u))
            c_blue = p["Cb"] * u * (1 - v) ** p["pb"]
            c_warm = p["Cw"] * v ** p["pw"] * (p["wfloor"] + (1 - p["wfloor"]) * u)
            a = c_blue * np.cos(hb) + c_warm * np.cos(hw)
            b = c_blue * np.sin(hb) + c_warm * np.sin(hw)
            row.append(rgb_to_hex(oklab_to_rgb([L, a, b])))
        matrix.append(row)
    return matrix


def delta_e(a: str, b: str) -> float:
    """OKLab distance x100, the scale the dataviz validator reports."""
    return float(np.linalg.norm(
        rgb_to_oklab(hex_to_rgb(a)) - rgb_to_oklab(hex_to_rgb(b))) * 100)


def chroma(h: str) -> float:
    return float(oklab_to_lch(rgb_to_oklab(hex_to_rgb(h)))[1])


# Cell indices in the flattened 3x3. The low-density row is held to a lower
# floor deliberately -- see audit().
SPARSE_CELLS = (0, 1, 2)
POPULATED_CELLS = (3, 4, 5, 6, 7, 8)


def audit(matrix, rising: bool = False) -> dict:
    """Separation and monotonicity checks, grouped by the reader's task.

    The previous gate held all 36 pairs to a single floor, which is the wrong
    test here. Telling two walk-time terciles apart *within the low-density
    row* is not a task this map supports: those 748 tracts are largely empty
    land -- airports, marshland, the Staten Island interior -- and the point of
    the rebalance is that they recede rather than compete with the finding. The
    row stays ordered by lightness; it is simply not three independently
    identifiable classes.

    Where the map's claims live -- among the populated cells, and above all the
    dense+far corner -- the full floor still applies.

    `rising=True` for the dark matrix, where lightness must increase toward the
    headline corner instead of decreasing.
    """
    import itertools
    flat = [matrix[i][j] for i in range(3) for j in range(3)]

    def gmin(pairs):
        return min(delta_e(flat[a], flat[b]) for a, b in pairs)

    hh = matrix[2][2]
    cmp = (lambda a, b: a < b) if rising else (lambda a, b: a > b)
    mono_rows = all(cmp(lightness(matrix[i][0]), lightness(matrix[i][1]))
                    and cmp(lightness(matrix[i][1]), lightness(matrix[i][2]))
                    for i in range(3))
    mono_cols = all(cmp(lightness(matrix[0][j]), lightness(matrix[1][j]))
                    and cmp(lightness(matrix[1][j]), lightness(matrix[2][j]))
                    for j in range(3))

    return {
        "among_populated_delta_e": round(gmin(list(itertools.combinations(POPULATED_CELLS, 2))), 1),
        "sparse_vs_populated_delta_e": round(gmin([(a, b) for a in SPARSE_CELLS
                                                   for b in POPULATED_CELLS]), 1),
        "within_sparse_row_delta_e": round(gmin(list(itertools.combinations(SPARSE_CELLS, 2))), 1),
        "headline_corner_delta_e": round(min(delta_e(hh, matrix[2][1]),
                                             delta_e(hh, matrix[1][2])), 1),
        "worst_pair_delta_e": round(gmin(list(itertools.combinations(range(9), 2))), 1),
        "chroma_headline": round(chroma(hh), 3),
        "chroma_sparse_far": round(chroma(matrix[0][2]), 3),
        "chroma_ratio": round(chroma(hh) / max(chroma(matrix[0][2]), 1e-6), 2),
        "lightness_monotone": bool(mono_rows and mono_cols),
    }


def composite(fg: str, bg: str, alpha: float = MAP_FILL_ALPHA) -> str:
    """`fg` drawn over `bg` at `alpha`, which is what the map actually paints.

    sRGB, because that is the space the GPU blends `fill-opacity` in. Blending
    in OKLab would be the better-looking choice and would be the wrong answer
    here: the job is to predict the pixel, not to improve it.
    """
    f, b = hex_to_rgb(fg), hex_to_rgb(bg)
    return rgb_to_hex([alpha * f[i] + (1 - alpha) * b[i] for i in range(3)])


def audit_ordinal(ramp: list[str], surfaces) -> dict:
    """Checks for an ordered ramp, which are not the checks for a matrix.

    Adjacent CVD separation is deliberately not the test: a ramp's neighbors
    are *meant* to be close, and running the categorical floor over them fails
    by design. What has to hold is that the order is readable (monotone
    lightness, with steps even enough to see), that nothing dissolves into the
    page, and that it is one hue -- a ramp that drifts across hues reads as
    diverging, which implies a midpoint this score does not have.

    `surfaces` is a sequence as of 2026-10-11, and the plural is the point:
    one ramp now serves both modes, so "nothing dissolves into the page" has
    to hold against both pages at once. Passing a single surface would leave
    the gate measuring half of what it claims to.
    """
    if isinstance(surfaces, str):
        surfaces = (surfaces,)
    # Contrast is checked on the ramp as COMPOSITED over each surface at the
    # opacity the map draws it with, which at MAP_FILL_ALPHA = 1 is the hex
    # itself. The lightness, hue and separation checks stay on the source
    # colors: those describe the ramp, while contrast describes the pixel.
    L = [lightness(h) for h in ramp]
    deltas = [abs(b - a) for a, b in zip(L, L[1:])]
    rising = L[-1] > L[0]
    hues = [float(oklab_to_lch(rgb_to_oklab(hex_to_rgb(h)))[2]) for h in ramp]
    return {
        "steps": len(ramp),
        "min_delta_l": round(min(deltas), 3),
        "min_delta_e": round(min(delta_e(a, b) for a, b in zip(ramp, ramp[1:])), 1),
        "surface_contrast": round(min(contrast(composite(h, s), s)
                                      for h in ramp for s in surfaces), 2),
        "hue_spread_deg": round(max(hues) - min(hues), 1),
        "lightness_monotone": bool(all((b > a) == rising for a, b in zip(L, L[1:]))),
    }


def sample(ramp: list[str], n: int) -> list[str]:
    idx = np.linspace(0, len(ramp) - 1, n)
    return [ramp[int(round(i))] for i in idx]


def build() -> dict:
    blue = list(BLUE.values())
    warm = derive_ramp(ANCHOR_ORANGE, blue)

    # One banded ramp per hue family, served (light) -> under-served (dark),
    # shared by both modes. See CONTRAST_FLOOR for why the band exists and
    # what it costs; `ramps` keeps the untrimmed documented families because
    # they are what the warm and aqua ramps are derived *from*.
    # Which member of each family supplies the *hue* the band is built at.
    # `banded` refits chroma per step, so the anchor only has to name a hue --
    # but which one matters, because sRGB cannot hold every hue across the
    # band and the drift shows up as a ramp that reads as diverging. Measured
    # hue spread across the 6 banded steps, by anchor:
    #
    #   warm family:  index 3-8 drift 7.0-7.8 deg, index 10 drifts 0.3
    #   blue family:  index 7-8 drift 2.5,         index 3  drifts 0.4
    #
    # So the anchors are the family members whose hue survives the band, not
    # the categorical anchors the families were *derived* from. ANCHOR_ORANGE
    # itself drifts 7.7 degrees and would fail HUE_SPREAD_MAX.
    BAND_ANCHOR = {"blue": 3, "warm": 10}
    blue_band = banded(blue[BAND_ANCHOR["blue"]], SEQ_STEPS)
    warm_band = banded(warm[BAND_ANCHOR["warm"]], SEQ_STEPS)
    aqua_band = banded(ANCHOR_AQUA, 3)

    pal = {
        "ink": INK,
        "ramps": {"density": blue, "walk": warm},
        # What the sequential choropleths sample. Separate from `ramps` above
        # because those are the full documented families and these are the
        # part of them that works on both surfaces -- a consumer that reaches
        # for `ramps` by mistake would get steps outside the band and lose the
        # guarantee silently, so the two are named differently.
        "bands": {"density": blue_band, "walk": warm_band},
        # Discrete choropleth classes, three steps inside the band. No
        # `_dark` counterpart any more: the direction of the ramp is now the
        # same in both modes, which is the whole point of the change.
        "density_classes": sample(blue_band, 3),
        "walk_classes": sample(warm_band, 3),
        "bivariate": bivariate_field("light"),
        "bivariate_dark": bivariate_field("dark"),
        "surface_ramp": banded(warm[BAND_ANCHOR["warm"]],
                               SURFACE_RAMP_STEPS),
        # Walksheds are an ORDERED magnitude (5 / 10 / 15 min), so they get one
        # hue in three steps -- contour lines, not categories. Aqua keeps them
        # clear of the blue density ramp and the warm walk ramp underneath.
        "walkshed": aqua_band,
        # Stations sit on top of a choropleth, so they carry identity by SIZE
        # and RING rather than hue: four more hues here would collide with the
        # data ramps and blow past the all-pairs categorical cap for map forms.
        "station": {
            "light": {"fill": "#fcfcfb", "stroke": "#0b0b0b"},
            "dark": {"fill": "#1a1a19", "stroke": "#ffffff"},
        },
        # The walk-access gap score: one ordered ramp, GAP_STEPS steps. This
        # is the resolution a 4x4 matrix was asked for and could not pass the
        # gates for -- an ordered ramp carries more steps than a matrix
        # because the reader only has to see the order, not identify a cell.
        # Six rather than the original seven, because seven inside the shared
        # band are 0.055 of lightness apart and the gate is 0.06.
        "gap_classes": banded(ANCHOR_VIOLET, GAP_STEPS),
        "unpopulated": {"light": "#e4e3dd", "dark": "#232322"},
    }
    return pal


def main() -> int:
    pal = build()
    out = C.WEB_DATA / "palette.json"
    out.write_text(json.dumps(pal, indent=2))

    print("density ramp (blue, documented):")
    print("  " + " ".join(pal["density_classes"]))
    print("walk ramp (derived from orange anchor):")
    print("  " + " ".join(pal["walk_classes"]))
    print("\nbivariate matrix (rows = density low->high, cols = walk near->far):")
    for row in pal["bivariate"]:
        print("  " + "  ".join(
            f"{c} L={lightness(c):.2f} C={chroma(c):.3f}" for c in row))

    print("\nbivariate matrix, dark mode:")
    for row in pal["bivariate_dark"]:
        print("  " + "  ".join(
            f"{c} L={lightness(c):.2f} C={chroma(c):.3f}" for c in row))

    # Gates, grouped by the reader's task (see audit()). The headline corner --
    # dense and poorly served -- must be unmistakable; the populated cells must
    # all be resolvable from one another; and the low-density row is allowed to
    # compress, because the design intent is that nearly empty land recedes
    # instead of competing with the finding.
    GATES = {"among_populated_delta_e": 15.0,
             "sparse_vs_populated_delta_e": 11.0,
             "within_sparse_row_delta_e": 8.0,
             "headline_corner_delta_e": 15.0,
             "chroma_ratio": 2.0}
    pal["bivariate_audit"] = {}
    for key, rising in (("bivariate", False), ("bivariate_dark", True)):
        rep = audit(pal[key], rising=rising)
        pal["bivariate_audit"][key] = rep
        print(f"\naudit [{key}]:")
        for k, v in rep.items():
            print(f"  {k:30} {v}")
        assert rep["lightness_monotone"], f"{key} lightness is not monotone"
        for gate, floor in GATES.items():
            assert rep[gate] >= floor, (
                f"{key} {gate} is {rep[gate]}, below the {floor} floor")

    # Ordinal-ramp gates: see audit_ordinal(). The lightness floor is the
    # dataviz spec's 0.06 per step; the contrast floor is its 2:1, now
    # measured against **both** surfaces because one ramp serves both modes;
    # and the hue spread keeps it a single hue, so it cannot be read as
    # diverging.
    #
    # Every shared ramp goes through this, not just the score: the whole claim
    # of the 2026-10-11 change is that one palette works on both pages, and a
    # claim that is only checked on one ramp is not checked.
    ORDINAL_GATES = {"min_delta_l": 0.06, "surface_contrast": CONTRAST_FLOOR}
    HUE_SPREAD_MAX = 2.0
    BOTH = (SURFACE_LIGHT, SURFACE_DARK)
    print("\nshared ordered ramps (one palette, both modes):")
    pal["gap_audit"] = {}
    # `banded` is True where the reader sees discrete steps and has to read
    # their order, which is what the lightness and hue gates are for. The
    # raster is interpolated, so only its contrast is gated.
    for key, stepped in (("gap_classes", True), ("bands.density", True),
                         ("bands.walk", True), ("walkshed", True),
                         ("density_classes", True), ("walk_classes", True),
                         ("surface_ramp", False)):
        ramp = (pal["bands"][key.split(".")[1]] if key.startswith("bands.")
                else pal[key])
        rep = audit_ordinal(ramp, BOTH)
        pal["gap_audit"][key] = rep
        print(f"  {key}:")
        print("    " + " ".join(ramp))
        for k, v in rep.items():
            print(f"    {k:22} {v}")
        assert rep["surface_contrast"] >= CONTRAST_FLOOR, (
            f"{key} drops to {rep['surface_contrast']}:1 against one of the "
            f"two surfaces, below the {CONTRAST_FLOOR} floor. Shared ramps "
            f"are gated on the WORSE of the two, so a ramp that passed when "
            f"dark mode had its own stepping can fail here -- that is the "
            f"gate working, not a regression.")
        if not stepped:
            continue
        assert rep["lightness_monotone"], f"{key} lightness is not monotone"
        assert rep["hue_spread_deg"] <= HUE_SPREAD_MAX, (
            f"{key} spans {rep['hue_spread_deg']} degrees of hue, not one hue")
        assert rep["min_delta_l"] >= ORDINAL_GATES["min_delta_l"], (
            f"{key} min_delta_l is {rep['min_delta_l']}, below the "
            f"{ORDINAL_GATES['min_delta_l']} floor")
    out.write_text(json.dumps(pal, indent=2))
    print(f"\nsaved {out.relative_to(C.ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
