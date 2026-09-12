from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .chunking import chunk_document, chunk_documents
from .client import OpenAICompatibleClient
from .config import AppConfig, RunConfig, config_hash
from .execution import (
    execute_job,
    finalize_run,
    initial_manifest,
    prepare_run,
    validate_source_language_chain,
)
from .formats import resolve_format
from .jobs import expand_jobs
from .loader import load_source_document
from .models import (
    ParsedExample,
    RejectedRecord,
    RunManifest,
    SFTRecord,
    SkippedExample,
)
from .output import DatasetWriter, append_jsonl, append_skipped_jsonl, load_existing_example_ids
from .parsing import parse_model_output
from .prompt_packs import resolve_recipes
from .prompts import PROMPT_VERSION, build_messages, families
from .readers import read_corpus
from .validation import second_pass_validate


def validate_config_semantics(
    config: RunConfig,
    *,
    search_root: Path | None = None,
):
    recipes = resolve_recipes(config.generation, search_root=search_root)
    if not recipes:
        raise ValueError("resolved generation recipe list is empty")
    for recipe in recipes:
        definition = resolve_format(recipe.format)
        if recipe.max_turns is not None and definition.name != "multi_turn":
            raise ValueError("max_turns is only supported by multi_turn")
        common_settings = {"temperature", "top_p", "max_tokens"}
        unsupported = set(recipe.settings) - common_settings - set(definition.supported_settings)
        if unsupported:
            raise ValueError(
                f"unsupported settings for {definition.name}: {sorted(unsupported)}"
            )
    return recipes


def inspect_run(config: RunConfig, *, search_root: Path | None = None) -> dict[str, Any]:
    recipes = validate_config_semantics(config, search_root=search_root)
    sources, source_failures = read_corpus(config.input)
    chunks = chunk_documents(sources, config.chunking)
    jobs = expand_jobs(chunks, recipes)
    validate_source_language_chain(sources, chunks, jobs)
    sizes = [len(chunk.text) for chunk in chunks]
    by_format: dict[str, int] = {}
    sources_by_language: dict[str, int] = {}
    chunks_by_language: dict[str, int] = {}
    for source in sources:
        sources_by_language[source.language] = sources_by_language.get(source.language, 0) + 1
    for chunk in chunks:
        chunks_by_language[chunk.language] = chunks_by_language.get(chunk.language, 0) + 1
    for job in jobs:
        by_format[job.qa_format] = by_format.get(job.qa_format, 0) + 1
    return {
        "config_name": config.name,
        "config_hash": config_hash(config, recipes),
        "source_count": len(sources),
        "source_failure_count": len(source_failures),
        "chunk_count": len(chunks),
        "chunk_size": {
            "minimum": min(sizes) if sizes else 0,
            "maximum": max(sizes) if sizes else 0,
            "average": round(sum(sizes) / len(sizes), 2) if sizes else 0,
        },
        "chunking": config.chunking.model_dump(mode="json"),
        "endpoint": {
            "profile": config.endpoint.profile,
            "backend": config.endpoint.backend,
            "base_url": config.endpoint.base_url,
            "model": config.endpoint.model,
        },
        "recipe_count": len(recipes),
        "recipes": [recipe.model_dump(mode="json") for recipe in recipes],
        "job_count": len(jobs),
        "jobs_by_format": by_format,
        "sources_by_language": sources_by_language,
        "chunks_by_language": chunks_by_language,
        "output_directory": str(config.output.directory),
        "resume": config.output.resume,
    }


def run_config_pipeline(
    config: RunConfig,
    *,
    search_root: Path | None = None,
    client: OpenAICompatibleClient | None = None,
    retry_stages: set[str] | None = None,
) -> list[SFTRecord]:
    plan = prepare_run(config, search_root=search_root)
    writer = DatasetWriter(config, plan.config_hash)
    manifest = initial_manifest(plan, resumed=writer.resumed)
    writer.initialize(manifest, list(plan.sources), list(plan.chunks))

    if not writer.resumed:
        for failure in plan.source_failures:
            writer.append_rejection(
                RejectedRecord(
                    failure_stage=failure.failure_stage,
                    failure_reason=failure.failure_reason,
                    provenance={
                        "source_path": failure.source_path,
                        "record_index": failure.record_index,
                    },
                    recorded_at=_now(),
                )
            )

    terminal = writer.terminal_job_ids() if config.output.resume else set()
    terminal -= writer.retryable_job_ids(retry_stages or set())
    signatures = writer.accepted_signatures()
    inference = client or OpenAICompatibleClient(config.endpoint)
    generated: list[SFTRecord] = []

    try:
        for job in plan.jobs:
            if job.job_id in terminal:
                continue
            outcome = execute_job(plan, job.job_id, inference)
            if outcome.outcome == "accepted" and outcome.record and outcome.signature:
                if (
                    config.validation.reject_duplicates
                    and outcome.signature in signatures
                ):
                    duplicate = outcome.rejection or RejectedRecord(
                        job_id=job.job_id,
                        source_id=outcome.record.source_id,
                        chunk_id=outcome.record.chunk_id,
                        qa_format=job.qa_format,
                        language=job.language,
                        failure_stage="duplicate",
                        failure_reason="normalized messages duplicate an accepted example",
                        raw_output=outcome.raw_output,
                        duplicate_of=signatures[outcome.signature],
                        provenance={
                            "source_path": plan.chunk(job.chunk_id).provenance_path,
                            "source_checksum": plan.chunk(job.chunk_id).source_checksum,
                            "chunk_strategy": plan.chunk(job.chunk_id).strategy,
                            "chunk_index": plan.chunk(job.chunk_id).chunk_index,
                        },
                        recorded_at=outcome.recorded_at,
                    )
                    writer.append_rejection(duplicate)
                    continue
                writer.append_record(outcome.record)
                signatures[outcome.signature] = outcome.record.example_id
                generated.append(outcome.record)
                continue
            if outcome.rejection:
                writer.append_rejection(outcome.rejection, outcome=outcome.outcome)

        manifest = finalize_run(plan)
        writer.write_manifest(manifest)
    except Exception:
        writer.write_manifest(
            manifest.model_copy(update={"run_status": "failed", "completed_at": _now()})
        )
        raise
    return generated


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_pipeline(
    config: AppConfig,
    client: OpenAICompatibleClient | None = None,
) -> list[ParsedExample]:
    source = load_source_document(
        config.input_path, config.source_id, source_language=config.source_language
    )
    chunks = chunk_document(source, config.chunk_size)
    client = client or OpenAICompatibleClient(config.backend)
    existing_ids = load_existing_example_ids(config.output_path) if config.resume else set()

    generated: list[ParsedExample] = []
    skipped: list[SkippedExample] = []
    now = _now()
    for chunk in chunks:
        for family in families():
            example_id = f"{chunk.chunk_id}::{family.value}"
            if example_id in existing_ids:
                continue
            raw = ""
            try:
                raw = client.generate(build_messages(chunk, family))
                parsed = parse_model_output(raw)
                passed, note = second_pass_validate(
                    parsed, chunk, expected_task_type=family.value
                )
                row = ParsedExample(
                    example_id=example_id,
                    source_id=source.source_id,
                    chunk_id=chunk.chunk_id,
                    prompt_template_id=family.value,
                    task_type=parsed.task_type,
                    input_text=parsed.input_text,
                    output_text=parsed.output_text,
                    model_name=config.backend.model,
                    backend_name=config.backend.name,
                    backend_base_url=config.backend.base_url,
                    prompt_version=PROMPT_VERSION,
                    generated_at=now,
                    validation_status="passed" if passed else "failed",
                    validation_notes=note,
                    provenance={
                        "source_path": source.provenance_path,
                        "chunk_index": chunk.chunk_index,
                        "source_format": chunk.source_format,
                        "source_url": source.source_url or "",
                    },
                )
                if passed:
                    generated.append(row)
                else:
                    skipped.append(
                        SkippedExample(
                            example_id=example_id,
                            source_id=source.source_id,
                            chunk_id=chunk.chunk_id,
                            prompt_template_id=family.value,
                            task_type=parsed.task_type,
                            failure_stage="validation",
                            failure_reason=note or "validation failed",
                            raw_output=raw,
                            provenance=row.provenance,
                        )
                    )
            except Exception as exc:
                skipped.append(
                    SkippedExample(
                        example_id=example_id,
                        source_id=source.source_id,
                        chunk_id=chunk.chunk_id,
                        prompt_template_id=family.value,
                        failure_stage="generate_or_parse",
                        failure_reason=f"{type(exc).__name__}: {exc}",
                        raw_output=raw or None,
                        provenance={
                            "source_path": source.provenance_path,
                            "chunk_index": chunk.chunk_index,
                            "source_format": chunk.source_format,
                            "source_url": source.source_url or "",
                        },
                    )
                )

    if generated:
        append_jsonl(config.output_path, generated)
    if skipped:
        append_skipped_jsonl(config.output_path.with_suffix(".skipped.jsonl"), skipped)
    return generated
