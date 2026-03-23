import sqlite3
import os
from typing import List, Optional
from src.utils.logger import logger
import config

class DBHandler:
    def __init__(self, db_path: str = config.DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initializes the database schema and enables WAL mode for concurrency."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            # Enable WAL mode for better async concurrency
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            
            with open(config.DB_SCHEMA_PATH, 'r') as f:
                conn.executescript(f.read())
            conn.commit()
        self._migrate_db()

    def _migrate_db(self):
        """Idempotently add any missing columns to existing tables."""
        migrations = [
            ("adjudications", "needs_review",        "INTEGER DEFAULT 0"),
            ("adjudications", "critic_verdict",       "TEXT"),
            ("adjudications", "evidence_confidence",  "FLOAT"),
            ("requirements",  "req_hash",             "TEXT"),
        ]
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Ensure adjudication_cache table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS adjudication_cache (
                    cache_key TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for table, column, coltype in migrations:
                cursor.execute(f"PRAGMA table_info({table})")
                existing_cols = [row[1] for row in cursor.fetchall()]
                if column not in existing_cols:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                    logger.info(f"Migration: added column '{column}' to '{table}'")
            conn.commit()

    def add_document(self, filename: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO documents (filename) VALUES (?)", (filename,))
            conn.commit()
            return cursor.lastrowid

    def update_document_status(self, doc_id: int, status: str):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE documents SET status = ? WHERE id = ?", (status, doc_id))
            conn.commit()

    def add_page(self, doc_id: int, page_number: int, status: str = 'pending') -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO pages (doc_id, page_number, status) VALUES (?, ?, ?)",
                (doc_id, page_number, status)
            )
            conn.commit()
            return cursor.lastrowid

    def update_page_content(self, page_id: int, raw_text: str = None, markdown_text: str = None, is_ocr: bool = False, status: str = 'ocr_done'):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE pages 
                SET raw_text = ?, markdown_text = ?, is_ocr = ?, status = ?
                WHERE id = ?
            """, (raw_text, markdown_text, 1 if is_ocr else 0, status, page_id))
            conn.commit()

    def has_requirements(self, doc_id: int) -> bool:
        """Returns True if this doc already has extracted (locked) requirements."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM requirements WHERE doc_id = ?", (doc_id,))
            return cursor.fetchone()[0] > 0

    def add_requirement(self, doc_id: int, page_id: int, req_data: dict) -> int:
        """Insert requirement only if req_hash not already present for this doc (deduplication)."""
        req_hash = req_data.get("req_hash")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Dedup check: skip if identical requirement already stored for this doc
            if req_hash:
                cursor.execute(
                    "SELECT id FROM requirements WHERE doc_id = ? AND req_hash = ?",
                    (doc_id, req_hash)
                )
                if cursor.fetchone():
                    logger.debug(f"Skipping duplicate requirement (hash={req_hash[:8]}…)")
                    return -1   # Signal: skipped as duplicate
            cursor.execute("""
                INSERT INTO requirements (doc_id, page_id, req_id, req_text, section_id, section_title, obligation_level, category, req_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_id,
                page_id,
                req_data.get("req_id"),
                req_data.get("text"),
                req_data.get("section_id"),
                req_data.get("section_title"),
                req_data.get("obligation_level"),
                req_data.get("category"),
                req_hash
            ))
            conn.commit()
            return cursor.lastrowid

    def add_adjudication(self, req_db_id: int, adj_data: dict):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO adjudications
                    (req_id, compliance_status, confidence_score, evidence_confidence,
                     evidence_summary, source_document, exact_quote, gap_analysis, needs_review, critic_verdict)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                req_db_id,
                adj_data.get("compliance_status"),
                adj_data.get("confidence_score"),
                adj_data.get("evidence_confidence"),
                adj_data.get("evidence_summary"),
                adj_data.get("source_document"),
                adj_data.get("exact_quote"),
                adj_data.get("gap_analysis"),
                1 if adj_data.get("needs_review") else 0,
                adj_data.get("critic_verdict")
            ))
            cursor.execute("UPDATE requirements SET status = 'adjudicated' WHERE id = ?", (req_db_id,))
            conn.commit()

    def get_adjudication_cache(self, cache_key: str):
        """Returns cached result_json string or None if not found."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT result_json FROM adjudication_cache WHERE cache_key = ?", (cache_key,))
            row = cursor.fetchone()
            return row[0] if row else None

    def set_adjudication_cache(self, cache_key: str, result_json: str):
        """Stores an adjudication result in the cache table."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO adjudication_cache (cache_key, result_json) VALUES (?, ?)",
                (cache_key, result_json)
            )
            conn.commit()

    def update_adjudication(self, req_db_id: int, adj_data: dict):
        """Human write-back: update an existing adjudication row with corrected values."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE adjudications
                SET compliance_status = ?,
                    confidence_score  = ?,
                    evidence_summary  = ?,
                    gap_analysis      = ?,
                    needs_review      = 0
                WHERE req_id = (
                    SELECT id FROM requirements WHERE req_id = ? AND doc_id = (
                        SELECT MAX(id) FROM documents WHERE filename = ?
                    )
                )
            """, (
                adj_data.get("compliance_status"),
                adj_data.get("confidence_score"),
                adj_data.get("evidence_summary"),
                adj_data.get("gap_analysis"),
                adj_data.get("req_id"),
                adj_data.get("filename")
            ))
            conn.commit()
            return cursor.rowcount

    def get_requirements_for_doc(self, doc_id: int) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM requirements WHERE doc_id = ?", (doc_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_completed_documents(self) -> list:
        """Fetches a list of all successful document analyses."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id, filename, upload_date FROM documents WHERE status = 'completed' ORDER BY id DESC")
            return [dict(row) for row in cursor.fetchall()]

    def delete_document(self, doc_id: int):
        """Manually cascades deletion of a document since schema doesn't strictly enforce ON DELETE CASCADE."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 1. Delete Adjudications (linked to requirements)
            cursor.execute("DELETE FROM adjudications WHERE req_id IN (SELECT id FROM requirements WHERE doc_id = ?)", (doc_id,))
            # 2. Delete Requirements
            cursor.execute("DELETE FROM requirements WHERE doc_id = ?", (doc_id,))
            # 3. Delete Pages
            cursor.execute("DELETE FROM pages WHERE doc_id = ?", (doc_id,))
            # 4. Delete the Document itself
            cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.commit()

    def get_adjudication_progress(self, doc_id: int) -> dict:
        """Returns the number of completed and total requirements for a document."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM requirements WHERE doc_id = ?", (doc_id,))
            total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM requirements WHERE doc_id = ? AND status = 'adjudicated'", (doc_id,))
            completed = cursor.fetchone()[0]
            
            # Get document status
            cursor.execute("SELECT status FROM documents WHERE id = ?", (doc_id,))
            doc_status_row = cursor.fetchone()
            doc_status = doc_status_row[0] if doc_status_row else "unknown"
            
            return {"total": total, "completed": completed, "status": doc_status}

    # --- TASK QUEUE METHODS ---
    def upsert_task(self, doc_id: int, status: str):
        """Adds or updates a task in the persistence queue."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO task_queue (doc_id, status, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(doc_id) DO UPDATE SET 
                    status=excluded.status, 
                    updated_at=excluded.updated_at
            """, (doc_id, status))
            conn.commit()

    def get_incomplete_tasks(self) -> List[int]:
        """Returns a list of doc_ids that were interrupted (status='processing')."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT doc_id FROM task_queue WHERE status = 'processing'")
            return [row["doc_id"] for row in cursor.fetchall()]

    def delete_task(self, doc_id: int):
        """Removes a task from the persistence queue."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM task_queue WHERE doc_id = ?", (doc_id,))
            conn.commit()

