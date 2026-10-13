# The IAM user a person uses day to day: `terraform apply` on infra/site, and
# scripts/publish.sh run from a laptop when CI is not the route. It can manage
# the site's bucket and CloudFront and nothing about identity -- no users, no
# roles, no policies -- so a leaked key cannot widen its own access.
#
# Its access key is deliberately not a Terraform resource. A key created here
# would sit in plain text in the state file, and the state file is read on
# every apply. new-key.sh creates it with the CLI and writes it straight into
# a local AWS profile instead.

resource "aws_iam_user" "deployer" {
  name = "${var.project}-deployer"
}

data "aws_iam_policy_document" "deployer" {
  # Read and write only its own state object and the lock file beside it, not
  # the bootstrap state, which records the IAM this user is not allowed to
  # change. Listing is unrestricted on purpose: `terraform init` lists
  # workspaces under an `env:/` prefix as well as the key, a prefix condition
  # turns that into an AccessDenied, and a listing shows names, not contents.
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid     = "SiteState"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "${aws_s3_bucket.state.arn}/${local.site_state_key}",
      "${aws_s3_bucket.state.arn}/${local.site_state_key}.tflock",
    ]
  }

  # Full control of the one bucket infra/site creates, which is what
  # Terraform needs to manage its policy, encryption and public-access block.
  # The name is fixed in advance, so this can be granted before it exists.
  statement {
    sid     = "SiteBucket"
    actions = ["s3:*"]
    resources = [
      "arn:aws:s3:::${local.site_bucket}",
      "arn:aws:s3:::${local.site_bucket}/*",
    ]
  }

  # CloudFront's create actions do not support resource-level permissions --
  # a distribution has no ARN until it exists -- so this cannot be scoped to
  # one distribution. It is also the service the site module is made of.
  statement {
    sid       = "CloudFront"
    actions   = ["cloudfront:*"]
    resources = ["*"]
  }

  # Rotating its own key without the bootstrap profile: create the new one,
  # switch the profile, delete the old one.
  statement {
    sid = "OwnAccessKeys"
    actions = [
      "iam:CreateAccessKey",
      "iam:DeleteAccessKey",
      "iam:ListAccessKeys",
      "iam:UpdateAccessKey",
      "iam:GetUser",
    ]
    resources = [aws_iam_user.deployer.arn]
  }
}

resource "aws_iam_user_policy" "deployer" {
  name   = "deploy-site"
  user   = aws_iam_user.deployer.name
  policy = data.aws_iam_policy_document.deployer.json
}
