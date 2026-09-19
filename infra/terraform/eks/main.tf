locals {
  tags = { project = var.budget_tag, owner = "jeffrey", managed-by = "terraform", cost-center = "portfolio" }
  azs  = ["${var.region}a", "${var.region}b"]
}

data "aws_availability_zones" "available" {}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name = var.cluster_name
  cidr = "10.42.0.0/16"
  azs  = local.azs

  private_subnets = ["10.42.0.0/19", "10.42.32.0/19"]
  public_subnets  = ["10.42.64.0/22", "10.42.68.0/22"]

  enable_nat_gateway     = true
  single_nat_gateway     = true      # one NAT: cheaper, and this cluster is not HA by design
  enable_dns_hostnames   = true

  # Karpenter discovers subnets by this tag; the LB controller uses the role tags.
  private_subnet_tags = { "karpenter.sh/discovery" = var.cluster_name, "kubernetes.io/role/internal-elb" = 1 }
  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
}

module "bucket" {
  source = "../modules/model-bucket"
  name   = var.bucket_name
  tags   = local.tags
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name    = var.cluster_name
  cluster_version = var.k8s_version

  cluster_endpoint_public_access = true    # kubectl from the laptop; API is still authenticated. Restrict with cluster_endpoint_public_access_cidrs if wanted.
  enable_cluster_creator_admin_permissions = true

  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnets
  control_plane_subnet_ids = module.vpc.private_subnets

  cluster_addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = {}
    eks-pod-identity-agent = {}
    aws-ebs-csi-driver     = { service_account_role_arn = module.ebs_csi_role.iam_role_arn }
  }

  # A small managed node group for the system workloads (Karpenter, Prometheus, KEDA, gateway).
  # GPU nodes are never in a node group: Karpenter creates them on demand and removes them when idle.
  eks_managed_node_groups = {
    system = {
      instance_types = [var.system_instance_type]
      min_size       = 2
      max_size       = 3
      desired_size   = 2
      labels         = { role = "system" }
    }
  }

  # Karpenter needs the security group tagged for discovery.
  node_security_group_tags = { "karpenter.sh/discovery" = var.cluster_name }
}

module "ebs_csi_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.44"
  role_name             = "${var.cluster_name}-ebs-csi"
  attach_ebs_csi_policy = true
  oidc_providers = {
    main = { provider_arn = module.eks.oidc_provider_arn, namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"] }
  }
}

# ---------------------------------------------------------------- Karpenter
module "karpenter" {
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "~> 20.24"

  cluster_name          = module.eks.cluster_name
  enable_v1_permissions = true
  enable_pod_identity   = true
  create_pod_identity_association = true
  node_iam_role_additional_policies = {
    AmazonSSMManagedInstanceCore = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  }
}

resource "helm_release" "karpenter" {
  namespace           = "kube-system"
  name                = "karpenter"
  repository          = "oci://public.ecr.aws/karpenter"
  repository_username = data.aws_ecrpublic_authorization_token.token.user_name
  repository_password = data.aws_ecrpublic_authorization_token.token.password
  chart               = "karpenter"
  version             = "1.6.1"
  wait                = true
  values = [yamlencode({
    settings = { clusterName = module.eks.cluster_name, interruptionQueue = module.karpenter.queue_name }
    serviceAccount = { name = module.karpenter.service_account }
    controller = { resources = { requests = { cpu = "500m", memory = "512Mi" } } }
    nodeSelector = { role = "system" }
  })]
  depends_on = [module.eks]
}

# GPU node class + pool: g5 spot first, on-demand as fallback; consolidates and scales to zero when idle.
resource "kubectl_manifest" "gpu_nodeclass" {
  yaml_body = yamlencode({
    apiVersion = "karpenter.k8s.aws/v1"
    kind       = "EC2NodeClass"
    metadata   = { name = "gpu-a10g" }
    spec = {
      amiSelectorTerms = [{ alias = "al2023@latest" }]   # AL2023 NVIDIA variant is picked automatically for GPU instance types
      role             = module.karpenter.node_iam_role_name
      subnetSelectorTerms        = [{ tags = { "karpenter.sh/discovery" = module.eks.cluster_name } }]
      securityGroupSelectorTerms = [{ tags = { "karpenter.sh/discovery" = module.eks.cluster_name } }]
      blockDeviceMappings = [{
        deviceName = "/dev/xvda"
        ebs        = { volumeSize = "200Gi", volumeType = "gp3", iops = 6000, throughput = 250, deleteOnTermination = true }
      }]
      metadataOptions = { httpTokens = "required" }
      tags = local.tags
    }
  })
  depends_on = [helm_release.karpenter]
}

resource "kubectl_manifest" "gpu_nodepool" {
  yaml_body = yamlencode({
    apiVersion = "karpenter.sh/v1"
    kind       = "NodePool"
    metadata   = { name = "gpu-a10g" }
    spec = {
      template = {
        metadata = { labels = { "karpenter.sh/nodepool" = "gpu-a10g", "nvidia.com/gpu.present" = "true" } }
        spec = {
          nodeClassRef = { group = "karpenter.k8s.aws", kind = "EC2NodeClass", name = "gpu-a10g" }
          taints       = [{ key = "nvidia.com/gpu", value = "present", effect = "NoSchedule" }]
          expireAfter  = "24h"
          requirements = [
            { key = "karpenter.k8s.aws/instance-family", operator = "In", values = var.gpu_instance_families },
            { key = "karpenter.sh/capacity-type", operator = "In", values = var.gpu_capacity_types },
            { key = "kubernetes.io/arch", operator = "In", values = ["amd64"] },
            { key = "karpenter.k8s.aws/instance-gpu-count", operator = "In", values = ["1", "4"] },
          ]
        }
      }
      disruption = {
        consolidationPolicy = "WhenEmptyOrUnderutilized"
        consolidateAfter    = "2m"
        budgets             = [{ nodes = "1" }]
      }
      limits = { cpu = var.gpu_max_cpus, "nvidia.com/gpu" = "8" }
      weight = 10
    }
  })
  depends_on = [kubectl_manifest.gpu_nodeclass]
}

# ---------------------------------------------------------------- cluster add-ons
resource "helm_release" "nvidia_device_plugin" {
  name             = "nvidia-device-plugin"
  namespace        = "nvidia-device-plugin"
  create_namespace = true
  repository       = "https://nvidia.github.io/k8s-device-plugin"
  chart            = "nvidia-device-plugin"
  version          = "0.17.3"
  values = [yamlencode({
    gfd = { enabled = true }     # GPU feature discovery labels nodes with product/memory
    tolerations = [{ key = "nvidia.com/gpu", operator = "Exists", effect = "NoSchedule" }]
    nodeSelector = { "nvidia.com/gpu.present" = "true" }
    # config.name = "time-slicing"  # uncomment with charts/experiments/time-slicing-configmap.yaml applied
  })]
  depends_on = [module.eks]
}

resource "helm_release" "dcgm_exporter" {
  name             = "dcgm-exporter"
  namespace        = "monitoring"
  create_namespace = true
  repository       = "https://nvidia.github.io/dcgm-exporter/helm-charts"
  chart            = "dcgm-exporter"
  version          = "4.2.3"
  values = [yamlencode({
    tolerations    = [{ key = "nvidia.com/gpu", operator = "Exists", effect = "NoSchedule" }]
    nodeSelector   = { "nvidia.com/gpu.present" = "true" }
    serviceMonitor = { enabled = true, interval = "5s", additionalLabels = { release = "kube-prometheus-stack" } }
    arguments      = ["-f", "/etc/dcgm-exporter/dcp-metrics-included.csv"]   # includes profiling metrics (tensor core activity)
  })]
  depends_on = [helm_release.kube_prometheus_stack]
}

resource "helm_release" "kube_prometheus_stack" {
  name             = "kube-prometheus-stack"
  namespace        = "monitoring"
  create_namespace = true
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  version          = "77.6.2"
  timeout          = 600
  values = [yamlencode({
    prometheus = { prometheusSpec = {
      scrapeInterval = "5s"
      retention      = "3d"
      serviceMonitorSelectorNilUsesHelmValues = false
      podMonitorSelectorNilUsesHelmValues     = false
      nodeSelector = { role = "system" }
      storageSpec  = { volumeClaimTemplate = { spec = { storageClassName = "gp3-model-cache", resources = { requests = { storage = "20Gi" } } } } }
    } }
    grafana = {
      adminPassword = "admin"
      nodeSelector  = { role = "system" }
      sidecar       = { dashboards = { enabled = true, label = "grafana_dashboard" } }
    }
    alertmanager = { enabled = false }
  })]
  depends_on = [module.eks, kubernetes_storage_class.gp3]
}

resource "helm_release" "keda" {
  name             = "keda"
  namespace        = "keda"
  create_namespace = true
  repository       = "https://kedacore.github.io/charts"
  chart            = "keda"
  version          = "2.17.2"
  values           = [yamlencode({ nodeSelector = { role = "system" } })]
  depends_on       = [module.eks]
}

resource "kubernetes_storage_class" "gp3" {
  metadata { name = "gp3-model-cache" }
  storage_provisioner    = "ebs.csi.aws.com"
  reclaim_policy         = "Delete"
  volume_binding_mode    = "WaitForFirstConsumer"   # the PVC is created in the AZ where the GPU node lands
  allow_volume_expansion = true
  parameters = { type = "gp3", iops = "6000", throughput = "250", encrypted = "true" }
  depends_on = [module.eks]
}

# Grafana dashboards from stacks/observability, picked up by the sidecar.
resource "kubernetes_config_map" "dashboards" {
  metadata {
    name      = "solar-llm-dashboards"
    namespace = "monitoring"
    labels    = { grafana_dashboard = "1" }
  }
  data = { for f in fileset("${path.module}/../../../stacks/observability/grafana/dashboards", "*.json") :
    f => file("${path.module}/../../../stacks/observability/grafana/dashboards/${f}") }
  depends_on = [helm_release.kube_prometheus_stack]
}

# ---------------------------------------------------------------- namespaces + identities for the workloads
resource "kubernetes_namespace" "ns" {
  for_each = toset(["llm", "gateway", "solarbench"])
  metadata { name = each.key }
}

# Pod Identity: engine pods read the model bucket; the gateway may call Bedrock.
resource "aws_iam_role" "engine" {
  name               = "${var.cluster_name}-engine"
  assume_role_policy = data.aws_iam_policy_document.pod_identity.json
}
resource "aws_iam_role_policy_attachment" "engine_bucket" {
  role       = aws_iam_role.engine.name
  policy_arn = module.bucket.read_policy_arn
}
resource "aws_iam_role" "gateway" {
  name               = "${var.cluster_name}-gateway"
  assume_role_policy = data.aws_iam_policy_document.pod_identity.json
}
resource "aws_iam_role_policy_attachment" "gateway_bedrock" {
  role       = aws_iam_role.gateway.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockLimitedAccess"
}
data "aws_iam_policy_document" "pod_identity" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals { type = "Service", identifiers = ["pods.eks.amazonaws.com"] }
  }
}
resource "aws_eks_pod_identity_association" "engine" {
  for_each        = toset(["vllm-fp16", "vllm-awq", "sglang-fp16", "triton-int4"])
  cluster_name    = module.eks.cluster_name
  namespace       = "llm"
  service_account = each.key
  role_arn        = aws_iam_role.engine.arn
}
resource "aws_eks_pod_identity_association" "gateway" {
  cluster_name    = module.eks.cluster_name
  namespace       = "gateway"
  service_account = "gateway"
  role_arn        = aws_iam_role.gateway.arn
}

output "cluster_name" { value = module.eks.cluster_name }
output "bucket"       { value = module.bucket.bucket }
output "kubeconfig"   { value = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region}" }
output "grafana"      { value = "kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80" }
