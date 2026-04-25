from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Form
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
import httpx
from urllib.parse import quote
from langdetect import detect, LangDetectException

# --- App ---
app = FastAPI(title="Survival Station RAG API")

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# --- Config ---
VECTOR_DIR = os.getenv("VECTOR_DIR", "data/vectorstore")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "phi3:mini")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
LIBRETRANSLATE_URL = os.getenv("LIBRETRANSLATE_URL", "http://libretranslate:5000")

# OCR: Tesseract lang code -> LibreTranslate lang code
OCR_TO_LT_LANG = {
    "eng": "en",
    "spa": "es",
    "deu": "de",
    "fra": "fr",
    "ita": "it",
    "ukr": "uk",
    "tur": "tr",
    "rus": "ru",
}

# All installed Tesseract language packs combined for /query/image auto-detection.
# Order affects Tesseract's internal voting — put the most common languages first.
OCR_MULTILANG = "eng+spa+deu+fra+ita+ukr+tur+rus"

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
UPSCALE_THRESHOLD = 1500            # px: if longest side < this, upscale x2

# L2 distance threshold for Kiwix fallback. IndexFlatL2 scores are distances —
# lower = closer = more relevant. Suggest Kiwix when min_distance >= threshold
# (no KB chunk is close enough). Tune based on observed query distances.
KIWIX_DISTANCE_THRESHOLD: float = 1.0

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
    kiwix_search_url: str

class OCRResponse(BaseModel):
    text: str
    source_lang: str      # LibreTranslate code (en, es, de)
    tesseract_lang: str   # Tesseract code (eng, spa, deu)
    char_count: int
    psm: int
    preprocess: bool

class ImageQueryResponse(BaseModel):
    question: str
    ocr_text: str
    source_lang: str       # langdetect ISO 639-1 code
    translated: bool
    answer: str
    kiwix_search_url: str

# --- OCR helpers ---
def preprocess_for_ocr(image_bytes: bytes) -> np.ndarray:
    """Grayscale + conditional upscale.

    Kept minimal on purpose: heavier preprocessing (denoise, adaptive threshold)
    hurt accuracy on real-world highway/outdoor photos during testing.
    Grayscale removes color noise that confuses Tesseract without altering
    shapes; upscale helps small text reach Tesseract's optimal DPI range.
    """
    pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = np.array(pil_img)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    longest = max(h, w)
    if longest < UPSCALE_THRESHOLD:
        scale = 2
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    return gray


def run_ocr(image_bytes: bytes, lang: str, psm: int, preprocess: bool) -> str:
    """Prepare image and run Tesseract. Returns stripped text."""
    try:
        if preprocess:
            image_for_ocr = preprocess_for_ocr(image_bytes)
        else:
            image_for_ocr = Image.open(io.BytesIO(image_bytes))
    except Image.UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="Could not decode image")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preprocessing failed: {e}")

    try:
        text = pytesseract.image_to_string(image_for_ocr, lang=lang, config=f"--psm {psm}")
    except pytesseract.TesseractError as e:
        raise HTTPException(status_code=500, detail=f"OCR failed: {e}")

    return text.strip()


async def translate_to_english(text: str, source_lang: str) -> str:
    """Translate text to English via LibreTranslate. Falls back to original on any error."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{LIBRETRANSLATE_URL}/translate",
                json={"q": text, "source": source_lang, "target": "en", "format": "text"},
            )
            resp.raise_for_status()
            return resp.json().get("translatedText", text)
    except Exception:
        return text


def kiwix_url_if_needed(rag_response, query: str) -> str:
    """Always return a Kiwix search URL. Logs whether the RAG was confident."""
    try:
        distances = [n.score for n in rag_response.source_nodes if n.score is not None]
        if distances:
            closest = min(distances)
            if closest > KIWIX_DISTANCE_THRESHOLD:
                print(f"[DEBUG-RAG] kiwix: min_distance={closest:.3f} > threshold={KIWIX_DISTANCE_THRESHOLD} (RAG not confident)")
            else:
                print(f"[DEBUG-RAG] kiwix: min_distance={closest:.3f} <= threshold={KIWIX_DISTANCE_THRESHOLD} (RAG confident)")
        else:
            print("[DEBUG-RAG] kiwix: no scored nodes")
    except Exception as e:
        print(f"[DEBUG-RAG] kiwix: exception {e!r}")
    return f"http://10.42.0.1:8888/search?pattern={quote(query)}"


# --- Routes ---
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    response = query_engine.query(request.question)
    try:
        node_scores = [
            (i, round(n.score, 3) if n.score is not None else None)
            for i, n in enumerate(response.source_nodes)
        ]
        print(f"[DEBUG-RAG] prompt_sent={request.question[:120]!r}", flush=True)
        print(f"[DEBUG-RAG] node_scores={node_scores}  threshold={KIWIX_DISTANCE_THRESHOLD}", flush=True)
    except Exception as e:
        print(f"[DEBUG-RAG] could not read node scores: {e!r}", flush=True)
    kiwix_url = kiwix_url_if_needed(response, request.question)
    return QueryResponse(question=request.question, answer=str(response), kiwix_search_url=kiwix_url)


@app.post("/ocr", response_model=OCRResponse)
async def ocr(
    file: UploadFile = File(...),
    lang: str = Query("eng", description="Tesseract lang code: eng, spa, deu (or combined: eng+spa)"),
    psm: int = Query(6, ge=0, le=13, description="Tesseract Page Segmentation Mode (0-13). 6=uniform block, 4=single column, 3=auto"),
    preprocess: bool = Query(True, description="Apply OpenCV preprocessing (grayscale + conditional upscale)"),
):
    """Extract text from an uploaded image using Tesseract OCR."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    image_bytes = await file.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 10MB)")

    requested_langs = lang.split("+")
    for code in requested_langs:
        if code not in OCR_TO_LT_LANG:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported lang '{code}'. Available: {list(OCR_TO_LT_LANG.keys())}"
            )

    clean_text = run_ocr(image_bytes, lang, psm, preprocess)
    primary = requested_langs[0]

    return OCRResponse(
        text=clean_text,
        source_lang=OCR_TO_LT_LANG[primary],
        tesseract_lang=primary,
        char_count=len(clean_text),
        psm=psm,
        preprocess=preprocess,
    )


@app.post("/query/image", response_model=ImageQueryResponse)
async def query_image(
    file: UploadFile = File(...),
    question: str = Form(""),
    psm: int = Form(6),
):
    """OCR an image, auto-detect language, translate to English if needed, then query the RAG engine."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    image_bytes = await file.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 10MB)")

    ocr_text = run_ocr(image_bytes, OCR_MULTILANG, psm, preprocess=True)
    if not ocr_text:
        raise HTTPException(status_code=422, detail="No text found in image")

    # Detect language; default to English if text is too short for langdetect
    source_lang = "en"
    try:
        source_lang = detect(ocr_text)
    except LangDetectException:
        pass

    # Translate to English if the detected language is not English
    english_text = ocr_text
    translated = False
    if source_lang != "en":
        english_text = await translate_to_english(ocr_text, source_lang)
        translated = english_text != ocr_text

    q = question.strip()
    prompt = (
        "You are a helpful assistant. Reply with EXACTLY these four markdown sections in this order:\n\n"
        "## Identification\n"
        "One sentence stating what the image appears to contain. Use the OCR text and translation as basis. If unclear, say so plainly.\n\n"
        "## Translation\n"
        "The English translation of the OCR text, polished and grammatically clean. Keep it as a direct rendering — do NOT add interpretation.\n\n"
        "## About\n"
        "2-3 sentences explaining the medication or topic based ONLY on the knowledge base context provided. "
        "Focus only on what is relevant to the image content — do NOT list multiple topics or summarize the entire knowledge base. "
        "If the knowledge base context does not match, write exactly: No specific information found in the local knowledge base.\n\n"
        "## Wikipedia\n"
        "Write exactly: For more information, see Wikipedia (link below).\n\n"
        "---\n"
        f"OCR text (raw, language: {source_lang}):\n{ocr_text}\n\n"
        f"English translation:\n{english_text}\n"
        + (f"\nUser question (hint only): {q}" if q else "")
    )

    rag_response = query_engine.query(prompt)
    answer = str(rag_response)

    try:
        node_scores = [
            (i, round(n.score, 3) if n.score is not None else None)
            for i, n in enumerate(rag_response.source_nodes)
        ]
        print(f"[DEBUG-RAG] prompt_sent={prompt[:120]!r}")
        print(f"[DEBUG-RAG] node_scores={node_scores}  threshold={KIWIX_DISTANCE_THRESHOLD}")
    except Exception as e:
        print(f"[DEBUG-RAG] could not read node scores: {e!r}")

    # Use the user's question as the Kiwix search term; fall back to first 100 chars of OCR text
    search_term = q or english_text[:100]
    kiwix_url = kiwix_url_if_needed(rag_response, search_term)

    return ImageQueryResponse(
        question=question,
        ocr_text=ocr_text,
        source_lang=source_lang,
        translated=translated,
        answer=answer,
        kiwix_search_url=kiwix_url,
    )
