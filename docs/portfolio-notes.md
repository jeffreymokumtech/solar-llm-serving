# Portfolio notes

Target CV entry. Every clause maps to a measured number or a deployed artefact; the table tracks which are done.

> **LLM Inference Platform — vLLM / TensorRT-LLM / AWS EKS.** Deployed open-weight LLMs (Qwen3-8B) with vLLM,
> SGLang and Triton/TensorRT-LLM behind OpenAI-compatible APIs on AWS GPU infrastructure; benchmarked concurrency,
> TTFT/TPOT, throughput, cost per million tokens and GPU-memory utilisation across FP16/INT4 quantisation and tensor
> parallelism; implemented Kubernetes GPU scheduling, queue-depth autoscaling with Karpenter scale-to-zero, persistent
> model caching, Prometheus/DCGM/Grafana observability and a routing gateway with Bedrock fallback; workload derived
> from a real solar O&M product whose LangGraph agent runs against the platform.

| Claim | Evidence | Status |
|---|---|---|
| vLLM, SGLang, Triton/TRT-LLM deployed | `stacks/`, `charts/`, results rows per stack | code written, runs pending |
| TTFT/TPOT/throughput/goodput benchmarked | `docs/results.md` headline + per-task tables | pending |
| $/M tokens | cost columns at the knee, spot + on-demand | pending |
| GPU memory utilisation | `metrics.csv` plateau vs `solarbench budget` prediction | pending |
| FP16 vs INT4 | AWQ rows + quality table | pending |
| Tensor parallelism | TP=4 vs 4×TP=1 rows | pending |
| Kubernetes GPU scheduling | chart, device plugin, GFD, time-slicing experiment | code written |
| Queue-depth autoscaling + scale-to-zero | KEDA ScaledObject + Karpenter NodePool; cold-scale breakdown | code written, demo pending |
| Persistent model cache | PVC + S3 init container; cold vs warm load time | code written, timing pending |
| Observability | dashboards JSON, ServiceMonitors, screenshots | code written, screenshots pending |
| Gateway with Bedrock fallback | `gateway/`, fallback demo | code written, demo pending |
| NuraVolt agent runs against it | `docs/nuravolt-integration.md` transcript | pending |

LinkedIn post draft and the results narrative are written after the runs, from the numbers.
