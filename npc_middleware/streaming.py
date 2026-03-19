from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

from npc_middleware.config import NPC_SYSTEM_PROMPT_TEMPLATE
from npc_middleware.database import (
    get_npc_profile,
    insert_memory,
    search_memories,
)
from npc_middleware.emotions import (
    format_emotional_state,
    get_or_create_state,
    update_emotions_from_interaction,
)
from npc_middleware.config import LORE_INJECTION_TEMPLATE, MAX_RESPONSE_WORDS
from npc_middleware.guardrails import (
    fetch_relevant_lore,
    filter_token,
    _get_active_policies,
    run_guardrails,
)
from npc_middleware.ollama_client import embed, generate, generate_stream
from npc_middleware.reflection import increment_counter, maybe_consolidate
from npc_middleware.relationships import propagate_emotion_deltas
from npc_middleware.schemas import InteractRequest

logger = logging.getLogger(__name__)


def _format_memories(memories: list[dict]) -> str:
    lines = []
    for mem in memories:
        if mem["type"] == "consolidation":
            lines.append(f"[Summary] {mem['message']}")
        else:
            lines.append(
                f"[{mem['timestamp']}] Player said: \"{mem['message']}\" "
                f"— You replied: \"{mem['response']}\""
            )
    return "\n".join(lines)


async def _send_status(ws: WebSocket, status: str) -> None:
    await ws.send_json({"type": "status", "status": status})


def _push_tokens_to_queue(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
    prompt: str,
    system_prompt: str,
) -> None:
    try:
        for token in generate_stream(prompt, system_prompt):
            asyncio.run_coroutine_threadsafe(queue.put(token), loop).result()
    except Exception as e:
        asyncio.run_coroutine_threadsafe(
            queue.put(("__error__", str(e))), loop
        ).result()
    finally:
        asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()


def _extract_correction_reason(flags: list[str]) -> str:
    for f in flags:
        if f.startswith("violated:"):
            return f.split(":", 1)[1]
    return "policy_violation"


async def websocket_interact(websocket: WebSocket) -> None:
    await websocket.accept()

    try:
        raw = await websocket.receive_text()
        data = json.loads(raw)

        # Validate input
        req = InteractRequest.model_validate(data)

        # 1. Resolve NPC profile
        profile = get_npc_profile(req.npc_id)
        if not profile:
            await websocket.send_json({
                "type": "error",
                "detail": f"NPC '{req.npc_id}' not found.",
            })
            return

        npc_name = profile["npc_name"]
        personality = profile["personality"]

        # 2. Embed
        await _send_status(websocket, "embedding_message")
        query_embedding = await asyncio.to_thread(embed, req.message)

        # 3. Retrieve memories
        await _send_status(websocket, "retrieving_memories")
        raw_memories = search_memories(
            npc_id=req.npc_id,
            query_embedding=query_embedding,
            player_id=req.player_id,
        )
        memories_text = _format_memories(raw_memories) if raw_memories else "No prior memories."

        # 4. Emotional state
        emo_state = get_or_create_state(req.npc_id, req.player_id)
        emotional_state_text = format_emotional_state(emo_state)

        # 5. Determine streaming mode and build prompt
        mode = data.get("mode", "stream_validated")

        if mode == "stream_raw":
            # --- RAW MODE: stream tokens first, guardrails after ---
            system_prompt = NPC_SYSTEM_PROMPT_TEMPLATE.format(
                npc_name=npc_name,
                personality=personality,
                memories=memories_text,
                emotional_state=emotional_state_text,
                lore_section="",
            )
            await _send_status(websocket, "generating_response")

            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()
            full_tokens: list[str] = []

            loop.run_in_executor(
                None, _push_tokens_to_queue, loop, queue, req.message, system_prompt,
            )

            while True:
                token = await queue.get()
                if token is None:
                    break
                if isinstance(token, tuple) and token[0] == "__error__":
                    await websocket.send_json({"type": "error", "detail": token[1]})
                    return
                full_tokens.append(token)
                await websocket.send_json({"type": "token", "content": token})

            full_response = "".join(full_tokens)

            await _send_status(websocket, "checking_guardrails")
            guardrail_result = await run_guardrails(
                response=full_response,
                npc_profile=profile,
                system_prompt=system_prompt,
                player_message=req.message,
            )

            await websocket.send_json({
                "type": "complete",
                "response": guardrail_result.final_response,
                "guardrail_flags": guardrail_result.flags,
                "guardrail_retried": guardrail_result.retried,
                "retrieved_memory_count": len(raw_memories),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            if guardrail_result.retried:
                reason = _extract_correction_reason(guardrail_result.flags)
                await websocket.send_json({
                    "type": "correction",
                    "response": guardrail_result.final_response,
                    "guardrail_flags": guardrail_result.flags,
                    "reason": reason,
                })

            npc_response = guardrail_result.final_response

        else:
            # --- VALIDATED MODE (default): inject lore upfront, stream with inline filtering ---

            # Pre-fetch relevant lore and inject into system prompt
            lore_text = fetch_relevant_lore(query_embedding)
            lore_section = LORE_INJECTION_TEMPLATE.format(lore_facts=lore_text) if lore_text else ""
            system_prompt = NPC_SYSTEM_PROMPT_TEMPLATE.format(
                npc_name=npc_name,
                personality=personality,
                memories=memories_text,
                emotional_state=emotional_state_text,
                lore_section=lore_section,
            )

            # Stream tokens with inline regex filtering
            await _send_status(websocket, "generating_response")

            active_policies = _get_active_policies(profile)
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()
            full_tokens: list[str] = []
            all_flags: list[str] = []
            word_count = 0
            max_words_reached = False

            loop.run_in_executor(
                None, _push_tokens_to_queue, loop, queue, req.message, system_prompt,
            )

            while True:
                token = await queue.get()
                if token is None:
                    break
                if isinstance(token, tuple) and token[0] == "__error__":
                    await websocket.send_json({"type": "error", "detail": token[1]})
                    return

                # Word count check (max_response_length)
                word_count += len(token.split())
                if word_count > MAX_RESPONSE_WORDS:
                    if not max_words_reached:
                        max_words_reached = True
                        all_flags.append("auto_fixed:max_response_length")
                    continue  # silently drop tokens past the limit

                # Inline regex filtering (profanity, modern refs, character breaks)
                filtered, token_flags = filter_token(token, profile, active_policies)
                all_flags.extend(token_flags)

                full_tokens.append(filtered)
                await websocket.send_json({"type": "token", "content": filtered})

            full_response = "".join(full_tokens)

            # Deduplicate flags
            unique_flags = list(dict.fromkeys(all_flags))

            await websocket.send_json({
                "type": "complete",
                "response": full_response,
                "guardrail_flags": unique_flags,
                "guardrail_retried": False,
                "retrieved_memory_count": len(raw_memories),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            npc_response = full_response

        # 10. Store interaction
        insert_memory(
            npc_id=req.npc_id,
            player_id=req.player_id,
            record_type="interaction",
            message=req.message,
            response=npc_response,
            embedding=query_embedding,
            metadata=req.metadata,
        )

        # 11. Fire-and-forget side effects
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

    except WebSocketDisconnect:
        pass
    except json.JSONDecodeError:
        try:
            await websocket.send_json({"type": "error", "detail": "Invalid JSON"})
        except Exception:
            pass
    except Exception as e:
        logger.exception("WebSocket interact error")
        try:
            await websocket.send_json({"type": "error", "detail": str(e)})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
