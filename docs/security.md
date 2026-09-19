# Security notes

Scope: a benchmarking platform that runs for hours, not days, in one AWS account. The controls are the ones that
would carry over to a real deployment; the ones skipped are named.

**Network.** The single GPU instance sits in the default VPC with one security-group ingress: SSH from one
address. Inference (8000), Grafana (3000) and Prometheus (9090) are reached through SSM port forwarding, never
opened. On EKS, engines and gateway are `ClusterIP` only; there is no LoadBalancer or Ingress in this repo. The
EKS API endpoint is public but authenticated (IAM) and can be restricted by CIDR in `main.tf`. A `NetworkPolicy`
on each engine allows ingress only from the gateway, monitoring and solarbench namespaces.

**Identity.** No long-lived keys on nodes or in pods. The instance uses an instance profile (SSM + bucket +
Bedrock). EKS pods use Pod Identity associations: engine service accounts can read the model bucket; the gateway
service account can call Bedrock; nothing else. Karpenter's node role is the module default plus SSM. The HF token
is a Kubernetes Secret referenced with `optional: true`.

**Supply chain.** Every image is pinned to a version tag in `stacks/versions.env` and the Helm values (no `latest`
except the gateway image the repo itself builds). Engine containers run as the vendor ships them (root inside the
container; vLLM and Triton images are not built for non-root). The gateway runs as a non-root user with a read-only
filesystem-compatible layout. IMDSv2 is required on every instance.

**Data.** The workload is synthetic; no customer data leaves the machine. Prometheus retention is 3 days. The S3
bucket is private, encrypted, and destroyed with the stack.

**Tenancy and abuse.** The gateway is the only intended entry point: API keys with per-key rate limits, request
size bounded by the engines' `max-model-len`, and saturation-aware routing so one tenant's burst degrades into
Bedrock fallback rather than into a queue that grows without bound. Prompt content is not logged by the gateway;
token counts and upstream choice are.

**Not done.** TLS between gateway and engines (in-cluster plaintext), mTLS/mesh, image signing and scanning in CI,
Pod Security Admission labels, audit logging to CloudWatch, and per-tenant token budgets. Each is a day of work and
listed here rather than half-implemented.
