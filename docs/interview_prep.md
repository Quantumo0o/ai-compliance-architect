# Technical Interview Preparation: AI Compliance Matrix Architect

This document outlines the high-level engineering decisions and "impressive" features an interviewer (technical lead or architect) is likely to challenge you on.

---

## 🏗️ 1. System Architecture & Scalability
**Possible Question**: *"Why did you choose an asynchronous architecture with a task queue instead of a direct REST API flow?"*

**Winning Answer**:
*   **Decoupling**: Compliance analysis is time-consuming (Extraction + Adjudication can take 30-60 seconds). A direct REST API would time out. 
*   **Persistence**: By using an **Async Task Queue** backed by SQLite, the system can recover from a crash. If the server restarts, it checks the `task_queue` table and resumes unfinished work automatically.
*   **User Experience**: The UI remains responsive. The user sees a progress bar instead of a frozen screen.

---

## 🔍 2. RAG & Vector Excellence
**Possible Question**: *"How did you handle the 'Lost in the Middle' or 'Semantic Drowning' problem where company context overpowers the tender context?"*

**Winning Answer**:
*   **Domain-Segregated Reranking**: We found that "shiny" company profiles (full of marketing keywords) would mathematically "win" over the "dry" legal text of an RFP during search.
*   **Fix**: I implemented a **Hard-Balanced Search Engine**. It fetches and reranks Company Profile and Tender RFP in two isolated pools and forces a strict **50/50 context split** (5 facts from each) before handing it to the Chatbot. This guarantees the LLM never goes "blind" to the tender requirements.

---

## 🛡️ 3. Accuracy & Hallucination Control
**Possible Question**: *"AI can hallucinate compliance. How did you ensure the 'Fully Compliant' score is actually trustworthy?"*

**Winning Answer**:
*   **Double-Blind Adjudication**: We don't just ask one AI. We have a **Critic Agent** strategy.
*   **Isolation**: The Critic Agent (Llama 3.1 Nemotron) performs its own **independent vector search** using a different embedding model (`nv-embedqa-e5-v5`). 
*   **Conflict Detection**: If the Critic disagrees with the Primary Judge, the system flags it as "Needs Review." This cross-verification significantly reduces false positives in compliance report generation.

---

## 🛠️ 4. Handling Messy Real-World Data
**Possible Question**: *"What was the hardest part about processing the PDF documents?"*

**Winning Answer**:
*   **Page-Stitching Logic**: Legal requirements often split across two pages. A standard RAG would see two broken halves. I built a **Page Stitching Buffer** that merges overlapping text between pages to ensure multi-page clauses are captured whole.
*   **OCR Heuristics**: To save costs and time, the system only triggers **NVIDIA Nemotron-OCR** when it detects "Low Text Density" (less than 200 characters), indicating a scanned image or a complex diagram.

---

## ⚡ 5. API Resilience
**Possible Question**: *"How did you handle API Rate Limits (429 errors) when adjudicating 50+ requirements at once?"*

**Winning Answer**:
*   **Exponential Backoff**: I wrapped all NVIDIA API calls in a decorator that implements **Jittered Exponential Backoff**. 
*   **Concurrency Control**: The FastAPI worker processes requirements in a controlled loop to stay within token-per-minute (TPM) limits while maintaining high throughput.

---

### 💡 Interview Tip:
If asked *"What would you improve next?"*, mention:
1.  **Multi-Modal Support**: Analyzing images/blueprints alongside text.
2.  **Long-Context Models**: Moving from 10-chunk RAG to `gpt-4o` or `gemini-1.5-pro` 2-million token context to read the *entire* RFP at once.
