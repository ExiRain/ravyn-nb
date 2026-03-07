from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def _env(key: str, default: str) -> str:
    v = os.getenv(key)
    return v if v not in (None, "") else default


def _env_int(key: str, default: int) -> int:
    v = os.getenv(key)
    return int(v) if v not in (None, "") else default


def _env_float(key: str, default: float) -> float:
    v = os.getenv(key)
    return float(v) if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # --- Paths (repo-relative by default) ---
    PROJECT_ROOT: Path = Path(_env("RAVYN_ROOT", str(Path(__file__).resolve().parents[1])))

    # models
    TTS_MODEL_DIR: Path = Path(_env("RAVYN_TTS_MODEL_DIR", "models/tts/06BCustomVoice"))
    LLM_GGUF_PATH: Path = Path(_env("RAVYN_LLM_GGUF_PATH", "models/llm/Qwen2.5-14B-Instruct-Q4_K_M.gguf"))

    # output / temp
    DATA_DIR: Path = Path(_env("RAVYN_DATA_DIR", "data"))
    TMP_DIR: Path = Path(_env("RAVYN_TMP_DIR", "data/tmp"))

    # --- Networking ---
    NOTEBOOK_HOST: str = _env("RAVYN_HOST", "0.0.0.0")
    API_PORT: int = _env_int("RAVYN_API_PORT", 9000)

    # RabbitMQ on notebook (PC connects over LAN)
    RABBIT_HOST: str = _env("RAVYN_RABBIT_HOST", "0.0.0.0")
    RABBIT_PORT: int = _env_int("RAVYN_RABBIT_PORT", 5672)
    RABBIT_USER: str = _env("RAVYN_RABBIT_USER", "ravyn")
    RABBIT_PASS: str = _env("RAVYN_RABBIT_PASS", "103595")
    RABBIT_VHOST: str = _env("RAVYN_RABBIT_VHOST", "/")
    QUEUE_REQUEST: str = _env("RAVYN_QUEUE_REQUEST", "ravyn.request")
    QUEUE_RESPONSE: str = _env("RAVYN_QUEUE_RESPONSE", "ravyn.response")

    # --- LLM runner defaults (llama-cli) ---
    LLM_CTX: int = _env_int("RAVYN_LLM_CTX", 4096)
    LLM_TEMP: float = _env_float("RAVYN_LLM_TEMP", 0.7)

    def resolved(self) -> "Settings":
        root = self.PROJECT_ROOT

        def r(p: Path) -> Path:
            return p if p.is_absolute() else (root / p)

        return Settings(
            PROJECT_ROOT=root,
            TTS_MODEL_DIR=r(self.TTS_MODEL_DIR),
            LLM_GGUF_PATH=r(self.LLM_GGUF_PATH),
            DATA_DIR=r(self.DATA_DIR),
            TMP_DIR=r(self.TMP_DIR),
            NOTEBOOK_HOST=self.NOTEBOOK_HOST,
            API_PORT=self.API_PORT,
            RABBIT_HOST=self.RABBIT_HOST,
            RABBIT_PORT=self.RABBIT_PORT,
            RABBIT_USER=self.RABBIT_USER,
            RABBIT_PASS=self.RABBIT_PASS,
            RABBIT_VHOST=self.RABBIT_VHOST,
            QUEUE_REQUEST=self.QUEUE_REQUEST,
            QUEUE_RESPONSE=self.QUEUE_RESPONSE,
            LLM_CTX=self.LLM_CTX,
            LLM_TEMP=self.LLM_TEMP,
        )


def get_settings() -> Settings:
    s = Settings().resolved()
    s.DATA_DIR.mkdir(parents=True, exist_ok=True)
    s.TMP_DIR.mkdir(parents=True, exist_ok=True)
    return s