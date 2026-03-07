import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

MODEL_PATH = "./06BCustomVoice"

print("Loading TTS model...")

tts = Qwen3TTSModel.from_pretrained(
    MODEL_PATH,
    device_map="cpu",
    dtype=torch.float32,
)

print("Warming up...")
tts.generate_custom_voice(
    text="hello",
    language="English",
    speaker="ono_anna",
    instruct="Speak in a soft, calm voice"
)

print("TTS ready.")

def speak(text, output_path="output.wav"):
    wavs, sr = tts.generate_custom_voice(
        text=text,
        language="English",
        speaker="ono_anna",
        instruct="Speak in a soft, calm voice"
    )
    sf.write(output_path, wavs[0], sr)
    print("Saved:", output_path)


# Simple manual test
while True:
    text = input("Say something: ")
    speak(text)
