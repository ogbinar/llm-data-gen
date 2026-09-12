from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .client import OpenAICompatibleClient
from .execution import (
    JobOutcome,
    RunPlan,
    TransientGenerationError,
    execute_job,
    finalize_run,
    initial_manifest,
    now_utc,
    prepare_run,
    rejected_outcome,
    validate_job_outcome,
    validate_run_plan,
)
from .models import CheckpointRecord, RejectedRecord, RunManifest
from .output import DatasetWriter, write_jsonl_atomic, write_text_atomic


MAX_TRANSIENT_RETRIES = 2
MAX_LIFETIME_ATTEMPTS = MAX_TRANSIENT_RETRIES + 1
RETRY_DELAYS_SECONDS = (0.25, 0.5)


def queue_database_path() -> Path:
    configured = os.getenv("LLM_DATA_GEN_QUEUE_DB")
    return Path(configured or ".llm-data-gen/huey.db").expanduser().resolve()


def prepare_queued_run(config, *, search_root: Path | None = None) -> RunPlan:
    current = prepare_run(config, search_root=search_root)
    output = config.output.directory
    snapshot_path = output / "work" / "run-plan.json"
    if snapshot_path.exists():
        existing = load_run_plan(output)
        _assert_same_snapshot(existing, current)
        plan = existing
    else:
        plan = current
        _persist_snapshot(plan)
    _register_run(plan)
    return plan


def load_run_plan(run: str | Path) -> RunPlan:
    directory = resolve_run_directory(run)
    path = directory / "work" / "run-plan.json"
    if not path.exists():
        raise ValueError(f"queued run snapshot not found: {path}")
    plan = RunPlan.model_validate_json(path.read_text(encoding="utf-8"))
    if plan.config.output.directory.resolve() != directory.resolve():
        raise ValueError("run snapshot/output directory mismatch")
    validate_run_plan(plan)
    return plan


def resolve_run_directory(run: str | Path) -> Path:
    candidate = Path(run).expanduser()
    if candidate.exists() or (candidate / "work" / "run-plan.json").exists():
        return candidate.resolve()
    pointer = _registry_directory() / f"{_safe_id(str(run))}.json"
    if not pointer.exists():
        raise ValueError(f"unknown queued run {str(run)!r}")
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    if payload.get("run_id") != str(run):
        raise ValueError("queue run registry identity mismatch")
    return Path(payload["output_directory"]).resolve()


def task_payload(plan: RunPlan, job_id: str) -> dict[str, str]:
    plan.job(job_id)
    return {
        "run_id": plan.run_id,
        "config_hash": plan.config_hash,
        "job_id": job_id,
    }


def unfinished_job_ids(plan: RunPlan) -> list[str]:
    return [job.job_id for job in plan.jobs if not outcome_path(plan, job.job_id).exists()]


def mark_enqueued(plan: RunPlan, job_ids: list[str]) -> None:
    """Record explicit enqueue/reconciliation in application state.

    Attempt counts come from durable attempt files and are never reset by
    reconciliation.
    """
    for job_id in job_ids:
        if not outcome_path(plan, job_id).exists():
            _write_state(
                plan,
                job_id,
                "pending",
                attempt_count=len(read_attempts(plan, job_id)),
            )
    write_queue_state(plan)


def execute_queued_job(
    run_id: str,
    expected_config_hash: str,
    job_id: str,
    *,
    client_factory: Callable[[object], OpenAICompatibleClient] = OpenAICompatibleClient,
    sleep: Callable[[float], None] = time.sleep,
) -> JobOutcome:
    plan = load_run_plan(run_id)
    if plan.config_hash != expected_config_hash:
        raise ValueError("queued task/config hash mismatch")
    plan.job(job_id)
    with _job_lock(plan, job_id):
        existing = read_outcome(plan, job_id)
        if existing:
            return existing
        inference = client_factory(plan.config.endpoint)
        attempts = read_attempts(plan, job_id)
        if len(attempts) >= MAX_LIFETIME_ATTEMPTS:
            outcome = _exhausted_outcome(plan, job_id, attempts)
            write_outcome(plan, outcome)
            _write_state(
                plan, job_id, "terminal", attempt_count=MAX_LIFETIME_ATTEMPTS
            )
            write_queue_state(plan)
            return outcome
        while len(attempts) < MAX_LIFETIME_ATTEMPTS:
            attempt = len(attempts) + 1
            _start_attempt(plan, job_id, attempt)
            _write_state(plan, job_id, "running", attempt_count=attempt)
            try:
                outcome = execute_job(
                    plan,
                    job_id,
                    inference,
                    raise_transient=True,
                    attempt_count=attempt,
                )
            except TransientGenerationError as exc:
                reason = redact_secrets(
                    f"{type(exc.original).__name__}: {exc.original}", plan
                )
                _finish_attempt(plan, job_id, attempt, "transient_error", reason)
                attempts = read_attempts(plan, job_id)
                if attempt < MAX_LIFETIME_ATTEMPTS:
                    _write_state(
                        plan,
                        job_id,
                        "retrying",
                        attempt_count=attempt,
                        error=reason,
                    )
                    sleep(RETRY_DELAYS_SECONDS[attempt - 1])
                    continue
                outcome = _exhausted_outcome(plan, job_id, attempts)
                break
            else:
                _finish_attempt(plan, job_id, attempt, "completed", None)
                attempts = read_attempts(plan, job_id)
                outcome = outcome.model_copy(update={"attempt_count": len(attempts)})
                break
        else:  # pragma: no cover - defensive; loop exits through return/break.
            outcome = _exhausted_outcome(plan, job_id, attempts)
        outcome = _sanitize_outcome(outcome, plan)
        write_outcome(plan, outcome)
        _write_state(
            plan,
            job_id,
            "terminal",
            attempt_count=outcome.attempt_count,
        )
        write_queue_state(plan)
        return outcome


def read_outcome(plan: RunPlan, job_id: str) -> JobOutcome | None:
    path = outcome_path(plan, job_id)
    if not path.exists():
        return None
    outcome = JobOutcome.model_validate_json(path.read_text(encoding="utf-8"))
    if outcome.job_id != job_id:
        raise ValueError("job outcome identity mismatch")
    validate_job_outcome(plan, outcome)
    return outcome


def write_outcome(plan: RunPlan, outcome: JobOutcome) -> None:
    validate_job_outcome(plan, outcome)
    path = outcome_path(plan, outcome.job_id)
    content = outcome.model_dump_json() + "\n"
    _write_immutable(path, content)


def outcome_path(plan: RunPlan, job_id: str) -> Path:
    return (
        plan.config.output.directory
        / "work"
        / "outcomes"
        / plan.config_hash
        / f"{_safe_id(job_id)}.json"
    )


def queue_status(run: str | Path | RunPlan) -> dict[str, object]:
    plan = run if isinstance(run, RunPlan) else load_run_plan(run)
    counts = {name: 0 for name in ("pending", "running", "retrying", "terminal")}
    for job in plan.jobs:
        if outcome_path(plan, job.job_id).exists():
            counts["terminal"] += 1
            continue
        state = _read_state(plan, job.job_id)
        name = str(state.get("state") or "pending")
        counts[name if name in counts else "pending"] += 1
    ready = counts["terminal"] == len(plan.jobs)
    manifest_path = plan.config.output.directory / "manifest.json"
    finalized = False
    if manifest_path.exists():
        finalized = json.loads(manifest_path.read_text(encoding="utf-8")).get(
            "run_status"
        ) == "completed" and ready
    application = {
        "planned": len(plan.jobs),
        **counts,
        "ready_to_finalize": ready and not finalized,
        "finalized": finalized,
    }
    broker = _broker_status()
    return {
        "run_id": plan.run_id,
        "config_hash": plan.config_hash,
        # Keep flat application counts for CLI compatibility while making the
        # distinction explicit for operators and machine readers.
        **application,
        "application": application,
        "broker": broker,
    }


def write_queue_state(plan: RunPlan) -> dict[str, object]:
    status = queue_status(plan)
    write_text_atomic(
        plan.config.output.directory / "queue-state.json",
        json.dumps(status, indent=2, sort_keys=True) + "\n",
    )
    return status


def finalize_queued_run(
    run: str | Path | RunPlan,
    *,
    after_replace: Callable[[str], None] | None = None,
) -> RunManifest:
    plan = run if isinstance(run, RunPlan) else load_run_plan(run)
    with _finalization_lock(plan):
        return _finalize_locked(plan, after_replace=after_replace)


def _finalize_locked(
    plan: RunPlan, *, after_replace: Callable[[str], None] | None
) -> RunManifest:
    validate_run_plan(plan)
    outcomes: dict[str, JobOutcome] = {}
    for job in plan.jobs:
        outcome = read_outcome(plan, job.job_id)
        if outcome is None:
            raise ValueError(
                f"run is not ready to finalize; missing outcome for job {job.job_id}"
            )
        # read_outcome already performs full plan binding and revalidation.
        outcomes[job.job_id] = outcome

    dataset: list[dict] = []
    rejected: list[dict] = [
        RejectedRecord(
            failure_stage=failure.failure_stage,
            failure_reason=failure.failure_reason,
            provenance={
                "source_path": failure.source_path,
                "record_index": failure.record_index,
            },
            recorded_at=plan.created_at,
        ).model_dump(mode="json")
        for failure in plan.source_failures
    ]
    checkpoints: list[dict] = []
    signatures: dict[str, str] = {}
    for job in plan.jobs:
        outcome = outcomes[job.job_id]
        if outcome.outcome == "accepted" and outcome.record and outcome.signature:
            if (
                plan.config.validation.reject_duplicates
                and outcome.signature in signatures
            ):
                rejection = _duplicate_rejection(
                    plan, outcome, signatures[outcome.signature]
                )
                rejected.append(rejection.model_dump(mode="json"))
                checkpoints.append(
                    CheckpointRecord(
                        job_id=job.job_id,
                        outcome="rejected",
                        reason=rejection.failure_reason,
                        recorded_at=outcome.recorded_at,
                    ).model_dump(mode="json")
                )
            else:
                dataset.append(outcome.record.model_dump(mode="json"))
                signatures[outcome.signature] = outcome.record.example_id
                checkpoints.append(
                    CheckpointRecord(
                        job_id=job.job_id,
                        outcome="accepted",
                        example_id=outcome.record.example_id,
                        recorded_at=outcome.recorded_at,
                    ).model_dump(mode="json")
                )
        elif outcome.rejection:
            rejected.append(outcome.rejection.model_dump(mode="json"))
            checkpoints.append(
                CheckpointRecord(
                    job_id=job.job_id,
                    outcome=outcome.outcome,
                    reason=outcome.rejection.failure_reason,
                    recorded_at=outcome.recorded_at,
                ).model_dump(mode="json")
            )
        else:
            raise ValueError(f"invalid terminal outcome for job {job.job_id}")

    directory = plan.config.output.directory
    summary = _summarize(dataset, rejected, checkpoints)
    manifest_path = directory / "manifest.json"
    current = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    stage = directory / "work" / "finalization" / plan.config_hash
    ready = stage / "READY"
    if not ready.exists():
        completed_at = current.completed_at if current.run_status == "completed" else None
        manifest = finalize_run(plan, current, summary, completed_at=completed_at)
        write_jsonl_atomic(stage / "dataset.jsonl", dataset)
        write_jsonl_atomic(stage / "rejected.jsonl", rejected)
        write_jsonl_atomic(stage / "checkpoint.jsonl", checkpoints)
        write_text_atomic(
            stage / "manifest.json",
            json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False)
            + "\n",
        )
        staged_names = (
            "dataset.jsonl",
            "rejected.jsonl",
            "checkpoint.jsonl",
            "manifest.json",
        )
        write_text_atomic(
            ready,
            json.dumps(
                {
                    "schema_version": "finalization_stage_v1",
                    "files": {
                        name: _file_sha256(stage / name) for name in staged_names
                    },
                },
                sort_keys=True,
            )
            + "\n",
        )
    _validate_stage(stage, ready)
    manifest = RunManifest.model_validate_json(
        (stage / "manifest.json").read_text(encoding="utf-8")
    )
    for name in ("dataset.jsonl", "rejected.jsonl", "checkpoint.jsonl"):
        _replace_from_stage(stage / name, directory / name)
        if after_replace:
            after_replace(name)
    # The completed manifest is the commit marker and is always replaced last.
    _replace_from_stage(stage / "manifest.json", manifest_path)
    if after_replace:
        after_replace("manifest.json")
    write_queue_state(plan)
    return manifest


@contextmanager
def consumer_process_lock(database: Path | None = None) -> Iterator[None]:
    """Hold the singleton-consumer lock for one SQLite queue database."""

    database = (database or queue_database_path()).resolve()
    lock_path = database.with_name(f"{database.name}.consumer.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "unknown"
            raise RuntimeError(
                "another llm-data-gen consumer already owns SQLite queue "
                f"{database} (pid {owner}); use --workers N on that single "
                "consumer instead of starting a second process"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def redact_secrets(value: str, plan: RunPlan) -> str:
    redacted = value
    env_name = plan.config.endpoint.api_key_env
    secret = os.getenv(env_name) if env_name else None
    if secret:
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = re.sub(r"(?i)Bearer\s+[^\s,;]+", "Bearer [REDACTED]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", redacted)
    return redacted


def _sanitize_outcome(outcome: JobOutcome, plan: RunPlan) -> JobOutcome:
    env_name = plan.config.endpoint.api_key_env
    secret = os.getenv(env_name) if env_name else None
    serialized = outcome.model_dump_json()
    if secret and secret in serialized:
        job = plan.job(outcome.job_id)
        return rejected_outcome(
            plan,
            job,
            plan.chunk(job.chunk_id),
            "generation",
            "inference output or error contained the configured API key and was redacted",
            raw=None,
            attempt_count=outcome.attempt_count,
            recorded_at=outcome.recorded_at,
        )
    if not outcome.rejection:
        return outcome
    reason = redact_secrets(outcome.rejection.failure_reason, plan)
    raw = (
        redact_secrets(outcome.rejection.raw_output, plan)
        if outcome.rejection.raw_output is not None
        else None
    )
    rejection = outcome.rejection.model_copy(
        update={"failure_reason": reason, "raw_output": raw}
    )
    return outcome.model_copy(
        update={"rejection": rejection, "raw_output": raw}
    )


def _persist_snapshot(plan: RunPlan) -> None:
    directory = plan.config.output.directory
    serialized = plan.model_dump_json()
    _ensure_no_configured_secret(serialized, plan)
    writer = DatasetWriter(plan.config, plan.config_hash)
    writer.initialize(initial_manifest(plan), list(plan.sources), list(plan.chunks))
    work = directory / "work"
    _write_immutable(work / "run-plan.json", serialized + "\n")
    _write_immutable(
        work / "chunks.jsonl",
        "".join(chunk.model_dump_json() + "\n" for chunk in plan.chunks),
    )
    _write_immutable(
        work / "jobs.jsonl",
        "".join(job.model_dump_json() + "\n" for job in plan.jobs),
    )
    write_queue_state(plan)


def _assert_same_snapshot(existing: RunPlan, current: RunPlan) -> None:
    fields = ("config_hash", "sources_hash", "chunks_hash", "jobs_hash")
    if any(getattr(existing, field) != getattr(current, field) for field in fields):
        raise ValueError("immutable run snapshot differs from current config or source data")


def _register_run(plan: RunPlan) -> None:
    payload = {
        "run_id": plan.run_id,
        "config_hash": plan.config_hash,
        "output_directory": str(plan.config.output.directory.resolve()),
    }
    path = _registry_directory() / f"{_safe_id(plan.run_id)}.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError(f"queued run ID collision for {plan.run_id!r}")
        return
    _write_immutable(path, json.dumps(payload, sort_keys=True) + "\n")


def _registry_directory() -> Path:
    database = queue_database_path()
    return database.parent / f"{database.stem}-runs"


@contextmanager
def _job_lock(plan: RunPlan, job_id: str) -> Iterator[None]:
    path = plan.config.output.directory / "work" / "locks" / f"{_safe_id(job_id)}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _state_path(plan: RunPlan, job_id: str) -> Path:
    return plan.config.output.directory / "work" / "states" / f"{_safe_id(job_id)}.json"


def _write_state(
    plan: RunPlan,
    job_id: str,
    state: str,
    *,
    attempt_count: int,
    error: str | None = None,
) -> None:
    payload = {
        "job_id": job_id,
        "state": state,
        "attempt_count": attempt_count,
    }
    if error:
        payload["error"] = redact_secrets(error, plan)
    write_text_atomic(
        _state_path(plan, job_id), json.dumps(payload, sort_keys=True) + "\n"
    )


def _read_state(plan: RunPlan, job_id: str) -> dict:
    path = _state_path(plan, job_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _attempt_directory(plan: RunPlan, job_id: str) -> Path:
    return (
        plan.config.output.directory
        / "work"
        / "attempts"
        / plan.config_hash
        / _safe_id(job_id)
    )


def read_attempts(plan: RunPlan, job_id: str) -> list[dict]:
    plan.job(job_id)
    directory = _attempt_directory(plan, job_id)
    attempts: list[dict] = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected_number = len(attempts) + 1
        if (
            payload.get("run_id") != plan.run_id
            or payload.get("config_hash") != plan.config_hash
            or payload.get("job_id") != job_id
            or payload.get("attempt") != expected_number
        ):
            raise ValueError("durable attempt record identity or sequence mismatch")
        attempts.append(payload)
        if len(attempts) > MAX_LIFETIME_ATTEMPTS:
            raise ValueError("durable attempt record exceeds lifetime budget")
    return attempts


def _attempt_path(plan: RunPlan, job_id: str, attempt: int) -> Path:
    return _attempt_directory(plan, job_id) / f"{attempt:03d}.json"


def _start_attempt(plan: RunPlan, job_id: str, attempt: int) -> None:
    existing = read_attempts(plan, job_id)
    if attempt != len(existing) + 1 or attempt > MAX_LIFETIME_ATTEMPTS:
        raise ValueError("invalid durable attempt allocation")
    payload = {
        "schema_version": "job_attempt_v1",
        "run_id": plan.run_id,
        "config_hash": plan.config_hash,
        "job_id": job_id,
        "attempt": attempt,
        "status": "started",
        "started_at": now_utc(),
        "finished_at": None,
        "diagnostic": None,
    }
    _write_immutable(
        _attempt_path(plan, job_id, attempt),
        json.dumps(payload, sort_keys=True) + "\n",
    )


def _finish_attempt(
    plan: RunPlan,
    job_id: str,
    attempt: int,
    status: str,
    diagnostic: str | None,
) -> None:
    path = _attempt_path(plan, job_id, attempt)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "started":
        raise ValueError("durable attempt record was already completed")
    payload.update(
        {
            "status": status,
            "finished_at": now_utc(),
            "diagnostic": redact_secrets(diagnostic, plan) if diagnostic else None,
        }
    )
    write_text_atomic(path, json.dumps(payload, sort_keys=True) + "\n")


def _exhausted_outcome(
    plan: RunPlan, job_id: str, attempts: list[dict]
) -> JobOutcome:
    job = plan.job(job_id)
    last = attempts[-1] if attempts else {}
    diagnostic = last.get("diagnostic") or "attempt interrupted before terminal diagnostic"
    return rejected_outcome(
        plan,
        job,
        plan.chunk(job.chunk_id),
        "generation",
        f"transient failure exhausted after {len(attempts)} attempts across lifetime: "
        f"{diagnostic}",
        raw=None,
        attempt_count=len(attempts),
    )


@contextmanager
def _finalization_lock(plan: RunPlan) -> Iterator[None]:
    path = plan.config.output.directory / "work" / "locks" / "finalization.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _replace_from_stage(source: Path, destination: Path) -> None:
    write_text_atomic(destination, source.read_text(encoding="utf-8"))


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_stage(stage: Path, ready: Path) -> None:
    payload = json.loads(ready.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "finalization_stage_v1":
        raise ValueError("invalid finalization stage commit record")
    expected = payload.get("files") or {}
    names = ("dataset.jsonl", "rejected.jsonl", "checkpoint.jsonl", "manifest.json")
    if set(expected) != set(names):
        raise ValueError("incomplete finalization stage commit record")
    for name in names:
        if not (stage / name).exists() or expected[name] != _file_sha256(stage / name):
            raise ValueError(f"finalization stage integrity mismatch for {name}")


def _broker_status() -> dict[str, object]:
    database = queue_database_path()
    messages: int | None = None
    if database.exists():
        try:
            with sqlite3.connect(database) as connection:
                messages = int(connection.execute("SELECT COUNT(*) FROM task").fetchone()[0])
        except sqlite3.Error:
            messages = None
    return {
        "database": str(database),
        "messages_present": messages,
        "scope": "entire_broker_not_run_attributed",
    }


def _duplicate_rejection(
    plan: RunPlan, outcome: JobOutcome, duplicate_of: str
) -> RejectedRecord:
    job = plan.job(outcome.job_id)
    chunk = plan.chunk(job.chunk_id)
    return RejectedRecord(
        job_id=job.job_id,
        source_id=chunk.source_id,
        chunk_id=chunk.chunk_id,
        qa_format=job.qa_format,
        language=job.language,
        language_origin=job.language_origin,
        prompt_name=job.prompt_name,
        prompt_version=job.prompt_version,
        prompt_hash=outcome.record.prompt_hash if outcome.record else None,
        generator_model=plan.config.endpoint.model,
        inference_backend=plan.config.endpoint.backend,
        endpoint_profile=plan.config.endpoint.profile,
        failure_stage="duplicate",
        failure_reason="normalized messages duplicate an accepted example",
        raw_output=outcome.raw_output,
        duplicate_of=duplicate_of,
        provenance={
            "source_path": chunk.provenance_path,
            "source_checksum": chunk.source_checksum,
            "chunk_strategy": chunk.strategy,
            "chunk_index": chunk.chunk_index,
        },
        recorded_at=outcome.recorded_at,
    )


def _summarize(dataset: list[dict], rejected: list[dict], checkpoints: list[dict]) -> dict:
    latest = {row["job_id"]: row["outcome"] for row in checkpoints}
    by_format: dict[str, int] = {}
    by_language: dict[str, int] = {}
    by_source: dict[str, int] = {}
    failures: dict[str, int] = {}
    for row in dataset:
        for target, key in (
            (by_format, "qa_format"),
            (by_language, "language"),
            (by_source, "source_id"),
        ):
            value = str(row.get(key) or "unknown")
            target[value] = target.get(value, 0) + 1
    for row in rejected:
        stage = str(row.get("failure_stage") or "unknown")
        failures[stage] = failures.get(stage, 0) + 1
    return {
        "accepted": sum(value == "accepted" for value in latest.values()),
        "rejected": sum(value == "rejected" for value in latest.values()),
        "not_applicable": sum(value == "not_applicable" for value in latest.values()),
        "accepted_by_format": by_format,
        "accepted_by_language": by_language,
        "accepted_by_source": by_source,
        "failures_by_stage": failures,
    }


def _ensure_no_configured_secret(serialized: str, plan: RunPlan) -> None:
    env_name = plan.config.endpoint.api_key_env
    secret = os.getenv(env_name) if env_name else None
    if secret and secret in serialized:
        raise ValueError("configured API-key value appeared in queued snapshot; refusing enqueue")


def _write_immutable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != content:
                raise ValueError(
                    f"immutable artifact already exists with different content: {path}"
                )
    finally:
        temporary.unlink(missing_ok=True)


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)
