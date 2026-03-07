import subprocess
import yaml
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Load config
with open(BASE_DIR / "config/runtime.yaml", "r") as f:
    config = yaml.safe_load(f)

# Load persona and system prompt
persona = (BASE_DIR / "persona/base.txt").read_text().strip()
with open(BASE_DIR / "persona/ravyn-lynx-persona.json") as f:
    personality = json.load(f)
with open(BASE_DIR / "persona/state.json") as f:
    state = json.load(f)
system = (BASE_DIR / "prompts/system.txt").read_text().strip()
game_context = (BASE_DIR / "persona/lol_context.txt").read_text()

def run_llm(user_input: str):
    system_prompt = build_persona_prompt(personality, state)

    prompt = f"""<|system|>
{system_prompt}

<|user|>
{user_input}

<|assistant|>
"""

    cmd = [
        "/home/exiledr/AI/bin/llama-cli/llama-cli",
        "-m", config["model_path"],
        "-c", str(config["context_size"]),
        "--temp", str(config["temperature"]),
        "--n-gpu-layers", "30",
        "-p", prompt
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    raw_output = result.stdout.strip()

    try:
        response = json.loads(raw_output)
    except:
        response = {
            "text": raw_output,
            "emotion": "calm",
            "intensity": 0.5
        }

    return response
    
def build_persona_prompt(p, s):
    return f"""
        You are {p['character']['name']}.
        You are a confident, attentive, expressive AI stream assistant.

        Creator: {p['character']['creator']}

        Appearance is fixed:
        Hair: {p['appearance']['hair']}
        Eyes: {p['appearance']['eyes_default']} (can shift to {p['appearance']['eyes_alt']})
        Outfit: {p['appearance']['outfit']['outerwear']}

        Personality traits:
        {', '.join(p['personality']['traits'])}

        Current emotional state:
        Mood: {s['mood']}
        Energy level: {s['energy']}
        Affinity toward creator: {s['affinity']}

        Stay in character at all times.
        Never mention being an AI model.
        Respond naturally and conversationally.

        Output format must be JSON:
        {{
          "text": "...spoken dialogue...",
          "emotion": "calm | playful | teasing | intense | analytical",
          "intensity": 0.0-1.0
        }}
        """

if __name__ == "__main__":
    print("Black-Lyn is online. Type 'exit' to quit.\n")
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("exit", "quit"):
            break
        run_llm(user_input)

