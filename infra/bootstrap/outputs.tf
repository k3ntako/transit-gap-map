output "state_bucket" {
  description = "Holds bootstrap.tfstate and site.tfstate."
  value       = aws_s3_bucket.state.bucket
}

output "site_bucket" {
  description = "The name infra/site will create the site bucket under."
  value       = local.site_bucket
}

output "deployer_user" {
  description = "Run ./new-key.sh to give this user an access key in a local profile."
  value       = aws_iam_user.deployer.name
}

output "github_deploy_role_arn" {
  description = "Set as the AWS_ROLE_ARN repository variable; the deploy workflow assumes it."
  value       = aws_iam_role.github_deploy.arn
}
