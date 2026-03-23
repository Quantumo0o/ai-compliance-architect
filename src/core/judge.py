import sqlite3
import hashlib
import json
import math
import requests
import asyncio
import httpx
from openai import OpenAI
from openai import AsyncOpenAI
from typing import Dict, Any
from src.utils.logger import logger
from src.utils.resilience import retry_with_backoff
from src.utils.validator import Adjudication
from src.utils.db_handler import DBHandler
from src.core.vector_store import VectorStoreManager
import config


class AdjudicationManager:
    def __init__(self, db_write_queue: asyncio.Queue = None):
        self.api_key = config.NVIDIA_MISTRAL_KEY
        self.invoke_url = config.MISTRAL_INVOKE_URL
        self.db = DBHandler()
        self.vector_store = VectorStoreManager()
        self.db_write_queue = db_write_queue
        
        # Async client for critic agent
        self.critic_client_async = AsyncOpenAI(
            api_key=config.NVIDIA_NEMOTRON_ULTRA_KEY,
            base_url=config.NEMOTRON_ULTRA_BASE_URL
        )

    async def _queue_db_write(self, func_name: str, *args, **kwargs):
        """Helper to push a write task to the queue or execute directly if no queue."""
        if self.db_write_queue:
            await self.db_write_queue.put((func_name, args, kwargs))
        else:
            await asyncio.to_thread(getattr(self.db, func_name), *args, **kwargs)

    @retry_with_backoff(retries=5)
    def adjudicate_requirement(self, req_db_id: int, portfolio: str = None):
        """
        Phase 1: Agent A (Mistral Large) makes a compliance decision.
        Phase 2: If confidence < threshold, Critic Agent (Nemotron Ultra) challenges it.
        """
        requirement = self._get_requirement(req_db_id)
        if not requirement:
            logger.warning(f"Requirement id={req_db_id} not found in DB.")
            return

        req_text = requirement.get("req_text", "")
        req_label = requirement.get("req_id", str(req_db_id))

        # ── 1. RAG: retrieve evidence (now two-stage reranked) ──────────────
        logger.info(f"Retrieving evidence for {req_label} (Portfolio: {portfolio or 'Global'})")
        evidence_chunks = self.vector_store.search(req_text, portfolio=portfolio)

        if not evidence_chunks:
            logger.warning(f"{req_label}: No evidence found. Marking as Non-Compliant (no data).")
            self.db.add_adjudication(req_db_id, {
                "compliance_status": "Non-Compliant",
                "confidence_score": 0.0,
                "evidence_summary": "No matching evidence was found in the company knowledge base.",
                "source_document": "N/A",
                "exact_quote": "N/A",
                "gap_analysis": "The knowledge base does not contain documents relevant to this requirement.",
                "needs_review": False,
                "critic_verdict": None
            })
            return

        evidence_text = "\n\n".join([
            f"[Source: {c['metadata'].get('source', 'Unknown')}]\n{c['text']}"
            for c in evidence_chunks
        ])

        # Compute evidence_confidence from top chunk's reranker logit (using sigmoid)
        evidence_confidence = 0.0
        if evidence_chunks:
            top_score = evidence_chunks[0].get("reranker_score", 0.0)
            evidence_confidence = 1.0 / (1.0 + math.exp(-top_score))

        # Check Adjudication Cache before calling APIs
        cache_key = hashlib.sha256((req_text + evidence_text).encode('utf-8')).hexdigest()
        cached_json = self.db.get_adjudication_cache(cache_key)
        if cached_json:
            logger.info(f"{req_label}: Cache hit. Skipping LLM calls.")
            adj_data = json.loads(cached_json)
            adj_data["evidence_confidence"] = evidence_confidence
            self.db.add_adjudication(req_db_id, adj_data)
            return

        # ── 2. Agent A (Tier-1): Judge compliance ───────────────────────────
        system_prompt = (
            "You are a Senior RFP Compliance Judge. "
            "You will receive a specific RFP requirement and several paragraphs of company evidence. "
            "Determine if the company fully, partially, or does not comply. "
            "Provide a gap analysis explaining why. "
            "The confidence_score should be between 0.0 and 1.0. "
            "IMPORTANT: In 'evidence_summary' and 'source_document', explicitly cite the EXACT page number where the evidence was found based on the provided [Source] tags. "
            "Return ONLY a valid JSON object (no markdown fences) matching exactly: "
            '{"compliance_status": "Fully Compliant|Partially Compliant|Non-Compliant", '
            '"confidence_score": 0.0, "evidence_summary": "... (from Page X)", "source_document": "filename (Page X)", '
            '"exact_quote": "...", "gap_analysis": "..."}'
        )

        req_page = requirement.get("page_num")
        req_page_str = f" (from RFP Page {req_page})" if req_page else ""
        user_prompt = (
            f"Requirement ({req_label}){req_page_str}:\n{req_text}\n\n"
            f"Evidence:\n{evidence_text}"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        }
        payload = {
            "model": config.TIERED_MODEL,  # Try cheap model first
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 2048,
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }

        logger.info(f"Adjudicating {req_label} (Tier-1) | Model: [{config.TIERED_MODEL}]")
        response = requests.post(self.invoke_url, headers=headers, json=payload, timeout=180)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]

        try:
            adj = Adjudication.model_validate_json(content)

            # Escalate to Tier-2 if Tier-1 confidence is too low
            if adj.confidence_score < config.TIERED_CONFIDENCE_THRESHOLD:
                logger.info(f"{req_label}: Tier-1 conf ({adj.confidence_score:.2f}) < threshold. Escalating... | Model: [{config.ADJUDICATION_MODEL}]")
                payload["model"] = config.ADJUDICATION_MODEL
                response = requests.post(self.invoke_url, headers=headers, json=payload, timeout=180)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                adj = Adjudication.model_validate_json(content)

            agent_a_result = adj.model_dump()
            agent_a_result["needs_review"] = False
            agent_a_result["critic_verdict"] = None
            agent_a_result["evidence_confidence"] = evidence_confidence
            logger.info(f"{req_label}: Agent A -> {adj.compliance_status} (confidence={adj.confidence_score:.2f})")

            # -- 3. Agent B (Nemotron Ultra): Critic (Low confidence + High Risk) ----
            req_category = requirement.get("category", "")
            if adj.confidence_score < config.CRITIC_CONFIDENCE_THRESHOLD and req_category in config.HIGH_RISK_CATEGORIES:
                logger.info(f"{req_label}: Critic triggered (Category: {req_category}, Conf: {adj.confidence_score:.2f}) | Model: [{config.CRITIC_MODEL}]")
                
                # --- DOUBLE-BLIND RETRIEVAL ---
                logger.info(f"{req_label}: Critic performing independent retrieval...")
                critic_evidence_chunks = self.vector_store.search(req_text, portfolio=portfolio, top_k=config.CRITIC_RAG_TOP_K, for_critic=True)
                critic_evidence_text = "\n\n".join([
                    f"[Source: {c['metadata'].get('source', 'Unknown')}]\n{c['text']}"
                    for c in critic_evidence_chunks
                ])

                critic_verdict = self._run_critic_agent(req_text, critic_evidence_text, adj.compliance_status)

                if critic_verdict:
                    agent_a_result["critic_verdict"] = critic_verdict
                    # Flag conflict if verdicts are opposite on compliance
                    a_compliant = "compliant" in adj.compliance_status.lower() and adj.compliance_status != "Non-Compliant"
                    b_compliant = "compliant" in critic_verdict.lower() and "non-compliant" not in critic_verdict.lower()

                    if a_compliant != b_compliant:
                        agent_a_result["needs_review"] = True
                        agent_a_result["gap_analysis"] = (
                            f"⚠️ CONFLICT — Human Review Required.\n"
                            f"Agent A ({adj.compliance_status}): {adj.gap_analysis}\n\n"
                            f"Critic (Nemotron Ultra): {critic_verdict}"
                        )
                        logger.warning(f"{req_label}: CONFLICT detected between Agent A and Critic!")

            self.db.set_adjudication_cache(cache_key, json.dumps(agent_a_result))
            self.db.add_adjudication(req_db_id, agent_a_result)

        except Exception as e:
            logger.error(f"{req_label}: adjudication parse failed — {e}\nRaw: {content[:300]}")
            raise

    def _run_critic_agent(self, req_text: str, evidence_text: str, agent_a_decision: str) -> str | None:
        """
        Calls Nemotron Ultra 253B in 'detailed thinking' mode to find
        counter-evidence challenging Agent A's compliance decision.
        """
        try:
            critic_system = (
                "detailed thinking on\n\n"
                "You are a Devil's Advocate Compliance Reviewer. "
                "Your job is to challenge compliance decisions. "
                "You will be given an RFP requirement, evidence, and a preliminary decision. "
                "Find any weaknesses, missing proof, or counter-arguments in the evidence. "
                "Summarize your verdict in 2-3 sentences: state whether you AGREE or DISAGREE "
                "with the preliminary decision, and why. "
                "Be specific and critical."
            )
            critic_user = (
                f"RFP Requirement:\n{req_text}\n\n"
                f"Supporting Evidence:\n{evidence_text}\n\n"
                f"Preliminary Decision: {agent_a_decision}\n\n"
                "Challenge this decision. Find holes."
            )

            response = self.critic_client.chat.completions.create(
                model=config.CRITIC_MODEL,
                messages=[
                    {"role": "system", "content": critic_system},
                    {"role": "user", "content": critic_user}
                ],
                temperature=0.6,
                top_p=0.95,
                max_tokens=1024
            )
            verdict = response.choices[0].message.content
            logger.info(f"Critic Agent verdict: {verdict[:120]}...")
            return verdict

        except Exception as e:
            logger.warning(f"Critic Agent call failed: {e}. Skipping critic for this requirement.")
            return None

    def _get_requirement(self, req_db_id: int) -> Dict[str, Any]:
        with sqlite3.connect(config.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM requirements WHERE id = ?", (req_db_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    async def adjudicate_requirement_async(self, req_db_id: int, client: httpx.AsyncClient, portfolio: str = None):
        """
        Asynchronous version of Phase 1 and Phase 2.
        Uses httpx to avoid blocking the event loop during network requests.
        """
        requirement = await asyncio.to_thread(self._get_requirement, req_db_id)
        if not requirement:
            logger.warning(f"Requirement id={req_db_id} not found in DB.")
            return

        req_text = requirement.get("req_text", "")
        req_label = requirement.get("req_id", str(req_db_id))

        # ── 1. RAG: retrieve evidence (now two-stage reranked) ──────────────
        logger.info(f"Retrieving evidence for {req_label} (Portfolio: {portfolio or 'Global'})")
        evidence_chunks = await asyncio.to_thread(self.vector_store.search, req_text, portfolio=portfolio)

        if not evidence_chunks:
            logger.warning(f"{req_label}: No evidence found. Marking as Non-Compliant (no data).")
            await self._queue_db_write("add_adjudication", req_db_id, {
                "compliance_status": "Non-Compliant",
                "confidence_score": 0.0,
                "evidence_summary": "No matching evidence was found in the company knowledge base.",
                "source_document": "N/A",
                "exact_quote": "N/A",
                "gap_analysis": "The knowledge base does not contain documents relevant to this requirement.",
                "needs_review": False,
                "critic_verdict": None
            })
            return

        evidence_text = "\n\n".join([
            f"[Source: {c['metadata'].get('source', 'Unknown')}]\n{c['text']}"
            for c in evidence_chunks
        ])

        # Compute evidence_confidence from top chunk's reranker logit (using sigmoid)
        evidence_confidence = 0.0
        if evidence_chunks:
            top_score = evidence_chunks[0].get("reranker_score", 0.0)
            evidence_confidence = 1.0 / (1.0 + math.exp(-top_score))

        # Check Adjudication Cache before calling APIs
        cache_key = hashlib.sha256((req_text + evidence_text).encode('utf-8')).hexdigest()
        cached_json = await asyncio.to_thread(self.db.get_adjudication_cache, cache_key)
        if cached_json:
            logger.info(f"{req_label}: Cache hit. Skipping LLM calls.")
            adj_data = json.loads(cached_json)
            adj_data["evidence_confidence"] = evidence_confidence
            await self._queue_db_write("add_adjudication", req_db_id, adj_data)
            return

        # ── 2. Agent A (Tier-1): Judge compliance ───────────────────────────
        system_prompt = (
            "You are a Senior RFP Compliance Judge. "
            "You will receive a specific RFP requirement and several paragraphs of company evidence. "
            "Determine if the company fully, partially, or does not comply. "
            "Provide a gap analysis explaining why. "
            "The confidence_score should be between 0.0 and 1.0. "
            "IMPORTANT: In 'evidence_summary' and 'source_document', explicitly cite the EXACT page number where the evidence was found based on the provided [Source] tags. "
            "Return ONLY a valid JSON object (no markdown fences) matching exactly: "
            '{"compliance_status": "Fully Compliant|Partially Compliant|Non-Compliant", '
            '"confidence_score": 0.0, "evidence_summary": "... (from Page X)", "source_document": "filename (Page X)", '
            '"exact_quote": "...", "gap_analysis": "..."}'
        )

        req_page = requirement.get("page_num")
        req_page_str = f" (from RFP Page {req_page})" if req_page else ""
        user_prompt = (
            f"Requirement ({req_label}){req_page_str}:\n{req_text}\n\n"
            f"Evidence:\n{evidence_text}"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        }
        payload = {
            "model": config.TIERED_MODEL,  # Try cheap model first
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 2048,
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }
        logger.info(f"Adjudicating {req_label} (Tier-1) | Model: [{config.TIERED_MODEL}]")
        response = await client.post(self.invoke_url, headers=headers, json=payload, timeout=180)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]

        try:
            adj = Adjudication.model_validate_json(content)

            # Escalate to Tier-2 if Tier-1 confidence is too low
            if adj.confidence_score < config.TIERED_CONFIDENCE_THRESHOLD:
                logger.info(f"{req_label}: Tier-1 conf ({adj.confidence_score:.2f}) < threshold. Escalating... | Model: [{config.ADJUDICATION_MODEL}]")
                payload["model"] = config.ADJUDICATION_MODEL
                response = await client.post(self.invoke_url, headers=headers, json=payload, timeout=180)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                adj = Adjudication.model_validate_json(content)

            agent_a_result = adj.model_dump()
            agent_a_result["needs_review"] = False
            agent_a_result["critic_verdict"] = None
            agent_a_result["evidence_confidence"] = evidence_confidence
            logger.info(f"{req_label}: Agent A -> {adj.compliance_status} (confidence={adj.confidence_score:.2f})")

            # -- 3. Agent B (Nemotron Ultra): Critic (Low confidence + High Risk) ----
            req_category = requirement.get("category", "")
            if adj.confidence_score < config.CRITIC_CONFIDENCE_THRESHOLD and req_category in config.HIGH_RISK_CATEGORIES:
                logger.info(f"{req_label}: Critic triggered (Category: {req_category}, Conf: {adj.confidence_score:.2f}) | Model: [{config.CRITIC_MODEL}]")
                
                # --- DOUBLE-BLIND RETRIEVAL ---
                logger.info(f"{req_label}: Critic performing independent retrieval...")
                critic_evidence_chunks = await asyncio.to_thread(self.vector_store.search, req_text, portfolio=portfolio, top_k=config.CRITIC_RAG_TOP_K, for_critic=True)
                critic_evidence_text = "\n\n".join([
                    f"[Source: {c['metadata'].get('source', 'Unknown')}]\n{c['text']}"
                    for c in critic_evidence_chunks
                ])

                critic_verdict = await self._run_critic_agent_async(req_text, critic_evidence_text, adj.compliance_status)

                if critic_verdict:
                    agent_a_result["critic_verdict"] = critic_verdict
                    # Flag conflict if verdicts are opposite on compliance
                    a_compliant = "compliant" in adj.compliance_status.lower() and adj.compliance_status != "Non-Compliant"
                    b_compliant = "compliant" in critic_verdict.lower() and "non-compliant" not in critic_verdict.lower()

                    if a_compliant != b_compliant:
                        agent_a_result["needs_review"] = True
                        agent_a_result["gap_analysis"] = (
                            f"⚠️ CONFLICT — Human Review Required.\n"
                            f"Agent A ({adj.compliance_status}): {adj.gap_analysis}\n\n"
                            f"Critic (Nemotron Ultra): {critic_verdict}"
                        )
                        logger.warning(f"{req_label}: CONFLICT detected between Agent A and Critic!")

            await self._queue_db_write("set_adjudication_cache", cache_key, json.dumps(agent_a_result))
            await self._queue_db_write("add_adjudication", req_db_id, agent_a_result)

        except Exception as e:
            logger.error(f"{req_label}: adjudication parse failed — {e}\nRaw: {content[:300]}")
            raise

    async def _run_critic_agent_async(self, req_text: str, evidence_text: str, agent_a_decision: str) -> str | None:
        """
        Asynchronous version of the Critic AI call.
        """
        try:
            critic_system = (
                "detailed thinking on\n\n"
                "You are a Devil's Advocate Compliance Reviewer. "
                "Your job is to challenge compliance decisions. "
                "You will be given an RFP requirement, evidence, and a preliminary decision. "
                "Find any weaknesses, missing proof, or counter-arguments in the evidence. "
                "Summarize your verdict in 2-3 sentences: state whether you AGREE or DISAGREE "
                "with the preliminary decision, and why. "
                "Be specific and critical."
            )
            critic_user = (
                f"RFP Requirement:\n{req_text}\n\n"
                f"Supporting Evidence:\n{evidence_text}\n\n"
                f"Preliminary Decision: {agent_a_decision}\n\n"
                "Challenge this decision. Find holes."
            )

            response = await self.critic_client_async.chat.completions.create(
                model=config.CRITIC_MODEL,
                messages=[
                    {"role": "system", "content": critic_system},
                    {"role": "user", "content": critic_user}
                ],
                temperature=0.6,
                top_p=0.95,
                max_tokens=1024
            )
            verdict = response.choices[0].message.content
            logger.info(f"Critic Agent verdict: {verdict[:120]}...")
            return verdict

        except Exception as e:
            logger.warning(f"Critic Agent async call failed: {e}. Skipping critic for this requirement.")
            return None
