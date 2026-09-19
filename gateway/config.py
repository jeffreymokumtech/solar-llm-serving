from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Upstream:
    name: str
    base_url: str
    model: str
    metrics_url: str | None = None
    max_queue: int = 16  # route away when the engine reports more waiting requests than this


@dataclass
class GatewayConfig:
    """Everything comes from environment variables so the same image runs in
    Compose and in Kubernetes.

    GATEWAY_UPSTREAMS   name=base_url=model[=metrics_url],...   e.g. awq=http://vllm-awq:8000=Qwen/Qwen3-8B-AWQ=http://vllm-awq:8000/metrics
    GATEWAY_ROUTES      task=upstream,...                       e.g. ticket_triage=awq,fault_log_extraction=awq,daily_report_summary=fp16
    GATEWAY_DEFAULT     upstream name used when no route matches
    GATEWAY_API_KEYS    key:rate_per_minute,...                 e.g. nv-demo:120,nv-bench:100000
    GATEWAY_BEDROCK_MODEL_ID  if set, requests fall back to Bedrock (converse API) when all local upstreams are unhealthy or saturated
    """

    upstreams: dict[str, Upstream] = field(default_factory=dict)
    routes: dict[str, str] = field(default_factory=dict)
    default: str = ""
    api_keys: dict[str, int] = field(default_factory=dict)
    bedrock_model_id: str | None = None
    bedrock_region: str = "eu-west-1"
    request_timeout_s: float = 120.0

    @classmethod
    def from_env(cls) -> GatewayConfig:
        cfg = cls()
        for item in filter(None, os.getenv("GATEWAY_UPSTREAMS", "").split(",")):
            parts = item.split("=")
            name, base_url, model = parts[0], parts[1], parts[2]
            metrics = parts[3] if len(parts) > 3 else None
            cfg.upstreams[name] = Upstream(name, base_url, model, metrics)
        for item in filter(None, os.getenv("GATEWAY_ROUTES", "").split(",")):
            task, up = item.split("=")
            cfg.routes[task] = up
        cfg.default = os.getenv("GATEWAY_DEFAULT", next(iter(cfg.upstreams), ""))
        for item in filter(None, os.getenv("GATEWAY_API_KEYS", "").split(",")):
            key, rate = item.split(":")
            cfg.api_keys[key] = int(rate)
        cfg.bedrock_model_id = os.getenv("GATEWAY_BEDROCK_MODEL_ID") or None
        cfg.bedrock_region = os.getenv("AWS_REGION", cfg.bedrock_region)
        return cfg
