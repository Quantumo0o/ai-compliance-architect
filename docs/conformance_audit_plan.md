# Architecture Conformance Audit Plan (V5 Blueprint)

This plan provides a structured checklist to verify that the current implementation follows the **[Architecture Blueprint (V5 Production)](file:///c:/Users/shubh/OneDrive/Documents/AI%20tool/ai-compliance-architect/ARCHITECTURE_BLUEPRINT.md)**.

---

## 🛠️ Verification Checklist

### 1. Scaling: SQLite Write-Ahead Logging (WAL) & Asynchronous Queue
*   [ ] **Write-Ahead Logging (WAL) Mode**: Check `src/utils/db_handler.py`.
    *   *Verification*: Look for `PRAGMA journal_mode=WAL`.
*   [ ] **Asynchronous Database (DB) Write Queue**: Check `src/api/server.py`.
    *   *Verification*: Ensure `db_queue = asyncio.Queue()` exists and `db_writer_worker` is running.
*   [ ] **Queue Integration**: Check `src/core/judge.py`.
    *   *Verification*: Ensure `_queue_db_write` is used for `add_adjudication` and `set_adjudication_cache`.

### 2. Data Integrity: Page-Stitching context
*   [ ] **Context Buffer**: Check `src/core/extraction.py`.
    *   *Verification*: Ensure `self.overlap_buffer` is initialized and prepended to the user prompt in `extract_from_page`.
*   [ ] **Prompt Logic**: Check the system prompt in `extraction.py`.
    *   *Verification*: Ensure it mentions handling truncated requirements from the overlap.

### 3. Reliability: Startup Task Recovery
*   [ ] **Recovery Logic**: Check `src/api/server.py`.
    *   *Verification*: Look for `startup_event` calling `db.get_incomplete_tasks()` and re-triggering analysis.
*   [ ] **Persistence Table**: Check `db/schema.sql`.
    *   *Verification*: Ensure the `task_queue` table exists.

### 4. Search: Double-Blind Isolation (Retrieval-Augmented Generation - RAG)
*   [ ] **Isolation Search**: Check `src/core/judge.py`.
    *   *Verification*: Ensure the Critic Agent uses `for_critic=True` in `self.vector_store.search`.
*   [ ] **Critic Model**: Check `config.py`.
    *   *Verification*: Ensure `CRITIC_EMBEDDING_MODEL` is set to `nvidia/nv-embedqa-e5-v5`.

### 5. Security: Passkey Gate
*   [ ] **User Interface (UI) Barrier**: Check `app.py`.
    *   *Verification*: Look for the `check_password()` function and `st.stop()` call before the main dashboard title.

---

## 🔍 How to Run the Audit
1.  **Static Scan**: View each file listed above to confirm the logic is present.
2.  **Concurrency Test**: Start the server and upload a large Portable Document Format (PDF) Request for Proposal (RFP). Watch the logs for "Database write worker started" and verify no "Locked" errors occur.
3.  **Crash Test**: Kill the server terminal mid-analysis, wait 5 seconds, restart it, and verify the analysis resumes automatically.
4.  **Security Test**: Open the UI in a private browser and verify you are challenged for a passkey.
