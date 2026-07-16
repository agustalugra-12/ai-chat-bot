"""RAG service — extract text from PDF/DOCX, chunk, BM25 retrieve.

Lexical (BM25) retrieval, no external embeddings required.
Chunks are stored in MongoDB collection `rag_chunks` linked to `rag_documents`.
"""
import io
import re
from typing import List, Optional

from pypdf import PdfReader
from docx import Document as DocxDocument
from rank_bm25 import BM25Okapi


def extract_text_from_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n\n".join(parts)


def extract_text_from_docx(data: bytes) -> str:
    doc = DocxDocument(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)


def extract_text_from_txt(data: bytes) -> str:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return extract_text_from_pdf(data)
    if name.endswith(".docx"):
        return extract_text_from_docx(data)
    if name.endswith(".txt") or name.endswith(".md"):
        return extract_text_from_txt(data)
    raise ValueError(f"Unsupported file type: {filename}")


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 80) -> List[str]:
    """Simple sliding window over cleaned paragraphs."""
    # normalise whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) <= chunk_size:
        return [text] if text else []

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        # try to end on sentence boundary
        if end < len(text):
            dot = text.rfind(". ", start, end)
            nl = text.rfind("\n", start, end)
            pivot = max(dot, nl)
            if pivot > start + chunk_size * 0.4:
                end = pivot + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+", re.UNICODE)


def tokenize(s: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(s or "")]


def bm25_search(query: str, chunks: List[dict], k: int = 5) -> List[dict]:
    """Return top-k chunks with score. Each chunk is {id, doc_id, doc_title, text}."""
    if not chunks:
        return []
    corpus = [tokenize(c["text"]) for c in chunks]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(tokenize(query))
    ranked = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)
    out = []
    for score, chunk in ranked[:k]:
        if score <= 0:
            continue
        out.append({**chunk, "score": float(score)})
    return out


def build_rag_context(hits: List[dict], max_chars: int = 3500) -> str:
    if not hits:
        return ""
    out = ["=== DOKUMEN REFERENSI (RAG) ==="]
    total = 0
    for h in hits:
        block = f"[{h.get('doc_title','doc')}] {h['text']}"
        if total + len(block) > max_chars:
            break
        out.append(block)
        total += len(block)
    out.append("=== AKHIR DOKUMEN ===")
    return "\n\n".join(out)
