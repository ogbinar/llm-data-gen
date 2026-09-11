from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable

from .config import ChunkingConfig
from .models import Chunk, SourceDocument

Chunker = Callable[[SourceDocument, ChunkingConfig], list[tuple[str, int | None, int | None]]]


def chunk_document(document: SourceDocument, config: ChunkingConfig | int) -> list[Chunk]:
    legacy = isinstance(config, int)
    resolved = ChunkingConfig(chunk_size=config) if legacy else config
    try:
        chunker = CHUNKER_REGISTRY[resolved.strategy]
    except KeyError as exc:
        raise ValueError(f"unknown chunking strategy: {resolved.strategy}") from exc

    checksum = document.source_checksum or hashlib.sha256(document.text.encode("utf-8")).hexdigest()
    spans = chunker(document, resolved)
    result: list[Chunk] = []
    for index, (text, start, end) in enumerate(spans, start=1):
        chunk_id = (
            f"{document.source_id}-chunk-{index:03d}"
            if legacy
            else f"{document.source_id}::{resolved.strategy}::{resolved.version}::{index:04d}"
        )
        result.append(
            Chunk(
                source_id=document.source_id,
                chunk_id=chunk_id,
                chunk_index=index,
                source_format=document.source_format or _source_format(document.provenance_path),
                text=text.strip(),
                token_estimate=max(1, len(re.findall(r"\S+", text))),
                provenance_path=document.provenance_path,
                title=document.title,
                strategy=resolved.strategy,
                strategy_version=resolved.version,
                source_checksum=checksum,
                start_offset=start,
                end_offset=end,
                domain=document.domain,
                metadata=document.metadata,
            )
        )
    return result


def chunk_documents(
    documents: Iterable[SourceDocument],
    config: ChunkingConfig,
) -> list[Chunk]:
    return [chunk for document in documents for chunk in chunk_document(document, config)]


def list_chunkers() -> list[str]:
    return sorted(CHUNKER_REGISTRY)


def _document_chunks(
    document: SourceDocument,
    config: ChunkingConfig,
) -> list[tuple[str, int | None, int | None]]:
    text = _normalize_text(document.text)
    return [(text, 0, len(text))] if text else []


def _recursive_text_chunks(
    document: SourceDocument,
    config: ChunkingConfig,
) -> list[tuple[str, int | None, int | None]]:
    text = _normalize_text(document.text)
    return _recursive_spans(text, config.chunk_size, config.chunk_overlap)


def _markdown_section_chunks(
    document: SourceDocument,
    config: ChunkingConfig,
) -> list[tuple[str, int | None, int | None]]:
    text = _normalize_text(document.text)
    headings = list(re.finditer(r"(?m)^#{1,6}\s+.+$", text))
    if not headings:
        return _recursive_spans(text, config.chunk_size, config.chunk_overlap)

    boundaries = [0]
    boundaries.extend(match.start() for match in headings if match.start() > 0)
    boundaries.append(len(text))
    chunks: list[tuple[str, int | None, int | None]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        section = text[start:end].strip()
        if not section:
            continue
        actual_start = text.find(section, start, end)
        if len(section) <= config.chunk_size:
            chunks.append((section, actual_start, actual_start + len(section)))
            continue
        for piece, local_start, local_end in _recursive_spans(
            section, config.chunk_size, config.chunk_overlap
        ):
            chunks.append(
                (
                    piece,
                    actual_start + (local_start or 0),
                    actual_start + (local_end or len(piece)),
                )
            )
    return chunks


def _fixed_token_chunks(
    document: SourceDocument,
    config: ChunkingConfig,
) -> list[tuple[str, int | None, int | None]]:
    text = _normalize_text(document.text)
    matches = list(re.finditer(r"\S+", text))
    if not matches:
        return []
    step = config.chunk_size - config.chunk_overlap
    chunks: list[tuple[str, int | None, int | None]] = []
    for token_start in range(0, len(matches), step):
        token_end = min(token_start + config.chunk_size, len(matches))
        start = matches[token_start].start()
        end = matches[token_end - 1].end()
        chunks.append((text[start:end], start, end))
        if token_end == len(matches):
            break
    return chunks


def _recursive_spans(text: str, size: int, overlap: int) -> list[tuple[str, int, int]]:
    if not text:
        return []
    chunks: list[tuple[str, int, int]] = []
    start = 0
    while start < len(text):
        maximum_end = min(start + size, len(text))
        end = maximum_end
        if maximum_end < len(text):
            window = text[start:maximum_end]
            minimum = max(1, size // 2)
            for separator in ("\n\n", "\n", ". ", " "):
                boundary = window.rfind(separator, minimum)
                if boundary >= 0:
                    end = start + boundary + len(separator)
                    break
        piece = text[start:end].strip()
        if piece:
            actual_start = text.find(piece, start, end)
            chunks.append((piece, actual_start, actual_start + len(piece)))
        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap)
        start = next_start
    return chunks


def _normalize_text(text: str) -> str:
    return "\n".join(
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ).strip()


def _source_format(path: str) -> str:
    suffix = path.rsplit(".", 1)
    return suffix[-1].lower() if len(suffix) == 2 else "text"


CHUNKER_REGISTRY: dict[str, Chunker] = {
    "document": _document_chunks,
    "recursive_text": _recursive_text_chunks,
    "markdown_sections": _markdown_section_chunks,
    "fixed_tokens": _fixed_token_chunks,
}

