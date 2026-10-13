// The stats page: the figures that used to sit in the map's title card, and
// the full list that used to be the method page's "Every number" section. The
// card had shrunk from fifteen rows to four and still competed with the map
// for the reader's first look, so the map page now carries no figures at all
// and this is where they live.
//
// Everything is rendered by stats.js from one fetch of stats.json, the same
// as about.html, so no number on this page is typed in by hand.

import {
  fmt, esc, facts, statGroups, boroughBars, boroughTable, sourceHtml,
  licenseHtml,
} from "./stats.js";
import { initDocPage, set, scrollToHash } from "./doc.js";

function factsHtml(stats) {
  return facts(stats).map((f) => `
    <div class="fact">
      <p class="fv">${f.value}</p>
      <p class="fl">${f.label}</p>
      <p class="fb">${f.body}</p>
    </div>`).join("");
}

function statGroupHtml(stats) {
  return statGroups(stats).map((g) => `
    <div class="statgroup">
      <p class="grouplabel">${esc(g.title)}</p>
      ${g.rows.map(([k, v]) => `<div class="statrow">
        <span class="k">${k}</span><span class="v">${v}</span></div>`).join("")}
    </div>`).join("");
}

// "2023/acs/acs5" is the Census API path; a reader wants the survey's name.
function surveyName(ds) {
  const m = /^(\d{4})\/acs\/acs5$/.exec(ds ?? "");
  return m ? `the ${m[1]} American Community Survey 5-year estimates`
    : "Census estimates";
}

async function main() {
  const stats = await initDocPage("#s-lede");
  if (!stats) return;

  set("#s-lede", `Facts and figures on walking access to New York's trains,
    across ${fmt(stats.total_population)} people in
    ${fmt(stats.n_geographies)} areas, each one a census tract. Population
    is from
    ${surveyName(stats.census_dataset)}.`);
  set("#s-facts", factsHtml(stats));
  set("#s-bars", boroughBars(stats));
  set("#s-boro", boroughTable(stats));
  set("#s-groups", statGroupHtml(stats));
  set("#s-src", sourceHtml(stats));
  set("#s-built", esc((stats.built_at || "").slice(0, 10)));
  set("#s-license", licenseHtml(stats));
  scrollToHash();
}

main();
