# Master System Integration QA Plan (Phases 21–24)

This test protocol is designed to aggressively stress-test the 4 major architectural upgrades we just deployed. We will use an "AC Maintenance" evaluation to prove the system's structural integrity.

## Stage 1: The "Clean Slate" Test (Verifying the SQL Patch)
**Objective:** Ensure the Factory Reset bypasses Windows File Locks and safely wipes memory.
1. Make sure your Uvicorn backend is running.
2. Click the **"🔴 Factory Reset App (Wipe Data)"** button in the sidebar.
3. **Pass Condition:** The screen refreshes, you see a green success message, the Document Library is empty, and the backend terminal does *not* throw a `WinError 32` Permission Crash.

## Stage 2: The Multi-Tenant Test (Phase 21)
**Objective:** Ensure the AI only reads knowledge from the selected portfolio.
1. Select `AC_Maintenance` from the **Active Portfolio Selection** dropdown.
2. Click **"📚 Initialise Knowledge Base"**. 
3. **Pass Condition:** The green banner explicitly says `✅ Company KB Initialised! [X chunks]`. 
4. Switch the dropdown to a non-existent portfolio (e.g., if you had `Laptops`). The RAG engine should refuse to answer AC-specific questions.

## Stage 3: The "Project-Aware" Ingestion Stress Test (Phases 22 & 24)
**Objective:** Verify that the Tender is indexed for Chat *and* the JSON-Resilience catches token cut-offs.
1. Ensure the dropdown is set to `AC_Maintenance`.
2. Upload the **AC Maintenance RFP PDF**.
3. Click **🚀 Start AI Analysis**.
4. Monitor the Uvicorn terminal.
5. **Pass Condition:** 
   - You see "Extraction started | Model: [gpt-oss-120b]"
   - You see "Indexing [X] RFP pages into Vector Store."
   - Even if the page is incredibly complex, the system pushes through to "Extraction Complete" without throwing an EOF Pydantic ValidationError. (If it truncates, you should see `🎉 Successfully recovered requirements via truncation!` in the terminal).

## Stage 4: The 120B Deep-Reasoning Audit (Phase 23)
**Objective:** Prove the Chat Engine can synthesize facts from BOTH the uploaded RFP and the Company KB with explicit citations.
1. Wait for the RFP to finish ingesting.
2. Select the **Chat (RAG)** tab. Check that the Status Bar says: `Global KB: Active ✅ | Portfolio: AC_Maintenance ✅ | Current RFP: Active ✅`.
3. Ask the prompt: **"Tell me about this tender and state exactly how our company qualifies to bid on it."**
4. **Pass Condition:** 
   - The AI responds using the new `gpt-oss-120b` Reasoning Model.
   - The response is highly structured.
   - It explicitly pulls details *about the tender* from the RFP (e.g., location, required SLA).
   - It explicitly proves *compliance* by quoting the Company KB (e.g., ISO certifications, BSNL case studies).
   - It provides bracketed source citations at the bottom (e.g., `[Company_Profile.md]`, `[AC_Maintenance_RFP.pdf (Page 3)]`).

## Stage 5: The Sync-Deletion Test
**Objective:** Ensure no 'Ghost Data' remains after a project is deleted.
1. Go to the Document Library and click **"🗑️ Delete Analysis"**.
2. Go back to the Chat Tab and ask: "What was the location of the tender?"
3. **Pass Condition:** The AI responds gracefully that it does not know or cannot find the information in the current context, proving the Vector Store successfully wiped the project tags.
