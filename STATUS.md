# Ravyn — notebook state

The notebook is an **LLM service and nothing else**. It writes her lines and
publishes them; the PC speaks them.

**The full project document lives in `ravyn-lynx-p/STATUS.md`** — decisions,
architecture, the character and reaction plan. This file covers only what is
specific to this machine.

---

## Role

```
PC ──ravyn.request──→ [ worker → llama-server ] ──ravyn.response──→ PC → TTS → Godot
```

Nothing here loads a speech model or talks to Godot. That was deliberate: it
keeps the whole 4070 available to the LLM. If you find yourself adding TTS back,
re-read `ravyn-lynx-p/STATUS.md` §1 first — it has moved between machines
several times and the current arrangement is settled.

**Contract:** exactly one `ravyn.response` per `ravyn.request`, including on
empty LLM output and on exceptions. The PC clears its busy flag on that reply,
so a dropped one stalls the dispatcher until its 90s watchdog fires.

## Hardware

RTX 4070, **8188MiB, ~8.1GB free** (13MiB at idle). Earlier planning assumed
6.9GB and was too conservative.

## Running

```bash
./scripts/start_stack.sh          # tmux: LLM | WORKER | RABBIT | MONITOR
./scripts/setup_venv.sh           # first time
```

### Model choice

**`scripts/start_llm.sh` is the only thing that decides which model runs.**
`app/settings.py` does not — it carries request parameters only (temperature,
max_tokens, thinking). Model paths used to be duplicated there and silently did
nothing; they were removed in `dd61311`.

```bash
./scripts/start_llm.sh            # Q5_K_M official, 4096 ctx  (~7.5GB)
./scripts/start_llm.sh q4         # Q4_K_M official, 8192 ctx  (~6.8GB)
./scripts/start_llm.sh old        # abliterated Q4_K_S, 4096   (A/B baseline)
RAVYN_CTX=6144 ./scripts/start_llm.sh
```

Context differs per quant because the weights do not fit the same way. Q5 at
8192 lands ~7.8GB and OOMs as soon as anything else wants VRAM, so the larger
context goes to the smaller quant. KV cache is quantised to `q8_0` — that is
what makes 8192 fit at Q4, and it is the headroom freed by moving TTS to the PC.

### The models

| | Notes |
|---|---|
| `Qwen3.5-9B-Q5_K_M` | official, Apache 2.0, 201 languages |
| `Qwen3.5-9B-Q4_K_M` | official, more context room |
| `Qwen3.5-9B-...-UNCENSORED-...-Q4_K_S-imat` | abliterated community merge |

**The abliterated build is the prime suspect for the remaining quality
problems.** Abliteration drops benchmarks across the board, hardest on
instruction-following and non-English — which is exactly what fails: narrating
herself despite a prohibition, blowing the 2–3 sentence rule, repeating a whole
prior answer once.

Her persona does not need an uncensored model. It asks for "damn, hell", roasting
bad plays, calling teammates apes — all within what official instruct models do.
The abliteration tax is being paid for headroom the prompt forbids using.

Qwen3.8's smallest open weight is 27B (4-bit ≈ 17–19GB). Not an option.

---

## Output filters (`adapters/mq/rabbitmq.py`)

The model ignores instructions it is given, so the prompt is backed by filters.

**`_strip_narration`** — she was speaking prose about herself aloud: *"Ravyn
tilts her head at the chat notification"*, *'"NewViewer_123," she murmurs'*.
Matches **by name**, never by pronoun — "she/he + verb" would eat real speech,
since she talks about teammates that way constantly. Also strips dialogue
attributions and quotation marks. Returns an explicit flag so `Stripped
narration ->` only logs when narration was actually removed (`c27d4aa`).

**`_gate_tch`** — `TCH_COOLDOWN = 25`, one number, tune by ear. This took three
attempts: the dismissive game template *asked* for "tch" on the five most
frequent event types, the prompt banned it, and the filter only stripped
position 0. Then `\btch\b` missed "tchk" entirely — no word boundary before the
k. Now matches every spelling (tch, tchk, tchh, tsk, tsk tsk) while leaving
catch, watch, kitchen, match, tech, thick, stitch, touch alone.

**`_gate_fufu`** — unchanged, one per eight responses, never on game events.

---

## Persona (`persona/`)

`system_prompt.txt` is her character. `context_templates.py` frames each signal
type; `context_builder.py` routes to a template by `event_type`.

**Game events carry an identity block** (`4f7c9f1`) naming Exiled, his champion,
and that he is hers. Before it she saw `GAME EVENT: You died` with no indication
whose death it was, and roasted him like a stranger — `player_name` was in the
context but no template read it, and `USER_MEMORY` keys off `context["user"]`
which game events never set.

`context_builder.py` reads `context["lang"]` — `"ru"` forces Russian,
`"multilang"` mirrors the speaker. The PC decides which; see the full document.

**Known gap:** the persona is English. Banned openers, "fufu", the teammate
vocabulary — none survive translation. A Russian addendum is the streamer's
writing, not something to generate.

---

## Open here

- **Russian output quality is untested.** Set `LANG_REPLY = "ru"` on the PC,
  send chat, listen. Ten minutes, and it gates every RU decision.
- **Official vs abliterated A/B** — `./scripts/start_llm.sh old` is the baseline
- Watch for `Stripped narration ->` and `ALL narration, saying nothing:` — if
  they fire often on the official model the prompt is not landing; if quiet, that
  problem was the abliterated build
- One response once repeated an entire previous answer verbatim before answering.
  Seen once, never reproduced. Suspect history/template; 8192 context may fix it.
- No PR on this repo. **It must merge together with the PC** — merging the PC
  alone brings back double-voice and silent quote mode.
- Stray submodule: a gitlink at path `ravyn-nb/` with no `.gitmodules`
- `adapters/audio/stream_api.py` and `adapters/tts/qwen_tts.py` are no longer
  wired to anything. Kept as reference; delete when you are confident.
