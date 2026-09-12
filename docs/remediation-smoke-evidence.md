# §6.12 Manual Smoke Evidence

These commands reproduce the two manual acceptance smokes from the repository
root. They create all disposable state beneath a `mktemp` directory and do not
read or print credentials. Automated process tests separately cover the
single-consumer lock, dequeue-loss reconciliation, and a three-thread real Huey
consumer against an instrumented fake endpoint; this document does not claim a
live multi-worker run.

## Environment assumptions

- Linux/POSIX shell with `uv`, Python 3.11+, `curl`, `sha256sum`, and `timeout`.
- Network access to the package index is available for the clean base install.
- For the live smoke, the repository development environment has been installed
  with `uv sync --extra queue` and no other consumer owns the selected temporary
  SQLite database.
- The live endpoint is an unauthenticated OpenAI-compatible service. It defaults
  to `http://127.0.0.1:8080/v1` and model
  `qwen38-27b-chat-rocmfp4`; override those non-secret values with
  `LLM_DATA_GEN_SMOKE_BASE_URL` and `LLM_DATA_GEN_SMOKE_MODEL`.

## Genuinely base-only, Huey-absent install

```bash
cd /projects/llm-data-gen
SMOKE_ROOT=$(mktemp -d)
printf 'SMOKE_ROOT=%s\n' "$SMOKE_ROOT"

uv venv --python 3.11 "$SMOKE_ROOT/base-only"
uv pip install --python "$SMOKE_ROOT/base-only/bin/python" .
"$SMOKE_ROOT/base-only/bin/python" - <<'PY'
from importlib.metadata import distributions
from importlib.util import find_spec
from pathlib import Path

from llm_data_gen.config import load_run_config
from llm_data_gen.pipeline import validate_config_semantics

assert find_spec("huey") is None
config_path = Path("configs/example-customer-service.yaml")
config = load_run_config(config_path)
recipes = validate_config_semantics(config, search_root=config_path.parent)
print({
    "huey_present": False,
    "distribution_count": len(list(distributions())),
    "inline_import": "ok",
    "config_version": config.version,
    "recipe_count": len(recipes),
})
PY
```

Observed on 2026-09-12: 14 distributions, `huey_present: False`, successful
inline-pipeline import, and successful validation of the shipped V2 config.

## Bounded live one-worker queue

The following deliberately creates a one-job config and bounds the worker process
to 180 seconds. It requires the live endpoint assumption above; it sends only the
checked-in public sample text.

```bash
cd /projects/llm-data-gen
SMOKE_ROOT=${SMOKE_ROOT:-$(mktemp -d)}
printf 'SMOKE_ROOT=%s\n' "$SMOKE_ROOT"
export LLM_DATA_GEN_QUEUE_DB="$SMOKE_ROOT/huey.db"
export LLM_DATA_GEN_SMOKE_BASE_URL=${LLM_DATA_GEN_SMOKE_BASE_URL:-http://127.0.0.1:8080/v1}
export LLM_DATA_GEN_SMOKE_MODEL=${LLM_DATA_GEN_SMOKE_MODEL:-qwen38-27b-chat-rocmfp4}

uv run python - "$SMOKE_ROOT/live.yaml" "$SMOKE_ROOT/output" <<'PY'
import os
import sys
from pathlib import Path

import yaml

config = yaml.safe_load(Path("configs/example-customer-service.yaml").read_text())
config["name"] = "remediation-live-one-worker-smoke"
config["input"]["path"] = str(Path("data/raw/globe-broadband.txt").resolve())
config["endpoint"]["base_url"] = os.environ["LLM_DATA_GEN_SMOKE_BASE_URL"]
config["endpoint"]["model"] = os.environ["LLM_DATA_GEN_SMOKE_MODEL"]
config["generation"] = {
    "recipes": [{"format": "factual_qa", "num_examples": 1}]
}
config["output"]["directory"] = sys.argv[2]
Path(sys.argv[1]).write_text(yaml.safe_dump(config, sort_keys=False))
PY

curl --fail --silent --show-error --max-time 5 \
  "$LLM_DATA_GEN_SMOKE_BASE_URL/models" >/dev/null
ENQUEUE_JSON=$(uv run llm-data-gen enqueue "$SMOKE_ROOT/live.yaml")
RUN_ID=$(printf '%s' "$ENQUEUE_JSON" | uv run python -c \
  'import json,sys; print(json.load(sys.stdin)["run_id"])')
timeout --signal=TERM 180 uv run llm-data-gen worker --workers 1 \
  >"$SMOKE_ROOT/worker.log" 2>&1 &
WORKER_PID=$!

COMPLETE=0
for _ in $(seq 1 180); do
  if uv run llm-data-gen queue-status "$RUN_ID" | uv run python -c \
    'import json,sys; raise SystemExit(json.load(sys.stdin)["terminal"] != 1)'
  then
    COMPLETE=1
    break
  fi
  sleep 1
done
kill -TERM "$WORKER_PID" 2>/dev/null || true
wait "$WORKER_PID" || true
test "$COMPLETE" -eq 1

uv run llm-data-gen finalize "$RUN_ID" >/dev/null
sha256sum "$SMOKE_ROOT/output"/{dataset,rejected,checkpoint}.jsonl \
  "$SMOKE_ROOT/output/manifest.json" >"$SMOKE_ROOT/hashes.before"
uv run llm-data-gen finalize "$RUN_ID" >/dev/null
sha256sum "$SMOKE_ROOT/output"/{dataset,rejected,checkpoint}.jsonl \
  "$SMOKE_ROOT/output/manifest.json" >"$SMOKE_ROOT/hashes.after"
diff -u "$SMOKE_ROOT/hashes.before" "$SMOKE_ROOT/hashes.after"

uv run python - "$SMOKE_ROOT/output" "$RUN_ID" <<'PY'
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
manifest = json.loads((output / "manifest.json").read_text())
rows = [json.loads(line) for line in (output / "dataset.jsonl").read_text().splitlines()]
rejected = (output / "rejected.jsonl").read_text().splitlines()
attempts = list((output / "work" / "attempts").rglob("*.json"))
assert manifest["requested_jobs"] == manifest["accepted_jobs"] == 1
assert manifest["rejected_jobs"] == manifest["not_applicable_jobs"] == 0
assert len(rows) == len(attempts) == 1 and not rejected
assert rows[0]["language"] == "english"
source = Path("data/raw/globe-broadband.txt").read_text()
assert rows[0]["evidence"] and all(item in source for item in rows[0]["evidence"])
print({"run_id": sys.argv[2], "accepted": 1, "attempts": 1,
       "language": "english", "exact_evidence": True,
       "repeated_finalization_hashes_equal": True})
PY
```

Observed on 2026-09-12: the endpoint probe returned HTTP 200 and the run finished
in 10.5 seconds with one worker, one durable attempt, one accepted English
`sft_chat_v1` row, exact evidence, zero rejections, and identical hashes across
repeated finalization. One job in 10.5 seconds is approximately 5.7 jobs/min by
simple arithmetic, but this is not a throughput benchmark and does not establish
scaling or live multi-worker performance.

Both procedures intentionally leave the printed `SMOKE_ROOT` directory available
for inspection; it is disposable and can be removed after reviewing the evidence.
