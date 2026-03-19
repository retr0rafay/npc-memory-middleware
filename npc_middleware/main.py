from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket

from npc_middleware.streaming import websocket_interact

from npc_middleware.config import NPC_SYSTEM_PROMPT_TEMPLATE
from npc_middleware.database import (
    get_npc_profile,
    init_db,
    insert_memory,
    list_all_npc_profiles,
    search_memories,
    update_npc_policies,
    upsert_npc_profile,
)
from npc_middleware.emotions import (
    format_emotional_state,
    get_or_create_state,
    update_emotions_from_interaction,
)
from npc_middleware.guardrails import (
    insert_lore_entry,
    list_lore_entries,
    run_guardrails,
)
from npc_middleware.ollama_client import embed, generate
from npc_middleware.reflection import increment_counter, maybe_consolidate
from npc_middleware.relationships import (
    delete_relationship,
    get_relationships,
    propagate_emotion_deltas,
    upsert_relationship,
)
from npc_middleware.schemas import (
    EmotionalState,
    InteractRequest,
    InteractResponse,
    LoreEntry,
    LoreEntryRequest,
    NPCProfileRequest,
    PolicyUpdateRequest,
    Relationship,
    RelationshipDeleteRequest,
    RelationshipRequest,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("LanceDB initialized")
    yield


app = FastAPI(title="NPC Memory Middleware", version="0.1.0", lifespan=lifespan)


@app.get("/npcs")
async def list_npcs() -> list[dict]:
    profiles = list_all_npc_profiles()
    results = []
    for p in profiles:
        policies = p.get("content_policies", "{}")
        if isinstance(policies, str):
            try:
                policies = json.loads(policies)
            except (json.JSONDecodeError, TypeError):
                policies = {}
        results.append({
            "npc_id": p["npc_id"],
            "npc_name": p["npc_name"],
            "personality": p["personality"],
            "content_policies": policies,
            "created_at": p.get("created_at", ""),
        })
    return results


@app.post("/npc/profile", status_code=201)
async def create_npc_profile(req: NPCProfileRequest) -> dict:
    upsert_npc_profile(
        npc_id=req.npc_id,
        npc_name=req.npc_name,
        personality=req.personality,
        content_policies=req.content_policies,
    )
    return {"status": "ok", "npc_id": req.npc_id}


@app.post("/interact", response_model=InteractResponse)
async def interact(req: InteractRequest) -> InteractResponse:
    # 1. Resolve NPC profile
    profile = get_npc_profile(req.npc_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"NPC '{req.npc_id}' not found. Create a profile first.")

    npc_name = profile["npc_name"]
    personality = profile["personality"]

    # 2. Embed the player message
    query_embedding = embed(req.message)

    # 3. Retrieve relevant memories (interactions + consolidations in one search)
    raw_memories = search_memories(
        npc_id=req.npc_id,
        query_embedding=query_embedding,
        player_id=req.player_id,
    )

    memories_text = _format_memories(raw_memories) if raw_memories else "No prior memories."

    # 4. Fetch emotional state (with lazy decay applied)
    emo_state = get_or_create_state(req.npc_id, req.player_id)
    emotional_state_text = format_emotional_state(emo_state)

    # 5. Build prompt and generate response
    system_prompt = NPC_SYSTEM_PROMPT_TEMPLATE.format(
        npc_name=npc_name,
        personality=personality,
        memories=memories_text,
        emotional_state=emotional_state_text,
    )

    npc_response = generate(prompt=req.message, system=system_prompt)

    # 6. Run guardrails validation
    guardrail_result = await run_guardrails(
        response=npc_response,
        npc_profile=profile,
        system_prompt=system_prompt,
        player_message=req.message,
    )
    npc_response = guardrail_result.final_response

    # 7. Store the interaction (post-guardrail response)
    record_id = insert_memory(
        npc_id=req.npc_id,
        player_id=req.player_id,
        record_type="interaction",
        message=req.message,
        response=npc_response,
        embedding=query_embedding,
        metadata=req.metadata,
    )

    # 7. Fire-and-forget: reflection + emotion update + gossip propagation
    increment_counter(req.npc_id, req.player_id)
    asyncio.create_task(
        maybe_consolidate(req.npc_id, req.player_id, npc_name)
    )

    async def _update_and_propagate():
        deltas = await update_emotions_from_interaction(
            req.npc_id, req.player_id, npc_name, personality,
            req.message, npc_response,
        )
        if deltas is not None:
            await propagate_emotion_deltas(req.npc_id, req.player_id, deltas)

    asyncio.create_task(_update_and_propagate())

    from datetime import datetime, timezone

    return InteractResponse(
        npc_id=req.npc_id,
        player_id=req.player_id,
        response=npc_response,
        retrieved_memory_count=len(raw_memories),
        timestamp=datetime.now(timezone.utc).isoformat(),
        guardrail_flags=guardrail_result.flags,
        guardrail_retried=guardrail_result.retried,
    )


@app.websocket("/interact/stream")
async def interact_stream(websocket: WebSocket):
    await websocket_interact(websocket)


@app.post("/npc/relationship", status_code=201)
async def create_relationship(req: RelationshipRequest) -> dict:
    if not get_npc_profile(req.source_npc_id):
        raise HTTPException(status_code=404, detail=f"NPC '{req.source_npc_id}' not found.")
    if not get_npc_profile(req.target_npc_id):
        raise HTTPException(status_code=404, detail=f"NPC '{req.target_npc_id}' not found.")
    upsert_relationship(req.source_npc_id, req.target_npc_id, req.relationship_type, req.strength)
    return {"status": "ok"}


@app.get("/npc/{npc_id}/relationships", response_model=list[Relationship])
async def list_relationships(npc_id: str) -> list[Relationship]:
    if not get_npc_profile(npc_id):
        raise HTTPException(status_code=404, detail=f"NPC '{npc_id}' not found.")
    rows = get_relationships(npc_id)
    return [
        Relationship(
            source_npc_id=r["source_npc_id"],
            target_npc_id=r["target_npc_id"],
            relationship_type=r["relationship_type"],
            strength=float(r["strength"]),
        )
        for r in rows
    ]


@app.delete("/npc/relationship")
async def remove_relationship(req: RelationshipDeleteRequest) -> dict:
    delete_relationship(req.source_npc_id, req.target_npc_id)
    return {"status": "ok"}


@app.post("/lore", status_code=201)
async def add_lore(req: LoreEntryRequest) -> dict:
    embedding = embed(req.text)
    entry_id = insert_lore_entry(req.text, embedding, req.metadata)
    return {"status": "ok", "id": entry_id}


@app.get("/lore", response_model=list[LoreEntry])
async def list_lore() -> list[LoreEntry]:
    entries = list_lore_entries()
    results = []
    for e in entries:
        meta = e.get("metadata", "{}")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        results.append(LoreEntry(
            id=e["id"],
            text=e["text"],
            metadata=meta,
            created_at=e["created_at"],
        ))
    return results


@app.patch("/npc/{npc_id}/policies")
async def update_policies(npc_id: str, req: PolicyUpdateRequest) -> dict:
    profile = get_npc_profile(npc_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"NPC '{npc_id}' not found.")
    update_npc_policies(npc_id, req.content_policies)
    return {"status": "ok", "npc_id": npc_id}


@app.get("/npc/{npc_id}/emotions/{player_id}", response_model=EmotionalState)
async def get_emotions(npc_id: str, player_id: str) -> EmotionalState:
    profile = get_npc_profile(npc_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"NPC '{npc_id}' not found.")

    state = get_or_create_state(npc_id, player_id)
    return EmotionalState(
        npc_id=state["npc_id"],
        player_id=state["player_id"],
        trust=float(state["trust"]),
        fear=float(state["fear"]),
        anger=float(state["anger"]),
        affection=float(state["affection"]),
        last_updated=state["last_updated"],
    )


def _format_memories(memories: list[dict]) -> str:
    lines = []
    for mem in memories:
        if mem["type"] == "consolidation":
            lines.append(f"[Summary] {mem['message']}")
        else:
            lines.append(f"[{mem['timestamp']}] Player said: \"{mem['message']}\" — You replied: \"{mem['response']}\"")
    return "\n".join(lines)


# --- Static UI ---
from pathlib import Path
from fastapi.staticfiles import StaticFiles

_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_static_dir), html=True), name="static")
