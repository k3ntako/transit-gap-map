// Everything the stats page and the method page say about the numbers.
//
// It exists because the pages have to agree with each other and with the map,
// and the way that goes wrong is one of them keeping a stale sentence after a
// rebuild moves a number. So nothing here reads the DOM or holds state: each
// function takes the parsed `stats.json` and returns a string or rows, and the
// pages render what it gives them.
//
// Numbers are never written as literals in the prose. If a sentence needs a
// figure, it interpolates it from `stats`, so a rebuild cannot leave the words
// disagreeing with the map.

export const fmt = (n, d = 0) =>
  n == null || !isFinite(n) ? "—" : n.toLocaleString("en-US",
    { minimumFractionDigits: d, maximumFractionDigits: d });

export const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export const floorMin = (stats) => stats.score?.floor_minutes ?? 10;

// --- the stats page's facts -------------------------------------------------

const listOf = (xs) => xs.length < 2 ? xs.join("")
  : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

// One figure and one or two plain sentences each. The figure leads and the
// sentence says what it counts; neither teases the other, because a fact that
// needs a click to make sense is a headline, not a fact. Every one is guarded,
// so a build that predates a section drops the card rather than printing a
// dash in a sentence.
export function facts(stats) {
  const f = [];
  const w = stats.pct_within_min;
  const a = stats.pct_within_min_access;
  const fl = fmt(floorMin(stats), 0);

  if (w) f.push({
    value: `${fmt(w["10"], 0)}%`,
    label: `live within a ${fl}-minute walk of a train`,
    body: `That counts the subway, the Staten Island Railway, the LIRR and
      Metro-North. ${fmt(w["5"], 0)}% are within 5 minutes and
      ${fmt(w["15"], 0)}% within 15.`,
  });

  if (a) f.push({
    value: `${fmt(a["10"], 0)}%`,
    label: `are within ${fl} minutes counting the wait`,
    body: `Walk time plus the expected wait for a train. The average wait is
      ${fmt(stats.mean_wait_min, 1)} minutes.`,
  });

  if (stats.median_walk_min != null && stats.by_borough?.length) {
    const slow = stats.by_borough.slice()
      .sort((x, y) => y.mean_walk_min - x.mean_walk_min).slice(0, 2)
      .map((r) => esc(r.boroname));
    f.push({
      value: `${fmt(stats.median_walk_min, 1)} min`,
      label: "is the median walk to the nearest station",
      body: `The mean is ${fmt(stats.mean_walk_min, 1)} minutes, pulled up by
        long walks. ${slow.join(" and ")} have the longest averages.`,
    });
  }

  if (stats.half_mile_detour_penalty != null) f.push({
    value: `${fmt(stats.half_mile_detour_penalty, 0)} points`,
    label: "is how much straight-line distance overstates access",
    body: `A half-mile circle around each subway station covers
      ${fmt(stats.pct_within_half_mile_subway_straight, 0)}% of New Yorkers.
      Walking the actual streets, ${fmt(stats.pct_within_half_mile_subway_only,
        0)}% are within a half mile.`,
  });

  const far = stats.transit_deserts?.[0];
  if (far) f.push({
    value: `${fmt(far.walk_min, 1)} min`,
    label: "is the longest walk in a high-density tract",
    body: `A tract in ${esc(far.ntaname)}, ${esc(far.boroname)}, with
      ${fmt(far.pop)} residents at ${fmt(far.density_km2)} people per km².
      Among the densest third of tracts, none is farther from a train.`,
  });

  const big = stats.clusters?.largest?.[0];
  if (big) f.push({
    value: `${fmt(big.pop / 1e6, 2)}M`,
    label: `people live in one continuous area past the ${fl}-minute mark`,
    body: `${fmt(big.n_tracts)} adjoining census tracts, including
      ${listOf(big.where.map(esc))}.`,
  });

  const sc = stats.score;
  if (sc) {
    // Minutes to years, for scale. One trip each, which is how the score
    // itself is defined, so the sentence says so rather than implying a day.
    const years = sc.total_excess_person_minutes / 60 / 24 / 365;
    f.push({
      value: `${fmt(sc.total_excess_person_minutes / 1e6, 1)}M`,
      label: `extra minutes of walking past the ${fl}-minute mark`,
      body: `Added up across the ${fmt(sc.population_beyond_standard / 1e6,
        2)}M people who live past it, for one trip each. That is about
        ${fmt(years, 0)} years.`,
    });
  }

  const r = stats.commuter_rail_dependence;
  if (r) f.push({
    value: `${fmt(r.population / 1000, 0)}k`,
    label: "New Yorkers' nearest train is the LIRR or Metro-North",
    body: `${fmt(r.pct_of_population, 1)}% of the city. Both charge a separate
      fare, and the nearest subway station is a median
      ${fmt(r.median_penalty_min, 0)} minutes farther on foot.`,
  });

  const b = stats.bus_ftn;
  if (b) f.push({
    value: fmt(b.n_ftn),
    label: `of ${fmt(b.n_stops)} bus stops have frequent service all day`,
    body: `A single route every ${fmt(b.max_headway_min, 0)} minutes or
      better at the morning peak, midday and evening. Counting the morning
      peak alone, ${fmt(b.n_ftn_peak_only)} would qualify.`,
  });

  const ac = stats.accessibility;
  if (ac?.weighted_jobs_time != null) f.push({
    value: `${fmt(ac.weighted_jobs_time, 0)} min`,
    label: `to reach ${fmt(100 * ac.threshold_share, 0)}% of the city's jobs`,
    body: `On foot and by train, for the average New Yorker. For the slowest
      tenth of tracts it takes ${fmt(ac.p90_jobs_time, 0)} minutes.`,
  });

  const al = stats.alignment;
  const pc = al?.precision?.[al.shipped]?.["25"];
  if (pc != null) f.push({
    value: `${fmt(100 * pc, 0)}%`,
    label: "of the 25 most under-served tracts lie in a proposed rail corridor",
    body: `Corridors proposed by the MTA and partners, against the map's
      under-service score. A score with no predictive value would hit
      ${fmt(100 * al.baseline, 0)}%.`,
  });

  return f;
}

// The borough comparison as bars, for the stats page: one series, so one
// hue and no legend, and every bar labeled with its value, so the color
// carries nothing the text does not. Sorted rather than alphabetical, because
// the comparison is the point. The table on the same page is the exact view.
export function boroughBars(stats) {
  const rows = (stats.by_borough ?? []).slice()
    .sort((x, y) => y.pct_within_10 - x.pct_within_10);
  if (!rows.length) return "";
  return `<div class="bars" role="list">${rows.map((r) => {
    const v = fmt(r.pct_within_10, 0);
    return `<div class="bar" role="listitem"
      title="${esc(r.boroname)}: ${v}% within 10 min, mean walk ${
        fmt(r.mean_walk_min, 1)} min">
      <span class="k">${esc(r.boroname)}</span>
      <span class="track"><i style="width:${
        Math.max(0, Math.min(100, r.pct_within_10))}%"></i></span>
      <span class="v">${v}%</span></div>`;
  }).join("")}</div>`;
}

// --- the method page's full list --------------------------------------------

// Grouped, because fifteen rows in one column is the wall that moved this off
// the map in the first place. Each group answers one question.
export function statGroups(stats) {
  const w = stats.pct_within_min;
  const a = stats.pct_within_min_access;
  const sub = stats.pct_within_min_subway;
  const ac = stats.accessibility;
  const sc = stats.score;
  const cl = stats.clusters;
  const g = [];

  const walk = [
    ["Within 5 min", `${fmt(w["5"], 0)}%`],
    ["Within 10 min", `${fmt(w["10"], 0)}%`],
    ["Within 15 min", `${fmt(w["15"], 0)}%`],
    ["Mean walk time", `${fmt(stats.mean_walk_min, 1)} min`],
    ["Median walk time", `${fmt(stats.median_walk_min, 1)} min`],
  ];
  if (sub) walk.push(["Within 10 min, subway only", `${fmt(sub["10"], 0)}%`]);
  g.push({ title: "Walking to a train", rows: walk });

  if (a) g.push({
    title: "Walk plus wait",
    rows: [
      ["Within 5 min", `${fmt(a["5"], 0)}%`],
      ["Within 10 min", `${fmt(a["10"], 0)}%`],
      ["Within 15 min", `${fmt(a["15"], 0)}%`],
      ["Mean walk plus wait", `${fmt(stats.mean_access_min, 1)} min`],
      ["Mean wait for a train", `${fmt(stats.mean_wait_min, 1)} min`],
    ],
  });

  if (ac) {
    const rows = [
      [`Jobs reachable in ${fmt(ac.budget_min, 0)} min`,
       `${fmt(ac.weighted_jobs_reach / 1e6, 2)}M`],
      ["… of the city's jobs", `${fmt(100 * ac.weighted_share, 0)}%`],
    ];
    if (ac.weighted_jobs_time != null) rows.push(
      [`Minutes to ${fmt(100 * ac.threshold_share, 0)}% of the jobs`,
       `${fmt(ac.weighted_jobs_time, 0)} min`],
      ["… slowest tenth of tracts", `${fmt(ac.p90_jobs_time, 0)} min`],
      [`… tracts needing over ${fmt(ac.budget_min, 0)} min`,
       `${fmt(ac.n_past_budget)} of ${fmt(stats.n_geographies)}`],
      ["… tracts that never get there",
       `${fmt(ac.n_no_threshold)}`],
    );
    g.push({ title: "Reaching jobs", rows });
  }

  if (sc) {
    const rows = [
      ["People past the standard", `${fmt(sc.population_beyond_standard)}`],
      ["Excess person-minutes, citywide",
       `${fmt(sc.total_excess_person_minutes / 1e6, 1)}M`],
      ["Tracts at or inside the standard",
       `${fmt(sc.n_at_standard)} of ${fmt(sc.n_scored)}`],
    ];
    if (cl) rows.push(
      ["Contiguous blocks past it", `${fmt(cl.n_clusters)}`],
      ["… tracts in them", `${fmt(cl.n_tracts_beyond)}`],
      ["… that are a single tract", `${fmt(cl.n_single_tract_clusters)}`],
    );
    g.push({ title: "Under-service", rows });
  }

  return g;
}

// --- the prose --------------------------------------------------------------

// Shows why routing matters: the same people, the same stations, the only
// difference being straight-line versus walking the street network.
export function benchHtml(stats) {
  return `Within a half mile of the <em>subway</em> alone:
    <strong>${fmt(stats.pct_within_half_mile_subway_only, 0)}%</strong> on
    foot, versus ${fmt(stats.pct_within_half_mile_subway_straight, 0)}% as the
    crow flies. Straight-line distance overstates access by
    ${fmt(stats.half_mile_detour_penalty, 0)} points.`;
}

// Both framings of the commuter-rail assumption, together, because each one
// alone is misleading: the citywide share barely moves, since these tracts
// were mostly outside the 10-minute band anyway, while the people in them
// would walk twenty minutes further each way without it.
export function railHtml(stats) {
  const r = stats.commuter_rail_dependence;
  if (!r) return "";
  const gap = Math.abs(
    stats.pct_within_min["10"] - stats.pct_within_min_subway["10"]);
  return `For <strong>${fmt(r.population)}</strong> New Yorkers
    (${fmt(r.pct_of_population, 1)}%) the nearest train is the LIRR or
    Metro-North, which charge a separate fare. Discounting them moves the
    citywide 10-minute share by only ${fmt(gap, 1)} points — but costs those
    tracts a median <strong>${fmt(r.median_penalty_min, 1)} minutes</strong>
    each way.`;
}

// The full argument, for the method page.
export function validityHtml(stats) {
  const al = stats.alignment;
  if (!al) return "";
  const pc = al.precision[al.shipped];
  const cl = al.clump?.[al.shipped];
  const off = (al.disagree ?? []).filter((d) => d.overlap < 0.25);
  const offPop = off.reduce((a, d) => a + d.pop, 0);
  return `<p>${fmt(100 * pc["25"], 0)}% of the 25 worst-scoring tracts sit
    inside one of eight corridors the MTA and partners have actually
    proposed, against a ${fmt(100 * al.baseline, 0)}% baseline — the share a
    score with no predictive value would hit. ${fmt(al.robust.n_all)} tracts
    stay in the worst 50 under every one of ${fmt(al.robust.n_scores)}
    alternative weightings, out of ${fmt(al.robust.n_candidates)} candidate
    scores measured; those are the findings that survive the weighting rather
    than depending on it.</p>`
    + (cl?.precision == null ? "" : `<p>A rail line serves a strip, not a
    tract, so the figure that gates the build is the clumped one: the worst
    ${fmt(al.clump_rule?.probe_k)} tracts form ${fmt(cl.n_blocks)} blocks of
    ${fmt(al.clump_rule?.min_tracts)} or more, holding ${fmt(cl.n_tracts)} of
    those tracts between them, and
    <strong>${fmt(100 * cl.precision, 0)}%</strong> of those fall in a
    proposed corridor. Grouping <em>raises</em> agreement, which is the
    direction it should move if the scattered tracts were noise. The gate is
    the ${fmt(100 * al.baseline, 0)}% baseline plus
    ${fmt(100 * (al.clump_rule?.margin ?? 0), 0)} points
    (${fmt(100 * (al.clump_rule?.floor ?? 0), 0)}%), derived rather than set
    near where the score already sits — a floor chosen that way would quietly
    make agreement with the agencies a target.</p>`)
    + `<p>Agreement is a check, not a goal. Official plans reflect need
    filtered through cost, politics and the availability of an existing
    right-of-way, so a place with no plan is not a place with no need, and
    this test never counts one against the score. It tests whether the score
    is better than noise, not whether it matches the MTA.</p>`
    + (off.length === 0 ? "" : `<p><strong>Where they differ.</strong>
    ${fmt(off.length)} of the ${fmt(cl?.n_blocks)} blocks sit almost entirely
    outside any proposal, holding ${fmt(offPop)} people. That is consistent
    with the constraint above: the plans cluster on existing rights-of-way,
    and northeastern Queens has none.</p>`);
}

// --- tables -----------------------------------------------------------------

export function boroughTable(stats) {
  const rows = stats.by_borough.map((r) =>
    `<tr><td>${esc(r.boroname)}</td><td class="num">${fmt(r.pop)}</td>
     <td class="num">${fmt(r.mean_walk_min, 1)}</td>
     <td class="num">${fmt(r.pct_within_10, 0)}%</td></tr>`).join("");
  return `
    <table>
      <caption>Walking access to a train station, by borough.
        Population-weighted.</caption>
      <thead><tr><th>Borough</th><th class="num">Population</th>
        <th class="num">Mean walk (min)</th>
        <th class="num">Within 10 min</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// The corridor-scale finding, which no per-tract ranking can produce: the
// largest contiguous runs of tracts past the standard, and how many people
// live in them. This is the number an agency would recognize.
export function clusterTable(stats) {
  const c = stats.clusters;
  if (!c || !c.largest?.length) return "";
  const rows = c.largest.map((r) =>
    `<tr><td>${esc(r.where.join(", "))}</td>
     <td class="num">${fmt(r.n_tracts)}</td>
     <td class="num">${fmt(r.pop)}</td></tr>`).join("");
  return `
    <table>
      <caption>Contiguous blocks of tracts past the
        ${fmt(floorMin(stats), 0)}-minute standard, largest first.
        ${fmt(c.n_clusters)} blocks hold ${fmt(c.n_tracts_beyond)} tracts
        between them, and ${fmt(c.n_single_tract_clusters)} of those blocks
        are a single isolated tract — a complaint rather than a project.
        Adjacency is land adjacency: the census polygons exclude water, so
        tracts facing each other across the East River do not touch.</caption>
      <thead><tr><th>Where</th><th class="num">Tracts</th>
        <th class="num">Population</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// Every block the score ranks worst, with the share of it that falls inside a
// proposed corridor. The low-overlap rows are the hypothesis this project has
// to offer, so they are published rather than summarized away.
export function disagreeTable(stats) {
  const d = stats.alignment?.disagree;
  if (!d?.length) return "";
  const rows = d.map((b) =>
    `<tr><td>${esc(b.where.join(", "))}</td><td>${esc(b.boro)}</td>
     <td class="num">${fmt(b.n_tracts)}</td>
     <td class="num">${fmt(b.pop)}</td>
     <td class="num">${fmt(100 * b.overlap, 0)}%</td></tr>`).join("");
  return `
    <table>
      <caption>The worst-scoring blocks, and how much of each one a proposed
        corridor covers. 0% is not a failing block; it is a place with a
        measured gap and nothing proposed for it.</caption>
      <thead><tr><th>Where</th><th>Borough</th><th class="num">Tracts</th>
        <th class="num">Population</th>
        <th class="num">In a corridor</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// --- attribution ------------------------------------------------------------

export function sourceHtml(stats) {
  return Object.values(stats.attribution ?? {}).map(esc).join(" · ");
}

// AGPL-3.0 section 13: a networked instance must prominently offer its
// corresponding source. `config.SOURCE_URL` feeds `stats.source_url`; with no
// URL set (a local run) we say the source ships alongside instead. The scheme
// is checked rather than assumed, because this builds a link out of a value
// that arrived in a data file.
export function licenseHtml(stats) {
  if (!stats.license) return "";
  const url = (stats.source_url || "").trim();
  const tail = /^https:\/\/[^\s]+$/i.test(url)
    ? `<a href="${esc(url)}" rel="noopener">source code</a>`
    : "source in this distribution";
  return `${esc(stats.license)} — ${tail}`;
}
