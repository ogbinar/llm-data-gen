from __future__ import annotations

from huey import SqliteHuey

from .queueing import execute_queued_job, queue_database_path


database_path = queue_database_path()
database_path.parent.mkdir(parents=True, exist_ok=True)
huey = SqliteHuey(
    "llm-data-gen",
    filename=str(database_path),
    results=False,
    immediate=False,
    fsync=True,
)


@huey.task(retries=0, name="llm_data_gen.generate_job")
def generate_job(run_id: str, config_hash: str, job_id: str) -> dict[str, object]:
    outcome = execute_queued_job(run_id, config_hash, job_id)
    return {
        "run_id": outcome.run_id,
        "job_id": outcome.job_id,
        "outcome": outcome.outcome,
        "attempt_count": outcome.attempt_count,
    }


def enqueue_payload(payload: dict[str, str]) -> None:
    generate_job(payload["run_id"], payload["config_hash"], payload["job_id"])
