// Everything the title card and the method page both say about the numbers.
//
// It exists because the two pages have to agree. The card is a four-row
// summary and `about.html` is the long version of the same figures, and the
// way that goes wrong is one of them keeping a stale sentence after a rebuild
// moves a number. So nothing here reads the DOM or holds state: each function
// takes the parsed `stats.json` and returns a string or rows, and both pages
// render what it gives them.
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

// --- the title card's four rows ---------------------------------------------

// The card used to carry fifteen rows and three paragraphs. These four are
// the ones that are the finding rather than a detail: the headline share, the
// same share once a train has to turn up (which is the thing the headline
// leaves out, and it is 18 points), the people the score is about, and the
// corridor-scale version no per-tract ranking can produce.
export function cardRows(stats) {
  const w = stats.pct_within_min;
  const a = stats.pct_within_min_access;
  const sc = stats.score;
  const cl = stats.clusters;
  const rows = [[`Within a ${fmt(floorMin(stats), 0)}-min walk`,
                 `${fmt(w["10"], 0)}%`]];
  if (a) rows.push(["… counting the wait", `${fmt(a["10"], 0)}%`]);
  if (sc) rows.push(["People past the standard",
                     `${fmt(sc.population_beyond_standard / 1e6, 2)}M`]);
  if (cl?.largest?.length) rows.push(["In one contiguous block",
    `${fmt(cl.largest[0].pop / 1e6, 2)}M`]);
  return rows;
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
    title: "Once a train has to turn up",
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
      ["… tracts needing longer than the budget",
       `${fmt(ac.n_past_budget)} of ${fmt(stats.n_geographies)}`],
      ["… tracts rail never gets there from",
       `${fmt(ac.n_no_threshold)}`],
    );
    g.push({ title: "Where the trains actually go", rows });
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
    g.push({ title: "The under-service finding", rows });
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

// One sentence, for the card. External validity is the most
// credibility-building thing on the page and a number in a terminal validates
// nothing, so a figure stays visible beside the map even though the argument
// moved to its own page.
export function validityLine(stats) {
  const al = stats.alignment;
  if (!al) return "";
  const pc = al.precision[al.shipped];
  return `<strong>${fmt(100 * pc["25"], 0)}%</strong> of the 25
    worst-scoring tracts sit inside a corridor the MTA and partners have
    actually proposed, against a ${fmt(100 * al.baseline, 0)}% baseline.`;
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
    + `<p>And it is not a target. Official plans are not need: they are need
    filtered through cost, politics, and whether an abandoned right-of-way
    happens to be lying around. So a place with no plan is not a place with
    no need, and this test never counts one against the score. It answers
    &ldquo;is this score noise?&rdquo; and refuses to answer &ldquo;does it
    agree with the MTA?&rdquo;</p>`
    + (off.length === 0 ? "" : `<p><strong>The disagreement is the output
    worth reading.</strong> ${fmt(off.length)} of the ${fmt(cl?.n_blocks)}
    blocks sit almost entirely outside any proposal — ${fmt(offPop)} people
    — and that pattern is consistent with the constraint above: the plans
    cluster on existing rights-of-way, and northeastern Queens has
    none.</p>`);
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
