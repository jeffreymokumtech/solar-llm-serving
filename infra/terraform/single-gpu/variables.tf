variable "region"         { type = string, default = "eu-west-1" }
variable "project"        { type = string, default = "solar-llm-serving" }
variable "instance_type"  { type = string, default = "g5.xlarge" }   # g5.12xlarge for the TP=4 session
variable "spot"           { type = bool,   default = true }
variable "spot_max_price" { type = string, default = "0.60" }        # USD/h ceiling; on-demand g5.xlarge is 1.006
variable "volume_gb"      { type = number, default = 300 }
variable "ssh_cidr"       { type = string, description = "your IP/32 for SSH; everything else goes through SSM" }
variable "key_name"       { type = string, default = null }
variable "bucket_name"    { type = string, description = "globally unique S3 bucket for the model cache" }
