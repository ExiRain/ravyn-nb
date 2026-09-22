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

The PC may now **synthesise a response and then never speak it** — the voice
gate rework asks the VAD again after TTS, and drops the line if the streamer is
still talking (`ravyn-lynx-p/STATUS.md` §6). Nothing changes on this side: the
contract is unchanged, the response is still consumed, and busy still clears.
It is worth knowing only because a line can now appear in the worker log and
never be heard, which is correct rather than a lost message.

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

## Game events are not conversation

`memory.get_history(source)` returns **nothing** for `source == "game"`, and
game reactions never enter it.

They used to. Every game event went through `add_exchange` like a chat message,
so the next one reached the LLM with her last five game reactions replayed as a
dialogue — each "user turn" being the entire framed prompt, SITUATION block and
all. `MAX_HISTORY` is 5; the live session was terminated because she said the
same thing 5-6 times in a row. That is what a five-deep history of near-identical
turns predicts. Opener-based anti-repetition could not help: she varied the first
four words and repeated the substance.

A game reaction is not a turn in a conversation — nobody said anything to her —
and her continuity across a game comes from the SITUATION block, which is current
and accurate where a transcript of five stale prompts is neither.

What she *said* is kept separately in `recent_game_lines` (capped at
`MAX_GAME_LINES`, cleared on GameStart, not persisted) for the sole purpose of
telling her not to say it again: *"do not repeat any of them, and do not
rephrase them"*. `get_repetition_guard(source)` returns that for game events and
the old opener list for chat, where varying the phrasing is all that is wanted.

Game lines also stay out of `exchange_count`, so they never trigger memory
compression — a game produces dozens and they would crowd out the chat they are
supposed to sit alongside.

`python tests/test_game_memory.py` — 15 checks.
`python tests/test_persona_context.py` — 31 checks, including that no
scaffolding reaches memory and that her appearance carries its narration rule.
`python tests/test_per_user_memory.py` — 31 checks: threads do not mix, notes
are written from one person's words, eviction is bounded.

---

## Two bugs that made her look broken

**Every response was the single most likely continuation.** The request carried
`temperature: 0.7` and no `seed`, and llama-server reuses one seed per slot — so
the sampler was deterministic. Asking *"@Ravyn ты умная?"* twice returned a
**byte-identical** answer, mood tags and all. It was not a prompt problem: the
prompt grew by an exchange each time (6792 → 6823 → 6854 chars) and the output
still did not move. Temperature had been doing nothing since the server started.
`run_llm` now sends an explicit random seed per request, plus a ±0.05
temperature jitter as belt and braces: `seed` is an OpenAI-compat field and not
every llama.cpp build applies it per request on `/v1/chat/completions`. The
jitter is inaudible but perturbs the softmax enough that a fixed seed cannot
land on the same token sequence twice. `run_llm_simple` gets neither,
deliberately — memory compression is better off deterministic.

**The seed is logged**, which makes the next diagnosis a one-liner:

```
[llm] Prompt: 6 messages, ~6823 chars, seed=272306291
```

| What the log shows | What it means |
|---|---|
| no `seed=` at all | the worker is still running old code — **restart it**, `git pull` does not reload a live process |
| different seeds, identical replies | the server ignored the seed; the jitter is now the thing carrying it |
| different seeds, different replies | fixed |

**She pulled every subject back to the game.** Asked about her clothes, or
whether she was clever, she answered with his bad plays. Her persona carries a
LEAGUE OF LEGENDS section and her history is full of game reactions, so left
alone that is the gravity well. Both chat templates now say to answer what was
actually asked and not to steer back to the game uninvited, and the persona says
plainly that the game is a thing she watches rather than the only thing she is.

## What the model was asked, and what it answered

`logs/worker-<date>-<time>.jsonl`, one JSON line per request, written by
`app/worker_log.py` from the moment the worker connects.

The PC has recorded what she *said* since `ravyn-lynx-p/orchestrator/session_log.py`
landed. That is enough to prove she repeated herself and to name the angle
that produced it, and not enough to say why. Four things existed only as
stdout in the WORKER pane and scrolled away with it:

- **the prompt** — the log said `~6823 chars, seed=272306291`, never the text,
  so there was no way to check whether the angle was even in it
- **the seed**, per request
- **her raw output**, before the filters. The PC receives the cleaned text and
  cannot know what was cut
- **which filter fired on which line** — the tally gives the rate, not the
  sentence

A live session produced four near-identical death roasts under three
different angles. Whether the model ignored the direction, whether the
direction never arrived, or whether a filter removed the part that differed,
is a question only this file can answer.

Per record: `req_id`, source, the knobs the PC chose (`config_key`,
`angle_id`, `tone`), the **framed prompt** (the last user message — SITUATION,
ANGLE and TONE verbatim), `prompt_chars`, `history_turns`, `seed`, the
temperature actually used, `llm_s`, the **raw** model output, the text before
the filters, what they `removed`, and what was finally said. The system prompt
is written **once per run** as its own record rather than on every line: it is
identical each time and would otherwise be nine tenths of the file.

**`run_llm` returns the seed and the temperature now.** They were printed and
discarded, which meant the one diagnosis they exist for — different seeds,
identical replies, so the server ignored the seed — had to be made by eye
against scrollback. `tools/worker_report.py` makes it in one line.

**Did the direction land?** Checked at record time, where the context the PC
sent and the prompt built from it are both in hand: each of `angle`,
`tone_instruction` and `situation` is matched against the framed prompt and
the answer stored as a flag. Guessing it afterwards from block headings does
not work — the angle has one (`YOUR ANGLE THIS TIME`), the tone and the
situation are injected as bare text.

**Games are marked, not split into files.** The worker has no idea what a game
is — it answers requests — but the boundaries arrive on their own as
`event_type`, so `GameStart` and `GameEnd` write markers into the same file.
One file per run matches the PC's session log, and a game that ends in a crash
keeps its first half rather than living in a file that was never closed. A
game the log ends inside runs to the last line instead of being dropped.

```bash
python tools/worker_report.py            # newest log
python tools/worker_report.py --game 2   # the second game on its own
python tools/worker_report.py --prompt   # the persona exactly as it ran
python tools/worker_report.py --line 14  # one request: prompt, raw, said
```

The report answers: how many angles reached the prompt, whether two different
seeds ever returned the same bytes, what the filters took and how often, how
much history each source carried (game events must carry **none**), prompt
size and LLM latency.

**`req_id` joins the two halves.** The PC stamps it on every request
(`ravyn-lynx-p/orchestrator/models.py`); this side records it, and falls back
to its own id when an older client sends none. Neither repo needs the other to
work — only the join does.

`logs/` is gitignored. The framed prompt contains viewer notes and chat
history by name, so these files stay on the notebook.

`python tests/test_worker_log.py` — 39 checks.

---

## Output filters (`adapters/mq/rabbitmq.py`)

The model ignores instructions it is given, so the prompt is backed by filters.

**`persona/filters.py`** — everything between the model and the TTS, in one
testable place. It was inline in the worker, where the patterns could not be
inspected or tested without reading the consumer.

| Catches | Why it matters |
|---|---|
| Narration by name, **all tenses + possessive** | The old `\bRavyn\s+\w+s\b` was present tense only: *"Ravyn tilts"* was removed while *"Ravyn tilted her head"* and *"Ravyn's ears flick"* were read aloud |
| Dialogue attributions | *'"NewViewer_123," she murmurs'* |
| Stage directions, **any length** | The old caps were 20 chars bracketed / 30 in asterisks, so *"\*She leans back in her chair, unimpressed\*"* was spoken in full |
| Instruction leak | *"My angle here is to be dismissive"* — she narrates her direction instead of performing it |
| Echoed block labels | `GAME EVENT:`, `SITUATION:`. The first attempt put `game event:` inside a `\b…\b` group, where **it could never match** — a word boundary after `:` needs a word character next |
| Emoji, self-quoting, over-length | The sentence cap is enforced *here* so it is counted, rather than the PC silently truncating mid-thought |

Matched **by name, never by pronoun**: she talks about teammates as "she/he"
constantly, so a pronoun rule would eat real speech.

**Narrow beats thorough.** Deleting a real line is far worse than missing one,
so the leak patterns are possessive-anchored — bare *"the angle"* is not a tell,
because *"you have the angle on him"* is something she actually says. Three
phrasings ate real speech on a first pass and are now regression cases.

### The tally is the measurement

Every intervention is counted and reported every ten responses:

```
[worker] Cleaned [narration] -> Rough. Try again.
[filters] 40 responses: narration 3, leak 1, over-length 7
```

*"She felt off today"* is not something you can act on. `narration 3, leak 1` is
— it is a number that moves when a prompt changes, which is what makes the
character work tunable instead of guesswork. `over-length` counts how often the
2–3 sentence rule is being ignored; a high rate there is a prompt problem, not a
filter problem.

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

**Game events also carry a SITUATION block and an ANGLE**, both built on the PC
(`orchestrator/game_state.py`, `orchestrator/game_angles.py`). The situation is
measured fact — minute, his line, kill totals, drake and tower counts, both team
comps. The angle is a per-event instruction chosen from that state, and when it
is present it **replaces** the fixed per-type template: keeping both would put
two different directions in one prompt, and the fixed one is exactly what made
every ally death sound the same.

A third optional block, `player_notes`, carries what Exiled told her about his
own account — roles, champions, matchups, from `ravyn-lynx-p/data/champions.json`.
It is a **different category** from the situation and carries its own heading
saying so: the situation is measured, these are his claims. Without that label
the situation block's "do not state anything beyond these" would forbid the very
lines the file exists to produce, and blending the two would let her present his
opinion as something the game told her — the exact §7 failure.

All three are optional. `context_builder` falls back to the old
SERIOUS/DISMISSIVE/MILESTONE routing when they are absent, so an older PC client
or a game the API could not read still works.

A fourth block, `tone_instruction`, says **how warm to be** — chosen on the PC
from what the numbers say he just did (`orchestrator/tone.py`). It is separate
from the angle on purpose: the angle says *what* to talk about, the tone says
*how warm to be about it*, and multiplying them is where the variety comes from.

**The fixed "death #5 onward is always a roast" escalation is gone.** It was the
same failure the angle system was built to fix, in template form: a
maximum-heat instruction handed out twice in a row gets the same roast twice —
live report, *"FULL ROAST only makes her repeat herself on the 2nd message"*.
The PC's tone ladder decides now, and refuses consecutive roasts.
`GAME_EVENT_DEATH_ROAST` survives only as the fallback for a client that sends
no angle.

Why any of this exists: `ravyn-lynx-p/STATUS.md` §7, "Why she felt repetitive".

`context_builder.py` reads `context["lang"]` — `"ru"` forces Russian,
`"multilang"` mirrors the speaker. The PC decides which; see the full document.

**Her appearance is loaded from `ravyn.json`** — and until now it was not.
That file carried her whole look (dark-blue fluffy hair, fox ears, blue eyes
that go red when sparked, the scar, the oversized orange jacket, the choker)
and **nothing in the codebase read it**. A viewer saying "nice jacket" got an
improvised answer that could contradict the avatar.

Only the `appearance` key is read. The rest of that file restates
`system_prompt.txt`, and carrying one rule in two places is how they drift.

It ships with a hard rule against narrating any of it — *"You never narrate any
of this. Not your ears, not your tail, not your eyes, not what you are
wearing."* That is not decoration: handing her ear and tail vocabulary is
exactly what feeds the narration failure `_strip_narration` exists to clean up.
It is facts for **answering** with, never things to perform. ~170 tokens, and it
is in every prompt including game events, so keep it short.

### What she remembers, and what she never does

| | Kept | Where | Persisted |
|---|---|---|---|
| Viewer notes | ≤200 chars per person, LLM-written | `memory.user_notes` | yes, `data/memory.json` |
| Rolling summary | 2-3 sentences, spans the whole room | `memory.general_memory` | yes |
| Chat history | last 5 exchanges **per person**, raw messages only | `memory.histories[user]` | no |
| Her game lines | last 6, cleared on GameStart | `memory.recent_game_lines` | no |

**History is per person.** One shared buffer was fine while the streamer was
the only chatter and wrong the moment he was not:

- A reply to one viewer carried the last five messages from *everyone* as one
  conversation, so she answered person C mid-thread with person A.
- `get_user_note_compression_prompt` built from that shared buffer and wrote
  the result to whoever was active at the trigger. Five viewers talking meant
  the fifth one's notes were written from a transcript of all five — she would
  remember other people's personalities as theirs. Notes are the only part of
  her memory that survives a restart, so a wrong one is wrong forever. That is
  why this was fixed before voice rather than after.

Each person gets their own buffer and their own exchange counter, so notes are
written from their words when *they* have said enough, and compressing one
person leaves everyone else's thread intact. `MAX_TRACKED_USERS` (24) bounds it,
evicting the least recently active — that costs them their short-term thread and
nothing else, since notes persist separately. Unattributed speech (voice with no
name yet) shares one `ANON` buffer rather than silently joining someone's
conversation.

`general_memory` deliberately still spans everybody: "what has been happening on
this stream" is a property of the room, not of one person. The per-user note is
the opposite.

What she can still see of the room is `recent_chat`, which the PC batches into
context and which is framed as other people talking rather than as her
conversation.

**No prompt scaffolding is ever stored.** The SITUATION block, the ANGLE, the
TONE and the theme opening are built fresh for one response and thrown away;
`add_exchange` stores the raw signal text, not the framed message. Storing them
would replay stale instructions as if somebody had said them — which is the
failure that got a session terminated.

**What she calls his teammates comes from the PC now**, per event, and hardens
with his death count — piggies, apes, creatures, bronze hardstuck. The system
prompt used to carry one flat list she picked from at random, which gave her no
way to sound angrier at death nine than at death one. `system_prompt.txt` now
defers to the injected words and keeps only a fallback for when none arrive.

**`GAME_EVENT_RULES` bans restating the direction.** From a live session: *"some
topics are said directly and not accepted as theme or tone of conversation"* —
she was narrating her own instructions ("my angle here is…") rather than
performing them. Everything above the event is direction, not dialogue.

**She speaks Russian now, half the time.** The PC rolls it once per game and
stamps `lang` on every game signal, which reaches the existing LANGUAGE
directive here. The angles, tones and themes stay English on purpose: they are
instructions to the model, not text she says, and a 201-language model takes
English direction to Russian output without help.

Her persona is the gap. Everything in `system_prompt.txt` — the banned openers,
"fufu", the teammate ladder — is English and none of it survives translation, so
a Russian game will sound flatter until the addendum exists.

**Voice arrives as `source="voice"`** with `user` and `is_owner` set, so it takes
the CHAT_EXILED framing and shares his history buffer with his chat. Nothing
here needed changing for it.

**She knows when it is him from a flag, not from his name.** `context["is_owner"]`
comes from the PC, which resolves it against `data/identity.json` — one file,
one loader, shared by chat and the game source. `context_builder` used to
pattern-match `("exiled", "exiledr", "exiledra1n")` here as well, which meant
his identity lived in three places across two machines and this copy could not
be changed without a deploy. The name check survives only as a fallback for a
client that sends no flag.

`source="voice"` is routed alongside `"chat"`, so when STT lands it needs
nothing new here: set `context["user"]` and `context["is_owner"]` and it gets
the same framing and the same per-person memory buffer.

### `data/memory.json` is runtime state, not source

It is **gitignored**, and was tracked until it caused its first merge conflict.
The notebook rewrites it whenever memory compresses, so tracking it guaranteed
a conflict on every pull — and resolving one by hand means picking somebody's
stale `general_memory`, which then sits in the system prompt of **every single
response** until it happens to be overwritten. A test run left
`"Ravyn and multiple viewers discussed Team K deaths"` in there, and she would
have carried a phantom memory of a mock game into a live stream.

Shape, for when it needs checking by eye:

```json
{ "general_memory": "", "user_notes": {}, "mood_attribution": {}, "last_updated": 0 }
```

All four keys, `user_notes` keyed by lowercase name. It regenerates on first
run, so **deleting it is always a safe reset** — and the right move after any
mock or `--test` session, since nothing in it is worth keeping and all of it
reaches the prompt.

Note what is *not* in the file: conversation history and her recent game lines
are runtime-only by design. Only the compressed summary and the per-person
notes persist, which is also why a wrong note is wrong until it is overwritten.

**Known gap:** the persona is English. Banned openers, "fufu", the teammate
vocabulary — none survive translation. A Russian addendum is the streamer's
writing, not something to generate.

---

## Open here

- The two logs join on `req_id` but nothing reads them together yet. A joined
  view would answer "she repeated herself — was the angle in the prompt?"
  without opening two files side by side
- **Russian output quality is untested.** Set `LANG_REPLY = "ru"` on the PC,
  send chat, listen. Ten minutes, and it gates every RU decision.
- **Official vs abliterated A/B** — `./scripts/start_llm.sh old` is the baseline
- Watch the `[filters]` tally rather than individual lines. A high `narration`
  or `over-length` rate on the official model means the prompt is not landing;
  quiet means that problem was the abliterated build
- One response once repeated an entire previous answer verbatim before answering.
  Seen once, never reproduced. Suspect history/template; 8192 context may fix it.
- No PR on this repo. **It must merge together with the PC** — merging the PC
  alone brings back double-voice and silent quote mode.
- Stray submodule: a gitlink at path `ravyn-nb/` with no `.gitmodules`

## Deleted, and why

Everything below was verified unreachable before removal — nothing imported it,
nothing called it, no script referenced it.

| Removed | Why |
|---|---|
| `adapters/audio/stream_api.py`, `adapters/tts/qwen_tts.py` | Audio and TTS moved to the PC permanently (§1). Their only remaining references were each other |
| `transport/audio_stream_server.py`, `transport/test_r.py` | Same — the notebook does not stream audio |
| `tts_test.py`, `tts_test1.py`, `tools/test_request.py` | TTS testers on the machine that no longer does TTS |
| `app/logging.py` | An empty file, zero bytes |
| `MemoryManager.set_mood_cause` / `get_mood_cause` and the persisted `mood_attribution` | Written and read by nothing |
| `MOOD_NUDGE` template and its branch | Guarded by `context["mood_nudge"]`, which the PC has never set |
| `LLM_GGUF_PATH`, `LLM_GGUF_FALLBACK` | Read by nothing. `scripts/start_llm.sh` owns model choice — `dd61311` removed these once and a merge of an older `main` put them back, directly under the comment saying they had been removed |

The empty `adapters/audio/`, `adapters/tts/`, `transport/` and `tools/`
directories went with them.
