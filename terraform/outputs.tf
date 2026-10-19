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

output "lakekeeper_vending_role_arn" {
  description = "The STS role Lakekeeper assumes to vend short-term S3 credentials to engines. Goes into .env as LAKEKEEPER_STS_ROLE_ARN."
  value       = aws_iam_role.lakekeeper_vending.arn
}

# --- Live questions -----------------------------------------------------------

output "live_control_url" {
  description = "The wake/status Lambda's URL. The website reads it from ui/live-config.json (`make live-config`)."
  value       = aws_lambda_function_url.live_control.function_url
}

output "live_instance_id" {
  description = "The on-demand live server."
  value       = aws_instance.live.id
}

output "live_set_secrets" {
  description = "Run these once after the first apply (secrets never pass through Terraform state)."
  value       = <<-EOT
    aws ssm put-parameter --region ${var.aws_region} --overwrite --type SecureString --name ${var.live_param_prefix}/turnstile-secret --value 'YOUR_TURNSTILE_SECRET'
    aws ssm put-parameter --region ${var.aws_region} --overwrite --type SecureString --name ${var.live_param_prefix}/openelectricity-key --value 'YOUR_OPENELECTRICITY_KEY'
  EOT
}

output "live_kill_switch" {
  description = "Turn live questions off (or back on with 'on')."
  value       = "aws ssm put-parameter --region ${var.aws_region} --overwrite --type String --name ${var.live_param_prefix}/switch --value off"
}
