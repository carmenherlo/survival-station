from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from llama_index.core import VectorStoreIndex, StorageContext, load_index_from_storage
from llama_index.vector_stores.faiss import FaissVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.core import Settings
import pytesseract
from PIL import Image
import cv2
import numpy as np
import io
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

# OCR: Tesseract lang code -> LibreTranslate lang code
OCR_TO_LT_LANG = {
    "eng": "en",
    "spa": "es",
    "deu": "de",
    "fra": "fr",
    "ukr": "uk",
    "tur": "tr",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
UPSCALE_THRESHOLD = 1500            # px: if longest side < this, upscale x2

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

class OCRResponse(BaseModel):
    text: str
    source_lang: str      # LibreTranslate code (en, es, de)
    tesseract_lang: str   # Tesseract code (eng, spa, deu)
    char_count: int
    psm: int
    preprocess: bool

# --- OCR helpers ---
def preprocess_for_ocr(image_bytes: bytes) -> np.ndarray:
    """Grayscale + conditional upscale.

    Kept minimal on purpose: heavier preprocessing (denoise, adaptive threshold)
    hurt accuracy on real-world highway/outdoor photos during testing.
    Grayscale removes color noise that confuses Tesseract without altering
    shapes; upscale helps small text reach Tesseract's optimal DPI range.
    """
    # Load as RGB then to numpy
    pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = np.array(pil_img)

    # Grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    # Upscale small images (helps Tesseract see details)
    h, w = gray.shape
    longest = max(h, w)
    if longest < UPSCALE_THRESHOLD:
        scale = 2
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    return gray

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

@app.post("/ocr", response_model=OCRResponse)
async def ocr(
    file: UploadFile = File(...),
    lang: str = Query("eng", description="Tesseract lang code: eng, spa, deu (or combined: eng+spa)"),
    psm: int = Query(6, ge=0, le=13, description="Tesseract Page Segmentation Mode (0-13). 6=uniform block, 4=single column, 3=auto"),
    preprocess: bool = Query(True, description="Apply OpenCV preprocessing (grayscale + conditional upscale)"),
):
    """Extract text from an uploaded image using Tesseract OCR."""
    # Validate content type
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
 
    # Read + size cap
    image_bytes = await file.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 10MB)")
 
    # Validate lang codes
    requested_langs = lang.split("+")
    for code in requested_langs:
        if code not in OCR_TO_LT_LANG:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported lang '{code}'. Available: {list(OCR_TO_LT_LANG.keys())}"
            )
 
    # Prepare image
    try:
        if preprocess:
            image_for_ocr = preprocess_for_ocr(image_bytes)
        else:
            image_for_ocr = Image.open(io.BytesIO(image_bytes))
    except Image.UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="Could not decode image")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preprocessing failed: {e}")
 
    # OCR
    try:
        tess_config = f"--psm {psm}"
        text = pytesseract.image_to_string(image_for_ocr, lang=lang, config=tess_config)
    except pytesseract.TesseractError as e:
        raise HTTPException(status_code=500, detail=f"OCR failed: {e}")
 
    primary = requested_langs[0]
    lt_source = OCR_TO_LT_LANG[primary]
    clean_text = text.strip()
 
    return OCRResponse(
        text=clean_text,
        source_lang=lt_source,
        tesseract_lang=primary,
        char_count=len(clean_text),
        psm=psm,
        preprocess=preprocess,
    )