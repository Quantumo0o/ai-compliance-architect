import sqlite3
import hashlib
import requests
from src.utils.logger import logger
from src.utils.resilience import retry_with_backoff
from src.utils.validator import RequirementList
from src.utils.db_handler import DBHandler
import config


class ExtractionManager:
    def __init__(self):
        self.db = DBHandler()
        self.api_key = config.NVIDIA_MISTRAL_KEY
        self.invoke_url = config.MISTRAL_INVOKE_URL
        self.overlap_buffer = ""  # Stores the end of the previous page

    @retry_with_backoff(retries=5)
    def extract_from_page(self, page_id: int, markdown_text: str, page_number: int) -> int:
        """
        Calls Mistral-Large to extract structured requirements from a page.
        Returns the number of requirements found.
        """
        if not markdown_text or not markdown_text.strip():
            logger.warning(f"Page {page_number} has no text content. Skipping extraction.")
            return 0

        # Page-Stitching: Prepend overlap from previous page
        context_text = f"--- PREVIOUS PAGE OVERLAP ---\n{self.overlap_buffer}\n--- CURRENT PAGE ---\n{markdown_text}" if self.overlap_buffer else markdown_text

        system_prompt = (
            "You are an expert Bid Manager and compliance analyst. "
            "Extract EVERY enforceable requirement from the RFP text. "
            "Look for: 'Shall', 'Must', 'Required To', 'Is Responsible For', 'Will Comply With'. "
            "IMPORTANT: You are provided with some OVERLAP from the previous page to ensure context. "
            "If a requirement WAS truncated on the previous page and is COMPLETED here, extract it fully. "
            "Duplicate requirements will be handled by our system; focus on COMPLETENESS. "
            "Return ONLY a valid JSON object matching this schema: "
            '{"requirements": [{"req_id": "REQ-001", "text": "...", "section_id": "3.2", '
            '"section_title": "...", "page_number": 1, "obligation_level": "Mandatory", "category": "Security"}]}'
        )

        user_prompt = f"[Target Page {page_number}]\n\n{context_text}"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        }
        payload = {
            "model": config.EXTRACTION_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 8192,
            "temperature": 0.0,
            "top_p": 0.0001,
            "response_format": {"type": "json_object"}
        }

        logger.info(f"Page {page_number}: Extraction started | Model: [{config.EXTRACTION_MODEL}]")
        response = requests.post(self.invoke_url, headers=headers, json=payload, timeout=300)
        response.raise_for_status()

        # Update overlap buffer for next page (last 1000 chars for better context-stitching)
        self.overlap_buffer = markdown_text[-1000:]

        msg = response.json()["choices"][0]["message"]
        content = msg.get("content")
        # Fallback for reasoning models that put everything in reasoning_content
        if not content:
            content = msg.get("reasoning_content", "")

        import re

        doc_id = self._get_doc_id(page_id)
        try:
            # Strip out any reasoning block if present
            if content:
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                # If wrapped in markdown json block, extract just the json
                match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, flags=re.DOTALL)
                if match:
                    content = match.group(1)

            try:
                req_list = RequirementList.model_validate_json(content)
            except Exception as parse_e:
                logger.warning(f"Page {page_number}: Pydantic validation failed ({parse_e}). Initiating Progressive Truncation Recovery...")
                # Attempt to rescue truncated JSON array
                repaired = False
                temp_content = content
                
                # Progressively slice off broken structural characters until we find the last complete requirement object
                while temp_content.rfind('}') > 0:
                    last_obj_end = temp_content.rfind('}')
                    if last_obj_end == -1: 
                        break
                    
                    # Force close the JSON array and root object
                    test_str = temp_content[:last_obj_end+1] + "\n]}"
                    
                    try:
                        req_list = RequirementList.model_validate_json(test_str)
                        logger.info(f"Page {page_number}: 🎉 Successfully recovered {len(req_list.requirements)} requirements via truncation!")
                        repaired = True
                        break
                    except Exception:
                        # Slice away the broken brace and try the next one back
                        temp_content = temp_content[:last_obj_end]
                        
                if not repaired:
                    raise parse_e # Re-raise original error if irreparably corrupted

            for req in req_list.requirements:
                req_data = req.model_dump()
                req_data["page_number"] = page_number   # Override in case LLM hallucinated
                
                # Deterministic normalization: Strip and normalize spaces before hashing
                raw_text = req_data.get("text", "").strip()
                normalized_text = re.sub(r'\s+', ' ', raw_text).lower()
                
                # Compute deterministic hash for deduplication
                req_hash = hashlib.sha256(normalized_text.encode()).hexdigest()
                req_data["req_hash"] = req_hash
                self.db.add_requirement(doc_id=doc_id, page_id=page_id, req_data=req_data)

            logger.info(f"Page {page_number}: extracted {len(req_list.requirements)} requirement(s).")
            return len(req_list.requirements)

        except Exception as e:
            raw_content = str(content)[:300] if content else str(response.json())[:300]
            logger.error(f"Page {page_number}: failed to parse/store requirements — {e}\nRaw: {raw_content}")
            raise

    def _get_doc_id(self, page_id: int) -> int:
        with sqlite3.connect(config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT doc_id FROM pages WHERE id = ?", (page_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"No page found with id={page_id}")
            return row[0]
