# llm-data-gen Frozen V2 Plan and Status

**Status:** V2 frozen; optional Huey + SQLite queue accepted after §6.12
remediation

**Freeze and queue acceptance date:** 2026-09-12

**Product contracts:** [spec.md](spec.md) and
[spec/qa-formats.md](spec/qa-formats.md)

**Active tracker:** [TODO.md](TODO.md) — no active required work

**Historical record:**
[IMPLEMENTATION_PLAN_HISTORY_2026-09-12.md](IMPLEMENTATION_PLAN_HISTORY_2026-09-12.md)

## 0. Canonical Current Baseline

The accepted product is a config-driven V2 pipeline that converts local source
corpora into grounded `sft_chat_v1` interactions. Inline execution is the
installed and runtime default. Huey + SQLite is an optional local process queue;
it does not reopen or alter the frozen dataset contract.

The non-negotiable rule is:

```text
format may change; source language must not
```

The pipeline does not translate, choose a target language, or intentionally add
or remove code-switching. English remains English, Filipino/Tagalog remains
Filipino/Tagalog, and Taglish remains Taglish.

## 1. Frozen Product Contract

A strict `version: 2` run configuration controls the corpus and field mappings,
required source language, chunking, OpenAI-compatible endpoint/model, prompt pack
or explicit recipes, validation, output directory, and resume policy.

Language is declared provenance, not a generation dimension:

```text
SourceDocument(language + origin)
  -> Chunk(language + origin)
  -> GenerationJob(language + origin)
  -> SFTRecord(language copied by pipeline)
```

- `input.language` is required and supports `english`, `filipino`, `tagalog`, and
  `taglish`.
- Text and Markdown inherit that configured default. JSON, JSONL, CSV, and
  Parquet may opt into per-record overrides through `input.language_field`.
- Missing/blank record labels use the default; unsupported non-empty labels
  become source-stage rejections. Mixed-language plain-text directories require
  separate runs or conversion to structured records.
- Recipes select format, settings, and sample count. Jobs expand as
  `chunk × format × sample index`; language never multiplies jobs.
- Prompts preserve source language, register, terminology, and existing
  code-switching and forbid translation and unsupported claims.
- The strict model-facing schema omits `language`. A model-returned language key
  is rejected; the pipeline owns the accepted row label.
- Exact evidence, strict message/format validation, normalized exact duplicate
  rejection, `not_applicable`, row-level failure isolation, targeted retry, and
  resume remain part of the contract.

## 2. Current Architecture

```text
RunConfig V2
  -> reader and chunker registries
  -> recipe resolution and deterministic job expansion
  -> inline executor (default)
       or immutable run snapshot -> Huey/SQLite -> one consumer process
  -> shared single-job inference, parsing, and validation
  -> terminal job outcomes
  -> inline writer or queued deterministic finalizer
  -> canonical JSONL artifacts and derived exports
```

The shared execution seams are:

```text
prepare_run(config) -> RunPlan
execute_job(run_plan, job_id, client) -> JobOutcome
finalize_run(run_plan) -> RunManifest
```

Readers support text, Markdown, JSON, JSONL, CSV, and Parquet. Chunkers are
`document`, `recursive_text`, `markdown_sections`, and `fixed_tokens`. All ten
formats in [the QA-format contract](spec/qa-formats.md) are implemented.

Every run writes:

```text
manifest.json
config.resolved.yaml
sources.jsonl
chunks.jsonl
dataset.jsonl
rejected.jsonl
checkpoint.jsonl
```

Queued runs also retain `queue-state.json` and immutable private snapshots,
attempts, states, outcomes, locks, and finalization staging under `work/`. Private
chunk text does not change the public `include_chunk_text` behavior. JSONL is
canonical; CSV, Parquet, and legacy JSONL are derived exports.

## 3. Compatibility Decisions

- V1 configurations and recipe/prompt-pack `languages` arrays fail before
  inference with actionable `input.language` migration guidance.
- The historical target-language pack is inactive and intentionally rejected by
  the V2 loader. Active built-in and YAML packs contain no language arrays.
- Prompt, recipe, job, and config identity changes prevent V1/V2 resume mixing.
- Config hashes prevent incompatible output-directory reuse.
- The deprecated argument-based compatibility CLI remains callable but requires
  `--source-language`; new work uses YAML V2.
- Historical V1 and intermediate V2 outputs remain separate from final V2 runs.

## 4. Frozen V2 Acceptance Record

The original V2 freeze gate passed 54 deterministic tests plus source/test
compilation. Both shipped configurations validated and inspected without
inference. The final local r4 language-preservation runs produced:

- Globe broadband, English: 10/10 accepted;
- Proclamation No. 1041, Tagalog: 5/5 accepted;
- QC SSDD online services, Taglish: 5/5 accepted.

All 20 accepted rows passed mechanical schema, exact-evidence, roles/turns,
format-limit, identity/signature, prompt-version, and language-provenance checks.
Manual review found no obvious unsupported factual claim or language-preservation
violation, and normal resume generated zero new rows for all three runs.

This is the frozen product-baseline record, not the current test count. The dated
[history](IMPLEMENTATION_PLAN_HISTORY_2026-09-12.md) preserves its implementation
narrative, paths, retries, material choices, and qualitative findings.

## 5. Accepted Optional Queue Contract

The queue is a persistent local buffer for planned jobs and a bound on active
inference calls. It is not a promise of faster inference.

### Scope and lifecycle

```text
llm-data-gen enqueue CONFIG
llm-data-gen worker [--workers N]
llm-data-gen queue-status RUN
llm-data-gen finalize RUN
```

- `enqueue` prepares or validates an immutable snapshot and submits only jobs
  without terminal outcomes. Queue messages contain only `run_id`, expected
  `config_hash`, and `job_id`; configured API-key values are not serialized.
- `worker` uses `--workers`, then `LLM_DATA_GEN_WORKERS`, then default `1`.
  Values below one are rejected. `N` is thread concurrency in the one supported
  consumer process and is not a request/token rate limit.
- A queue-database process lock rejects a second consumer. The accepted scope is
  one host, one SQLite database, and one consumer process with configurable
  threads—not multiple consumers, multi-host work, or distributed leases.
- `queue-status` separates per-run application state from the broker-wide
  `messages_present` count; it never implies that a particular pending/running
  job still has a SQLite message.
- `finalize` requires terminal outcomes for all planned jobs and is idempotent.
  The output directory or registered run ID may identify the run.

### Recovery, retries, and integrity

- SQLiteHuey removes a message when dequeued. After interruption, the operator
  must run `enqueue CONFIG` to reconcile unfinished application state and submit
  fresh messages. Consumer restart alone is not recovery; automatic startup
  reconciliation is deferred.
- A durable attempt-owned record is written and fsynced before every inference
  call. Connection errors, timeouts, HTTP 429, and HTTP 5xx responses may retry
  twice with bounded backoff, for at most three calls across the job's complete
  lifetime, including interruption and redelivery.
- Parsing, schema, grounding, unsupported configuration, and `not_applicable`
  outcomes are terminal rather than blindly retried. Exhausted transient errors
  become one inspectable generation rejection with sanitized attempt history.
- A job-scoped lock and immutable `config_hash + job_id` outcome make repeated
  delivery application-idempotent. Workers never append to canonical JSONL.
- Before persistence and finalization, every accepted, rejected, and
  `not_applicable` outcome is rebound to its snapshotted job/chunk. Identity,
  source, format, language/origin, prompt, model/backend, rejection fields,
  provenance, normalized signature, structure, and exact evidence are checked.
- Finalization holds a run lock, resolves duplicates in planned-job order, builds
  and hashes a complete staged generation, replaces dataset/rejection/checkpoint
  files deterministically, and writes the completed manifest last as the commit
  marker. This is safely rerunnable recoverable staging, not a true multi-file
  filesystem transaction.

## 6. Queue Acceptance and Remediation

The initial queue slice established the shared execution seam, snapshots,
optional Huey adapter, CLI lifecycle, persistent SQLite burst behavior, and
basic concurrency/retry/finalization coverage. Its post-implementation review
found that accepted reliability claims required stronger cross-interruption
retry accounting, full outcome binding, an enforceable process model, explicit
dequeue-loss recovery, and manifest-last recovery. That superseded first-slice
narrative is preserved in the dated
[history](IMPLEMENTATION_PLAN_HISTORY_2026-09-12.md#queue-extension-history-2026-09-12).

### 6.12 Completed Pareto Reliability Remediation

The bounded remediation closed the correctness and operational-truthfulness gaps
without expanding the queue into a distributed orchestration system:

1. Durable attempt records enforce the three-call lifetime budget across
   interruption, recovery, and redelivery and retain sanitized diagnostics.
2. Full outcome-to-plan validation covers accepted, rejected, and
   `not_applicable` payloads before persistence and finalization.
3. A database-scoped process lock enforces one SQLite consumer process, while
   `--workers N` provides the documented in-process concurrency ceiling.
4. `enqueue CONFIG` is the explicit, tested reconciliation boundary for dequeued
   work; no consumer-restart redelivery is claimed.
5. Run-scoped locking and complete staging make manifest-last finalization
   recoverable, serialized, rerunnable, and byte-stable.
6. Focused regressions cover retry interruption, tampered outcomes, process
   locking, real-Huey thread concurrency, dequeue loss and CLI reconciliation,
   interrupted/concurrent finalization, queue/inline equivalence across all
   outcome classes, and queued English, Tagalog, and Taglish preservation.

All remediation items passed; none remain active in [TODO.md](TODO.md).

## 7. Final Verification Evidence (2026-09-12)

Exact manual commands and environment assumptions are retained in
[docs/remediation-smoke-evidence.md](docs/remediation-smoke-evidence.md).

- Focused gate: `uv run pytest -q tests/test_queueing.py` — **77 passed**.
- Full gate: `uv run pytest -q` — **131 passed**, including the real SQLite
  50-job persistence burst.
- `uv run python -m compileall -q src tests`, `uv lock --check`, and
  `git diff --check` passed.
- Real-process tests reject a second consumer for one database and recover an
  intentionally dequeued/lost message via the actual `enqueue CONFIG` CLI and a
  real consumer restart, with one inference call and no duplicate outcome.
- A real Huey consumer with three threads processed nine jobs against an
  instrumented fake OpenAI-compatible endpoint and observed exactly three
  concurrent calls. This proves the local concurrency bound only; it is not live
  model throughput evidence.
- A fresh base-only environment installed 14 distributions, contained no Huey
  module, imported the inline pipeline, and validated the shipped V2 config.
- One bounded live single-worker queue smoke against the available local endpoint
  completed one job in 10.5 seconds: one accepted English `sft_chat_v1` row, one
  durable attempt, exact source evidence, zero rejections, empty broker, and
  identical dataset/rejected/checkpoint/manifest hashes after repeated
  finalization. The arithmetic rate is about **5.7 jobs/min**, but one job is not
  a throughput benchmark and supplies no evidence about scaling or live
  multi-worker performance.

## 8. Known Limits and Non-Binding Future Work

Current limits:

- language labels are curator-declared provenance, not semantic detection;
- exact evidence proves substring support, not complete semantic factuality;
- prompt constraints cannot mathematically prove zero language drift;
- deduplication is normalized exact matching, not semantic similarity;
- some frozen demonstration rows have weak semantic fit or awkward wording;
- SQLite remains local/single-host/single-consumer-process, with no automatic
  startup reconciliation, rate limiting, cleanup, priorities, scheduling,
  autoscaling, external broker, or distributed locking.

Possible future work is non-binding and needs separate scope: reviewed semantic
language/factuality evaluation, fuzzy or embedding deduplication, semantic
chunking, multi-example inference batching, request/token rate limiting, an
external broker and multi-host orchestration, training, or a graphical interface.
Any extension must preserve V2 language provenance, deterministic identities and
ordering, exact-evidence behavior, row-level failure isolation, and output
compatibility.
