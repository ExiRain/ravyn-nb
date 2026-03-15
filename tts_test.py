"""
tts_test.py - Ravyn TTS voice tester
Kokoro + pyrubberband for clean pitch shifting.
Fully local, MIT license, stream safe.

Install deps first:
    sudo dnf install rubberband
    pip install pyrubberband soundfile numpy kokoro

Usage:
    python tts_test.py          # runs SELECTED preset
    python tts_test.py all      # generates all presets
    python tts_test.py ravyn_3  # specific preset by name
    python tts_test.py "hey!"   # custom text with SELECTED preset
"""

import sys
import numpy as np
import soundfile as sf
import pyrubberband as rb
from kokoro import KPipeline

# =========================================================
# CONFIG
# =========================================================

SAMPLE_RATE = 24000
OUTPUT_DIR  = "."
SELECTED    = "ravyn_3"

TEST_TEXT = (
    "Hello... I'm Ravyn. "
    "I'll be your companion today. "
    "I hope we can have a nice time together."
)

# =========================================================
# PRESETS
# voice     : kokoro voice
# speed     : 0.95 = slightly shy, close to natural
# semitones : clean pitch shift via rubberband
#   +1 = barely noticeable
#   +2 = subtle younger
#   +3 = young adult sweet spot
#   +4 = younger still
#   +5 = starting to sound unnatural
# =========================================================

PRESETS = {
    "bella_0":  { "voice": "af_bella", "speed": 0.95, "semitones": 0 },
    "bella_2":  { "voice": "af_bella", "speed": 0.95, "semitones": 2 },
    "bella_3":  { "voice": "af_bella", "speed": 0.95, "semitones": 3 },
    "bella_4":  { "voice": "af_bella", "speed": 0.95, "semitones": 4 },
    "sky_0":    { "voice": "af_sky",   "speed": 0.95, "semitones": 0 },
    "sky_2":    { "voice": "af_sky",   "speed": 0.95, "semitones": 2 },
    "sky_3":    { "voice": "af_sky",   "speed": 0.95, "semitones": 3 },
    "ravyn_2":  { "voice": "af_bella", "speed": 0.93, "semitones": 2 },
    "ravyn_3":  { "voice": "af_bella", "speed": 0.93, "semitones": 3 },
    "ravyn_4":  { "voice": "af_bella", "speed": 0.93, "semitones": 4 },
}

# =========================================================
# GENERATE
# =========================================================

def generate(preset_name: str, text: str) -> str:
    if preset_name not in PRESETS:
        print(f"Unknown preset '{preset_name}'. Available: {list(PRESETS.keys())}")
        return ""

    p        = PRESETS[preset_name]
    out_path = f"{OUTPUT_DIR}/ravyn_{preset_name}.wav"

    print(f"\n[{preset_name}] voice={p['voice']} speed={p['speed']} semitones=+{p['semitones']}")

    pipe   = KPipeline(lang_code='a')
    chunks = []

    for _, _, audio in pipe(text, voice=p["voice"], speed=p["speed"]):
        chunks.append(audio)

    if not chunks:
        print("  ERROR: no audio generated")
        return ""

    full_audio = np.concatenate(chunks)

    # clean pitch shift - no warble, no timbre change
    if p["semitones"] != 0:
        full_audio = rb.pitch_shift(full_audio, SAMPLE_RATE, p["semitones"])

    full_audio = full_audio.astype(np.float32)
    sf.write(out_path, full_audio, SAMPLE_RATE)
    print(f"  Saved: {out_path} ({len(full_audio)/SAMPLE_RATE:.2f}s)")
    return out_path


def generate_all(text: str):
    print(f"\nGenerating ALL {len(PRESETS)} presets...\n")
    for name in PRESETS:
        generate(name, text)
    print("\nDone! Best candidates to compare: ravyn_2, ravyn_3, ravyn_4")


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "all":
            generate_all(TEST_TEXT)
        elif arg in PRESETS:
            generate(arg, TEST_TEXT)
        else:
            generate(SELECTED, arg)
    else:
        generate(SELECTED, TEST_TEXT)
