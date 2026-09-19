terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.60" } }
}
provider "aws" { region = "us-east-1" } # budgets live in us-east-1
variable "email" { type = string }
variable "monthly_usd" {
  type    = number
  default = 120
}
resource "aws_budgets_budget" "project" {
  name         = "solar-llm-serving"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  cost_filter {
    name   = "TagKeyValue"
    values = ["user:project$solar-llm-serving"]
  }
  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value == 100 ? "ACTUAL" : "FORECASTED"
      subscriber_email_addresses = [var.email]
    }
  }
}
