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
