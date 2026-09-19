variable "region"       { type = string, default = "eu-west-1" }
variable "project"      { type = string, default = "solar-llm-serving" }
variable "cluster_name" { type = string, default = "solar-llm" }
variable "k8s_version"  { type = string, default = "1.33" }
variable "bucket_name"  { type = string, description = "model cache bucket (same as single-gpu if you keep one)" }
variable "gpu_instance_families" { type = list(string), default = ["g5"] }
variable "gpu_capacity_types"    { type = list(string), default = ["spot", "on-demand"] }  # spot first, on-demand fallback
variable "gpu_max_cpus"          { type = string, default = "64" }                          # NodePool limit: 64 vCPU ~ one g5.12xlarge + a few g5.xlarge
variable "system_instance_type"  { type = string, default = "m6i.large" }
variable "budget_tag"   { type = string, default = "solar-llm-serving" }
