#!/bin/bash
# =========================================================
# Ravyn runtime stack — launches all services in tmux
#
# Usage:
#   ./scripts/start_stack.sh              # default
#   ./scripts/start_stack.sh fallback     # use Q4_K_S model
#   ./scripts/start_stack.sh thinking     # enable thinking mode
# =========================================================

SESSION="ravyn"
LLM_ARGS="${@}"

tmux new-session -d -s $SESSION

# ----- pane 0 : LLM -----
tmux send-keys -t $SESSION:0 \
"echo 'Starting LLM'; \
cd ~/ravyn-lynx && ./scripts/start_llm.sh $LLM_ARGS" C-m

# ----- split right : WORKER -----
tmux split-window -h -t $SESSION:0

tmux send-keys -t $SESSION:0.1 \
"echo 'Waiting 5s for LLM to load...'; sleep 5; \
echo 'Starting Worker'; \
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

# Arrange panes
tmux select-layout -t $SESSION tiled

# Attach
tmux attach -t $SESSION
