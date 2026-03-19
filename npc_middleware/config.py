import os
from pathlib import Path

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"
LLM_MODEL = "llama3"
EMBEDDING_DIM = 768

LANCEDB_PATH = str(Path(__file__).parent.parent / "data" / "npc_memory.lancedb")

RETRIEVAL_TOP_K = 5
CONSOLIDATION_THRESHOLD = 5

EMOTION_DIMENSIONS = {
    "trust":     {"baseline": 0.5, "decay_per_hour": 0.02},
    "fear":      {"baseline": 0.1, "decay_per_hour": 0.05},
    "anger":     {"baseline": 0.0, "decay_per_hour": 0.10},
    "affection": {"baseline": 0.3, "decay_per_hour": 0.03},
}

NPC_SYSTEM_PROMPT_TEMPLATE = """You are {npc_name}, an NPC in a game world.
Personality: {personality}

Your current feelings toward this player:
{emotional_state}

You have the following memories relevant to this conversation:
{memories}

Respond in character. Be concise. Stay consistent with your memories, personality, and emotional state."""

EMOTION_ANALYSIS_PROMPT = """Analyze the emotional impact of this interaction on an NPC.

NPC: {npc_name} (Personality: {personality})
Current emotional state toward this player: {current_emotions}
Player said: "{player_message}"
NPC responded: "{npc_response}"

Return ONLY a JSON object with deltas (positive or negative floats, magnitude 0.0 to 0.3) for each dimension:
{{"trust": 0.0, "fear": 0.0, "anger": 0.0, "affection": 0.0}}

Consider: Did the player threaten, compliment, betray, help, or provoke the NPC? Keep deltas small for mundane exchanges."""

PROPAGATION_MIN_DELTA = 0.1

# Guardrails
MAX_RESPONSE_WORDS = 150
GUARDRAILS_LORE_TOP_K = 3

LORE_CHECK_PROMPT = """Check if the NPC response contradicts any of these established world facts.

World facts:
{lore_facts}

NPC response: "{response}"

Return ONLY a JSON object: {{"contradicts": true, "explanation": "brief reason", "relevant_facts": ["the contradicted fact(s)"]}}
If there is no contradiction, return: {{"contradicts": false, "explanation": "", "relevant_facts": []}}"""

GUARDRAIL_REPROMPT_ADDENDUM = """

IMPORTANT CORRECTION: Your previous response had issues:
{violations}

{lore_context}
Please regenerate your response, staying in character and avoiding the above issues."""

CONSOLIDATION_PROMPT = """You are analyzing a sequence of interactions between an NPC and a player.

NPC: {npc_name}
Player ID: {player_id}

Recent interactions (chronological order):
{interactions}

Write a concise 2-3 sentence summary of how the NPC now perceives this player.
Focus on: trust level, notable events, emotional tone, and any promises or debts.
Write from the NPC's perspective in third person (e.g., "The player has...")."""
