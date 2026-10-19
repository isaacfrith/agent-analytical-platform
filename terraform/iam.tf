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
      "Action": ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"],
      "Resource": "${aws_s3_bucket.this.arn}"
    },
    {
      "Sid": "S3ObjectAccess",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
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
    },
    {
      "Sid": "AssumeVendingRole",
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "${aws_iam_role.lakekeeper_vending.arn}"
    }
  ]
}
POLICY
}

# --- Credential vending role (Phase 1) ---------------------------------------
#
# Lakekeeper holds the app user's access keys (as the warehouse's
# storage-credential) and calls sts:AssumeRole against this role whenever an
# engine (Trino, PyIceberg) requests vended credentials. The role's OWN policy
# below is what the vended short-term sessions carry — the user's own S3
# permissions are irrelevant to vended sessions.
resource "aws_iam_role" "lakekeeper_vending" {
  name = "agent-analytics-lakekeeper-vending"

  # Trust: the app user (laptop Lakekeeper), and the live server's role
  # (hosted Lakekeeper, aws-system-identity) only with the external ID.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { AWS = aws_iam_user.this.arn }
        Action    = "sts:AssumeRole"
      },
      {
        Effect    = "Allow"
        Principal = { AWS = aws_iam_role.live_server.arn }
        Action    = ["sts:AssumeRole", "sts:TagSession"]
        Condition = {
          StringEquals = { "sts:ExternalId" = random_password.lakekeeper_external_id.result }
        }
      }
    ]
  })

  tags = local.common_tags
}

# What vended sessions may do: full Iceberg data-file lifecycle on the bucket,
# including the multipart trio engines need to write large Parquet files
# (omitting it is the classic "writes fail at the final part" surprise).
resource "aws_iam_role_policy" "lakekeeper_vending_s3" {
  name = "lakehouse-s3"
  role = aws_iam_role.lakekeeper_vending.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BucketLevel"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:ListBucketMultipartUploads"
        ]
        Resource = aws_s3_bucket.this.arn
      },
      {
        Sid    = "ObjectLevel"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts"
        ]
        Resource = "${aws_s3_bucket.this.arn}/*"
      }
    ]
  })
}
