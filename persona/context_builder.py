"""
Context builder — assembles the full messages array for the LLM.

Takes a signal's context dict and builds:
  1. System message (base personality + stream state + memory)
  2. Conversation history (last N exchanges)
  3. Current user message (signal-specific framing)
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
) -> list[dict]:
    """
    Build the full messages array for the LLM.

    Args:
        text:           The signal text (chat message, seed, event description)
        source:         Signal source ("chat", "eventsub", "game", "silence_filler", etc.)
        context:        Signal context dict from orchestrator
        history:        List of {"role": "user"/"assistant", "content": "..."} dicts
        general_memory: Compressed stream memory summary
        user_memory:    Per-user notes for the active user

    Returns:
        List of message dicts ready for /v1/chat/completions
    """

    # --- Build system message ---
    system_parts = [SYSTEM_PROMPT]

    # stream state
    viewer_count = context.get("viewer_count", "unknown")
    stream_mood = context.get("stream_mood", "neutral")
    game_active = context.get("game_active", False)
    game_line = GAME_ACTIVE if game_active else GAME_INACTIVE

    system_parts.append(STREAM_STATE.format(
        viewer_count=viewer_count,
        stream_mood=stream_mood,
        game_line=game_line,
    ))

    # general memory
    if general_memory:
        system_parts.append(GENERAL_MEMORY.format(summary=general_memory))

    # per-user memory
    user = context.get("user", "")
    if user and user_memory:
        system_parts.append(USER_MEMORY.format(user=user, notes=user_memory))

    # mood nudge
    if context.get("mood_nudge"):
        system_parts.append(MOOD_NUDGE.format(
            mood_description=context["mood_nudge"].get("description", "shifted"),
            cause=context["mood_nudge"].get("cause", "recent events"),
        ))

    system_message = "\n\n".join(system_parts)

    # --- Build user message (signal-specific framing) ---
    user_message = _frame_signal(text, source, context, user_memory)

    # --- Assemble messages array ---
    messages = [{"role": "system", "content": system_message}]

    for entry in history:
        messages.append(entry)

    messages.append({"role": "user", "content": user_message})

    return messages


def _build_recent_chat_block(context: dict) -> str:
    """Build the recent chat context block from batch context."""
    recent_chat = context.get("recent_chat", [])
    if not recent_chat:
        return ""

    lines = "\n".join(f"  - {line}" for line in recent_chat)
    return RECENT_CHAT_BLOCK.format(lines=lines)


def _frame_signal(text: str, source: str, context: dict, user_memory: str) -> str:
    """Frame the signal text based on its source type."""

    user = context.get("user", "")
    trigger = context.get("trigger", "")
    event_type = context.get("event_type", "")

    # user notes line
    user_notes = ""
    if user_memory and user:
        user_notes = f"(You remember: {user_memory})"

    # recent chat context block
    recent_chat_block = _build_recent_chat_block(context)

    # --- Chat message ---
    if source == "chat":
        if user and user.lower() in ("exiled", "exiledr", "exiledra1n"):
            return CHAT_EXILED.format(
                message=text,
                recent_chat_block=recent_chat_block,
            )
        return CHAT_MESSAGE.format(
            user=user or "someone",
            message=text,
            user_notes=user_notes,
            recent_chat_block=recent_chat_block,
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

    # --- Game events (tiered by personality) ---
    if source == "game":
        SERIOUS_EVENTS = {"MyKill", "MyDeath", "MyAssist", "MyMultikill", "BaronKill", "Ace"}
        DISMISSIVE_EVENTS = {"DragonKill", "HeraldKill", "TurretKilled", "AllyKill", "AllyDeath"}
        MILESTONE_EVENTS = {"GameStart", "GameEnd"}

        if event_type in SERIOUS_EVENTS:
            return GAME_EVENT_SERIOUS.format(event=text)
        elif event_type in DISMISSIVE_EVENTS:
            return GAME_EVENT_DISMISSIVE.format(event=text)
        elif event_type in MILESTONE_EVENTS:
            return GAME_EVENT_MILESTONE.format(event=text)
        return GAME_EVENT_SERIOUS.format(event=text)  # fallback to serious

    # --- Silence filler (improv) ---
    if source == "silence_filler" and trigger == "silence_timer":
        return SILENCE_IMPROV.format(seed=text)

    # --- Channel promotion ---
    if source == "promotion":
        return PROMOTION

    # --- Fallback ---
    return text