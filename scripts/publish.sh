#!/usr/bin/env bash
# Upload web/ to the site bucket, then switch CloudFront over to it.
#
#   scripts/publish.sh               # pages and data (run `make all` first)
#   scripts/publish.sh --code-only   # pages, scripts and styles only; the
#                                    # data already on the site is left alone
#
# The same script runs in two places. In GitHub Actions the bucket and
# distribution come from repository variables and the credentials from the
# deploy role. On a laptop they come from `terraform output` in infra/site
# and the `transit-gap-map` AWS profile.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB="${ROOT}/web"

CODE_ONLY=0
case "${1:-}" in
  "")          ;;
  --code-only) CODE_ONLY=1 ;;
  *)           echo "Usage: $0 [--code-only]" >&2; exit 2 ;;
esac

if [[ -z "${GITHUB_ACTIONS:-}" ]]; then
  export AWS_PROFILE="${AWS_PROFILE:-transit-gap-map}"
fi

site_output() {
  if ! terraform -chdir="${ROOT}/infra/site" output -raw "$1" 2>/dev/null; then
    echo "Could not read '$1' from infra/site. On a new machine, run:" >&2
    echo "  infra/site/apply.sh init" >&2
    return 1
  fi
}
if [[ -z "${SITE_BUCKET:-}" ]]; then SITE_BUCKET="$(site_output site_bucket)"; fi
if [[ -z "${DISTRIBUTION_ID:-}" ]]; then DISTRIBUTION_ID="$(site_output distribution_id)"; fi

# Every file the pages fetch, except hubs.json, which app.js treats as
# optional. A build that stopped partway would otherwise publish a map that
# loads and then fails in the reader's browser.
REQUIRED=(
  tracts.geojson tract_cols.json stations.geojson walksheds.geojson
  bus_ftn.geojson siting.geojson stats.json palette.json walk_surface.json
  walk_surface.png
)
if [[ "$CODE_ONLY" -eq 0 ]]; then
  missing=()
  for f in "${REQUIRED[@]}"; do
    [[ -f "${WEB}/data/${f}" ]] || missing+=("$f")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "web/data is incomplete (missing: ${missing[*]}). Run \`make all\` first," >&2
    echo "or pass --code-only to publish the pages without touching the data." >&2
    exit 1
  fi
  echo "Publishing pages and data built $(python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["built_at"])' "${WEB}/data/stats.json")"
else
  echo "Publishing pages only; data/ on the site is unchanged"
fi
echo "  to s3://${SITE_BUCKET}"
echo

# Data first, then pages, then the invalidation. CloudFront holds every file
# until it is invalidated, so readers see none of this until the last step,
# and then see all of it at once.
if [[ "$CODE_ONLY" -eq 0 ]]; then
  # .geojson gets an explicit type because guessing gives it
  # application/octet-stream, and CloudFront compresses by type: its list has
  # application/json and not application/geo+json. Uncompressed, the tracts
  # file alone downloads at about five times config.BUDGET_TRACTS_WIRE_KB.
  # MapLibre and fetch().json() read the
  # body either way. scripts/smoke.sh checks that it arrived compressed.
  aws s3 sync "${WEB}/data" "s3://${SITE_BUCKET}/data" --delete --no-progress \
    --exclude "*.geojson" --exclude ".gitkeep"
  aws s3 sync "${WEB}/data" "s3://${SITE_BUCKET}/data" --delete --no-progress \
    --exclude "*" --include "*.geojson" --content-type "application/json"
fi

aws s3 sync "${WEB}" "s3://${SITE_BUCKET}" --delete --no-progress \
  --exclude "data/*" --exclude ".DS_Store"

# "/*" is one path for billing purposes, and the first 1,000 a month are free.
echo
echo "Invalidating the CloudFront cache"
inv="$(aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" \
  --paths "/*" --query Invalidation.Id --output text)"
aws cloudfront wait invalidation-completed --distribution-id "$DISTRIBUTION_ID" --id "$inv"
echo "Done. CloudFront is serving the new files."
