from llama_index.core import VectorStoreIndex, StorageContext, load_index_from_storage
from llama_index.vector_stores.faiss import FaissVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.core import Settings
import sys

VECTOR_DIR = "data/vectorstore"

# Embedding model - must match ingest.py
Settings.embed_model = OllamaEmbedding(
    model_name="nomic-embed-text",
    base_url="http://localhost:11434"
)

# LLM - generates the final answer
Settings.llm = Ollama(
    model="phi3:mini",
    base_url="http://localhost:11434",
    request_timeout=120.0,
    context_window=4096
)

print("Loading index...")
vector_store = FaissVectorStore.from_persist_dir(VECTOR_DIR)
storage_context = StorageContext.from_defaults(
    vector_store=vector_store,
    persist_dir=VECTOR_DIR
)
index = load_index_from_storage(storage_context)
query_engine = index.as_query_engine()

question = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "How do I purify water without electricity?"
print(f"\nQuestion: {question}\n")
response = query_engine.query(question)
print(f"Answer: {response}\n")