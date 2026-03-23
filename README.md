# AI Compliance Matrix Architect 🛡️⚖️🏆

An industrial-grade, multi-agent RAG system designed to transform complex, messy RFP (Request for Proposal) PDF documents into structured, audited compliance matrices with 100% data integrity.

## 🚀 Key Features

- **Industrial-Grade Extraction**: Uses a 1000-character page-stitching buffer and Mistral-Large-3 to capture requirements spanning multiple pages.
- **Async Double-Blind Auditing**: Employs a primary Judge (Agent A) and a skeptical Critic (Agent B) to cross-verify compliance claims and minimize AI hallucinations.
- **Domain-Segregated Reranking**: Mathematically balances context retrieval between company evidence and tender rules to prevent "Semantic Drowning."
- **Persistent Task Queue**: Built on a SQLite state-machine to ensure analysis resumes automatically after server restarts.
- **Secure Access**: Protected by a centralized Passkey Gate for corporate network safety.

## 🏗️ Architecture

The system is built on a high-performance Python stack:
- **Backend**: FastAPI with an Asynchronous Task Queue.
- **Frontend**: Streamlit Dashboard.
- **Vector Engine**: ChromaDB with BGE-M3 and NV-Embed embeddings.
- **LLM Orchestration**: Supports NVIDIA-hosted models (Mistral-Large, Llama 3.1, Nemotron-Ultra).

## 🛠️ Getting Started

### Prerequisites
- Python 3.10+
- NVIDIA API Key (for hosted models)

### Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/ai-compliance-architect.git
   cd ai-compliance-architect
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Set up your environment variables in `.env`:
   ```env
   NVIDIA_MISTRAL_KEY=your_key_here
   NVIDIA_NEMOTRON_ULTRA_KEY=your_key_here
   # See config.py for full list
   ```

### Running the App
1. Start the Backend API:
   ```bash
   python -m uvicorn src.api.server:app --port 8000
   ```
2. Start the Frontend UI:
   ```bash
   python -m streamlit run app.py
   ```
3. **Login**: Enter the default passkey `admin123` to unlock the dashboard.

## 📂 Documentation

Detailed engineering audits and guides are available in the `/docs` folder:
- [Architecture Blueprint](./docs/architecture_blueprint.md)
- [Prompt Library](./docs/prompts_library.md)
- [Interview Prep Guide](./docs/interview_prep.md)
- [Project Walkthrough](./docs/walkthrough.md)

## ⚖️ License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
