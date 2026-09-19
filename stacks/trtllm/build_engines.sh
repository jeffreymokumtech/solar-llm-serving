#!/usr/bin/env bash
# Builds two TensorRT-LLM engines for Qwen3-8B on the current GPU and stores them under $ENGINES (and S3 if set).
# Engines are compiled for ONE GPU architecture (A10G = sm86), one precision and fixed shape limits; rebuild when any changes.
#   set -a; source stacks/versions.env; set +a
#   ENGINES=/data/engines MODELS=/data/hf/hub bash stacks/trtllm/build_engines.sh fp16
#   ENGINES=/data/engines MODELS=/data/hf/hub bash stacks/trtllm/build_engines.sh int4
set -euo pipefail
VARIANT=${1:?fp16|int4}
ENGINES=${ENGINES:-/data/engines}
MODELS=${MODELS:-/data/hf/hub}
MODEL_DIR=$(ls -d "$MODELS"/models--Qwen--Qwen3-8B/snapshots/* | head -1)   # HF cache layout
MAX_BATCH=${MAX_BATCH:-64}; MAX_INPUT=${MAX_INPUT:-8192}; MAX_SEQ=${MAX_SEQ:-8704}; MAX_TOKENS=${MAX_TOKENS:-8192}
OUT="$ENGINES/qwen3-8b-$VARIANT"
mkdir -p "$OUT" "$ENGINES/ckpt"

docker run --rm --gpus all --ipc=host -v "$MODELS":/models -v "$ENGINES":/engines -v "$PWD/stacks/trtllm":/work \
  "$TRTLLM_BUILD_IMAGE" bash -lc "
set -euo pipefail
cd /app/tensorrt_llm
if [ '$VARIANT' = fp16 ]; then
  python3 examples/models/core/qwen/convert_checkpoint.py --model_dir $MODEL_DIR --output_dir /engines/ckpt/fp16 --dtype float16
  CKPT=/engines/ckpt/fp16
else
  # INT4 AWQ via ModelOpt; calibration texts come from the solar workload (see make_calib.py) instead of a generic corpus.
  python3 /work/make_calib.py --out /engines/calib
  python3 examples/quantization/quantize.py --model_dir $MODEL_DIR --dtype float16 --qformat int4_awq --awq_block_size 128 \
      --calib_size 256 --calib_dataset /engines/calib --output_dir /engines/ckpt/int4_awq
  CKPT=/engines/ckpt/int4_awq
fi
trtllm-build --checkpoint_dir \$CKPT --output_dir /engines/qwen3-8b-$VARIANT \
  --gemm_plugin auto --gpt_attention_plugin auto --kv_cache_type paged --use_paged_context_fmha enable \
  --max_batch_size $MAX_BATCH --max_input_len $MAX_INPUT --max_seq_len $MAX_SEQ --max_num_tokens $MAX_TOKENS
ls -la /engines/qwen3-8b-$VARIANT
"
echo "engine at $OUT"
if [ -n "${ENGINE_BUCKET:-}" ]; then
  aws s3 sync "$OUT" "s3://$ENGINE_BUCKET/engines/qwen3-8b-$VARIANT/"
fi
