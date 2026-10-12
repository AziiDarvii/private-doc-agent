"""The document agent: parallel chunk analysis, a merge step, and grounded Q&A."""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from . import llm

ANALYSIS_CHUNK_CHARS = 1800  # small parts = sharper reviews, and they run in parallel on the GPU
QA_CHUNK_CHARS = 1500
QA_CONTEXT_CHARS = 6000
MAX_DOC_CHARS = 60000
MAX_PARALLEL_CHUNKS = 8
MAX_KEY_FACTS = 12
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
STOPWORDS = frozenset("the a an and or of to in on for is are was be what who when how does do with this that it my".split())

CHUNK_PROMPT = """You review private documents for a person who is not a lawyer or doctor.
Read this part of a document and reply with JSON only:
{"summary": "2-3 plain-English sentences",
 "risks": [{"severity": "high|medium|low", "issue": "short title", "why": "one sentence on why it matters to the reader", "quote": "exact short quote from the text"}],
 "key_facts": ["dates, amounts, deadlines, names of parties"]}
Only include real risks (unfair terms, penalties, deadlines, health warnings, hidden fees). Use [] if none."""

MERGE_PROMPT = """Combine these partial summaries of ONE document into a single clear summary
for a non-expert: what the document is, who it involves, and the 3 most important things to know.
Max 120 words. Plain text, no headings."""

QA_PROMPT = """Answer the question using ONLY the document excerpts below.
If the answer is not in them, say "The document doesn't say."
Be brief and plain-English. End with a line: Source: "<short exact quote>"."""


def chunk_text(text: str, max_chars: int) -> List[str]:
    """Split on paragraph boundaries, hard-splitting paragraphs that are too long."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces = [p[i : i + max_chars] for p in paragraphs for i in range(0, len(p), max_chars)]
    chunks: List[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > max_chars:
            chunks = chunks + [current]
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    return chunks + [current] if current else chunks


def _analyze_chunk(chunk: str) -> Dict[str, object]:
    text, _ = llm.chat(
        [{"role": "system", "content": CHUNK_PROMPT}, {"role": "user", "content": chunk}],
        max_tokens=700,
        json_mode=True,
    )
    parsed = llm.parse_json(text) or {}
    risks = parsed.get("risks") if isinstance(parsed.get("risks"), list) else []
    facts = parsed.get("key_facts") if isinstance(parsed.get("key_facts"), list) else []
    return {
        "summary": str(parsed.get("summary", "")),
        "risks": [r for r in risks if isinstance(r, dict)],
        "key_facts": [str(f) for f in facts],
    }


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        key = item.lower().strip()
        if key and key not in seen:
            seen = seen | {key}
            out = out + [item]
    return out


def analyze(document: str) -> Dict[str, object]:
    started = time.time()
    chunks = chunk_text(document[:MAX_DOC_CHARS], ANALYSIS_CHUNK_CHARS)
    if not chunks:
        raise ValueError("The document is empty.")
    # All chunks go to the GPU at once: vLLM batches them together (continuous batching).
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_CHUNKS) as pool:
        parts = list(pool.map(_analyze_chunk, chunks))

    if len(parts) == 1:
        summary = parts[0]["summary"]
    else:
        joined = "\n\n".join(f"Part {i + 1}: {p['summary']}" for i, p in enumerate(parts))
        summary, _ = llm.chat(
            [{"role": "system", "content": MERGE_PROMPT}, {"role": "user", "content": joined}], max_tokens=250
        )

    risks = sorted(
        (r for p in parts for r in p["risks"]),
        key=lambda r: SEVERITY_ORDER.get(str(r.get("severity", "low")).lower(), 3),
    )
    return {
        "summary": summary,
        "risks": risks,
        "key_facts": _dedupe([f for p in parts for f in p["key_facts"]])[:MAX_KEY_FACTS],
        "chunks": len(chunks),
        "seconds": round(time.time() - started, 2),
    }


def _keywords(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9$]+", text.lower()) if w not in STOPWORDS and len(w) > 2}


def retrieve(document: str, question: str) -> List[str]:
    """Pick the excerpts that share the most keywords with the question."""
    chunks = chunk_text(document[:MAX_DOC_CHARS], QA_CHUNK_CHARS)
    q = _keywords(question)
    ranked = sorted(chunks, key=lambda c: len(q & _keywords(c)), reverse=True)
    picked: List[str] = []
    for chunk in ranked:
        if sum(len(c) for c in picked) + len(chunk) > QA_CONTEXT_CHARS:
            break
        picked = picked + [chunk]
    return picked


def answer(document: str, question: str) -> Dict[str, object]:
    started = time.time()
    excerpts = retrieve(document, question)
    context = "\n\n---\n\n".join(excerpts)
    text, _ = llm.chat(
        [
            {"role": "system", "content": QA_PROMPT},
            {"role": "user", "content": f"Document excerpts:\n{context}\n\nQuestion: {question}"},
        ],
        max_tokens=400,
    )
    return {"answer": text, "excerpts_used": len(excerpts), "seconds": round(time.time() - started, 2)}
