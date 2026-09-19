locals {
  tags = { project = var.project, owner = "jeffrey", managed-by = "terraform", cost-center = "portfolio" }
}

data "aws_caller_identity" "me" {}
data "aws_vpc" "default" { default = true }
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Deep Learning Base OSS Nvidia Driver GPU AMI: driver + docker + nvidia-container-toolkit preinstalled.
data "aws_ami" "dlami" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04) *"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

module "bucket" {
  source = "../modules/model-bucket"
  name   = var.bucket_name
  tags   = local.tags
}

resource "aws_security_group" "gpu" {
  name        = "${var.project}-gpu"
  description = "SSH from one address; no inference ports exposed (use SSM port forwarding)"
  vpc_id      = data.aws_vpc.default.id
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}
resource "aws_iam_role" "gpu" {
  name               = "${var.project}-gpu"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.gpu.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy_attachment" "bucket" {
  role       = aws_iam_role.gpu.name
  policy_arn = module.bucket.readwrite_policy_arn
}
resource "aws_iam_role_policy_attachment" "bedrock" { # gateway fallback experiments from the instance
  role       = aws_iam_role.gpu.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockLimitedAccess"
}
resource "aws_iam_instance_profile" "gpu" {
  name = "${var.project}-gpu"
  role = aws_iam_role.gpu.name
}

resource "aws_instance" "gpu" {
  ami                    = data.aws_ami.dlami.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.gpu.id]
  iam_instance_profile   = aws_iam_instance_profile.gpu.name
  key_name               = var.key_name
  user_data              = templatefile("${path.module}/user_data.sh", { bucket = module.bucket.bucket })

  dynamic "instance_market_options" {
    for_each = var.spot ? [1] : []
    content {
      market_type = "spot"
      spot_options {
        max_price                      = var.spot_max_price
        spot_instance_type             = "one-time"
        instance_interruption_behavior = "terminate"
      }
    }
  }
  root_block_device {
    volume_size = var.volume_gb
    volume_type = "gp3"
    throughput  = 250
    iops        = 6000
  }
  metadata_options { http_tokens = "required" }
  tags = { Name = "${var.project}-${var.instance_type}" }
}

output "instance_id" { value = aws_instance.gpu.id }
output "public_ip" { value = aws_instance.gpu.public_ip }
output "bucket" { value = module.bucket.bucket }
output "ssm_shell" { value = "aws ssm start-session --target ${aws_instance.gpu.id} --region ${var.region}" }
output "ssm_forward" { value = "aws ssm start-session --target ${aws_instance.gpu.id} --region ${var.region} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"3000\"],\"localPortNumber\":[\"3000\"]}'" }
