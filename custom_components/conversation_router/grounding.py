"""Grounding check for Sophia NLU matches.

Sophia NLU always reports confidence 1.0 and, when nothing really fits, still
latches onto one word of the sentence and binds it to an unrelated entity
("how deep is the ocean" -> cover.garage_door, "how much propane is left" ->
HassTurnOn on the garage door). Because Sophia executes the action before its
reply comes back, the check has to run BEFORE handing the request to Sophia:
ask its NLU server what it would match (recognition only, nothing executes),
then verify that every meaningful word of the sentence is explained by the
matched entity (its name, aliases, device, area) or by the intent's own
parameters. Unexplained words mean the match is a guess.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

# Words that carry no entity meaning: question/command scaffolding, pronouns,
# prepositions, states and generic measurement words.
STOPWORDS = frozenset(
    """
    a an the is are was were be been am what whats how hows who where when which why
    do does did can could would will should shall may might must please thanks thank
    alfred sophia hey hi ok okay jarvis computer
    i im me my mine we our us you your it its this that these those there here
    of in on at to for from by with and or but if so as into onto off up down out over
    set turn switch make get tell show give let put keep check
    much many current currently right now just still yet already again all every any some
    open opened close closed shut lock locked unlock unlocked start started stop stopped
    pause paused resume run running on off active inactive
    level state status value reading percent percentage degree degrees
    temperature temp humidity brightness bright dim dimmer brighter color colour volume
    minute minutes hour hours second seconds
    s t d ll re ve
    """.split()
)

# Words that refer to a domain without naming the entity.
DOMAIN_WORDS: dict[str, tuple[str, ...]] = {
    "light": ("light", "lights", "lamp", "lamps", "bulb", "bulbs"),
    "switch": ("switch", "plug", "outlet", "power"),
    "fan": ("fan", "fans", "speed"),
    "cover": ("cover", "blind", "blinds", "shade", "shades", "curtain", "curtains"),
    "lock": ("lock", "door"),
    "climate": ("thermostat", "heat", "heating", "cool", "cooling", "ac", "hvac"),
    "media_player": ("speaker", "speakers", "music", "tv", "television", "player", "song", "play", "playing"),
    "vacuum": ("vacuum", "robot", "clean", "cleaning"),
    "weather": ("weather", "forecast", "outside"),
    "sensor": ("sensor",),
    "binary_sensor": ("sensor",),
    "alarm_control_panel": ("alarm", "security", "arm", "armed", "disarm", "disarmed"),
}

# Slots that name the target. Every other slot value (state, brightness,
# color, temperature...) is an intent parameter and its words count as explained.
TARGET_SLOTS = frozenset({"name", "entity_id", "common_name", "domain", "device_class"})

_WORD_RE = re.compile(r"[a-z0-9]+")


def words(text: Any) -> set[str]:
    """Lower-case word tokens; apostrophes split ("what's" -> what, s).

    Non-strings (None, HA's ComputedNameType alias marker, ...) yield nothing.
    """
    if not isinstance(text, str):
        return set()
    return set(_WORD_RE.findall(text.lower().replace("’", "'")))


def stem(word: str) -> str:
    """Crude plural folding so "doors" matches "door" and "batteries" "battery"."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def stems(text_words: set[str]) -> set[str]:
    return {stem(w) for w in text_words}


def unexplained_words(
    text: str,
    vocab: set[str],
    slot_values: list[str],
    extra_allowed: set[str] | None = None,
) -> list[str]:
    """Meaningful words of `text` not covered by the entity vocabulary or slots."""
    allowed = stems(vocab) | stems({w for v in slot_values for w in words(v)})
    allowed |= stems(STOPWORDS) | stems(extra_allowed or set())
    left = []
    for w in sorted(words(text)):
        if w.isdigit() or re.fullmatch(r"\d+(st|nd|rd|th|am|pm|f|c)?", w):
            continue
        if stem(w) in allowed:
            continue
        left.append(w)
    return left


async def async_recognize(
    host: str, port: int, text: str, language: str, timeout: float = 3.0
) -> list[dict[str, Any]]:
    """Ask Sophia's NLU server what it would match. Recognition only: nothing executes.

    A throwaway session id keeps the probe out of any real conversation's state.
    """
    request = {
        "type": "recognize",
        "data": {
            "text": text,
            "language": language or "en",
            "session_id": f"router-probe-{uuid.uuid4().hex}",
            "context": {"is_reply": 0},
        },
    }

    async def _exchange() -> bytes:
        reader, writer = await asyncio.open_connection(host, port)
        try:
            writer.write((json.dumps(request, separators=(",", ":")) + "\n").encode())
            await writer.drain()
            if writer.can_write_eof():
                writer.write_eof()
            return await reader.read(65536)
        finally:
            writer.close()
            await writer.wait_closed()

    raw = await asyncio.wait_for(_exchange(), timeout)
    return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]


def matched_targets(results: list[dict[str, Any]]) -> list[tuple[str, str, list[str]]]:
    """(intent, entity_id, non-target slot values) for each recognized intent that names an entity."""
    out = []
    for result in results:
        if result.get("type") != "intent":
            continue
        data = result.get("data") or {}
        intent_name = (data.get("intent") or {}).get("name") or ""
        slots = {e.get("name"): e.get("value") for e in data.get("entities") or [] if e.get("name")}
        entity_id = slots.get("entity_id")
        if not entity_id or not isinstance(entity_id, str):
            continue
        params = [str(v) for k, v in slots.items() if k not in TARGET_SLOTS and v is not None]
        out.append((intent_name, entity_id, params))
    return out
