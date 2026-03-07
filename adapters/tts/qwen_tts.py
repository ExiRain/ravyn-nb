from pathlib import Path
import torch
import soundfile as sf
import io

from qwen_tts import Qwen3TTSModel
from app.settings import get_settings


settings = get_settings()

MODEL_DIR = Path(settings.TTS_MODEL_DIR).resolve()

print("Loading Qwen TTS model from:", MODEL_DIR)

# Important: force local loading and bypass hub validation
model = Qwen3TTSModel.from_pretrained(
    pretrained_model_name_or_path=str(MODEL_DIR),
    device_map="cpu",
    dtype=torch.float32,
    trust_remote_code=True,
    local_files_only=True,
    attn_implementation="eager"
)

print("TTS ready")


VOICE_DESCRIPTION = "young female vtuber voice, energetic, playful"


def synthesize(text: str) -> bytes:

    wavs, sr = model.generate_custom_voice(
        text=text,
        language="English",
        speaker="serena",
        voice_description=VOICE_DESCRIPTION
    )

    buffer = io.BytesIO()
    sf.write(buffer, wavs[0], sr, format="WAV")

    return buffer.getvalue()