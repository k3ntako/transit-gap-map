# The one module that is applied with administrator credentials. Everything
# that needs IAM lives here, so the credentials that run the site module
# (infra/site) never need to create users, roles or policies -- an identity
# that can write IAM policy can grant itself anything, and least privilege
# for the deployer means it cannot.
#
# Run it through apply.sh, not bare `terraform`: the state bucket's name
# includes the account id, a backend block cannot read a variable, and the
# script is what supplies it.

terraform {
  # 1.10 is the first release with S3-native state locking (use_lockfile),
  # which is what lets this skip the DynamoDB lock table entirely.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Partial configuration: bucket, key and region arrive from apply.sh as
  # -backend-config arguments.
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
