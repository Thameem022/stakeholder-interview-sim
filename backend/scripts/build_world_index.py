"""
build_world_index.py

Build a FAISS vector index for a shared "world bible" (.docx) knowledge base.

Example:

    /opt/anaconda3/envs/glob/bin/python backend/build_world_index.py \
    --input-docx "backend/Harborville World Bible Case Dossier.docx" \
    --output-dir "backend/world_bible_index" \
    --embedding-model "sentence-transformers/multi-qa-mpnet-base-dot-v1" \
    --device mps \
    --max-chunk-words 800 \
    --overlap-words 80 \
    --batch-size 8
  
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import faiss
import numpy as np

# Stability guards for macOS/conda CPU runs (prevents tokenizer/thread-related crashes)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

from sentence_transformers import SentenceTransformer


@dataclass
class SectionUnit:
    section_title: str
    subsection_title: Optional[str]
    content: str
    source: str
    document_name: str


@dataclass
class ChunkRecord:
    chunk_id: str
    text: str
    source: str
    document_name: str
    section_title: str
    subsection_title: Optional[str]
    chunk_type: str
    topic_tags: List[str]
    canonical_entities: List[str]
    word_count: int


CANONICAL_ENTITY_CANDIDATES = [
    "Harbortown",
    "Town Council",
    "Town Manager",
    "waterfront",
    "downtown",
    "marsh",
    "resilience",
    "flood",
    "adaptation",
    "emergency management",
]

# Simple keyword-based topic map (non-LLM) for robust, interpretable tagging.
TOPIC_KEYWORD_MAP: Dict[str, List[str]] = {
    "governance": ["town council", "town manager", "ordinance", "policy", "governance"],
    "waterfront": ["waterfront", "harbor", "port", "dock", "shoreline"],
    "downtown": ["downtown", "main street", "commercial core", "business district"],
    "flood_risk": ["flood", "inundation", "storm surge", "sea level", "drainage"],
    "adaptation": ["adaptation", "resilience", "mitigation", "pathway", "planning"],
    "emergency_management": ["emergency management", "evacuation", "response", "preparedness"],
    "environment": ["marsh", "wetland", "ecosystem", "habitat", "conservation"],
    "infrastructure": ["infrastructure", "road", "bridge", "utility", "stormwater"],
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "for", "on", "with", "from",
    "by", "at", "is", "are", "be", "this", "that", "it", "as", "into", "about",
}


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _heading_level(paragraph_style_name: str) -> Optional[int]:
    """Return heading level from a style name like 'Heading 1', else None."""
    if not paragraph_style_name:
        return None
    m = re.match(r"^Heading\s+(\d+)$", paragraph_style_name.strip(), flags=re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))


def _infer_heading_level_from_text(text: str) -> Optional[int]:
    """
    Fallback heading detector when DOCX styles are inconsistent.
    - "Section N ..." -> Heading 1
    - "N.M ..."       -> Heading 2
    """
    t = (text or "").strip()
    if not t:
        return None

    if re.match(r"^Section\s+\d+\b", t, flags=re.IGNORECASE):
        return 1
    if re.match(r"^\d+\.\d+\b", t):
        return 2
    return None


def load_docx_with_structure(input_docx: Path) -> List[Dict[str, Any]]:
    """
    Load .docx and return paragraph-level structured rows:
    [{text, is_heading, heading_level, style_name}].
    """
    if not input_docx.exists():
        raise FileNotFoundError(f"Input .docx not found: {input_docx}")

    try:
        docx_module = importlib.import_module("docx")
        Document = getattr(docx_module, "Document")
    except Exception as exc:
        raise ImportError(
            "python-docx is required. Install with: pip install python-docx"
        ) from exc

    doc = Document(str(input_docx))
    rows: List[Dict[str, Any]] = []

    for p in doc.paragraphs:
        text = (p.text or "").strip()
        if not text:
            continue
        style_name = (p.style.name if p.style is not None else "") or ""
        level = _heading_level(style_name)
        if level is None:
            level = _infer_heading_level_from_text(text)
        rows.append(
            {
                "text": text,
                "is_heading": level is not None,
                "heading_level": level,
                "style_name": style_name,
            }
        )

    return rows


def build_structured_sections(rows: Sequence[Dict[str, Any]], document_name: str) -> List[SectionUnit]:
    """
    Convert paragraph rows into structured section units with heading context.

        Heading logic:
            - Heading 1 -> section_title
            - Heading 2 -> subsection_title
            - Every heading boundary flushes content first (no cross-boundary merge)
    """
    units: List[SectionUnit] = []
    current_section = "Untitled Section"
    current_subsection: Optional[str] = None
    buffer: List[str] = []

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        text = "\n\n".join(buffer).strip()
        if text:
            units.append(
                SectionUnit(
                    section_title=current_section,
                    subsection_title=current_subsection,
                    content=text,
                    source="world_bible",
                    document_name=document_name,
                )
            )
        buffer = []

    for row in rows:
        if row["is_heading"]:
            # Start a new unit when heading changes.
            flush_buffer()
            level = row["heading_level"]
            heading_text = row["text"]
            if level == 1:
                current_section = heading_text
                current_subsection = None
            elif level == 2:
                current_subsection = heading_text
            else:
                # Keep H3+ under current H2/H1; still close current unit above.
                pass
            continue

        buffer.append(row["text"])

    flush_buffer()
    return units


def _topic_tags_from_titles(section_title: str, subsection_title: Optional[str], text: str) -> List[str]:
    """
    Derive rough topic tags via deterministic keyword matching.
    Priority order:
      1) keyword hits in heading + content
      2) fallback lexical tags from headings
    """
    scan_text = f"{section_title} {subsection_title or ''}\n{text}".lower()
    tags: List[str] = []

    for topic, keywords in TOPIC_KEYWORD_MAP.items():
        if any(kw in scan_text for kw in keywords):
            tags.append(topic)

    if tags:
        return tags[:8]

    # fallback to heading lexical tokens
    seed = f"{section_title} {subsection_title or ''}"
    for w in re.findall(r"[A-Za-z][A-Za-z\-]+", seed.lower()):
        if w in STOPWORDS or len(w) < 3:
            continue
        if w not in tags:
            tags.append(w)

    return tags[:8]


def _canonical_entities_from_text(text: str) -> List[str]:
    found: List[str] = []
    lower_text = text.lower()
    for ent in CANONICAL_ENTITY_CANDIDATES:
        # Word-boundary for single-token entities; substring for multi-word phrases.
        el = ent.lower()
        if " " in el:
            present = el in lower_text
        else:
            present = re.search(rf"\b{re.escape(el)}\b", lower_text) is not None
        if present:
            found.append(ent)
    return found


def _infer_chunk_type(section_title: str, subsection_title: Optional[str], text: str) -> str:
    probe = f"{section_title} {subsection_title or ''}\n{text}".lower()
    if "executive summary" in probe:
        return "executive_summary"
    section_l = section_title.lower()
    subsection_l = (subsection_title or "").lower()
    if (
        "canonical institutions" in probe
        or re.match(r"^section\s*9\b", section_l) is not None
        or ("institution" in section_l and "canonical" in section_l)
        or ("organization" in section_l and "canonical" in section_l)
        or ("institution" in subsection_l and "canonical" in subsection_l)
    ):
        return "institution_list"
    return "section_body"


def _chunk_text_paragraph_aware(text: str, max_chunk_words: int, overlap_words: int) -> List[str]:
    """
    Chunk text primarily on paragraph boundaries.
    If a single paragraph exceeds max size, split by sentence fallback.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    current: List[str] = []
    current_words = 0

    def flush_current(with_overlap: bool = True) -> None:
        nonlocal current, current_words
        if not current:
            return
        chunk_text = "\n\n".join(current).strip()
        if chunk_text:
            chunks.append(chunk_text)

        if with_overlap and overlap_words > 0 and chunk_text:
            words = chunk_text.split()
            tail = " ".join(words[-overlap_words:]) if words else ""
            current = [tail] if tail else []
            current_words = _word_count(tail)
        else:
            current = []
            current_words = 0

    for para in paragraphs:
        para_words = _word_count(para)

        # Oversized paragraph -> sentence fallback split.
        if para_words > max_chunk_words:
            flush_current(with_overlap=False)
            sentences = re.split(r"(?<=[.!?])\s+", para)
            temp: List[str] = []
            temp_words = 0
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                s_words = _word_count(s)
                if temp_words + s_words > max_chunk_words and temp:
                    chunk = " ".join(temp).strip()
                    chunks.append(chunk)
                    if overlap_words > 0:
                        tail = " ".join(chunk.split()[-overlap_words:])
                        temp = [tail, s]
                        temp_words = _word_count(tail) + s_words
                    else:
                        temp = [s]
                        temp_words = s_words
                else:
                    temp.append(s)
                    temp_words += s_words
            if temp:
                chunks.append(" ".join(temp).strip())
            continue

        if current_words + para_words > max_chunk_words and current:
            flush_current(with_overlap=True)

        current.append(para)
        current_words += para_words

    flush_current(with_overlap=False)
    return [c for c in chunks if c.strip()]


def chunk_sections(
    sections: Sequence[SectionUnit],
    max_chunk_words: int = 800,
    overlap_words: int = 80,
) -> List[ChunkRecord]:
    """
    Heading-aware chunking:
      1) keep each section/subsection coherent
      2) split long sections with paragraph-aware chunker
    """
    chunks: List[ChunkRecord] = []

    for i, sec in enumerate(sections):
        sub_chunks = _chunk_text_paragraph_aware(
            sec.content,
            max_chunk_words=max_chunk_words,
            overlap_words=overlap_words,
        )
        for j, text in enumerate(sub_chunks):
            chunk_id = f"{i:04d}_{j:03d}"
            topic_tags = _topic_tags_from_titles(sec.section_title, sec.subsection_title, text)
            entities = _canonical_entities_from_text(text)
            chunk_type = _infer_chunk_type(sec.section_title, sec.subsection_title, text)
            chunks.append(
                ChunkRecord(
                    chunk_id=chunk_id,
                    text=text,
                    source=sec.source,
                    document_name=sec.document_name,
                    section_title=sec.section_title,
                    subsection_title=sec.subsection_title,
                    chunk_type=chunk_type,
                    topic_tags=topic_tags,
                    canonical_entities=entities,
                    word_count=_word_count(text),
                )
            )
    return chunks


def build_faiss_index(
    chunks: Sequence[ChunkRecord],
    model_name: str,
    device: str,
    batch_size: int = 8,
) -> Tuple[faiss.Index, np.ndarray]:
    """Embed chunks and build cosine-sim FAISS index (IndexFlatIP on normalized vectors)."""
    if not chunks:
        raise ValueError("No chunks to index.")

    # Optional torch thread limiting for additional CPU stability.
    try:
        torch = importlib.import_module("torch")
        if device == "cpu":
            torch.set_num_threads(1)
            if hasattr(torch, "set_num_interop_threads"):
                torch.set_num_interop_threads(1)
    except Exception:
        pass

    model = SentenceTransformer(model_name, device=device)
    texts = [c.text for c in chunks]
    embeddings = model.encode(
        texts,
        batch_size=max(1, int(batch_size)),
        normalize_embeddings=True,
        show_progress_bar=True,
        convert_to_numpy=True,
    ).astype("float32")

    dim = int(embeddings.shape[1])
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)

    return index, embeddings


def save_chunks_json(chunks: Sequence[ChunkRecord], out_path: Path) -> None:
    payload = [asdict(c) for c in chunks]
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_stats(sections: Sequence[SectionUnit], chunks: Sequence[ChunkRecord]) -> None:
    n_sections = len(sections)
    n_chunks = len(chunks)
    avg_chunk_words = (sum(c.word_count for c in chunks) / n_chunks) if n_chunks else 0.0

    print("\n=== Build Stats ===")
    print(f"Extracted sections: {n_sections}")
    print(f"Total chunks: {n_chunks}")
    print(f"Average chunk length (words): {avg_chunk_words:.1f}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build FAISS index from world bible .docx")
    parser.add_argument("--input-docx", type=Path, required=True, help="Path to source .docx world bible")
    parser.add_argument("--output-dir", type=Path, default=Path("world_bible_index"), help="Output directory")
    parser.add_argument("--embedding-model", type=str, default="sentence-transformers/multi-qa-mpnet-base-dot-v1")
    parser.add_argument("--device", type=str, default="cpu", choices=["cpu", "cuda", "mps"], help="Embedding device")
    parser.add_argument("--max-chunk-words", type=int, default=800, help="Target max words per chunk")
    parser.add_argument("--overlap-words", type=int, default=80, help="Word overlap between chunks")
    parser.add_argument("--batch-size", type=int, default=8, help="Embedding batch size (lower if CPU is unstable)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        rows = load_docx_with_structure(args.input_docx)
    except Exception as exc:
        raise SystemExit(f"Failed to load docx: {exc}")

    if not rows:
        print("⚠️ Warning: document extraction produced no non-empty paragraphs.")
        return

    sections = build_structured_sections(rows, document_name=args.input_docx.name)
    if not sections:
        print("⚠️ Warning: no structured sections built from document.")
        return

    chunks = chunk_sections(
        sections,
        max_chunk_words=args.max_chunk_words,
        overlap_words=args.overlap_words,
    )

    if not chunks:
        print("⚠️ Warning: no chunks generated.")
        return

    try:
        index, _ = build_faiss_index(
            chunks,
            model_name=args.embedding_model,
            device=args.device,
            batch_size=args.batch_size,
        )
    except Exception as exc:
        raise SystemExit(f"Failed to build FAISS index: {exc}")

    index_path = output_dir / "index.faiss"
    chunks_path = output_dir / "world_bible_chunks.json"

    faiss.write_index(index, str(index_path))
    save_chunks_json(chunks, chunks_path)

    _print_stats(sections, chunks)
    print("\nSaved artifacts:")
    print(f"- FAISS index: {index_path}")
    print(f"- Chunk metadata/text: {chunks_path}")

    print("\nExample run:")
    print(
        "python backend/build_world_index.py --input-docx backend/Harbortown_World_Bible.docx "
        "--output-dir backend/world_bible_index --device cpu"
    )


if __name__ == "__main__":
    main()
