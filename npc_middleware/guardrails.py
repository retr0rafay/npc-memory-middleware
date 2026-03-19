from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from npc_middleware.config import (
    EMBEDDING_DIM,
    GUARDRAIL_REPROMPT_ADDENDUM,
    GUARDRAILS_LORE_TOP_K,
    LORE_CHECK_PROMPT,
    MAX_RESPONSE_WORDS,
)
from npc_middleware.database import get_db
from npc_middleware.ollama_client import embed, generate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class PolicyResult:
    passed: bool
    violation: str = ""
    auto_fixed: str | None = None
    severity: str = "soft"


@dataclass
class GuardrailResult:
    final_response: str
    flags: list[str] = field(default_factory=list)
    retried: bool = False


# ---------------------------------------------------------------------------
# Lore storage (LanceDB)
# ---------------------------------------------------------------------------

def insert_lore_entry(
    text: str,
    embedding: list[float],
    metadata: dict | None = None,
) -> str:
    db = get_db()
    table = db.open_table("lore")
    entry_id = uuid.uuid4().hex

    table.add([{
        "id": entry_id,
        "text": text,
        "metadata": json.dumps(metadata or {}),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "vector": embedding,
    }])

    return entry_id


def list_lore_entries() -> list[dict]:
    db = get_db()
    table = db.open_table("lore")

    try:
        return table.search().limit(1000).to_list()
    except Exception:
        return []


def search_lore(query_embedding: list[float], top_k: int = GUARDRAILS_LORE_TOP_K) -> list[dict]:
    db = get_db()
    table = db.open_table("lore")

    try:
        return table.search(query_embedding).limit(top_k).to_list()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Policy registry
# ---------------------------------------------------------------------------

_PROFANITY_WORDS = [
    "damn", "hell", "shit", "fuck", "ass", "bastard", "crap", "bitch",
]
_PROFANITY_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _PROFANITY_WORDS) + r")\b",
    re.IGNORECASE,
)

_MODERN_REFS_PATTERN = re.compile(
    r"\b(internet|smartphone|computer|laptop|email|website|television|TV|"
    r"social media|google|twitter|facebook|instagram|tiktok|youtube|"
    r"electricity|airplane|automobile|phone|wifi|bluetooth|app|download|"
    r"screenshot|selfie|hashtag|podcast|streaming)\b",
    re.IGNORECASE,
)

_AI_ASSISTANT_PATTERN = re.compile(
    r"(as an AI|I'm a language model|I cannot help|I'm an artificial|"
    r"how can I assist you|I don't have personal|as a large language|"
    r"I'm programmed to|my training data|I was trained)",
    re.IGNORECASE,
)


def _check_no_profanity(response: str, profile: dict) -> PolicyResult:
    if _PROFANITY_PATTERN.search(response):
        fixed = _PROFANITY_PATTERN.sub("***", response)
        return PolicyResult(
            passed=False,
            violation="Response contains profanity.",
            auto_fixed=fixed,
            severity="soft",
        )
    return PolicyResult(passed=True)


def _check_no_modern_references(response: str, profile: dict) -> PolicyResult:
    match = _MODERN_REFS_PATTERN.search(response)
    if match:
        return PolicyResult(
            passed=False,
            violation=f"Response references modern concept: '{match.group()}'.",
            severity="soft",
        )
    return PolicyResult(passed=True)


def _check_stay_in_character(response: str, profile: dict) -> PolicyResult:
    if _AI_ASSISTANT_PATTERN.search(response):
        return PolicyResult(
            passed=False,
            violation="Response breaks character with AI-assistant language.",
            severity="soft",
        )
    return PolicyResult(passed=True)


def _check_max_response_length(response: str, profile: dict) -> PolicyResult:
    words = response.split()
    if len(words) > MAX_RESPONSE_WORDS:
        # Truncate at sentence boundary near the limit
        truncated_words = words[:MAX_RESPONSE_WORDS]
        truncated = " ".join(truncated_words)

        # Try to end at a sentence boundary
        last_period = truncated.rfind(".")
        last_excl = truncated.rfind("!")
        last_q = truncated.rfind("?")
        last_boundary = max(last_period, last_excl, last_q)

        if last_boundary > len(truncated) // 2:
            truncated = truncated[: last_boundary + 1]

        return PolicyResult(
            passed=False,
            violation=f"Response exceeds {MAX_RESPONSE_WORDS} words ({len(words)} words).",
            auto_fixed=truncated,
            severity="soft",
        )
    return PolicyResult(passed=True)


POLICY_REGISTRY: dict[str, callable] = {
    "no_profanity": _check_no_profanity,
    "no_modern_references": _check_no_modern_references,
    "stay_in_character": _check_stay_in_character,
    "max_response_length": _check_max_response_length,
}

# Policies safe to run on partial text (token-by-token)
INLINE_FILTER_POLICIES = {"no_profanity", "no_modern_references", "stay_in_character"}

# Default policies (all enabled)
DEFAULT_POLICIES = {name: True for name in POLICY_REGISTRY}


# ---------------------------------------------------------------------------
# Lore pre-fetch (for prompt injection)
# ---------------------------------------------------------------------------

def fetch_relevant_lore(query_embedding: list[float]) -> str:
    """Fetch relevant lore facts and format them for prompt injection."""
    results = search_lore(query_embedding)
    if not results:
        return ""
    return "\n".join(f"- {entry['text']}" for entry in results)


# ---------------------------------------------------------------------------
# Inline token filtering (for live streaming)
# ---------------------------------------------------------------------------

def filter_token(text: str, profile: dict, active_policies: list[str]) -> tuple[str, list[str]]:
    """Filter a text chunk using fast regex policies. Returns (filtered_text, flags)."""
    current = text
    flags = []

    for policy_name in active_policies:
        if policy_name not in INLINE_FILTER_POLICIES:
            continue
        check_fn = POLICY_REGISTRY.get(policy_name)
        if not check_fn:
            continue
        result = check_fn(current, profile)
        if not result.passed:
            if result.auto_fixed is not None:
                current = result.auto_fixed
                flags.append(f"auto_fixed:{policy_name}")
            else:
                flags.append(f"flagged:{policy_name}")

    return current, flags


# ---------------------------------------------------------------------------
# Validation pipeline
# ---------------------------------------------------------------------------

def _get_active_policies(profile: dict) -> list[str]:
    policies_json = profile.get("content_policies", "{}")
    try:
        policies = json.loads(policies_json) if isinstance(policies_json, str) else policies_json
    except (json.JSONDecodeError, TypeError):
        policies = {}

    # Merge with defaults: default all on, override with profile settings
    merged = {**DEFAULT_POLICIES, **policies}
    return [name for name, enabled in merged.items() if enabled]


def _run_content_policies(
    response: str, profile: dict, active_policies: list[str],
) -> tuple[str, list[str], list[str]]:
    """Run all active policies. Returns (possibly_fixed_response, flags, violations_for_reprompt)."""
    current = response
    flags: list[str] = []
    violations: list[str] = []

    for policy_name in active_policies:
        check_fn = POLICY_REGISTRY.get(policy_name)
        if not check_fn:
            continue

        result = check_fn(current, profile)
        if result.passed:
            continue

        if result.auto_fixed is not None:
            current = result.auto_fixed
            flags.append(f"auto_fixed:{policy_name}")
        else:
            violations.append(result.violation)
            flags.append(f"violated:{policy_name}")

    return current, flags, violations


async def _check_lore_consistency(
    response: str,
) -> tuple[bool, list[str], str]:
    """Check response against lore. Returns (consistent, relevant_facts, explanation)."""
    response_embedding = await asyncio.to_thread(embed, response)
    lore_results = search_lore(response_embedding)

    if not lore_results:
        return True, [], ""

    lore_facts = "\n".join(f"- {entry['text']}" for entry in lore_results)

    prompt = LORE_CHECK_PROMPT.format(lore_facts=lore_facts, response=response)

    raw = await asyncio.to_thread(generate, prompt)

    try:
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
        json_match = re.search(r"\{[^}]*\}", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group()

        result = json.loads(cleaned)

        if result.get("contradicts", False):
            facts = result.get("relevant_facts", [])
            explanation = result.get("explanation", "")
            return False, facts, explanation

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Failed to parse lore check result: %s", e)

    return True, [], ""


async def run_guardrails(
    response: str,
    npc_profile: dict,
    system_prompt: str,
    player_message: str,
) -> GuardrailResult:
    active_policies = _get_active_policies(npc_profile)
    flags: list[str] = []
    retried = False

    # 1. Run content policies (first pass)
    current, policy_flags, violations = _run_content_policies(
        response, npc_profile, active_policies,
    )
    flags.extend(policy_flags)

    # 2. Check lore consistency
    lore_consistent, contradicted_facts, lore_explanation = await _check_lore_consistency(current)

    if not lore_consistent:
        violations.append(f"Lore contradiction: {lore_explanation}")
        flags.append("violated:lore_consistency")

    # 3. If violations need re-prompting, do ONE retry
    if violations:
        retried = True
        violations_text = "\n".join(f"- {v}" for v in violations)

        lore_context = ""
        if contradicted_facts:
            lore_context = "Correct world facts:\n" + "\n".join(
                f"- {fact}" for fact in contradicted_facts
            )

        correction = GUARDRAIL_REPROMPT_ADDENDUM.format(
            violations=violations_text,
            lore_context=lore_context,
        )

        corrected_prompt = system_prompt + correction
        current = await asyncio.to_thread(
            generate, player_message, corrected_prompt,
        )

        # Re-run content policies on retried response (auto-fix only, no further retry)
        current, retry_flags, _ = _run_content_policies(
            current, npc_profile, active_policies,
        )
        flags.extend(retry_flags)

    logger.info(
        "Guardrails complete: flags=%s retried=%s",
        flags, retried,
    )

    return GuardrailResult(
        final_response=current,
        flags=flags,
        retried=retried,
    )
