// The method page. It renders one JSON file and draws nothing, which is the
// whole reason it is a separate page: the map's payloads are 2 MB and a
// reader who wants to know how a number was computed should not pay for them.
//
// Every figure comes from stats.js, the module the stats page also uses, so
// a number quoted here and the same number there are one function called
// twice.

import {
  fmt, esc, floorMin, benchHtml, railHtml, validityHtml, clusterTable,
  disagreeTable, needCrossHtml, needCrossTable, stepFreeHtml, stepFreeTable,
  sitingHtml, sitingTable, sourceHtml, licenseHtml,
} from "./stats.js";
import { initDocPage, set, scrollToHash } from "./doc.js";

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
    ${fmt(sc.n_at_standard)} of ${fmt(sc.n_scored)} populated areas are at or
    inside the standard and score zero. It replaced a product of percentile
    ranks, which the corridor test below scored at
    ${fmt(100 * (stats.alignment?.precision?.["ranks (original)"]?.["25"] ?? 0),
      0)}% against a
    ${fmt(100 * (stats.alignment?.baseline ?? 0), 0)}% random baseline &mdash;
    indistinguishable from noise.`;
}

// Why the margins are fetched at all, said once on the page that explains
// the method rather than on every tooltip.
function marginsHtml(stats) {
  const m = stats.margins;
  if (!m) return "";
  return `<strong>Every one of these shares is a survey estimate.</strong>
    The American Community Survey publishes a margin of error with each one,
    and for areas this small those margins are wide: the median area's
    transit-commute share carries &plusmn;${fmt(m.median_moe?.tcom, 1)}
    percentage points, and in
    <strong>${fmt(m.pct_unreliable, 0)}%</strong> of populated areas one of
    the two shares the map can weight by has a margin over 30% of the
    estimate itself. Those areas are flagged in the tooltip rather than
    dropped, because hiding an uncertain estimate replaces
    &ldquo;uncertain&rdquo; with &ldquo;zero&rdquo;. None of this reaches the
    score, which is built from population and walking time.`;
}

function waitHtml(stats) {
  const a = stats.pct_within_min_access;
  if (!a) return "";
  return `<strong>Waiting time is included.</strong> Living near a station
    says nothing about how often a train comes: the share of New Yorkers within ten minutes falls from
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
    ${fmt(ac.budget_min, 0)}-minute budget has a problem at the bottom of
    the distribution: it right-censors.
    <strong>${fmt(ac.n_past_budget)} of ${fmt(stats.n_geographies)}
    areas</strong> need longer than that to reach
    ${fmt(100 * ac.threshold_share, 0)}% of the city's jobs, so they all
    land near zero and can no longer be told apart &mdash; and they are the
    areas this map is meant to find. The budget also sits almost exactly at
    the median of what it cuts (${fmt(ac.median_jobs_time, 1)} minutes), so
    half the city is on the censored side. So the primary measure
    swaps the axes: fix the share of jobs, measure the minutes. The
    population-weighted answer is
    <strong>${fmt(ac.weighted_jobs_time, 0)} minutes</strong> to
    ${fmt(100 * ac.threshold_share, 0)}% of the city's jobs, and
    ${fmt(ac.p90_jobs_time, 0)} minutes for the slowest tenth of areas.`;
}

async function main() {
  const stats = await initDocPage("#m-method");
  if (!stats) return;

  set("#m-method", esc(stats.method ?? ""));
  set("#m-bench", benchHtml(stats));
  set("#m-score", scoreHtml(stats));
  set("#m-clusters", clusterTable(stats));
  set("#m-validity", validityHtml(stats));
  set("#m-disagree", disagreeTable(stats));
  set("#m-wait", waitHtml(stats));
  set("#m-censor", censorHtml(stats));
  set("#m-rail", railHtml(stats));
  set("#m-cross", needCrossHtml(stats));
  set("#m-cross-table", needCrossTable(stats));
  set("#m-margins", marginsHtml(stats));
  set("#m-stepfree", stepFreeHtml(stats));
  set("#m-stepfree-table", stepFreeTable(stats));
  set("#m-siting", sitingHtml(stats));
  set("#m-siting-table", sitingTable(stats));
  set("#m-src", sourceHtml(stats));
  set("#m-built", esc((stats.built_at || "").slice(0, 10)));
  set("#m-license", licenseHtml(stats));
  scrollToHash();
}

main();
