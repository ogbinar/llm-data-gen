from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from pydantic import ValidationError

from llm_data_gen.cli import resolve_worker_count
from llm_data_gen.client import StaticClient
from llm_data_gen.config import RunConfig
from llm_data_gen.execution import execute_job, is_transient_error, prepare_run
from llm_data_gen.pipeline import run_config_pipeline
from llm_data_gen.queueing import (
    execute_queued_job,
    finalize_queued_run,
    mark_enqueued,
    prepare_queued_run,
    queue_status,
    read_outcome,
    read_attempts,
    task_payload,
    unfinished_job_ids,
    write_outcome,
)


def make_config(tmp_path, *, name="queue-test", jobs=3, output="queued"):
    source = tmp_path / "source.txt"
    if not source.exists():
        source.write_text(
            "The service ID is 12345. Restart the modem before contacting support.",
            encoding="utf-8",
        )
    return RunConfig.model_validate(
        {
            "version": 2,
            "name": name,
            "input": {"path": source, "language": "english", "domain": "support"},
            "chunking": {"strategy": "document"},
            "endpoint": {
                "profile": "local-qwen",
                "backend": "static",
                "base_url": "http://example.invalid/v1",
                "model": "static-model",
                "api_key_env": "QUEUE_TEST_API_KEY",
            },
            "generation": {
                "recipes": [{"format": "factual_qa", "num_examples": jobs}]
            },
            "output": {"directory": tmp_path / output},
        }
    )


def response(question="What is the service ID?"):
    return json.dumps(
        {
            "status": "generated",
            "qa_format": "factual_qa",
            "messages": [
                {"role": "user", "content": question},
                {"role": "assistant", "content": "The service ID is 12345."},
            ],
            "evidence": ["The service ID is 12345."],
            "metadata": {},
        }
    )


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_DATA_GEN_QUEUE_DB", str(tmp_path / "broker" / "huey.db"))


def execute_all(plan, raw=None):
    raw = raw or response()
    for job in plan.jobs:
        execute_queued_job(
            plan.run_id,
            plan.config_hash,
            job.job_id,
            client_factory=lambda _config, raw=raw: StaticClient([raw]),
            sleep=lambda _delay: None,
        )


def canonical_rows(directory):
    return [
        json.loads(line)
        for line in (directory / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def test_inline_uses_shared_seams_and_matches_queue_contract(tmp_path):
    inline = make_config(tmp_path, jobs=1, output="inline")
    queued = make_config(tmp_path, jobs=1, output="queued")
    inline_rows = run_config_pipeline(inline, client=StaticClient([response()]))
    plan = prepare_queued_run(queued)
    execute_all(plan)
    finalize_queued_run(plan)
    queued_rows = canonical_rows(queued.output.directory)
    left = inline_rows[0].model_dump(mode="json")
    right = queued_rows[0]
    left.pop("generated_at")
    right.pop("generated_at")
    assert left == right
    assert left["language"] == "english"
    assert left["generation_parameters"]["source_language_origin"] == "input_default"


@pytest.mark.parametrize(
    "label, raw, expected",
    [
        ("accepted", response(), "accepted"),
        ("parsing", "not-json", "parsing"),
        (
            "validation",
            json.dumps(
                {
                    "status": "generated",
                    "qa_format": "factual_qa",
                    "messages": [
                        {"role": "user", "content": "Unsupported?"},
                        {"role": "assistant", "content": "Unsupported."},
                    ],
                    "evidence": ["absent evidence"],
                }
            ),
            "validation",
        ),
        (
            "not-applicable",
            json.dumps(
                {
                    "status": "not_applicable",
                    "qa_format": "factual_qa",
                    "reason": "No distinct supported example.",
                }
            ),
            "not_applicable",
        ),
    ],
)
def test_inline_queue_equivalence_covers_all_outcome_classes(
    tmp_path, label, raw, expected
):
    inline = make_config(tmp_path, jobs=1, output=f"inline-{label}")
    queued = make_config(tmp_path, jobs=1, output=f"queued-{label}")
    run_config_pipeline(inline, client=StaticClient([raw]))
    plan = prepare_queued_run(queued)
    execute_all(plan, raw)
    finalize_queued_run(plan)

    def canonical_class(directory):
        checkpoint = json.loads(
            (directory / "checkpoint.jsonl").read_text(encoding="utf-8")
        )
        if checkpoint["outcome"] == "accepted":
            return "accepted"
        rejected = json.loads(
            (directory / "rejected.jsonl").read_text(encoding="utf-8")
        )
        return rejected["failure_stage"]

    assert canonical_class(inline.output.directory) == expected
    assert canonical_class(queued.output.directory) == expected


def test_inline_import_and_execution_do_not_require_huey(tmp_path):
    script = "import sys; import llm_data_gen.pipeline; assert 'huey' not in sys.modules"
    subprocess.run([sys.executable, "-c", script], check=True)
    config = make_config(tmp_path, jobs=1, output="inline")
    assert run_config_pipeline(config, client=StaticClient([response()]))
    huey_requirements = [
        requirement
        for requirement in importlib.metadata.requires("llm-data-gen") or []
        if requirement.lower().startswith("huey")
    ]
    assert huey_requirements
    assert all("extra ==" in requirement for requirement in huey_requirements)


def test_snapshot_is_immutable_and_detects_source_change(tmp_path):
    config = make_config(tmp_path, jobs=1)
    plan = prepare_queued_run(config)
    snapshot = config.output.directory / "work" / "run-plan.json"
    before = snapshot.read_bytes()
    public_chunk = json.loads(
        (config.output.directory / "chunks.jsonl").read_text(encoding="utf-8")
    )
    private_chunk = json.loads(
        (config.output.directory / "work" / "chunks.jsonl").read_text(encoding="utf-8")
    )
    assert "text" not in public_chunk and "text_sha256" in public_chunk
    assert private_chunk["text"] == config.input.path.read_text(encoding="utf-8")
    assert prepare_queued_run(config).created_at == plan.created_at
    assert snapshot.read_bytes() == before
    with pytest.raises(ValidationError):
        plan.run_id = "changed"
    config.input.path.write_text("Source changed after enqueue.", encoding="utf-8")
    with pytest.raises(ValueError, match="immutable run snapshot differs"):
        prepare_queued_run(config)


def test_restart_recovery_enqueues_only_unfinished_jobs(tmp_path):
    config = make_config(tmp_path, jobs=3)
    plan = prepare_queued_run(config)
    mark_enqueued(plan, [job.job_id for job in plan.jobs])
    execute_queued_job(
        plan.run_id,
        plan.config_hash,
        plan.jobs[0].job_id,
        client_factory=lambda _: StaticClient([response("First?")]),
    )
    recovered = prepare_queued_run(config)
    assert unfinished_job_ids(recovered) == [job.job_id for job in plan.jobs[1:]]
    mark_enqueued(recovered, unfinished_job_ids(recovered))
    status = queue_status(recovered)
    assert status["terminal"] == 1
    assert status["pending"] == 2


def test_concurrent_redelivery_generates_and_persists_once(tmp_path):
    plan = prepare_queued_run(make_config(tmp_path, jobs=1))
    calls = 0
    lock = threading.Lock()

    class Client:
        def generate(self, messages, parameters):
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.02)
            return response()

    def deliver():
        return execute_queued_job(
            plan.run_id,
            plan.config_hash,
            plan.jobs[0].job_id,
            client_factory=lambda _: Client(),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: deliver(), range(4)))
    assert calls == 1
    assert {item.record.example_id for item in outcomes} == {outcomes[0].record.example_id}
    assert len(list((plan.config.output.directory / "work" / "outcomes").rglob("*.json"))) == 1
    finalize_queued_run(plan)
    assert len(canonical_rows(plan.config.output.directory)) == 1


@pytest.mark.parametrize("workers, expected_min, expected_max", [(1, 1, 1), (4, 2, 4)])
def test_worker_bound_controls_observed_generation_concurrency(
    tmp_path, workers, expected_min, expected_max
):
    plan = prepare_queued_run(make_config(tmp_path, jobs=6))
    active = maximum = 0
    lock = threading.Lock()

    class Client:
        def generate(self, messages, parameters):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return response()

    def run(job):
        execute_queued_job(
            plan.run_id,
            plan.config_hash,
            job.job_id,
            client_factory=lambda _: Client(),
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run, plan.jobs))
    assert expected_min <= maximum <= expected_max
    manifest = finalize_queued_run(plan)
    assert (manifest.accepted_jobs, manifest.rejected_jobs) == (1, 5)
    assert canonical_rows(plan.config.output.directory)[0]["job_id"] == plan.jobs[0].job_id


def test_transient_retry_is_bounded_and_terminal_failure_is_not_retried(tmp_path):
    plan = prepare_queued_run(make_config(tmp_path, jobs=2))
    attempts = 0

    class EventuallyWorks:
        def generate(self, messages, parameters):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise httpx.ConnectError("temporary")
            return response("Recovered?")

    observed_retrying = []
    outcome = execute_queued_job(
        plan.run_id,
        plan.config_hash,
        plan.jobs[0].job_id,
        client_factory=lambda _: EventuallyWorks(),
        sleep=lambda _delay: observed_retrying.append(queue_status(plan)["retrying"]),
    )
    assert outcome.outcome == "accepted"
    assert outcome.attempt_count == attempts == 3
    assert observed_retrying == [1, 1]

    terminal_calls = 0

    class Terminal:
        def generate(self, messages, parameters):
            nonlocal terminal_calls
            terminal_calls += 1
            raise RuntimeError("schema-side terminal failure")

    rejected = execute_queued_job(
        plan.run_id,
        plan.config_hash,
        plan.jobs[1].job_id,
        client_factory=lambda _: Terminal(),
        sleep=lambda _delay: None,
    )
    assert terminal_calls == 1
    assert rejected.outcome == "rejected"
    assert rejected.attempt_count == 1


def test_exhausted_transient_failure_becomes_inspectable_rejection(tmp_path):
    plan = prepare_queued_run(make_config(tmp_path, jobs=1))

    class AlwaysTimeout:
        def generate(self, messages, parameters):
            raise httpx.ReadTimeout("still unavailable")

    outcome = execute_queued_job(
        plan.run_id,
        plan.config_hash,
        plan.jobs[0].job_id,
        client_factory=lambda _: AlwaysTimeout(),
        sleep=lambda _delay: None,
    )
    assert outcome.attempt_count == 3
    assert "exhausted after 3 attempts" in outcome.rejection.failure_reason
    manifest = finalize_queued_run(plan)
    assert manifest.rejected_jobs == 1
    assert json.loads(
        (plan.config.output.directory / "rejected.jsonl").read_text(encoding="utf-8")
    )["failure_stage"] == "generation"


def test_deterministic_failures_are_terminal_without_retry(tmp_path):
    plan = prepare_queued_run(make_config(tmp_path, jobs=3))
    raws = [
        "not json",
        json.dumps(
            {
                "status": "generated",
                "qa_format": "factual_qa",
                "messages": [
                    {"role": "user", "content": "What is unsupported?"},
                    {"role": "assistant", "content": "An unsupported claim."},
                ],
                "evidence": ["This evidence is absent from the source."],
            }
        ),
        json.dumps(
            {
                "status": "not_applicable",
                "qa_format": "factual_qa",
                "reason": "The source does not support another distinct example.",
            }
        ),
    ]
    stages = []
    calls = []
    for job, raw in zip(plan.jobs, raws, strict=True):
        client = StaticClient([raw])
        outcome = execute_queued_job(
            plan.run_id,
            plan.config_hash,
            job.job_id,
            client_factory=lambda _config, client=client: client,
            sleep=lambda _delay: pytest.fail("terminal outcome must not back off"),
        )
        stages.append(outcome.rejection.failure_stage)
        calls.append(len(client.calls))
    assert stages == ["parsing", "validation", "not_applicable"]
    assert calls == [1, 1, 1]


def test_http_retry_classification():
    request = httpx.Request("POST", "http://example.invalid")
    for status in (429, 500, 503):
        error = httpx.HTTPStatusError(
            "temporary", request=request, response=httpx.Response(status, request=request)
        )
        assert is_transient_error(error)
    for status in (400, 401, 404):
        error = httpx.HTTPStatusError(
            "terminal", request=request, response=httpx.Response(status, request=request)
        )
        assert not is_transient_error(error)


def test_finalization_uses_planned_order_for_dedup_and_is_byte_idempotent(tmp_path):
    plan = prepare_queued_run(make_config(tmp_path, jobs=3))
    outcomes = [execute_job(plan, job.job_id, StaticClient([response()])) for job in plan.jobs]
    for outcome in reversed(outcomes):
        write_outcome(plan, outcome)
    manifest = finalize_queued_run(plan)
    directory = plan.config.output.directory
    before = {name: (directory / name).read_bytes() for name in (
        "dataset.jsonl", "rejected.jsonl", "checkpoint.jsonl", "manifest.json"
    )}
    assert manifest.accepted_jobs == 1
    assert manifest.rejected_jobs == 2
    assert canonical_rows(directory)[0]["job_id"] == plan.jobs[0].job_id
    rejected = [json.loads(line) for line in (directory / "rejected.jsonl").read_text().splitlines()]
    assert [row["job_id"] for row in rejected] == [job.job_id for job in plan.jobs[1:]]
    finalize_queued_run(plan)
    after = {name: (directory / name).read_bytes() for name in before}
    assert before == after


def test_simultaneous_runs_are_isolated_and_mismatches_fail_before_inference(tmp_path):
    first = prepare_queued_run(make_config(tmp_path, name="first", jobs=1, output="one"))
    second = prepare_queued_run(make_config(tmp_path, name="second", jobs=1, output="two"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute_all, (first, second)))
    finalize_queued_run(first)
    finalize_queued_run(second)
    assert first.config_hash != second.config_hash
    assert canonical_rows(first.config.output.directory)
    assert canonical_rows(second.config.output.directory)
    called = False

    def forbidden(_config):
        nonlocal called
        called = True
        return StaticClient([response()])

    with pytest.raises(ValueError, match="config hash mismatch"):
        execute_queued_job(
            first.run_id,
            second.config_hash,
            first.jobs[0].job_id,
            client_factory=forbidden,
        )
    assert not called


def test_task_snapshot_status_and_errors_do_not_disclose_api_key(tmp_path, monkeypatch):
    secret = "sk-super-secret-value-123456"
    monkeypatch.setenv("QUEUE_TEST_API_KEY", secret)
    plan = prepare_queued_run(make_config(tmp_path, jobs=1))
    payload = task_payload(plan, plan.jobs[0].job_id)
    assert set(payload) == {"run_id", "config_hash", "job_id"}
    assert secret not in json.dumps(payload)
    assert secret not in (plan.config.output.directory / "work" / "run-plan.json").read_text()

    class LeakyTimeout:
        def generate(self, messages, parameters):
            raise httpx.ReadTimeout(f"Bearer {secret}")

    execute_queued_job(
        plan.run_id,
        plan.config_hash,
        plan.jobs[0].job_id,
        client_factory=lambda _: LeakyTimeout(),
        sleep=lambda _delay: None,
    )
    all_text = "".join(
        path.read_text(encoding="utf-8")
        for path in plan.config.output.directory.rglob("*.json")
    )
    assert secret not in all_text
    assert "[REDACTED]" in all_text


def test_nontransient_errors_are_also_secret_safe(tmp_path, monkeypatch):
    secret = "sk-another-secret-value-123456"
    monkeypatch.setenv("QUEUE_TEST_API_KEY", secret)
    plan = prepare_queued_run(make_config(tmp_path, jobs=1))

    class LeakyTerminal:
        def generate(self, messages, parameters):
            raise RuntimeError(f"authorization failed for {secret}")

    outcome = execute_queued_job(
        plan.run_id,
        plan.config_hash,
        plan.jobs[0].job_id,
        client_factory=lambda _: LeakyTerminal(),
    )
    assert outcome.outcome == "rejected"
    assert secret not in outcome.model_dump_json()
    assert "configured API key" in outcome.rejection.failure_reason


def test_worker_count_precedence_and_validation():
    assert resolve_worker_count(4, {"LLM_DATA_GEN_WORKERS": "2"}) == 4
    assert resolve_worker_count(None, {"LLM_DATA_GEN_WORKERS": "2"}) == 2
    assert resolve_worker_count(None, {}) == 1
    with pytest.raises(ValueError, match="at least 1"):
        resolve_worker_count(0, {})
    with pytest.raises(ValueError, match="positive integer"):
        resolve_worker_count(None, {"LLM_DATA_GEN_WORKERS": "many"})


def test_operational_queue_settings_do_not_change_config_or_job_identity(tmp_path, monkeypatch):
    config = make_config(tmp_path, jobs=2)
    first = prepare_run(config)
    monkeypatch.setenv("LLM_DATA_GEN_WORKERS", "8")
    monkeypatch.setenv("LLM_DATA_GEN_QUEUE_DB", str(tmp_path / "different.db"))
    second = prepare_run(config)
    assert first.config_hash == second.config_hash
    assert [job.job_id for job in first.jobs] == [job.job_id for job in second.jobs]


def test_real_sqlite_huey_persists_secret_free_small_message(tmp_path, monkeypatch):
    import importlib
    from huey.consumer import Consumer
    import llm_data_gen.queue_huey as adapter

    database = tmp_path / "real-huey.db"
    monkeypatch.setenv("LLM_DATA_GEN_QUEUE_DB", str(database))
    adapter = importlib.reload(adapter)
    assert adapter.huey.storage.conn.execute("PRAGMA synchronous").fetchone() == (2,)
    plan = prepare_queued_run(make_config(tmp_path, jobs=1))
    payload = task_payload(plan, plan.jobs[0].job_id)
    adapter.enqueue_payload(payload)
    task = adapter.huey.dequeue()
    assert task.args == (payload["run_id"], payload["config_hash"], payload["job_id"])
    assert task.kwargs == {}
    adapter.huey.enqueue(task)

    def static_execute(run_id, config_hash, job_id):
        return execute_queued_job(
            run_id,
            config_hash,
            job_id,
            client_factory=lambda _: StaticClient([response()]),
            sleep=lambda _delay: None,
        )

    monkeypatch.setattr(adapter, "execute_queued_job", static_execute)
    consumer = Consumer(
        adapter.huey,
        workers=1,
        worker_type="thread",
        periodic=False,
        initial_delay=0.01,
        max_delay=0.05,
    )
    consumer.start()
    deadline = time.monotonic() + 2
    while read_outcome(plan, plan.jobs[0].job_id) is None and time.monotonic() < deadline:
        time.sleep(0.01)
    consumer.stop(graceful=True)
    assert read_outcome(plan, plan.jobs[0].job_id).outcome == "accepted"
    assert finalize_queued_run(plan).accepted_jobs == 1
    assert database.exists()


def test_sqlite_dequeue_loss_is_recovered_by_reenqueue(tmp_path, monkeypatch):
    import importlib
    from huey.consumer import Consumer
    import llm_data_gen.queue_huey as adapter

    database = tmp_path / "restart-recovery.db"
    monkeypatch.setenv("LLM_DATA_GEN_QUEUE_DB", str(database))
    adapter = importlib.reload(adapter)
    config = make_config(tmp_path, jobs=2)
    plan = prepare_queued_run(config)
    for job in plan.jobs:
        adapter.enqueue_payload(task_payload(plan, job.job_id))
    mark_enqueued(plan, [job.job_id for job in plan.jobs])

    # SqliteHuey removes a message at dequeue time. Dropping this object models
    # a worker dying before application execution or outcome persistence.
    lost_task = adapter.huey.dequeue()
    assert lost_task is not None
    assert len(adapter.huey) == 1

    recovered = prepare_queued_run(config)
    missing = unfinished_job_ids(recovered)
    assert missing == [job.job_id for job in plan.jobs]
    for job_id in missing:
        adapter.enqueue_payload(task_payload(recovered, job_id))
    mark_enqueued(recovered, missing)

    def static_execute(run_id, config_hash, job_id):
        return execute_queued_job(
            run_id,
            config_hash,
            job_id,
            client_factory=lambda _: StaticClient([response()]),
            sleep=lambda _delay: None,
        )

    monkeypatch.setattr(adapter, "execute_queued_job", static_execute)
    consumer = Consumer(
        adapter.huey,
        workers=1,
        worker_type="thread",
        periodic=False,
        initial_delay=0.01,
        max_delay=0.05,
    )
    consumer.start()
    deadline = time.monotonic() + 2
    while queue_status(recovered)["terminal"] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    consumer.stop(graceful=True)
    assert queue_status(recovered)["terminal"] == 2
    assert len(list((config.output.directory / "work" / "outcomes").rglob("*.json"))) == 2
    manifest = finalize_queued_run(recovered)
    assert (manifest.accepted_jobs, manifest.rejected_jobs) == (1, 1)


def test_sqlite_broker_persists_burst_without_starting_inference(tmp_path, monkeypatch):
    import importlib
    import llm_data_gen.queue_huey as adapter

    database = tmp_path / "burst.huey.db"
    monkeypatch.setenv("LLM_DATA_GEN_QUEUE_DB", str(database))
    adapter = importlib.reload(adapter)
    plan = prepare_queued_run(make_config(tmp_path, jobs=50))
    for job in plan.jobs:
        adapter.enqueue_payload(task_payload(plan, job.job_id))
    mark_enqueued(plan, [job.job_id for job in plan.jobs])
    assert len(adapter.huey) == 50
    assert queue_status(plan)["pending"] == 50
    assert not list((plan.config.output.directory / "work" / "outcomes").rglob("*.json"))


def test_memory_huey_executes_small_payload_without_model_dependency(tmp_path):
    from huey import MemoryHuey

    plan = prepare_queued_run(make_config(tmp_path, jobs=2))
    memory = MemoryHuey("queue-test", immediate=True, results=False)

    @memory.task()
    def task(run_id, config_hash, job_id):
        return execute_queued_job(
            run_id,
            config_hash,
            job_id,
            client_factory=lambda _: StaticClient([response()]),
            sleep=lambda _delay: None,
        ).outcome

    for job in plan.jobs:
        task(**task_payload(plan, job.job_id))
    assert queue_status(plan)["terminal"] == 2
    assert finalize_queued_run(plan).accepted_jobs == 1


def test_retry_budget_survives_interruption_and_redelivery(tmp_path):
    plan = prepare_queued_run(make_config(tmp_path, jobs=1))
    calls = 0

    class SimulatedProcessDeath(BaseException):
        pass

    class DiesDuringInference:
        def generate(self, messages, parameters):
            nonlocal calls
            calls += 1
            raise SimulatedProcessDeath()

    with pytest.raises(SimulatedProcessDeath):
        execute_queued_job(
            plan.run_id,
            plan.config_hash,
            plan.jobs[0].job_id,
            client_factory=lambda _: DiesDuringInference(),
            sleep=lambda _: None,
        )
    attempts = read_attempts(plan, plan.jobs[0].job_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "started"

    class StillUnavailable:
        def generate(self, messages, parameters):
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("contains no secret")

    outcome = execute_queued_job(
        plan.run_id,
        plan.config_hash,
        plan.jobs[0].job_id,
        client_factory=lambda _: StillUnavailable(),
        sleep=lambda _: None,
    )
    assert calls == outcome.attempt_count == 3
    assert [item["status"] for item in read_attempts(plan, outcome.job_id)] == [
        "started",
        "transient_error",
        "transient_error",
    ]
    assert outcome.rejection.failure_stage == "generation"

    class Forbidden:
        def generate(self, messages, parameters):
            pytest.fail("terminal redelivery must not spend a fourth attempt")

    assert execute_queued_job(
        plan.run_id,
        plan.config_hash,
        outcome.job_id,
        client_factory=lambda _: Forbidden(),
    ) == outcome


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value["record"].__setitem__("source_id", "wrong"), "source_id"),
        (lambda value: value["record"].__setitem__("chunk_id", "wrong"), "chunk_id"),
        (lambda value: value["record"].__setitem__("qa_format", "multi_turn"), "qa_format"),
        (lambda value: value["record"].__setitem__("language", "tagalog"), "language"),
        (lambda value: value["record"].__setitem__("prompt_hash", "sha256:bad"), "prompt_hash"),
        (lambda value: value["record"].__setitem__("generator_model", "wrong"), "generator_model"),
        (lambda value: value["record"].__setitem__("inference_backend", "wrong"), "inference_backend"),
        (lambda value: value.__setitem__("signature", "sha256:bad"), "signature"),
        (
            lambda value: value["record"]["messages"][1].__setitem__(
                "content", "tampered answer"
            ),
            "signature",
        ),
        (lambda value: value["record"].__setitem__("evidence", ["not in source"]), "validation"),
        (
            lambda value: value["record"]["generation_parameters"].__setitem__(
                "source_language_origin", "record_field"
            ),
            "generation_parameters",
        ),
    ],
)
def test_tampered_accepted_outcomes_fail_before_canonical_output(
    tmp_path, mutation, message
):
    plan = prepare_queued_run(make_config(tmp_path, jobs=1))
    execute_all(plan)
    path = next((plan.config.output.directory / "work" / "outcomes").rglob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        finalize_queued_run(plan)
    assert not (plan.config.output.directory / "work" / "finalization" / plan.config_hash / "READY").exists()


@pytest.mark.parametrize("outcome_kind", ["rejected", "not_applicable"])
@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value["rejection"].__setitem__("job_id", "wrong"), "job_id"),
        (lambda value: value["rejection"].__setitem__("source_id", "wrong"), "source_id"),
        (lambda value: value["rejection"].__setitem__("chunk_id", "wrong"), "chunk_id"),
        (lambda value: value["rejection"].__setitem__("qa_format", "multi_turn"), "qa_format"),
        (lambda value: value["rejection"].__setitem__("language", "tagalog"), "language"),
        (
            lambda value: value["rejection"].__setitem__(
                "language_origin", "record_field"
            ),
            "language_origin",
        ),
        (lambda value: value["rejection"].__setitem__("prompt_hash", "sha256:bad"), "prompt_hash"),
        (lambda value: value["rejection"].__setitem__("generator_model", "wrong"), "generator_model"),
        (lambda value: value["rejection"].__setitem__("inference_backend", "wrong"), "inference_backend"),
        (lambda value: value["rejection"].__setitem__("endpoint_profile", "wrong"), "endpoint_profile"),
        (
            lambda value: value["rejection"]["provenance"].__setitem__(
                "source_checksum", "tampered"
            ),
            "provenance",
        ),
        (lambda value: value["rejection"].__setitem__("raw_output", "wrong"), "raw_output"),
    ],
)
def test_tampered_nonaccepted_identity_fails_before_canonical_output(
    tmp_path, outcome_kind, mutation, message
):
    plan = prepare_queued_run(make_config(tmp_path, jobs=1))
    raw = "not json" if outcome_kind == "rejected" else json.dumps(
        {
            "status": "not_applicable",
            "qa_format": "factual_qa",
            "reason": "No supported example.",
        }
    )
    execute_all(plan, raw)
    path = next((plan.config.output.directory / "work" / "outcomes").rglob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        finalize_queued_run(plan)


@pytest.mark.parametrize(
    "raw, mutation, message",
    [
        (
            "not json",
            lambda value: value["rejection"].__setitem__(
                "failure_stage", "validation"
            ),
            "failure stage",
        ),
        (
            "not json",
            lambda value: value.__setitem__("outcome", "not_applicable"),
            "status",
        ),
        (
            "not json",
            lambda value: value["rejection"].__setitem__(
                "failure_reason", "different parse failure"
            ),
            "reason",
        ),
        (
            json.dumps(
                {
                    "status": "not_applicable",
                    "qa_format": "factual_qa",
                    "reason": "No supported example.",
                }
            ),
            lambda value: value.__setitem__("outcome", "rejected"),
            "status",
        ),
    ],
)
def test_tampered_nonaccepted_classification_fails_before_canonical_output(
    tmp_path, raw, mutation, message
):
    plan = prepare_queued_run(make_config(tmp_path, jobs=1))
    execute_all(plan, raw)
    path = next((plan.config.output.directory / "work" / "outcomes").rglob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        finalize_queued_run(plan)
    assert not (
        plan.config.output.directory
        / "work"
        / "finalization"
        / plan.config_hash
        / "READY"
    ).exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.__setitem__("outcome", "not_applicable"),
        lambda value: value["rejection"].__setitem__("failure_stage", "parsing"),
    ],
)
def test_rawless_generation_outcome_tamper_fails_before_canonical_output(
    tmp_path, mutation
):
    plan = prepare_queued_run(make_config(tmp_path, jobs=1))

    class TerminalFailure:
        def generate(self, messages, parameters):
            raise RuntimeError("terminal generation failure")

    execute_queued_job(
        plan.run_id,
        plan.config_hash,
        plan.jobs[0].job_id,
        client_factory=lambda _: TerminalFailure(),
    )
    path = next((plan.config.output.directory / "work" / "outcomes").rglob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="raw-less.*generation"):
        finalize_queued_run(plan)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (
            lambda value: value["rejection"].__setitem__(
                "failure_reason",
                value["rejection"]["failure_reason"].replace(
                    "after 3 attempts", "after 2 attempts"
                ),
            ),
            "retry count",
        ),
        (
            lambda value: value["rejection"].__setitem__(
                "failure_stage", "validation"
            ),
            "raw-less.*generation",
        ),
    ],
)
def test_exhausted_generation_tamper_fails(tmp_path, mutation, message):
    plan = prepare_queued_run(make_config(tmp_path, jobs=1))

    class AlwaysTimeout:
        def generate(self, messages, parameters):
            raise httpx.ReadTimeout("still unavailable")

    execute_queued_job(
        plan.run_id,
        plan.config_hash,
        plan.jobs[0].job_id,
        client_factory=lambda _: AlwaysTimeout(),
        sleep=lambda _: None,
    )
    path = next((plan.config.output.directory / "work" / "outcomes").rglob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        finalize_queued_run(plan)


def test_finalization_interruption_recovers_manifest_last_and_is_byte_stable(tmp_path):
    plan = prepare_queued_run(make_config(tmp_path, jobs=2))
    execute_all(plan)

    def interrupt(name):
        if name == "dataset.jsonl":
            raise RuntimeError("simulated replacement interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        finalize_queued_run(plan, after_replace=interrupt)
    manifest = json.loads(
        (plan.config.output.directory / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["run_status"] == "running"
    assert (plan.config.output.directory / "work" / "finalization" / plan.config_hash / "READY").exists()

    completed = finalize_queued_run(plan)
    assert completed.run_status == "completed"
    names = ("dataset.jsonl", "rejected.jsonl", "checkpoint.jsonl", "manifest.json")
    before = {name: (plan.config.output.directory / name).read_bytes() for name in names}
    finalize_queued_run(plan)
    assert before == {
        name: (plan.config.output.directory / name).read_bytes() for name in names
    }


def test_concurrent_finalizers_serialize_on_one_completed_generation(tmp_path):
    plan = prepare_queued_run(make_config(tmp_path, jobs=3))
    execute_all(plan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        manifests = list(pool.map(lambda _: finalize_queued_run(plan), range(2)))
    assert manifests[0] == manifests[1]
    assert manifests[0].completed_at == manifests[1].completed_at


@pytest.mark.parametrize(
    "language, source_text, question, answer, evidence",
    [
        (
            "english",
            "The support desk opens at eight.",
            "When does the support desk open?",
            "The support desk opens at eight.",
            "The support desk opens at eight.",
        ),
        (
            "tagalog",
            "Bukas ang tanggapan tuwing Lunes.",
            "Kailan bukas ang tanggapan?",
            "Bukas ang tanggapan tuwing Lunes.",
            "Bukas ang tanggapan tuwing Lunes.",
        ),
        (
            "taglish",
            "Mag-upload ng valid ID para ma-complete ang request.",
            "Ano ang i-upload para ma-complete ang request?",
            "Mag-upload ng valid ID para ma-complete ang request.",
            "Mag-upload ng valid ID para ma-complete ang request.",
        ),
    ],
)
def test_queued_multilingual_no_translation_and_exact_evidence(
    tmp_path, language, source_text, question, answer, evidence
):
    source = tmp_path / f"{language}.txt"
    source.write_text(source_text, encoding="utf-8")
    config = make_config(tmp_path, name=language, jobs=1, output=f"out-{language}")
    config = config.model_copy(
        update={"input": config.input.model_copy(update={"path": source, "language": language})}
    )
    raw = json.dumps(
        {
            "status": "generated",
            "qa_format": "factual_qa",
            "messages": [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ],
            "evidence": [evidence],
        },
        ensure_ascii=False,
    )
    plan = prepare_queued_run(config)
    execute_all(plan, raw)
    finalize_queued_run(plan)
    row = canonical_rows(config.output.directory)[0]
    assert row["language"] == language
    assert row["generation_parameters"]["source_language_origin"] == "input_default"
    assert row["messages"][1]["content"] == answer
    assert row["evidence"] == [evidence]
    assert evidence in source_text


def test_two_real_worker_processes_enforce_singleton_sqlite_consumer(tmp_path):
    database = tmp_path / "singleton.db"
    environment = {**os.environ, "LLM_DATA_GEN_QUEUE_DB": str(database), "PYTHONUNBUFFERED": "1"}
    command = [sys.executable, "-m", "llm_data_gen", "worker", "--workers", "1"]
    first = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lock_path = database.with_name(f"{database.name}.consumer.lock")
    deadline = time.monotonic() + 5
    while (not lock_path.exists() or not lock_path.read_text().strip()) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert first.poll() is None
    second = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=5)
    assert second.returncode != 0
    assert "another llm-data-gen consumer" in second.stderr
    assert "--workers N" in second.stderr
    first.terminate()
    first.wait(timeout=5)


def test_real_huey_thread_consumer_bounds_instrumented_endpoint_concurrency(
    tmp_path, monkeypatch
):
    class Handler(BaseHTTPRequestHandler):
        active = 0
        maximum = 0
        lock = threading.Lock()

        def do_POST(self):
            with self.lock:
                type(self).active += 1
                type(self).maximum = max(type(self).maximum, type(self).active)
            time.sleep(0.08)
            body = json.dumps(
                {"choices": [{"message": {"content": response()}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            with self.lock:
                type(self).active -= 1

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    database = tmp_path / "bounded-real.db"
    monkeypatch.setenv("LLM_DATA_GEN_QUEUE_DB", str(database))
    config = make_config(tmp_path, jobs=9)
    config = config.model_copy(
        update={
            "endpoint": config.endpoint.model_copy(
                update={"base_url": f"http://127.0.0.1:{server.server_port}/v1"}
            )
        }
    )
    plan = prepare_queued_run(config)
    import importlib
    import llm_data_gen.queue_huey as adapter

    adapter = importlib.reload(adapter)
    mark_enqueued(plan, [job.job_id for job in plan.jobs])
    for job in plan.jobs:
        adapter.enqueue_payload(task_payload(plan, job.job_id))
    environment = {**os.environ, "LLM_DATA_GEN_QUEUE_DB": str(database)}
    worker = subprocess.Popen(
        [sys.executable, "-m", "llm_data_gen", "worker", "--workers", "3"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while queue_status(plan)["terminal"] < len(plan.jobs) and time.monotonic() < deadline:
        time.sleep(0.03)
    worker.terminate()
    worker.wait(timeout=5)
    server.shutdown()
    thread.join(timeout=5)
    assert queue_status(plan)["terminal"] == len(plan.jobs)
    assert Handler.maximum == 3
    assert queue_status(plan)["broker"]["messages_present"] == 0


def test_lost_dequeue_recovery_uses_cli_reconciliation_then_real_consumer_restart(
    tmp_path, monkeypatch
):
    calls = 0

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            nonlocal calls
            calls += 1
            body = json.dumps(
                {"choices": [{"message": {"content": response("Recovered job?")}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    database = tmp_path / "lost-real.db"
    monkeypatch.setenv("LLM_DATA_GEN_QUEUE_DB", str(database))
    config = make_config(tmp_path, jobs=1, output="lost-output")
    config_path = tmp_path / "lost.yaml"
    config_path.write_text(
        f"""version: 2
name: lost-real
input:
  path: {config.input.path}
  language: english
  domain: support
chunking:
  strategy: document
endpoint:
  profile: local-qwen
  backend: static
  base_url: http://127.0.0.1:{server.server_port}/v1
  model: static-model
  api_key_env: QUEUE_TEST_API_KEY
generation:
  recipes:
    - format: factual_qa
      num_examples: 1
output:
  directory: {config.output.directory}
""",
        encoding="utf-8",
    )
    environment = {**os.environ, "LLM_DATA_GEN_QUEUE_DB": str(database)}
    enqueue = [sys.executable, "-m", "llm_data_gen", "enqueue", str(config_path)]
    subprocess.run(enqueue, env=environment, check=True, capture_output=True, text=True)

    import importlib
    import llm_data_gen.queue_huey as adapter

    adapter = importlib.reload(adapter)
    lost = adapter.huey.dequeue()
    assert lost is not None and len(adapter.huey) == 0
    plan = prepare_queued_run(config.model_copy(update={"name": "lost-real", "endpoint": config.endpoint.model_copy(update={"base_url": f"http://127.0.0.1:{server.server_port}/v1"})}))
    status = queue_status(plan)
    assert status["application"]["pending"] == 1
    assert status["broker"]["messages_present"] == 0

    subprocess.run(enqueue, env=environment, check=True, capture_output=True, text=True)
    assert queue_status(plan)["broker"]["messages_present"] == 1
    worker = subprocess.Popen(
        [sys.executable, "-m", "llm_data_gen", "worker", "--workers", "1"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 8
    while queue_status(plan)["terminal"] < 1 and time.monotonic() < deadline:
        time.sleep(0.03)
    worker.terminate()
    worker.wait(timeout=5)
    server.shutdown()
    thread.join(timeout=5)
    assert queue_status(plan)["terminal"] == 1
    assert calls == 1
    assert finalize_queued_run(plan).accepted_jobs == 1
