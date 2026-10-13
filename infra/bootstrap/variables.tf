variable "project" {
  description = "Prefix for every resource name. Changing it after the first apply renames, and so replaces, everything."
  type        = string
  default     = "transit-gap-map"
}

variable "region" {
  description = "Home region. us-east-1 because CloudFront only accepts ACM certificates issued there, which matters once a custom domain is added."
  type        = string
  default     = "us-east-1"
}

variable "github_repo" {
  description = "owner/name of the repository whose Actions may deploy. Must match config.SOURCE_URL."
  type        = string
  default     = "k3ntako/transit-gap-map"
}

variable "github_deploy_branch" {
  description = "Only workflow runs on this branch can assume the deploy role."
  type        = string
  default     = "main"
}

variable "create_github_oidc_provider" {
  description = "An AWS account holds one GitHub OIDC provider. Set false if another project already created it, and this module will use the existing one."
  type        = bool
  default     = true
}

variable "budget_monthly_usd" {
  description = "Monthly spend that triggers an email. Expected spend is about zero; this is a tripwire for a traffic spike, not a forecast."
  type        = number
  default     = 5
}

variable "budget_email" {
  description = "Where the budget alarm is sent. Set in terraform.tfvars (gitignored) so it stays out of the public repository."
  type        = string
}
