const MAPLIBRE_URL =
  "https://cdn.jsdelivr.net/npm/maplibre-gl@6.13.0/dist/maplibre-gl.mjs";

let maplibregl;

const BASEMAP = {
  light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};
const GEO = "data/tracts.geojson";

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
};

let map, pal, stats, surf;

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

// --- layers -----------------------------------------------------------------

function addLayers() {
  const st = pal.station[state.theme];

  // setStyle() tears the style down, but guard anyway so this is safe to call
  // more than once.
  const LAYERS = ["tracts-fill", "tracts-line", "tracts-hover", "surface-img",
    "stations-rail", "stations-subway",
    ...stats.thresholds_min.map((t) => `shed-${t}`)];
  LAYERS.forEach((l) => { if (map.getLayer(l)) map.removeLayer(l); });
  ["tracts", "stations", "walksheds", "surface"].forEach((sr) => {
    if (map.getSource(sr)) map.removeSource(sr);
  });

  map.addSource("tracts", { type: "geojson", data: GEO, promoteId: "geoid" });
  map.addSource("stations", { type: "geojson", data: "data/stations.geojson" });
  map.addSource("walksheds", { type: "geojson", data: "data/walksheds.geojson" });
  map.addSource("surface", {
    type: "image",
    url: `data/${surf.images[state.theme]}`,
    coordinates: surf.coordinates,
  });

  map.addLayer({
    id: "tracts-fill", type: "fill", source: "tracts",
    paint: {
      "fill-color": fillColor(),
      "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.95, 0.78],
    },
  });

  map.addLayer({
    id: "tracts-line", type: "line", source: "tracts",
    paint: {
      "line-color": state.theme === "dark" ? "#000" : "#fff",
      "line-width": 0.4, "line-opacity": 0.5,
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
}

// --- hover ------------------------------------------------------------------

function wireHover() {
  const tip = $("#tooltip");
  map.getContainer().appendChild(tip);

  const clear = () => {
    if (state.hovered != null) {
      map.setFeatureState({ source: "tracts", id: state.hovered }, { hover: false });
      state.hovered = null;
    }
    tip.style.display = "none";
  };

  map.on("mousemove", "tracts-fill", (e) => {
    const f = e.features?.[0];
    if (!f) return clear();
    if (state.hovered !== f.id) {
      if (state.hovered != null)
        map.setFeatureState({ source: "tracts", id: state.hovered }, { hover: false });
      state.hovered = f.id;
      map.setFeatureState({ source: "tracts", id: f.id }, { hover: true });
    }
    const p = f.properties;
    const L = stats.lookups;
    const name = L.nta[p.nta] || "Unknown area";
    const boro = L.boro[p.boro] || "";
    const stn = L.st[p.st] || "";
    const routes = L.sroutes[p.sroutes] || "";
    const mode = L.smode[p.smode] || "";
    const noacc = p.noacc === true || p.noacc === "true";

    const row = (k, v) => `<div class="trow"><span class="tk">${k}</span><span class="tv">${v}</span></div>`;
    let html = `<div class="tname">${name}</div><div class="tk" style="margin-bottom:5px">${boro}</div>`;
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
        html += `<div class="tnote">Nearest: <strong>${stn}</strong>`
          + (routes ? ` (${routes})` : "") + ` · ${label}</div>`;
      }
    }
    if (p.pop === 0)
      html += `<div class="tnote">No residents — excluded from the statistics.</div>`;

    tip.innerHTML = html;
    tip.style.display = "block";
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    const box = map.getCanvas().getBoundingClientRect();
    let x = e.point.x + pad, y = e.point.y + pad;
    if (x + w > box.width - 8) x = e.point.x - w - pad;
    if (y + h > box.height - 8) y = e.point.y - h - pad;
    x = Math.max(8, x); y = Math.max(8, y);
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
  });

  map.on("mouseleave", "tracts-fill", clear);
  map.getCanvas().addEventListener("mouseout", clear);
}

// --- legend -----------------------------------------------------------------

function renderLegend() {
  const el = $("#legendbody");
  const b = stats.breaks;

  if (state.choropleth === "bivariate") {
    const c = state.theme === "dark" ? pal.bivariate_dark : pal.bivariate;
    // Rows top-to-bottom = high density to low, so the grid reads like a map key.
    const cells = [2, 1, 0].map((i) =>
      c[i].map((hex) => `<i style="background:${hex}"></i>`).join("")).join("");
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
    `<div class="ramprow"><i style="background:${hex}"></i>
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
  document.documentElement.setAttribute("data-theme", next);
  $("#themebtn").textContent = next === "dark" ? "Light mode" : "Dark mode";
  // setStyle swaps the basemap wholesale, so every source and layer is re-added
  // once the new style reports ready.
  map.setStyle(BASEMAP[next]);
  map.once("style.load", () => {
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
      if (on) repaint(); else $("#legendbody").innerHTML =
        `<div class="sub">Choropleth hidden.</div>`;
    });
  });

  const toggle = (id, key, layers) => $(id).addEventListener("change", (e) => {
    state[key] = e.target.checked;
    layers().forEach((l) =>
      map.setLayoutProperty(l, "visibility", e.target.checked ? "visible" : "none"));
  });
  toggle("#t-surface", "surface", () => ["surface-img"]);
  toggle("#t-sheds", "walksheds", () => stats.thresholds_min.map((t) => `shed-${t}`));
  toggle("#t-stations", "stations", () => ["stations-subway", "stations-rail"]);

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
    center: [-73.95, 40.705],
    zoom: 10.1,
    minZoom: 8.5,
    maxZoom: 16,
    attributionControl: false,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-right");

  map.on("load", () => {
    addLayers();
    renderLegend();
    renderKeys();
    renderStats();
    renderTable();
    wireControls();
    wireHover();          // bound once, so theme switches don't stack handlers
    $("#loading").style.display = "none";
  });

  map.on("error", (e) => console.error("map error", e?.error || e));
}

main();
