# S3 bucket holding the HF cache mirror, TRT-LLM engines and Triton repos: the persistent model cache
# that both the single GPU instance and the EKS pods warm from.
variable "name" { type = string }
variable "tags" { type = map(string) }

resource "aws_s3_bucket" "this" {
  bucket        = var.name
  force_destroy = true
  tags          = var.tags
}
resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule { apply_server_side_encryption_by_default { sse_algorithm = "AES256" } }
}
resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    id     = "expire-abandoned-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 2 }
  }
}
data "aws_iam_policy_document" "read" {
  statement {
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.this.arn, "${aws_s3_bucket.this.arn}/*"]
  }
}
data "aws_iam_policy_document" "readwrite" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:AbortMultipartUpload"]
    resources = [aws_s3_bucket.this.arn, "${aws_s3_bucket.this.arn}/*"]
  }
}
resource "aws_iam_policy" "read" {
  name   = "${var.name}-read"
  policy = data.aws_iam_policy_document.read.json
}
resource "aws_iam_policy" "readwrite" {
  name   = "${var.name}-readwrite"
  policy = data.aws_iam_policy_document.readwrite.json
}
output "bucket"             { value = aws_s3_bucket.this.bucket }
output "read_policy_arn"    { value = aws_iam_policy.read.arn }
output "readwrite_policy_arn" { value = aws_iam_policy.readwrite.arn }
