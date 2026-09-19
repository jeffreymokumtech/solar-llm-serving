# Running NuraVolt's agent against this platform

[NuraVolt](https://github.com/jeffreymokumtech/nuravolt) is a solar and storage operations product whose LangGraph
agent plans tool calls against the product's MCP server and asks a human before every write. Its model provider is
configurable: Bedrock in production, any OpenAI-compatible endpoint locally (`OPENAI_BASE_URL`). This platform is
that endpoint, which makes the integration a configuration change and the agent's own eval suite the acceptance
test.

The `agent_tool_turn` task class in `solarbench` is derived from the agent's real prompt shape: the 17 MCP tool
schemas (copied into `solarbench/workload/nuravolt_tools.json`) plus a short user request, answered with one JSON
tool call. It is the prefix-caching workload because the ~2.5k-token system prompt is identical on every turn.

## Procedure
```bash
# 1. platform up (single instance or EKS), gateway reachable on localhost:8080 with key nv-demo
# 2. NuraVolt local stack
git clone https://github.com/jeffreymokumtech/nuravolt && cd nuravolt
OPENAI_BASE_URL=http://host.docker.internal:8080/v1 OPENAI_API_KEY=nv-demo OPENAI_MODEL=Qwen/Qwen3-8B \
AGENT_MODEL_PROVIDER=openai docker compose up -d
# 3. the agent's live eval: its ten scripted cases against the real model and its MCP server
python scripts/agent_eval_live.py --cases nuravolt/agent/evals/cases.yaml
# 4. an approved write, end to end, through the platform
python -m nuravolt.agent chat --thread demo "Open a HIGH priority ticket on Kilima inverter INV-07 for the string fault."
```

## What to record
- The eval's trajectory pass count with Qwen3-8B-AWQ on an A10G versus the Bedrock model it normally uses.
- Gateway metrics during the eval: prefix-cache hit ratio on the agent turns, TTFT p50 for an agent step, cost per
  agent turn from `$/M tokens` × tokens per turn.
- One transcript of the approval interrupt served by this platform (pasted below once produced).

## Transcript
_To be added after the first run._
