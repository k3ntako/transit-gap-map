#!/usr/bin/env bash
# Give the deploy workflow what it needs, from the values that already exist:
# the AWS names from infra/site's outputs as repository variables, and the
# Census key from .env as a repository secret. Safe to re-run; it overwrites.
#
#   scripts/github-setup.sh
#
# Needs `gh auth login` and the deployer's AWS profile. Run it after the first
# infra/site apply, and again whenever an apply changes an output.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export AWS_PROFILE="${AWS_PROFILE:-transit-gap-map}"

# The repository is read from config.SOURCE_URL, the link the deployed pages
# show readers to satisfy AGPL section 13, so the workflow's home and the
# advertised source cannot quietly disagree.
source_url="$(sed -nE 's/^SOURCE_URL = "(.*)"/\1/p' "${ROOT}/config.py")"
REPO="${source_url#https://github.com/}"
if [[ -z "$REPO" || "$REPO" == "$source_url" ]]; then
  echo "Could not read a GitHub repository from config.SOURCE_URL ('${source_url}')." >&2
  exit 1
fi

out() { terraform -chdir="${ROOT}/infra/site" output -raw "$1"; }
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
# The role's name is fixed by infra/bootstrap; it is rebuilt here rather than
# read from bootstrap's state, which the deployer cannot read.
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/transit-gap-map-github-deploy"

echo "Setting variables on ${REPO}"
gh variable set AWS_ROLE_ARN    --repo "$REPO" --body "$ROLE_ARN"
gh variable set SITE_BUCKET     --repo "$REPO" --body "$(out site_bucket)"
gh variable set DISTRIBUTION_ID --repo "$REPO" --body "$(out distribution_id)"
gh variable set SITE_URL        --repo "$REPO" --body "$(out site_url)"

# The worktree or checkout this runs from may not hold .env; ENV_FILE points
# at another one.
ENV_FILE="${ENV_FILE:-${ROOT}/.env}"
key="$(sed -nE 's/^CENSUS_API_KEY=["'\'']?([^"'\'']*)["'\'']?$/\1/p' "$ENV_FILE" 2>/dev/null || true)"
if [[ -n "$key" ]]; then
  # Through stdin, so the key never appears in the process list.
  printf '%s' "$key" | gh secret set CENSUS_API_KEY --repo "$REPO"
  echo "Set secret CENSUS_API_KEY from ${ENV_FILE#"${ROOT}"/}"
else
  echo "No CENSUS_API_KEY in ${ENV_FILE}; the secret was not changed." >&2
  echo "Set it with: ENV_FILE=/path/to/.env $0   or   gh secret set CENSUS_API_KEY --repo ${REPO}" >&2
fi

echo
gh variable list --repo "$REPO"
