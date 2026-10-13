#!/usr/bin/env bash
# One-time (and rarely repeated) setup, run with the BOOTSTRAP profile, whose
# policy is in DEPLOY.md, step 1:
#
#   AWS_PROFILE=bootstrap infra/bootstrap/apply.sh          # plan, then ask
#   AWS_PROFILE=bootstrap infra/bootstrap/apply.sh plan     # plan only
#
# Safe to re-run: every step checks before it acts. See DEPLOY.md for where
# this sits in the order of things.
set -euo pipefail

cd "$(dirname "$0")"

PROJECT="transit-gap-map"
REGION="${AWS_REGION:-us-east-1}"
ACTION="${1:-apply}"

if [[ ! -f terraform.tfvars ]]; then
  echo "Missing infra/bootstrap/terraform.tfvars." >&2
  echo "  cp terraform.tfvars.example terraform.tfvars   # then set budget_email" >&2
  exit 1
fi

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
CALLER="$(aws sts get-caller-identity --query Arn --output text)"
BUCKET="${PROJECT}-tfstate-${ACCOUNT}"

echo "Account: ${ACCOUNT}"
echo "As:      ${CALLER}"
echo "State:   s3://${BUCKET}/bootstrap.tfstate"
echo

# Terraform cannot store its state in a bucket that does not exist yet, so the
# empty bucket is made here and state.tf adopts it with an import block.
# head-bucket exits 0 when it is ours, and non-zero both when it is missing
# and when someone else owns the name, so the error text tells those apart.
if ! err="$(aws s3api head-bucket --bucket "$BUCKET" 2>&1)"; then
  if [[ "$err" == *"404"* || "$err" == *"Not Found"* ]]; then
    echo "Creating state bucket ${BUCKET}"
    if [[ "$REGION" == "us-east-1" ]]; then
      # us-east-1 is the one region that rejects an explicit LocationConstraint.
      aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
    else
      aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
        --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
    fi
    # Versioning before the first state write, not after the first apply,
    # so the very first state object already has history behind it.
    aws s3api put-bucket-versioning --bucket "$BUCKET" \
      --versioning-configuration Status=Enabled
  else
    echo "Cannot use bucket ${BUCKET}:" >&2
    echo "$err" >&2
    exit 1
  fi
fi

terraform init -input=false -reconfigure \
  -backend-config="bucket=${BUCKET}" \
  -backend-config="key=bootstrap.tfstate" \
  -backend-config="region=${REGION}"

case "$ACTION" in
  plan)  terraform plan -var="region=${REGION}" ;;
  apply) terraform apply -var="region=${REGION}" ;;
  *)     echo "Usage: $0 [plan|apply]" >&2; exit 2 ;;
esac

if [[ "$ACTION" == "apply" ]]; then
  echo
  echo "Next:"
  echo "  AWS_PROFILE=${AWS_PROFILE:-bootstrap} infra/bootstrap/new-key.sh"
fi
