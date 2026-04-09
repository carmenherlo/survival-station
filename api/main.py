from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from llama_index.core import VectorStoreIndex, StorageContext, load_index_from_storage
from llama_index.vector_stores.faiss import FaissVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.core import Settings
import os

# --- App ---
app = FastAPI(title="Survival Station RAG API")

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"], # allow_methods=["*"],
    allow_headers=["*"],
)

# --- Config ---
VECTOR_DIR = os.getenv("VECTOR_DIR", "data/vectorstore")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "phi3:mini")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

# --- Ollama settings ---
Settings.embed_model = OllamaEmbedding(
    model_name=EMBED_MODEL,
    base_url=OLLAMA_URL
)
Settings.llm = Ollama(
    model=LLM_MODEL,
    base_url=OLLAMA_URL,
    request_timeout=120.0,
    context_window=4096
)

# --- Load index ---
print("Loading vector index...")
vector_store = FaissVectorStore.from_persist_dir(VECTOR_DIR)
storage_context = StorageContext.from_defaults(
    vector_store=vector_store,
    persist_dir=VECTOR_DIR
)
index = load_index_from_storage(storage_context)
query_engine = index.as_query_engine()
print("Index loaded OK")

# --- Models ---
class QueryRequest(BaseModel):
    question: str

class QueryResponse(BaseModel):
    question: str
    answer: str

# --- Routes ---
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    response = query_engine.query(request.question)
    return QueryResponse(question=request.question, answer=str(response))