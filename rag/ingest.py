from llama_index.core import SimpleDirectoryReader, VectorStoreIndex, StorageContext
from llama_index.vector_stores.faiss import FaissVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.core import Settings
import faiss
import os

DOCS_DIR = "rag/docs"
VECTOR_DIR = "data/vectorstore"

# Embedding model via Ollama - no torch, no CUDA needed
Settings.embed_model = OllamaEmbedding(
    model_name="nomic-embed-text",
    base_url="http://localhost:11434"
)
Settings.llm = None

print("Loading documents...")
documents = SimpleDirectoryReader(DOCS_DIR).load_data()
print(f"  {len(documents)} chunks loaded")

print("Creating vector index...")
dimension = 768  # nomic-embed-text vector size
faiss_index = faiss.IndexFlatL2(dimension)
vector_store = FaissVectorStore(faiss_index=faiss_index)
storage_context = StorageContext.from_defaults(vector_store=vector_store)

index = VectorStoreIndex.from_documents(
    documents,
    storage_context=storage_context,
)

os.makedirs(VECTOR_DIR, exist_ok=True)
index.storage_context.persist(persist_dir=VECTOR_DIR)
print(f"Index successfully saved to {VECTOR_DIR}")