# NPC Memory Middleware

A headless middleware that gives game NPCs persistent memory, emotions, relationships, and personality — powered by local LLMs.

**No cloud APIs. Everything runs on your machine.**

## Features

- **RAG Memory Pipeline** — NPCs remember past interactions via semantic search
- **Emotional State Machine** — Trust, fear, anger, affection per NPC-player pair with time decay
- **Relationship Graph** — NPCs gossip. Help the blacksmith, and his cousin at the tavern hears about it
- **Guardrails Engine** — Content policies + lore consistency checks prevent NPCs from breaking character
- **Reflection Loop** — Automatic memory consolidation summarizes interactions into long-term perception
- **WebSocket Streaming** — NPC responses stream token-by-token for real-time feel
- **Authoring UI** — Web dashboard for managing NPCs, relationships, lore, and content policies
- **Playable Demo** — "Thornhaven" — a text RPG with 5 interconnected NPCs

## Tech Stack

- **FastAPI** — API server
- **LanceDB** — Local vector database for memory storage
- **Ollama** — Local LLM inference (Llama 3 + nomic-embed-text)

## Quick Start (Docker)

```bash
git clone <repo-url> && cd NPC_MIDDLEWARE
docker compose up --build
```

This starts:
- **Ollama** on port 11434 (auto-pulls llama3 + nomic-embed-text)
- **Middleware** on port 8000

Then open:
- Demo game: http://localhost:8000/ui/demo.html
- Authoring UI: http://localhost:8000/ui/

## Quick Start (Local)

```bash
# 1. Install Ollama
brew install ollama    # or download from ollama.com

# 2. Start Ollama and pull models
ollama serve &
ollama pull llama3
ollama pull nomic-embed-text

# 3. Install dependencies and run
pip install -r requirements.txt
uvicorn npc_middleware.main:app --reload
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/npcs` | List all NPCs |
| POST | `/npc/profile` | Create/update NPC |
| POST | `/interact` | Chat with NPC (sync) |
| WS | `/interact/stream` | Chat with NPC (streaming) |
| GET | `/npc/{id}/emotions/{player_id}` | Get emotional state |
| POST | `/npc/relationship` | Create NPC relationship |
| GET | `/npc/{id}/relationships` | List relationships |
| POST | `/lore` | Add world lore entry |
| GET | `/lore` | List lore entries |
| PATCH | `/npc/{id}/policies` | Update content policies |

## Architecture

```
Client (Game/UI) → FastAPI Middleware → Ollama (Local LLM)
                                     → LanceDB (Vector DB)
```

Each interaction flows through: **Embed → Retrieve → Emotions → Prompt → Generate → Guardrails → Store → Background tasks (emotion update, gossip, reflection)**

## Project Structure

```
npc_middleware/
├── main.py              # FastAPI app, all endpoints
├── streaming.py         # WebSocket streaming handler
├── database.py          # LanceDB tables and operations
├── ollama_client.py     # LLM and embedding calls
├── emotions.py          # Emotional state machine with decay
├── reflection.py        # Memory consolidation
├── relationships.py     # NPC social graph + gossip propagation
├── guardrails.py        # Content policies + lore consistency
├── schemas.py           # Pydantic models
└── config.py            # Settings and prompt templates
static/
├── index.html           # Authoring UI
└── demo.html            # Thornhaven playable demo
```

## License

MIT
