#!/usr/bin/env bash
# =========================================================
# Ravyn LLM launcher
#
# Usage:
#   ./scripts/start_llm.sh           # default (Q4_K_M)
#   ./scripts/start_llm.sh fallback  # use Q4_K_S if VRAM is tight
#   ./scripts/start_llm.sh thinking  # enable thinking mode
# =========================================================

set -euo pipefail

cd "$(dirname "$0")/.."

LLAMA_SERVER="/home/exiledr/AI/bin/llama-cli/build/bin/llama-server"

# --- Model selection ---
MODEL_PRIMARY="models/llm/Qwen3.5-9B-Claude-4.6-OS-AV-H-UNCENSORED-THINK-D_AU-Q4_K_M-imat.gguf"
MODEL_FALLBACK="models/llm/Qwen3.5-9B-Claude-4.6-OS-AV-H-UNCENSORED-THINK-D_AU-Q4_K_S-imat.gguf"

MODEL="$MODEL_PRIMARY"
EXTRA_FLAGS=""

for arg in "$@"; do
    case "$arg" in
        fallback)
            MODEL="$MODEL_FALLBACK"
            echo "Using fallback model (Q4_K_S)"
            ;;
        thinking)
            EXTRA_FLAGS="--chat-template-kwargs '{\"enable_thinking\":true}'"
            echo "Thinking mode enabled"
            ;;
    esac
done

if [ ! -f "$MODEL" ]; then
    echo "ERROR: Model not found: $MODEL"
    exit 1
fi

# --- Config ---
CTX=8192
PORT=8081
GPU_LAYERS=99

echo "========================================="
echo "  Ravyn LLM Server"
echo "========================================="
echo "  Model: $(basename $MODEL)"
echo "  Context: $CTX"
echo "  Port: $PORT"
echo "  GPU layers: $GPU_LAYERS"
echo "========================================="

# --- Launch ---
exec $LLAMA_SERVER \
    -m "$MODEL" \
    -c $CTX \
    --port $PORT \
    -ngl $GPU_LAYERS \
    --jinja \
    --temp 0.6 \
    --top-k 20 \
    --top-p 0.95 \
    --presence-penalty 1.5 \
    $EXTRA_FLAGS
