# llm-data-gen Test Contract

## Baseline Compatibility

- Legacy argument-based CLI remains callable.
- Legacy five-family pipeline retains strict parsing, grounding checks, skipped-row sidecar, and partial-line-safe resume.

## Configuration

- Valid YAML loads through Pydantic.
- Unknown fields, profiles, formats, source languages, packs, and chunkers fail before inference.
- Config V2 requires `input.language`; V1 target-language arrays fail with migration guidance.
- Relative paths resolve against the config file.
- Numeric bounds and overlap rules are enforced.
- Resolved config hashing is deterministic.
- Secret values never appear in resolved artifacts.
- Explicit recipes deterministically replace matching pack recipes.

## Corpus Readers

- Single files and directories are supported.
- Directory traversal is sorted and reproducible.
- JSONL processes every valid record.
- Text, Markdown, JSON, JSONL, CSV, and Parquet normalize into one source schema.
- Malformed and empty records are isolated.
- Source IDs and checksums are stable.
- Text/Markdown inherit the configured language; structured records support
  `language_field` defaults and overrides, including mixed corpora.
- Invalid record-level languages are isolated with record provenance.

## Chunking

- `document`, `recursive_text`, `markdown_sections`, and `fixed_tokens` share one chunk schema.
- Chunk boundaries and IDs are deterministic.
- Recursive overlap is honored without infinite loops.
- Markdown headings remain section boundaries when size permits.
- Token windows honor size and overlap.

## Registries and Prompting

- All ten QA formats and four source-language labels are registered.
- Legacy format aliases resolve correctly.
- Prompts preserve source language/register and forbid translation or changes to code-switching.
- Prompt packs and recipe overrides expand deterministically.
- Prompt name, version, and hash are retained.
- Prompt construction has no corpus-reader, chunker, or backend coupling.

## Generation Jobs

- Every chunk expands across format and sample index only.
- Jobs inherit language from chunks; language never multiplies job counts.
- Job and recipe hashes are deterministic.
- Generation-setting changes alter job identity.
- Inspection reports exact expected jobs without inference.

## Parsing and Validation

- Raw and fenced JSON objects parse.
- Additional prose, arrays, and malformed JSON fail closed.
- Two-turn and multi-turn messages parse structurally.
- Roles start with user, alternate, and end with assistant when required.
- The strict model-facing schema rejects a model-returned language label.
- Source, chunk, job, and accepted-row language provenance agree.
- Intent responses require `metadata.intent`.
- Multi-turn examples honor structural limits.
- Exact evidence exists in the source.
- Exact normalized conversation duplicates are rejected.
- `not_applicable` requires a reason.

## Output and Resume

- Every run creates all seven standard artifacts.
- Accepted rows validate as `sft_chat_v1`.
- Manifests report counts, dimensions, versions, endpoint labels, and run status.
- Resolved configurations are redacted.
- Incompatible config hashes cannot share an output directory.
- Terminal jobs are not regenerated.
- Partial checkpoint lines are ignored.
- Selected rejected stages can be retried.
- A successful retry supersedes the prior terminal outcome in summary counts.

## Exports

- Canonical JSONL exports to CSV.
- Canonical JSONL exports to Parquet.
- Flexible maps are JSON-encoded in Parquet.
- Canonical chat records export to the legacy input/output schema.

## Commands

- `validate-config` performs no inference.
- `inspect` performs no inference.
- Registry listing commands reflect formats and source-language labels.
- `run` writes a standard dataset directory.
- `retry` only re-executes selected rejected stages.
- `export` produces the requested derived format.

## Optional Huey + SQLite Queue

- Inline import and execution remain Huey-free.
- Immutable run snapshots bind config, sources, chunks, and ordered jobs.
- Queue messages contain only run/config/job identity and no configured secret.
- One consumer process is enforced per SQLite database; positive worker count
  controls bounded thread concurrency in that process.
- Durable attempt records enforce at most three transient inference calls across
  interruption, reconciliation, and redelivery; deterministic failures do not
  retry blindly.
- `enqueue CONFIG` reconciles unfinished application state after dequeue loss;
  consumer restart alone is not treated as message recovery.
- Accepted, rejected, and `not_applicable` outcomes are fully rebound to their
  plan and revalidated before canonical output.
- Redelivery cannot duplicate terminal outcomes or accepted rows, and concurrent
  runs remain isolated.
- Finalization resolves duplicates in planned order, serializes concurrent
  finalizers, recovers after staged replacement interruption, and writes the
  completed manifest last with byte-stable reruns.
- Queue and inline execution agree for deterministic accepted, parsing-rejected,
  validation-rejected, and `not_applicable` fixtures.
- Queued English, Tagalog/Filipino, and Taglish fixtures preserve provenance,
  exact evidence, and the no-translation contract.

## Final Gate

~~~bash
uv run pytest -q
uv run pytest -q tests/test_queueing.py
uv run python -m compileall -q src tests
uv lock --check
git diff --check
~~~

The accepted 2026-09-12 gate was 131 full tests and 77 focused queue/remediation
tests. Live evidence is a bounded one-job, one-worker smoke, not a throughput
benchmark; configured three-worker concurrency is proven by a real Huey consumer
against an instrumented fake endpoint.
