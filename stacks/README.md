# Serving stacks

Each directory holds a Docker Compose file for one engine, all exposing the same OpenAI-compatible
`/v1/chat/completions` on `:8000` and Prometheus metrics, so `solarbench run` needs only `--stack` and `--base-url`.

| Directory | Engine | Variants (compose profiles) |
|---|---|---|
| `vllm/` | vLLM | `fp16`, `awq`, `fp16-noprefix`, `awq-spec` (n-gram speculative decoding), `tp4` (g5.12xlarge) |
| `sglang/` | SGLang | `fp16`, `awq` |
| `trtllm/` | TensorRT-LLM engines behind Triton | `fp16`, `int4` (engines built first by `build_engines.sh`) |
| `observability/` | DCGM exporter + Prometheus + Grafana | always alongside an engine |

Run exactly one engine profile at a time on a single A10G: two 8B engines do not fit next to each other.

```bash
set -a; source stacks/versions.env; set +a
docker compose -f stacks/observability/compose.yml up -d
docker compose -f stacks/vllm/compose.yml --profile awq up -d
curl -s localhost:8000/v1/models | jq .
solarbench run --stack vllm-awq --model $MODEL_AWQ --instance-type g5.xlarge \
  --metrics-url http://localhost:8000/metrics --metrics-url http://localhost:9400/metrics \
  --mode open --rps 2 --duration 180
```
