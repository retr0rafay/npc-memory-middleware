from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from npc_middleware.config import CONSOLIDATION_PROMPT, CONSOLIDATION_THRESHOLD
from npc_middleware.database import (
    get_unconsolidated_interactions,
    insert_memory,
    mark_consolidated,
)
from npc_middleware.ollama_client import embed, generate

logger = logging.getLogger(__name__)

# In-memory counter: (npc_id, player_id) -> unconsolidated interaction count
_counters: dict[tuple[str, str], int] = defaultdict(int)


def increment_counter(npc_id: str, player_id: str) -> int:
    key = (npc_id, player_id)
    _counters[key] += 1
    return _counters[key]


def reset_counter(npc_id: str, player_id: str) -> None:
    _counters[(npc_id, player_id)] = 0


def should_consolidate(npc_id: str, player_id: str) -> bool:
    return _counters.get((npc_id, player_id), 0) >= CONSOLIDATION_THRESHOLD


async def maybe_consolidate(npc_id: str, player_id: str, npc_name: str) -> None:
    if not should_consolidate(npc_id, player_id):
        return

    try:
        interactions = get_unconsolidated_interactions(npc_id, player_id)

        if len(interactions) < CONSOLIDATION_THRESHOLD:
            reset_counter(npc_id, player_id)
            return

        interaction_text = "\n".join(
            f"[{ix['timestamp']}] Player: {ix['message']} | NPC: {ix['response']}"
            for ix in interactions
        )

        prompt = CONSOLIDATION_PROMPT.format(
            npc_name=npc_name,
            player_id=player_id,
            interactions=interaction_text,
        )

        summary = await asyncio.to_thread(generate, prompt)
        summary_embedding = embed(summary)

        insert_memory(
            npc_id=npc_id,
            player_id=player_id,
            record_type="consolidation",
            message=summary,
            response="",
            embedding=summary_embedding,
            metadata={
                "source_ids": [ix["id"] for ix in interactions],
                "interaction_count": len(interactions),
            },
        )

        mark_consolidated([ix["id"] for ix in interactions])
        reset_counter(npc_id, player_id)

        logger.info(
            "Consolidated %d interactions for npc=%s player=%s",
            len(interactions), npc_id, player_id,
        )

    except Exception:
        logger.exception(
            "Reflection failed for npc=%s player=%s", npc_id, player_id,
        )
