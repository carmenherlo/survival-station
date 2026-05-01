from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from llama_index.core import VectorStoreIndex, StorageContext, load_index_from_storage
from llama_index.vector_stores.faiss import FaissVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.core import Settings
from langdetect import detect as langdetect_detect, LangDetectException
import pytesseract
from PIL import Image, ImageOps
import cv2
import numpy as np
import io
import json
import os
import asyncio
import urllib.request
from urllib.parse import quote

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
OCR_MULTILANG = "eng+spa+fra+deu"

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
UPSCALE_THRESHOLD = 1500            # px: if longest side < this, upscale x2
MAX_OCR_DIM = 2000                  # px: downscale phone photos before Tesseract

# L2 distance threshold for Kiwix fallback. IndexFlatL2 scores are distances —
# lower = closer = more relevant. Suggest Kiwix when min_distance >= threshold
# (no KB chunk is close enough). Tune based on observed query distances.
KIWIX_DISTANCE_THRESHOLD: float = 1.0

# Languages supported by the LibreTranslate instance (set via LT_LOAD_ONLY in compose)
LT_SUPPORTED = {"es", "en", "de", "fr", "uk", "ru", "it", "ar", "tr"}

LANG_NAMES = {
    "en": "English", "es": "Spanish", "de": "German", "fr": "French",
    "it": "Italian", "uk": "Ukrainian", "tr": "Turkish",
    "ru": "Russian", "ar": "Arabic",
}

# --- Ollama settings ---
Settings.embed_model = OllamaEmbedding(
    model_name=EMBED_MODEL,
    base_url=OLLAMA_URL
)
Settings.llm = Ollama(
    model=LLM_MODEL,
    base_url=OLLAMA_URL,
    request_timeout=120.0,
    context_window=1024,
    additional_kwargs={"num_predict": 200, "num_ctx": 1024},
)

# --- Load index ---
print("Loading vector index...")
vector_store = FaissVectorStore.from_persist_dir(VECTOR_DIR)
storage_context = StorageContext.from_defaults(
    vector_store=vector_store,
    persist_dir=VECTOR_DIR
)
index = load_index_from_storage(storage_context)
query_engine = index.as_query_engine(similarity_top_k=1)
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
    language: str         # detected language name, e.g. "German"
    translated_text: str  # English translation; empty string if source is English or unavailable
    answer: str           # survival knowledge only
    kiwix_search_url: str

# --- OCR helpers ---
def preprocess_for_ocr(image_bytes: bytes) -> np.ndarray:
    """Grayscale + resize to OCR-optimal resolution.

    Downscale phone photos (4000+ px) — Tesseract doesn't gain accuracy beyond
    ~2000 px but CPU time grows quadratically. Upscale tiny images so text
    reaches Tesseract's optimal DPI range. No denoise/threshold: those hurt
    accuracy on real-world photos.
    """
    pil_img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    img = np.array(pil_img)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    longest = max(h, w)
    if longest > MAX_OCR_DIM:
        scale = MAX_OCR_DIM / longest
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    elif longest < UPSCALE_THRESHOLD:
        gray = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
    return gray


def run_ocr(image_bytes: bytes, lang: str, psm: int, preprocess: bool) -> str:
    """Prepare image and run Tesseract. Returns stripped text."""
    try:
        if preprocess:
            image_for_ocr = preprocess_for_ocr(image_bytes)
        else:
            image_for_ocr = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)))
    except Image.UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="Could not decode image")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preprocessing failed: {e}")

    try:
        text = pytesseract.image_to_string(image_for_ocr, lang=lang, config=f"--psm {psm}")
    except pytesseract.TesseractError as e:
        raise HTTPException(status_code=500, detail=f"OCR failed: {e}")

    return text.strip()



async def identify_from_ocr(text: str) -> dict:
    """Ask phi3:mini to identify the object from translated OCR text.
    Returns dict with keys SCAN and TYPE (may be empty/Unknown)."""
    prompt = (
        "From the OCR text below, identify the object. Use ONLY what the text says.\n"
        "Reply with exactly these two lines, nothing else:\n"
        "SCAN: [Medication / Food / Plant / Tool / Document / Sign / Unknown]\n"
        "TYPE: [specific product name and dosage, e.g. Ibuprofen 400mg — or Unknown]\n\n"
        f"OCR: {text[:400]}\n"
    )
    try:
        result = await asyncio.to_thread(Settings.llm.complete, prompt)
        parsed = {}
        for line in str(result).splitlines():
            line = line.strip()
            for key in ("SCAN", "TYPE"):
                if line.upper().startswith(key + ":"):
                    parsed[key] = line[len(key) + 1:].strip()
                    break
        print(f"[IDENTIFY] {parsed}", flush=True)
        return parsed
    except Exception as e:
        print(f"[IDENTIFY] failed: {e!r}", flush=True)
        return {}


async def translate_to_english(text: str, source_lang: str) -> str:
    """Call LibreTranslate in a thread. Returns original text on failure."""
    def _call():
        payload = json.dumps({"q": text, "source": source_lang, "target": "en"}).encode()
        req = urllib.request.Request(
            f"{LIBRETRANSLATE_URL}/translate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())["translatedText"]
    try:
        return await asyncio.to_thread(_call)
    except Exception as e:
        print(f"[translate] failed ({source_lang}→en): {e!r}", flush=True)
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

    clean_text = await asyncio.to_thread(run_ocr, image_bytes, lang, psm, preprocess)
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
    """OCR → langdetect → LibreTranslate → RAG. No LLM image description."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    image_bytes = await file.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 10MB)")

    # Step 1: OCR — Tesseract is CPU-bound, run in thread pool
    ocr_text = await asyncio.to_thread(run_ocr, image_bytes, OCR_MULTILANG, psm, True)
    if not ocr_text:
        raise HTTPException(status_code=422, detail="No text found in image")
    print(f"[OCR] {ocr_text[:200]!r}", flush=True)

    # Step 2: Detect language
    try:
        detected = langdetect_detect(ocr_text)
    except LangDetectException:
        detected = "en"
    if detected not in LT_SUPPORTED:
        detected = "en"
    lang_name = LANG_NAMES.get(detected, detected.upper())
    print(f"[LANG] {detected} ({lang_name})", flush=True)

    # Step 3: Translate to English via LibreTranslate if needed
    if detected != "en":
        english_text = await translate_to_english(ocr_text[:500], detected)
    else:
        english_text = ocr_text
    print(f"[TRANSLATED] {english_text[:200]!r}", flush=True)

    # Step 4: Identify object from translated OCR text
    q = question.strip()
    id_info = await identify_from_ocr(english_text)
    scan = id_info.get("SCAN", "").strip()
    typ  = id_info.get("TYPE", "").strip()

    is_unknown = (
        not scan or scan.upper() in ("UNKNOWN", "")
        or not typ  or typ.upper()  in ("UNKNOWN", "")
    )

    # Step 5: RAG — prefer identified type for sharper embedding match
    rag_query = q if q else (typ if not is_unknown else english_text[:200])
    rag_response = await asyncio.to_thread(query_engine.query, rag_query)

    try:
        node_scores = [
            (i, round(n.score, 3) if n.score is not None else None)
            for i, n in enumerate(rag_response.source_nodes)
        ]
        print(f"[DEBUG-RAG] rag_query={rag_query[:80]!r}")
        print(f"[DEBUG-RAG] node_scores={node_scores}  threshold={KIWIX_DISTANCE_THRESHOLD}")
    except Exception as e:
        print(f"[DEBUG-RAG] could not read node scores: {e!r}")

    rag_text = str(rag_response)
    # Truncate at sentence boundary, max ~200 words
    words = rag_text.split()
    if len(words) > 200:
        truncated = ' '.join(words[:200])
        last_stop = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
        rag_text = truncated[:last_stop + 1] if last_stop > 100 else truncated + '…'

    # Build identification header
    if is_unknown:
        id_section = "## SCAN: not identified"
    else:
        id_section = f"## SCAN: {scan}\n## TYPE: {typ}"

    answer = f"{id_section}\n\n## Survival Knowledge\n{rag_text}"

    translated_text = ""
    if detected != "en" and english_text.strip() != ocr_text.strip():
        translated_text = english_text.strip()

    kiwix_url = kiwix_url_if_needed(rag_response, rag_query)

    return ImageQueryResponse(
        question=question,
        ocr_text=ocr_text,
        language=lang_name,
        translated_text=translated_text,
        answer=answer,
        kiwix_search_url=kiwix_url,
    )
