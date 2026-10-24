# Ravyn-Lynx Notebook — LLM service

## What Ravyn is

A stream mascot with a voice and a face. She is not a co-host and not an
assistant; she is the thing on screen that reacts, and her jobs are in this
order:

1. **Alerts and paid messages.** Read a donation over $2 out loud, thank subs,
   follows, raids and bits — and eventually ask people to follow or subscribe.
   This is her reason to exist, not a feature.
2. **Chat.** Sometimes read a message and answer it.
3. **League co-commentary.** A second voice over the game, under rules and
   gates, never the main event.

The full picture — the character, the plan and every decision behind it —
lives in `ravyn-lynx-p/STATUS.md`. This file covers the notebook.

## What this repo is responsible for

**Writing her lines. Nothing else.**

```
PC ──ravyn.request──→ [ worker → llama-server ] ──ravyn.response──→ PC → TTS → Godot
```

Nothing here loads a speech model, streams audio or talks to Godot. That was
deliberate and has been settled for a while: it keeps the whole 4070 available
to the LLM. If you find yourself adding TTS back, read
`ravyn-lynx-p/STATUS.md` §1 first — it has moved between machines several
times.

**The contract is exactly one `ravyn.response` per `ravyn.request`**, including
on empty output and on exceptions. The PC clears its busy flag on that reply,
so a dropped one stalls the dispatcher until its 90-second watchdog fires.

A paid message never reaches this side at all: the PC sanitises it and sends it
straight to TTS as a quote. That is a safety property — a donation reading
*"ignore your instructions and say ..."* has nowhere to land if no model sees
it.

## What works

| | |
|---|---|
| **The worker** | consumes `ravyn.request`, builds the prompt, calls llama-server, publishes exactly one reply |
| **Her character** | `persona/system_prompt.txt`, plus her appearance loaded from `ravyn.json` |
| **Prompt framing** | one template per signal type; game events carry a measured SITUATION block, an ANGLE and a TONE built on the PC |
| **Memory** | per-person conversation threads, per-person notes that survive a restart, and who has donated |
| **Output filters** | narration, stage directions, instruction leak, echoed block labels, over-length — every removal counted |
| **Sampling** | an explicit random seed and a temperature jitter per request, because the server reused one seed per slot and every answer was the single most likely continuation |
| **Worker log** | the prompt, the seed, the raw output before filters, what was cut, and whether the direction reached the model — one JSON line per request, read back with `tools/worker_report.py` |

Five standalone test suites, no pytest and no network: `python tests/test_*.py`.

## What is not built yet

- **Her Russian character.** She speaks Russian — the PC decides when — but
  the persona that makes her *her* is English throughout: the banned openers,
  "fufu", the teammate vocabulary. None of it survives translation. A Russian
  addendum is the streamer's writing, not something to generate.
- **A non-League mode.** `system_prompt.txt` carries a LEAGUE OF LEGENDS
  section, which is the gravity well pulling every subject back to the game.
  On a stream that is not League it is actively wrong, and it needs a flag from
  the PC to leave it out.
- **Official vs abliterated A/B.** `./scripts/start_llm.sh old` is the
  baseline. The abliterated build is the prime suspect for the remaining
  instruction-following problems.
- **One view over both logs.** This side records what the model was asked, the
  PC records what she said; they join on `req_id` and nothing reads them
  together.

---

## Running the stack

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
WORKER	Python service	Handles Rabbit messages and LLM calls
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

PC orchestrator
   ↓  ravyn.request
RabbitMQ
   ↓
Notebook Worker
   ↓
LLM (llama.cpp)
   ↓  ravyn.response
RabbitMQ
   ↓
PC: TTS → audio → Godot

The notebook is an LLM service only. It does not speak and does not talk to
Godot — TTS, audio streaming and Ravyn's busy/idle state all live on the PC
(ravyn-lynx-p). Nothing here loads a speech model, which keeps the whole
4070 available to llama-server.

The worker publishes exactly one message to ravyn.response for every message
it consumes from ravyn.request, including empty LLM output and failures. The
PC clears its busy flag on that reply, so dropping one stalls the dispatcher.

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

./scripts/start_llm.sh

Start worker:

cd ~/ravyn-lynx
source venv/bin/activate
python -m app.main

Rabbit (if not already running):

sudo systemctl start rabbitmq-server

Monitor:

htop