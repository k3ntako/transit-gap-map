// What the two document pages -- stats.html and about.html -- share: the
// theme, the links between pages, and fetching the one file they read.
//
// The theme travels in the query string rather than the hash, so the hash
// stays available as a section anchor (stats.html#boroughs). An unpinned
// visit follows the reader's OS setting, the same rule the map applies.

import { esc } from "./stats.js";

const $ = (s) => document.querySelector(s);

export const set = (sel, html) => {
  const el = $(sel);
  if (el) el.innerHTML = html;
};

const params = new URLSearchParams(location.search);
const asked = params.get("t");
let pinned = asked === "dark" || asked === "light";
let theme = pinned ? asked
  : matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";

function applyTheme(next, pin = true) {
  theme = next;
  pinned = pin;
  document.documentElement.setAttribute("data-theme", theme);
  $("#themebtn").textContent = theme === "dark" ? "Light mode" : "Dark mode";
  // Carried onto every cross-page link so a reader does not get flashed on
  // the way, and written into this page's own URL so a reload keeps it. The
  // map reads its theme from the hash, the document pages from the query.
  for (const a of document.querySelectorAll("a[data-page]")) {
    const page = a.dataset.page;
    a.href = page === "index.html"
      ? `index.html${pinned ? `#t=${theme}` : ""}`
      : `${page}${pinned ? `?t=${theme}` : ""}`;
  }
  if (pinned) {
    history.replaceState(null, "",
      `${location.pathname}?t=${theme}${location.hash}`);
  }
}

// Wires the theme toggle and resolves to the parsed stats.json, or to null
// after writing a "not built yet" note into `errorSel`.
export async function initDocPage(errorSel) {
  applyTheme(theme, pinned);
  $("#themebtn").addEventListener("click", () =>
    applyTheme(theme === "dark" ? "light" : "dark"));

  try {
    const r = await fetch("data/stats.json");
    if (!r.ok) throw new Error(`data/stats.json: ${r.status}`);
    return await r.json();
  } catch (err) {
    set(errorSel,
      `<strong>Data not built yet.</strong> Run <code>make all</code> first
       &mdash; this page reads the same <code>web/data/stats.json</code> the
       map does. <span class="sub">${esc(err.message)}</span>`);
    return null;
  }
}

// A section only exists once the fetch has resolved, so the browser's own
// anchor scroll has already given up by the time there is anything to scroll
// to. Call after rendering.
export function scrollToHash() {
  if (location.hash.length > 1) {
    const el = document.querySelector(location.hash);
    if (el) el.scrollIntoView();
  }
}
