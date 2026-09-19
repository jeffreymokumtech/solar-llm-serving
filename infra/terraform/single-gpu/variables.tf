variable "region" {
  type    = string
  default = "eu-west-1"
}
variable "project" {
  type    = string
  default = "solar-llm-serving"
}
variable "instance_type" { # g5.12xlarge for the TP=4 session
  type    = string
  default = "g5.xlarge"
}
variable "spot" {
  type    = bool
  default = true
}
variable "spot_max_price" { # USD/h ceiling; on-demand g5.xlarge is 1.006
  type    = string
  default = "0.60"
}
variable "volume_gb" {
  type    = number
  default = 300
}
variable "ssh_cidr" {
  type        = string
  description = "your IP/32 for SSH; everything else goes through SSM"
}
variable "key_name" {
  type    = string
  default = null
}
variable "bucket_name" {
  type        = string
  description = "globally unique S3 bucket for the model cache"
}