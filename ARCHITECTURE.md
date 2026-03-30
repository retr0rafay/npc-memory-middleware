# NPC Memory Middleware - Architecture

> A headless AI middleware that gives game NPCs persistent memory, emotions, relationships, and personality - all running locally.

---

## System Overview

```mermaid
graph TB
    subgraph Clients["Clients"]
        GE["Game Engine"]
        AU["Authoring UI<br/><i>/ui/index.html</i>"]
        DD["Demo UI<br/><i>/ui/demo.html</i>"]
    end

    subgraph Middleware["FastAPI Middleware :8000"]
        direction TB
        API["REST + WebSocket API"]
        IP["Interaction Pipeline"]
        GM["Guardrails Module"]
        SM["Streaming Module"]
    end

    subgraph Intelligence["Local AI (Ollama :11434)"]
        LLM["llama3<br/><i>Text Generation</i>"]
        EMB["nomic-embed-text<br/><i>768-dim Embeddings</i>"]
    end

    subgraph Storage["LanceDB (Embedded)"]
        MEM[("memories")]
        EMO[("emotions")]
        REL[("relationships")]
        LOR[("lore")]
        NPC[("npc_profiles")]
    end

    GE -->|"HTTP / WebSocket"| API
    AU -->|"HTTP / WebSocket"| API
    DD -->|"HTTP / WebSocket"| API

    API --> IP
    API --> SM
    IP --> GM
    SM --> GM

    IP -->|"generate / embed"| LLM
    IP -->|"embed"| EMB
    SM -->|"stream tokens"| LLM
    GM -->|"lore check"| LLM
    GM -->|"embed"| EMB

    IP --> MEM
    IP --> EMO
    IP --> LOR
    IP --> NPC
    IP --> REL
    GM --> LOR

    style Clients fill:#e8f4f8,stroke:#2196F3,stroke-width:2px
    style Middleware fill:#fff3e0,stroke:#FF9800,stroke-width:2px
    style Intelligence fill:#f3e5f5,stroke:#9C27B0,stroke-width:2px
    style Storage fill:#e8f5e9,stroke:#4CAF50,stroke-width:2px
```

---

## Interaction Processing Pipeline

The core flow when a player talks to an NPC:

```mermaid
flowchart TD
    A["Player sends message"] --> B["Embed message<br/><i>nomic-embed-text → 768-dim vector</i>"]
    B --> C["Retrieve memories<br/><i>Vector similarity search, top 5</i>"]
    B --> D["Fetch lore<br/><i>Semantic search, top 3 facts</i>"]
    B --> E["Load emotion state<br/><i>Apply time-decay</i>"]
    B --> F["Load NPC profile<br/><i>Personality + policies</i>"]

    C --> G["Build system prompt"]
    D --> G
    E --> G
    F --> G

    G --> H["LLM generates response<br/><i>llama3</i>"]

    H --> I{"Guardrails check"}
    I -->|"Pass"| J["Return response to player"]
    I -->|"Violation"| K["Re-prompt with corrections"]
    K --> L["LLM retry"]
    L --> J

    J --> M["Store interaction as memory"]
    J --> N["Background tasks"]

    subgraph Background["Fire & Forget (async)"]
        N --> O["Update emotions<br/><i>LLM analyzes deltas</i>"]
        N --> P["Maybe consolidate<br/><i>Summarize if 5+ interactions</i>"]
        O --> Q["Gossip propagation<br/><i>Spread to related NPCs</i>"]
    end

    style A fill:#e3f2fd,stroke:#1565C0
    style J fill:#e8f5e9,stroke:#2E7D32
    style I fill:#fff8e1,stroke:#F9A825
    style Background fill:#fce4ec,stroke:#C62828,stroke-width:2px
```

---

## Emotion System

NPCs have four emotion dimensions per player, each with independent decay rates:

```mermaid
graph LR
    subgraph Emotions["Emotional Dimensions"]
        T["Trust<br/>baseline: 0.5<br/>decay: 0.02/hr"]
        F["Fear<br/>baseline: 0.1<br/>decay: 0.05/hr"]
        AN["Anger<br/>baseline: 0.0<br/>decay: 0.10/hr"]
        AF["Affection<br/>baseline: 0.3<br/>decay: 0.03/hr"]
    end

    INT["Interaction"] -->|"LLM analyzes<br/>emotional impact"| D["Emotion Deltas<br/><i>±0.0 to ±0.3</i>"]
    D --> T
    D --> F
    D --> AN
    D --> AF

    TIME["Time passes"] -->|"Decay toward<br/>baseline"| T
    TIME -->|"Decay toward<br/>baseline"| F
    TIME -->|"Decay toward<br/>baseline"| AN
    TIME -->|"Decay toward<br/>baseline"| AF

    style Emotions fill:#f3e5f5,stroke:#7B1FA2,stroke-width:2px
    style D fill:#fff3e0,stroke:#E65100
    style TIME fill:#e3f2fd,stroke:#1565C0
```

---

## Social Graph & Gossip Propagation

When an NPC's emotions change, related NPCs are influenced:

```mermaid
flowchart LR
    P["Player"] -->|"interaction"| A["NPC A<br/><i>Blacksmith</i>"]

    A -->|"friend (0.8)"| B["NPC B<br/><i>Tavern Owner</i>"]
    A -->|"rival (0.6)"| C["NPC C<br/><i>Merchant</i>"]
    A -->|"family (0.9)"| D["NPC D<br/><i>Apprentice</i>"]

    subgraph Propagation["Gossip Propagation"]
        direction TB
        E["Emotion delta from<br/>player interaction"]
        E -->|"delta × 0.8"| F["B's emotions shift"]
        E -->|"delta × 0.6"| G["C's emotions shift"]
        E -->|"delta × 0.9"| H["D's emotions shift"]
    end

    A --> E

    style A fill:#fff3e0,stroke:#E65100,stroke-width:2px
    style B fill:#e8f5e9,stroke:#2E7D32
    style C fill:#ffebee,stroke:#C62828
    style D fill:#e8f5e9,stroke:#2E7D32
    style Propagation fill:#f5f5f5,stroke:#616161,stroke-width:1px,stroke-dasharray: 5 5
```

> Propagation only fires when the total magnitude of deltas >= 0.1, preventing noise.

---

## Memory Lifecycle

```mermaid
flowchart TD
    subgraph Store["Memory Storage"]
        I1["Interaction 1"] --> DB[("LanceDB<br/>memories table")]
        I2["Interaction 2"] --> DB
        I3["Interaction 3"] --> DB
        I4["Interaction 4"] --> DB
        I5["Interaction 5"] --> DB
    end

    DB -->|"Threshold reached<br/>(5 interactions)"| R["Reflection Module"]
    R -->|"LLM summarizes into<br/>2-3 sentence perception"| S["Consolidated Memory"]
    S -->|"Embedded & stored"| DB

    I1 -.->|"marked consolidated"| X["Won't be<br/>re-summarized"]
    I2 -.->|"marked consolidated"| X
    I3 -.->|"marked consolidated"| X
    I4 -.->|"marked consolidated"| X
    I5 -.->|"marked consolidated"| X

    subgraph Retrieval["On Next Query"]
        Q["Player message"] -->|"embed"| VS["Vector similarity<br/>search (top 5)"]
        VS -->|"returns mix of"| RES["Recent interactions<br/>+ consolidated summaries"]
    end

    DB --> VS

    style Store fill:#e3f2fd,stroke:#1565C0,stroke-width:2px
    style S fill:#e8f5e9,stroke:#2E7D32,stroke-width:2px
    style Retrieval fill:#fff3e0,stroke:#E65100,stroke-width:2px
```

---

## Guardrails Pipeline

Multi-layer content validation with auto-correction:

```mermaid
flowchart TD
    R["NPC Response"] --> L1

    subgraph L1["Layer 1: Content Policies (Regex)"]
        direction LR
        P1["no_profanity<br/><i>blacklist → ***</i>"]
        P2["no_modern_references<br/><i>flag anachronisms</i>"]
        P3["stay_in_character<br/><i>flag AI language</i>"]
        P4["max_response_length<br/><i>truncate at 150 words</i>"]
    end

    L1 --> L2

    subgraph L2["Layer 2: Lore Consistency (LLM)"]
        direction LR
        LE["Embed response"] --> LS["Search top 3<br/>lore facts"]
        LS --> LC["LLM evaluates<br/>contradictions"]
    end

    L2 --> D{"Violations?"}
    D -->|"None"| OK["Return response<br/>guardrail_flags: none"]
    D -->|"Found"| RP["Re-prompt LLM<br/><i>inject violation context</i>"]
    RP --> RE["Re-run policies<br/><i>auto-fix only, no 2nd retry</i>"]
    RE --> OK2["Return corrected response<br/>guardrail_retried: true"]

    style L1 fill:#fff8e1,stroke:#F9A825,stroke-width:2px
    style L2 fill:#fce4ec,stroke:#C62828,stroke-width:2px
    style OK fill:#e8f5e9,stroke:#2E7D32
    style OK2 fill:#e8f5e9,stroke:#2E7D32
```

---

## WebSocket Streaming Modes

```mermaid
flowchart LR
    subgraph Raw["stream_raw (fast)"]
        direction TB
        R1["Generate full<br/>response"] --> R2["Stream all<br/>tokens to client"] --> R3["Run guardrails<br/>post-stream"] --> R4{"Violations?"}
        R4 -->|"Yes"| R5["Send correction<br/>message"]
        R4 -->|"No"| R6["Send complete"]
    end

    subgraph Validated["stream_validated (default)"]
        direction TB
        V1["Inject lore into<br/>system prompt"] --> V2["Stream tokens"] --> V3["Inline regex<br/>filter per token"] --> V4["Word count<br/>enforcement"] --> V5["Send filtered<br/>token to client"]
        V5 --> V6["Final validation<br/>on complete response"]
    end

    style Raw fill:#e3f2fd,stroke:#1565C0,stroke-width:2px
    style Validated fill:#f3e5f5,stroke:#7B1FA2,stroke-width:2px
```

---

## Database Schema

```mermaid
erDiagram
    npc_profiles {
        string npc_id PK
        string npc_name
        string personality
        string content_policies "JSON"
        string created_at
    }

    memories {
        string id PK
        string npc_id FK
        string player_id
        string type "interaction | consolidation"
        string message
        string response
        string timestamp
        string metadata "JSON"
        bool consolidated
        vector_768 vector "768-dim float32"
    }

    emotions {
        string npc_id PK,FK
        string player_id PK
        float trust "0.0 - 1.0"
        float fear "0.0 - 1.0"
        float anger "0.0 - 1.0"
        float affection "0.0 - 1.0"
        string last_updated
    }

    relationships {
        string source_npc_id FK
        string target_npc_id FK
        string relationship_type
        float strength "0.0 - 1.0"
    }

    lore {
        string id PK
        string text
        string metadata "JSON"
        string created_at
        vector_768 vector "768-dim float32"
    }

    npc_profiles ||--o{ memories : "has"
    npc_profiles ||--o{ emotions : "has"
    npc_profiles ||--o{ relationships : "source"
    npc_profiles ||--o{ relationships : "target"
    memories }o--|| npc_profiles : "belongs to"
    lore }o..o{ memories : "validates against"
```

---

## Module Dependency Map

```mermaid
graph TD
    MAIN["main.py<br/><i>FastAPI routes &<br/>orchestration</i>"]

    MAIN --> DB["database.py<br/><i>LanceDB CRUD &<br/>vector search</i>"]
    MAIN --> OC["ollama_client.py<br/><i>LLM generate &<br/>embed wrapper</i>"]
    MAIN --> EM["emotions.py<br/><i>State machine &<br/>time-decay</i>"]
    MAIN --> RL["relationships.py<br/><i>Social graph &<br/>gossip</i>"]
    MAIN --> GR["guardrails.py<br/><i>Policies &<br/>lore checks</i>"]
    MAIN --> ST["streaming.py<br/><i>WebSocket handler &<br/>token filtering</i>"]
    MAIN --> RF["reflection.py<br/><i>Memory<br/>consolidation</i>"]
    MAIN --> SC["schemas.py<br/><i>Pydantic models</i>"]
    MAIN --> CF["config.py<br/><i>Settings &<br/>prompts</i>"]

    ST --> OC
    ST --> DB
    ST --> GR
    ST --> EM
    GR --> OC
    GR --> DB
    RL --> DB
    RL --> EM
    RF --> OC
    RF --> DB
    EM --> DB
    EM --> CF
    GR --> CF
    ST --> CF
    DB --> CF

    style MAIN fill:#fff3e0,stroke:#E65100,stroke-width:3px
    style DB fill:#e8f5e9,stroke:#2E7D32,stroke-width:2px
    style OC fill:#f3e5f5,stroke:#7B1FA2,stroke-width:2px
    style GR fill:#ffebee,stroke:#C62828,stroke-width:2px
    style ST fill:#e3f2fd,stroke:#1565C0,stroke-width:2px
```

---

## Deployment Architecture

```mermaid
graph TB
    subgraph Docker["Docker Compose"]
        subgraph OC["ollama (container)"]
            OL["Ollama Server :11434"]
            M1["llama3"]
            M2["nomic-embed-text"]
            OL --> M1
            OL --> M2
        end

        subgraph OS["ollama-setup (init container)"]
            PULL["Pull models on startup"]
        end

        subgraph MW["middleware (container)"]
            FA["FastAPI + Uvicorn :8000"]
            LC["LanceDB (embedded)"]
            FA --> LC
        end

        OS -->|"pulls models"| OC
        MW -->|"HTTP API"| OC
    end

    subgraph Volumes["Persistent Volumes"]
        V1[("ollama_data<br/><i>Model cache</i>")]
        V2[("lancedb_data<br/><i>Vector DB files</i>")]
    end

    OC --> V1
    LC --> V2

    EXT["External Client :8000"] --> FA

    style Docker fill:#f5f5f5,stroke:#212121,stroke-width:2px
    style OC fill:#f3e5f5,stroke:#7B1FA2
    style MW fill:#fff3e0,stroke:#E65100
    style Volumes fill:#e8f5e9,stroke:#2E7D32,stroke-width:2px
```

---

## API Endpoint Map

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/npcs` | List all NPC profiles |
| `POST` | `/npc/profile` | Create or update an NPC |
| `PATCH` | `/npc/{id}/policies` | Update content policies |
| `POST` | `/interact` | Synchronous NPC interaction |
| `WS` | `/interact/stream` | Streaming NPC interaction |
| `GET` | `/npc/{id}/emotions/{player_id}` | Get emotional state |
| `POST` | `/npc/relationship` | Create NPC relationship |
| `GET` | `/npc/{id}/relationships` | List NPC relationships |
| `DELETE` | `/npc/relationship` | Remove relationship |
| `POST` | `/lore` | Add world lore entry |
| `GET` | `/lore` | List all lore entries |

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|----------|
| **API** | FastAPI + Uvicorn | Async HTTP/WebSocket server |
| **LLM** | Ollama (llama3) | Local text generation |
| **Embeddings** | Ollama (nomic-embed-text) | 768-dim vector embeddings |
| **Vector DB** | LanceDB | Embedded similarity search |
| **Validation** | Pydantic v2 | Request/response schemas |
| **Serialization** | PyArrow | LanceDB data structures |
| **Deployment** | Docker Compose | Multi-container orchestration |
