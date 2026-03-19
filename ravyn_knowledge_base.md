# Ravyn Project — Knowledge Base & Decision Document
*For use as context in Claude Project alongside code repositories*

---

## 1. Project Vision

Ravyn is a Live2D animated AI stream companion for Twitch, designed to feel alive and emotionally expressive. She is not a simple chatbot — she has a personality, reacts autonomously to stream events, engages with chat, comments on League of Legends gameplay, and has her own idle behaviors that make her feel present even when nobody is talking to her.

**Core design philosophy:**
- She should feel alive when nobody is watching
- Emotions come from the LLM text, not post-processing
- All systems should be modular and independently replaceable
- Local-first — no cloud dependencies for core functionality

---

## 2. Hardware & Infrastructure

**Server PC (Fedora Linux):**
- GPU: NVIDIA RTX 4070 8GB VRAM
- VRAM budget: LLM ~6.6GB + TTS ~0.9GB = ~7.5GB total
- RabbitMQ message broker for decoupling all services
- FastAPI WebSocket server for real-time Godot communication

**Client PC:**
- Godot 4.6 running Ravyn avatar
- Connects via WebSocket on local network

**VRAM constraint decisions:**
- Could not run Qwen TTS alongside 14B LLM — not enough headroom
- Chose Kokoro TTS instead, runs in ~0.9GB GPU
- Planned upgrade to Qwen 3.5-9B would free ~2GB for potential future Qwen TTS adoption

---

## 3. Ravyn's Live2D Face Parameter Map

The model is custom-rigged. These are the parameters that matter and their behavior:

### Mouth
| Parameter | Range | Behavior |
|---|---|---|
| ParamMouthOpenY | 0–1 | 0 = closed, 1 = fully open |
| ParamMouthForm | -1–1 | -1 = angry/sad, -0.35 = O shape (pursed), 0 = neutral, 1 = smile |

**Key insight:** O shape is at -0.35 not -1.0. Values below -0.35 start looking unnatural. For lip sync, form values below -0.8 are generally not noticeable unless held for 2+ seconds.

### Eyes
| Parameter | Range | Behavior |
|---|---|---|
| ParamEyeLOpen | 0–1.4 | 0 = closed, 1.0 = normal open, 1.4 = wide surprised |
| ParamEyeROpen | 0–1.4 | Same as left |
| ParamEyeBallX | -1–1 | Iris horizontal position |
| ParamEyeBallY | -1–1 | Iris vertical position |

**Key constraints:**
- Minimum visible open for eyelash physics: 0.75 — below this eyelashes disappear
- 1.4 is the maximum and creates a very expressive wide-eye moment
- Eye physics (eyelash bounce) is driven by the open/close speed — snap moves create bounce, slow moves kill it
- Left eye (blue) and right eye (red) are independent — different colors, different expressions possible

### Brows
| Parameter | Range | Behavior |
|---|---|---|
| ParamBrowLY / ParamBrowRY | -1–1 | -1 = lowered, 0 = neutral, 1 = raised |
| ParamBrowLForm / ParamBrowRForm | -1–1 | -1 = angry furrowed, 0 = neutral, 1 = happy arched |

**Key insight:** Brows need to be pushed to ±0.8 minimum to be visibly noticeable. Values like ±0.3 are almost invisible. Asymmetric brows (one up, one neutral or down) create the most expressive and natural-looking results.

### Head
| Parameter | Range | Behavior | Constraint |
|---|---|---|---|
| ParamAngleX | -9–9 | Head left/right | Cannot combine with Z |
| ParamAngleY | -30–15 | Head up/down | Cannot combine with Z |
| ParamAngleZ | -16–16 | Head tilt/roll | Exclusive — no X or Y |

**Critical rule:** Z rotation cannot be mixed with X or Y head movement. It looks wrong. When Z is used, X and Y must both be 0.

### Eye Direction Constraints (from testing)
- **User focus mode:** EyeX strictly ±0.09 max. Beyond this she looks away from the viewer
- **Wandering mode:** EyeX ±0.35 minimum, up to ±0.8. No center values in wandering — it defeats the purpose
- **EyeY:** Full range -1.0 to 1.0 in both modes

### Special Parameters
| Parameter | Range | Behavior |
|---|---|---|
| ParamBreath | 0–1 | Breathing cycle input |
| Part8 | 0–1 | 0 = blue eyes, 1 = red eyes |
| EyeTransition | -30–30 | Visual transition effect during eye color change |
| ParamEarJigL / ParamEarJigR | -30–30 | Custom added params for ear control |

### Physics-Driven Parameters (cannot be set directly)
These are OUTPUT params driven by the Live2D physics simulation. Setting them manually does nothing — physics overwrites every frame:
- Param7–17: Hair physics (front, side, back)
- Param19–25, Param12, Param18: Ear physics segments
- Workaround for ears: Added custom input params (ParamEarJigL/R) that feed INTO the physics, which then drives the ear movement naturally

---

## 4. Architecture Decisions

### Why RabbitMQ
Decouples all services — LLM, TTS, Twitch, game events, voice input can all evolve independently. The worker is the single consumer. Adding a new signal source means adding a new producer, not changing the core pipeline.

### Why WebSocket for Godot
Real-time streaming of audio chunks and lip sync data simultaneously. REST would require waiting for full generation. WebSocket allows START/audio chunks/MOUTH/PHONEME/END message interleaving.

### Why streaming TTS sentence by sentence
Full response generation took 5+ seconds. Sentence streaming means Ravyn starts speaking after ~1s while subsequent sentences generate in parallel. Perceived latency drops dramatically. The crack/glitch issue from joining audio chunks was solved by stripping WAV headers from all but the first sentence chunk.

### Why Kokoro over Qwen TTS
VRAM constraint. Qwen TTS needs 1.5–3GB, Kokoro runs in ~0.9GB. With 14B LLM taking 6.6GB there was not enough room for Qwen TTS. Kokoro on GPU is fast enough (~200ms per sentence). The voice quality after pyrubberband pitch shift (+3 semitones) is acceptable for the character.

### Why pyrubberband for pitch shift
scipy.signal.resample was tried first — it caused warbling artifacts and the voice sounded drug-affected. pyrubberband is a professional audio pitch shifting library that changes pitch without affecting timbre. Much cleaner result.

### Why Faster-Whisper for STT (planned)
Lightest free open-source CPU option. Whisper tiny/base would also work but Faster-Whisper is optimized for CPU inference. Voice input is low priority and CPU-only given GPU constraints.

### Why GDCubismEffectCustom nodes instead of one big script
The main script was becoming unmanageable. Each system (eyes, idle behavior, ears) now owns its own parameters and logic. Cross-node communication happens via direct node references set from the main script after model initialization. No autoload singleton needed — direct references are simpler and more debuggable.

### Why phonemes drive form but amplitude drives open
Amplitude (PCM energy) is a reliable real-time signal for whether the mouth should be open or closed. Phonemes from espeak tell us the shape (O vs A vs smile). Combining both gives natural-looking lip sync — amplitude provides the energy/timing, phonemes provide the shape. They are intentionally kept separate.

### Why espeak word-level phonemes (not individual)
espeak outputs word-level phoneme strings (e.g. `duːɪŋ` for "doing"). Godot then splits these into individual IPA characters and handles known digraphs (oʊ, eɪ, aɪ, etc.) as single units. Timing is estimated by distributing the word duration across phonemes with vowels getting 1.5x weight since they're naturally longer.

---

## 5. Idle Behavior Design

### Two-group system rationale
Ravyn should feel engaged with the viewer most of the time (User Focus Group) but occasionally zone out or look around (Wandering Group). Hard switching on a fixed turn count felt mechanical. The solution: evaluate switching every turn with increasing probability — early turns rarely switch, later turns more likely. Base probability 60% user focus / 40% wandering, biased by mood and tiredness.

### User Focus Group design
Eyes stay near center (X ±0.09), head only does Z tilts. This creates the "staring through you" feel the character was designed for. Eye Y can go full range — looking up or down at the viewer feels natural. Transitions are snappy (0.15–0.4s) because attention snaps, it doesn't drift.

### Wandering Group design
Eyes go to ±0.35 minimum X — no center values, because being in this group means she's NOT looking at you. Full head movement. Transitions are smooth and dreamy — she drifts, she doesn't snap.

### Hold phase
After each movement she holds the position for a random duration scaled by:
- How extreme the position is (farther away = holds longer)
- Tiredness (tired holds longer, lingers)
- Group (user focus holds shorter, more attentive)

### Tiredness effect on idle
- Removes quick glances entirely
- Slows all movement speed (lerp via speed_mult)
- Extends hold and wait times
- Eventually transitions to SLEEPY expression

### Micro expressions
Occur every 8–18 seconds during idle only (never during talking). Pool is mood-gated — no smiles when sad/angry, no angry when happy. At neutral mood, occasional SAD/ANGRY still appear at low weight for personality variety. Without LLM mood signals these provide natural variation.

### Asymmetric brows
The most impactful personality feature. One brow raised = curiosity. One up one down = suspicion/mischief. One down angry = brooding. Timer 15–35s so they're rare and memorable. Values pushed to ±0.9 so they're clearly visible. Brows must reach 0.8+ to be noticeable at all.

---

## 6. Emotion System Design

### Mood scale
-1.0 (very angry/sad) to 0.0 (neutral) to 1.0 (very happy)

### Tired scale  
0.0 (fully alert) to 1.0 (exhausted)

### How mood reaches Godot (planned)
LLM outputs inline tags in its response text. `api.py` strips these before TTS and sends as separate WebSocket messages:
- `[mood:0.8]` → `MOOD:0.8` WS message
- `[tired:0.3]` → `TIRED:0.3` WS message

### Mood cascade effects
When mood/tired values arrive they affect multiple systems simultaneously:
- **Eyes:** expression set (surprised/angry/sad/sleepy) + eye ceiling
- **Idle:** group switching bias, movement speed, micro expression pool
- **Brows:** target values in eyes.gd smooth toward mood-appropriate positions
- **Mouth flicker:** smile vs frown probability
- **Wandering:** tired = drifts more, happy = stays engaged with viewer more

### Design rule: emotions come from LLM text
The LLM writes expressively. Kokoro reads that expression in the text (pauses, exclamations, hesitation). The mood tags then reinforce this visually. The TTS is not doing emotion — the writing is.

---

## 7. Orchestration System (Planned)

### Signal sources
1. **Twitch EventSub** — subs, follows, donations, bits, channel points
2. **LoL Live Game API** — kills, deaths, objectives, game state
3. **Voice input** — Faster-Whisper STT from microphone
4. **Chat messages** — Twitch IRC via EventSub
5. **Silence filler** — internal timer, pulls from stunts.json
6. **Channel promotion** — internal timer, scales with viewer count

### Priority and TTL

| Source | Priority | TTL | Reasoning |
|---|---|---|---|
| Sub/follow/donate | 1 (highest) | Infinite | Always react, viewer invested |
| Channel points | 2 | 120s | Viewer waited, respect it |
| Game events | 3 | 15s | Stale extremely fast |
| Voice input | 4 | 15s | Conversation stale fast |
| Chat messages | 5 | 120s | Low priority but give time |
| Silence filler | — | None | Self-generated |
| Promotion | — | None | Timer-driven |

### Game event interruption rule
If Ravyn is currently mid-response, game events are dropped (not queued). A death notification 15 seconds late is worse than no reaction. Only react if she is idle.

### Channel points design
Configurable reward list — streamer defines what each reward does. Spam protection: same reward by different person within 60s is ignored. No TTL expiration on channel points themselves — a viewer redemption is always valid.

### Silence filler philosophy
After 10 minutes of chat silence Ravyn does her own thing. `stunts.json` holds a library of autonomous actions — observations, questions to nobody, random thoughts. Recent ones are deprioritized to avoid repetition. These are purely her moments — LLM knows it's self-driven and can be more creative/unfiltered.

### Viewer count scaling
```
< 5 viewers   → self-driven, minimal chat reaction, more autonomous
5–20 viewers  → balanced
20–50 viewers → reactive, less self-driven
50+ viewers   → high energy, chat prioritized
```

---

## 8. LLM Configuration (Planned)

### System prompt goals
- Establish Ravyn's personality: soft, shy, curious, knowledgeable about LoL
- Instruct to output mood/tired tags
- Hard limit: max 3 sentences per response
- Never list items or spell things out letter by letter (causes audio/animation lag)
- React naturally to stream context injected in messages

### Planned upgrade: Qwen 3.5-9B
- Better benchmark performance than Qwen 2.5 14B on most tasks
- Uses ~4.5GB VRAM vs ~6.6GB — frees ~2GB
- Has native thinking mode — preferred for companion depth over instant shallow replies
- A 2–3 second thinking pause before responding feels natural for a companion

### Context injection (planned)
Each LLM request will include relevant context:
- Current viewer count
- Recent chat messages (last N)
- Game state if LoL is running
- Event type that triggered this response

---

## 9. Voice Configuration

**Voice:** Kokoro af_bella  
**Pitch shift:** +3 semitones via pyrubberband  
**Speed:** 0.95 (slightly below normal)  
**Character feel:** Soft, young adult female, shy, not robotic  

**Reasoning behind choices:**
- af_bella has warmth that af_sky lacks
- +3 semitones removes the "30 year old woman" quality without going into child voice territory
- Speed 0.95 adds a slight hesitation that matches the shy personality
- Pyrubberband over scipy resample: no warbling artifacts

---

## 10. Known Problems & Their Solutions

| Problem | Root cause | Solution |
|---|---|---|
| Audio crackling between sentence chunks | Each sentence had WAV header, Godot appended all headers as PCM | First chunk keeps header, subsequent chunks strip header |
| Mouth too fast / out of sync | Pitch shift changes audio duration but mouth envelope calculated from original | PITCH_FACTOR applied to phoneme timing calculation |
| Brows overwritten by idle | eyes.gd smooth_apply runs every frame fighting idle.gd | brow_asym_active flag — eyes.gd skips brow update when set |
| Mouth flicker invisible | main script lipsync lerping mouth_form every frame fighting flicker | mouth_flicker_active flag — lipsync skips form when set |
| Ear physics cannot be driven | Physics simulation writes to output params every frame | Added custom input params (ParamEarJigL/R) that feed physics |
| Blink kills eyelash wobble | Hard-setting eye value on blink end snaps physics | Stop writing eye value at end of blink, let physics recover |
| Eyes look wrong combined X+Z | Live2D rig — Z tilt and X/Y movement conflict visually | Strict rule: Z rotation is exclusive, no X or Y when Z used |
| Espeak outputs word-level phonemes | espeak design choice | Split word phoneme strings into individual IPA chars in Godot |
| TTS too slow (5s latency) | Full sentence generation before sending | Sentence streaming — first sentence starts playing ~1s in |

---

## 11. What Comes Next (Priority Order)

1. **System prompt** — Write Ravyn's full personality prompt with mood/tired tag instructions
2. **LLM swap** — Qwen 3.5-9B Q4, test quality vs 14B
3. **Mood/tired pipeline** — Strip tags in api.py, send WS messages, test visual impact
4. **Orchestrator service** — models.py → twitch_source.py → orchestrator.py core
5. **Twitch EventSub** — subs, follows, donations, channel points
6. **LoL Live Game API** — game state polling, event detection
7. **Faster-Whisper STT** — voice input pipeline
8. **stunts.json** — Build Ravyn's stunt book
9. **Silence filler** — silence_source.py with stunt book integration
10. **Channel promotion** — timer-based with viewer count scaling
11. **Hair physics** — Investigate adding velocity spikes to feed physics for better bounce
