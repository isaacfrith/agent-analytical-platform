# Terraform runs under ADMIN credentials (your "default" profile or env vars) —
# it needs s3:CreateBucket, iam:CreateUser and iam:PutUserPolicy. The scoped
# user it creates (iam.tf) is for the application, never for Terraform itself.
provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
}

# The account ID is only knowable at plan/apply time, and the Bedrock
# inference-profile ARN is account-scoped, so the policy (iam.tf) needs it.
# Foundation-model ARNs are NOT account-scoped — see the notes in iam.tf.
data "aws_caller_identity" "current" {}

locals {
  common_tags = {
    Project   = "agent-analytical-platform"
    ManagedBy = "terraform"
  }
}
