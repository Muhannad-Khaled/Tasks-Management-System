"""Persist a parsed SOW and its extraction into PostgreSQL.

Chunk keys are the join between the document and everything generated from it,
so they are written first and every task records the keys it cites.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ingestion.parser import ParsedDocument
from app.models import Assumption, Project, ProjectTask, SOWChunk, SOWDocument, SOWSection
from app.schemas.sow import StructuredSOW


def persist_document(
    db: Session, project: Project, doc: ParsedDocument, stored_path: str, parsing_status: str
) -> tuple[SOWDocument, dict[str, SOWChunk]]:
    """Write the document tree; return it with a chunk_key -> chunk index."""
    sow_doc = SOWDocument(
        project_id=project.id,
        doc_key=doc.doc_key,
        filename=doc.filename,
        file_type=doc.file_type,
        stored_path=stored_path,
        parsing_status=parsing_status,
    )
    db.add(sow_doc)
    db.flush()

    chunks_by_key: dict[str, SOWChunk] = {}
    for section in doc.sections:
        db_section = SOWSection(
            sow_document_id=sow_doc.id,
            section_index=section.section_index,
            title=section.title,
            page_start=section.page_start,
            page_end=section.page_end,
        )
        db.add(db_section)
        db.flush()
        for chunk in section.chunks:
            key = doc.chunk_key(section, chunk)
            db_chunk = SOWChunk(
                section_id=db_section.id,
                chunk_index=chunk.chunk_index,
                chunk_key=key,
                text=chunk.text,
                page=chunk.page,
            )
            db.add(db_chunk)
            chunks_by_key[key] = db_chunk
    db.flush()
    return sow_doc, chunks_by_key


def persist_extraction(
    db: Session,
    project: Project,
    extraction: StructuredSOW,
    chunks_by_key: dict[str, SOWChunk],
) -> list[ProjectTask]:
    """Write tasks and assumptions, resolving citations and dependencies.

    Citations the model invented (keys that are not in the document) are dropped
    rather than stored, so a task can never point at evidence that does not exist.
    """
    for assumption in extraction.assumptions:
        db.add(
            Assumption(
                project_id=project.id,
                assumption_key=assumption.assumption_id,
                category=assumption.category,
                value=assumption.value,
                reason=assumption.reason,
                confidence=assumption.confidence,
            )
        )

    tasks_by_extracted_id: dict[str, ProjectTask] = {}
    for extracted in extraction.tasks:
        valid_keys = [k for k in extracted.source_chunk_keys if k in chunks_by_key]
        section_id = None
        if valid_keys:
            section_id = chunks_by_key[valid_keys[0]].section_id
        task = ProjectTask(
            project_id=project.id,
            title=extracted.title,
            description=extracted.description,
            team=str(extracted.team),
            priority=str(extracted.priority),
            estimated_hours=extracted.estimated_hours,
            source_status=str(extracted.source_status),
            source_sow_section_id=section_id,
            source_chunk_keys=",".join(valid_keys),
        )
        db.add(task)
        db.flush()
        tasks_by_extracted_id[extracted.task_id] = task

    for extracted in extraction.tasks:
        task = tasks_by_extracted_id[extracted.task_id]
        for dependency_id in extracted.depends_on:
            upstream = tasks_by_extracted_id.get(dependency_id)
            # Unknown ids are dropped and self-dependencies would deadlock the graph.
            if upstream is not None and upstream.id != task.id:
                task.depends_on.append(upstream)

    db.commit()
    return list(tasks_by_extracted_id.values())


def invented_citations(extraction: StructuredSOW, chunks_by_key: dict[str, SOWChunk]) -> list[str]:
    """Citation keys the model produced that do not exist in the document."""
    cited = {k for t in extraction.tasks for k in t.source_chunk_keys}
    cited |= {k for r in extraction.requirements for k in r.source_chunk_keys}
    return sorted(cited - set(chunks_by_key))
