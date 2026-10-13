provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform/infra/site"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # The same derivation as infra/bootstrap's, which is what its IAM policies
  # were written against. Derived rather than read from bootstrap's state, so
  # the deployer never needs access to that state.
  site_bucket = "${var.project}-site-${data.aws_caller_identity.current.account_id}"
}

# --- the bucket ---------------------------------------------------------------
#
# Private. Readers never reach S3 directly: CloudFront signs its requests to
# the bucket (origin access control) and the bucket policy admits only this
# distribution. A public S3 website endpoint would be simpler and would serve
# uncompressed, over plain HTTP, with no CDN in front.

resource "aws_s3_bucket" "site" {
  bucket = local.site_bucket
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "site" {
  statement {
    sid       = "CloudFrontReadOnly"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site.json

  # The public-access block must exist first or S3 can reject the policy
  # while it is still evaluating whether the bucket is public.
  depends_on = [aws_s3_bucket_public_access_block.site]
}
