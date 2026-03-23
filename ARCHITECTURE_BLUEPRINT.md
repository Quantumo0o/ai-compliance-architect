# Architecture Blueprint: Async Double-Blind RAG (V4 - Hard Truths)

# Architecture Blueprint: Async Double-Blind RAG (V5 - Production Local)

This document is the result of a cynical, zero-sugar-coat engineering audit of the **AI Compliance Matrix Architect**, updated to reflect the deliberate choice of a zero-dependency, local-only architecture.

## 🏗️ High-Level Workflow (The "Optimized Local" Reality)

```mermaid
graph TD
    subgraph "Phase 1: Ingestion & Heuristics"
        A[RFP PDF] --> B[pdfplumber: Digital Extract]
        B --> STITCH[Page Stitching Buffer]
        STITCH --> C{Risk Scorer}
        C -- "High Entropy" --> D[pdf2image + Nemotron-OCR]
        C -- "Clean" --> E[Consolidated Markdown]
        D --> E
    end

    subgraph "Phase 2: Extraction"
        E --> F[Mistral-Large-3]
        F --> G[Pydantic Regex/JSON Parser]
        G --> H[(SQLite DB: WAL Mode)]
    end

    subgraph "Phase 3: Async Adjudication Pipeline"
        H --> I[FastAPI Background Task]
        I --> CA{"SHA-256 Context Cache"}
        
        subgraph "Path A: Primary Judging"
            CA -- "Miss" --> RAG1[BGE-M3 Vector Search]
            RAG1 --> RERANK[NV-RerankQA Precision Pass]
            RERANK --> T1[Tier-1: Llama-3.1-8B]
            T1 --> T2C{Conf < 0.70?}
            T2C -- "Yes" --> T2[Tier-2: Mistral-Large-3]
        end

        subgraph "Path B: Double-Blind Critic (Isolation)"
            T2 --> CRIT_C{Risk/Conf Trigger}
            T1 --> CRIT_C
            CRIT_C -- "High Risk" --> RAG2[NV-EmbedQA-E5 Vector Search]
            RAG2 --> CRIT[Nemotron-Ultra: Critical Thinking]
        end
        
        FINAL[Final Result Assembly]
        T1 --> FINAL
        T2 --> FINAL
        CRIT --> FINAL
        
        FINAL --> QUEUE[Async DB Write Queue]
        QUEUE --> H
    end

    subgraph "Phase 4: Persistence & Recovery"
        H --> REC[Startup Task Recovery]
        REC --> I
    end

    subgraph "Vector Memory (Multi-Tenant)"
        KBA[(ChromaDB: Main Coll)]
        KBB[(ChromaDB: Critic Coll)]
        
        TAGS1[[Metadata Tags: Portfolio + Doc_ID]]
        TAGS2[[Metadata Tags: Portfolio + Doc_ID]]
        
        KBA --- TAGS1
        KBB --- TAGS2
    end

    TAGS1 -.-> RAG1
    TAGS2 -.-> RAG2
```

## ⚖️ Deployment Reality (Local Optimization Strategy)

We have deliberately chosen a **"Pure Python"** stack to ensure 100% portability.

### 1. Multi-Tenant Knowledge Portfolios
*   **The Problem**: Mixing data from different industries (e.g. AC vs Laptops) causes RAG hallucinations.
*   **The Fix**: A tiered folder structure (`global/` vs `portfolios/`) and logical `$or` metadata filters in ChromaDB. The AI always pulls Global company truths + ONLY the selected industry's context.

### 2. Project-Aware Chat (RFP Indexing)
*   **The Problem**: Chatbot previously only saw company documents, ignoring the uploaded RFP PDF.
*   **The Fix**: Phase 1 Ingestion now automatically indexes every RFP page into the Vector Store. The Chat engine performs a 3-way join: `Global + Active Portfolio + Current RFP`.

### 3. Scaling: SQLite WAL + Async Queue
*   **The Problem**: SQLite normally locks on concurrent writes.
*   **The Fix**: We use **WAL (Write-Ahead Logging)** mode for better concurrency and a single **Async DB Queue** in the FastAPI process. All background tasks push results to this queue; a dedicated consumer thread writes them to SQLite one by one. This eliminates "Database is Locked" errors while maintaining zero external dependencies.

### 4. Reliability: SQLite Task Recovery
*   **The Problem**: Background tasks disappear if the server crashes.
*   **The Fix**: We store a `task_queue` table in SQLite. On server startup, the system automatically identifies "In Progress" tasks and restarts them.

### 5. Data Integrity: Page Stitching
*   **The Problem**: Requirements split across pages are often lost.
*   **The Fix**: Phase 1 now includes a **Page Stitching Buffer** that overlaps page boundaries (200 tokens) to ensure the LLM sees the end of one page and the start of the next as a single context.

### 4. Security: Dashboard Passkey
*   **The Problem**: UI is open to anyone on the network.
*   **The Fix**: A simple environment-variable-backed **Passkey Gate** is implemented in Streamlit.
