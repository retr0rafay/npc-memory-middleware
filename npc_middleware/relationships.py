from __future__ import annotations

import logging

from npc_middleware.config import PROPAGATION_MIN_DELTA
from npc_middleware.database import get_db, upsert_emotional_state
from npc_middleware.emotions import _clamp, get_or_create_state

logger = logging.getLogger(__name__)


def get_relationships(npc_id: str) -> list[dict]:
    db = get_db()
    table = db.open_table("relationships")

    try:
        results = (
            table.search()
            .where(f"source_npc_id = '{npc_id}' OR target_npc_id = '{npc_id}'")
            .limit(100)
            .to_list()
        )
        return results
    except Exception:
        return []


def get_outgoing_relationships(npc_id: str) -> list[dict]:
    db = get_db()
    table = db.open_table("relationships")

    try:
        results = (
            table.search()
            .where(f"source_npc_id = '{npc_id}'")
            .limit(100)
            .to_list()
        )
        return results
    except Exception:
        return []


def upsert_relationship(
    source_npc_id: str,
    target_npc_id: str,
    relationship_type: str,
    strength: float,
) -> None:
    db = get_db()
    table = db.open_table("relationships")

    try:
        table.delete(
            f"source_npc_id = '{source_npc_id}' AND target_npc_id = '{target_npc_id}'"
        )
    except Exception:
        pass

    table.add([{
        "source_npc_id": source_npc_id,
        "target_npc_id": target_npc_id,
        "relationship_type": relationship_type,
        "strength": strength,
    }])


def delete_relationship(source_npc_id: str, target_npc_id: str) -> None:
    db = get_db()
    table = db.open_table("relationships")

    try:
        table.delete(
            f"source_npc_id = '{source_npc_id}' AND target_npc_id = '{target_npc_id}'"
        )
    except Exception:
        pass


async def propagate_emotion_deltas(
    source_npc_id: str,
    player_id: str,
    deltas: dict[str, float],
) -> None:
    magnitude = sum(abs(v) for v in deltas.values())
    if magnitude < PROPAGATION_MIN_DELTA:
        logger.debug(
            "Skipping propagation for npc=%s (magnitude=%.3f < threshold=%.3f)",
            source_npc_id, magnitude, PROPAGATION_MIN_DELTA,
        )
        return

    relationships = get_outgoing_relationships(source_npc_id)
    if not relationships:
        return

    for rel in relationships:
        target_npc_id = rel["target_npc_id"]
        strength = float(rel["strength"])

        try:
            state = get_or_create_state(target_npc_id, player_id)

            attenuated = {dim: val * strength for dim, val in deltas.items()}

            new_trust = _clamp(float(state["trust"]) + attenuated.get("trust", 0.0))
            new_fear = _clamp(float(state["fear"]) + attenuated.get("fear", 0.0))
            new_anger = _clamp(float(state["anger"]) + attenuated.get("anger", 0.0))
            new_affection = _clamp(float(state["affection"]) + attenuated.get("affection", 0.0))

            upsert_emotional_state(
                target_npc_id, player_id,
                new_trust, new_fear, new_anger, new_affection,
            )

            logger.info(
                "Propagated emotions from npc=%s to npc=%s (rel=%s, strength=%.2f) "
                "for player=%s: trust=%.2f fear=%.2f anger=%.2f affection=%.2f",
                source_npc_id, target_npc_id, rel["relationship_type"], strength,
                player_id, new_trust, new_fear, new_anger, new_affection,
            )

        except Exception:
            logger.exception(
                "Failed to propagate emotions from npc=%s to npc=%s for player=%s",
                source_npc_id, target_npc_id, player_id,
            )
