#!/usr/bin/env bash
# =========================================================
# Ravyn LLM launcher
#
# This script is the ONLY thing that decides which model runs.
# app/settings.py does not — it only carries request-time
# parameters (temperature, max_tokens) that the client sends.
#
# Usage:
#   ./scripts/start_llm.sh            # Q5_K_M official  (default)
#   ./scripts/start_llm.sh q4         # Q4_K_M official, more context
#   ./scripts/start_llm.sh old        # the abliterated build, for A/B
#   ./scripts/start_llm.sh q4 thinking
#
#   RAVYN_CTX=6144 ./scripts/start_llm.sh    # override the context size
# =========================================================

set -euo pipefail

cd "$(dirname "$0")/.."

LLAMA_SERVER="/home/exiledr/AI/bin/llama-cli/build/bin/llama-server"

MODEL_Q5="models/llm/Qwen3.5-9B-Q5_K_M.gguf"
MODEL_Q4="models/llm/Qwen3.5-9B-Q4_K_M.gguf"
MODEL_OLD="models/llm/Qwen3.5-9B-Claude-4.6-OS-AV-H-UNCENSORED-THINK-D_AU-Q4_K_S-imat.gguf"

# Context differs per quant because the 4070 has ~8.1GB and the weights do
# not. Rough totals with q8_0 KV and llama.cpp's compute buffers:
#   Q5_K_M + 4096  ~7.5GB   (0.6GB spare — fine, but do not push it)
#   Q4_K_M + 8192  ~6.8GB   (1.3GB spare)
#   Q5_K_M + 8192  ~7.8GB   (0.3GB spare — OOMs when anything else wants VRAM)
MODEL="$MODEL_Q5"
LABEL="Q5_K_M (official)"
CTX=4096
EXTRA_FLAGS=""

for arg in "$@"; do
    case "$arg" in
        q5)       MODEL="$MODEL_Q5";  LABEL="Q5_K_M (official)";     CTX=4096 ;;
        q4)       MODEL="$MODEL_Q4";  LABEL="Q4_K_M (official)";     CTX=8192 ;;
        old)      MODEL="$MODEL_OLD"; LABEL="Q4_K_S (abliterated)";  CTX=4096 ;;
        thinking) EXTRA_FLAGS="--chat-template-kwargs '{\"enable_thinking\":true}'" ;;
        *)
            echo "Unknown argument: $arg"
            echo "Usage: $0 [q5|q4|old] [thinking]"
            exit 1
            ;;
    esac
done

CTX="${RAVYN_CTX:-$CTX}"
PORT=8081
GPU_LAYERS=99

if [ ! -f "$MODEL" ]; then
    echo "ERROR: Model not found: $MODEL"
    echo
    echo "Available in models/llm:"
    ls -1 models/llm/*.gguf 2>/dev/null | sed 's/^/  /' || echo "  (none)"
    exit 1
fi

echo "========================================="
echo "  Ravyn LLM Server"
echo "========================================="
echo "  Model:   $LABEL"
echo "  File:    $(basename "$MODEL")"
echo "  Context: $CTX  (KV cache quantised to q8_0)"
echo "  Port:    $PORT"
echo "========================================="
echo "  Watch nvidia-smi — if this OOMs, run with q4"
echo "  or lower it: RAVYN_CTX=2048 $0"
echo "========================================="

exec $LLAMA_SERVER \
    -m "$MODEL" \
    -c $CTX \
    --port $PORT \
    -ngl $GPU_LAYERS \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --jinja \
    --temp 0.6 \
    --top-k 20 \
    --top-p 0.95 \
    --presence-penalty 1.5 \
    $EXTRA_FLAGS
