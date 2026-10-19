variable "aws_region" {
  description = "One region for everything: the bucket, Bedrock model access, and the inference profile. A single region removes a whole class of cross-region 403 debugging."
  type        = string
}

variable "aws_profile" {
  description = "Named ADMIN profile Terraform authenticates with (needs s3:CreateBucket, iam:CreateUser, iam:PutUserPolicy). Leave null to use the default credential chain (env vars or the 'default' profile). NOT the app user — that user is created by this stack."
  type        = string
  default     = null
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name (unique across ALL AWS accounts)."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "Bucket names must be 3-63 characters: lowercase letters, digits, hyphens and dots, starting and ending with a letter or digit."
  }
}

variable "iam_user_name" {
  description = "Name of the scoped programmatic user the application authenticates as."
  type        = string
  default     = "agent-analytics-dev"
}

# --- Live questions (live.tf, live_control.tf, budget.tf) --------------------

variable "bedrock_model_id" {
  description = "The Bedrock model (inference profile ID) the hosted agent may call. Its IAM grant names this model only."
  type        = string
  default     = "au.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "live_instance_type" {
  description = "Size of the on-demand server. ARM (Graviton); the Docker stack is capped at ~4 GB, so 8 GB leaves room for the agent."
  type        = string
  default     = "t4g.large"
}

variable "live_param_prefix" {
  description = "SSM Parameter Store path for the live server's settings, secrets and state."
  type        = string
  default     = "/agent-analytics/live"
}

variable "site_origin" {
  description = "The website allowed to call the wake Lambda and the live API (CORS)."
  type        = string
  default     = "https://isaacfrith.github.io"
}

variable "repo_url" {
  description = "Public Git repository the server pulls `main` from on every wake."
  type        = string
  default     = "https://github.com/isaacfrith/agent-analytical-platform.git"
}

variable "turnstile_site_key" {
  description = "Cloudflare Turnstile site key (public; the page renders the bot check with it). The secret key is set separately in SSM, never in Terraform."
  type        = string
  default     = "0x4AAAAAAFcGTJXz9hwCGRlj"
}

variable "budget_limit_usd" {
  description = "Monthly AWS spend (whole account) at which the live server is stopped and blocked from waking."
  type        = number
  default     = 20
}

variable "budget_email" {
  description = "Where AWS Budgets sends spend alerts (50%, 80%, forecast 100%) and action notices."
  type        = string
}
