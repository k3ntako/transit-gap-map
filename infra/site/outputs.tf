output "site_url" {
  description = "Where the map is served."
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "site_bucket" {
  description = "scripts/publish.sh uploads here."
  value       = aws_s3_bucket.site.bucket
}

output "distribution_id" {
  description = "scripts/publish.sh invalidates this after an upload."
  value       = aws_cloudfront_distribution.site.id
}
