# The site should cost about nothing: a few MB in S3, and CloudFront traffic
# inside its free allowance. What could change that is a spike -- a link that
# gets passed around -- and AWS has no spending cap, only alarms. So this is
# the alarm. An account's first two budgets are free.
#
# It watches the whole account, not just this project, because cost filters
# on tags only see a tag once it has been activated as a cost-allocation tag
# in the billing console, which is a manual step this would silently depend on.

resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_monthly_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Forecast first: it fires partway through a bad month rather than after it.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }
}
