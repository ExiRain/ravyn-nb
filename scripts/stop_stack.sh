#!/bin/bash

tmux kill-session -t ravyn 2>/dev/null
pkill -f llama-server
pkill -f uvicorn
pkill -f "python -m app"
echo "Stack stopped"