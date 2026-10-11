const MAPLIBRE_URL =
  "https://cdn.jsdelivr.net/npm/maplibre-gl@6.13.0/dist/maplibre-gl.mjs";

let maplibregl;

const BASEMAP = {
  light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};
const GEO = "data/tracts.geojson";

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

// Tracts under a display floor fade to this instead of being filtered out. A
// choropleth with holes in it reads as missing data, not as excluded.
const FADED_OPACITY = 0.12;

const $ = (s) => document.querySelector(s);
const fmt = (n, d = 0) =>
  n == null || !isFinite(n) ? "—" : n.toLocaleString("en-US",
    { minimumFractionDigits: d, maximumFractionDigits: d });

const state = {
  theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
  choropleth: "bivariate",
  // The gap score's weight: 1 is density only, 0 is walk time only.
  gapWeight: 0.5,
  surface: false,
  walksheds: true,
  stations: true,
  hovered: null,
  hoveredStation: null,
  densFloor: 0,
  popFloor: 0,
  // Set once the theme is an explicit choice -- by the toggle or by the URL --
  // so a shared link carries the theme but an unshared one keeps following the
  // reader's own OS setting.
  themePinned: false,
};

let map, pal, stats, surf, tracts;

const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// queryRenderedFeatures throws on an unknown layer id, and during a theme
// switch the style is torn down and rebuilt, so the guard is not paranoia.
const query = (layer, point) =>
  map.getLayer(layer) ? map.queryRenderedFeatures(point, { layers: [layer] }) : [];

// --- permalink --------------------------------------------------------------

// #c=walk&dens=10000&pop=500&t=dark&surface=1&sheds=0&v=11.20/40.7050/-73.9500
//
// Only what differs from the default is written, so the usual link stays short
// and reads as a list of deliberate choices. Plain key=value with unknown keys
// ignored, which means a later control is one more key and there is no format
// to version. `v` is zoom/lat/lon, the order OSM and Mapbox hashes use.
const CHOROPLETHS = ["bivariate", "gap", "density", "walk", "access", "none"];
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
  const dens = num("dens", 0, 30000);
  if (dens != null) state.densFloor = dens;
  const pop = num("pop", 0, 3000);
  if (pop != null) state.popFloor = pop;
  const w = num("w", 0, 1);
  if (w != null) state.gapWeight = w;
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
    if (state.choropleth !== "bivariate") q.push(`c=${state.choropleth}`);
    if (state.densFloor) q.push(`dens=${state.densFloor}`);
    if (state.popFloor) q.push(`pop=${state.popFloor}`);
    if (state.gapWeight !== 0.5) q.push(`w=${state.gapWeight}`);
    if (state.themePinned) q.push(`t=${state.theme}`);
    if (state.surface) q.push("surface=1");
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
  $("#f-dens").value = state.densFloor;
  $("#f-pop").value = state.popFloor;
  $("#f-weight").value = state.gapWeight;
  showGapWeight();
  // Open the panel when a floor is in force, or the faded tracts look like a bug.
  if (state.densFloor || state.popFloor) $("#settings").open = true;
}

// --- the walk-access gap score ----------------------------------------------

// Not "under-served": two of the three axes in ROADMAP.md's reframe are still
// missing, so this is what it says it is -- a crowd with a long walk.
const GAP_BANDS = 7;

// Percentile rank rather than the raw value. Density is roughly log-normal --
// the middle tract holds 16,664 people/km2 and the densest 110,790 -- while
// walk time is not, so a product of raw values would be a density map with a
// rounding error. Ranks put both on 0-1, where neither can dominate.
//
// `(i + 1) / n` with later writes winning means ties take the highest position,
// so a rank reads as "the share of tracts at or below this one".
function rankInto(feats, src, dst) {
  const vals = feats.map((f) => f.properties[src]).sort((a, b) => a - b);
  const rank = new Map();
  vals.forEach((v, i) => rank.set(v, (i + 1) / vals.length));
  for (const f of feats) f.properties[dst] = rank.get(f.properties[src]);
}

// The ranks are written into the fetched GeoJSON *before* it is handed to the
// source, so the score is a paint expression over ordinary properties: a weight
// change is then one setPaintProperty, with no per-feature state to push and no
// re-parse of 1.7 MB of geometry. Nothing is added to the payload on disk.
function rankTracts() {
  const scored = tracts.features.filter(
    (f) => f.properties.pop > 0 && f.properties.walk != null);
  rankInto(scored, "dens", "rd");
  rankInto(scored, "walk", "rw");
  return scored.length;
}

// A weighted geometric mean, so a tract needs BOTH halves to be bad. A weighted
// sum lets one half carry it: Mariner's Harbor at 279 people/km2 and a
// 127-minute walk lands mid-ramp under a sum and near the floor under this,
// which is the honest answer for 1,078 people on marshland.
const gapScore = (p, w) => Math.pow(p.rd, w) * Math.pow(p.rw, 1 - w);

// Quantile breaks, not equal intervals. The product is heavily skewed -- half
// the city scores under 0.18 of a 0-0.79 range -- so equal slices would put
// nearly everything in the palest band and eight tracts in the darkest.
//
// The bands come out equal to within a tract or two, except at w=0 or w=1,
// where the score collapses to one variable and that variable's ties cannot be
// split: walk time ships rounded to 0.1 min, so those bands run ~300-345.
let gapCache = { w: null };
function gapCompute(w) {
  if (gapCache.w === w) return gapCache;
  const sorted = tracts.features
    .filter((f) => f.properties.rd != null)
    .map((f) => gapScore(f.properties, w))
    .sort((a, b) => a - b);
  const breaks = [];
  for (let i = 1; i < GAP_BANDS; i++) {
    let b = sorted[Math.floor(i * sorted.length / GAP_BANDS)];
    const prev = breaks[breaks.length - 1];
    // `step` requires strictly ascending stops, and a quantile can repeat when
    // many tracts share a score. Nudging keeps the band empty instead of
    // throwing out the whole expression.
    if (prev !== undefined && b <= prev) b = prev + 1e-6;
    breaks.push(b);
  }
  gapCache = { w, sorted, breaks };
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

const gapRamp = () =>
  state.theme === "dark" ? pal.gap_classes_dark : pal.gap_classes;

function gapColor() {
  const ramp = gapRamp();
  const w = state.gapWeight;
  // `^` is an expression operator, so the entire score evaluates in the paint.
  const score = ["*", ["^", ["get", "rd"], w], ["^", ["get", "rw"], 1 - w]];
  const steps = ["step", score, ramp[0]];
  gapCompute(w).breaks.forEach((b, i) => steps.push(b, ramp[i + 1]));
  // No rank means unpopulated, or no station reachable on foot: no score, so it
  // takes the unpopulated color rather than quietly landing in band 0.
  return ["case", ["!", ["has", "rd"]], unpop(), steps];
}

// --- paint expressions ------------------------------------------------------

const biFlat = () => state.theme === "dark" ? pal.bivariate_dark.flat() : pal.bivariate.flat();
const densCls = () => state.theme === "dark" ? pal.density_classes_dark : pal.density_classes;
const walkCls = () => state.theme === "dark" ? pal.walk_classes_dark : pal.walk_classes;
const shedCls = () => state.theme === "dark" ? pal.walkshed_dark : pal.walkshed;
const unpop = () => pal.unpopulated[state.theme];

function fillColor() {
  if (state.choropleth === "gap") return gapColor();
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
const belowFloor = () => ["any",
  ["<", ["coalesce", ["get", "dens"], 0], state.densFloor],
  ["<", ["coalesce", ["get", "pop"], 0], state.popFloor]];

const isBelowFloor = (p) =>
  (p.dens ?? 0) < state.densFloor || (p.pop ?? 0) < state.popFloor;

const fillOpacity = () => ["case",
  belowFloor(), FADED_OPACITY,
  ["boolean", ["feature-state", "hover"], false], 0.95,
  0.78];

// The outlines have to fade with the fill, or an excluded tract still reads as
// a crisp mesh of boundaries laid over the basemap.
const lineOpacity = () => ["case", belowFloor(), 0.15, 0.5];

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

function addLayers() {
  const st = pal.station[state.theme];

  // setStyle() tears the style down, but guard anyway so this is safe to call
  // more than once.
  const LAYERS = ["tracts-fill", "tracts-line", "tracts-hover", "surface-img",
    "stations-hit", "stations-rail", "stations-subway",
    ...stats.thresholds_min.map((t) => `shed-${t}`)];
  LAYERS.forEach((l) => { if (map.getLayer(l)) map.removeLayer(l); });
  ["tracts", "stations", "walksheds", "surface"].forEach((sr) => {
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
  map.addLayer({
    id: "tracts-fill", type: "fill", source: "tracts",
    layout: { visibility: choro },
    paint: {
      "fill-color": fillColor(),
      "fill-opacity": fillOpacity(),
    },
  });

  map.addLayer({
    id: "tracts-line", type: "line", source: "tracts",
    layout: { visibility: choro },
    paint: {
      "line-color": state.theme === "dark" ? "#000" : "#fff",
      "line-width": 0.4, "line-opacity": lineOpacity(),
    },
  });

  // Hover ring: 2px in the surface color, per the mark spec for overlapping marks.
  map.addLayer({
    id: "tracts-hover", type: "line", source: "tracts",
    paint: {
      "line-color": pal.ink[state.theme].primary,
      "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2, 0],
    },
  });

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
      if (p.wlo != null && p.whi != null)
        html += row("Range in tract", `${fmt(p.wlo, 1)}–${fmt(p.whi, 1)} min`);
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
      }
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
    if (state.choropleth === "gap" && p.rd != null) {
      const s = gapScore(p, state.gapWeight);
      html += `<div class="tclass">Gap: worse than <strong>`
        + `${Math.round(100 * gapPercentile(s))}%</strong> of tracts<br>`
        + `density ${Math.round(100 * p.rd)}th percentile · `
        + `walk ${Math.round(100 * p.rw)}th percentile</div>`;
    } else if (state.choropleth === "access" && accessClass(p) >= 0) {
      html += `<div class="tclass">Access cost `
        + `<strong>${CLS_ACCESS[accessClass(p)]}</strong> of tracts</div>`;
    } else if (p.dc >= 0 && p.wc >= 0) {
      html += `<div class="tclass">Density <strong>${CLS_DENSITY[p.dc]}</strong>`
        + ` · Walk <strong>${CLS_WALK[p.wc]}</strong></div>`;
    }
    if (p.pop === 0)
      html += `<div class="tnote">No residents — excluded from the statistics.</div>`;
    else if (isBelowFloor(p))
      html += `<div class="tnote">Below a display floor — faded, not excluded.</div>`;
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

// Which of the 7 gap bands a tract falls in, or -1 if it has no score.
function gapBand(p) {
  if (p.rd == null) return -1;
  const s = gapScore(p, state.gapWeight);
  const { breaks } = gapCompute(state.gapWeight);
  let i = 0;
  while (i < breaks.length && s >= breaks[i]) i++;
  return i;
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

  if (state.choropleth === "none") {
    el.innerHTML = `<div class="sub">Choropleth hidden.</div>`;
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
    el.innerHTML = `
      <div class="bivwrap">
        <div class="bivaxis-y">Density <span class="arrow">→</span></div>
        <div>
          <div class="bivgrid">${cells}</div>
          <div class="bivaxis-x">Walk time to station <span class="arrow">→</span></div>
        </div>
      </div>
      <div class="sub" style="margin-top:9px">
        Darkest corner (top right) is <strong>dense but poorly served</strong>:
        above ${fmt(b.density[1])} people/km² and over ${fmt(b.walk_min[1], 1)} min walk.
      </div>`;
    return;
  }

  if (state.choropleth === "gap") {
    const ramp = gapRamp();
    const pct = (i) => Math.round(100 * i / GAP_BANDS);
    // Worst band at the top, so the legend reads in the same direction as the
    // finding. The label is a percentile of tracts, never the score itself:
    // the score is a position in a line-up, not a quantity.
    el.innerHTML = ramp.map((hex, i) => [hex, i]).reverse().map(([hex, i]) =>
      `<div class="ramprow"><i data-cls="${i}" style="background:${hex}"></i>
       <span class="lab">${pct(i)}\u2013${pct(i + 1)}%</span></div>`).join("")
      + `<div class="ramprow"><i style="background:${unpop()}"></i>
         <span class="lab">no residents</span></div>`
      + `<div class="sub" style="margin-top:9px">Percentile of tracts, worst
         first. Density and walk time each as a rank, then multiplied \u2014 a
         tract needs <strong>both</strong> a crowd and a long walk to score
         high.</div>`;
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
       <strong>plus the expected wait</strong> at the station the trip would
       actually use — half the scheduled 8–9am headway, capped at 15 min. Not
       comparable to published "within N minutes" figures, which are all
       walk-only.</div>` : "");
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
        <span class="lab">LIRR / Metro-North</span></div>`;
}

// --- stats + table ----------------------------------------------------------

function renderStats() {
  const w = stats.pct_within_min;
  $("#hero-n").textContent = `${fmt(w["10"], 0)}%`;
  $("#hero-u").innerHTML =
    `of New Yorkers live within a<br><strong>10-minute walk</strong> of a train station`;

  const a = stats.pct_within_min_access;
  const rows = [
    ["Within 5 min", `${fmt(w["5"], 0)}%`],
    ["Within 10 min", `${fmt(w["10"], 0)}%`],
    ["Within 15 min", `${fmt(w["15"], 0)}%`],
    ["Mean walk time", `${fmt(stats.mean_walk_min, 1)} min`],
  ];
  // Counting the wait is what the hero number leaves out, and the gap between
  // the two is large enough that stating only the first would be misleading:
  // the 10-minute share falls from 64% to 46% once a train has to turn up.
  if (a) rows.push(
    ["Within 10 min, incl. wait", `${fmt(a["10"], 0)}%`],
    ["Mean wait for a train", `${fmt(stats.mean_wait_min, 1)} min`],
  );
  $("#statlist").innerHTML = rows.map(([k, v]) =>
    `<div class="statrow"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("");

  // Shows why routing matters: the same people, the same stations, the only
  // difference being straight-line versus walking the street network.
  $("#bench").innerHTML =
    `Within a half mile of the <em>subway</em> alone:
     <strong>${fmt(stats.pct_within_half_mile_subway_only, 0)}%</strong> on foot,
     versus ${fmt(stats.pct_within_half_mile_subway_straight, 0)}% as the crow flies.
     Straight-line distance overstates access by
     ${fmt(stats.half_mile_detour_penalty, 0)} points.`;
}

function renderTable() {
  const boro = stats.by_borough.map((r) =>
    `<tr><td>${r.boroname}</td><td class="num">${fmt(r.pop)}</td>
     <td class="num">${fmt(r.mean_walk_min, 1)}</td>
     <td class="num">${fmt(r.pct_within_10, 0)}%</td></tr>`).join("");

  const des = stats.transit_deserts.map((r) =>
    `<tr><td>${r.ntaname ?? ""}</td><td>${r.boroname}</td>
     <td class="num">${fmt(r.pop)}</td><td class="num">${fmt(r.density_km2)}</td>
     <td class="num">${fmt(r.walk_min, 1)}</td></tr>`).join("");

  $("#tables").innerHTML = `
    <table>
      <caption>Walking access to a train station, by borough. Population-weighted.</caption>
      <thead><tr><th>Borough</th><th class="num">Population</th>
        <th class="num">Mean walk (min)</th><th class="num">Within 10 min</th></tr></thead>
      <tbody>${boro}</tbody>
    </table>
    <table>
      <caption>Dense but poorly served: the 20 highest-density tracts with the
        longest walks. These are the dark corner of the bivariate map.</caption>
      <thead><tr><th>Neighborhood</th><th>Borough</th><th class="num">Population</th>
        <th class="num">Density /km²</th><th class="num">Walk (min)</th></tr></thead>
      <tbody>${des}</tbody>
    </table>`;
}

// --- theme / controls -------------------------------------------------------

function repaint() {
  map.setPaintProperty("tracts-fill", "fill-color", fillColor());
  renderLegend();
}

// The weight slider is meaningless unless the gap layer is the one on screen.
function showGapWeight() {
  $("#gapweight").hidden = state.choropleth !== "gap";
}

function applyTheme(next) {
  state.theme = next;
  state.themePinned = true;
  syncHash();
  document.documentElement.setAttribute("data-theme", next);
  $("#themebtn").textContent = next === "dark" ? "Light mode" : "Dark mode";
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
      showGapWeight();
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
  toggle("#t-stations", "stations",
    () => ["stations-subway", "stations-rail", "stations-hit"]);

  // The floors repaint from fields already on every feature: no pipeline round
  // trip, which is the roadmap's "ship metrics, score in the browser" rule.
  const floor = (id, out, key, label) => {
    const input = $(id), output = $(out);
    const show = () => { output.textContent = label(state[key]); };
    input.addEventListener("input", () => {
      state[key] = Number(input.value);
      show();
      map.setPaintProperty("tracts-fill", "fill-opacity", fillOpacity());
      map.setPaintProperty("tracts-line", "line-opacity", lineOpacity());
      syncHash();
    });
    show();
  };
  floor("#f-dens", "#o-dens", "densFloor", (v) => v ? `${fmt(v)} /km²` : "off");
  floor("#f-pop", "#o-pop", "popFloor", (v) => v ? `${fmt(v)} people` : "off");

  // The weight only repaints: the score lives in the expression, so there is
  // nothing to recompute per feature beyond the quantile breaks.
  const weight = $("#f-weight"), weightOut = $("#o-weight");
  const showWeight = () => {
    const w = state.gapWeight;
    weightOut.textContent = `${Math.round(w * 100)} / ${Math.round((1 - w) * 100)}`;
  };
  weight.addEventListener("input", () => {
    state.gapWeight = Number(weight.value);
    showWeight();
    if (state.choropleth === "gap") repaint();
    syncHash();
  });
  showWeight();

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
      j(GEO),
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
  [pal, stats, surf, tracts] = data.v;
  const ranked = rankTracts();
  console.info(`ranked ${ranked} populated tracts for the gap score`);

  // Before anything reads state: the hash decides the theme, the layer, the
  // floors and the opening camera.
  const view = applyHash();

  document.documentElement.setAttribute("data-theme", state.theme);
  $("#themebtn").textContent = state.theme === "dark" ? "Light mode" : "Dark mode";
  $("#builtat").textContent = (stats.built_at || "").slice(0, 10);
  $("#src").innerHTML = Object.values(stats.attribution).join(" · ");

  // AGPL-3.0 section 13: a networked instance must prominently offer its
  // corresponding source. config.SOURCE_URL feeds stats.source_url; with no
  // URL set (a local run) we say the source ships alongside instead.
  const lic = $("#license");
  if (lic && stats.license) {
    const url = (stats.source_url || "").trim();
    lic.textContent = `${stats.license} \u2014 `;
    if (/^https:\/\/[^\s]+$/i.test(url)) {
      const link = document.createElement("a");
      link.href = url;
      link.rel = "noopener";
      link.textContent = "source code";
      lic.append(link);
    } else {
      lic.append("source in this distribution");
    }
  }

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
    renderKeys();
    renderStats();
    renderTable();
    reflectState();
    wireControls();
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
