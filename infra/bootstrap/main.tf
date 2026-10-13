provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform/infra/bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # S3 bucket names are global across every AWS account, so the account id is
  # what keeps these from colliding with someone else's. infra/site derives
  # the same names the same way rather than reading this module's state, so
  # the deployer never needs read access to bootstrap.tfstate.
  state_bucket = "${var.project}-tfstate-${local.account_id}"
  site_bucket  = "${var.project}-site-${local.account_id}"

  # The site module's state object. The deployer may read and write this key
  # and its lock file, and nothing else in the state bucket.
  site_state_key = "site.tfstate"
}
