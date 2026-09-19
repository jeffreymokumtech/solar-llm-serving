terraform {
  required_version = ">= 1.6"
  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 5.60" }
    helm       = { source = "hashicorp/helm", version = "~> 2.15" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.31" }
    kubectl    = { source = "alekc/kubectl", version = "~> 2.0" }
  }
}
provider "aws" {
  region = var.region
  default_tags { tags = local.tags }
}
# Karpenter's controller chart is pulled from the public ECR gallery, which needs a token from us-east-1.
provider "aws" {
  alias  = "virginia"
  region = "us-east-1"
}
data "aws_ecrpublic_authorization_token" "token" { provider = aws.virginia }

provider "kubernetes" {
  host                   = module.eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", module.eks.cluster_name, "--region", var.region]
  }
}
provider "helm" {
  kubernetes {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", module.eks.cluster_name, "--region", var.region]
    }
  }
}
provider "kubectl" {
  host                   = module.eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
  load_config_file       = false
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", module.eks.cluster_name, "--region", var.region]
  }
}
