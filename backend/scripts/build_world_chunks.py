"""
build_world_chunks.py

Slim version of build_world_index.py — produces world_bible_chunks.json
WITHOUT torch/faiss/sentence-transformers. Embedding happens later in
embed_and_load.py via OpenAI text-embedding-3-small.

The chunking logic is ported verbatim from build_world_index.py:
- Heading-aware paragraph chunking
- max_chunk_words=800, overlap_words=80
- Deterministic keyword-based topic_tags + canonical_entities

Usage:
    uv run python scripts/build_world_chunks.py \\
        --input-docx "path/to/Harborville_World_Bible.docx" \\
        --output scripts/seed_data/world_bible_chunks.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


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
    if not paragraph_style_name:
        return None
    m = re.match(r"^Heading\s+(\d+)$", paragraph_style_name.strip(), flags=re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))


def _infer_heading_level_from_text(text: str) -> Optional[int]:
    t = (text or "").strip()
    if not t:
        return None
    if re.match(r"^Section\s+\d+\b", t, flags=re.IGNORECASE):
        return 1
    if re.match(r"^\d+\.\d+\b", t):
        return 2
    return None


def load_docx_with_structure(input_docx: Path) -> List[Dict[str, Any]]:
    if not input_docx.exists():
        raise FileNotFoundError(f"Input .docx not found: {input_docx}")

    try:
        docx_module = importlib.import_module("docx")
        Document = getattr(docx_module, "Document")
    except Exception as exc:
        raise ImportError(
            "python-docx is required. Install with: uv sync --extra scripts"
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
            flush_buffer()
            level = row["heading_level"]
            heading_text = row["text"]
            if level == 1:
                current_section = heading_text
                current_subsection = None
            elif level == 2:
                current_subsection = heading_text
            continue
        buffer.append(row["text"])

    flush_buffer()
    return units


def _topic_tags_from_titles(section_title: str, subsection_title: Optional[str], text: str) -> List[str]:
    scan_text = f"{section_title} {subsection_title or ''}\n{text}".lower()
    tags: List[str] = []

    for topic, keywords in TOPIC_KEYWORD_MAP.items():
        if any(kw in scan_text for kw in keywords):
            tags.append(topic)

    if tags:
        return tags[:8]

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build world-bible chunks JSON (no embeddings)")
    parser.add_argument("--input-docx", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-chunk-words", type=int, default=800)
    parser.add_argument("--overlap-words", type=int, default=80)
    args = parser.parse_args()

    rows = load_docx_with_structure(args.input_docx)
    sections = build_structured_sections(rows, document_name=args.input_docx.name)
    chunks = chunk_sections(sections, max_chunk_words=args.max_chunk_words, overlap_words=args.overlap_words)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps([asdict(c) for c in chunks], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(chunks)} chunks to {args.output}")


if __name__ == "__main__":
    main()
