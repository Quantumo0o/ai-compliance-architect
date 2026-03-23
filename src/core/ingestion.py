import os
import base64
import requests
from typing import Optional
import pdfplumber
from pdf2image import convert_from_path
from src.utils.logger import logger
from src.utils.resilience import retry_with_backoff
from src.utils.db_handler import DBHandler
import config


class IngestionManager:
    def __init__(self):
        self.db = DBHandler()
        self.upload_dir = config.UPLOAD_DIR
        self.cache_dir = config.CACHE_DIR
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(self.cache_dir, exist_ok=True)

    def process_pdf(self, pdf_path: str, doc_id: int):
        """
        Main entry point. Extracts text page by page with OCR fallback.
        """
        from src.core.vector_store import VectorStoreManager
        vsm = VectorStoreManager()
        
        # Prevent duplicates: Delete any existing chunks for this doc_id if re-processing
        vsm.delete_by_doc_id(doc_id)

        try:
            with pdfplumber.open(pdf_path) as pdf:
                total = len(pdf.pages)
                all_page_docs = []
                for i, page in enumerate(pdf.pages):
                    page_number = i + 1
                    logger.info(f"Processing page {page_number}/{total}")

                    # 1. Try digital extraction
                    text = page.extract_text()
                    is_ocr = False

                    # 2. Low-text heuristic -> trigger OCR
                    if not text or len(text.strip()) < 200:
                        logger.warning(
                            f"Page {page_number}: low text density ({len(text or '')} chars). "
                            "Triggering Nemotron-OCR."
                        )
                        text = self._ocr_page(pdf_path, page_number)
                        is_ocr = True

                    # 3. Persist to SQLite DB
                    page_id = self.db.add_page(doc_id, page_number)
                    self.db.update_page_content(
                        page_id=page_id,
                        raw_text=text,
                        markdown_text=text,
                        is_ocr=is_ocr,
                        status='ocr_done'
                    )

                    # 4. Prepare for Vector Store
                    if text.strip():
                        all_page_docs.append({
                            "text": text.strip(),
                            "metadata": {
                                "doc_id": str(doc_id),
                                "source_type": "rfp",
                                "source": f"{os.path.basename(pdf_path)} (Page {page_number})",
                                "portfolio": "internal_rfp"
                            }
                        })

                # Batch add to Vector Store
                if all_page_docs:
                    logger.info(f"Indexing {len(all_page_docs)} RFP pages into Vector Store.")
                    vsm.add_documents(all_page_docs)

            self.db.update_document_status(doc_id, 'ingested')
            logger.info(f"Finished ingesting: {pdf_path}")

        except Exception as e:
            logger.error(f"Error processing PDF {pdf_path}: {e}")
            self.db.update_document_status(doc_id, 'failed')
            raise

    @retry_with_backoff(retries=3)
    def _ocr_page(self, pdf_path: str, page_number: int) -> str:
        """
        Converts a PDF page to a PNG image and calls NVIDIA Nemotron-OCR.
        Returns the extracted text string.
        """
        poppler_dir = r"C:\poppler\poppler-25.12.0\Library\bin"
        pp = poppler_dir if os.path.exists(poppler_dir) else None
        
        images = convert_from_path(pdf_path, first_page=page_number, last_page=page_number, dpi=120, poppler_path=pp)
        if not images:
            raise ValueError(f"Could not convert page {page_number} to image.")

        img = images[0]
        # Resize image to prevent massive base64 payloads
        img.thumbnail((1000, 1000))
        img_path = os.path.join(self.cache_dir, f"page_{page_number}.jpg")
        img.save(img_path, "JPEG", quality=75, optimize=True)

        with open(img_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()

        if len(image_b64) > 180_000:
            logger.warning(
                f"Page {page_number}: base64 image is {len(image_b64)} bytes. "
                "Exceeds 180 KB limit for inline base64; NVIDIA may reject. "
                "Asset upload is needed for production."
            )

        headers = {
            "Authorization": f"Bearer {config.NVIDIA_NEMOTRON_OCR_KEY}",
            "Accept": "application/json"
        }
        payload = {
            "input": [
                {
                    "type": "image_url",
                    "url": f"data:image/jpeg;base64,{image_b64}"
                }
            ]
        }

        logger.info(f"Page {page_number}: Image to Text OCR | Model: [nvidia/nemotron-ocr-v1]")
        response = requests.post(config.NEMOTRON_OCR_URL, headers=headers, json=payload)
        response.raise_for_status()

        result = response.json()

        # Parse safely and concatenate detected text pieces
        try:
            data_obj = result["data"][0]
            if "text_detections" in data_obj:
                pieces = []
                for det in data_obj["text_detections"]:
                    pred = det.get("text_prediction", {})
                    text_val = pred.get("text", "")
                    if text_val:
                        pieces.append(text_val)
                extracted = "\n".join(pieces)
            elif "text" in data_obj:
                extracted = data_obj["text"]
            else:
                raise KeyError("No 'text_detections' or 'text' field.")
        except (KeyError, IndexError, TypeError):
            # Log the actual structure and return empty string so the pipeline continues
            logger.error(f"Unexpected Nemotron-OCR response structure: {result}")
            extracted = ""

        return extracted

    def save_uploaded_file(self, file_content: bytes, filename: str) -> str:
        file_path = os.path.join(self.upload_dir, filename)
        with open(file_path, "wb") as f:
            f.write(file_content)
        return file_path
