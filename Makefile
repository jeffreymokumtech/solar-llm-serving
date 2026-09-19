# Local + cloud workflow. Every AWS target is explicit; nothing here runs on a timer.
SHELL := /bin/bash
PY ?= python3
TF_SINGLE := infra/terraform/single-gpu
TF_EKS := infra/terraform/eks

.PHONY: help venv test lint gen report mock bench-mock up-single down-single up-eks down-eks deploy-% undeploy-% helm-lint tf-validate

help:            ## list targets
	@grep -E '^[a-zA-Z_%-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n", $$1, $$2}'

venv:            ## create .venv with dev + gateway extras (uv)
	uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -e ".[dev,gateway]"

lint:            ## ruff + helm lint + terraform validate (tools that are installed)
	ruff check . && ruff format --check .
	@command -v helm >/dev/null && $(MAKE) helm-lint || echo "helm not installed, skipping chart lint"
	@command -v terraform >/dev/null && $(MAKE) tf-validate || echo "terraform not installed, skipping validate"

test:            ## unit tests (no GPU, in-process mock server)
	pytest -q

gen:             ## regenerate the committed workload dataset
	solarbench gen --seed 42 --n 200

report:          ## regenerate docs/results.md and plots from results/
	solarbench report

mock:            ## run the mock OpenAI server on :8000 (for harness/gateway development)
	uvicorn solarbench.mock_server:app --port 8000

bench-mock:      ## smoke-run the harness against the mock server
	solarbench run --stack mock --base-url http://localhost:8000 --model mock-model --mode open --rps 20 --duration 10 --metrics-url http://localhost:8000/metrics --out /tmp/solarbench-mock

helm-lint:
	helm lint charts/llm-server && for v in charts/llm-server/values-*.yaml; do helm template t charts/llm-server -f $$v >/dev/null; done
	helm lint charts/gateway && helm template g charts/gateway >/dev/null

tf-validate:
	for d in $(TF_SINGLE) $(TF_EKS) infra/terraform/budget; do (cd $$d && terraform init -backend=false -input=false >/dev/null && terraform validate); done
	terraform fmt -check -recursive infra/terraform

# ----------------------------------------------------------------- AWS: single GPU instance
up-single:       ## spot g5.xlarge (INSTANCE_TYPE=g5.12xlarge for TP=4); needs TF_VAR_ssh_cidr and TF_VAR_bucket_name
	cd $(TF_SINGLE) && terraform init -input=false && terraform apply -auto-approve -var instance_type=$${INSTANCE_TYPE:-g5.xlarge}
	@cd $(TF_SINGLE) && terraform output ssm_shell

down-single:     ## destroy the instance (bucket survives: force_destroy is on, so `terraform destroy` empties it too if you remove the module)
	cd $(TF_SINGLE) && terraform destroy -auto-approve -target=aws_instance.gpu

# ----------------------------------------------------------------- AWS: EKS
up-eks:          ## create the cluster and add-ons (~20 min); needs TF_VAR_bucket_name
	cd $(TF_EKS) && terraform init -input=false && terraform apply -auto-approve
	@cd $(TF_EKS) && terraform output -raw kubeconfig | bash

down-eks:        ## delete every workload first (so Karpenter drains GPU nodes and PVCs are released), then the cluster
	-kubectl delete scaledobject --all -A --timeout=60s
	-helm uninstall -n llm $$(helm list -n llm -q) 2>/dev/null
	-helm uninstall -n gateway gateway 2>/dev/null
	-kubectl delete pvc --all -n llm --timeout=120s
	-kubectl wait --for=delete node -l karpenter.sh/nodepool=gpu-a10g --timeout=300s
	cd $(TF_EKS) && terraform destroy -auto-approve

deploy-%:        ## deploy one engine: make deploy-vllm-awq | deploy-vllm-fp16 | deploy-sglang-fp16 | deploy-triton-int4
	helm upgrade --install $* charts/llm-server -n llm -f charts/llm-server/values-$*.yaml \
	  --set cache.s3.enabled=true --set cache.s3.bucket=$${BUCKET:?set BUCKET} --wait --timeout 30m

undeploy-%:
	helm uninstall $* -n llm

deploy-gateway:  ## deploy the gateway (image must exist in GHCR; see .github/workflows/image.yml)
	kubectl -n gateway create secret generic gateway-api-keys --from-literal=GATEWAY_API_KEYS="$${GATEWAY_API_KEYS:-nv-demo:600,nv-bench:100000}" --dry-run=client -o yaml | kubectl apply -f -
	helm upgrade --install gateway charts/gateway -n gateway --wait

logs-%:
	kubectl -n llm logs deploy/$* -f --tail=100

nodes:           ## watch Karpenter provision / remove GPU nodes
	kubectl get nodes -L karpenter.sh/nodepool,karpenter.sh/capacity-type,node.kubernetes.io/instance-type -w
