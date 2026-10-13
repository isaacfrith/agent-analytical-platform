output "bucket_name" {
  description = "Lakehouse bucket — becomes AWS_S3_BUCKET in .env."
  value       = aws_s3_bucket.this.id
}

output "bucket_arn" {
  value = aws_s3_bucket.this.arn
}

output "iam_user_name" {
  description = "The scoped app user — create its access key out-of-band after apply."
  value       = aws_iam_user.this.name
}

output "account_id" {
  description = "AWS account the stack lives in."
  value       = data.aws_caller_identity.current.account_id
}

output "aws_region" {
  description = "Region everything lives in (echo of the input, for convenience)."
  value       = var.aws_region
}
