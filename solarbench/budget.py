"""GPU memory budget calculator: predicts what an inference server needs for a
given model, dtype and scheduler limits, so the prediction can be compared with
what DCGM measures.

    total = weights + KV cache (max_num_seqs * max_model_len tokens) + activations/workspace + CUDA context

vLLM reserves `gpu_memory_utilization * VRAM` in total and sizes the KV cache
to whatever is left after weights and a profiled activation peak; so the useful
question is "how many concurrent tokens fit", which `kv_tokens_that_fit` answers.
"""

from __future__ import annotations

from dataclasses import dataclass

BYTES = {"fp32": 4, "fp16": 2, "bf16": 2, "fp8": 1, "int8": 1, "int4": 0.5, "awq": 0.5, "gptq": 0.5}


@dataclass(frozen=True)
class ModelSpec:
    name: str
    params_b: float
    layers: int
    hidden: int
    heads: int
    kv_heads: int
    head_dim: int
    vocab: int

    @property
    def kv_bytes_per_token(self) -> dict[str, float]:
        # K and V, per layer, kv_heads * head_dim each
        base = 2 * self.layers * self.kv_heads * self.head_dim
        return {"fp16": base * 2, "fp8": base * 1, "int8": base * 1}


MODELS = {
    "Qwen/Qwen3-8B": ModelSpec("Qwen/Qwen3-8B", 8.2, 36, 4096, 32, 8, 128, 151936),
    "Qwen/Qwen3-8B-AWQ": ModelSpec("Qwen/Qwen3-8B-AWQ", 8.2, 36, 4096, 32, 8, 128, 151936),
    "Qwen/Qwen3-4B": ModelSpec("Qwen/Qwen3-4B", 4.0, 36, 2560, 32, 8, 128, 151936),
    "Qwen/Qwen3-32B": ModelSpec("Qwen/Qwen3-32B", 32.8, 64, 5120, 64, 8, 128, 151936),
    "meta-llama/Llama-3.1-8B-Instruct": ModelSpec("meta-llama/Llama-3.1-8B-Instruct", 8.03, 32, 4096, 32, 8, 128, 128256),
}
GPUS_GIB = {"A10G": 24, "L4": 24, "L40S": 48, "A100-40": 40, "A100-80": 80, "H100": 80}


def weights_gib(spec: ModelSpec, dtype: str) -> float:
    b = BYTES[dtype]
    # 4-bit formats keep embeddings and lm_head in 16-bit; approximate that.
    if b < 1:
        emb = 2 * spec.vocab * spec.hidden * 2 / 2**30
        return (spec.params_b * 1e9 * b) / 2**30 + emb * 0.5
    return spec.params_b * 1e9 * b / 2**30


def kv_gib(spec: ModelSpec, tokens: int, kv_dtype: str = "fp16") -> float:
    return spec.kv_bytes_per_token[kv_dtype] * tokens / 2**30


def budget(
    model: str,
    dtype: str = "fp16",
    gpu: str = "A10G",
    max_num_seqs: int = 64,
    max_model_len: int = 8192,
    kv_dtype: str = "fp16",
    gpu_memory_utilization: float = 0.90,
    tensor_parallel: int = 1,
    activation_gib: float = 1.5,
    cuda_context_gib: float = 0.6,
) -> dict[str, float]:
    spec = MODELS[model]
    vram = GPUS_GIB[gpu]
    w = weights_gib(spec, dtype) / tensor_parallel
    reserve = vram * gpu_memory_utilization
    kv_avail = max(0.0, reserve - w - activation_gib - cuda_context_gib)
    per_tok = spec.kv_bytes_per_token[kv_dtype] / tensor_parallel / 2**30
    fit_tokens = int(kv_avail / per_tok) if per_tok else 0
    worst_case_need = kv_gib(spec, max_num_seqs * max_model_len, kv_dtype) / tensor_parallel
    return {
        "vram_gib": vram,
        "reserved_gib": round(reserve, 2),
        "weights_gib_per_gpu": round(w, 2),
        "activation_gib": activation_gib,
        "cuda_context_gib": cuda_context_gib,
        "kv_available_gib": round(kv_avail, 2),
        "kv_bytes_per_token_per_gpu": round(spec.kv_bytes_per_token[kv_dtype] / tensor_parallel),
        "kv_tokens_that_fit": fit_tokens,
        "concurrent_seqs_at_max_len": fit_tokens // max_model_len if max_model_len else 0,
        "concurrent_seqs_at_2k": fit_tokens // 2048,
        "worst_case_kv_gib_for_limits": round(worst_case_need, 2),
        "predicted_used_gib": round(min(vram, w + activation_gib + cuda_context_gib + kv_avail), 2),
    }
