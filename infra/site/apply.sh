#!/usr/bin/env bash
# The site's bucket and CloudFront, run as the DEPLOYER (not an admin):
#
#   infra/site/apply.sh plan      # preview
#   infra/site/apply.sh           # apply; asks before changing anything
#   infra/site/apply.sh init      # only connect to the state, e.g. on a new
#                                 # machine, so scripts/publish.sh can read
#                                 # the outputs
#
# Uses the `transit-gap-map` AWS profile that infra/bootstrap/new-key.sh
# writes; set AWS_PROFILE to override.
set -euo pipefail

cd "$(dirname "$0")"

export AWS_PROFILE="${AWS_PROFILE:-transit-gap-map}"
PROJECT="transit-gap-map"
REGION="${AWS_REGION:-us-east-1}"
ACTION="${1:-apply}"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
echo "Account: ${ACCOUNT}"
echo "As:      $(aws sts get-caller-identity --query Arn --output text)"
echo

terraform init -input=false -reconfigure \
  -backend-config="bucket=${PROJECT}-tfstate-${ACCOUNT}" \
  -backend-config="region=${REGION}" >/dev/null
echo "Connected to s3://${PROJECT}-tfstate-${ACCOUNT}/site.tfstate"

case "$ACTION" in
  init)  exit 0 ;;
  plan)  terraform plan -var="region=${REGION}" ;;
  apply) terraform apply -var="region=${REGION}" ;;
  *)     echo "Usage: $0 [plan|apply|init]" >&2; exit 2 ;;
esac

if [[ "$ACTION" == "apply" ]]; then
  echo
  echo "Site: $(terraform output -raw site_url)"
  echo
  echo "Next, if the GitHub settings are not set yet or these values changed:"
  echo "  scripts/github-setup.sh"
fi
