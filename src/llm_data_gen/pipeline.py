from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .chunking import chunk_document, chunk_documents
from .client import OpenAICompatibleClient
from .config import AppConfig, RunConfig, config_hash
from .formats import resolve_format
from .jobs import expand_jobs
from .languages import resolve_language
from .loader import load_source_document
from .models import (
    ParsedExample,
    RejectedRecord,
    RunManifest,
    SFTRecord,
    SkippedExample,
)
from .output import DatasetWriter, append_jsonl, append_skipped_jsonl, load_existing_example_ids
from .parsing import parse_generated_output, parse_model_output
from .prompt_packs import resolve_recipes
from .prompting import compose_prompt
from .prompts import PROMPT_VERSION, build_messages, families
from .readers import read_corpus
from .validation import (
    normalized_message_signature,
    second_pass_validate,
    validate_generated_example,
)


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
        for language in recipe.languages:
            resolve_language(language)
    return recipes


def inspect_run(config: RunConfig, *, search_root: Path | None = None) -> dict[str, Any]:
    recipes = validate_config_semantics(config, search_root=search_root)
    sources, source_failures = read_corpus(config.input)
    chunks = chunk_documents(sources, config.chunking)
    jobs = expand_jobs(chunks, recipes)
    sizes = [len(chunk.text) for chunk in chunks]
    by_format: dict[str, int] = {}
    by_language: dict[str, int] = {}
    for job in jobs:
        by_format[job.qa_format] = by_format.get(job.qa_format, 0) + 1
        by_language[job.language] = by_language.get(job.language, 0) + 1
    return {
        "config_name": config.name,
        "config_hash": config_hash(config),
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
        "jobs_by_language": by_language,
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
    recipes = validate_config_semantics(config, search_root=search_root)
    sources, source_failures = read_corpus(config.input)
    chunks = chunk_documents(sources, config.chunking)
    jobs = expand_jobs(chunks, recipes)
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    digest = config_hash(config)
    writer = DatasetWriter(config, digest)
    started_at = _now()
    manifest = RunManifest(
        config_name=config.name,
        config_hash=digest,
        run_status="running",
        started_at=started_at,
        resumed=writer.resumed,
        endpoint_profile=config.endpoint.profile,
        inference_backend=config.endpoint.backend,
        generator_model=config.endpoint.model,
        prompt_versions={
            resolve_format(recipe.format).name: resolve_format(recipe.format).prompt_version
            for recipe in recipes
        },
        source_count=len(sources),
        source_failure_count=len(source_failures),
        chunk_count=len(chunks),
        requested_jobs=len(jobs),
    )
    writer.initialize(manifest, sources, chunks)

    if not writer.resumed:
        for failure in source_failures:
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
        for job in jobs:
            if job.job_id in terminal:
                continue
            chunk = chunks_by_id[job.chunk_id]
            prompt_messages, prompt_hash = compose_prompt(chunk, job)
            parameters = {
                "temperature": config.endpoint.temperature,
                "top_p": config.endpoint.top_p,
                **({"max_tokens": config.endpoint.max_tokens} if config.endpoint.max_tokens else {}),
                **job.settings,
            }
            raw = ""
            try:
                raw = inference.generate(prompt_messages, parameters)
            except Exception as exc:
                _reject(
                    writer,
                    job,
                    chunk,
                    "generation",
                    f"{type(exc).__name__}: {exc}",
                    raw=None,
                )
                continue
            try:
                parsed = parse_generated_output(raw)
            except Exception as exc:
                _reject(
                    writer,
                    job,
                    chunk,
                    "parsing",
                    f"{type(exc).__name__}: {exc}",
                    raw=raw,
                )
                continue
            if parsed.status == "not_applicable":
                _reject(
                    writer,
                    job,
                    chunk,
                    "not_applicable",
                    parsed.reason or "source does not support this format",
                    raw=raw,
                    outcome="not_applicable",
                )
                continue

            errors = validate_generated_example(parsed, job, chunk, config.validation)
            if errors:
                _reject(
                    writer,
                    job,
                    chunk,
                    "validation",
                    "; ".join(errors),
                    raw=raw,
                )
                continue

            signature = normalized_message_signature(parsed)
            if config.validation.reject_duplicates and signature in signatures:
                _reject(
                    writer,
                    job,
                    chunk,
                    "duplicate",
                    "normalized messages duplicate an accepted example",
                    raw=raw,
                    duplicate_of=signatures[signature],
                )
                continue

            generated_at = _now()
            example_digest = hashlib.sha256(
                f"{job.job_id}::{signature}".encode("utf-8")
            ).hexdigest()
            record = SFTRecord(
                example_id=f"sha256:{example_digest}",
                job_id=job.job_id,
                source_id=chunk.source_id,
                chunk_id=chunk.chunk_id,
                domain=chunk.domain,
                qa_format=job.qa_format,
                language=job.language,
                messages=parsed.messages,
                evidence=parsed.evidence,
                prompt_name=job.prompt_name,
                prompt_version=job.prompt_version,
                prompt_hash=prompt_hash,
                generator_model=config.endpoint.model,
                inference_backend=config.endpoint.backend,
                endpoint_profile=config.endpoint.profile,
                generation_parameters={
                    **parameters,
                    "sample_index": job.sample_index,
                    "recipe_hash": job.recipe_hash,
                    **({"max_turns": job.max_turns} if job.max_turns else {}),
                },
                generated_at=generated_at,
                metadata=parsed.metadata,
            )
            writer.append_record(record)
            signatures[signature] = record.example_id
            generated.append(record)

        summary = writer.summarize()
        manifest = manifest.model_copy(
            update={
                "run_status": "completed",
                "completed_at": _now(),
                "accepted_jobs": summary["accepted"],
                "rejected_jobs": summary["rejected"],
                "not_applicable_jobs": summary["not_applicable"],
                "accepted_by_format": summary["accepted_by_format"],
                "accepted_by_language": summary["accepted_by_language"],
                "accepted_by_source": summary["accepted_by_source"],
                "failures_by_stage": summary["failures_by_stage"],
            }
        )
        writer.write_manifest(manifest)
    except Exception:
        writer.write_manifest(
            manifest.model_copy(update={"run_status": "failed", "completed_at": _now()})
        )
        raise
    return generated


def _reject(
    writer: DatasetWriter,
    job,
    chunk,
    stage: str,
    reason: str,
    *,
    raw: str | None,
    outcome: str = "rejected",
    duplicate_of: str | None = None,
) -> None:
    writer.append_rejection(
        RejectedRecord(
            job_id=job.job_id,
            source_id=chunk.source_id,
            chunk_id=chunk.chunk_id,
            qa_format=job.qa_format,
            language=job.language,
            failure_stage=stage,
            failure_reason=reason,
            raw_output=raw,
            duplicate_of=duplicate_of,
            provenance={
                "source_path": chunk.provenance_path,
                "source_checksum": chunk.source_checksum,
                "chunk_strategy": chunk.strategy,
                "chunk_index": chunk.chunk_index,
            },
            recorded_at=_now(),
        ),
        outcome=outcome,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_pipeline(
    config: AppConfig,
    client: OpenAICompatibleClient | None = None,
) -> list[ParsedExample]:
    source = load_source_document(config.input_path, config.source_id)
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
