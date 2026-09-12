# llm-data-gen

`llm-data-gen` turns local source corpora into grounded, validated
`sft_chat_v1` datasets through a reproducible YAML configuration. It provides
deterministic source and job identities, exact-evidence validation, row-level
failure isolation, resume/retry support, and derived CSV, Parquet, and legacy
JSONL exports.

> **Frozen V2 contract:** change the interaction format, never the source
> language. The pipeline does not translate, choose a target language, or
> intentionally add or remove code-switching.

English remains English, Filipino/Tagalog remains Filipino/Tagalog, and Taglish
remains Taglish. Language is declared input provenance that flows from source to
chunk to job to accepted row; it is not a recipe dimension or a label selected
by the model.

## Quick start: inline execution (default)

Requirements: Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
uv run llm-data-gen validate-config configs/example-customer-service.yaml
uv run llm-data-gen inspect configs/example-customer-service.yaml
uv run llm-data-gen run configs/example-customer-service.yaml
```

`validate-config` and `inspect` do not perform inference. `run` is the default,
synchronous path and does not import or require Huey. Start the OpenAI-compatible
endpoint configured in the YAML before running inference.

A minimal V2 configuration looks like this when stored in `configs/`:

```yaml
version: 2
name: minimal-source-preserving-v2
input:
  path: ../data/raw/globe-broadband.txt
  language: english
generation:
  recipes:
    - format: factual_qa
      num_examples: 1
output:
  directory: ../output/minimal-source-preserving-v2
```

Paths are resolved relative to the configuration file. Omitted sections use
validated defaults. See
[`configs/example-customer-service.yaml`](configs/example-customer-service.yaml)
for an explicit example.

## Optional persistent queue: Huey + SQLite

Use the queue when planned jobs need a persistent local buffer or bounded
concurrent inference. It changes execution, not dataset identity, validation, or
the source-language contract.

```bash
uv sync --extra queue
export LLM_DATA_GEN_QUEUE_DB="$PWD/.llm-data-gen/huey.db"

# Prepare an immutable run snapshot and enqueue unfinished jobs.
uv run llm-data-gen enqueue configs/example-customer-service.yaml

# Keep this single consumer process running (one worker by default).
uv run llm-data-gen worker --workers 1
```

From another shell, using the same `LLM_DATA_GEN_QUEUE_DB`:

```bash
uv run llm-data-gen queue-status output/example-customer-service-source-preserving-v2
uv run llm-data-gen finalize output/example-customer-service-source-preserving-v2
```

The output directory or the `run_id` printed by `enqueue` can identify a queued
run. `finalize` succeeds only after every planned job has a terminal application
outcome.

Operational limits and recovery rules:

- SQLite queueing is supported only on one local host with exactly one consumer
  process per queue database. `--workers N` supplies thread concurrency inside
  that process; a second consumer is rejected.
- Worker-count precedence is `--workers`, then `LLM_DATA_GEN_WORKERS`, then `1`.
  It limits concurrent generation calls, not requests/minute or tokens/minute.
- After interruption, run `enqueue CONFIG` again before restarting/continuing the
  consumer. This is the manual reconciliation step for unfinished work. Consumer
  restart alone cannot restore a message SQLiteHuey removed at dequeue.
- Transient connection, timeout, HTTP 429, and HTTP 5xx failures receive at most
  two retries. Durable attempt records enforce at most three inference calls per
  job across interruption, recovery, and redelivery.
- Workers write immutable per-job outcomes, never canonical shared JSONL.
  Finalization runs in planned-job order, stages the complete canonical
  generation, replaces data files deterministically, and writes the completed
  manifest last. This is recoverable staging, not a multi-file transaction.
- There is no automatic startup reconciliation, per-request/token rate limiter,
  multi-consumer process mode, multi-host execution, or external broker adapter.

## Architecture

```text
RunConfig V2
  -> readers -> SourceDocument(language + origin)
  -> chunker -> Chunk(language + origin)
  -> recipes -> deterministic GenerationJob(language + origin)
  -> inline executor (default)
       or immutable snapshot -> Huey/SQLite -> one consumer process
  -> shared inference, parsing, and validation
  -> terminal job outcomes
  -> deterministic writer/finalizer
  -> canonical dataset and derived exports
```

Jobs expand as `chunk × format × sample index`, never by language. Both
execution paths share preparation, single-job execution, validation, and output
contracts. Accepted rows must pass strict schema and conversation checks,
format-specific rules, source/chunk/job language-provenance equality, exact
source-evidence checks, and normalized exact deduplication. A grounded request
that the source cannot support may become `not_applicable`.

## Inputs, formats, and outputs

Readers support text, Markdown, JSON, JSONL, CSV, and Parquet files or
directories. `input.language` is required and accepts `english`, `filipino`,
`tagalog`, or `taglish`. Structured records may opt into per-record overrides
with `input.language_field`; mixed-language plain-text directories require
separate runs.

Chunkers are `document`, `recursive_text`, `markdown_sections`, and
`fixed_tokens`. Ten interaction formats are available: `factual_qa`,
`transactional`, `troubleshooting`, `scenario_response`, `multi_turn`,
`feedback_response`, `conflict_resolution`, `needs_recommendation`, `cross_sell`,
and `intent_response`.

Every run creates the seven canonical artifacts:

```text
manifest.json          run status, counts, model, and hashes
config.resolved.yaml   effective secret-free configuration
sources.jsonl          normalized source provenance
chunks.jsonl           chunks and inherited provenance
dataset.jsonl          accepted sft_chat_v1 rows
rejected.jsonl         source and row-level failures
checkpoint.jsonl       terminal outcomes used for resume
```

Queued runs additionally retain `queue-state.json` and private immutable state
under `work/`. JSONL is canonical; use `llm-data-gen export` for CSV, Parquet, or
legacy JSONL.

## Testing and status

```bash
uv sync --extra dev
uv run pytest -q
uv run pytest -q tests/test_queueing.py
uv run python -m compileall -q src tests
uv lock --check
git diff --check
```

Frozen V2 and the optional local queue are accepted as of 2026-09-12. The final
verification gate is **131 full tests** and **77 focused queue/remediation tests**,
plus successful compilation, lock, and diff checks. Real-process tests prove the
single-consumer lock and manual dequeue-loss reconciliation. Three-worker
concurrency was proved only against an instrumented fake OpenAI-compatible
endpoint.

The live queue smoke was deliberately just one job: it completed in 10.5 seconds
(about 5.7 jobs/min if arithmetically extrapolated), with one accepted English
row, one durable attempt, exact evidence, zero rejections, and byte-stable
repeated finalization. That observation is **not a throughput benchmark** and
does not establish scaling or multi-worker live-model performance.

Known limits remain: declared language provenance is not semantic language
detection; exact evidence is not full semantic factuality; prompt constraints
cannot prove zero language drift; and deduplication is exact rather than semantic.

## Project documents

- [Canonical implementation plan and status](IMPLEMENTATION_PLAN.md)
- [Active tracker](TODO.md)
- [Product specification](spec.md)
- [QA-format contract](spec/qa-formats.md)
- [Test contract](TEST_CASES.md)
- [Reproducible remediation smoke evidence](docs/remediation-smoke-evidence.md)
- [Dated V1/V2 implementation history](IMPLEMENTATION_PLAN_HISTORY_2026-09-12.md)
- [GitHub repository](https://github.com/ogbinar/llm-data-gen)
