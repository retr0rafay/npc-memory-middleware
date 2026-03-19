from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import lancedb
import pyarrow as pa

from npc_middleware.config import EMBEDDING_DIM, LANCEDB_PATH, RETRIEVAL_TOP_K

_db: lancedb.DBConnection | None = None

MEMORIES_SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("npc_id", pa.string()),
    pa.field("player_id", pa.string()),
    pa.field("type", pa.string()),          # "interaction" | "consolidation"
    pa.field("message", pa.string()),
    pa.field("response", pa.string()),
    pa.field("timestamp", pa.string()),
    pa.field("metadata", pa.string()),       # JSON-encoded
    pa.field("consolidated", pa.bool_()),
    pa.field("vector", pa.list_(pa.float32(), EMBEDDING_DIM)),
])

EMOTIONS_SCHEMA = pa.schema([
    pa.field("npc_id", pa.string()),
    pa.field("player_id", pa.string()),
    pa.field("trust", pa.float32()),
    pa.field("fear", pa.float32()),
    pa.field("anger", pa.float32()),
    pa.field("affection", pa.float32()),
    pa.field("last_updated", pa.string()),
])

RELATIONSHIPS_SCHEMA = pa.schema([
    pa.field("source_npc_id", pa.string()),
    pa.field("target_npc_id", pa.string()),
    pa.field("relationship_type", pa.string()),
    pa.field("strength", pa.float32()),
])

LORE_SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("text", pa.string()),
    pa.field("metadata", pa.string()),
    pa.field("created_at", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), EMBEDDING_DIM)),
])

NPC_PROFILES_SCHEMA = pa.schema([
    pa.field("npc_id", pa.string()),
    pa.field("npc_name", pa.string()),
    pa.field("personality", pa.string()),
    pa.field("created_at", pa.string()),
    pa.field("content_policies", pa.string()),
])


def init_db() -> lancedb.DBConnection:
    global _db
    _db = lancedb.connect(LANCEDB_PATH)

    if "memories" not in _db.table_names():
        _db.create_table("memories", schema=MEMORIES_SCHEMA)

    if "npc_profiles" not in _db.table_names():
        _db.create_table("npc_profiles", schema=NPC_PROFILES_SCHEMA)

    if "emotions" not in _db.table_names():
        _db.create_table("emotions", schema=EMOTIONS_SCHEMA)

    if "relationships" not in _db.table_names():
        _db.create_table("relationships", schema=RELATIONSHIPS_SCHEMA)

    if "lore" not in _db.table_names():
        _db.create_table("lore", schema=LORE_SCHEMA)

    return _db


def get_db() -> lancedb.DBConnection:
    if _db is None:
        return init_db()
    return _db


def insert_memory(
    npc_id: str,
    player_id: str,
    record_type: str,
    message: str,
    response: str,
    embedding: list[float],
    metadata: dict | None = None,
) -> str:
    db = get_db()
    table = db.open_table("memories")
    record_id = uuid.uuid4().hex

    table.add([{
        "id": record_id,
        "npc_id": npc_id,
        "player_id": player_id,
        "type": record_type,
        "message": message,
        "response": response,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata": json.dumps(metadata or {}),
        "consolidated": False,
        "vector": embedding,
    }])

    return record_id


def search_memories(
    npc_id: str,
    query_embedding: list[float],
    top_k: int = RETRIEVAL_TOP_K,
    player_id: str | None = None,
) -> list[dict]:
    db = get_db()
    table = db.open_table("memories")

    where_clause = f"npc_id = '{npc_id}'"
    if player_id:
        where_clause += f" AND player_id = '{player_id}'"

    results = (
        table.search(query_embedding)
        .where(where_clause)
        .limit(top_k)
        .to_list()
    )

    return results


def get_unconsolidated_interactions(
    npc_id: str,
    player_id: str,
    limit: int = 5,
) -> list[dict]:
    db = get_db()
    table = db.open_table("memories")

    results = (
        table.search()
        .where(
            f"npc_id = '{npc_id}' AND player_id = '{player_id}' "
            f"AND type = 'interaction' AND consolidated = false"
        )
        .limit(limit)
        .to_list()
    )

    # Sort by timestamp for chronological order
    results.sort(key=lambda r: r["timestamp"])
    return results


def mark_consolidated(record_ids: list[str]) -> None:
    db = get_db()
    table = db.open_table("memories")

    id_list = ", ".join(f"'{rid}'" for rid in record_ids)
    # LanceDB update: set consolidated = true for these IDs
    table.update(where=f"id IN ({id_list})", values={"consolidated": True})


# --- NPC Profiles ---

def upsert_npc_profile(
    npc_id: str,
    npc_name: str,
    personality: str,
    content_policies: dict | None = None,
) -> None:
    db = get_db()
    table = db.open_table("npc_profiles")

    try:
        table.delete(f"npc_id = '{npc_id}'")
    except Exception:
        pass

    table.add([{
        "npc_id": npc_id,
        "npc_name": npc_name,
        "personality": personality,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "content_policies": json.dumps(content_policies or {}),
    }])


def update_npc_policies(npc_id: str, policies: dict[str, bool]) -> None:
    profile = get_npc_profile(npc_id)
    if not profile:
        return

    existing = json.loads(profile.get("content_policies", "{}"))
    existing.update(policies)

    upsert_npc_profile(
        npc_id=profile["npc_id"],
        npc_name=profile["npc_name"],
        personality=profile["personality"],
        content_policies=existing,
    )


# --- Emotional State ---

def get_emotional_state(npc_id: str, player_id: str) -> dict | None:
    db = get_db()
    table = db.open_table("emotions")

    try:
        results = (
            table.search()
            .where(f"npc_id = '{npc_id}' AND player_id = '{player_id}'")
            .limit(1)
            .to_list()
        )
        return results[0] if results else None
    except Exception:
        return None


def upsert_emotional_state(
    npc_id: str,
    player_id: str,
    trust: float,
    fear: float,
    anger: float,
    affection: float,
) -> None:
    db = get_db()
    table = db.open_table("emotions")

    try:
        table.delete(f"npc_id = '{npc_id}' AND player_id = '{player_id}'")
    except Exception:
        pass

    table.add([{
        "npc_id": npc_id,
        "player_id": player_id,
        "trust": trust,
        "fear": fear,
        "anger": anger,
        "affection": affection,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }])


def list_all_npc_profiles() -> list[dict]:
    db = get_db()
    table = db.open_table("npc_profiles")
    try:
        return table.search().limit(1000).to_list()
    except Exception:
        return []


def get_npc_profile(npc_id: str) -> dict | None:
    db = get_db()
    table = db.open_table("npc_profiles")

    try:
        results = table.search().where(f"npc_id = '{npc_id}'").limit(1).to_list()
        return results[0] if results else None
    except Exception:
        return None
