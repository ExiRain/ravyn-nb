"""
Context builder — assembles the full messages array for the LLM.
"""

import json
from pathlib import Path
from persona.context_templates import *

PERSONA_DIR = Path(__file__).parent
SYSTEM_PROMPT = (PERSONA_DIR / "system_prompt.txt").read_text(encoding="utf-8").strip()


def _load_appearance() -> str:
    """
    Her look, from persona/ravyn.json.

    Only `appearance` is read. The rest of that file restates
    system_prompt.txt, and carrying one rule in two places is how they drift.
    Flattened to one line per feature and kept short on purpose: the notebook
    runs at 4096 context on the default quant, and this is in every single
    prompt including game events.

    A missing or malformed file costs her the ability to describe herself and
    nothing else.
    """
    try:
        data = json.loads((PERSONA_DIR / "ravyn.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[persona] No appearance loaded: {e}")
        return ""

    look = data.get("appearance") or {}
    if not look:
        return ""

    lines = []
    for label, value in look.items():
        if isinstance(value, dict):
            value = ", ".join(str(v) for v in value.values() if v)
        if value:
            lines.append(f"  {label.replace('_', ' ')}: {value}")

    return APPEARANCE.format(lines="\n".join(lines)) if lines else ""


APPEARANCE_BLOCK = _load_appearance()


def build_messages(
    text: str,
    source: str,
    context: dict,
    history: list,
    general_memory: str = "",
    user_memory: str = "",
    recent_openers: str = "",
) -> list[dict]:

    system_parts = [SYSTEM_PROMPT]

    if APPEARANCE_BLOCK:
        system_parts.append(APPEARANCE_BLOCK)

    lang = context.get("lang", "en")
    if lang == "ru":
        system_parts.append("LANGUAGE: Always respond in Russian. All your speech must be in Russian.")
    elif lang == "multilang":
        system_parts.append("LANGUAGE: Respond in the same language the user writes in.")


    viewer_count = context.get("viewer_count", "unknown")
    stream_mood = context.get("stream_mood", "neutral")
    game_active = context.get("game_active", False)
    game_line = GAME_ACTIVE if game_active else GAME_INACTIVE

    system_parts.append(STREAM_STATE.format(
        viewer_count=viewer_count, stream_mood=stream_mood, game_line=game_line,
    ))

    if general_memory:
        system_parts.append(GENERAL_MEMORY.format(summary=general_memory))

    user = context.get("user", "")
    if user and user_memory:
        system_parts.append(USER_MEMORY.format(user=user, notes=user_memory))

    if recent_openers:
        system_parts.append(recent_openers)

    if context.get("mood_nudge"):
        system_parts.append(MOOD_NUDGE.format(
            mood_description=context["mood_nudge"].get("description", "shifted"),
            cause=context["mood_nudge"].get("cause", "recent events"),
        ))

    system_message = "\n\n".join(system_parts)

    user_message = _frame_signal(text, source, context, user_memory)

    messages = [{"role": "system", "content": system_message}]
    for entry in history:
        messages.append(entry)
    messages.append({"role": "user", "content": user_message})

    return messages


def _build_recent_chat_block(context: dict) -> str:
    recent_chat = context.get("recent_chat", [])
    if not recent_chat:
        return ""
    lines = "\n".join(f"  - {line}" for line in recent_chat)
    return RECENT_CHAT_BLOCK.format(lines=lines)


def _frame_signal(text: str, source: str, context: dict, user_memory: str) -> str:
    user = context.get("user", "")
    trigger = context.get("trigger", "")
    event_type = context.get("event_type", "")

    user_notes = ""
    if user_memory and user:
        user_notes = f"(You remember: {user_memory})"

    recent_chat_block = _build_recent_chat_block(context)

    # --- Chat, and voice when it lands ---
    # Voice is the same shape: somebody addressed her by name or by speaking.
    # The source sets context["user"] and context["is_owner"] exactly as chat
    # does, and gets the same framing and the same per-person memory.
    if source in ("chat", "voice"):
        # The PC decides who he is, from data/identity.json, and says so in
        # the context. It used to be pattern-matched against a tuple here as
        # well — three copies of one fact across two machines, and this copy
        # could not be edited without a deploy.
        #
        # The name check survives as a fallback for a client that sends no
        # flag, so an older PC still recognises him.
        is_owner = context.get("is_owner")
        if is_owner is None:
            is_owner = bool(user and user.lower() in
                            ("exiled", "exiledr", "exiledra1n"))

        if is_owner:
            return CHAT_EXILED.format(message=text,
                                      recent_chat_block=recent_chat_block)
        return CHAT_MESSAGE.format(
            user=user or "someone", message=text,
            user_notes=user_notes, recent_chat_block=recent_chat_block,
        )

    # --- Twitch events ---
    if source == "eventsub":
        if event_type == "sub":
            return EVENT_SUB.format(user=user or "Someone")
        elif event_type == "follow":
            return EVENT_FOLLOW.format(user=user or "Someone")
        elif event_type == "donate":
            amount = context.get("amount", "some money")
            return EVENT_DONATE.format(user=user or "Someone", amount=amount, message=text)
        return f"{user or 'Someone'} triggered an event: {text}"

    # --- Game events ---
    # The text already contains the quote seed from lol_game.py.
    # Templates tell the LLM to use it as inspiration, not verbatim.
    if source == "game":
        champion = context.get("player_champion", "")
        identity = GAME_IDENTITY.format(champion=champion or "his champion")

        # The PC measures the game and picks an angle from it; both are
        # optional, so an older client or a game the API could not read
        # degrades to exactly the previous behaviour instead of breaking.
        situation = context.get("situation", "")
        angle = context.get("angle", "")
        player_notes = context.get("player_notes", "")
        tone_instruction = context.get("tone_instruction", "")

        def framed(body: str) -> str:
            parts = [identity]
            if situation:
                parts.append(GAME_SITUATION.format(situation=situation))
            if player_notes:
                parts.append(GAME_PLAYER_NOTES.format(player_notes=player_notes))
            parts.append(body)
            return "\n\n".join(parts)

        # When an angle is present it REPLACES the fixed per-type instruction.
        # Keeping both would put two different directions in one prompt, and
        # the fixed one is what made every ally death sound the same.
        def angled(event_text: str) -> str:
            parts = [f"GAME EVENT: {event_text}",
                     GAME_ANGLE.format(angle=angle)]
            if tone_instruction:
                parts.append(GAME_TONE.format(tone_instruction=tone_instruction))
            parts.append(GAME_EVENT_RULES)
            return framed("\n\n".join(parts))

        SERIOUS_EVENTS = {"MyKill", "MyMultikill", "MyAssist",
                          "BaronKill", "Ace", "InhibKilled"}
        DISMISSIVE_EVENTS = {"DragonKill", "HeraldKill", "TurretKilled",
                             "AllyKill", "AllyDeath"}
        MILESTONE_EVENTS = {"GameStart", "GameEnd"}
        ROAST_EVENTS = {"TeamfightMissed"}

        # deaths — routed by death count, short_mode for interrupts
        if event_type == "MyDeath":
            death_count = context.get("death_count", 1)
            short = context.get("short_mode", False)

            # The fixed "death #5 onward is always a roast" escalation is
            # gone. It is what made her repeat herself: a maximum-heat
            # instruction, twice in a row, gets the same roast twice. The PC's
            # tone ladder now decides, and refuses consecutive roasts —
            # orchestrator/tone.py.
            if angle:
                built = angled(text)
            elif death_count >= 5:
                built = framed(GAME_EVENT_DEATH_ROAST.format(
                    event=text, death_count=death_count))
            else:
                built = framed(GAME_EVENT_DEATH.format(event=text))

            if short:
                built += "\nIMPORTANT: Exiled is currently talking. Keep this to ONE short punchy sentence. Maximum 6 words."

            return built

        if angle:
            return angled(text)

        if event_type in SERIOUS_EVENTS:
            return framed(GAME_EVENT_SERIOUS.format(event=text))
        elif event_type in ROAST_EVENTS:
            return framed(GAME_EVENT_ROAST.format(event=text))
        elif event_type in DISMISSIVE_EVENTS:
            return framed(GAME_EVENT_DISMISSIVE.format(event=text))
        elif event_type in MILESTONE_EVENTS:
            return framed(GAME_EVENT_MILESTONE.format(event=text))
        return framed(GAME_EVENT_SERIOUS.format(event=text))

    # --- Silence filler ---
    if source == "silence_filler" and trigger == "silence_timer":
        return SILENCE_IMPROV.format(seed=text)

    # --- Promotion ---
    if source == "promotion":
        return PROMOTION

    return text