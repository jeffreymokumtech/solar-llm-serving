# TensorRT-LLM + Triton

Unlike vLLM and SGLang, TensorRT-LLM does not load Hugging Face weights at start-up. The pipeline is:

1. **Convert** the HF checkpoint to the TRT-LLM checkpoint format (or **quantize** it with ModelOpt: INT4-AWQ here,
   calibrated on 256 prompts from the solar workload, `make_calib.py`).
2. **Build** an engine with `trtllm-build`: a TensorRT graph compiled for *this* GPU architecture (A10G = sm86),
   *this* precision and *these* shape limits (max batch, max input, max sequence, max tokens per iteration).
   10 to 40 minutes on an A10G for an 8B model. Change any of those and you rebuild.
3. **Prepare the Triton model repository** from the `tensorrtllm_backend` templates: `preprocessing` (tokenize),
   `tensorrt_llm` (the engine, in-flight batching, paged KV cache), `postprocessing` (detokenize), `ensemble` and
   `tensorrt_llm_bls` (the two ways to chain them). `prepare_triton_repo.sh` fills the templates.
4. **Serve** with Triton, using its OpenAI-compatible frontend so the harness and the gateway are unchanged.

Version pinning matters: the engine must be built by the same TensorRT-LLM version that the Triton container embeds
(`versions.env` pins matching tags). Engines are synced to S3 so the EKS pods pull a ready engine instead of rebuilding.

Guided decoding: Triton's OpenAI frontend does not implement `response_format` for every backend. The harness is run
with `--no-guided` against Triton and the quality evaluator shows the effect on JSON validity.

Not covered: NVIDIA NIM. NIM ships pre-built, profile-selected engines behind the same OpenAI API, which removes
steps 1 to 3, but it needs an NGC API key and an NVIDIA AI Enterprise licence (a 90-day developer licence exists).
`docs/concepts/nim.md` explains what it packages and how it would slot into `charts/llm-server`.
