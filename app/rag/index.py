"""ChromaDB index over SOW chunks (brief section 30).

Chunks are embedded with the metadata that makes team-scoped retrieval possible
later, and used now to find evidence for claims that cite nothing. A claim with
no citation is not automatically unsupported — the SOW may well establish it in
a chunk the generation step failed to reference — so the grounding pass looks
before it judges.

Uses Chroma's bundled MiniLM embedding rather than sentence-transformers, which
would pull in torch for the same model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings

from app.core.config import get_settings
from app.ingestion.parser import ParsedDocument

logger = logging.getLogger(__name__)

_PERSIST_DIR = Path("chroma_data")


@dataclass
class RetrievedChunk:
    chunk_key: str
    text: str
    section: str
    page: int | None
    distance: float


def _client() -> chromadb.ClientAPI:
    settings = get_settings()
    if settings.chroma_host and settings.chroma_host not in {"", "local"}:
        try:
            return chromadb.HttpClient(
                host=settings.chroma_host,
                port=settings.chroma_port,
                settings=Settings(anonymized_telemetry=False),
            )
        except Exception as exc:  # noqa: BLE001 - fall back rather than fail the run
            logger.warning("Chroma server unavailable (%s); using local persistence", exc)
    _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(_PERSIST_DIR), settings=Settings(anonymized_telemetry=False)
    )


def collection_name(project_id: str) -> str:
    # Chroma requires 3-63 chars, starting and ending alphanumeric.
    return f"sow-{project_id}"[:63]


def index_document(project_id: str, doc: ParsedDocument, team_hint: str = "") -> int:
    """Embed every chunk with the metadata team-scoped retrieval needs."""
    client = _client()
    collection = client.get_or_create_collection(
        name=collection_name(project_id), metadata={"hnsw:space": "cosine"}
    )

    ids, documents, metadatas = [], [], []
    for section, chunk in doc.iter_chunks():
        ids.append(doc.chunk_key(section, chunk))
        documents.append(chunk.text)
        metadatas.append(
            {
                "chunk_key": doc.chunk_key(section, chunk),
                "document_id": doc.doc_key,
                "section": section.title,
                "page": chunk.page if chunk.page is not None else -1,
                "is_table": chunk.is_table,
                "team": team_hint,
                "source_type": "sow",
            }
        )
    if ids:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    return len(ids)


def search(project_id: str, query: str, limit: int = 4) -> list[RetrievedChunk]:
    client = _client()
    try:
        collection = client.get_collection(name=collection_name(project_id))
    except Exception:  # noqa: BLE001 - an unindexed project simply has no evidence
        logger.warning("No Chroma collection for project %s", project_id)
        return []

    result = collection.query(query_texts=[query], n_results=limit)
    chunks: list[RetrievedChunk] = []
    for text, meta, distance in zip(
        result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        page = meta.get("page", -1)
        chunks.append(
            RetrievedChunk(
                chunk_key=str(meta.get("chunk_key", "")),
                text=text,
                section=str(meta.get("section", "")),
                page=None if page in (-1, None) else int(page),
                distance=float(distance),
            )
        )
    return chunks


def delete_project(project_id: str) -> None:
    try:
        _client().delete_collection(name=collection_name(project_id))
    except Exception as exc:  # noqa: BLE001 - a collection that never existed is fine
        logger.debug("No Chroma collection to delete for %s: %s", project_id, exc)
