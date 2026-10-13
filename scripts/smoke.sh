#!/usr/bin/env bash
# Check the live site against the files that were just published.
#
#   scripts/smoke.sh               # after a full publish
#   scripts/smoke.sh --code-only   # after publish.sh --code-only
#
# It fails on what would break the map for a reader and only reports what
# would not. Failing: a page or payload that is missing, differs byte for byte
# from web/, or arrives uncompressed; a Cache-Control that lets a browser keep
# a stale map. Reported: each payload's size over the wire, beside the
# gzipped budget config.py holds it to.
#
# The URL comes from SITE_URL in CI and from `terraform output` on a laptop.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB="${ROOT}/web"

CODE_ONLY=0
[[ "${1:-}" == "--code-only" ]] && CODE_ONLY=1

if [[ -z "${SITE_URL:-}" ]]; then
  if [[ -z "${GITHUB_ACTIONS:-}" ]]; then
    export AWS_PROFILE="${AWS_PROFILE:-transit-gap-map}"
  fi
  SITE_URL="$(terraform -chdir="${ROOT}/infra/site" output -raw site_url)"
fi
SITE_URL="${SITE_URL%/}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

failures=0
fail() { echo "  FAIL  $*"; failures=$((failures + 1)); }
ok()   { echo "  ok    $*"; }

# Byte-identical to the local copy, decompressed. That proves the upload, the
# invalidation and the content type all worked, rather than only "it is 200".
same_as_local() {
  local path="$1" local_file="$2"
  local out="${TMP}/body"
  if ! curl -fsS --compressed -o "$out" "${SITE_URL}/${path}"; then
    fail "/${path}: request failed"
    return
  fi
  if cmp -s "$out" "$local_file"; then
    ok "/${path}"
  else
    fail "/${path}: differs from ${local_file#"${ROOT}"/}"
  fi
}

echo "Pages at ${SITE_URL}"
same_as_local "" "${WEB}/index.html"
for f in "${WEB}"/*.html "${WEB}"/*.js "${WEB}"/*.css; do
  same_as_local "$(basename "$f")" "$f"
done

headers() { curl -fsS -o /dev/null -D - -H "Accept-Encoding: gzip" "$1" | tr -d '\r'; }
# A missing header is an answer (""), not an error, under pipefail.
header() { { grep -i "^$1:" || true; } | head -1 | cut -d' ' -f2- ; }

cc="$(headers "${SITE_URL}/index.html" | header cache-control)"
if [[ "$cc" == "no-cache" ]]; then
  ok "browsers revalidate (Cache-Control: ${cc})"
else
  fail "Cache-Control is '${cc}', expected 'no-cache'; a browser could keep an old map"
fi

if [[ "$CODE_ONLY" -eq 0 ]]; then
  echo
  echo "Data"
  # stats.json carries built_at, so matching it means this build's data is
  # the data being served, not the previous deploy's.
  same_as_local "data/stats.json" "${WEB}/data/stats.json"

  # stations.geojson has no budget, so no match is a normal answer here too --
  # and so is tract_cols.json, whose budget is the tracts.geojson one: that
  # 420 KB covers the pair, because a column moving between the two files
  # does not save a reader a byte.
  budget() { { grep -E "^BUDGET_$1_WIRE_KB" "${ROOT}/config.py" || true; } | sed -E 's/.*= *([0-9.]+).*/\1/'; }
  printf "  %-24s %10s %10s  %s\n" "" "wire KB" "budget" "encoding"
  for f in tracts.geojson tract_cols.json walksheds.geojson bus_ftn.geojson \
           siting.geojson stations.geojson hubs.json; do
    [[ -f "${WEB}/data/${f}" ]] || continue
    url="${SITE_URL}/data/${f}"
    enc="$(headers "$url" | header content-encoding)"
    wire="$(curl -fsS -o /dev/null -H "Accept-Encoding: gzip" -w '%{size_download}' "$url")"
    key="$(echo "${f%%.*}" | tr '[:lower:]' '[:upper:]')"
    b="$(budget "$key")"
    printf "  %-24s %10s %10s  %s\n" "$f" "$(awk -v w="$wire" 'BEGIN { printf "%.1f", w / 1024 }')" \
      "${b:--}" "${enc:-none}"
    if [[ "$enc" != "gzip" ]]; then
      fail "${f} arrived uncompressed. If publish.sh set application/json, CloudFront"
      echo "        skipped compression under load and cached that copy; re-run publish.sh."
    fi
  done
  echo "  (CloudFront's gzip level is not config.wire_kb's level 6, so a few KB"
  echo "  either side of the build's figure is expected; browsers get brotli, smaller.)"
fi

echo
if [[ "$failures" -gt 0 ]]; then
  echo "${failures} check(s) failed."
  exit 1
fi
echo "All checks passed: ${SITE_URL}"
