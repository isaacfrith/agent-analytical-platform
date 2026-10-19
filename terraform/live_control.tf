# The wake/status Lambda (lambda/live_control.py) behind a public function URL.
# The website polls GET /status and sends POST /wake (with a Turnstile token).
# Its role may describe instances, start ONLY the live server, and read/write
# only the live parameters. Invocation is public only through the function URL
# (`invoked_via_function_url`), never directly through the Lambda API.

data "archive_file" "live_control" {
  type        = "zip"
  source_file = "${path.module}/lambda/live_control.py"
  output_path = "${path.module}/.build/live_control.zip"
}

resource "aws_iam_role" "live_control" {
  name = "${local.live_name}-control"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "live_control" {
  name              = "/aws/lambda/${local.live_name}-control"
  retention_in_days = 14
  tags              = local.common_tags
}

resource "aws_iam_role_policy" "live_control" {
  name = "live-control"
  role = aws_iam_role.live_control.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.live_control.arn}:*"
      },
      {
        Sid      = "SeeInstances"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances"]
        Resource = "*" # DescribeInstances can't be scoped to one instance
      },
      {
        Sid      = "StartOnlyTheLiveServer"
        Effect   = "Allow"
        Action   = ["ec2:StartInstances"]
        Resource = aws_instance.live.arn
      },
      {
        Sid      = "ReadLiveParameters"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "${local.param_arn}/*"
      },
      {
        Sid      = "WriteWakeState"
        Effect   = "Allow"
        Action   = ["ssm:PutParameter"]
        Resource = [for name in ["url", "stage", "wakes"] : "${local.param_arn}/${name}"]
      },
    ]
  })
}

resource "aws_lambda_function" "live_control" {
  function_name    = "${local.live_name}-control"
  role             = aws_iam_role.live_control.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "live_control.handler"
  filename         = data.archive_file.live_control.output_path
  source_code_hash = data.archive_file.live_control.output_base64sha256
  timeout          = 15
  memory_size      = 128

  environment {
    variables = {
      INSTANCE_ID       = aws_instance.live.id
      PARAM_PREFIX      = var.live_param_prefix
      MAX_WAKES_PER_DAY = "6"
      MAX_RUNTIME_S     = "10800" # 3 hours a day; the server's watchdog enforces the same
    }
  }

  depends_on = [aws_cloudwatch_log_group.live_control, aws_iam_role_policy.live_control]
  tags       = local.common_tags
}

resource "aws_lambda_function_url" "live_control" {
  function_name      = aws_lambda_function.live_control.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = [var.site_origin, "http://localhost:8765"]
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type"]
    max_age       = 300
  }
}

resource "aws_lambda_permission" "live_control_url" {
  statement_id           = "PublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.live_control.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "live_control_invoke" {
  statement_id             = "PublicViaFunctionUrlOnly"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.live_control.function_name
  principal                = "*"
  invoked_via_function_url = true
}
