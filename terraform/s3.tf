# The lakehouse bucket. Two prefix conventions live inside it:
#   landing/   raw Parquet written by generate_data.py (Phase 2)
#   warehouse/ Iceberg data files, claimed by Lakekeeper (Phase 1)
# They are conventions only — nothing is created for them.
resource "aws_s3_bucket" "this" {
  bucket = var.bucket_name

  # DEV-ONLY: lets `terraform destroy` succeed even when the bucket has objects
  # in it (they are deleted first). Never set this on a bucket you'd cry about.
  force_destroy = true

  tags = local.common_tags
}

# Explicit public access block rather than relying on account defaults — the
# habit that survives someone flipping a default somewhere.
resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-S3 encryption: free, transparent, applies to everything written here.
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# CORS for the Lakekeeper UI: it runs DuckDB-WASM in the browser and fetches
# data files cross-origin with Authorization headers (vended STS creds) plus
# Range requests. Without this rule the browser blocks the reads and DuckDB
# misreports them as "404 Full download failed" (Phase 2 UI fix).
# Server-side readers (Trino, PyIceberg, AWS CLI) never touch CORS.
resource "aws_s3_bucket_cors_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = ["http://localhost:8181"]
    expose_headers  = ["ETag", "Content-Length", "Content-Range", "Accept-Ranges"]
    max_age_seconds = 3000
  }
}
