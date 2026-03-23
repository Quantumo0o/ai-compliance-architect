import os
import requests
import chromadb
from openai import OpenAI
from typing import List, Dict, Any
from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.utils.logger import logger
from src.utils.resilience import retry_with_backoff
import config


class VectorStoreManager:
    def __init__(self, collection_name: str = "company_capabilities"):
        self.kb_dir = config.KNOWLEDGE_BASE_DIR
        os.makedirs(self.kb_dir, exist_ok=True)

        # Initialize ChromaDB with persistent storage inside knowledge_base dir
        self.client = chromadb.PersistentClient(path=os.path.join(self.kb_dir, "chroma_db"))
        self.collection = self.client.get_or_create_collection(name=collection_name)
        self.critic_collection = self.client.get_or_create_collection(name=f"{collection_name}_critic")

        # BGE-M3 via NVIDIA OpenAI-compatible endpoint
        self.ai_client = OpenAI(
            api_key=config.NVIDIA_BGE_M3_KEY,
            base_url=config.BGE_M3_BASE_URL
        )

    @retry_with_backoff(retries=3)
    def _get_embedding(self, text: str, model_name: str = None, input_type: str = None) -> List[float]:
        """Calls NVIDIA embedding models to get a text embedding vector."""
        model = model_name or config.EMBEDDING_MODEL_NAME
        extra_body = {"truncate": "NONE"}
        if input_type:
            extra_body["input_type"] = input_type
            
        response = self.ai_client.embeddings.create(
            input=[text],
            model=model,
            encoding_format="float",
            extra_body=extra_body
        )
        return response.data[0].embedding

    @retry_with_backoff(retries=3)
    def _rerank(self, query: str, passages: List[str], top_k: int) -> List[tuple]:
        """
        Calls NVIDIA NV-RerankQA to re-score retrieved passages.
        Returns indices and scores of top_k passages.
        """
        if not passages:
            return []

        headers = {
            "Authorization": f"Bearer {config.NVIDIA_BGE_M3_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        payload = {
            "model": config.RERANKER_MODEL,
            "query": {"text": query},
            "passages": [{"text": p} for p in passages],
            "truncate": "END"
        }

        logger.info(f"Reranking candidates -> top {top_k} | Model: [{config.RERANKER_MODEL}]")
        resp = requests.post(config.RERANKER_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        rankings = resp.json().get("rankings", [])
        # Rankings are objects with 'index' and 'logit' (or 'score') fields
        sorted_rankings = sorted(rankings, key=lambda x: x.get("logit") if x.get("logit") is not None else x.get("score", 0), reverse=True)
        return [(r["index"], r.get("logit") if r.get("logit") is not None else r.get("score", 0)) for r in sorted_rankings[:top_k]]

    def add_documents(self, documents: List[Dict[str, Any]]):
        """
        Semantic-chunks and embeds a list of documents.
        Input format: [{'text': '...', 'metadata': {'source': 'filename.pdf'}}]
        """
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.RAG_CHUNK_SIZE,
            chunk_overlap=config.RAG_CHUNK_OVERLAP,
        )

        total_chunks = 0
        for doc in documents:
            text = doc.get("text", "")
            metadata = doc.get("metadata", {})
            if not text.strip():
                logger.warning(f"Skipping empty document: {metadata.get('source')}")
                continue

            chunks = text_splitter.split_text(text)

            for i, chunk in enumerate(chunks):
                chunk_id = f"{metadata.get('source', 'doc')}__chunk_{i}"

                # Agent A Embedding
                embedding_a = self._get_embedding(chunk, model_name=config.EMBEDDING_MODEL_NAME)
                self.collection.upsert(
                    ids=[chunk_id],
                    embeddings=[embedding_a],
                    documents=[chunk],
                    metadatas=[{**metadata, "chunk_index": i}]
                )

                # Agent B (Critic) Embedding
                embedding_b = self._get_embedding(chunk, model_name=config.CRITIC_EMBEDDING_MODEL, input_type="passage")
                self.critic_collection.upsert(
                    ids=[chunk_id],
                    embeddings=[embedding_b],
                    documents=[chunk],
                    metadatas=[{**metadata, "chunk_index": i}]
                )
                total_chunks += 1

        logger.info(f"Added {len(documents)} documents / {total_chunks} chunks to vector store.")

    def delete_by_doc_id(self, doc_id: int):
        """Removes all chunks associated with a specific document ID from both collections."""
        doc_id_str = str(doc_id)
        logger.info(f"Deleting vector chunks for doc_id={doc_id_str}")
        try:
            self.collection.delete(where={"doc_id": doc_id_str})
            self.critic_collection.delete(where={"doc_id": doc_id_str})
        except Exception as e:
            logger.error(f"Error deleting vector chunks: {e}")

    def search(self, query: str, portfolio: str = None, doc_id: int = None, top_k: int = None, for_critic: bool = False) -> List[Dict[str, Any]]:
        """
        Two-stage semantic search:
        1. Fetch RAG_FETCH_K=20 candidates using BGE-M3 embedding similarity.
        2. Rerank using NV-RerankQA and return the true top RAG_TOP_K=3.
        """
        target_collection = self.critic_collection if for_critic else self.collection
        model_name = config.CRITIC_EMBEDDING_MODEL if for_critic else config.EMBEDDING_MODEL_NAME
        input_type = "query" if for_critic else None

        if top_k is None:
            top_k = config.CRITIC_RAG_TOP_K if for_critic else config.RAG_TOP_K

        if target_collection.count() == 0:
            logger.warning("Vector store is empty. Please add knowledge base documents first.")
            return []

        # Stage 1: broad retrieval (Parallel Dual-Query strategy to prevent context drowning)
        fetch_k = min(config.RAG_FETCH_K, target_collection.count())
        query_embedding = self._get_embedding(query, model_name=model_name, input_type=input_type)
        
        all_docs = []
        all_metas = []

        # Query A: Fetch from Company Portfolios (Global + Specific)
        if portfolio:
            try:
                res_port = target_collection.query(
                    query_embeddings=[query_embedding],
                    n_results=fetch_k,
                    where={"$or": [{"portfolio": "global"}, {"portfolio": portfolio}]}
                )
                if res_port.get("documents") and res_port["documents"][0]:
                    all_docs.extend(res_port["documents"][0])
                    all_metas.extend(res_port["metadatas"][0])
            except Exception as e:
                logger.error(f"Portfolio query failed: {e}")

        # Query B: Fetch from Specific Project RFP (doc_id)
        if doc_id:
            try:
                res_doc = target_collection.query(
                    query_embeddings=[query_embedding],
                    n_results=fetch_k,
                    where={"doc_id": str(doc_id)}
                )
                if res_doc.get("documents") and res_doc["documents"][0]:
                    all_docs.extend(res_doc["documents"][0])
                    all_metas.extend(res_doc["metadatas"][0])
            except Exception as e:
                logger.error(f"Doc_ID query failed: {e}")

        # Fallback if no specific filters or if both failed
        if not portfolio and not doc_id:
            res_all = target_collection.query(
                query_embeddings=[query_embedding],
                n_results=fetch_k
            )
            if res_all.get("documents") and res_all["documents"][0]:
                all_docs.extend(res_all["documents"][0])
                all_metas.extend(res_all["metadatas"][0])

        if not all_docs:
            return []
            
        # Deduplicate while preserving source domain
        port_docs, port_metas = [], []
        rfp_docs, rfp_metas = [], []
        seen = set()
        
        for d, m in zip(all_docs, all_metas):
            if d not in seen:
                seen.add(d)
                if m.get("source_type") == "rfp":
                    rfp_docs.append(d)
                    rfp_metas.append(m)
                else:
                    port_docs.append(d)
                    port_metas.append(m)

        formatted = []
        
        # --- DOMAIN-SEGREGATED RERANKING ---
        # If this is a Chat query (both portfolio and doc_id provided), we must NEVER allow
        # the Company Knowledge to 'drown out' the RFP facts in semantic scoring.
        # We enforce a strict 50/50 token mix by reranking them in isolated pools.

        def safe_rerank(q, d, mets, k):
            if not d: return []
            try:
                top = self._rerank(q, d, k)
                return [{"text": d[idx], "metadata": mets[idx], "reranker_score": score} for idx, score in top]
            except Exception as e:
                logger.warning(f"Reranker failed ({e}). Falling back to BGE-M3 base similarity.")
                return [{"text": d[idx], "metadata": mets[idx], "reranker_score": 1.0 - (idx*0.01)} for idx in range(min(k, len(d)))]

        if portfolio and doc_id:
            # Chat Mode: Split top_k evenly
            half_k = top_k // 2
            rem_k = top_k - half_k
            
            p_res = safe_rerank(query, port_docs, port_metas, half_k)
            r_res = safe_rerank(query, rfp_docs, rfp_metas, rem_k)
            
            # Interleave results to ensure balanced reading by the Chatbot
            for i in range(max(len(p_res), len(r_res))):
                if i < len(p_res): formatted.append(p_res[i])
                if i < len(r_res): formatted.append(r_res[i])
                
        else:
            # Adjudication Mode (or missing filters): Standard unified reranking
            unified_docs = port_docs + rfp_docs
            unified_metas = port_metas + rfp_metas
            formatted = safe_rerank(query, unified_docs, unified_metas, top_k)

        return formatted
