# Dedicated programmatic user for the application (preflight, PyIceberg data
# writes, Bedrock calls). No aws_iam_user_login_profile on purpose — this user
# never touches the console.
resource "aws_iam_user" "this" {
  name = var.iam_user_name
  tags = local.common_tags
}

# Inline policy instead of aws_iam_policy + attachment: one user, one stack —
# the standalone-policy pattern buys reuse we don't need here.
#
# Two ARN shapes to internalize (the #1 cause of "my policy looks right but
# InvokeModel is still denied"):
#   foundation-model ARNs  -> EMPTY account field: arn:aws:bedrock:<region>::foundation-model/<model>
#   inference-profile ARNs -> account-scoped:     arn:aws:bedrock:<region>:<account>:inference-profile/<id>
#
# The foundation-model grant is region-wildcarded on purpose: cross-region
# inference profiles (the us./eu./apac.-prefixed IDs) route requests to
# foundation models in OTHER regions, so a single-region grant breaks them
# with an AccessDenied that looks like a policy typo.
resource "aws_iam_user_policy" "this" {
  name = "${var.iam_user_name}-scoped-access"
  user = aws_iam_user.this.name

  policy = <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3BucketAccess",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": "${aws_s3_bucket.this.arn}"
    },
    {
      "Sid": "S3ObjectAccess",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "${aws_s3_bucket.this.arn}/*"
    },
    {
      "Sid": "BedrockDiscovery",
      "Effect": "Allow",
      "Action": ["bedrock:ListFoundationModels", "bedrock:ListInferenceProfiles"],
      "Resource": "*"
    },
    {
      "Sid": "BedrockInvoke",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/*"
      ]
    }
  ]
}
POLICY
}
