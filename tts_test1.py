"""
tts_test.py - Ravyn TTS voice tester
Tests different Kokoro voices and blends to find Ravyn's ideal sound.
Soft, shy, slightly slow - just below normal speed.
"""

import soundfile as sf
import numpy as np
from kokoro import KPipeline

# =========================================================
# CONFIG
# =========================================================

SAMPLE_RATE = 24000
OUTPUT_DIR  = "."

TEST_TEXT = (
    "Hello... I'm Ravyn. "
    "I'll be your companion today. "
    "I hope we can have a nice time together."
)

# =========================================================
# VOICE PRESETS TO TEST
# Adjust SELECTED_VOICE below to pick which one plays
# =========================================================

VOICES = {

    # Single voices
    "sky":    { "voice": "af_sky",    "speed": 0.88 },  # softest, youngest
    "nicole": { "voice": "af_nicole", "speed": 0.88 },  # breathy, shy
    "bella":  { "voice": "af_bella",  "speed": 0.88 },  # warm, gentle

    # Blended voices - manually mixed via numpy
    "sky_nicole": {
        "voice": ["af_sky", "af_nicole"],
        "mix":   [0.65,      0.35],
        "speed": 0.88
    },
    "sky_bella": {
        "voice": ["af_sky", "af_bella"],
        "mix":   [0.55,      0.45],
        "speed": 0.88
    },
    "nicole_bella": {
        "voice": ["af_nicole", "af_bella"],
        "mix":   [0.5,          0.5],
        "speed": 0.88
    },

    # Ravyn - bella warmth + sky youth, shy and soft
    "ravyn": {
        "voice": ["af_bella", "af_sky"],
        "mix":   [0.55,        0.45],
        "speed": 0.85
    },
}

# =========================================================
# CHANGE THIS to test different presets
# Options: "sky", "nicole", "bella", "sky_nicole",
#          "sky_bella", "nicole_bella", "ravyn"
# =========================================================

SELECTED_VOICE = "ravyn"

# =========================================================
# GENERATE
# =========================================================

def load_mixed_voice(pipe: KPipeline, voices: list, mix: list):
    """Manually blend multiple voice tensors by weighted average."""
    import torch
    tensors = [pipe.load_voice(v) for v in voices]
    mixed = sum(t * w for t, w in zip(tensors, mix))
    return mixed


def generate(preset_name: str, text: str) -> str:
    preset   = VOICES[preset_name]
    voice    = preset["voice"]
    mix      = preset.get("mix", None)
    speed    = preset["speed"]
    out_path = f"{OUTPUT_DIR}/ravyn_{preset_name}.wav"

    print(f"\nGenerating preset: '{preset_name}'")
    print(f"  Voice : {voice}")
    print(f"  Speed : {speed}")
    print(f"  Text  : {text}")

    pipe = KPipeline(lang_code='a')

    # if voice is a list, blend them manually
    if isinstance(voice, list) and mix:
        voice_tensor = load_mixed_voice(pipe, voice, mix)
    else:
        voice_tensor = voice  # single string, kokoro handles it

    chunks = []
    for gs, ps, audio in pipe(text, voice=voice_tensor, speed=speed):
        chunks.append(audio)

    if not chunks:
        print("  ERROR: no audio generated")
        return ""

    full_audio = np.concatenate(chunks)
    sf.write(out_path, full_audio, SAMPLE_RATE)
    duration = len(full_audio) / SAMPLE_RATE
    print(f"  Saved : {out_path}  ({duration:.2f}s)")
    return out_path


def generate_all(text: str):
    """Generate all presets at once for easy comparison."""
    print("\nGenerating ALL presets for comparison...\n")
    for name in VOICES:
        generate(name, text)
    print("\nDone! Compare the wav files and pick your favourite.")


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        arg = sys.argv[1]

        if arg == "all":
            # python tts_test.py all
            generate_all(TEST_TEXT)

        elif arg in VOICES:
            # python tts_test.py sky
            generate(arg, TEST_TEXT)

        else:
            # python tts_test.py "custom text here"
            generate(SELECTED_VOICE, arg)

    else:
        # no args - generate selected preset with test text
        generate(SELECTED_VOICE, TEST_TEXT)
