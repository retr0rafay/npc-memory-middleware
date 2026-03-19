from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class InteractRequest(BaseModel):
    npc_id: str
    player_id: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class InteractResponse(BaseModel):
    npc_id: str
    player_id: str
    response: str
    retrieved_memory_count: int
    timestamp: str
    guardrail_flags: list[str] = Field(default_factory=list)
    guardrail_retried: bool = False


class NPCProfileRequest(BaseModel):
    npc_id: str
    npc_name: str
    personality: str
    content_policies: dict[str, bool] = Field(default_factory=dict)


class NPCProfile(BaseModel):
    npc_id: str
    npc_name: str
    personality: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EmotionalState(BaseModel):
    npc_id: str
    player_id: str
    trust: float
    fear: float
    anger: float
    affection: float
    last_updated: str


class EmotionDeltas(BaseModel):
    trust: float = 0.0
    fear: float = 0.0
    anger: float = 0.0
    affection: float = 0.0


class RelationshipRequest(BaseModel):
    source_npc_id: str
    target_npc_id: str
    relationship_type: str
    strength: float = Field(ge=0.0, le=1.0)


class RelationshipDeleteRequest(BaseModel):
    source_npc_id: str
    target_npc_id: str


class Relationship(BaseModel):
    source_npc_id: str
    target_npc_id: str
    relationship_type: str
    strength: float


class LoreEntryRequest(BaseModel):
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class LoreEntry(BaseModel):
    id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class PolicyUpdateRequest(BaseModel):
    content_policies: dict[str, bool]


class MemoryRecord(BaseModel):
    id: str
    npc_id: str
    player_id: str
    type: str  # "interaction" or "consolidation"
    message: str
    response: str = ""
    timestamp: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    consolidated: bool = False
