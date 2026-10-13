# The bucket that holds Terraform state, including this module's own.
#
# That is circular, and the circle is broken outside Terraform: apply.sh
# creates the empty bucket with the AWS CLI if it does not exist, `terraform
# init` then stores state in it, and the import block below adopts it so its
# settings are managed here like everything else. On every later run the
# import is a no-op. The alternative -- local state first, then
# `init -migrate-state` -- is two runs with a manual edit between them, which
# is the kind of step this is meant not to require remembering.

import {
  to = aws_s3_bucket.state
  id = local.state_bucket
}

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  # Losing this bucket loses track of every resource in the account that
  # belongs to this project. Destroying it should take a deliberate edit.
  lifecycle {
    prevent_destroy = true
  }
}

# Every apply writes a new version, so a bad apply or a corrupted state file
# can be rolled back by restoring the previous object.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}

    # Applies are rare here, so a year of history costs nothing and covers
    # "it worked last spring".
    noncurrent_version_expiration {
      noncurrent_days = 365
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}
