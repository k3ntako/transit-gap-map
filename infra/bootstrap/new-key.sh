#!/usr/bin/env bash
# Give the deployer user an access key and store it in a local AWS profile,
# without the secret ever touching Terraform state, the terminal or a file in
# this repository. Run with the BOOTSTRAP profile the first time:
#
#   AWS_PROFILE=bootstrap infra/bootstrap/new-key.sh
#
# To rotate later, the deployer can do it for itself (its policy allows
# managing its own keys), so the bootstrap profile is not needed again:
#
#   AWS_PROFILE=transit-gap-map infra/bootstrap/new-key.sh --rotate
set -euo pipefail

USER_NAME="transit-gap-map-deployer"
PROFILE="${DEPLOY_PROFILE:-transit-gap-map}"
REGION="${AWS_REGION:-us-east-1}"
ROTATE=0
[[ "${1:-}" == "--rotate" ]] && ROTATE=1

old_keys="$(aws iam list-access-keys --user-name "$USER_NAME" \
  --query 'AccessKeyMetadata[].AccessKeyId' --output text)"

# IAM allows two keys per user. Creating a third fails, and silently
# accumulating a second is how forgotten keys happen, so an existing key
# means either --rotate or a stop.
if [[ -n "$old_keys" && "$ROTATE" -eq 0 ]]; then
  echo "${USER_NAME} already has a key: ${old_keys}" >&2
  echo "Re-run with --rotate to replace it." >&2
  exit 1
fi

read -r key_id secret < <(aws iam create-access-key --user-name "$USER_NAME" \
  --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text)

aws configure set aws_access_key_id "$key_id" --profile "$PROFILE"
aws configure set aws_secret_access_key "$secret" --profile "$PROFILE"
aws configure set region "$REGION" --profile "$PROFILE"
unset secret

echo "Wrote key ${key_id} to AWS profile '${PROFILE}'."

if [[ "$ROTATE" -eq 1 && -n "$old_keys" ]]; then
  # A brand-new key can take a few seconds to be accepted everywhere; prove it
  # works before deleting the one it replaces.
  for _ in 1 2 3 4 5 6; do
    if aws sts get-caller-identity --profile "$PROFILE" >/dev/null 2>&1; then
      break
    fi
    sleep 5
  done
  aws sts get-caller-identity --profile "$PROFILE" >/dev/null
  for k in $old_keys; do
    aws iam delete-access-key --user-name "$USER_NAME" --access-key-id "$k" \
      --profile "$PROFILE"
    echo "Deleted old key ${k}."
  done
fi

echo "Check it with:  aws sts get-caller-identity --profile ${PROFILE}"
