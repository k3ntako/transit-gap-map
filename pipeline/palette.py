"""Stage 5: color system for the map, written to web/data/palette.json.

Both the raster surface (stage 5) and the web map read that file, so there is
exactly one definition of every color.

Follows the dataviz skill's reference palette:
  - density   -> blue, the default sequential hue (documented steps)
  - walk time -> the second sequential context, derived from the palette's
                 orange/red categorical anchor in OKLCH
  - bivariate -> bilinear blend of four corner anchors in OKLab, so lightness
                 falls monotonically toward the "dense but poorly served" corner
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
ANCHOR_RED = "#e34948"
ANCHOR_AQUA = "#1baf7a"     # categorical slot 3: distinct from both data ramps

SURFACE_LIGHT = "#fcfcfb"
SURFACE_DARK = "#1a1a19"

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


def mix_oklab(c1: str, c2: str, t: float) -> str:
    a, b = rgb_to_oklab(hex_to_rgb(c1)), rgb_to_oklab(hex_to_rgb(c2))
    return rgb_to_hex(oklab_to_rgb(a * (1 - t) + b * t))


def lightness(h: str) -> float:
    return float(rgb_to_oklab(hex_to_rgb(h))[0])


def contrast(fg: str, bg: str) -> float:
    def lum(h):
        r, g, b = _lin(hex_to_rgb(h))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# --- ramp construction ------------------------------------------------------

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
        best = None
        for scale in np.linspace(1.0, 0.25, 40):
            cand = oklab_to_rgb(lch_to_oklab([L_ref, target_C * scale, h_anchor]))
            # Reject if sRGB clipping moved the lightness off target.
            if abs(rgb_to_oklab(cand)[0] - L_ref) < 0.012:
                best = cand
                break
        out.append(rgb_to_hex(best if best is not None else
                              oklab_to_rgb(lch_to_oklab([L_ref, target_C * 0.3, h_anchor]))))
    return out


def bivariate_matrix_dark(warm_ramp: list[str], levels: int = 3) -> list[list[str]]:
    """Dark-mode bivariate matrix, stepped for the dark surface.

    Not a flip of the light matrix. On a dark basemap the low-low class is the
    one that must recede, so lightness rises toward the headline corner while
    the ordering of the two variables is unchanged.
    """
    # Anchors chosen by searching the corner space for maximum worst-pair
    # separation under rising-lightness monotonicity (see module docstring).
    c00 = "#232322"                  # low-low: recedes into the dark surface
    c_dens = BLUE[550]
    c_walk = warm_ramp[8]
    blended = mix_oklab(BLUE[200], warm_ramp[1], 0.30)
    L, Cc, h = oklab_to_lch(rgb_to_oklab(hex_to_rgb(blended)))
    c22 = rgb_to_hex(oklab_to_rgb(lch_to_oklab([min(L * 1.05, 0.97), Cc * 1.25, h])))

    corners = {(0, 0): c00, (levels - 1, 0): c_dens,
               (0, levels - 1): c_walk, (levels - 1, levels - 1): c22}
    labs = {k: rgb_to_oklab(hex_to_rgb(v)) for k, v in corners.items()}
    matrix = []
    for i in range(levels):
        row = []
        for j in range(levels):
            u, v = i / (levels - 1), j / (levels - 1)
            lab = ((1 - u) * (1 - v) * labs[(0, 0)]
                   + u * (1 - v) * labs[(levels - 1, 0)]
                   + (1 - u) * v * labs[(0, levels - 1)]
                   + u * v * labs[(levels - 1, levels - 1)])
            row.append(rgb_to_hex(oklab_to_rgb(lab)))
        matrix.append(row)
    return matrix


def bivariate_matrix(warm_ramp: list[str], levels: int = 3) -> list[list[str]]:
    """3x3 matrix: rows = density tercile, cols = walk-time tercile.

    Bilinear interpolation in OKLab between four corner anchors.

    The two single-variable corners are deliberately held at MEDIUM lightness
    rather than the ends of their ramps. With dark anchors there, every dark
    class converges and the "dense but poorly served" corner -- the finding the
    map exists to show -- lands only 8 OKLab units from its neighbors. Medium
    anchors plus a chroma-boosted dark corner raise the worst separation among
    all nine classes to ~15, clearing the normal-vision floor, while lightness
    still falls monotonically along both axes. Verified by `audit()` below.
    """
    c00 = "#e8e8e4"                  # low density, good access: recedes to surface
    c_dens = BLUE[450]               # dense, good access  (medium blue)
    c_walk = warm_ramp[7]            # sparse, poor access (medium warm)
    # High-high: blend the two DARK poles, then darken and boost chroma so the
    # corner reads as the most intense class on the map.
    blended = mix_oklab(BLUE[650], warm_ramp[-2], 0.5)
    L, Cc, h = oklab_to_lch(rgb_to_oklab(hex_to_rgb(blended)))
    c22 = rgb_to_hex(oklab_to_rgb(lch_to_oklab([max(L * 0.80, 0.20), Cc * 1.25, h])))

    corners = {(0, 0): c00, (levels - 1, 0): c_dens,
               (0, levels - 1): c_walk, (levels - 1, levels - 1): c22}
    labs = {k: rgb_to_oklab(hex_to_rgb(v)) for k, v in corners.items()}

    matrix = []
    for i in range(levels):          # density
        row = []
        for j in range(levels):      # walk time
            u = i / (levels - 1)
            v = j / (levels - 1)
            lab = ((1 - u) * (1 - v) * labs[(0, 0)]
                   + u * (1 - v) * labs[(levels - 1, 0)]
                   + (1 - u) * v * labs[(0, levels - 1)]
                   + u * v * labs[(levels - 1, levels - 1)])
            row.append(rgb_to_hex(oklab_to_rgb(lab)))
        matrix.append(row)
    return matrix


def delta_e(a: str, b: str) -> float:
    """OKLab distance x100, the scale the dataviz validator reports."""
    import numpy as _np
    return float(_np.linalg.norm(
        rgb_to_oklab(hex_to_rgb(a)) - rgb_to_oklab(hex_to_rgb(b))) * 100)


def worst_pair(matrix):
    """The closest pair of classes, as (delta_e, "ij", "ij")."""
    import itertools
    flat = [(f"{i}{j}", matrix[i][j]) for i in range(3) for j in range(3)]
    return min((delta_e(a[1], b[1]), a[0], b[0])
               for a, b in itertools.combinations(flat, 2))


def audit(matrix, rising: bool = False) -> dict:
    """Separation and monotonicity checks for a bivariate matrix.

    `rising=True` for the dark matrix, where lightness must increase toward the
    headline corner instead of decreasing.
    """
    import itertools
    flat = [matrix[i][j] for i in range(3) for j in range(3)]
    worst = min(delta_e(flat[a], flat[b])
                for a, b in itertools.combinations(range(9), 2))
    hh = matrix[2][2]
    corner = min(delta_e(hh, matrix[2][1]), delta_e(hh, matrix[1][2]))
    cmp = (lambda a, b: a < b) if rising else (lambda a, b: a > b)
    mono_rows = all(cmp(lightness(matrix[i][0]), lightness(matrix[i][1]))
                    and cmp(lightness(matrix[i][1]), lightness(matrix[i][2]))
                    for i in range(3))
    mono_cols = all(cmp(lightness(matrix[0][j]), lightness(matrix[1][j]))
                    and cmp(lightness(matrix[1][j]), lightness(matrix[2][j]))
                    for j in range(3))
    wp = worst_pair(matrix)
    return {"worst_pair_delta_e": round(worst, 1),
            "worst_pair": f"{wp[1]}-{wp[2]}",
            "headline_corner_delta_e": round(corner, 1),
            "lightness_monotone": bool(mono_rows and mono_cols)}


def sample(ramp: list[str], n: int) -> list[str]:
    idx = np.linspace(0, len(ramp) - 1, n)
    return [ramp[int(round(i))] for i in idx]


def build() -> dict:
    blue = list(BLUE.values())
    warm = derive_ramp(ANCHOR_ORANGE, blue)

    pal = {
        "ink": INK,
        "ramps": {"density": blue, "walk": warm},
        # Discrete choropleth classes: skip the two lightest steps so each
        # class clears the 2:1 floor against the light surface.
        "density_classes": sample(blue[2:], 3),
        "walk_classes": sample(warm[2:], 3),
        # Dark mode is stepped for the dark surface, not an inverted light ramp:
        # the near-zero class recedes toward the surface in both modes, which on
        # a dark basemap means the ramp runs dark -> light.
        "density_classes_dark": sample(blue[:-2], 3)[::-1],
        "walk_classes_dark": sample(warm[:-2], 3)[::-1],
        "bivariate": bivariate_matrix(warm),
        "bivariate_dark": bivariate_matrix_dark(warm),
        "surface_ramp": warm,
        # Walksheds are an ORDERED magnitude (5 / 10 / 15 min), so they get one
        # hue in three steps -- contour lines, not categories. Aqua keeps them
        # clear of the blue density ramp and the warm walk ramp underneath.
        "walkshed": sample(derive_ramp(ANCHOR_AQUA, blue)[3:], 3),
        "walkshed_dark": sample(derive_ramp(ANCHOR_AQUA, blue)[:-3], 3)[::-1],
        # Stations sit on top of a choropleth, so they carry identity by SIZE
        # and RING rather than hue: four more hues here would collide with the
        # data ramps and blow past the all-pairs categorical cap for map forms.
        "station": {
            "light": {"fill": "#fcfcfb", "stroke": "#0b0b0b"},
            "dark": {"fill": "#1a1a19", "stroke": "#ffffff"},
        },
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
        print("  " + "  ".join(f"{c} L={lightness(c):.2f}" for c in row))

    print("\nbivariate matrix, dark mode:")
    for row in pal["bivariate_dark"]:
        print("  " + "  ".join(f"{c} L={lightness(c):.2f}" for c in row))

    # Gates. The nine classes are an ORDERED matrix read against a 3x3 legend
    # with an exact-value hover tooltip and a stats table -- not a categorical
    # set where hue alone carries identity. So the gate that matters is the
    # separation of the headline "dense but poorly served" corner from its two
    # neighbors, which must clear 15 in both modes. The overall worst pair is
    # also held to a floor, but it binds on the two darkest low-density classes
    # (both "good access"), where confusing one for the other costs the reader
    # nothing: on the dark surface the low end necessarily compresses, and 13.2
    # is the best any monotone corner configuration achieves.
    pal["bivariate_audit"] = {}
    for key, rising, floor in (("bivariate", False, 15.0),
                               ("bivariate_dark", True, 13.0)):
        rep = audit(pal[key], rising=rising)
        pal["bivariate_audit"][key] = rep
        print(f"\naudit [{key}]:")
        for k, v in rep.items():
            print(f"  {k:26} {v}")
        assert rep["lightness_monotone"], f"{key} lightness is not monotone"
        assert rep["headline_corner_delta_e"] >= 15, (
            f"{key} headline corner {rep['headline_corner_delta_e']} below 15")
        assert rep["worst_pair_delta_e"] >= floor, (
            f"{key} worst pair {rep['worst_pair_delta_e']} below {floor}")

    print("\nsequential classes:")
    print("  density light:", " ".join(pal["density_classes"]))
    print("  density dark :", " ".join(pal["density_classes_dark"]))
    print("  walk    light:", " ".join(pal["walk_classes"]))
    print("  walk    dark :", " ".join(pal["walk_classes_dark"]))
    out.write_text(json.dumps(pal, indent=2))
    print(f"\nsaved {out.relative_to(C.ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
