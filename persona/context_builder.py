"""
Context builder — assembles the full messages array for the LLM.
"""

from pathlib import Path
from persona.context_templates import *

PERSONA_DIR = Path(__file__).parent
SYSTEM_PROMPT = (PERSONA_DIR / "system_prompt.txt").read_text(encoding="utf-8").strip()


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

    # --- Chat ---
    if source == "chat":
        if user and user.lower() in ("exiled", "exiledr", "exiledra1n"):
            return CHAT_EXILED.format(message=text, recent_chat_block=recent_chat_block)
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
        SERIOUS_EVENTS = {"MyKill", "MyMultikill", "MyAssist",
                          "BaronKill", "Ace", "InhibKilled"}
        DISMISSIVE_EVENTS = {"DragonKill", "HeraldKill", "TurretKilled",
                             "AllyKill", "AllyDeath"}
        MILESTONE_EVENTS = {"GameStart", "GameEnd"}
        ROAST_EVENTS = {"TeamfightMissed"}

        # deaths — routed by death count
        if event_type == "MyDeath":
            death_count = context.get("death_count", 1)
            if death_count >= 5:
                return GAME_EVENT_DEATH_ROAST.format(event=text, death_count=death_count)
            else:
                return GAME_EVENT_DEATH.format(event=text)

        if event_type in SERIOUS_EVENTS:
            return GAME_EVENT_SERIOUS.format(event=text)
        elif event_type in ROAST_EVENTS:
            return GAME_EVENT_ROAST.format(event=text)
        elif event_type in DISMISSIVE_EVENTS:
            return GAME_EVENT_DISMISSIVE.format(event=text)
        elif event_type in MILESTONE_EVENTS:
            return GAME_EVENT_MILESTONE.format(event=text)
        return GAME_EVENT_SERIOUS.format(event=text)

    # --- Silence filler ---
    if source == "silence_filler" and trigger == "silence_timer":
        return SILENCE_IMPROV.format(seed=text)

    # --- Promotion ---
    if source == "promotion":
        return PROMOTION

    return text