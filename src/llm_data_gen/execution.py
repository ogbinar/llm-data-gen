from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from .chunking import chunk_documents
from .client import OpenAICompatibleClient
from .config import GenerationRecipe, RunConfig, config_hash
from .formats import resolve_format
from .jobs import expand_jobs
from .models import (
    Chunk,
    GeneratedExample,
    GenerationJob,
    RejectedRecord,
    RunManifest,
    SFTRecord,
    SourceDocument,
    SourceFailure,
)
from .parsing import parse_generated_output
from .prompt_packs import resolve_recipes
from .prompting import compose_prompt
from .readers import read_corpus
from .validation import normalized_message_signature, validate_generated_example


class RunPlan(BaseModel):
    """A complete, serializable description of deterministic V2 work."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["run_plan_v1"] = "run_plan_v1"
    run_id: str
    config_hash: str
    created_at: str
    config: RunConfig
    recipes: tuple[GenerationRecipe, ...]
    sources: tuple[SourceDocument, ...]
    source_failures: tuple[SourceFailure, ...]
    chunks: tuple[Chunk, ...]
    jobs: tuple[GenerationJob, ...]
    chunks_hash: str
    jobs_hash: str
    sources_hash: str

    def job(self, job_id: str) -> GenerationJob:
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        raise ValueError(f"job {job_id!r} is not part of run {self.run_id!r}")

    def chunk(self, chunk_id: str) -> Chunk:
        for chunk in self.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk
        raise ValueError(f"chunk {chunk_id!r} is not part of run {self.run_id!r}")


class JobOutcome(BaseModel):
    """Terminal application outcome owned by exactly one generation job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["job_outcome_v1"] = "job_outcome_v1"
    run_id: str
    config_hash: str
    job_id: str
    outcome: Literal["accepted", "rejected", "not_applicable"]
    recorded_at: str
    attempt_count: int = 1
    signature: str | None = None
    record: SFTRecord | None = None
    rejection: RejectedRecord | None = None
    raw_output: str | None = None

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> "JobOutcome":
        if self.outcome == "accepted" and (not self.record or not self.signature):
            raise ValueError("accepted job outcome requires record and signature")
        if self.outcome != "accepted" and not self.rejection:
            raise ValueError("non-accepted job outcome requires a rejection")
        if self.outcome == "accepted" and self.rejection is not None:
            raise ValueError("accepted job outcome cannot contain a rejection")
        if self.outcome != "accepted" and (self.record is not None or self.signature is not None):
            raise ValueError("non-accepted job outcome cannot contain a record or signature")
        return self


class TransientGenerationError(Exception):
    """Signals an inference failure that a queue adapter may retry."""

    def __init__(self, original: Exception):
        super().__init__(str(original))
        self.original = original


def prepare_run(config: RunConfig, *, search_root: Path | None = None) -> RunPlan:
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

    sources, source_failures = read_corpus(config.input)
    chunks = chunk_documents(sources, config.chunking)
    jobs = expand_jobs(chunks, recipes)
    validate_source_language_chain(sources, chunks, jobs)
    digest = config_hash(config, recipes)
    created_at = now_utc()
    plan = RunPlan(
        run_id=f"{_slug(config.name)}-{digest[:12]}",
        config_hash=digest,
        created_at=created_at,
        config=config,
        recipes=tuple(recipes),
        sources=tuple(sources),
        source_failures=tuple(source_failures),
        chunks=tuple(chunks),
        jobs=tuple(jobs),
        chunks_hash=sequence_hash(chunks),
        jobs_hash=sequence_hash(jobs),
        sources_hash=sequence_hash(sources),
    )
    return plan


def initial_manifest(plan: RunPlan, *, resumed: bool = False) -> RunManifest:
    return RunManifest(
        config_name=plan.config.name,
        config_hash=plan.config_hash,
        run_status="running",
        started_at=plan.created_at,
        resumed=resumed,
        endpoint_profile=plan.config.endpoint.profile,
        inference_backend=plan.config.endpoint.backend,
        generator_model=plan.config.endpoint.model,
        prompt_versions={
            resolve_format(recipe.format).name: resolve_format(recipe.format).prompt_version
            for recipe in plan.recipes
        },
        source_count=len(plan.sources),
        source_failure_count=len(plan.source_failures),
        chunk_count=len(plan.chunks),
        requested_jobs=len(plan.jobs),
    )


def execute_job(
    run_plan: RunPlan,
    job_id: str,
    client: OpenAICompatibleClient,
    *,
    raise_transient: bool = False,
    attempt_count: int = 1,
) -> JobOutcome:
    validate_run_plan(run_plan)
    job = run_plan.job(job_id)
    chunk = run_plan.chunk(job.chunk_id)
    prompt_messages, prompt_hash = compose_prompt(chunk, job)
    config = run_plan.config
    parameters = {
        "temperature": config.endpoint.temperature,
        "top_p": config.endpoint.top_p,
        **({"max_tokens": config.endpoint.max_tokens} if config.endpoint.max_tokens else {}),
        **job.settings,
    }
    try:
        raw = client.generate(prompt_messages, parameters)
    except Exception as exc:
        if raise_transient and is_transient_error(exc):
            raise TransientGenerationError(exc) from exc
        return rejected_outcome(
            run_plan,
            job,
            chunk,
            "generation",
            f"{type(exc).__name__}: {exc}",
            raw=None,
            attempt_count=attempt_count,
        )
    try:
        parsed = parse_generated_output(raw)
    except Exception as exc:
        return rejected_outcome(
            run_plan,
            job,
            chunk,
            "parsing",
            f"{type(exc).__name__}: {exc}",
            raw=raw,
            attempt_count=attempt_count,
        )
    if parsed.qa_format != job.qa_format:
        return rejected_outcome(
            run_plan,
            job,
            chunk,
            "validation",
            f"format mismatch: expected {job.qa_format!r}, got {parsed.qa_format!r}",
            raw=raw,
            attempt_count=attempt_count,
        )
    if parsed.status == "not_applicable":
        return rejected_outcome(
            run_plan,
            job,
            chunk,
            "not_applicable",
            parsed.reason or "source does not support this format",
            raw=raw,
            outcome="not_applicable",
            attempt_count=attempt_count,
        )
    errors = validate_generated_example(parsed, job, chunk, config.validation)
    if errors:
        return rejected_outcome(
            run_plan,
            job,
            chunk,
            "validation",
            "; ".join(errors),
            raw=raw,
            attempt_count=attempt_count,
        )

    signature = normalized_message_signature(parsed)
    generated_at = now_utc()
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
        language=chunk.language,
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
            "source_language_origin": chunk.language_origin,
            **({"max_turns": job.max_turns} if job.max_turns else {}),
        },
        generated_at=generated_at,
        metadata=parsed.metadata,
    )
    return JobOutcome(
        run_id=run_plan.run_id,
        config_hash=run_plan.config_hash,
        job_id=job.job_id,
        outcome="accepted",
        recorded_at=generated_at,
        attempt_count=attempt_count,
        signature=signature,
        record=record,
        raw_output=raw,
    )


def rejected_outcome(
    plan: RunPlan,
    job: GenerationJob,
    chunk: Chunk,
    stage: str,
    reason: str,
    *,
    raw: str | None,
    outcome: str = "rejected",
    attempt_count: int = 1,
    duplicate_of: str | None = None,
    recorded_at: str | None = None,
) -> JobOutcome:
    timestamp = recorded_at or now_utc()
    _prompt_messages, prompt_hash = compose_prompt(chunk, job)
    rejection = RejectedRecord(
        job_id=job.job_id,
        source_id=chunk.source_id,
        chunk_id=chunk.chunk_id,
        qa_format=job.qa_format,
        language=job.language,
        language_origin=job.language_origin,
        prompt_name=job.prompt_name,
        prompt_version=job.prompt_version,
        prompt_hash=prompt_hash,
        generator_model=plan.config.endpoint.model,
        inference_backend=plan.config.endpoint.backend,
        endpoint_profile=plan.config.endpoint.profile,
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
        recorded_at=timestamp,
    )
    return JobOutcome(
        run_id=plan.run_id,
        config_hash=plan.config_hash,
        job_id=job.job_id,
        outcome=outcome,
        recorded_at=timestamp,
        attempt_count=attempt_count,
        rejection=rejection,
        raw_output=raw,
    )


def validate_job_outcome(plan: RunPlan, outcome: JobOutcome) -> None:
    """Bind a terminal outcome to its immutable plan and revalidate its payload."""

    validate_run_plan(plan)
    if outcome.run_id != plan.run_id or outcome.config_hash != plan.config_hash:
        raise ValueError("job outcome run/config identity mismatch")
    job = plan.job(outcome.job_id)
    chunk = plan.chunk(job.chunk_id)
    expected_provenance = {
        "source_path": chunk.provenance_path,
        "source_checksum": chunk.source_checksum,
        "chunk_strategy": chunk.strategy,
        "chunk_index": chunk.chunk_index,
    }
    if outcome.attempt_count < 1:
        raise ValueError("job outcome attempt count must be positive")

    if outcome.outcome == "accepted":
        record = outcome.record
        assert record is not None and outcome.signature is not None
        prompt_messages, prompt_hash = compose_prompt(chunk, job)
        del prompt_messages
        expected_parameters = {
            "temperature": plan.config.endpoint.temperature,
            "top_p": plan.config.endpoint.top_p,
            **(
                {"max_tokens": plan.config.endpoint.max_tokens}
                if plan.config.endpoint.max_tokens
                else {}
            ),
            **job.settings,
            "sample_index": job.sample_index,
            "recipe_hash": job.recipe_hash,
            "source_language_origin": chunk.language_origin,
            **({"max_turns": job.max_turns} if job.max_turns else {}),
        }
        identities = {
            "job_id": (record.job_id, job.job_id),
            "source_id": (record.source_id, chunk.source_id),
            "chunk_id": (record.chunk_id, chunk.chunk_id),
            "domain": (record.domain, chunk.domain),
            "qa_format": (record.qa_format, job.qa_format),
            "language": (record.language, chunk.language),
            "prompt_name": (record.prompt_name, job.prompt_name),
            "prompt_version": (record.prompt_version, job.prompt_version),
            "prompt_hash": (record.prompt_hash, prompt_hash),
            "generator_model": (record.generator_model, plan.config.endpoint.model),
            "inference_backend": (
                record.inference_backend,
                plan.config.endpoint.backend,
            ),
            "endpoint_profile": (
                record.endpoint_profile,
                plan.config.endpoint.profile,
            ),
            "generation_parameters": (
                record.generation_parameters,
                expected_parameters,
            ),
        }
        for name, (actual, expected) in identities.items():
            if actual != expected:
                raise ValueError(f"job outcome {name} identity mismatch")
        generated = GeneratedExample(
            qa_format=record.qa_format,
            messages=record.messages,
            evidence=record.evidence,
            metadata=record.metadata,
        )
        errors = validate_generated_example(
            generated, job, chunk, plan.config.validation
        )
        if errors:
            raise ValueError("job outcome validation failed: " + "; ".join(errors))
        signature = normalized_message_signature(generated)
        if outcome.signature != signature:
            raise ValueError("job outcome normalized message signature mismatch")
        expected_example_id = "sha256:" + hashlib.sha256(
            f"{job.job_id}::{signature}".encode("utf-8")
        ).hexdigest()
        if record.example_id != expected_example_id:
            raise ValueError("job outcome example identity mismatch")
        if record.generated_at != outcome.recorded_at:
            raise ValueError("job outcome generated timestamp mismatch")
        return

    rejection = outcome.rejection
    assert rejection is not None
    identities = {
        "job_id": (rejection.job_id, job.job_id),
        "source_id": (rejection.source_id, chunk.source_id),
        "chunk_id": (rejection.chunk_id, chunk.chunk_id),
        "qa_format": (rejection.qa_format, job.qa_format),
        "language": (rejection.language, job.language),
        "language_origin": (rejection.language_origin, job.language_origin),
        "prompt_name": (rejection.prompt_name, job.prompt_name),
        "prompt_version": (rejection.prompt_version, job.prompt_version),
        "prompt_hash": (rejection.prompt_hash, compose_prompt(chunk, job)[1]),
        "generator_model": (
            rejection.generator_model,
            plan.config.endpoint.model,
        ),
        "inference_backend": (
            rejection.inference_backend,
            plan.config.endpoint.backend,
        ),
        "endpoint_profile": (
            rejection.endpoint_profile,
            plan.config.endpoint.profile,
        ),
        "provenance": (rejection.provenance, expected_provenance),
        "recorded_at": (rejection.recorded_at, outcome.recorded_at),
        "raw_output": (rejection.raw_output, outcome.raw_output),
    }
    for name, (actual, expected) in identities.items():
        if actual != expected:
            raise ValueError(f"job outcome rejection {name} identity mismatch")

    if outcome.raw_output is None:
        if outcome.outcome != "rejected" or rejection.failure_stage != "generation":
            raise ValueError(
                "raw-less job outcome must be a rejected generation failure"
            )
        if not rejection.failure_reason.strip():
            raise ValueError("generation rejection requires a failure reason")
        exhaustion_prefix = (
            "transient failure exhausted after "
            f"{outcome.attempt_count} attempts across lifetime: "
        )
        if rejection.failure_reason.startswith("transient failure exhausted after "):
            if not rejection.failure_reason.startswith(exhaustion_prefix):
                raise ValueError(
                    "generation rejection retry count is inconsistent with outcome"
                )
            if not rejection.failure_reason.removeprefix(exhaustion_prefix).strip():
                raise ValueError("generation rejection requires a terminal diagnostic")
        return

    expected_outcome: Literal["rejected", "not_applicable"]
    expected_stage: str
    expected_reason: str
    try:
        parsed = parse_generated_output(outcome.raw_output)
    except Exception as exc:
        expected_outcome = "rejected"
        expected_stage = "parsing"
        expected_reason = f"{type(exc).__name__}: {exc}"
    else:
        if parsed.qa_format != job.qa_format:
            expected_outcome = "rejected"
            expected_stage = "validation"
            expected_reason = (
                f"format mismatch: expected {job.qa_format!r}, "
                f"got {parsed.qa_format!r}"
            )
        elif parsed.status == "not_applicable":
            expected_outcome = "not_applicable"
            expected_stage = "not_applicable"
            expected_reason = parsed.reason or "source does not support this format"
        else:
            errors = validate_generated_example(
                parsed, job, chunk, plan.config.validation
            )
            if not errors:
                raise ValueError(
                    "non-accepted job outcome contains valid generated output"
                )
            expected_outcome = "rejected"
            expected_stage = "validation"
            expected_reason = "; ".join(errors)

    if outcome.outcome != expected_outcome:
        raise ValueError("job outcome rejection status is inconsistent with raw output")
    if rejection.failure_stage != expected_stage:
        raise ValueError("job outcome rejection failure stage is inconsistent with raw output")
    if rejection.failure_reason != expected_reason:
        raise ValueError("job outcome rejection reason is inconsistent with raw output")


def finalize_run(
    run_plan: RunPlan,
    manifest: RunManifest | None = None,
    summary: dict | None = None,
    *,
    completed_at: str | None = None,
) -> RunManifest:
    """Build the common completed manifest used by both execution paths."""

    validate_run_plan(run_plan)
    if manifest is None:
        manifest_path = run_plan.config.output.directory / "manifest.json"
        manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    if summary is None:
        from .output import DatasetWriter

        summary = DatasetWriter(run_plan.config, run_plan.config_hash).summarize()
    return manifest.model_copy(
        update={
            "run_status": "completed",
            "completed_at": completed_at or now_utc(),
            "accepted_jobs": summary["accepted"],
            "rejected_jobs": summary["rejected"],
            "not_applicable_jobs": summary["not_applicable"],
            "accepted_by_format": summary["accepted_by_format"],
            "accepted_by_language": summary["accepted_by_language"],
            "accepted_by_source": summary["accepted_by_source"],
            "failures_by_stage": summary["failures_by_stage"],
        }
    )


def validate_run_plan(plan: RunPlan) -> None:
    if plan.config_hash != config_hash(plan.config, list(plan.recipes)):
        raise ValueError("run snapshot/config hash mismatch")
    if plan.chunks_hash != sequence_hash(plan.chunks):
        raise ValueError("run snapshot/chunk hash mismatch")
    if plan.jobs_hash != sequence_hash(plan.jobs):
        raise ValueError("run snapshot/job hash mismatch")
    if plan.sources_hash != sequence_hash(plan.sources):
        raise ValueError("run snapshot/source hash mismatch")
    validate_source_language_chain(plan.sources, plan.chunks, plan.jobs)


def validate_source_language_chain(
    sources: Iterable[SourceDocument],
    chunks: Iterable[Chunk],
    jobs: Iterable[GenerationJob],
) -> None:
    sources_by_id = {source.source_id: source for source in sources}
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    for chunk in chunks:
        source = sources_by_id[chunk.source_id]
        if (chunk.language, chunk.language_origin) != (
            source.language,
            source.language_origin,
        ):
            raise ValueError(f"source language provenance mismatch for chunk {chunk.chunk_id}")
    for job in jobs:
        chunk = chunks_by_id[job.chunk_id]
        if (job.language, job.language_origin) != (
            chunk.language,
            chunk.language_origin,
        ):
            raise ValueError(f"source language provenance mismatch for job {job.job_id}")


def is_transient_error(exc: Exception) -> bool:
    import httpx

    if isinstance(exc, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status <= 599
    return False


def sequence_hash(items: Iterable[BaseModel]) -> str:
    payload = [item.model_dump(mode="json") for item in items]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    slug = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in slug.split("-") if part)[:48] or "run"
