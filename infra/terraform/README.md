# Infrastructure

| Module | What | Typical cost while up |
|---|---|---|
| `budget/` | AWS Budget on the `project` tag with 50/80/100 % email alerts. Apply first. | 0 |
| `single-gpu/` | One g5 spot instance from the Deep Learning Base AMI, 300 GB gp3, SSM access, S3 model bucket. For the Compose sweeps and TRT-LLM engine builds. | ~$0.40/h (g5.xlarge spot), ~$2.2/h (g5.12xlarge spot) |
| `eks/` | VPC, EKS 1.33, one small system node group, Karpenter 1.6 with a g5 spot NodePool (scale-to-zero), NVIDIA device plugin + GFD, DCGM exporter, kube-prometheus-stack, KEDA, gp3 storage class, Pod Identity roles. | ~$0.10/h control plane + ~$0.20/h system nodes + ~$0.05/h NAT, plus GPU nodes only while pods exist |

Everything carries the `project=solar-llm-serving` tag so Cost Explorer can show the total. `make down-eks` /
`make down-single` destroy; nothing is meant to stay up between working sessions.

Prerequisites: AWS CLI v2 with credentials, Terraform >= 1.6, kubectl, helm; a Service Quota of at least 48 vCPU for
"All G and VT Spot Instance Requests" in the region (the default is often 0); a Hugging Face token if the model is gated.
