# The $20/month backstop (whole account; Bedrock spend can't be told apart by
# machine). Email alerts at 50% and 80% of actual spend and 100% forecast. At
# 100% of actual spend AWS automatically:
#   1. stops the live server, and
#   2. attaches a deny policy to the wake Lambda's role, so nothing restarts it.
# The daily limits (2 questions per visitor, 20 a day, 30 with the owner pass,
# 3 hours of running, 6 wakes) act immediately; billing data lags by hours, so this is the safety net.
# After it fires, reverse the actions in the AWS Budgets console once you've
# looked at the spend.

resource "aws_budgets_budget" "monthly" {
  name         = "agent-analytics-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [
      { threshold = 50, type = "ACTUAL" },
      { threshold = 80, type = "ACTUAL" },
      { threshold = 100, type = "FORECASTED" },
    ]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [var.budget_email]
    }
  }
}

resource "aws_iam_role" "budget_actions" {
  name = "${local.live_name}-budget-actions"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "budgets.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "budget_actions" {
  role       = aws_iam_role.budget_actions.name
  policy_arn = "arn:aws:iam::aws:policy/AWSBudgetsActionsWithAWSResourceControlAccess"
}

resource "aws_iam_policy" "deny_live_wake" {
  name        = "${local.live_name}-deny-wake"
  description = "Attached by the budget action at 100%: the wake Lambda can no longer start the live server."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Deny"
      Action   = "ec2:StartInstances"
      Resource = aws_instance.live.arn
    }]
  })
  tags = local.common_tags
}

resource "aws_budgets_budget_action" "stop_live_server" {
  budget_name        = aws_budgets_budget.monthly.name
  action_type        = "RUN_SSM_DOCUMENTS"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_actions.arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 100
  }

  definition {
    ssm_action_definition {
      action_sub_type = "STOP_EC2_INSTANCES"
      instance_ids    = [aws_instance.live.id]
      region          = var.aws_region
    }
  }

  subscriber {
    address           = var.budget_email
    subscription_type = "EMAIL"
  }
}

resource "aws_budgets_budget_action" "block_live_wakes" {
  budget_name        = aws_budgets_budget.monthly.name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_actions.arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 100
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.deny_live_wake.arn
      roles      = [aws_iam_role.live_control.name]
    }
  }

  subscriber {
    address           = var.budget_email
    subscription_type = "EMAIL"
  }
}
