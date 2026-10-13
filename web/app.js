// The prose and the figures the title card shares with about.html live in
// stats.js, so the summary beside the map and the long version on the method
// page cannot drift apart.
import {
  fmt, esc, floorMin as floorMinOf, sourceHtml, licenseHtml,
} from "./stats.js";

const MAPLIBRE_URL =
  "https://cdn.jsdelivr.net/npm/maplibre-gl@6.13.0/dist/maplibre-gl.mjs";

let maplibregl;

const BASEMAP = {
  light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};
const GEO = "data/tracts.geojson";
// The columns that used to be properties of GEO. Kept in a second file
// because a column costs about 2.5x more interleaved once per feature than
// as one contiguous array -- see `attachCols`. Not optional: without it every
// paint expression reads null.
const COLS = "data/tract_cols.json";

// The five boroughs plus the sliver of New Jersey, Westchester and Nassau that
// holds stations legitimately serving NYC. The opening view is framed with
// fitBounds rather than a fixed center/zoom so the city fills whatever window
// it lands in, and the recenter button returns to exactly this framing.
const HOME_BOUNDS = [[-74.26, 40.49], [-73.68, 40.94]];

// The pan limit, deliberately much wider than HOME_BOUNDS. MapLibre's bounds
// constraint does not merely clamp the center: when the bounds are narrower
// than the viewport it zooms *in* until they fill it, so bounds drawn tight
// around the city would force a zoom that crops the city on a small window.
// The margin is wider east-west than north-south because windows are landscape
// and NYC is not.
const MAX_BOUNDS = [[-74.69, 40.36], [-73.25, 41.07]];

// The panels sit on top of the map, so on a wide window the city is framed into
// the gap between them. Narrower than this there is no room for the offset to
// buy anything, and on a phone the panels are not over the map at all.
const fitOptions = (duration = 0) => innerWidth >= 1200
  ? { padding: { top: 16, right: 250, bottom: 16, left: 330 }, duration }
  : { padding: 24, duration };

// Tercile names, in words. The tooltip reported the numbers but never named the
// class the fill was showing, which left the reader matching hues against the
// legend by eye. "longest third" rather than "top third": for walk time, "top"
// is ambiguous about whether it means best or worst.
const CLS_DENSITY = ["lowest third", "middle third", "highest third"];
const CLS_WALK = ["shortest third", "middle third", "longest third"];
const CLS_ACCESS = ["cheapest third", "middle third", "costliest third"];

// Tracts under a display floor drop their fill entirely and are carried by
// their outline instead. 0.12 of the data color was the first answer and it
// was the wrong shape: a hue at low alpha over a near-black basemap composites
// to a muddy brown that still reads as a *color*, so the reader decodes it,
// and the one honest answer -- "this is excluded" -- is the one it cannot say.
// Dropping to 0 says it exactly.
//
// The original objection stands and is answered by `lineOpacity`/`lineColor`
// rather than by the fill: a choropleth with holes in it reads as missing
// data, not as excluded, so the polygons must not disappear. They do not --
// they become a mesh in the secondary ink, measured at 2.6:1 against
// dark-matter's land and 1.9:1 against positron's. What used to draw them was
// the fill alone; the outline was #000 at 0.15, which is 1.01:1 on that
// basemap, or nothing.
//
// Hit testing is unaffected, which is what makes this safe: `stations-hit` in
// this file is an invisible circle at opacity 0 whose whole job is to be
// hovered. A faded tract keeps its tooltip and every published statistic.
const FADED_OPACITY = 0;

const $ = (s) => document.querySelector(s);

const state = {
  theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
  // The under-service score, not the bivariate matrix, and the reason is that
  // this is the only layer with a validated number behind it: `verify.py`
  // ranks it at 80% of its worst 25 inside a proposed corridor, 52% clumped,
  // against a 26% baseline. The matrix cross-tabs the same two inputs into
  // terciles, which ties 53 tracts into its dense+far cell whose scores span
  // 5,261 to 80,727 excess person-minutes -- a 15x spread inside one swatch.
  // It also stays stepped per mode, so it is the one layer whose direction
  // flips with the theme; opening on it put the exception on the front page.
  choropleth: "gap",
  // How much the equity terms count. 0 weights residents equally, which is
  // the measured-best version; 1 doubles the weight on the most car-free,
  // highest-poverty tracts.
  gapWeight: 0,
  surface: false,
  walksheds: true,
  stations: true,
  busFtn: false,
  siting: false,
  // Which bivariate cell is isolated, -1 for none. The roadmap asks for this
  // by name, and for a specific reason: low density x short walk is a cell
  // that is computed, painted and nameless, and it is the cheap version of
  // "where could housing go next to transit that already exists".
  biCell: -1,
  hovered: null,
  hoveredStation: null,
  densFloor: 0,
  popFloor: 0,
  // Drop the fill on everything that already meets the standard, so the
  // finding is not competing with two thirds of the city for attention. A
  // *floor*, like the two above, and not a filter: the tracts keep their
  // outline, their tooltip and their place in every published statistic,
  // because a polygon that disappears reads as missing data rather than as
  // excluded data. See FADED_OPACITY for why the fill goes to 0 rather than
  // to a dim tint of itself.
  servedFaded: false,
  // Which job center the "time to a job center" layer shows. -1 is the
  // nearest of all of them, which is the polycentric question and the
  // default; 0..N-1 picks one, which is the question a reader who thinks in
  // places actually asks.
  hub: -1,
  // Set once the theme is an explicit choice -- by the toggle or by the URL --
  // so a shared link carries the theme but an unshared one keeps following the
  // reader's own OS setting.
  themePinned: false,
};

let map, pal, stats, surf, tracts, hubs, cols;

// queryRenderedFeatures throws on an unknown layer id, and during a theme
// switch the style is torn down and rebuilt, so the guard is not paranoia.
const query = (layer, point) =>
  map.getLayer(layer) ? map.queryRenderedFeatures(point, { layers: [layer] }) : [];

// --- permalink --------------------------------------------------------------

// #c=walk&dens=10000&pop=500&t=dark&surface=1&bus=1&sheds=0&v=11.20/40.7050/-73.9500
//
// Only what differs from the default is written, so the usual link stays short
// and reads as a list of deliberate choices. Plain key=value with unknown keys
// ignored, which means a later control is one more key and there is no format
// to version. `v` is zoom/lat/lon, the order OSM and Mapbox hashes use.
const CHOROPLETHS = ["gap", "clump", "bivariate", "density", "walk", "access",
                     "jobs", "jobtime", "hub", "stepfree", "bike", "none"];
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// Reads the hash into `state` and returns the camera it asked for, if any.
// Every value is validated and clamped: a hand-edited URL should land on a
// sane map, never a blank one.
function applyHash() {
  const q = new URLSearchParams(location.hash.replace(/^#/, ""));
  const num = (k, lo, hi) => {
    const v = Number(q.get(k));
    return q.has(k) && isFinite(v) ? clamp(v, lo, hi) : null;
  };

  if (CHOROPLETHS.includes(q.get("c"))) state.choropleth = q.get("c");
  if (q.has("bus")) state.busFtn = q.get("bus") === "1";
  if (q.has("sites")) state.siting = q.get("sites") === "1";
  if (q.has("cell")) {
    const v = Number(q.get("cell"));
    state.biCell = Number.isInteger(v) && v >= 0 && v <= 8 ? v : -1;
  }
  if (q.has("served")) state.servedFaded = q.get("served") === "1";
  const dens = num("dens", 0, 30000);
  if (dens != null) state.densFloor = dens;
  const pop = num("pop", 0, 3000);
  if (pop != null) state.popFloor = pop;
  const w = num("w", 0, 1);
  if (w != null) state.gapWeight = w;
  const h = num("hub", -1, 31);
  if (h != null) state.hub = Math.round(h);
  if (q.get("t") === "dark" || q.get("t") === "light") {
    state.theme = q.get("t");
    state.themePinned = true;
  }
  for (const [key, field] of [["surface", "surface"], ["sheds", "walksheds"],
    ["stations", "stations"]]) {
    if (q.has(key)) state[field] = q.get(key) === "1";
  }

  const v = (q.get("v") || "").split("/").map(Number);
  if (v.length !== 3 || !v.every(isFinite)) return null;
  return {
    zoom: clamp(v[0], 8.5, 16),
    center: [clamp(v[2], -180, 180), clamp(v[1], -85, 85)],
  };
}

let hashTimer;
function syncHash() {
  // Debounced: a pan fires moveend repeatedly and a slider drag fires input on
  // every pixel, and rewriting the URL on each one is wasted work.
  clearTimeout(hashTimer);
  hashTimer = setTimeout(() => {
    const q = [];
    if (state.choropleth !== "gap") q.push(`c=${state.choropleth}`);
    if (state.densFloor) q.push(`dens=${state.densFloor}`);
    if (state.popFloor) q.push(`pop=${state.popFloor}`);
    if (state.servedFaded) q.push("served=1");
    if (state.gapWeight) q.push(`w=${state.gapWeight}`);
    if (state.hub >= 0) q.push(`hub=${state.hub}`);
    if (state.themePinned) q.push(`t=${state.theme}`);
    if (state.surface) q.push("surface=1");
    if (state.busFtn) q.push("bus=1");
    if (state.siting) q.push("sites=1");
    if (state.biCell >= 0 && state.choropleth === "bivariate")
      q.push(`cell=${state.biCell}`);
    if (!state.walksheds) q.push("sheds=0");
    if (!state.stations) q.push("stations=0");
    const c = map.getCenter();
    q.push(`v=${map.getZoom().toFixed(2)}/${c.lat.toFixed(4)}/${c.lng.toFixed(4)}`);
    // replaceState rather than assigning location.hash: assignment pushes a
    // history entry per pan, which turns the back button into a scrub bar.
    history.replaceState(null, "",
      `${location.pathname}${location.search}#${q.join("&")}`);
  }, 300);
}

// Pushes `state` back into the controls, so a link that arrives with floors and
// a layer already set does not show defaults in the panel.
function reflectState() {
  const r = document.querySelector(
    `input[name="choro"][value="${state.choropleth}"]`);
  if (r) r.checked = true;
  $("#t-surface").checked = state.surface;
  $("#t-sheds").checked = state.walksheds;
  $("#t-stations").checked = state.stations;
  // Both of these read from the hash, so both have to be reflected here or a
  // shared link draws the layer with its box unchecked. `#bus=1` had that
  // bug; the siting layer would have inherited it.
  if ($("#t-busftn")) $("#t-busftn").checked = state.busFtn;
  if ($("#t-siting")) $("#t-siting").checked = state.siting;
  $("#f-dens").value = state.densFloor;
  $("#f-pop").value = state.popFloor;
  if ($("#t-served")) $("#t-served").checked = state.servedFaded;
  $("#f-weight").value = state.gapWeight;
  showLayerNotes();
  if ($("#f-hub")) $("#f-hub").value = String(state.hub);
  showHubPick();
  showSitingNote();
  // Open the panel when a floor is in force, or the faded tracts look like a bug.
  if (state.densFloor || state.popFloor || state.servedFaded)
    $("#settings").open = true;
}

// --- the under-service score -------------------------------------------------

// Now "under-service" rather than "walk-access gap": service quality exists in
// the pipeline, so the word is earned. What it is NOT is a blend of everything
// available, and that was decided by measurement rather than taste. See
// `pipeline/verify.py`: nine candidate scores were ranked by how many of their
// worst 25 tracts fall inside the eight corridors agencies have actually
// proposed, against a 26% baseline.
//
//   pop x excess walk     80%   <- this one
//   need x excess walk    76%
//   poor people x excess  76%
//   pop x excess access   76%
//   pop x need x excess   72%
//   carfree hh x excess   56%
//   ranks (what shipped)  20%   <- indistinguishable from random
//
// Two of those lines are counter-intuitive and both are load-bearing. Adding
// the *wait* makes it worse, because a frequency problem is solved by running
// more trains on existing track, not by a new line -- access cost is the better
// measure of a rider's morning and the worse predictor of where an agency
// builds. And the equity variables make it worse too, in every form tried.
// They remain available as sliders, because the MTA defines its own Equity
// Areas by exactly the zero-vehicle variable and a reader is entitled to
// weight it; the default is zero, and the measurement is why.
// How many bands the score paints: band 0, inside the standard, plus
// quantiles of the tracts past it. Read from the ramp rather than written
// down, because it was written down: palette.py cut GAP_STEPS from seven to
// six on 2026-10-11 and this stayed at 7, so the paint expression asked for
// ramp[6], got undefined, and MapLibre rejected the whole layer -- a `c=gap`
// permalink opened on a map with no tracts at all. The band count is the
// palette's to decide, so this asks it.
const gapBands = () => pal.gap_classes.length;

// The floor, in minutes, below which there is no gap to measure. Must match
// C.ACCESS_FLOOR_MINUTES; shipped in stats.json so it cannot drift.
const floorMin = () => floorMinOf(stats);

// Percentile rank rather than the raw value. Density is roughly log-normal --
// the middle tract holds 16,664 people/km2 and the densest 110,790 -- while
// walk time is not, so a product of raw values would be a density map with a
// rounding error. Ranks put both on 0-1, where neither can dominate.
//
// `(i + 1) / n` with later writes winning means ties take the highest position,
// so a rank reads as "the share of tracts at or below this one".
//
// A missing value gets *no* rank rather than sorting as if it were zero. Eleven
// tracts have no car-free-household estimate and four no poverty estimate, and
// ranking those as the least in need in the city is a fabrication. They fall
// through to NEUTRAL_RANK instead, in the JS score and the paint expression
// alike.
function rankInto(feats, src, dst) {
  const vals = feats.map((f) => f.properties[src])
    .filter((v) => v != null && isFinite(v))
    .sort((a, b) => a - b);
  const rank = new Map();
  vals.forEach((v, i) => rank.set(v, (i + 1) / vals.length));
  for (const f of feats) f.properties[dst] = rank.get(f.properties[src]);
}

// Every per-geography column, joined onto the features by geoid.
//
// `tracts.geojson` ships geometry and `geoid` and nothing else. The columns
// live in parallel arrays in `tract_cols.json` because gzip's window cannot
// span 2,325 interleaved features, so the same numbers cost roughly 2.5x more
// as per-feature properties than as contiguous arrays -- measured, the split
// moved the pair of files well under the budget that one file was pressed
// against. This is the join, and like `attachHubs` it has to run before
// `addSource`.
//
// A missing or partial sidecar is fatal rather than tolerated: with no columns
// there is nothing to paint, no table to rank and no tooltip to show, so it is
// loaded like the geometry and not like `hubs.json`. Returns a reason to show
// the reader, or null when every feature got its row.
function attachCols() {
  const geoids = cols?.geoids;
  const table = cols?.cols;
  if (!geoids?.length || !table) return `${COLS} carries no columns`;
  const row = new Map(geoids.map((g, i) => [g, i]));
  const names = Object.keys(table);
  let joined = 0;
  for (const f of tracts.features) {
    const i = row.get(f.properties.geoid);
    if (i == null) continue;
    for (const k of names) f.properties[k] = table[k][i];
    joined++;
  }
  return joined === tracts.features.length ? null
    : `${COLS} covers ${joined} of ${tracts.features.length} areas`;
}

// Per-hub travel times, joined onto the features by geoid.
//
// Written here rather than shipped in tracts.geojson because eight more
// per-feature columns measured about 100 KB gzipped against 45 KB of remaining
// budget, while the same numbers as parallel arrays are 34: gzip's window
// cannot span 2,325 interleaved features, so a column costs roughly 2.5x more
// interleaved than contiguous.
//
// It must run **before** `rankTracts`, and the reason is the bug that painted
// the whole city black: `addSource` serializes the properties object, so a
// property written after that point is invisible to every paint expression and
// fails silently. rankTracts freezes the properties for exactly that reason,
// which means this would also throw rather than fail quietly -- but the right
// place is still ahead of it.
function attachHubs() {
  if (!hubs?.geoids || !hubs?.minutes?.length) {
    hubs = null;
    return;
  }
  const row = new Map(hubs.geoids.map((g, i) => [g, i]));
  for (const f of tracts.features) {
    const i = row.get(f.properties.geoid);
    if (i == null) continue;
    hubs.minutes.forEach((arr, h) => { f.properties[`h${h}`] = arr[i]; });
  }
}

// The ranks are written into the fetched GeoJSON *before* it is handed to the
// source, so the score is a paint expression over ordinary properties: a weight
// change is then one setPaintProperty, with no per-feature state to push and no
// re-parse of 1.7 MB of geometry. Nothing is added to the payload on disk.
function rankTracts() {
  const scored = tracts.features.filter(
    (f) => f.properties.pop > 0 && f.properties.walk != null);
  // `rd` is now read only as the marker for "this tract has a score" -- the
  // density and walk-time ranks were the old rank-product score, which the
  // alignment test measured at 26% and replaced. The walk rank went with it;
  // the density one stays because the presence test is cheap and already
  // spelled out in the paint expression.
  rankInto(scored, "dens", "rd");
  rankInto(scored, "nveh", "rnv");
  rankInto(scored, "pov", "rpv");
  // Excess minutes beyond the standard, and the score at full population
  // weight. Written onto the feature so the paint expression stays a plain
  // read: a weight change is one setPaintProperty, not a re-parse of 2 MB.
  const fl = floorMin();
  for (const f of scored) {
    const p = f.properties;
    p.exc = Math.max((p.walk ?? 0) - fl, 0);
  }
  // Seal the properties. `addSource` serializes this object to the worker, so
  // a property written any later is invisible to every paint expression and
  // fails silently -- which is exactly how the gap layer painted the whole
  // city black. Modules are strict, so a late write now throws on the spot.
  for (const f of tracts.features) Object.freeze(f.properties);
  return scored.length;
}

// Excess person-minutes: how many people, times how far past the standard they
// are. Real units, one research-anchored constant, nothing normalized.
//
// `w` is now the equity weight, not a density/walk tradeoff. At w=0 the
// multiplier is population alone, which is the measured-best version. Turning
// it up blends in the ranked need terms -- car-free households and poverty --
// so a reader who holds that a car-free household beyond the standard is worse
// off than a car-owning one can say so and watch the map change. The product
// form is kept because a sum would let either half carry a tract on its own.
//
// Multiplying is also what keeps the empty-land failure mode out. The measured
// containment: 3 of the worst 153 tracts have under 2,000 people at w=0, and
// the shipped population floor fades those on demand. Pure need ranks, with no
// population term, put 27 in -- the smallest a 43-person tract -- which is why
// `w` scales the need terms *into* a population weight rather than replacing
// it.
//
// A tract with no estimate for a term takes the middle of the distribution,
// which is the one value that leaves its score unchanged at every weight. That
// is also what lets the blend be written as a paint expression: the formula no
// longer depends on *which* terms a feature happens to have.
const NEUTRAL_RANK = 0.5;
const rankOr = (v) => (v != null && isFinite(v) ? v : NEUTRAL_RANK);

// w=0 -> 1; both ranks neutral -> 1 at any w. `w * 2 * mean` of two terms is
// `w * (a + b)`, which is the form needExpr() mirrors.
const needBlend = (p, w) =>
  w ? 1 - w + w * (rankOr(p.rnv) + rankOr(p.rpv)) : 1;

// The same arithmetic as a MapLibre expression. `["number", v, fallback]` is an
// assertion that falls through on a null or absent property, so this is
// `rankOr` -- which is why rankInto leaves a missing rank unset rather than
// ranking it zero. Keeping the two definitions the same shape is the point:
// the tooltip, the table and the fill have to agree.
// Carless crossed with poverty, derived rather than shipped: both shares are
// already properties and the two medians are in stats.json, so a column here
// would cost ~2 KB to save a comparison. The order of the four is fixed by
// the arithmetic, so the labels are indexed by it rather than matched.
//
// The measured result is the reason this is a lens and not a score term:
// "carless without means" is the *best* served quadrant in the city (18% of
// its people past the 10-minute standard, against 71% for the car-owning,
// better-off quadrant), because poverty and carlessness concentrate in the
// dense core the subway was built around. It names who depends on transit,
// not who is missing it.
const NEED_QUADRANTS = ["Car-owning, better off", "Carless by choice",
                        "Poorer, car-owning", "Carless without means"];
function needQuadrant(p) {
  const c = stats.need_cross;
  if (!c || p.nveh == null || p.pov == null) return -1;
  return (p.nveh > c.median_no_vehicle_pct ? 1 : 0)
    + (p.pov > c.median_poverty_pct ? 2 : 0);
}

const needExpr = (w) => w
  ? ["+", 1 - w, ["*", w, ["+",
      ["number", ["get", "rnv"], NEUTRAL_RANK],
      ["number", ["get", "rpv"], NEUTRAL_RANK]]]]
  : 1;

const gapScore = (p, w) => (p.pop ?? 0) * (p.exc ?? 0) * needBlend(p, w);

// Quantile breaks, not equal intervals. The product is heavily skewed -- half
// the city scores under 0.18 of a 0-0.79 range -- so equal slices would put
// nearly everything in the palest band and eight tracts in the darkest.
//
// The bands come out equal to within a tract or two, except at w=0 or w=1,
// where the score collapses to one variable and that variable's ties cannot be
// split: walk time ships rounded to 0.1 min, so those bands run ~300-345.
// Band 0 is "inside the standard", and the others are quantiles of the
// tracts that are not. This is structural, not cosmetic: 58.8% of tracts score
// exactly zero because they are within a ten-minute walk, and quantile breaks
// cannot split a tie, so plain septiles left three of the seven colors empty.
// Most of the map paints the palest step, which is the honest picture and a
// visibly emptier one than a rank product gave.
let gapCache = { w: null };
function gapCompute(w) {
  if (gapCache.w === w) return gapCache;

  const scored = tracts.features.filter((f) => f.properties.rd != null);
  const all = scored.map((f) => gapScore(f.properties, w));
  const over = all.filter((v) => v > 0).sort((a, b) => a - b);
  const breaks = [1e-9];          // anything above zero leaves band 0
  const n = gapBands();
  for (let i = 1; i < n - 1; i++) {
    let b = over[Math.floor(i * over.length / (n - 1))];
    const prev = breaks[breaks.length - 1];
    // `step` requires strictly ascending stops, and a quantile can repeat when
    // many tracts share a score. Nudging keeps the band empty instead of
    // throwing out the whole expression.
    if (b === undefined || b <= prev) b = prev + 1e-6;
    breaks.push(b);
  }
  gapCache = { w, sorted: all.slice().sort((a, b) => a - b), over, breaks };
  return gapCache;
}

// Share of tracts scoring at or below `s`, for the tooltip.
function gapPercentile(s) {
  const { sorted } = gapCompute(state.gapWeight);
  let lo = 0, hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] <= s) lo = mid + 1; else hi = mid;
  }
  return lo / sorted.length;
}

// One ramp, both modes. See palette.py's CONTRAST_FLOOR note: every step of
// every ordered ramp now clears 2:1 against *both* the light and the dark
// surface, which is what lets the direction be the same in both -- darker is
// always further from the standard. It cost the deep ends of the ramps and one
// step of resolution, and it bought a map that does not change meaning when
// the reader changes theme.
const gapRamp = () => pal.gap_classes;

function gapColor() {
  const ramp = gapRamp();
  const w = state.gapWeight;
  // The score evaluates in the paint expression, so a weight change is one
  // setPaintProperty over properties the source already holds.
  //
  // It read a precomputed `nb` property instead, until that was measured: the
  // GeoJSON is serialized to the worker when `addSource` is called, so a
  // property written onto `tracts.features` after that point never reaches the
  // source. `nb` was written on the first `gapCompute`, which happens inside
  // `addLayers()` -- one call too late. `["get", "nb"]` therefore returned
  // null, `*` threw on every feature, and MapLibre fell back to the
  // `fill-color` default, which is black: the whole city painted one color and
  // the slider moved only the legend. Anything the paint expression reads must
  // either ship in the payload or be written before `addSource`.
  const score = ["*", ["get", "pop"], ["get", "exc"], needExpr(w)];
  const steps = ["step", score, ramp[0]];
  gapCompute(w).breaks.forEach((b, i) => steps.push(b, ramp[i + 1]));
  // No rank means unpopulated, or no station reachable on foot: no score, so it
  // takes the unpopulated color rather than quietly landing in band 0.
  return ["case", ["!", ["has", "rd"]], unpop(), steps];
}

// --- paint expressions ------------------------------------------------------

const biFlat = () => state.theme === "dark" ? pal.bivariate_dark.flat() : pal.bivariate.flat();
const densCls = () => pal.density_classes;
const walkCls = () => pal.walk_classes;

// --- the Phase 4 layers ------------------------------------------------------

// The banded ramps palette.py audits, read as shipped rather than resampled
// here. `pal.bands` is deliberately not `pal.ramps`: the latter are the full
// documented families and reaching for them would get steps outside the
// dual-surface band and lose the guarantee silently, which is why the two
// have different names. No dark-mode reversal any more -- that reversal is
// precisely what made the map mean the opposite thing in the other theme.
//
// SEQ_STEPS must equal palette.py's: it also sets the number of quantile
// breaks, and asking for more bands than the ramp has colors would repeat a
// color and silently merge two bands.
const SEQ_STEPS = 6;
function seqRamp(name) {
  const r = pal.bands?.[name];
  if (!r?.length) throw new Error(`palette.json has no band "${name}"`);
  return r;
}

// Quantile breaks, for the same reason the gap layer uses them: jobs reachable
// is heavily skewed -- a Manhattan tract reaches seventy times what a
// Rockaways tract does -- so equal intervals would paint the whole city the
// palest band. Computed in the browser from the shipped column, so no breaks
// have to be shipped and a field can be added without touching stats.json.
const seqCache = new Map();
function seqBreaks(key, positive) {
  const ck = positive ? key + "+" : key;
  if (seqCache.has(ck)) return seqCache.get(ck);
  const vals = tracts.features.map((f) => f.properties[key])
    .filter((v) => v != null && isFinite(v) && (!positive || v > 0))
    .sort((a, b) => a - b);
  const raw = [];
  for (let i = 1; i < SEQ_STEPS; i++)
    raw.push(vals[Math.floor(i * vals.length / SEQ_STEPS)]);
  // Quantile breaks cannot split a tie, so a repeated break would make one
  // band unreachable and leave a color unused. Dropping the duplicate leaves
  // fewer bands, which is the honest picture of a column with a big tie in it.
  const out = raw.filter((v, i) => i === 0 || v > raw[i - 1]);
  seqCache.set(ck, out);
  return out;
}

// The under-served blocks layer. Shaded by the population of the whole block
// rather than of the tract, which is the whole point: the question a block
// answers is "how many people would one project reach", and a tract cannot
// answer it. `cluid` is -1 for every tract that is not past the standard, and
// those are drawn as the unpopulated color rather than as the palest step --
// "no gap to measure" is not "the smallest gap", and `clupop` is 0 for all of
// them, which would otherwise swallow the bottom quantiles whole. That is
// also why the breaks are computed over the positive values only.
function clumpFill() {
  const b = seqBreaks("clupop", true);
  const colors = seqRamp("walk");
  if (!b.length)
    return ["case", ["<", ["get", "cluid"], 0], unpop(), colors[0]];
  const stops = [];
  b.forEach((v, i) => stops.push(v, colors[i + 1]));
  return ["case",
    ["<", ["get", "cluid"], 0], unpop(),
    ["step", ["get", "clupop"], colors[0], ...stops]];
}

// Which property the hub layer reads: the precomputed nearest-center column,
// or one hub's own array injected before the source was built. Falls back to
// the nearest-center column whenever that array is not actually there -- a
// build from before Phase 4, a failed hubs.json fetch, or a hand-edited
// `#hub=` past the end of the list. Without this the paint expression reads a
// property no feature has, which is the same silent class of failure as the
// one that painted the city black.
const hubKey = () =>
  (state.hub >= 0 && hubs && state.hub < hubs.minutes.length)
    ? `h${state.hub}` : "hub";

// A `step` expression over quantile breaks, with nulls handled before it --
// `step` needs a numeric input, and a tract with no path to any center
// is null rather than large.
function seqFill(key, ramp, invert) {
  const b = seqBreaks(key);
  const colors = invert ? ramp.slice().reverse() : ramp;
  // `step` is invalid with no stops, so a column that is entirely null or
  // entirely one value paints flat rather than throwing on parse.
  if (!b.length) return ["case", ["<", ["get", "dc"], 0], unpop(), colors[0]];
  const stops = [];
  b.forEach((v, i) => stops.push(v, colors[i + 1]));
  return ["case",
    ["==", ["get", key], null], unpop(),
    ["<", ["get", "dc"], 0], unpop(),
    ["step", ["get", key], colors[0], ...stops]];
}
const shedCls = () => pal.walkshed;
const unpop = () => pal.unpopulated[state.theme];

function fillColor() {
  if (state.choropleth === "gap") return gapColor();
  if (state.choropleth === "clump") return clumpFill();
  if (state.choropleth === "bivariate") {
    const c = biFlat();
    return ["match", ["get", "bi"],
      0, c[0], 1, c[1], 2, c[2], 3, c[3], 4, c[4],
      5, c[5], 6, c[6], 7, c[7], 8, c[8], unpop()];
  }
  if (state.choropleth === "access") {
    // Banded in the paint expression from `acc` and the shipped breakpoints,
    // because a second class column would cost 5 KB to say what `acc` already
    // says. `step` needs a numeric input, so the null case -- unpopulated, or
    // no station reachable on foot -- is handled before it.
    const ramp = walkCls();
    const b = stats.breaks.access_min;
    return ["case",
      ["==", ["get", "acc"], null], unpop(),
      ["<", ["get", "dc"], 0], unpop(),
      ["step", ["get", "acc"], ramp[0], b[0], ramp[1], b[1], ramp[2]]];
  }
  // More jobs is better, so the ramp is inverted: the dark end of a sequential
  // scale reads as "most of whatever is being measured", and here the thing
  // worth finding on the map is the tracts that reach the fewest.
  if (state.choropleth === "jobs")
    return seqFill("j45", seqRamp("density"), true);
  // The same jobs data with the axes swapped: minutes rather than a count, so
  // more is worse and it shares the walk ramp's direction. This is the layer
  // that still says something where the one above saturates -- a fixed time
  // budget bottoms out at zero for the 1,062 tracts that cannot reach a tenth
  // of the city's jobs inside it, and minutes keep counting.
  if (state.choropleth === "jobtime")
    return seqFill("jt10", seqRamp("walk"), false);
  // More minutes is worse, which is the same direction walk time already runs
  // in, so it gets the same ramp.
  if (state.choropleth === "hub")
    return seqFill(hubKey(), seqRamp("walk"), false);
  // Minutes to the nearest station with a step-free route built. Same unit
  // and same direction as walk time, so deliberately the same ramp rather
  // than a new one: a reader comparing this layer against "Walk time to
  // station" is comparing two readings of one measure, and a second hue
  // would suggest they are different quantities.
  if (state.choropleth === "stepfree")
    return seqFill("sf", seqRamp("walk"), false);
  // Minutes by bike, which is minutes again and in the same direction, so it
  // shares the walk ramp for the same reason the step-free layer does. Note
  // its breaks are its own quantiles, so the colors are NOT comparable
  // step-for-step with the walk layer's -- the legend prints the numbers.
  if (state.choropleth === "bike")
    return seqFill("bike", seqRamp("walk"), false);
  const [key, ramp] = state.choropleth === "density"
    ? ["dc", densCls()] : ["wc", walkCls()];
  return ["match", ["get", key], 0, ramp[0], 1, ramp[1], 2, ramp[2], unpop()];
}

// Which access tercile a tract sits in, for the tooltip and the legend
// outline. Mirrors the `step` above; there is no `ac` field to read.
const accessClass = (p) => {
  if (p.acc == null || p.dc < 0) return -1;
  const b = stats.breaks.access_min;
  return p.acc < b[0] ? 0 : p.acc < b[1] ? 1 : 2;
};

// The floors are a display filter, not a change to the data: a faded tract is
// still in the tooltip and still in every published statistic.
const belowFloor = () => {
  const any = ["any",
    ["<", ["coalesce", ["get", "dens"], 0], state.densFloor],
    ["<", ["coalesce", ["get", "pop"], 0], state.popFloor]];
  // "Meets the standard" is one definition across every layer, and it is the
  // project's own: within C.ACCESS_FLOOR_MINUTES of a station, so `exc` is 0.
  // Using the showing layer's own good end instead would mean the control
  // meant something different on each of the nine, and `exc` is the threshold
  // the score, the contiguity blocks and the clump layer all already use.
  // `coalesce` because `exc` is only written onto scored tracts -- an
  // unpopulated one has no excess and should fade with the rest.
  if (state.servedFaded) any.push(["<=", ["coalesce", ["get", "exc"], 0], 0]);
  // Isolating one cell of the matrix is the same kind of filter as a floor:
  // the other cells fade, they keep their outline and their tooltip, and they
  // still count in every published figure. Only meaningful on the layer that
  // paints `bi`, so it is scoped to it rather than leaking onto the ramps.
  if (state.biCell >= 0 && state.choropleth === "bivariate")
    any.push(["!=", ["coalesce", ["get", "bi"], -1], state.biCell]);
  return any;
};

// How many populated tracts are still drawn at full opacity. The roadmap asks
// for this by name, and the reason is specific: floors stack, and three of
// them together can empty the map without any single one looking wrong.
function showCount() {
  const el = $("#showing");
  if (!el || !tracts) return;
  let shown = 0, pop = 0, total = 0;
  for (const f of tracts.features) {
    const p = f.properties;
    if (!p.pop) continue;
    total++;
    if (isBelowFloor(p)) continue;
    shown++;
    pop += p.pop;
  }
  const none = shown === 0;
  el.innerHTML = none
    ? `<strong>No areas are showing.</strong> Together the filters exclude
       every populated area. Loosen one of them.`
    : `Showing <strong>${fmt(shown)}</strong> of ${fmt(total)} populated
       areas, holding ${fmt(pop)} people.`;
  el.style.color = none ? "var(--warn, #b4541a)" : "";
}

const isBelowFloor = (p) => fadedBy(p).length > 0;

// Which filters fade a tract, named rather than counted. A tooltip that says
// only "faded by a filter" leaves the reader unable to connect the effect to
// the control -- three of them can do it, they stack, and the one that fires
// most surprisingly is a floor catching a tract with a high score but few
// residents, which looks like the map hiding the finding.
const fadedBy = (p) => {
  const on = [];
  if ((p.dens ?? 0) < state.densFloor) on.push("the density floor");
  if ((p.pop ?? 0) < state.popFloor) on.push("the population floor");
  if (state.servedFaded && (p.exc ?? 0) <= 0) on.push("“meets the standard”");
  if (state.biCell >= 0 && state.choropleth === "bivariate"
      && (p.bi ?? -1) !== state.biCell) on.push("the chosen cell");
  return on;
};

// Opaque, and `palette.py:MAP_FILL_ALPHA` must match. The ramps are audited
// against CONTRAST_FLOOR on their own hex, so any transparency here means the
// audited color is not the one drawn: measured at the old 0.78 over
// dark-matter's land, the darkest step of every ramp reached the screen at
// 1.76-1.78:1 against a 2:1 gate, and the loss fell on the under-served end --
// the finding -- because that is the end nearest the page in dark mode.
// Opacity is affordable to give up because the labels now draw ON TOP of the
// choropleth (see labelAnchor), which is what the transparency was really
// buying: at 0.78 they were washed out underneath it.
//
// There is no hover case any more. It used to lift the fill 0.78 -> 0.95,
// which has nowhere to go from 1.0 and was always the weaker of the two hover
// signals; `tracts-hover` draws a 2px ink ring, which is the mark spec.
const fillOpacity = () => ["case", belowFloor(), FADED_OPACITY, 1.0];

// The outline is now the only thing drawing an excluded tract, so it gets more
// opacity than the fill it replaces rather than less. Still recessive: a
// hairline mesh against solid neighbors reads as "set aside", which is the
// intent, where a fill of any hue reads as a value.
const lineOpacity = () => ["case", belowFloor(), 0.40, 0.5];

// Two different jobs, so two colors. On a drawn tract the hairline separates
// adjacent fills and wants to be the surface color; on an excluded one there
// is no fill to separate and the line has to be visible against the basemap,
// which the surface color by definition is not.
const lineColor = () => ["case",
  belowFloor(), pal.ink[state.theme].secondary,
  state.theme === "dark" ? "#000" : "#fff"];

// The id of the first label layer, so the choropleth can be inserted beneath
// the basemap's text instead of on top of it. Both CARTO styles end in a run
// of symbol-only layers -- 26 in positron, 27 in dark-matter -- and that run
// is every place, road and water name on the map. Found by walking back from
// the end rather than naming `watername_ocean`, which is the id both styles
// happen to use today: a basemap that renamed it would silently put the labels
// back underneath, and this way it degrades to appending, which is the old
// behavior rather than a broken one.
function labelAnchor() {
  const layers = map.getStyle().layers || [];
  let i = layers.length;
  while (i > 0 && layers[i - 1].type === "symbol") i--;
  return i < layers.length ? layers[i].id : undefined;
}

// --- layers -----------------------------------------------------------------

// Opacity lives in one place now. It used to be applied twice -- 0.82 baked
// into the PNG's alpha channel by surface.py and 0.85 again here -- which
// multiplied out to this number without anyone choosing it. The PNG is now
// fully opaque where it has a value, which is also what lets it be an indexed
// image, so the composite is preserved here rather than being silently
// changed into something rounder.
const SURFACE_OPACITY = 0.82 * 0.85;

// An image source starts downloading the moment it is added, so adding this
// one in addLayers() cost every reader ~62 KB for a layer most of them never
// turn on, in competition with the basemap tiles and the walksheds. It is
// still prefetched -- the toggle should feel instant -- just last, after the
// map has gone quiet. Idempotent, because the toggle calls it too in case a
// reader gets there before the idle callback does.
function addSurface() {
  // No tracts-fill means the style was torn down by a theme switch, and that
  // switch has scheduled its own load. There is nothing here to attach to.
  if (!map.getLayer("tracts-fill")) return;
  if (!map.getSource("surface")) {
    map.addSource("surface", {
      type: "image",
      url: `data/${surf.images[state.theme]}`,
      coordinates: surf.coordinates,
    });
  }
  if (!map.getLayer("surface-img")) {
    map.addLayer({
      id: "surface-img", type: "raster", source: "surface",
      layout: { visibility: state.surface ? "visible" : "none" },
      paint: {
        "raster-opacity": SURFACE_OPACITY,
        "raster-resampling": "linear",
      },
    }, "tracts-fill");
  }
}

// `idle` fires when every tile and source has settled; requestIdleCallback
// then waits for a free frame after that, so the fetch lands in genuinely
// spare time. The timeout is the backstop for a tab that never idles, and
// setTimeout is the fallback for Safari, which has no requestIdleCallback.
const whenIdle = globalThis.requestIdleCallback
  ? (f) => globalThis.requestIdleCallback(f, { timeout: 3000 })
  : (f) => setTimeout(f, 300);

function deferSurface() {
  whenIdle(() => addSurface());
}

function addBusFtn() {
  if (!map.getLayer("tracts-fill")) return;
  if (!map.getSource("busftn")) {
    map.addSource("busftn", {
      type: "geojson", data: "data/bus_ftn.geojson",
    });
  }
  if (!map.getLayer("bus-ftn")) {
    const ink = pal.ink[state.theme];
    map.addLayer({
      id: "bus-ftn", type: "circle", source: "busftn",
      layout: { visibility: state.busFtn ? "visible" : "none" },
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 1.4, 14, 3.2],
        "circle-color": ink.secondary,
        "circle-opacity": 0.55,
        "circle-stroke-width": 0,
      },
    }, "stations-hit");
  }
}

// Where one more station would help most: 4,840 hypothetical stations, drawn
// as hollow circles sized by the excess person-minutes each one removes.
//
// Hollow, and in the station color rather than a ramp of its own, because
// that is exactly what each dot is -- a station that is not there. The rule
// is the one ROADMAP.md sets for anything proposed: it has to be
// unmistakable in a screenshot that this is not the network as it is. So the
// layer is off by default, the marker is an outline, and the legend says
// "hypothetical" rather than "proposed", since nobody has proposed these.
//
// Radius on the square root of the value: these are areas on the screen, and
// a radius proportional to the number would make the top site look thirty
// times the size of a median one rather than five and a half.
function addSiting() {
  if (!map.getLayer("tracts-fill")) return;
  if (!map.getSource("siting")) {
    map.addSource("siting", { type: "geojson", data: "data/siting.geojson" });
  }
  if (!map.getLayer("siting-dots")) {
    const st = pal.station[state.theme];
    const best = stats.siting?.best_excess_min_cut || 1;
    const r = (frac) => ["interpolate", ["linear"], ["zoom"],
      10, 2 + 7 * frac, 14, 4 + 16 * frac];
    map.addLayer({
      id: "siting-dots", type: "circle", source: "siting",
      layout: { visibility: state.siting ? "visible" : "none" },
      paint: {
        "circle-radius": ["interpolate", ["linear"],
          ["sqrt", ["/", ["coalesce", ["get", "cut"], 0], best]],
          0, r(0), 1, r(1)],
        "circle-color": st.stroke,
        "circle-opacity": 0.12,
        "circle-stroke-color": st.stroke,
        "circle-stroke-width": 1.2,
        "circle-stroke-opacity": 0.85,
      },
    }, "stations-hit");
  }
}

function addLayers() {
  const st = pal.station[state.theme];

  // setStyle() tears the style down, but guard anyway so this is safe to call
  // more than once.
  const LAYERS = ["tracts-fill", "tracts-line", "tracts-hover", "surface-img",
    "bus-ftn", "siting-dots", "stations-hit", "stations-rail",
    "stations-subway", "hub-ring", "hub-core",
    ...stats.thresholds_min.map((t) => `shed-${t}`)];
  LAYERS.forEach((l) => { if (map.getLayer(l)) map.removeLayer(l); });
  ["tracts", "stations", "walksheds", "surface", "busftn",
   "hubpoints"].forEach((sr) => {
    if (map.getSource(sr)) map.removeSource(sr);
  });

  // The parsed object, not the URL: main() has already fetched it to rank the
  // tracts, so handing over the URL would be a second download of 1.7 MB.
  map.addSource("tracts", { type: "geojson", data: tracts, promoteId: "geoid" });
  // generateId supplies the feature ids that feature-state needs for the hover
  // halo; stations.geojson carries no unique property worth promoting
  // (complex_id groups several stations together).
  map.addSource("stations", {
    type: "geojson", data: "data/stations.geojson", generateId: true,
  });
  map.addSource("walksheds", { type: "geojson", data: "data/walksheds.geojson" });

  // Visibility comes from state, not from the default: the choropleth can now
  // start hidden, because a permalink said so.
  const choro = state.choropleth === "none" ? "none" : "visible";
  // Under the labels, so a street or neighborhood name stays readable over an
  // opaque choropleth. Everything added after this without a `before` -- the
  // walksheds, the surface raster, the stations, the hubs -- still goes on top
  // of the labels, which is where those marks belong.
  const before = labelAnchor();
  map.addLayer({
    id: "tracts-fill", type: "fill", source: "tracts",
    layout: { visibility: choro },
    paint: {
      "fill-color": fillColor(),
      "fill-opacity": fillOpacity(),
    },
  }, before);

  map.addLayer({
    id: "tracts-line", type: "line", source: "tracts",
    layout: { visibility: choro },
    paint: {
      "line-color": lineColor(),
      "line-width": 0.4, "line-opacity": lineOpacity(),
    },
  }, before);

  // Hover ring: 2px in the surface color, per the mark spec for overlapping marks.
  map.addLayer({
    id: "tracts-hover", type: "line", source: "tracts",
    paint: {
      "line-color": pal.ink[state.theme].primary,
      "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2, 0],
    },
  }, before);

  // The surface is the one layer that is off by default, so it is not part of
  // the first paint -- unless a permalink asked for it, in which case the
  // reader is looking at it and it is not optional.
  if (state.surface) addSurface();
  else map.once("idle", deferSurface);

  const sheds = shedCls();
  stats.thresholds_min.forEach((t, i) => {
    map.addLayer({
      id: `shed-${t}`, type: "line", source: "walksheds",
      filter: ["==", ["get", "threshold_min"], t],
      layout: {
        visibility: state.walksheds ? "visible" : "none",
        "line-join": "round",
      },
      paint: { "line-color": sheds[i], "line-width": [2, 1.6, 1.2][i], "line-opacity": 0.95 },
    });
  });

  // The frequent bus network. Deliberately the quietest mark on the map: it is
  // context for the rail question, not a second answer to it, and 2,368 dots
  // drawn boldly would read as more important than the 557 stations. Loaded
  // lazily for the same reason the surface is -- it is off by default, so a
  // reader who never asks for it should not pay 47 KB for it.
  if (state.busFtn) addBusFtn();
  if (state.siting) addSiting();

  // Stations carry identity by SIZE and RING, not hue: extra hues here would
  // collide with the choropleth ramps underneath.
  map.addLayer({
    id: "stations-rail", type: "circle", source: "stations",
    filter: ["in", ["get", "mode"], ["literal", ["lirr", "mnr"]]],
    layout: { visibility: state.stations ? "visible" : "none" },
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 3.2, 13, 6.5],
      "circle-color": st.stroke, "circle-stroke-color": st.fill,
      "circle-stroke-width": 1.6,
    },
  });
  map.addLayer({
    id: "stations-subway", type: "circle", source: "stations",
    filter: ["in", ["get", "mode"], ["literal", ["subway", "sir"]]],
    layout: { visibility: state.stations ? "visible" : "none" },
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.2, 13, 4.6],
      "circle-color": st.fill, "circle-stroke-color": st.stroke,
      "circle-stroke-width": 1.2,
    },
  });

  // Hit target. MapLibre has no hit padding, so a 2 px dot is close to
  // unhoverable; an invisible wider circle beneath both visible layers is the
  // fix. It is also the hover halo, which is the only reason it carries a
  // color. The radius tracks roughly twice the drawn one rather than being a
  // generous fixed pad: Manhattan subway stops are ~15 px apart at the opening
  // zoom, so anything larger would swallow every tract hover on the island.
  map.addLayer({
    id: "stations-hit", type: "circle", source: "stations",
    layout: { visibility: state.stations ? "visible" : "none" },
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 4, 13, 8.5],
      "circle-color": pal.ink[state.theme].primary,
      "circle-opacity": ["case",
        ["boolean", ["feature-state", "hover"], false], 0.16, 0],
    },
  }, "stations-rail");

  addHubMarks(st);
}

// The job centers, drawn where they are. A layer that colors every tract by
// "minutes to a job center" and never says where the center *is* asks the
// reader to hold eight coordinates in their head, and the nearest-center mode
// is worse: the field is a minimum over eight places none of which is named.
//
// Drawn as an open ring rather than a filled dot so it cannot be mistaken for
// a station -- the stations layer is small filled circles and the hubs are a
// subset of it, so two filled dots at the same point would read as one. Two
// layers because MapLibre circles take a single stroke and this needs the
// 2 px surface separation the mark specs ask for between overlapping marks.
function addHubMarks(st) {
  const list = hubs?.hubs ?? stats.accessibility?.hubs ?? [];
  if (!list.length) return;
  map.addSource("hubpoints", {
    type: "geojson",
    data: {
      type: "FeatureCollection",
      features: list.map((h, i) => ({
        type: "Feature",
        properties: { i, name: h.name, borough: h.borough },
        geometry: { type: "Point", coordinates: [h.lon, h.lat] },
      })),
    },
  });
  const p = hubPaint(st);
  const vis = state.choropleth === "hub" ? "visible" : "none";
  map.addLayer({ id: "hub-ring", type: "circle", source: "hubpoints",
    layout: { visibility: vis }, paint: p.ring });
  map.addLayer({ id: "hub-core", type: "circle", source: "hubpoints",
    layout: { visibility: vis }, paint: p.core });
}

// The selected center is larger and opaque; the others stay visible but
// recede, because "which of these is the one I picked" is the question and
// hiding the rest would lose the fact that the layer has eight. With no
// selection -- the nearest-center mode -- nothing matches `sel`, so all eight
// draw at the same weight, which is exactly what that mode means.
//
// One function for both the initial add and every later selection change,
// because the paint depends on `state.hub` and two copies of these
// expressions would drift the first time one of them was edited.
function hubPaint(st) {
  const sel = state.hub;
  const pick = (on, off) => ["case", ["==", ["get", "i"], sel], on, off];
  const dim = (on, off) => (sel < 0 ? on : pick(on, off));
  return {
    ring: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"],
        9, pick(10, 7), 13, pick(17, 12)],
      "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-color": st.stroke,
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"],
        9, pick(3, 2), 13, pick(4.5, 2.5)],
      "circle-stroke-opacity": dim(0.95, 0.4),
    },
    core: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"],
        9, pick(4, 2.6), 13, pick(6.5, 4)],
      "circle-color": st.stroke,
      "circle-stroke-color": st.fill,
      "circle-stroke-width": 2,
      "circle-opacity": dim(1, 0.4),
      "circle-stroke-opacity": dim(1, 0.4),
    },
  };
}

// Visibility and emphasis, for a layer switch or a change of center.
function syncHubMarks() {
  if (!map.getLayer("hub-ring")) return;
  const vis = state.choropleth === "hub" ? "visible" : "none";
  const p = hubPaint(pal.station[state.theme]);
  for (const [id, paint] of [["hub-ring", p.ring], ["hub-core", p.core]]) {
    map.setLayoutProperty(id, "visibility", vis);
    for (const [k, v] of Object.entries(paint)) map.setPaintProperty(id, k, v);
  }
}

// --- hover ------------------------------------------------------------------

function wireHover() {
  const tip = $("#tooltip");
  map.getContainer().appendChild(tip);
  const canvas = map.getCanvas();

  const row = (k, v) =>
    `<div class="trow"><span class="tk">${k}</span><span class="tv">${v}</span></div>`;

  const setTract = (id) => {
    if (state.hovered === id) return;
    if (state.hovered != null)
      map.setFeatureState({ source: "tracts", id: state.hovered }, { hover: false });
    state.hovered = id;
    if (id != null) map.setFeatureState({ source: "tracts", id }, { hover: true });
  };

  const setStation = (id) => {
    if (state.hoveredStation === id) return;
    if (state.hoveredStation != null)
      map.setFeatureState(
        { source: "stations", id: state.hoveredStation }, { hover: false });
    state.hoveredStation = id;
    if (id != null) map.setFeatureState({ source: "stations", id }, { hover: true });
  };

  const clear = () => {
    setTract(null);
    setStation(null);
    highlightLegend(null);
    tip.style.display = "none";
    canvas.style.cursor = "";
  };

  const place = (point) => {
    tip.style.display = "block";
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    const box = canvas.getBoundingClientRect();
    let x = point.x + pad, y = point.y + pad;
    if (x + w > box.width - 8) x = point.x - w - pad;
    if (y + h > box.height - 8) y = point.y - h - pad;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${Math.max(8, y)}px`;
  };

  const stationHTML = (p) => {
    const routes = (p.routes || "").trim();
    const ada = p.ada === true || p.ada === "true";
    return `<div class="tname">${esc(p.name)}</div>`
      + `<div class="tk" style="margin-bottom:5px">${esc(p.mode_label || "")}`
      + (routes ? ` &middot; ${esc(routes)}` : "") + `</div>`
      + (p.borough ? row("Borough", esc(p.borough)) : "")
      + `<div class="tnote">${ada ? "Step-free access" : "No step-free access"}</div>`;
  };

  const tractHTML = (p) => {
    const L = stats.lookups;
    const name = L.nta[p.nta] || "Unknown area";
    const boro = L.boro[p.boro] || "";
    const stn = L.st[p.st] || "";
    const routes = L.sroutes[p.sroutes] || "";
    const mode = L.smode[p.smode] || "";
    const noacc = p.noacc === true || p.noacc === "true";

    let html = `<div class="tname">${esc(name)}</div>`
      + `<div class="tk" style="margin-bottom:5px">${esc(boro)}</div>`;
    html += row("Population", fmt(p.pop));
    html += row("Density", `${fmt(p.dens)} /km²`);
    if (noacc) {
      html += `<div class="tnote">No station reachable on foot (ferry-only island).</div>`;
    } else {
      html += row("Walk to station", `${fmt(p.walk, 1)} min`);
      if (p.nveh != null) html += row("No car, households", `${fmt(p.nveh, 0)}%`);
      if (p.pov != null) html += row("Below the poverty line", `${fmt(p.pov, 0)}%`);
      // One line rather than four rows. These are context for who lives
      // there, not inputs to anything on the map, and four more labeled rows
      // would push the walk figures off the top of a phone tooltip.
      const who = [
        p.tcom != null ? `${fmt(p.tcom, 0)}% commute by transit` : null,
        p.dis != null ? `${fmt(p.dis, 0)}% with a disability` : null,
        p.u18 != null ? `${fmt(p.u18, 0)}% under 18` : null,
        p.o65 != null ? `${fmt(p.o65, 0)}% over 65` : null,
      ].filter(Boolean);
      if (who.length)
        html += `<div class="tk" style="margin:3px 0 5px">${who.join(" · ")}</div>`;
      const q = needQuadrant(p);
      if (q >= 0)
        html += `<div class="tnote">${esc(NEED_QUADRANTS[q])}, against the
                 citywide medians — the two shares above crossed.</div>`;
      // Said in the tooltip rather than drawn on the map, because the flag is
      // about precision and not about the quantity: a tract at 2% poverty
      // ±1.5 points is over the threshold and still a low-poverty tract. It
      // matters where a reader is about to rank areas by these shares.
      if (p.moeflag === true || p.moeflag === "true")
        html += `<div class="tnote">These two estimates are imprecise for an
                 area this small (survey margin over 30% of the estimate).</div>`;
      if (p.wlo != null && p.whi != null)
        html += row("Range in area",`${fmt(p.wlo, 1)}–${fmt(p.whi, 1)} min`);
      // Always shown, not only on the access layer. The wait is a property of
      // the tract, not of whichever fill happens to be selected, and hiding it
      // behind a layer choice is how a reader ends up quoting the walk figure
      // as if it were the trip.
      if (p.acc != null) {
        html += row("+ wait for a train", `${fmt(p.wait, 1)} min`);
        html += row("Access cost", `${fmt(p.acc, 1)} min`);
      }
      if (stn) {
        const label = mode === "lirr" ? "LIRR" : mode === "mnr" ? "Metro-North"
          : mode === "sir" ? "SIR" : "Subway";
        html += `<div class="tnote">Nearest: <strong>${esc(stn)}</strong>`
          + (routes ? ` (${esc(routes)})` : "") + ` · ${label}</div>`;
        // Every access figure on this map counts a commuter-rail ticket as
        // equivalent to a subway swipe. It is not: LIRR and Metro-North charge
        // a separate zone-based fare no unlimited MetroCard covers. For the
        // 10.5% of New Yorkers whose nearest train is one of these, saying so
        // is the difference between a measurement and a claim.
        if (mode === "lirr" || mode === "mnr") {
          const sw = p.swalk;
          html += `<div class="tnote">Separate fare — not covered by a subway`
            + ` swipe.` + (sw != null
              ? ` Nearest subway is ${fmt(sw, 1)} min.` : "") + `</div>`;
        }
      }
      // Shown on every layer, not only the two Phase 4 ones, for the same
      // reason the wait is: these are properties of the tract rather than of
      // whichever fill happens to be selected.
      if (p.j45 != null) {
        const total = stats.accessibility?.jobs_total ?? 0;
        html += row(`Jobs in ${fmt(stats.accessibility?.budget_min, 0)} min`,
                    `${fmt(p.j45 * total / 100 / 1e6, 2)}M`);
      }
      // Beside the count, not instead of it: the count answers "how much is
      // near me" and this answers "how far to enough", and the second one is
      // the one that still separates two badly served tracts.
      if (p.jt10 != null)
        html += row("To a tenth of the city's jobs", `${fmt(p.jt10, 0)} min`);
      else if (p.j45 != null)
        html += `<div class="tnote">Never reaches a tenth of the city's
                 jobs.</div>`;
      // Shown with the walk it is paid on top of, because the number alone
      // reads as a walk time rather than as a penalty. Deliberately after
      // the `else if` above and not between the two: inserting a statement
      // there silently re-parents the else to the new `if`.
      if (p.bike != null)
        html += row("By bike to a station", `${fmt(p.bike, 1)} min`);
      if (p.sf != null && p.walk != null)
        html += row("To a step-free station",
                    `${fmt(p.sf, 1)} min (+${fmt(Math.max(p.sf - p.walk, 0), 1)})`);
      else if (p.walk != null)
        html += `<div class="tnote">No step-free station reachable on
                 foot.</div>`;
      const hv = p[hubKey()];
      const hubName = state.hub < 0 ? "nearest job center"
        : (stats.accessibility?.hubs?.[state.hub]?.name ?? "job center");
      if (hv != null) html += row(`To ${hubName}`, `${fmt(hv, 0)} min`);
      else if (p.j45 != null)
        html += `<div class="tnote">No train or ferry path to a job
                 center.</div>`;
      // The detour factor has no layer of its own: measured, it is nearly
      // flat -- the middle 80% of tracts run 0.79 to 1.28, against a full
      // range of 0.61 to 1.97 -- so a choropleth of it is close to one color.
      // That flatness is the finding, and it belongs in a sentence rather
      // than in a legend.
      if (p.det != null)
        html += row("Detour factor", `${fmt(p.det, 2)}\u00d7 typical`);
      // The block, on every layer and not only the blocks one, because it is
      // a property of the tract. A tract past the standard on its own says
      // something different from the same tract inside a 430-tract band, and
      // that difference is the whole argument for having this measure.
      if (p.cluid != null && p.cluid >= 0)
        html += row("Under-served zone",
                    p.clu === 1 ? "on its own"
                      : `${fmt(p.clu)} areas · ${fmt(p.clupop)} people`);

      // The access measure often prefers a further station with more trains.
      // Naming it only when it differs keeps the common case quiet and makes
      // the interesting case self-explanatory.
      const served = L.sst ? (L.sst[p.sst] || "") : "";
      if (served && served !== stn) {
        html += `<div class="tnote">Fastest overall: <strong>`
          + `${esc(served)}</strong>`
          + (p.stph != null ? ` · ${fmt(p.stph, 1)} trains/hr` : "")
          + `</div>`;
      }
    }
    // Says what the fill means, so the color does not have to be matched
    // against the legend by eye. The gap layer shows its decomposition
    // instead: a score nobody can interrogate persuades nobody.
    if (state.choropleth === "clump") {
      html += p.cluid != null && p.cluid >= 0
        ? `<div class="tclass">Zone ${p.cluid + 1} of `
          + `${fmt(stats.clusters?.n_clusters)}, by population</div>`
        : `<div class="tclass">Within the ${fmt(floorMin(), 0)}-minute `
          + `standard — not in a zone</div>`;
    }
    if (state.choropleth === "gap" && p.rd != null) {
      const s = gapScore(p, state.gapWeight);
      const fl = floorMin();
      if (s <= 0) {
        html += `<div class="tclass">Within the ${fmt(fl, 0)}-minute standard —`
          + ` no gap to measure</div>`;
      } else {
        // The decomposition, in the score's own terms. A score nobody can
        // interrogate persuades nobody, and "92nd percentile" is not an
        // interrogation -- it says where a tract sits without saying why.
        html += `<div class="tclass"><strong>${fmt(s / 1000, 0)}k excess`
          + ` person-minutes</strong><br>${fmt(p.pop)} residents × `
          + `${fmt(p.exc, 1)} min past ${fmt(fl, 0)}`
          + (state.gapWeight
            ? ` × ${fmt(needBlend(p, state.gapWeight), 2)} equity weight` : "")
          + `<br>worse than ${Math.round(100 * gapPercentile(s))}% of areas`
          + `</div>`;
      }
      // Contiguity: the honest headline is not the worst tract but the largest
      // run of them. One bad tract ringed by good ones is a bus-stop problem.
      if (p.clu > 1) {
        html += `<div class="tnote">In an under-served zone of <strong>`
          + `${fmt(p.clu)} areas</strong> past the standard, `
          + `${fmt(p.clupop)} people.</div>`;
      } else if (p.clu === 1) {
        html += `<div class="tnote">Isolated — every neighboring area is`
          + ` inside the standard.</div>`;
      }
    } else if (state.choropleth === "access" && accessClass(p) >= 0) {
      html += `<div class="tclass">Access cost `
        + `<strong>${CLS_ACCESS[accessClass(p)]}</strong> of areas</div>`;
    } else if (p.dc >= 0 && p.wc >= 0) {
      html += `<div class="tclass">Density <strong>${CLS_DENSITY[p.dc]}</strong>`
        + ` · Walk <strong>${CLS_WALK[p.wc]}</strong></div>`;
    }
    if (p.pop === 0)
      html += `<div class="tnote">No residents — excluded from the statistics.</div>`;
    else if (isBelowFloor(p)) {
      const by = fadedBy(p);
      const list = by.length === 1 ? by[0]
        : `${by.slice(0, -1).join(", ")} and ${by[by.length - 1]}`;
      html += `<div class="tnote">Hidden by ${list} — still counted.</div>`;
    }
    return html;
  };

  // One handler for the whole map rather than one per layer. The station dots
  // are drawn above the choropleth, so both handlers would fire and stack two
  // tooltips; querying the station hit layer first and returning makes the
  // precedence explicit instead of leaving it to dispatch order.
  map.on("mousemove", (e) => {
    const stn = query("stations-hit", e.point);
    if (stn.length) {
      setTract(null);
      highlightLegend(null);
      setStation(stn[0].id);
      tip.innerHTML = stationHTML(stn[0].properties);
      canvas.style.cursor = "pointer";
      place(e.point);
      return;
    }
    setStation(null);
    canvas.style.cursor = "";

    const f = query("tracts-fill", e.point)[0];
    if (!f) return clear();
    setTract(f.id);
    tip.innerHTML = tractHTML(f.properties);
    highlightLegend(f.properties);
    place(e.point);
  });

  canvas.addEventListener("mouseout", clear);
}

// Which gap band a tract falls in, or -1 if it has no score.
function gapBand(p) {
  if (p.rd == null) return -1;
  const s = gapScore(p, state.gapWeight);
  const { breaks } = gapCompute(state.gapWeight);
  let i = 0;
  while (i < breaks.length && s >= breaks[i]) i++;
  return i;
}

// What each cell of the matrix is, indexed by `bi` = density class * 3 + walk
// class. Two of the nine have names worth saying out loud: cell 8 is the
// finding the map exists for, and cell 0 is its dual -- the one the roadmap
// calls "where should the density go", which was computed and painted and
// nameless until now.
const BI_CELLS = [
  "few people, short walk", "few people, middling walk",
  "few people, long walk",
  "middling density, short walk", "middling density, middling walk",
  "middling density, long walk",
  "dense, short walk", "dense, middling walk", "dense and far from a train",
];

// Clicking a cell isolates it. Delegated from the legend body, because the
// grid is rebuilt on every render and a handler per swatch would leak.
function wireLegendClicks() {
  const el = $("#legendbody");
  if (!el || el.dataset.wired) return;
  el.dataset.wired = "1";
  el.addEventListener("click", (e) => {
    const cell = e.target.closest?.("i[data-bi]");
    if (!cell || state.choropleth !== "bivariate") return;
    const v = Number(cell.getAttribute("data-bi"));
    state.biCell = state.biCell === v ? -1 : v;
    map.setPaintProperty("tracts-fill", "fill-opacity", fillOpacity());
    map.setPaintProperty("tracts-line", "line-opacity", lineOpacity());
    map.setPaintProperty("tracts-line", "line-color", lineColor());
    renderLegend();
    showCount();
    syncHash();
  });
}

// Outlines the legend cell the hovered feature falls in, which teaches the
// legend rather than replacing it. The cells carry data-bi / data-cls for this.
function highlightLegend(p) {
  const prev = $("#legendbody .on");
  if (prev) prev.classList.remove("on");
  if (!p) return;
  const bivariate = state.choropleth === "bivariate";
  const key = bivariate ? p.bi
    : state.choropleth === "density" ? p.dc
      : state.choropleth === "walk" ? p.wc
        : state.choropleth === "access" ? accessClass(p)
          : state.choropleth === "gap" ? gapBand(p) : -1;
  if (key == null || key < 0) return;
  const cell = $(`#legendbody i[${bivariate ? "data-bi" : "data-cls"}="${key}"]`);
  if (cell) cell.classList.add("on");
}

// --- legend -----------------------------------------------------------------

function renderLegend() {
  const el = $("#legendbody");
  const b = stats.breaks;
  // True of every one-variable ramp, which is one palette in both themes. Not
  // of the bivariate matrix, which has a dark variant whose worst corner is
  // the brightest cell, so the note would contradict the swatches beside it.
  $("#darkernote").hidden =
    state.choropleth === "bivariate" || state.choropleth === "none";

  if (state.choropleth === "none") {
    el.innerHTML = `<div class="sub">Area colors are off.</div>`;
    return;
  }

  if (state.choropleth === "bivariate") {
    const c = state.theme === "dark" ? pal.bivariate_dark : pal.bivariate;
    // Rows top-to-bottom = high density to low, so the grid reads like a map key.
    // data-bi is the `bi` value a feature in this cell carries, so the tooltip
    // can outline it: bi = density class * 3 + walk class. The rows are emitted
    // highest density first, which is why the row index is not the class.
    const cells = [2, 1, 0].map((i) =>
      c[i].map((hex, j) =>
        `<i data-bi="${i * 3 + j}" style="background:${hex}"></i>`).join("")).join("");
    const sel = state.biCell >= 0 ? BI_CELLS[state.biCell] : null;
    el.innerHTML = `
      <div class="bivwrap">
        <div class="bivaxis-y">Density <span class="arrow">→</span></div>
        <div>
          <div class="bivgrid">${cells}</div>
          <div class="bivaxis-x">Walk time to station <span class="arrow">→</span></div>
        </div>
      </div>
      <div class="sub" style="margin-top:9px">
        The top-right corner is <strong>dense and far from a train</strong>:
        over ${fmt(b.density[1])} people/km² and over
        ${fmt(b.walk_min[1], 1)} min walk. The bottom-left is the opposite
        and is worth a name of its own —
        <strong>few people, short walk</strong>: land near transit the city
        has already built, which is where housing could go without a new
        line.
      </div>
      <div class="sub" style="margin-top:9px">${sel
        ? `Showing <strong>${esc(sel)}</strong> only. Click it again to show
           every cell.`
        : "Click a cell to show only the areas in it."}</div>`;
    return;
  }

  if (state.choropleth === "gap") {
    const ramp = gapRamp();
    const { breaks, over } = gapCompute(state.gapWeight);
    const fl = floorMin();
    // Labels are the score in its own units -- thousands of excess
    // person-minutes -- because the score HAS units now. A percentile would
    // throw away the one property that makes this measure better than the
    // rank product it replaced.
    const k = (v) => v >= 1000 ? `${fmt(v / 1000, 0)}k` : fmt(v, 0);
    const rows = [];
    const n = gapBands();
    for (let i = n - 1; i >= 1; i--) {
      const lo = breaks[i - 1], hi = breaks[i];
      rows.push(`<div class="ramprow"><i data-cls="${i}" style="background:${
        ramp[i]}"></i><span class="lab">${
        i === n - 1 ? `over ${k(lo)}` : `${k(lo)} – ${k(hi)}`
      }</span></div>`);
    }
    el.innerHTML = rows.join("")
      + `<div class="ramprow"><i data-cls="0" style="background:${ramp[0]}"></i>
         <span class="lab">within ${fmt(fl, 0)} min — no gap</span></div>`
      + `<div class="ramprow"><i style="background:${unpop()}"></i>
         <span class="lab">no residents</span></div>`
      + `<div class="sub" style="margin-top:9px"><strong>Excess
         person-minutes</strong>: residents &times; minutes of walk beyond
         ${fmt(fl, 0)} minutes. ${fmt(over.length)} areas are past that
         mark.</div>`;
    return;
  }

  if (state.choropleth === "clump") {
    const brk = seqBreaks("clupop", true);
    const ramp = seqRamp("walk");
    // Read from stats.json rather than recounted here. The browser has the
    // `cluid` and `clu` columns and could group them itself, but then the
    // block count would have two definitions -- aggregate.add_clusters and
    // this loop -- and the one that drifts is always the copy.
    const cl = stats.clusters ?? {};
    const big = cl.largest?.[0] ?? {};
    const k = (v) => v >= 1e6 ? `${fmt(v / 1e6, 2)}M` : `${fmt(v / 1e3, 0)}k`;
    const rows = ramp.slice(0, brk.length + 1).map((hex, i) => {
      // "and up" rather than "over": the top break is the largest block's own
      // population, because 430 of the 923 tracts share that one value, so a
      // band labeled "over" would describe a set whose only member sits
      // exactly on the boundary.
      const lab = i === 0 ? `under ${k(brk[0])}`
        : i === brk.length ? `${k(brk[i - 1])} and up`
        : `${k(brk[i - 1])} – ${k(brk[i])}`;
      return `<div class="ramprow"><i data-cls="${i}"
              style="background:${hex}"></i>
              <span class="lab">${lab} in the zone</span></div>`;
    });
    el.innerHTML = rows.join("")
      + `<div class="ramprow"><i style="background:${unpop()}"></i>
         <span class="lab">no gap to measure</span></div>`
      + `<div class="sub" style="margin-top:9px">${fmt(cl.n_tracts_beyond)}
         areas are past the ${fmt(stats.score?.floor_minutes ?? 10, 0)}-minute
         standard, in ${fmt(cl.n_clusters)} zones. The largest
         holds ${fmt(big.n_tracts)} areas and ${fmt(big.pop)} people
         (${esc((big.where ?? []).join(", "))}).
         ${fmt(cl.n_single_tract_clusters)} zones are a single area.</div>`;
    return;
  }

  if (state.choropleth === "jobs" || state.choropleth === "hub"
      || state.choropleth === "jobtime") {
    const jobs = state.choropleth === "jobs";
    const jt = state.choropleth === "jobtime";
    const key = jobs ? "j45" : jt ? "jt10" : hubKey();
    const ramp = jobs ? seqRamp("density").slice().reverse() : seqRamp("walk");
    const brk = seqBreaks(key);
    const total = stats.accessibility?.jobs_total ?? 0;
    // Shown as a count rather than the shipped percentage: "1.2M jobs" is a
    // quantity a reader has intuitions about and "27.9%" is not. The
    // percentage is what travels on the wire because it is four characters
    // instead of seven; the conversion belongs here.
    const k = jobs
      ? (v) => `${fmt(v * total / 100 / 1e6, 2)}M`
      : (v) => `${fmt(v, 0)} min`;
    const rows = ramp.slice(0, brk.length + 1).map((hex, i) => {
      const lab = i === 0 ? `under ${k(brk[0])}`
        : i === brk.length ? `over ${k(brk[i - 1])}`
        : `${k(brk[i - 1])} – ${k(brk[i])}`;
      return `<div class="ramprow"><i data-cls="${i}"
              style="background:${hex}"></i>
              <span class="lab">${lab}</span></div>`;
    });
    const hubName = state.hub < 0 ? null
      : (stats.accessibility?.hubs?.[state.hub]?.name ?? null);
    el.innerHTML = rows.join("")
      + `<div class="ramprow"><i style="background:${unpop()}"></i>
         <span class="lab">${jobs ? "no residents"
           : jt ? "never reaches that many"
           : "no path to a center"}</span></div>`
      + `<div class="sub" style="margin-top:9px">` + (jt
        ? `Minutes to reach
           <strong>${fmt(stats.accessibility?.threshold_jobs)} jobs</strong>,
           a tenth of the city's, by train, ferry and on foot. The median is
           ${fmt(stats.accessibility?.median_jobs_time, 0)} minutes; the
           slowest tenth of areas take over
           ${fmt(stats.accessibility?.p90_jobs_time, 0)}.`
        : jobs
        ? `Jobs reachable in <strong>${fmt(stats.accessibility?.budget_min, 0)}
           minutes</strong> by train, the Staten Island Ferry and on foot, out
           of ${fmt(total / 1e6, 2)}M in the city. Buses are not included.
           ${fmt(stats.accessibility?.n_past_budget)} areas reach less than a
           tenth of the city's jobs in that time, so they bunch near zero;
           &ldquo;Time to reach 10% of jobs&rdquo; separates them.`
        : `Minutes to ${hubName ? `<strong>${esc(hubName)}</strong>`
           : "the nearest of the eight job centers"} by train or ferry,
           including the walk at both ends and the wait. The rings mark the
           centers themselves${hubName ? ", with the selected one emphasized"
           : " \u2014 all eight, since this is the nearest of them"}.`)
      + `</div>`
      + `<div class="sub" style="margin-top:6px">Not part of the
         under-service score. Tested against the corridors agencies have
         proposed, it scores
         ${fmt(100 * (stats.alignment?.precision?.[jobs
           ? "pop x jobs out of reach" : jt ? "pop x excess time to jobs"
           : "pop x excess hub min"]?.["25"] ?? 0),
           0)}%, against ${fmt(100 * (stats.alignment?.baseline ?? 0), 0)}% for
         random and ${fmt(100 * (stats.alignment?.precision?.[
           stats.alignment?.shipped]?.["25"] ?? 0), 0)}% for the shipped
         score.</div>`;
    return;
  }

  if (state.choropleth === "bike") {
    const bk = stats.bike ?? {};
    const ramp = seqRamp("walk");
    const brk = seqBreaks("bike");
    const k = (v) => `${fmt(v, 0)} min`;
    const rows = ramp.slice(0, brk.length + 1).map((hex, i) => {
      const lab = i === 0 ? `under ${k(brk[0])}`
        : i === brk.length ? `over ${k(brk[i - 1])}`
        : `${k(brk[i - 1])} – ${k(brk[i])}`;
      return `<div class="ramprow"><i data-cls="${i}"
              style="background:${hex}"></i>
              <span class="lab">${lab}</span></div>`;
    });
    const worst = bk.bands?.[bk.bands.length - 1];
    el.innerHTML = rows.join("")
      + `<div class="ramprow"><i style="background:${unpop()}"></i>
         <span class="lab">no station reachable</span></div>`
      + `<div class="sub" style="margin-top:9px">Riding to the nearest
         station instead of walking, on the cycling network. It closes the
         middle of the gap and not the end of it: of the
         ${fmt(bk.pop_beyond_walking)} people more than
         ${fmt(bk.floor_minutes, 0)} minutes' walk from a train,
         <strong>${fmt(bk.pct_rescued, 0)}%</strong> are inside that by
         bike — but in the areas over
         ${fmt(worst?.walk_from, 0)} minutes' walk, only
         <strong>${fmt(100 * (worst?.pop_inside_by_bike ?? 0)
           / (worst?.pop || 1), 1)}%</strong> are.</div>`
      + `<div class="sub" style="margin-top:6px">Every street, not the
         protected network only, so read it as the best case. It also
         assumes somewhere to leave the bike, which at outer stations is the
         real constraint and is in none of this data. Not part of the
         under-service score.</div>`;
    return;
  }

  if (state.choropleth === "stepfree") {
    const sf = stats.step_free ?? {};
    const ramp = seqRamp("walk");
    const brk = seqBreaks("sf");
    const k = (v) => `${fmt(v, 0)} min`;
    const rows = ramp.slice(0, brk.length + 1).map((hex, i) => {
      const lab = i === 0 ? `under ${k(brk[0])}`
        : i === brk.length ? `over ${k(brk[i - 1])}`
        : `${k(brk[i - 1])} – ${k(brk[i])}`;
      return `<div class="ramprow"><i data-cls="${i}"
              style="background:${hex}"></i>
              <span class="lab">${lab}</span></div>`;
    });
    el.innerHTML = rows.join("")
      + `<div class="ramprow"><i style="background:${unpop()}"></i>
         <span class="lab">none reachable on foot</span></div>`
      + `<div class="sub" style="margin-top:9px">Walking time to the nearest
         of the <strong>${fmt(sf.n_step_free)} of ${fmt(sf.n_stations)}</strong>
         stations with a step-free route, against
         ${fmt(sf.median_walk_min, 1)} minutes to the nearest station of any
         kind. ${fmt(sf.pct_beyond_standard, 0)}% of New Yorkers live more
         than ${fmt(stats.score?.floor_minutes ?? 10, 0)} minutes from a
         station; <strong>${fmt(sf.pct_beyond_standard_step_free, 0)}%</strong>
         do if it has to be step-free.</div>`
      + `<div class="sub" style="margin-top:6px">Step-free
         <em>by design</em>, as of ${esc(sf.as_of ?? "")} — a route that was
         built, not one working today. Elevators go out of service and that is
         not in this map. Not part of the under-service score: a missing
         elevator is answered by an elevator, not by a new rail line.</div>`;
    return;
  }

  const isDens = state.choropleth === "density";
  const isAcc = state.choropleth === "access";
  const ramp = isDens ? densCls() : walkCls();
  const brk = isDens ? b.density : isAcc ? b.access_min : b.walk_min;
  const unit = isDens ? " /km²" : " min";
  const dec = isDens ? 0 : 1;
  const labels = [
    `under ${fmt(brk[0], dec)}${unit}`,
    `${fmt(brk[0], dec)} – ${fmt(brk[1], dec)}${unit}`,
    `over ${fmt(brk[1], dec)}${unit}`,
  ];
  el.innerHTML = ramp.map((hex, i) =>
    `<div class="ramprow"><i data-cls="${i}" style="background:${hex}"></i>
     <span class="lab">${labels[i]}</span></div>`).join("")
    + `<div class="ramprow"><i style="background:${unpop()}"></i>
       <span class="lab">no residents</span></div>`
    + (isAcc ? `<div class="sub" style="margin-top:9px">Walking time
       <strong>plus the expected wait</strong>: half the scheduled 8–9am
       headway, capped at 15 min. Published &ldquo;within N minutes&rdquo;
       figures are walk-only, so they are not comparable.</div>` : "");
}

function renderKeys() {
  const sheds = shedCls();
  const st = pal.station[state.theme];
  $("#keys").innerHTML =
    stats.thresholds_min.map((t, i) =>
      `<div class="keyline"><span class="sw" style="border-top-color:${sheds[i]};
        border-top-width:${[2, 1.6, 1.2][i]}px"></span>
        <span class="lab">${t}-min walkshed</span></div>`).join("")
    + `<div class="keyline"><span class="dot" style="background:${st.fill};
        border-color:${st.stroke}"></span><span class="lab">Subway / SIR station</span></div>`
    + `<div class="keyline"><span class="dot" style="background:${st.stroke};
        border-color:${st.fill};width:12px;height:12px"></span>
        <span class="lab">LIRR / Metro-North</span></div>`
    + (state.siting && stats.siting
      ? `<div class="keyline"><span class="dot" style="background:transparent;
          border-color:${st.stroke};width:11px;height:11px"></span>
         <span class="lab">One more station here — hypothetical, sized by
         the excess walking it would remove. Not a proposal.</span></div>` : "")
    + (state.busFtn && stats.bus_ftn
      ? `<div class="keyline"><span class="dot" style="background:${
          pal.ink[state.theme].secondary};border-color:transparent;
          width:6px;height:6px;opacity:0.55"></span>
         <span class="lab">Frequent bus stop (${fmt(stats.bus_ftn.n_ftn)} of
         ${fmt(stats.bus_ftn.n_stops)})</span></div>` : "");
}

// --- page links + table -----------------------------------------------------

// The document pages are siblings, so the theme has to travel in the URL or a
// reader who picked dark mode gets flashed. It goes in the query string
// rather than the hash, which leaves the hash free to be a section anchor.
// Every cross-page link carries `data-page`, so one pass finds them all.
function syncPageLinks() {
  const q = state.themePinned ? `?t=${state.theme}` : "";
  for (const a of document.querySelectorAll("a[data-page]"))
    a.href = `${a.dataset.page}${q}`;
}

// Only the table that moves with the weight slider lives here now. Ranking
// the worst tracts by the function the map is painting with is something a
// static page cannot do; the borough, block and corridor tables are not, so
// they are on stats.html and about.html and are not duplicated here.
function renderTable() {
  // Ranked live by the score the map is painting, not by a list baked at
  // build time. The old table picked its rows another way -- top density
  // tercile, then the longest walks -- so the map and the table disagreed, and
  // nine tracts scored worse than the worst row in it. Computing both from one
  // function is what makes that impossible rather than merely fixed.
  const L = stats.lookups;
  const worst = tracts.features
    .filter((f) => f.properties.rd != null && gapScore(f.properties, state.gapWeight) > 0)
    .map((f) => ({ p: f.properties, s: gapScore(f.properties, state.gapWeight) }))
    .sort((a, b) => b.s - a.s)
    .slice(0, 20);
  const des = worst.map(({ p, s }) =>
    `<tr><td>${esc(L.nta[p.nta] ?? "")}</td><td>${esc(L.boro[p.boro] ?? "")}</td>
     <td class="num">${fmt(p.pop)}</td><td class="num">${fmt(p.walk, 1)}</td>
     <td class="num">${fmt(s / 1000, 0)}k</td>
     <td class="num">${p.clu > 1 ? fmt(p.clu) : "—"}</td></tr>`).join("");

  $("#tables").innerHTML = `
    <table>
      <caption>The 20 areas with the highest under-service score, at the
        current equity weight. &ldquo;Zone&rdquo; is the number of
        neighboring areas past the ${fmt(floorMin(), 0)}-minute standard in
        the under-served zone this one belongs to.</caption>
      <thead><tr><th>Neighborhood</th><th>Borough</th><th class="num">Population</th>
        <th class="num">Walk (min)</th><th class="num">Excess person-min</th>
        <th class="num">Zone</th></tr></thead>
      <tbody>${des}</tbody>
    </table>
    <p class="sub">Borough figures are on the
      <a data-page="stats.html" href="stats.html">stats page</a>, and the
      largest zones on the
      <a data-page="about.html" href="about.html">methodology page</a>.</p>`;
  syncPageLinks();
}

// --- theme / controls -------------------------------------------------------

function repaint() {
  map.setPaintProperty("tracts-fill", "fill-color", fillColor());
  syncHubMarks();
  renderLegend();
  wireLegendClicks();
}

// Each layer's own control or caveat, shown only while that layer is on
// screen: the weight slider is meaningless anywhere but the gap layer, and a
// note explaining a layer the reader is not looking at is noise.
function showLayerNotes() {
  $("#gapweight").hidden = state.choropleth !== "gap";
  for (const [id, layer] of [["clumpnote", "clump"], ["binote", "bivariate"],
                             ["sfnote", "stepfree"],
                             ["bikenote", "bike"]]) {
    const el = $(`#${id}`);
    if (el) el.hidden = state.choropleth !== layer;
  }
}

// This one hangs off a checkbox rather than the layer radio, so it is not in
// showLayerNotes with the others.
function showSitingNote() {
  const el = $("#sitingnote");
  if (el) el.hidden = !state.siting;
}

function showHubPick() {
  const el = $("#hubpick");
  if (el) el.hidden = state.choropleth !== "hub" || !hubs;
}

// The hub picker is built from stats.json rather than hardcoded, because the
// hub list is derived from the jobs data and changes when that data does.
function fillHubPicker() {
  const sel = $("#f-hub");
  const list = stats.accessibility?.hubs ?? [];
  if (!sel || !list.length) return;
  sel.innerHTML = `<option value="-1">Nearest center</option>`
    + list.map((h, i) => `<option value="${i}">${esc(h.name)} — `
      + `${esc(h.borough || h.mode)}</option>`).join("");
  sel.value = String(state.hub);
  sel.addEventListener("change", () => {
    state.hub = Number(sel.value);
    if (state.choropleth === "hub") repaint();
    syncHash();
  });
}

function applyTheme(next) {
  state.theme = next;
  state.themePinned = true;
  syncHash();
  document.documentElement.setAttribute("data-theme", next);
  $("#themebtn").textContent = next === "dark" ? "Light mode" : "Dark mode";
  syncPageLinks();
  // setStyle swaps the basemap wholesale, so every source and layer is re-added
  // once the new style reports ready.
  map.setStyle(BASEMAP[next]);
  map.once("style.load", () => {
    // setStyle discards feature state along with the sources, so a stale id
    // here would make the first hover after a theme switch a silent no-op.
    state.hovered = null;
    state.hoveredStation = null;
    addLayers();
    renderLegend();
    renderKeys();
  });
}

function wireControls() {
  document.querySelectorAll('input[name="choro"]').forEach((r) => {
    r.addEventListener("change", () => {
      state.choropleth = r.value;
      const on = r.value !== "none";
      map.setLayoutProperty("tracts-fill", "visibility", on ? "visible" : "none");
      map.setLayoutProperty("tracts-line", "visibility", on ? "visible" : "none");
      if (on) repaint(); else renderLegend();
      showLayerNotes();
      showHubPick();
      // Leaving the matrix clears the isolated cell: `bi` means nothing on
      // the other eight layers, so a filter left on would fade most of the
      // map with no control on screen explaining it.
      if (r.value !== "bivariate" && state.biCell >= 0) {
        state.biCell = -1;
        map.setPaintProperty("tracts-fill", "fill-opacity", fillOpacity());
        map.setPaintProperty("tracts-line", "line-opacity", lineOpacity());
        map.setPaintProperty("tracts-line", "line-color", lineColor());
        showCount();
      }
      syncHash();
    });
  });

  // `prepare` runs before the visibility flip, for a layer that may not exist
  // yet: the surface is added lazily, and setLayoutProperty throws on an
  // unknown id rather than ignoring it.
  const toggle = (id, key, layers, prepare) =>
    $(id).addEventListener("change", (e) => {
      state[key] = e.target.checked;
      if (prepare) prepare();
      const vis = e.target.checked ? "visible" : "none";
      layers().forEach((l) => {
        if (map.getLayer(l)) map.setLayoutProperty(l, "visibility", vis);
      });
      syncHash();
    });
  toggle("#t-surface", "surface", () => ["surface-img"], addSurface);
  toggle("#t-sheds", "walksheds", () => stats.thresholds_min.map((t) => `shed-${t}`));
  // renderKeys too, because the key carries the stop count and the toggle is
  // the only thing that makes that line appear. prepare() runs after state is
  // assigned, so it sees the new value.
  //
  // No hover tooltip for these, deliberately. The dots are 1.4-3.2 px, so they
  // would need a hit layer several times their size, and 2,368 of those spread
  // over every commercial street would swallow tract hovers city-wide. The key
  // says what they are; the layer is context for the rail question, not a
  // second thing to interrogate.
  toggle("#t-busftn", "busFtn", () => ["bus-ftn"],
    () => { addBusFtn(); renderKeys(); });
  toggle("#t-siting", "siting", () => ["siting-dots"],
    () => { addSiting(); renderKeys(); showSitingNote(); });
  toggle("#t-stations", "stations",
    () => ["stations-subway", "stations-rail", "stations-hit"]);

  // The floors repaint from fields already on every feature: no pipeline round
  // trip, which is the roadmap's "ship metrics, score in the browser" rule.
  const applyFloors = () => {
    map.setPaintProperty("tracts-fill", "fill-opacity", fillOpacity());
    map.setPaintProperty("tracts-line", "line-opacity", lineOpacity());
    // The line COLOR depends on the floors too now, not just its opacity: an
    // excluded tract's outline is the only mark drawing it. Forgetting this
    // leaves a tract that just crossed a floor outlined in the surface color
    // over bare basemap, which is invisible -- the tract would simply vanish.
    map.setPaintProperty("tracts-line", "line-color", lineColor());
    showCount();
  };
  const floor = (id, out, key, label) => {
    const input = $(id), output = $(out);
    const show = () => { output.textContent = label(state[key]); };
    input.addEventListener("input", () => {
      state[key] = Number(input.value);
      show();
      applyFloors();
      syncHash();
    });
    show();
  };
  const served = $("#t-served");
  if (served) served.addEventListener("change", () => {
    state.servedFaded = served.checked;
    applyFloors();
    syncHash();
  });
  floor("#f-dens", "#o-dens", "densFloor", (v) => v ? `${fmt(v)} /km²` : "off");
  floor("#f-pop", "#o-pop", "popFloor", (v) => v ? `${fmt(v)} people` : "off");

  // The weight only repaints: the score lives in the expression, so there is
  // nothing to recompute per feature beyond the quantile breaks.
  const weight = $("#f-weight"), weightOut = $("#o-weight");
  const showWeight = () => {
    const w = state.gapWeight;
    weightOut.textContent = w ? `${Math.round(w * 100)}%` : "off";
  };
  weight.addEventListener("input", () => {
    state.gapWeight = Number(weight.value);
    showWeight();
    if (state.choropleth === "gap") {
      repaint();
      renderLegend();
      // The table ranked by the old score and the map by the new one is the
      // disagreement ROADMAP.md flagged; re-ranking here is what closes it.
      renderTable();
    }
    syncHash();
  });
  showWeight();
  fillHubPicker();

  $("#homebtn").addEventListener("click", () =>
    map.fitBounds(HOME_BOUNDS, fitOptions(650)));

  $("#themebtn").addEventListener("click", () =>
    applyTheme(state.theme === "dark" ? "light" : "dark"));

  const tw = $("#tablewrap");
  $("#tablebtn").addEventListener("click", () => {
    tw.classList.add("open");
    tw.focus();
  });
  $("#closetable").addEventListener("click", () => tw.classList.remove("open"));
  addEventListener("keydown", (e) => {
    if (e.key === "Escape") tw.classList.remove("open");
  });
}

// --- boot -------------------------------------------------------------------

function fail(title, body, detail) {
  $("#loading").innerHTML =
    `<div style="max-width:440px;text-align:center;line-height:1.6">
      <strong>${title}</strong><br>${body}
      ${detail ? `<br><br><span style="color:var(--ink-muted);font-size:11px">${detail}</span>` : ""}
     </div>`;
}

async function main() {
  const j = async (u) => {
    const r = await fetch(u);
    if (!r.ok) throw new Error(`${u}: ${r.status}`);
    return r.json();
  };

  // MapLibre is loaded dynamically so a CDN or module failure can be reported
  // in the page. With a static import, any failure is a module-level error:
  // nothing below ever executes and the loading overlay just sits there.
  //
  // Both requests start here and neither waits on the other, because the data
  // does not depend on the library. Awaiting the import first put 290 KB of
  // JavaScript in front of the 1.7 MB of GeoJSON instead of beside it.
  // `.then(ok, err)` rather than a bare `await` in a try: a rejection on the
  // branch that is not being awaited yet would otherwise go unhandled.
  const settle = (promise) =>
    promise.then((v) => ({ ok: true, v }), (err) => ({ ok: false, err }));
  const [lib, data] = await Promise.all([
    settle(import(MAPLIBRE_URL)),
    settle(Promise.all([
      j("data/palette.json"), j("data/stats.json"), j("data/walk_surface.json"),
      j(GEO), j(COLS),
      // Optional, and deliberately so: it is 34 KB that only the hub switcher
      // reads, and a build from before Phase 4 has no such file. A rejection
      // here would take the whole map down with it, so it resolves to null
      // and the switcher hides itself.
      j("data/hubs.json").catch(() => null),
    ])),
  ]);

  if (!lib.ok) {
    fail("Could not load MapLibre GL JS.",
         `Check your network connection or whether ${MAPLIBRE_URL} is reachable.`,
         lib.err.message);
    return;
  }
  maplibregl = lib.v;
  if (!maplibregl?.Map) {
    fail("MapLibre loaded but exposes no Map constructor.",
         "The CDN build may have changed its exports.", "");
    return;
  }

  if (!data.ok) {
    $("#loading").innerHTML =
      `<div style="max-width:420px;text-align:center;line-height:1.6">
        <strong>Data not built yet.</strong><br>
        Run <code>make all</code> first — the map reads the files it writes
        into <code>web/data/</code>.<br><br>
        <span style="color:var(--ink-muted)">${data.err.message}</span></div>`;
    return;
  }
  [pal, stats, surf, tracts, cols, hubs] = data.v;
  const colErr = attachCols();
  if (colErr) {
    fail("The area data did not load.",
         `<code>${COLS}</code> holds every column the map paints with, and `
         + "it is written by <code>make all</code> beside the geometry.",
         colErr);
    return;
  }
  attachHubs();
  const ranked = rankTracts();
  console.info(`ranked ${ranked} populated tracts for the gap score`);

  // Before anything reads state: the hash decides the theme, the layer, the
  // floors and the opening camera.
  const view = applyHash();

  document.documentElement.setAttribute("data-theme", state.theme);
  $("#themebtn").textContent = state.theme === "dark" ? "Light mode" : "Dark mode";
  $("#builtat").textContent = (stats.built_at || "").slice(0, 10);
  $("#src").innerHTML = sourceHtml(stats);

  // The one definition the card gives, so "area" is never left to mean a
  // neighborhood. Averaged over populated areas only: the parks, airports and
  // cemeteries drawn gray would otherwise pull the figure down.
  const nPop = stats.score?.n_scored;
  if (stats.total_population && nPop) {
    const avg = Math.round(stats.total_population / nPop / 100) * 100;
    $("#areadef").textContent =
      `Each area is a census tract, home to about ${fmt(avg)} people.`;
  }

  const lic = $("#license");
  if (lic) lic.innerHTML = licenseHtml(stats);

  map = new maplibregl.Map({
    container: "map",
    style: BASEMAP[state.theme],
    // A shared view wins over the opening framing; maxBounds still clamps it.
    ...(view ? { center: view.center, zoom: view.zoom }
      : { bounds: HOME_BOUNDS, fitBoundsOptions: fitOptions() }),
    maxBounds: MAX_BOUNDS,
    // maxBounds raises the effective floor well above this on any normal
    // window, because the constraint zooms in until the bounds fill the
    // viewport. minZoom stays as the backstop for a very small one.
    minZoom: 8.5,
    maxZoom: 16,
    // A pitched choropleth misrepresents area, which is the one thing a
    // choropleth must not do, and a rotated one breaks every "north shore" or
    // "east side" reading of the result. This is a city, not a globe.
    pitch: 0,
    pitchWithRotate: false,
    dragRotate: false,
    attributionControl: false,
  });
  // dragRotate covers the mouse; these are the touch and keyboard paths to the
  // same thing, and they are separate handlers with their own switches.
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-right");

  map.on("load", () => {
    addLayers();
    renderLegend();
    wireLegendClicks();
    renderKeys();
    renderTable();
    reflectState();
    wireControls();
    showCount();
    map.on("moveend", syncHash);
    syncHash();
    wireHover();          // bound once, so theme switches don't stack handlers
    $("#loading").style.display = "none";
  });

  map.on("error", (e) => console.error("map error", e?.error || e));

  // replaceState does not fire hashchange, so this only sees a hash the reader
  // changed: a permalink pasted into an already-open tab, or history
  // navigation. Re-reading it in place would mean re-syncing every control, so
  // a reload is both shorter and more honest.
  addEventListener("hashchange", () => location.reload());
}

main();
