# The on-demand live-questions server: one fixed-size x86_64 instance running the
# same stack as the laptop (Docker: Postgres, Lakekeeper, Trino, Valkey; plus
# WrenAI and the agent), started by the wake Lambda and stopped by its own
# watchdog. Security posture:
#   - no inbound ports: the security group has no ingress rules; visitors reach
#     the API through an outbound Cloudflare tunnel; no SSH key (Session
#     Manager for emergencies)
#   - no stored AWS keys: an instance role scoped to the hosted S3 prefix, the
#     one Bedrock model, and this server's own SSM parameters
#   - IMDSv2 required (hop limit 2 so containers can reach it), encrypted disk,
#     security updates on every boot (scripts/hosted/start.sh via live.service)

locals {
  live_name   = "agent-analytics-live"
  param_arn   = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.live_param_prefix}"
  model_short = replace(var.bedrock_model_id, "/^(au|apac|us|eu|global)\\./", "")
}

# --- Settings, secrets and state (SSM Parameter Store) ------------------------
# State parameters are written by the server and the wake Lambda; secrets are
# set by you with the AWS CLI (see outputs). Terraform creates them with
# placeholders and never overwrites them afterwards, so secrets stay out of
# Terraform state.

resource "random_password" "lakekeeper_external_id" {
  length  = 32
  special = false
}

resource "aws_ssm_parameter" "live_state" {
  for_each = { switch = "on", url = "-", stage = "-", runtime = "-", wakes = "-" }

  name  = "${var.live_param_prefix}/${each.key}"
  type  = "String"
  value = each.value
  tags  = local.common_tags

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "live_secret" {
  for_each = toset(["turnstile-secret", "openelectricity-key", "owner-pass"])

  name  = "${var.live_param_prefix}/${each.key}"
  type  = "SecureString"
  value = "set-me"
  tags  = local.common_tags

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "turnstile_site_key" {
  name  = "${var.live_param_prefix}/turnstile-site-key"
  type  = "String"
  value = var.turnstile_site_key
  tags  = local.common_tags
}

# --- Instance role -------------------------------------------------------------

resource "aws_iam_role" "live_server" {
  name = "${local.live_name}-server"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "live_server" {
  name = "live-server"
  role = aws_iam_role.live_server.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListHostedPrefix"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = aws_s3_bucket.this.arn
        Condition = {
          StringLike = { "s3:prefix" = ["hosted/*", "hosted"] }
        }
      },
      {
        Sid    = "ReadWriteHostedPrefix"
        Effect = "Allow"
        Action = [
          "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
          "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts",
        ]
        Resource = "${aws_s3_bucket.this.arn}/hosted/*"
      },
      {
        Sid    = "InvokeTheOneModel"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [
          "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.bedrock_model_id}",
          "arn:aws:bedrock:*::foundation-model/${local.model_short}",
        ]
      },
      {
        Sid      = "ReadOwnParameters"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = "${local.param_arn}/*"
      },
      {
        Sid      = "WriteOwnState"
        Effect   = "Allow"
        Action   = ["ssm:PutParameter"]
        Resource = [for name in ["url", "stage", "runtime"] : "${local.param_arn}/${name}"]
      },
      {
        # Lakekeeper (aws-system-identity) assumes the vending role to hand
        # Trino short-lived, table-scoped S3 credentials.
        Sid      = "AssumeVendingRole"
        Effect   = "Allow"
        Action   = ["sts:AssumeRole", "sts:TagSession"]
        Resource = aws_iam_role.lakekeeper_vending.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "live_server_ssm_core" {
  role       = aws_iam_role.live_server.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" # Session Manager, no SSH
}

resource "aws_iam_instance_profile" "live_server" {
  name = "${local.live_name}-server"
  role = aws_iam_role.live_server.name
}

# --- Network: default VPC, no inbound at all ------------------------------------

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "live_server" {
  name        = "${local.live_name}-server"
  description = "Live questions server: no inbound; outbound for AWS APIs, packages and the Cloudflare tunnel"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.live_name}-server" })
}

# --- The instance ----------------------------------------------------------------

# x86_64: WrenAI's engine (wren-core-py) ships Linux wheels for x86_64 only;
# on ARM it would have to be compiled from Rust source on every server.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# Changing architecture needs a new server (the AMI changes, and AMI updates
# are otherwise ignored); bumping this replaces the instance.
resource "terraform_data" "live_platform" {
  input = "al2023-x86_64"
}

resource "aws_instance" "live" {
  ami                                  = data.aws_ssm_parameter.al2023.value
  instance_type                        = var.live_instance_type
  subnet_id                            = sort(data.aws_subnets.default.ids)[0]
  vpc_security_group_ids               = [aws_security_group.live_server.id]
  iam_instance_profile                 = aws_iam_instance_profile.live_server.name
  associate_public_ip_address          = true # outbound only (no inbound rules); billed only while running
  instance_initiated_shutdown_behavior = "stop"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # lets the Docker containers reach it
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 30
    encrypted   = true
  }

  user_data = templatefile("${path.module}/templates/live_user_data.sh.tftpl", {
    repo_url         = var.repo_url
    aws_region       = var.aws_region
    param_prefix     = var.live_param_prefix
    bucket           = aws_s3_bucket.this.id
    bedrock_model_id = var.bedrock_model_id
    vending_role_arn = aws_iam_role.lakekeeper_vending.arn
    external_id      = random_password.lakekeeper_external_id.result
    site_origin      = var.site_origin
  })

  tags = merge(local.common_tags, { Name = local.live_name })

  lifecycle {
    ignore_changes       = [ami] # don't replace the server whenever Amazon publishes a new AMI
    replace_triggered_by = [terraform_data.live_platform]
  }

  depends_on = [aws_ssm_parameter.live_state, aws_ssm_parameter.live_secret]
}
