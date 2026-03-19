from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from npc_middleware.config import EMOTION_ANALYSIS_PROMPT, EMOTION_DIMENSIONS
from npc_middleware.database import get_emotional_state, upsert_emotional_state
from npc_middleware.ollama_client import generate

logger = logging.getLogger(__name__)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def get_or_create_state(npc_id: str, player_id: str) -> dict:
    state = get_emotional_state(npc_id, player_id)

    if state is None:
        # Initialize with baselines
        trust = EMOTION_DIMENSIONS["trust"]["baseline"]
        fear = EMOTION_DIMENSIONS["fear"]["baseline"]
        anger = EMOTION_DIMENSIONS["anger"]["baseline"]
        affection = EMOTION_DIMENSIONS["affection"]["baseline"]

        upsert_emotional_state(npc_id, player_id, trust, fear, anger, affection)

        return {
            "npc_id": npc_id,
            "player_id": player_id,
            "trust": trust,
            "fear": fear,
            "anger": anger,
            "affection": affection,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    return apply_decay(state)


def apply_decay(state: dict) -> dict:
    last_updated = datetime.fromisoformat(state["last_updated"])
    now = datetime.now(timezone.utc)
    hours_elapsed = (now - last_updated).total_seconds() / 3600.0

    if hours_elapsed < 0.01:
        return state

    changed = False
    for dim, cfg in EMOTION_DIMENSIONS.items():
        current = float(state[dim])
        baseline = cfg["baseline"]
        decay_rate = cfg["decay_per_hour"]

        new_val = current + (baseline - current) * min(1.0, decay_rate * hours_elapsed)
        new_val = _clamp(new_val)

        if abs(new_val - current) > 0.001:
            state[dim] = new_val
            changed = True

    if changed:
        upsert_emotional_state(
            state["npc_id"], state["player_id"],
            state["trust"], state["fear"], state["anger"], state["affection"],
        )
        state["last_updated"] = now.isoformat()

    return state


def format_emotional_state(state: dict) -> str:
    def label(v: float) -> str:
        if v >= 0.8:
            return "very high"
        if v >= 0.6:
            return "high"
        if v >= 0.4:
            return "moderate"
        if v >= 0.2:
            return "low"
        return "very low"

    return " | ".join(
        f"{dim.capitalize()}: {float(state[dim]):.2f} ({label(float(state[dim]))})"
        for dim in ("trust", "fear", "anger", "affection")
    )


async def update_emotions_from_interaction(
    npc_id: str,
    player_id: str,
    npc_name: str,
    personality: str,
    player_message: str,
    npc_response: str,
) -> dict[str, float] | None:
    try:
        state = get_or_create_state(npc_id, player_id)
        current_emotions = format_emotional_state(state)

        prompt = EMOTION_ANALYSIS_PROMPT.format(
            npc_name=npc_name,
            personality=personality,
            current_emotions=current_emotions,
            player_message=player_message,
            npc_response=npc_response,
        )

        raw = await asyncio.to_thread(generate, prompt)
        logger.info("Emotion LLM raw output for npc=%s player=%s: %s", npc_id, player_id, raw)

        # Strip markdown fences if present
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")

        # Try to extract JSON object if LLM added extra text
        json_match = re.search(r"\{[^}]+\}", cleaned)
        if json_match:
            cleaned = json_match.group()

        deltas = json.loads(cleaned)

        new_trust = _clamp(float(state["trust"]) + deltas.get("trust", 0.0))
        new_fear = _clamp(float(state["fear"]) + deltas.get("fear", 0.0))
        new_anger = _clamp(float(state["anger"]) + deltas.get("anger", 0.0))
        new_affection = _clamp(float(state["affection"]) + deltas.get("affection", 0.0))

        upsert_emotional_state(npc_id, player_id, new_trust, new_fear, new_anger, new_affection)

        logger.info(
            "Emotions updated for npc=%s player=%s: trust=%.2f fear=%.2f anger=%.2f affection=%.2f",
            npc_id, player_id, new_trust, new_fear, new_anger, new_affection,
        )

        return deltas

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("Failed to parse emotion deltas for npc=%s player=%s: %s", npc_id, player_id, e)
        return None
    except Exception:
        logger.exception("Emotion update failed for npc=%s player=%s", npc_id, player_id)
        return None
