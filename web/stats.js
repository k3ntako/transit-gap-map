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
    label: "is the longest walk in a high-density area",
    body: `An area in ${esc(far.ntaname)}, ${esc(far.boroname)}, with
      ${fmt(far.pop)} residents at ${fmt(far.density_km2)} people per km².
      Among the densest third of areas, none is farther from a train.`,
  });

  const big = stats.clusters?.largest?.[0];
  if (big) f.push({
    value: `${fmt(big.pop / 1e6, 2)}M`,
    label: `people live in one continuous zone past the ${fl}-minute mark`,
    body: `${fmt(big.n_tracts)} neighboring areas, including
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
      tenth of areas it takes ${fmt(ac.p90_jobs_time, 0)} minutes.`,
  });

  const al = stats.alignment;
  const pc = al?.precision?.[al.shipped]?.["25"];
  if (pc != null) f.push({
    value: `${fmt(100 * pc, 0)}%`,
    label: "of the 25 most under-served areas lie in a proposed rail corridor",
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
      ["… slowest tenth of areas", `${fmt(ac.p90_jobs_time, 0)} min`],
      [`… areas needing over ${fmt(ac.budget_min, 0)} min`,
       `${fmt(ac.n_past_budget)} of ${fmt(stats.n_geographies)}`],
      ["… areas that never get there",
       `${fmt(ac.n_no_threshold)}`],
    );
    g.push({ title: "Reaching jobs", rows });
  }

  if (sc) {
    const rows = [
      ["People past the standard", `${fmt(sc.population_beyond_standard)}`],
      ["Excess person-minutes, citywide",
       `${fmt(sc.total_excess_person_minutes / 1e6, 1)}M`],
      ["Areas at or inside the standard",
       `${fmt(sc.n_at_standard)} of ${fmt(sc.n_scored)}`],
    ];
    if (cl) rows.push(
      ["Under-served zones", `${fmt(cl.n_clusters)}`],
      ["… areas in them", `${fmt(cl.n_tracts_beyond)}`],
      ["… that are a single area", `${fmt(cl.n_single_tract_clusters)}`],
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
    areas a median <strong>${fmt(r.median_penalty_min, 1)} minutes</strong>
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
  return `<p>${fmt(100 * pc["25"], 0)}% of the 25 worst-scoring areas sit
    inside one of eight corridors the MTA and partners have actually
    proposed, against a ${fmt(100 * al.baseline, 0)}% baseline — the share a
    score with no predictive value would hit. ${fmt(al.robust.n_all)} areas
    stay in the worst 50 under every one of ${fmt(al.robust.n_scores)}
    alternative weightings, out of ${fmt(al.robust.n_candidates)} candidate
    scores measured; those are the findings that survive the weighting rather
    than depending on it.</p>`
    + (cl?.precision == null ? "" : `<p>A rail line serves a strip, not a
    single area, so the figure that gates the build is the grouped one: the
    worst ${fmt(al.clump_rule?.probe_k)} areas form ${fmt(cl.n_blocks)} zones
    of ${fmt(al.clump_rule?.min_tracts)} or more, holding ${fmt(cl.n_tracts)}
    of those areas between them, and
    <strong>${fmt(100 * cl.precision, 0)}%</strong> of those fall in a
    proposed corridor. Grouping <em>raises</em> agreement, which is the
    direction it should move if the scattered areas were noise. The gate is
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
    ${fmt(off.length)} of the ${fmt(cl?.n_blocks)} zones sit almost entirely
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
      <caption>Under-served zones: neighboring areas all past the
        ${fmt(floorMin(stats), 0)}-minute standard, largest first.
        ${fmt(c.n_clusters)} zones hold ${fmt(c.n_tracts_beyond)} areas
        between them, and ${fmt(c.n_single_tract_clusters)} of those zones
        are a single isolated area — a complaint rather than a project.
        Neighbors share a border on land: the census boundaries exclude
        water, so areas facing each other across the East River do not
        touch.</caption>
      <thead><tr><th>Where</th><th class="num">Areas</th>
        <th class="num">Population</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// Mode keys are the pipeline's, so the page needs its own spelling of them --
// short in a table cell, and as it would be said in a sentence.
const MODE_LABEL = {
  subway: "Subway", sir: "SIR", lirr: "LIRR", mnr: "Metro-North",
};
const MODE_PHRASE = {
  subway: "on the subway", sir: "on the Staten Island Railway",
  lirr: "on the LIRR", mnr: "on Metro-North",
};

// Where a new foot crossing would help most. Ranked per meter of span and
// never priced, and the caution is in the copy rather than in a footnote.
export function crossingsHtml(stats) {
  const c = stats.siting?.crossings;
  if (!c?.crossings?.length) return "";
  const best = c.crossings[0];
  return `The barrier probes this map is tested against — Newtown Creek,
    Hunts Point, Coney Island Creek — are places close together in a straight
    line and far apart on foot. Searching for every such pair gives the
    opposite question: not where to put a station, but where a
    <strong>footbridge</strong> would do some of the same work. Candidates are
    lattice pairs within ${fmt(c.max_span_m, 0)} m of each other whose walking
    route is at least ${fmt(c.min_ratio, 0)}&times; longer, and
    ${fmt(c.n_unreachable)} of the ${fmt(c.n_candidates)} that are worth
    anything are not connected at all inside two kilometers of walking. The
    best one, a ${fmt(best.span_m)} m span in
    ${esc(best.where)}, removes ${fmt(best.excess_min_cut / 1000, 0)}k
    person-minutes of walking.
    <strong>Cost is ranked, never priced.</strong> A footbridge over a rail
    cut and a crossing of a navigable channel differ by orders of magnitude,
    so the ordering is person-minutes removed per meter of span. And note what
    it does not know: this measures the length of a span, not what is
    underneath it. Several of these cross water. Look before quoting one.`;
}

export function crossingsTable(stats) {
  const c = stats.siting?.crossings;
  if (!c?.crossings?.length) return "";
  const rows = c.crossings.map((r, i) =>
    `<tr><td class="num">${i + 1}</td><td>${esc(r.where)}</td>
     <td>${esc(r.boro)}</td>
     <td class="num">${fmt(r.span_m)} m</td>
     <td class="num">${r.detour == null ? "no route"
       : `${fmt(r.detour, 0)}×`}</td>
     <td class="num">${fmt(r.excess_min_cut / 1000, 0)}k</td>
     <td class="num">${fmt(r.min_per_meter, 0)}</td></tr>`).join("");
  return `
    <table>
      <caption>Hypothetical foot crossings, ranked by person-minutes of
        walking removed per meter of span. &ldquo;On foot today&rdquo; is how
        much further the walking route is than the straight line;
        &ldquo;no route&rdquo; means the two sides are more than two
        kilometers apart on foot. Benefits further than
        ${fmt(c.reach_m / 1000, 1)} km from a crossing are not counted, so
        every figure here is a floor. Nobody has proposed these.</caption>
      <thead><tr><th class="num">#</th><th>Where</th><th>Borough</th>
        <th class="num">Span</th><th class="num">On foot today</th>
        <th class="num">Person-min removed</th>
        <th class="num">Per meter</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// Bikes: the result the roadmap predicted, measured. Published with its
// shape intact, because a single citywide ratio hides the whole finding.
export function bikeHtml(stats) {
  const b = stats.bike;
  if (!b?.bands?.length) return "";
  const fl = fmt(b.floor_minutes, 0);
  const worst = b.bands[b.bands.length - 1];
  const mid = b.bands[1];
  return `Swapping the walk for a ride is the same computation over the
    cycling network at ${fmt(b.speed_mps * 3.6, 0)} km/h, and the answer has
    a shape worth more than its average. Of the
    <strong>${fmt(b.pop_beyond_walking)}</strong> people more than ${fl}
    minutes' walk from a train, ${fmt(b.pct_rescued, 0)}% are inside ${fl}
    minutes by bike. But that figure is carried almost entirely by the
    middle: everyone in the ${fmt(mid.walk_from, 0)}–${fmt(mid.walk_to, 0)}
    minute band gets inside it, while of the ${fmt(worst.pop)} people in
    areas over ${fmt(worst.walk_from, 0)} minutes' walk,
    <strong>${fmt(worst.pop_inside_by_bike)}</strong> do —
    ${fmt(100 * worst.pop_inside_by_bike / worst.pop, 1)}%. Those areas are
    far by street network, not merely far on foot, so a bike moves them from
    a very long walk to a long ride. That is the measured case against
    treating bike infrastructure as a substitute for a line, and it is also
    the case <em>for</em> bike infrastructure in the middle band, where it
    plainly works.`;
}

export function bikeTable(stats) {
  const b = stats.bike;
  if (!b?.bands?.length) return "";
  const rows = b.bands.map((r) => {
    const band = r.walk_to == null
      ? `over ${fmt(r.walk_from, 0)} min`
      : `${fmt(r.walk_from, 0)}–${fmt(r.walk_to, 0)} min`;
    return `<tr><td>${band}</td>
      <td class="num">${fmt(r.pop)}</td>
      <td class="num">${fmt(r.median_walk_min, 1)}</td>
      <td class="num">${fmt(r.median_bike_min, 1)}</td>
      <td class="num">${fmt(100 * r.pop_inside_by_bike / r.pop, 0)}%</td></tr>`;
  }).join("");
  return `
    <table>
      <caption>By how far the walk is today. The last column is the share of
        each band's population that is within
        ${fmt(b.floor_minutes, 0)} minutes of a station by bike. Cycling is
        over every street rather than the protected network alone, so these
        are best cases, and nothing here accounts for having somewhere to
        leave the bike.</caption>
      <thead><tr><th>Walk today</th><th class="num">Population</th>
        <th class="num">Median walk</th><th class="num">Median ride</th>
        <th class="num">Inside ${fmt(b.floor_minutes, 0)} min by bike</th>
        </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// Where one more station would help most, and the two reasons the ranking is
// in person-minutes rather than in people moved.
export function sitingHtml(stats) {
  const t = stats.siting;
  if (!t?.sites?.length) return "";
  const fl = fmt(t.floor_minutes, 0);
  return `For each of
    <strong>${fmt(t.n_sites)}</strong> candidate sites on a
    ${fmt(t.spacing_m, 0)} m lattice, one hypothetical station is added and
    the whole map recomputed. A station can only change an answer within
    ${fl} minutes' walk of itself, so each pass stops there — which is what
    makes a question this size answerable in seconds rather than hours.
    Benefit is reported in the same unit as the under-service score, excess
    person-minutes removed, and that matters: the best site removes
    <strong>${fmt(t.best_excess_min_cut / 1000, 0)}k</strong> person-minutes
    of walking, and the best site that moves <em>nobody</em> inside the
    ${fl}-minute standard still removes
    <strong>${fmt(t.best_cut_moving_nobody / 1000, 0)}k</strong> — it cuts a
    25-minute walk to 14 without reaching 10, which a count of people moved
    scores at zero. The list below keeps only sites
    more than ${fmt(t.min_separation_m / 1000, 1)} km apart, so no two
    entries serve the same people; the map layer shows all of them.`;
}

export function sitingTable(stats) {
  const t = stats.siting;
  if (!t?.sites?.length) return "";
  const rows = t.sites.map((r, i) =>
    `<tr><td class="num">${i + 1}</td><td>${esc(r.where)}</td>
     <td>${esc(r.boro)}</td>
     <td class="num">${fmt(r.excess_min_cut / 1000, 0)}k</td>
     <td class="num">${fmt(r.pop_moved)}</td></tr>`).join("");
  return `
    <table>
      <caption>One more station, ranked by the excess walking it would
        remove. <strong>Nobody has proposed these.</strong> They are what
        this map's own numbers say, computed the same way for every site, and
        they count in no other figure on this page. A site is a point, not a
        line — a line is a connected sequence of points, and turning the
        first into the second is the part this project has not
        done.</caption>
      <thead><tr><th class="num">#</th><th>Where</th><th>Borough</th>
        <th class="num">Person-min removed</th>
        <th class="num">People moved inside ${fmt(stats.siting.floor_minutes, 0)} min</th>
        </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// Carless crossed with poverty, and the measured result is the opposite of
// the hypothesis it was built to test. Published as such: this project's rule
// is that a prediction that fails is a finding, not an embarrassment.
export function needCrossHtml(stats) {
  const c = stats.need_cross;
  if (!c?.quadrants?.length) return "";
  const q = (i) => c.quadrants[i] ?? {};
  const worst = [...c.quadrants].sort(
    (a, b) => (b.pct_past_standard ?? 0) - (a.pct_past_standard ?? 0))[0];
  const poor = q(3);
  const fl = fmt(floorMin(stats), 0);
  return `Having no car is an ambiguous signal on its own, and a quietly
    circular one: the Upper West Side has very few cars <em>because</em> the
    transit is excellent, so ranking need by carlessness alone scores the
    best-served neighborhoods in the city as the most dependent. Crossing it
    with poverty separates carless by choice from carless without means. Each
    area is placed against the citywide medians
    (${fmt(c.median_no_vehicle_pct, 0)}% of households without a car,
    ${fmt(c.median_poverty_pct, 0)}% below the poverty line), and the result
    is worth stating plainly because it is the opposite of what the crossing
    was built to find: <strong>&ldquo;carless without means&rdquo; is among
    the best-served groups in the city</strong>. Of its
    ${fmt(poor.pop)} people, ${fmt(poor.pct_past_standard, 0)}% live more than
    ${fl} minutes from a station, against
    <strong>${fmt(worst.pct_past_standard, 0)}%</strong> for
    ${esc((worst.name || "").toLowerCase())}. Poverty and carlessness
    concentrate in the dense core the subway was built around. So the crossing
    names who depends on transit, which is a real thing to know, and it does
    not name who is missing it. The
    ${fmt(poor.pop_past_standard)} people in both groups at once are the ones
    this map is about, and that is the number to carry rather than the share.`;
}

export function needCrossTable(stats) {
  const c = stats.need_cross;
  if (!c?.quadrants?.length) return "";
  const rows = c.quadrants.map((r) =>
    `<tr><td>${esc(r.name)}</td>
     <td class="num">${fmt(r.pop)}</td>
     <td class="num">${fmt(r.median_walk_min, 1)}</td>
     <td class="num">${fmt(r.pct_past_standard, 0)}%</td>
     <td class="num">${fmt(r.pop_past_standard)}</td></tr>`).join("");
  return `
    <table>
      <caption>Households without a car crossed with poverty, each area
        against the citywide median of both. The last column is the one the
        crossing was built for: people in that group who are also past the
        ${fmt(floorMin(stats), 0)}-minute standard.</caption>
      <thead><tr><th>Group</th><th class="num">Population</th>
        <th class="num">Median walk</th><th class="num">Past the standard</th>
        <th class="num">People past it</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// The elevator-priority table, and the deflating finding that comes with it.
//
// Two different numbers live here and the page has to keep them apart: the
// share of New Yorkers whose nearest station excludes them, and how much
// further a step-free rider has to walk. The first is a count over nearest
// stations; the second needs the whole map recomputed against the step-free
// stations only. Quoting the first as the second overstates the cost, because
// the real nearest step-free station may be a mile past the one you cannot
// use.
export function stepFreeTable(stats) {
  const sf = stats.step_free;
  if (!sf?.stations?.length) return "";
  const rows = sf.stations.map((r) =>
    `<tr><td>${esc(r.station)}</td><td>${esc(MODE_LABEL[r.mode] ?? r.mode)}</td>
     <td class="num">${fmt(r.pop)}</td>
     <td class="num">${fmt(r.n_geographies)}</td></tr>`).join("");
  return `
    <table>
      <caption>Stations without a step-free route, ranked by the population
        whose nearest station they are. <strong>${fmt(sf.top15_share, 0)}% of
        the affected population</strong> sits behind the worst 15 of them, and
        ${fmt(sf.n_stations_nearest_to_someone)} stations are somebody's
        nearest — so there is no short list of elevators that fixes this, and
        a &ldquo;top ten stations&rdquo; headline would say otherwise. Showing
        ${fmt(sf.stations.length)} rows for that reason. Step-free
        <em>by design</em> as of ${esc(sf.as_of ?? "")}: a route that was
        built, not one working today.</caption>
      <thead><tr><th>Station</th><th>Mode</th>
        <th class="num">People</th><th class="num">Areas</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

export function stepFreeHtml(stats) {
  const sf = stats.step_free;
  if (!sf) return "";
  const modes = Object.entries(sf.by_mode ?? {})
    .map(([m, [ok, n]]) => `${ok} of ${n} ${MODE_PHRASE[m] ?? m}`)
    .join(", ");
  return `<strong>${fmt(sf.n_step_free)} of ${fmt(sf.n_stations)}</strong>
    stations have a step-free route built (${esc(modes)}), and
    <strong>${fmt(sf.pct_nearest_not_step_free, 0)}%</strong> of New Yorkers
    &mdash; ${fmt(sf.pop_nearest_not_step_free)} people &mdash; have a nearest
    station that is not one of them. That is not the same as how much further
    they have to travel, which needs the map recomputed against the step-free
    stations alone: the median area's walk goes from
    ${fmt(sf.median_walk_min, 1)} to ${fmt(sf.median_step_free_min, 1)}
    minutes, <strong>+${fmt(sf.median_penalty_min, 1)} minutes</strong> at the
    median area. Past the ${fmt(floorMin(stats), 0)}-minute standard:
    ${fmt(sf.pct_beyond_standard, 0)}% of the city's population, and
    <strong>${fmt(sf.pct_beyond_standard_step_free, 0)}%</strong> if the
    station has to be step-free. It is a lens on the map and not a term in the
    score, because the score ranks places and its answer is new service, while
    this ranks stations and its answer is an elevator.`;
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
      <caption>The worst-scoring zones, and how much of each one a proposed
        corridor covers. 0% is not a failing zone; it is a place with a
        measured gap and nothing proposed for it.</caption>
      <thead><tr><th>Where</th><th>Borough</th><th class="num">Areas</th>
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
