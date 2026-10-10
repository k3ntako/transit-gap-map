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

let map, pal, stats, surf;

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
const CHOROPLETHS = ["bivariate", "density", "walk", "none"];
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
  // Open the panel when a floor is in force, or the faded tracts look like a bug.
  if (state.densFloor || state.popFloor) $("#settings").open = true;
}

// --- paint expressions ------------------------------------------------------

const biFlat = () => state.theme === "dark" ? pal.bivariate_dark.flat() : pal.bivariate.flat();
const densCls = () => state.theme === "dark" ? pal.density_classes_dark : pal.density_classes;
const walkCls = () => state.theme === "dark" ? pal.walk_classes_dark : pal.walk_classes;
const shedCls = () => state.theme === "dark" ? pal.walkshed_dark : pal.walkshed;
const unpop = () => pal.unpopulated[state.theme];

function fillColor() {
  if (state.choropleth === "bivariate") {
    const c = biFlat();
    return ["match", ["get", "bi"],
      0, c[0], 1, c[1], 2, c[2], 3, c[3], 4, c[4],
      5, c[5], 6, c[6], 7, c[7], 8, c[8], unpop()];
  }
  const [key, ramp] = state.choropleth === "density"
    ? ["dc", densCls()] : ["wc", walkCls()];
  return ["match", ["get", key], 0, ramp[0], 1, ramp[1], 2, ramp[2], unpop()];
}

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

  map.addSource("tracts", { type: "geojson", data: GEO, promoteId: "geoid" });
  // generateId supplies the feature ids that feature-state needs for the hover
  // halo; stations.geojson carries no unique property worth promoting
  // (complex_id groups several stations together).
  map.addSource("stations", {
    type: "geojson", data: "data/stations.geojson", generateId: true,
  });
  map.addSource("walksheds", { type: "geojson", data: "data/walksheds.geojson" });
  map.addSource("surface", {
    type: "image",
    url: `data/${surf.images[state.theme]}`,
    coordinates: surf.coordinates,
  });

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

  map.addLayer({
    id: "surface-img", type: "raster", source: "surface",
    layout: { visibility: state.surface ? "visible" : "none" },
    paint: { "raster-opacity": 0.85, "raster-resampling": "linear" },
  }, "tracts-fill");

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
      if (stn) {
        const label = mode === "lirr" ? "LIRR" : mode === "mnr" ? "Metro-North"
          : mode === "sir" ? "SIR" : "Subway";
        html += `<div class="tnote">Nearest: <strong>${esc(stn)}</strong>`
          + (routes ? ` (${esc(routes)})` : "") + ` · ${label}</div>`;
      }
    }
    // Says what the fill means, so the color does not have to be matched
    // against the legend grid by eye.
    if (p.dc >= 0 && p.wc >= 0)
      html += `<div class="tclass">Density <strong>${CLS_DENSITY[p.dc]}</strong>`
        + ` · Walk <strong>${CLS_WALK[p.wc]}</strong></div>`;
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

// Outlines the legend cell the hovered feature falls in, which teaches the
// legend rather than replacing it. The cells carry data-bi / data-cls for this.
function highlightLegend(p) {
  const prev = $("#legendbody .on");
  if (prev) prev.classList.remove("on");
  if (!p) return;
  const bivariate = state.choropleth === "bivariate";
  const key = bivariate ? p.bi
    : state.choropleth === "density" ? p.dc
      : state.choropleth === "walk" ? p.wc : -1;
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

  const isDens = state.choropleth === "density";
  const ramp = isDens ? densCls() : walkCls();
  const brk = isDens ? b.density : b.walk_min;
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
       <span class="lab">no residents</span></div>`;
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

  const rows = [
    ["Within 5 min", `${fmt(w["5"], 0)}%`],
    ["Within 10 min", `${fmt(w["10"], 0)}%`],
    ["Within 15 min", `${fmt(w["15"], 0)}%`],
    ["Mean walk time", `${fmt(stats.mean_walk_min, 1)} min`],
  ];
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
      syncHash();
    });
  });

  const toggle = (id, key, layers) => $(id).addEventListener("change", (e) => {
    state[key] = e.target.checked;
    layers().forEach((l) =>
      map.setLayoutProperty(l, "visibility", e.target.checked ? "visible" : "none"));
    syncHash();
  });
  toggle("#t-surface", "surface", () => ["surface-img"]);
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
  // Loaded dynamically so a CDN or module failure can be reported in the page.
  // With a static import, any failure is a module-level error: nothing below
  // ever executes and the loading overlay just sits there.
  try {
    maplibregl = await import(MAPLIBRE_URL);
  } catch (err) {
    fail("Could not load MapLibre GL JS.",
         `Check your network connection or whether ${MAPLIBRE_URL} is reachable.`,
         err.message);
    return;
  }
  if (!maplibregl?.Map) {
    fail("MapLibre loaded but exposes no Map constructor.",
         "The CDN build may have changed its exports.", "");
    return;
  }

  const j = async (u) => {
    const r = await fetch(u);
    if (!r.ok) throw new Error(`${u}: ${r.status}`);
    return r.json();
  };

  try {
    [pal, stats, surf] = await Promise.all([
      j("data/palette.json"), j("data/stats.json"), j("data/walk_surface.json"),
    ]);
  } catch (err) {
    $("#loading").innerHTML =
      `<div style="max-width:420px;text-align:center;line-height:1.6">
        <strong>Data not built yet.</strong><br>
        Run <code>make all</code> first — the map reads the files it writes
        into <code>web/data/</code>.<br><br>
        <span style="color:var(--ink-muted)">${err.message}</span></div>`;
    return;
  }

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
