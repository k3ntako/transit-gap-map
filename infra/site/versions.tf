# The hosting itself: a private S3 bucket and the CloudFront distribution in
# front of it. Applied as the deployer (infra/bootstrap creates that user and
# everything else that needs IAM), through apply.sh, which supplies the state
# bucket name -- a backend block cannot read a variable.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    key          = "site.tfstate"
    use_lockfile = true
    encrypt      = true
  }
}
