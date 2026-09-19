#!/bin/bash
# First boot: model cache directory, docker compose plugin, AWS CLI, pull the HF cache mirror from S3 if present.
set -eux
mkdir -p /data/hf /data/engines /data/triton
chown -R ubuntu:ubuntu /data
apt-get update && apt-get install -y docker-compose-plugin jq unzip
curl -sS "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip && unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install --update
usermod -aG docker ubuntu
nvidia-smi
sudo -u ubuntu aws s3 sync --no-progress "s3://${bucket}/hf" /data/hf --size-only || true
echo "ready" > /data/READY
