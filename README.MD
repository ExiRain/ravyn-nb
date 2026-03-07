Ravyn-Lynx Runtime Stack

This project runs several services simultaneously on the notebook.
To simplify development and debugging, the services are orchestrated using tmux.

tmux allows:

running multiple services in one terminal session

monitoring logs simultaneously

detaching and reconnecting without stopping the services
Starting the Stack

The entire runtime environment can be started with:

./scripts/start_stack.sh

This script launches a tmux session called:

ravyn

Inside the session four panes are created automatically.

Runtime Layout

The default layout is:

+-------------------+-------------------+
|        LLM        |      WORKER       |
|                   |                   |
+-------------------+-------------------+
|       RABBIT      |      MONITOR      |
|                   |                   |
+-------------------+-------------------+
Pane	Service	Purpose
LLM	llama.cpp server	Runs the language model
WORKER	Python service	Handles Rabbit messages, LLM calls, and TTS
RABBIT	RabbitMQ	Message queue backend
MONITOR	htop	System monitoring
tmux Controls

tmux commands start with the prefix key:

CTRL + B

After pressing the prefix, press the second key.

Switching Panes

Move between panes using arrow keys:

CTRL + B → Arrow key

Example:

CTRL + B → Left
CTRL + B → Right
CTRL + B → Up
CTRL + B → Down
Scrolling Logs

tmux uses copy mode for scrolling.

Enter scroll mode:

CTRL + B → [

Now you can scroll with:

Arrow keys
PageUp
PageDown

Exit scroll mode:

ESC

or

Q
Zoom Pane (Fullscreen)

To temporarily focus on one pane:

CTRL + B → Z

Press again to restore the layout.

Detach Session

Detach from tmux while leaving services running:

CTRL + B → D

This returns to the normal terminal while the stack continues running.

Reattach Session

To reconnect later:

tmux attach -t ravyn
Service Overview

The runtime pipeline currently follows this flow:

PC client
   ↓
RabbitMQ
   ↓
Notebook Worker
   ↓
LLM (llama.cpp)
   ↓
TTS (Qwen-TTS)
   ↓
Audio Stream

The notebook acts as the AI runtime host, while the PC acts as the control and playback client.

Monitoring

The monitor pane runs:

htop

If it is not installed:

sudo dnf install htop

This allows real-time observation of:

CPU usage

GPU load (via nvidia-smi if needed)

memory consumption

running services

Development Notes

During development the system is typically run on a local network.
Later deployments may use:

VPN connectivity

remote notebook hosting

containerized services

Future improvements may include:

Prometheus metrics

Grafana dashboards

automatic service restart

Start LLM:

/home/exiledr/AI/bin/llama-cli/llama-server \
-m ~/ravyn-lynx/models/llm/Qwen2.5-14B-Instruct-Q4_K_M.gguf \
-c 4096 \
--port 8081

Start worker:

cd ~/ravyn-lynx
source venv/bin/activate
python -m app.main

Rabbit (if not already running):

sudo systemctl start rabbitmq-server

Monitor:

htop