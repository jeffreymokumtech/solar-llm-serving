# Runbook

The order below is the order the results in `docs/results.md` were produced in. Every cloud step ends with the
matching `down` target; the cost of a forgotten instance is the reason the budget alarm is applied first.

## 0. One-time
```bash
make venv && source .venv/bin/activate
solarbench gen && pytest -q                       # dataset + harness work offline
(cd infra/terraform/budget && terraform init && terraform apply -var email=you@example.com)
aws service-quotas request-service-quota-increase --service-code ec2 --quota-code L-3819A6DF --desired-value 48   # G/VT spot vCPUs
```

## 1. Single GPU sweeps (g5.xlarge spot)
```bash
export TF_VAR_ssh_cidr=$(curl -s ifconfig.me)/32 TF_VAR_bucket_name=solar-llm-models-<account-id>
make up-single
aws ssm start-session --target <instance-id>       # or ssh ubuntu@<ip>
git clone https://github.com/jeffreymokumtech/solar-llm-serving && cd solar-llm-serving
set -a; source stacks/versions.env; source .env; set +a
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
huggingface-cli download $MODEL_FP16 && huggingface-cli download $MODEL_AWQ   # into $HF_CACHE; then aws s3 sync /data/hf s3://$ENGINE_BUCKET/hf
docker compose -f stacks/observability/compose.yml up -d
```
Then per variant (one at a time):
```bash
docker compose -f stacks/vllm/compose.yml --profile awq up -d && until curl -sf localhost:8000/health; do sleep 5; done
for rps in 1 2 4 6 8 12; do
  solarbench run --stack vllm-awq --model $MODEL_FP16 --instance-type g5.xlarge --mode open --rps $rps --duration 240 \
     --metrics-url http://localhost:8000/metrics --metrics-url http://localhost:9400/metrics --notes "prefix cache on, max-num-seqs 128"
done
solarbench quality results/<run>                   # structured-task accuracy for that engine/precision
docker compose -f stacks/vllm/compose.yml --profile awq down
```
Experiments and the flag that isolates each: prefix caching (`fp16` vs `fp16-noprefix`), scheduler limits
(`MAX_NUM_SEQS=32|64|128` env), guided decoding overhead (`--no-guided` on the same dataset), speculative decoding
(`awq` vs `awq-spec`), SGLang (`stacks/sglang`). The g5.12xlarge session: `INSTANCE_TYPE=g5.12xlarge make up-single`,
then `--profile tp4` vs `stacks/vllm/replicas_tp1.yml` through the gateway on :8080.

`solarbench profile results/<run>/metrics.csv` prints the memory and queue peaks and the second at which queueing
started; `solarbench budget --model Qwen/Qwen3-8B --dtype fp16` is the prediction to compare against.

## 2. TensorRT-LLM engines (same instance)
```bash
ENGINES=/data/engines MODELS=/data/hf/hub bash stacks/trtllm/build_engines.sh fp16      # ~15-30 min
ENGINES=/data/engines MODELS=/data/hf/hub bash stacks/trtllm/build_engines.sh int4      # + ~20 min calibration
bash stacks/trtllm/prepare_triton_repo.sh fp16 && bash stacks/trtllm/prepare_triton_repo.sh int4
docker compose -f stacks/trtllm/compose.yml --profile int4 up -d
solarbench run --stack trtllm-int4 --no-guided ... --metrics-url http://localhost:8002/metrics --metrics-url http://localhost:9400/metrics
aws s3 sync /data/engines s3://$ENGINE_BUCKET/engines && aws s3 sync /data/triton s3://$ENGINE_BUCKET/triton
make down-single
```
First-run checks (image tags and script paths inside the containers move between releases): `docker run --rm
$TRTLLM_BUILD_IMAGE ls /app/tensorrt_llm/examples/models/core/qwen` and `docker run --rm $TRITON_TRTLLM_IMAGE ls
/opt/tritonserver/python/openai /app/tools`. Fix `versions.env` before building; never mix a build image and a
Triton image from different TRT-LLM releases.

## 3. EKS
```bash
export TF_VAR_bucket_name=solar-llm-models-<account-id>
make up-eks                                         # ~20 min; ends with kubeconfig configured
kubectl create secret generic hf-token -n llm --from-literal=HF_TOKEN=$HF_TOKEN
BUCKET=$TF_VAR_bucket_name make deploy-vllm-awq     # watch `make nodes`: a g5 spot node appears, then the pod
make deploy-gateway
kubectl -n gateway port-forward svc/gateway 8080:8080 &
solarbench run --stack eks-vllm-awq --base-url http://localhost:8080 --api-key nv-bench --mode open --rps 6 --duration 300
```
Autoscale demo: `kubectl apply -f charts/experiments/load-job.yaml` with `RPS=14`; watch `kubectl get scaledobject,hpa
-n llm -w` and `make nodes`. Record from the events: time KEDA raised the replica count, time Karpenter's node became
Ready, time the pod's startupProbe passed. That is the cold-scale breakdown in the results. Repeat with
`cache.s3.enabled=false` for the no-warm-cache number.

Spot interruption test: `aws ec2 send-spot-instance-interruptions` (FIS) on the GPU node; Karpenter's interruption
queue drains it, the PDB keeps one replica, a replacement node comes up. Time it.

Time-slicing experiment: `kubectl apply -f charts/experiments/time-slicing-configmap.yaml`, set `config.name` on the
device plugin release, deploy two `vllm-awq` replicas with `--gpu-memory-utilization 0.45` each.

Always finish with `make down-eks` and check `kubectl get nodes` is empty of GPU nodes before `terraform destroy`
runs (the target does this), then Cost Explorer the next day.

## 4. NuraVolt integration
See `docs/nuravolt-integration.md`.
