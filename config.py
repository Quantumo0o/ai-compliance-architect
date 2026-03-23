import os
from dotenv import load_dotenv

load_dotenv()

# API Keys from .env
NVIDIA_DIN_V2_KEY = os.getenv("NVIDIA_DIN_V2_KEY")
NVIDIA_BGE_M3_KEY = os.getenv("NVIDIA_BGE_M3_KEY")
NVIDIA_NEMOTRON_OCR_KEY = os.getenv("NVIDIA_NEMOTRON_OCR_KEY")
NVIDIA_MISTRAL_KEY = os.getenv("NVIDIA_MISTRAL_KEY")
NVIDIA_NEMOTRON_ULTRA_KEY = os.getenv("NVIDIA_NEMOTRON_ULTRA_KEY")

# Default NVIDIA Key (fallback or primary if applicable)
NVIDIA_API_KEY = NVIDIA_MISTRAL_KEY 

# LLM Models
EXTRACTION_MODEL = "openai/gpt-oss-120b"
ADJUDICATION_MODEL = "mistralai/mistral-large-3-675b-instruct-2512"   # Tier-2: full judge
TIERED_MODEL = "meta/llama-3.1-8b-instruct"                           # Tier-1: cheap first pass
CRITIC_MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"
RERANKER_MODEL = "nvidia/llama-3.2-nv-rerankqa-1b-v2"

# Endpoint URLs
NEMOTRON_OCR_URL = "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v1"
BGE_M3_BASE_URL = "https://integrate.api.nvidia.com/v1"
NEMOTRON_ULTRA_BASE_URL = "https://integrate.api.nvidia.com/v1"
MISTRAL_INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
CRITIC_INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
RERANKER_URL = "https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-3_2-nv-rerankqa-1b-v2/reranking"
DINOV2_URL = "https://ai.api.nvidia.com/v1/cv/nvidia/nv-dinov2"

# Embedding Models
EMBEDDING_MODEL_NAME = "baai/bge-m3"
CRITIC_EMBEDDING_MODEL = "nvidia/nv-embedqa-e5-v5"

# Paths
UPLOAD_DIR = "data/uploads"
KNOWLEDGE_BASE_DIR = "data/knowledge_base"
CACHE_DIR = "data/cache"
DB_PATH = "db/compliance_architect.db"
DB_SCHEMA_PATH = "db/schema.sql"

# Extraction Settings
OBLIGATION_KEYWORDS = ["shall", "must", "requirement", "mandatory", "will"]

# RAG Settings
RAG_FETCH_K = 20     # chunks to retrieve before reranking
RAG_TOP_K = 3        # final chunks after reranking
RAG_CHUNK_SIZE = 1200
RAG_CHUNK_OVERLAP = 400

# App Settings
APP_PASSKEY = os.getenv("APP_PASSKEY", "admin123")

# Critic Agent Settings
CRITIC_CONFIDENCE_THRESHOLD = 0.65
TIERED_CONFIDENCE_THRESHOLD = 0.70
CRITIC_RAG_TOP_K = 5  # Independent retrieval depth for the Critic
# Only run Critic on these high-stakes categories
HIGH_RISK_CATEGORIES = {"Financial", "Eligibility", "Technical Compliance", "Technical", "Quality"}

# Resource Limits
MAX_PDF_PAGES = 500
BATCH_SIZE = 25   # (raised from 10 for faster parallel adjudication)
