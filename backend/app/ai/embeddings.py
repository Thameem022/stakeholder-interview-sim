"""Retrieval embeddings: Amazon Titan Text Embeddings V2 on Bedrock.

One InvokeModel call per text (Titan V2 embeds a single input per request),
run off the event loop and bounded in concurrency. Vectors are normalised so
pgvector's cosine distance behaves as before. The dimension is configured
(BEDROCK_EMBEDDING_DIMENSIONS) and must match the pgvector columns — migration
0011 sets them to 1024.
"""

from __future__ import annotations

import asyncio
import json
from typing import List

from app.ai.aws import bedrock_runtime
from app.config import settings

# Titan V2 accepts up to 8,192 tokens; corpus chunks and queries are far below.
_MAX_CHARS = 30000
_CONCURRENCY = 8


def _invoke(text: str) -> List[float]:
    body = json.dumps({
        "inputText": text[:_MAX_CHARS],
        "dimensions": settings.bedrock_embedding_dimensions,
        "normalize": True,
    })
    response = bedrock_runtime().invoke_model(
        modelId=settings.bedrock_embedding_model_id,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())
    vector = payload["embedding"]
    if len(vector) != settings.bedrock_embedding_dimensions:
        raise ValueError(
            f"embedding has {len(vector)} dimensions, expected "
            f"{settings.bedrock_embedding_dimensions}"
        )
    return vector


async def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    gate = asyncio.Semaphore(_CONCURRENCY)

    async def one(text: str) -> List[float]:
        async with gate:
            return await asyncio.to_thread(_invoke, text)

    return list(await asyncio.gather(*(one(t) for t in texts)))
