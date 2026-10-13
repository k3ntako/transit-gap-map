# --- caching ----------------------------------------------------------------
#
# The payload filenames are stable (data/tracts.geojson is always called
# that), so anything that caches by name serves stale data after a rebuild
# unless told otherwise. The answer here is two different lifetimes for two
# different caches:
#
#   CloudFront keeps every file for a year, and scripts/publish.sh clears it
#   with one invalidation after each upload. Nothing changes for readers
#   until that invalidation, which also makes the switch close to atomic --
#   no reader gets new HTML against old data mid-upload.
#
#   Browsers are told `no-cache`, which means keep it but ask before using
#   it. A repeat visit costs a round trip per file to the nearest edge, which
#   answers 304 Not Modified from its own cache, and can never show a stale
#   map. The alternative, a build id in every fetch URL, would need the
#   pipeline to rewrite the pages and buys back only those round trips.
#
# The year lives in the cache policy and the `no-cache` in a response-header
# policy, so neither depends on publish.sh remembering to set object metadata.

resource "aws_cloudfront_cache_policy" "static" {
  name        = "${var.project}-static"
  comment     = "Cache until invalidated; the deploy invalidates."
  min_ttl     = 0
  default_ttl = 31536000
  max_ttl     = 31536000

  parameters_in_cache_key_and_forwarded_to_origin {
    # Required for compression: CloudFront caches the gzip and brotli copies
    # separately only when Accept-Encoding is part of the key.
    enable_accept_encoding_gzip   = true
    enable_accept_encoding_brotli = true

    # The document pages carry the theme in the query string (?t=dark) and
    # the map in the hash. Neither changes which file S3 returns, so keeping
    # them out of the key keeps one cached copy per file.
    query_strings_config {
      query_string_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    cookies_config {
      cookie_behavior = "none"
    }
  }
}

resource "aws_cloudfront_response_headers_policy" "site" {
  name    = "${var.project}-headers"
  comment = "Browsers revalidate every file; plus baseline security headers."

  custom_headers_config {
    items {
      header   = "Cache-Control"
      value    = "no-cache"
      override = true
    }
  }

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = false
      preload                    = false
      override                   = true
    }
    content_type_options {
      override = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    # No frame-options and no content-security-policy, on purpose. Embedding
    # the map elsewhere is a use worth allowing, and a CSP would have to list
    # the MapLibre CDNs and CARTO's tile hosts exactly -- a mistake there is a
    # blank map in production that never shows up under `make serve`.
  }
}

# --- the distribution -------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.project}-site"
  description                       = "CloudFront signs its requests to the private site bucket."
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  comment             = "${var.project}: the static map"
  default_root_object = "index.html"
  price_class         = var.price_class
  http_version        = "http2and3"
  is_ipv6_enabled     = true

  origin {
    origin_id                = "site-bucket"
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id           = "site-bucket"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = aws_cloudfront_cache_policy.static.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # The free *.cloudfront.net certificate. A custom domain replaces this with
  # an ACM certificate (issued in us-east-1) and adds `aliases`; DEPLOY.md
  # lists the steps, including the permissions bootstrap has to grant first.
  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
