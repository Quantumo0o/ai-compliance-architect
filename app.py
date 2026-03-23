import streamlit as st
import pandas as pd
import sqlite3
import os
import asyncio
import httpx
import requests
from src.core.ingestion import IngestionManager
from src.core.extraction import ExtractionManager
from src.core.judge import AdjudicationManager
from src.core.vector_store import VectorStoreManager
from src.utils.db_handler import DBHandler
import config

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Compliance Matrix Architect",
    page_icon="🤖",
    layout="wide"
)

# ── Premium dark-theme CSS ─────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* Base styling */
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, sans-serif;
}
[data-testid="stAppViewContainer"] { 
    background: radial-gradient(circle at 10% 20%, #0d1117 0%, #030408 100%);
    color: #e6edf3;
}
[data-testid="stSidebar"] { 
    background-color: #0d1117; 
    border-right: 1px solid #30363d;
}
h1, h2, h3, h4, h5, h6 { 
    color: #ffffff !important; 
    font-weight: 700 !important;
    letter-spacing: -0.02em;
}
p, span, label, div {
    color: #c9d1d9;
}

/* Buttons */
.stButton>button {
    background: linear-gradient(135deg, #238636 0%, #194824 100%) !important;
    color: #ffffff !important; 
    border-radius: 8px !important; 
    border: 1px solid rgba(255,255,255,0.1) !important;
    font-weight: 600 !important; 
    padding: 0.6rem 1.4rem !important;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    transition: all 0.25s ease;
}
.stButton>button:hover { 
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(0,0,0,0.5);
    background: linear-gradient(135deg, #2ea043 0%, #238636 100%) !important;
    border-color: rgba(255,255,255,0.2) !important;
}

/* File Uploader Dropzone */
[data-testid="stFileUploadDropzone"] {
    border: 2px dashed #30363d !important;
    background-color: rgba(22, 27, 34, 0.4) !important;
    border-radius: 12px !important;
    transition: all 0.3s ease;
}
[data-testid="stFileUploadDropzone"]:hover {
    border-color: #58a6ff !important;
    background-color: rgba(88, 166, 255, 0.05) !important;
}

/* Dataframe and Chat */
.stDataFrame { 
    border-radius: 10px; 
    border: 1px solid #30363d;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
}
[data-testid="stChatMessage"] {
    background-color: rgba(22, 27, 34, 0.6);
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 0.5rem 1rem;
    margin-bottom: 1rem;
    box-shadow: 0 4px 12px rgba(0,0,0,0.2);
}
[data-testid="stChatInput"] {
    border-radius: 12px;
    border: 1px solid #30363d;
    background-color: #161b22;
}

/* Dividers & Elements */
hr {
    border-color: #30363d !important;
    margin: 2rem 0;
}
[data-testid="stStatusWidget"] {
    background-color: rgba(22, 27, 34, 0.5) !important;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 1rem;
}
</style>
""", unsafe_allow_html=True)

# ── Security Gate ─────────────────────────────────────────────────────────────
def check_password():
    """Returns True if the user had the correct password."""
    if "password_correct" not in st.session_state:
        st.subheader("🔐 Restricted Access")
        st.info("Please enter the application passkey to unlock the dashboard.")
        
        # Use a form to handle 'Enter' key submission smoothly
        with st.form("login_form"):
            password = st.text_input("Passkey", type="password")
            submit = st.form_submit_button("Unlock Dashboard")
            
            if submit:
                if password == config.APP_PASSKEY:
                    st.session_state["password_correct"] = True
                    st.rerun()
                else:
                    st.error("🚫 Incorrect passkey.")
        return False
    return True

if not check_password():
    st.stop()

# ── Title ──────────────────────────────────────────────────────────────────────
st.title("🤖 AI Compliance Matrix Architect")
st.markdown("**RAG-powered RFP analysis · Mistral-Large-3 · NVIDIA Nemotron-OCR · BGE-M3**")
st.divider()

# ── Sidebar: Knowledge Base setup ─────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Setup")
    st.caption("Place your company PDFs in `data/knowledge_base/` then click below.")

    if st.button("🔴 Factory Reset App (Wipe Data)"):
        with st.spinner("Wiping SQLite and Vector Databases (gracefully)..."):
            # 1. Wipe SQLite tables instead of deleting the locked file
            import sqlite3
            if os.path.exists(config.DB_PATH):
                with sqlite3.connect(config.DB_PATH) as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM requirements")
                    cursor.execute("DELETE FROM adjudications")
                    cursor.execute("DELETE FROM pages")
                    cursor.execute("DELETE FROM documents")
                    cursor.execute("DELETE FROM adjudication_cache")
                    conn.commit()
            
            # 2. Wipe ChromaDB via API instead of locked folder deletion
            from src.core.vector_store import VectorStoreManager
            try:
                vsm = VectorStoreManager()
                if vsm.client:
                    vsm.client.delete_collection("compliance_db")
                    vsm.client.delete_collection("critic_db")
            except Exception as e:
                pass # Ignore if collections don't exist yet

            st.success("App reset to factory defaults! Please refresh the page.")
            st.stop()

    if st.button("📚 Initialise Knowledge Base"):
        docs = []
        global_dir = os.path.join(config.KNOWLEDGE_BASE_DIR, "global")
        portfolios_dir = os.path.join(config.KNOWLEDGE_BASE_DIR, "portfolios")
        
        def read_folder(folder_path, portfolio_tag):
            if not os.path.exists(folder_path): return
            for fname in os.listdir(folder_path):
                if not fname.endswith((".txt", ".md", ".pdf")): continue
                path = os.path.join(folder_path, fname)
                if fname.endswith(".pdf"):
                    import pdfplumber
                    with pdfplumber.open(path) as pdf:
                        for page_num, p in enumerate(pdf.pages, start=1):
                            page_text = p.extract_text() or ""
                            if page_text.strip():
                                docs.append({
                                    "text": page_text.strip(),
                                    "metadata": {"source": f"{fname} (Page {page_num})", "portfolio": portfolio_tag}
                                })
                else:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        text = fh.read()
                    if text.strip():
                        docs.append({"text": text.strip(), "metadata": {"source": f"{fname}", "portfolio": portfolio_tag}})

        read_folder(global_dir, "global")
        if os.path.exists(portfolios_dir):
            for port_folder in os.listdir(portfolios_dir):
                port_path = os.path.join(portfolios_dir, port_folder)
                if os.path.isdir(port_path):
                    read_folder(port_path, port_folder)

        if not docs:
            st.warning("No .txt / .md / .pdf files found in global/ or portfolios/ directories.")
        else:
            from src.core.vector_store import VectorStoreManager
            vsm = VectorStoreManager()
            with st.spinner("Embedding documents…"):
                vsm.add_documents(docs)
            st.success(f"✅ Company KB Initialised! ({len(docs)} document chunks securely embedded in database.)")

    st.divider()
    
    st.header("📂 Document Library")
    
    db = DBHandler()
    completed_docs = db.get_completed_documents()
    
    if completed_docs:
        doc_options = {d["id"]: f"{d['filename']} ({d['upload_date'][:10]})" for d in completed_docs}
        
        idx = 0
        current_sel = st.session_state.get("selected_doc_id")
        if current_sel in doc_options:
            idx = list(doc_options.keys()).index(current_sel)
            
        selected_doc_id = st.selectbox(
            "View past analysis:", 
            options=list(doc_options.keys()), 
            format_func=lambda x: doc_options[x],
            index=idx
        )
        st.caption("ℹ️ **Active Project**: This is the *CLIENT'S* specific Tender or RFP. The AI evaluates this document against your Active Portfolio.")
        st.session_state.selected_doc_id = selected_doc_id

        if st.button("🗑️ Delete Analysis", type="secondary"):
            from src.core.vector_store import VectorStoreManager
            vsm = VectorStoreManager()
            vsm.delete_by_doc_id(selected_doc_id)
            db.delete_document(selected_doc_id)
            if st.session_state.get("selected_doc_id") == selected_doc_id:
                st.session_state.selected_doc_id = None
            st.rerun()
    else:
        st.caption("No completed analyses yet.")

    st.divider()
    st.caption("API endpoints configured in `.env`")

# ── Main Area ──────────────────────────────────────────────────────────────────
st.subheader("🏢 Active Portfolio Selection")
portfolios_dir = os.path.join(config.KNOWLEDGE_BASE_DIR, "portfolios")
available_portfolios = [d for d in os.listdir(portfolios_dir) if os.path.isdir(os.path.join(portfolios_dir, d))] if os.path.exists(portfolios_dir) else []

if available_portfolios:
    st.session_state.active_portfolio = st.selectbox(
        "Select which line of business to use for evidence retrieval:", 
        options=available_portfolios
    )
    st.caption("ℹ️ **Active Portfolio**: This is *YOUR* company's Knowledge Base (e.g., your case studies, ISO certs). The AI uses this to prove you are compliant.")
else:
    st.session_state.active_portfolio = None
    st.info("No portfolios found in `data/knowledge_base/portfolios/`.")

st.divider()

uploaded_file = st.file_uploader("📄 Upload RFP PDF", type=["pdf"])

if uploaded_file:
    db = DBHandler()

    if st.button("🚀 Start AI Analysis"):
        with sqlite3.connect(config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM documents WHERE filename = ? AND status = 'completed'", (uploaded_file.name,))
            row = cursor.fetchone()
            is_duplicate = row is not None

        if is_duplicate:
            st.session_state.selected_doc_id = row[0]
            st.warning(f"Document '{uploaded_file.name}' has already been analyzed! Scroll down to see the matrix.")
        else:
            # Save uploaded file
            ingestion = IngestionManager()
            file_path = ingestion.save_uploaded_file(uploaded_file.read(), uploaded_file.name)
            doc_id = db.add_document(uploaded_file.name)

            # ── Phase 1: Ingest ────────────────────────────────────────────────────
            with st.status("📥 Phase 1 — Ingestion & OCR…", expanded=True) as status:
                ingestion.process_pdf(file_path, doc_id)
                status.update(label="✅ Ingestion complete!", state="complete")

            # ── Phase 2: Extract ───────────────────────────────────────────────────
            with st.status("🔍 Phase 2 — Extracting requirements (Mistral-Large)…", expanded=True) as status:
                if db.has_requirements(doc_id):
                    st.info("Requirements already extracted for this document. Using locked requirements.")
                    status.update(label="✅ Extraction complete (locked)!", state="complete")
                else:
                    with sqlite3.connect(config.DB_PATH) as conn:
                        conn.row_factory = sqlite3.Row
                        pages = conn.execute(
                            "SELECT * FROM pages WHERE doc_id = ?", (doc_id,)
                        ).fetchall()

                    pb = st.progress(0, text="Extracting…")
                    extraction = ExtractionManager()
                    for i, page in enumerate(pages):
                        extraction.extract_from_page(page["id"], page["markdown_text"], page["page_number"])
                        pb.progress((i + 1) / len(pages), text=f"Page {page['page_number']}/{len(pages)}")
                    status.update(label="✅ Extraction complete!", state="complete")

            # ── KB Coverage Check ──────────────────────────────────────────────────
            with sqlite3.connect(config.DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                reqs = conn.execute(
                    "SELECT * FROM requirements WHERE doc_id = ?", (doc_id,)
                ).fetchall()

            categories = {req["category"] for req in reqs if req["category"]}
            vsm = VectorStoreManager()
            for cat in categories:
                # broad semantic check for the category name itself
                results = vsm.search(cat, top_k=1)
                if not results:
                    st.warning(f"⚠️ **Knowledge Base Warning**: No documents found matching category '{cat}'. Requirements in this category will likely fail.")

            # ── Phase 3: Adjudicate ────────────────────────────────────────────────
            with st.status("⚖️ Phase 3 — RAG Adjudication (Background Tasks)…", expanded=True) as status:

                pb2 = st.progress(0, text="Initializing async backend…")
                
                # 1. Trigger the FastAPI backend
                import time
                try:
                    params = {"portfolio": st.session_state.get("active_portfolio")} if st.session_state.get("active_portfolio") else {}
                    res = requests.post(f"http://localhost:8000/analyze/{doc_id}", params=params)
                    if res.status_code == 200:
                        st.toast("Started background async adjudication!")
                    else:
                        st.toast(f"API replied: {res.text}")
                except requests.exceptions.ConnectionError:
                    st.error("Cannot connect to backend API! Please run `uvicorn src.api.server:app --port 8000`.")
                    st.stop()
                
                # 2. Poll progress
                completed = 0
                total = len(reqs)
                while True:
                    try:
                        poll_res = requests.get(f"http://localhost:8000/status/{doc_id}")
                        if poll_res.status_code == 200:
                            data = poll_res.json()
                            completed = data.get("completed", 0)
                            total = data.get("total", len(reqs))
                            state = data.get("status", "unknown")
                            
                            if total > 0:
                                pb2.progress(completed / total, text=f"Requirement {completed}/{total} - {data.get('progress', 0.0)}%")
                            
                            if state == "completed":
                                break
                            elif state == "failed":
                                st.error("Adjudication failed on the backend.")
                                break
                    except Exception as e:
                        st.warning(f"Polling error: {e}")
                    
                    time.sleep(2.0)
                        
                status.update(label="✅ Adjudication complete!", state="complete")

            db.update_document_status(doc_id, "completed")
            st.session_state.selected_doc_id = doc_id
            st.balloons()
            st.success("🎉 Compliance Matrix Generated!")

# ── Results Table ──────────────────────────────────────────────────────────
if uploaded_file or st.session_state.get("selected_doc_id"):
    st.divider()
    st.header("📊 Compliance Matrix")

    doc_filter_sql = ""
    doc_filter_params = ()
    filename_for_export = "analysis"

    if uploaded_file:
        doc_filter_sql = "SELECT MAX(id) FROM documents WHERE filename = ?"
        doc_filter_params = (uploaded_file.name,)
        filename_for_export = uploaded_file.name
    elif st.session_state.get("selected_doc_id"):
        doc_filter_sql = "?"
        doc_filter_params = (st.session_state.selected_doc_id,)
        with sqlite3.connect(config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT filename FROM documents WHERE id = ?", (st.session_state.selected_doc_id,))
            res = cursor.fetchone()
            if res:
                filename_for_export = res[0]

    with sqlite3.connect(config.DB_PATH) as conn:
        df = pd.read_sql_query(f"""
            SELECT
                r.req_id         AS "Req ID",
                r.section_id     AS "Section",
                r.category       AS "Category",
                r.req_text       AS "Requirement",
                a.compliance_status  AS "Status",
                a.confidence_score   AS "Decision Conf",
                a.evidence_confidence AS "Evidence Match",
                a.evidence_summary   AS "Evidence Summary",
                a.source_document    AS "Source",
                a.gap_analysis       AS "Gap Analysis",
                CASE a.needs_review WHEN 1 THEN '⚠️ Yes' ELSE '✅ No' END AS "Review?",
                a.critic_verdict AS "Critic Notes"
            FROM requirements r
            LEFT JOIN adjudications a ON r.id = a.req_id
            WHERE r.doc_id = ({doc_filter_sql})
        """, conn, params=doc_filter_params)

    if not df.empty:
        # ── 1. Dashboard Metrics ──
        st.subheader("Executive Summary")
        m1, m2, m3, m4 = st.columns(4)
        compliant_pct = (df["Status"] == "Fully Compliant").mean() * 100
        avg_conf = df["Decision Conf"].mean()
        conflicts = (df["Review?"] == "⚠️ Yes").sum()

        m1.metric("Total Requirements", len(df))
        m2.metric("Fully Compliant", f"{compliant_pct:.1f}%")
        m3.metric("Avg AI Confidence", f"{avg_conf:.2f}")
        m4.metric("⚠️ Conflicts", int(conflicts))

        # ── 2. Filters ──
        st.subheader("🔍 Filter & Analyze")
        f1, f2, f3 = st.columns(3)
        with f1:
            status_filter = st.multiselect("Filter by Status", df["Status"].dropna().unique(), default=df["Status"].dropna().unique())
        with f2:
            cat_filter = st.multiselect("Filter by Category", df["Category"].dropna().unique(), default=df["Category"].dropna().unique())
        with f3:
            show_conflicts = st.checkbox("Show conflicts only", value=False)

        filtered_df = df[df["Status"].isin(status_filter) & df["Category"].isin(cat_filter)]
        if show_conflicts:
            filtered_df = filtered_df[filtered_df["Review?"] == "⚠️ Yes"]

        # ── 3. Editable Workspace ──
        st.subheader("📋 Compliance Workspace")
        st.caption("You can directly edit Status, Evidence Summary, Gap Analysis, and Confidence. Click **Commit Changes** to save.")

        # Store original for diff-checking
        if "original_df" not in st.session_state:
            st.session_state.original_df = filtered_df.copy()

        edited_df = st.data_editor(
            filtered_df,
            width="stretch",
            column_config={
                "Req ID":           st.column_config.TextColumn(disabled=True),
                "Section":          st.column_config.TextColumn(disabled=True),
                "Category":         st.column_config.TextColumn(disabled=True),
                "Requirement":      st.column_config.TextColumn(disabled=True),
                "Source":           st.column_config.TextColumn(disabled=True),
                "Review?":          st.column_config.TextColumn(disabled=True),
                "Critic Notes":     st.column_config.TextColumn(disabled=True),
                "Status":           st.column_config.SelectboxColumn(
                    options=["Fully Compliant", "Partially Compliant", "Non-Compliant"]
                ),
                "Decision Conf":    st.column_config.NumberColumn(min_value=0.0, max_value=1.0, step=0.01, format="%.2f"),
                "Evidence Match":   st.column_config.NumberColumn(min_value=0.0, max_value=1.0, step=0.01, format="%.2f"),
                "Evidence Summary": st.column_config.TextColumn(),
                "Gap Analysis":     st.column_config.TextColumn(),
            },
            hide_index=True,
            key="compliance_editor"
        )

        # ── 4. Commit Button (SQL Write-Back) ──
        col_commit, _ = st.columns([1, 5])
        with col_commit:
            if st.button("💾 Commit Human Review Changes"):
                from src.utils.db_handler import DBHandler as _DB
                db_wb = _DB()
                saved = 0
                for _, row in edited_df.iterrows():
                    db_wb.update_adjudication(
                        req_db_id=None,  # we use req_id label + filename for lookup
                        adj_data={
                            "req_id": row["Req ID"],
                            "filename": filename_for_export,
                            "compliance_status": row["Status"],
                            "confidence_score": row["Decision Conf"],
                            "evidence_summary": row["Evidence Summary"],
                            "gap_analysis": row["Gap Analysis"],
                        }
                    )
                    saved += 1
                st.success(f"✅ {saved} row(s) committed to database!")
                st.session_state.pop("original_df", None)  # reset

        # ── 5. Download ──
        csv = edited_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download as CSV",
            csv,
            f"{filename_for_export}_compliance_matrix.csv",
            "text/csv"
        )
    else:
        st.info("No results yet. Click **Start AI Analysis** above.")

# ── Interactive Q&A ────────────────────────────────────────────────────────────
st.divider()
chat_header_col1, chat_header_col2 = st.columns([4, 1])
with chat_header_col1:
    st.header("💬 Chat with your RFP")
    st.caption("Ask any follow-up question about requirements, gaps, or evidence. Powered by gpt-oss-120b Reasoning RAG.")
with chat_header_col2:
    if st.button("🗑️ Clear Chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []

# ── Chat Header & Status ──
active_portfolio = st.session_state.get("active_portfolio")
selected_doc_id = st.session_state.get("selected_doc_id")

status_cols = st.columns([1, 1, 1])
with status_cols[0]:
    st.caption("📂 **Global KB**: Active ✅")
with status_cols[1]:
    st.caption(f"🎯 **Portfolio**: {active_portfolio or 'Not Selected ❌'}")
with status_cols[2]:
    st.caption(f"📄 **Current RFP**: {'Active ✅' if selected_doc_id else 'Not Selected ❌'}")

st.divider()

# Render history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("e.g. 'How do we prove ISO 27001 compliance?'"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Retrieving context & asking Mistral…"):
            # RAG: pull relevant evidence
            from src.core.vector_store import VectorStoreManager
            vsm = VectorStoreManager()
            context_chunks = vsm.search(
                prompt, 
                top_k=10, 
                portfolio=st.session_state.get("active_portfolio"),
                doc_id=st.session_state.get("selected_doc_id")
            )
            context_text = "\n\n".join(
                f"[{c['metadata'].get('source', 'doc')}]\n{c['text']}"
                for c in context_chunks
            ) if context_chunks else "No specific evidence retrieved."

            # 🧠 DEEP REASONING RAG PROMPT
            system_prompt = f"""You are a specialized Compliance Reasoning Engine. 
Your goal is to answer questions about a specifically uploaded Tender (RFP) and the bidder's Company Knowledge.

STRICT RAG RULES:
1. Categorize all information into "📄 TENDER FACT" or "🏢 COMPANY CAPABILITY".
2. If the answer is not in the provided context, state clearly that the information is missing.
3. Use a professional, engineering-briefing tone.
4. Always cite the [Source File] and [Page Number] provided in the context.

RAG CONTEXT:
{context_text}
"""

            # Use the high-performance Reasoning Model for Chat
            try:
                resp = requests.post(
                    config.MISTRAL_INVOKE_URL,
                    headers={"Authorization": f"Bearer {config.NVIDIA_MISTRAL_KEY}"},
                    json={
                        "model": config.EXTRACTION_MODEL, 
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.1,
                        "max_tokens": 2048
                    }
                )
                resp.raise_for_status()
                answer = resp.json()["choices"][0]["message"]["content"]
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
            except Exception as e:
                err_msg = f"⚠️ Error: {e}"
                st.error(err_msg)
                st.session_state.messages.append({"role": "assistant", "content": err_msg})
