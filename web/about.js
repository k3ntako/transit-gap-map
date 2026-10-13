// The method page. It renders one JSON file and draws nothing, which is the
// whole reason it is a separate page: the map's payloads are 2 MB and a
// reader who wants to know how a number was computed should not pay for them.
//
// Every figure comes from stats.js, the module the title card also uses, so
// the four rows beside the map and the long version here are the same
// function called twice.

import {
  fmt, esc, floorMin, statGroups, benchHtml, railHtml, validityHtml,
  boroughTable, clusterTable, disagreeTable, sourceHtml, licenseHtml,
} from "./stats.js";

const $ = (s) => document.querySelector(s);
const set = (sel, html) => {
  const el = $(sel);
  if (el) el.innerHTML = html;
};

// The theme travels in the query string rather than the hash, so the hash
// stays available as a section anchor -- the map links straight to
// #agreement. An unpinned visit follows the reader's OS setting, the same
// rule the map applies.
const params = new URLSearchParams(location.search);
const asked = params.get("t");
let theme = asked === "dark" || asked === "light" ? asked
  : matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
let pinned = asked === "dark" || asked === "light";

function applyTheme(next, pin = true) {
  theme = next;
  pinned = pin;
  document.documentElement.setAttribute("data-theme", theme);
  $("#themebtn").textContent = theme === "dark" ? "Light mode" : "Dark mode";
  // Carried onto the back links so a reader does not get flashed on the way
  // home, and written into this page's own URL so a reload keeps it.
  const q = pinned ? `#t=${theme}` : "";
  for (const id of ["#backlink", "#backlink2"]) {
    const a = $(id);
    if (a) a.href = `index.html${q}`;
  }
  if (pinned) {
    history.replaceState(null, "",
      `${location.pathname}?t=${theme}${location.hash}`);
  }
}

function statGroupHtml(stats) {
  return statGroups(stats).map((g) => `
    <div class="statgroup">
      <p class="grouplabel">${esc(g.title)}</p>
      ${g.rows.map(([k, v]) => `<div class="statrow">
        <span class="k">${k}</span><span class="v">${v}</span></div>`).join("")}
    </div>`).join("");
}

// The score, in words, with its own constant read from the build rather than
// repeated here.
function scoreHtml(stats) {
  const sc = stats.score;
  if (!sc) return "";
  const fl = fmt(floorMin(stats), 0);
  return `The map's headline score is
    <strong>population &times; minutes past the standard</strong>: excess
    person-minutes, in units a reader can check. The standard is ${fl}
    minutes, the walk at which the half-mile planning rule and the observed
    85th-percentile walk to a metro station in the research literature agree.
    Citywide that comes to
    <strong>${fmt(sc.total_excess_person_minutes / 1e6, 1)}M</strong> excess
    person-minutes on a single one-way trip, held by
    <strong>${fmt(sc.population_beyond_standard)}</strong> people;
    ${fmt(sc.n_at_standard)} of ${fmt(sc.n_scored)} populated tracts are at or
    inside the standard and score zero. It replaced a product of percentile
    ranks, which the corridor test below scored at
    ${fmt(100 * (stats.alignment?.precision?.["ranks (original)"]?.["25"] ?? 0),
      0)}% against a
    ${fmt(100 * (stats.alignment?.baseline ?? 0), 0)}% random baseline &mdash;
    indistinguishable from noise.`;
}

function waitHtml(stats) {
  const a = stats.pct_within_min_access;
  if (!a) return "";
  return `<strong>The wait is counted, and it is the part a walk map leaves
    out.</strong> Living near a station says nothing about whether a train
    turns up: the share of New Yorkers within ten minutes falls from
    ${fmt(stats.pct_within_min["10"], 0)}% to
    <strong>${fmt(a["10"], 0)}%</strong> once the expected wait is added,
    a mean of ${fmt(stats.mean_wait_min, 1)} minutes. The wait charged at a
    station is the combined headway of every service making the same next
    stop, sampled on one representative weekday morning &mdash; so a rider at
    Fordham bound for Grand Central waits once for the Harlem and New Haven
    lines together, not twice.`;
}

function censorHtml(stats) {
  const ac = stats.accessibility;
  if (!ac || ac.weighted_jobs_time == null) return "";
  return `Counting the jobs reachable inside a fixed
    ${fmt(ac.budget_min, 0)}-minute budget does something quietly fatal at
    the bottom of the distribution: it right-censors.
    <strong>${fmt(ac.n_past_budget)} of ${fmt(stats.n_geographies)}
    tracts</strong> need longer than that to reach
    ${fmt(100 * ac.threshold_share, 0)}% of the city's jobs, so they all
    saturate near zero and stop being distinguishable from one another
    &mdash; and they are precisely the tracts this map exists to find. Worse,
    the budget sits almost exactly at the median of the thing it cuts
    (${fmt(ac.median_jobs_time, 1)} minutes), which is the worst available
    place to cut when the far side is the subject. So the primary measure
    swaps the axes: fix the share of jobs, measure the minutes. The
    population-weighted answer is
    <strong>${fmt(ac.weighted_jobs_time, 0)} minutes</strong> to
    ${fmt(100 * ac.threshold_share, 0)}% of the city's jobs, and
    ${fmt(ac.p90_jobs_time, 0)} minutes for the slowest tenth of tracts.`;
}

async function main() {
  applyTheme(theme, pinned);
  $("#themebtn").addEventListener("click", () =>
    applyTheme(theme === "dark" ? "light" : "dark"));

  let stats;
  try {
    const r = await fetch("data/stats.json");
    if (!r.ok) throw new Error(`data/stats.json: ${r.status}`);
    stats = await r.json();
  } catch (err) {
    set("#m-method",
      `<strong>Data not built yet.</strong> Run <code>make all</code> first
       &mdash; this page reads the same <code>web/data/stats.json</code> the
       map does. <span class="sub">${esc(err.message)}</span>`);
    return;
  }

  set("#m-method", esc(stats.method ?? ""));
  set("#m-bench", benchHtml(stats));
  set("#m-pop", fmt(stats.total_population));
  set("#m-tracts", fmt(stats.n_geographies));
  set("#m-groups", statGroupHtml(stats));
  set("#m-boro", boroughTable(stats));
  set("#m-score", scoreHtml(stats));
  set("#m-clusters", clusterTable(stats));
  set("#m-validity", validityHtml(stats));
  set("#m-disagree", disagreeTable(stats));
  set("#m-wait", waitHtml(stats));
  set("#m-censor", censorHtml(stats));
  set("#m-rail", railHtml(stats));
  set("#m-src", sourceHtml(stats));
  set("#m-built", esc((stats.built_at || "").slice(0, 10)));
  set("#m-license", licenseHtml(stats));

  // The map links to #agreement, and the section only exists once the fetch
  // has resolved, so the browser's own anchor scroll has already given up by
  // the time there is anything to scroll to.
  if (location.hash.length > 1) {
    const el = document.querySelector(location.hash);
    if (el) el.scrollIntoView();
  }
}

main();
