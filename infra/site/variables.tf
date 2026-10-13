variable "project" {
  description = "Must match infra/bootstrap's project, which granted the deployer access to buckets by this prefix."
  type        = string
  default     = "transit-gap-map"
}

variable "region" {
  description = "Must match infra/bootstrap's region."
  type        = string
  default     = "us-east-1"
}

variable "price_class" {
  description = <<-EOT
    Which CloudFront edge locations serve the site. PriceClass_100 is North
    America and Europe: the audience for a New York map, and the cheapest
    class. Readers elsewhere are still served, from the nearest of those.
  EOT
  type        = string
  default     = "PriceClass_100"
}
