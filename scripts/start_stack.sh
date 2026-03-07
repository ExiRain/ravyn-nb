#!/bin/bash

SESSION="ravyn"

tmux new-session -d -s $SESSION

# ----- pane 0 : LLM -----
tmux send-keys -t $SESSION:0 \
"echo 'Starting LLM'; \
/home/exiledr/AI/bin/llama-cli/llama-server \
-m ~/ravyn-lynx/models/llm/Qwen2.5-14B-Instruct-Q4_K_M.gguf \
-c 4096 \
--port 8081" C-m


# ----- split right : WORKER -----
tmux split-window -h -t $SESSION:0

tmux send-keys -t $SESSION:0.1 \
"echo 'Starting Worker'; \
cd ~/ravyn-lynx && source venv/bin/activate && python -m app.main" C-m


# ----- split bottom left : RABBIT -----
tmux select-pane -t $SESSION:0.0
tmux split-window -v

tmux send-keys -t $SESSION:0.2 \
"echo 'RabbitMQ status'; \
sudo systemctl status rabbitmq-server" C-m


# ----- split bottom right : MONITOR -----
tmux select-pane -t $SESSION:0.1
tmux split-window -v

tmux send-keys -t $SESSION:0.3 \
"htop" C-m


# Arrange panes nicely
tmux select-layout -t $SESSION tiled

# Attach to session
tmux attach -t $SESSION