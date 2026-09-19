#!/usr/bin/env bash
# Fills the tensorrtllm_backend inflight-batcher model repository for one engine variant.
# Produces /data/triton/<variant>/{preprocessing,tensorrt_llm,postprocessing,ensemble,tensorrt_llm_bls}.
set -euo pipefail
VARIANT=${1:?fp16|int4}
ENGINES=${ENGINES:-/data/engines}; MODELS=${MODELS:-/data/hf/hub}; REPO_ROOT=${REPO_ROOT:-/data/triton}
TOKENIZER=$(ls -d "$MODELS"/models--Qwen--Qwen3-8B/snapshots/* | head -1)
REPO="$REPO_ROOT/$VARIANT"
docker run --rm -v "$ENGINES":/engines -v "$MODELS":/models -v "$REPO_ROOT":/repo "$TRITON_TRTLLM_IMAGE" bash -lc "
set -euo pipefail
rm -rf /repo/$VARIANT && cp -r /app/all_models/inflight_batcher_llm /repo/$VARIANT
F=/app/tools/fill_template.py; R=/repo/$VARIANT
python3 \$F -i \$R/preprocessing/config.pbtxt tokenizer_dir:$TOKENIZER,triton_max_batch_size:64,preprocessing_instance_count:1
python3 \$F -i \$R/postprocessing/config.pbtxt tokenizer_dir:$TOKENIZER,triton_max_batch_size:64,postprocessing_instance_count:1
python3 \$F -i \$R/tensorrt_llm_bls/config.pbtxt triton_max_batch_size:64,decoupled_mode:True,bls_instance_count:1
python3 \$F -i \$R/ensemble/config.pbtxt triton_max_batch_size:64
python3 \$F -i \$R/tensorrt_llm/config.pbtxt triton_backend:tensorrtllm,triton_max_batch_size:64,decoupled_mode:True,\
engine_dir:/engines/qwen3-8b-$VARIANT,max_tokens_in_paged_kv_cache:,max_attention_window_size:,kv_cache_free_gpu_mem_fraction:0.90,\
batching_strategy:inflight_fused_batching,batch_scheduler_policy:max_utilization,enable_chunked_context:True,\
exclude_input_in_output:True,encoder_input_features_data_type:TYPE_FP16
"
echo "triton model repository at $REPO"
