# llm-data-gen Test Contract

## Baseline Compatibility

- Legacy argument-based CLI remains callable.
- Legacy five-family pipeline retains strict parsing, grounding checks, skipped-row sidecar, and partial-line-safe resume.

## Configuration

- Valid YAML loads through Pydantic.
- Unknown fields, profiles, formats, languages, packs, and chunkers fail before inference.
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

## Chunking

- `document`, `recursive_text`, `markdown_sections`, and `fixed_tokens` share one chunk schema.
- Chunk boundaries and IDs are deterministic.
- Recursive overlap is honored without infinite loops.
- Markdown headings remain section boundaries when size permits.
- Token windows honor size and overlap.

## Registries and Prompting

- All ten QA formats and three languages are registered.
- Legacy format aliases resolve correctly.
- Format and language instructions compose independently.
- Prompt packs and recipe overrides expand deterministically.
- Prompt name, version, and hash are retained.
- Prompt construction has no corpus-reader, chunker, or backend coupling.

## Generation Jobs

- Every chunk expands across format, language, and sample index.
- Job and recipe hashes are deterministic.
- Generation-setting changes alter job identity.
- Inspection reports exact expected jobs without inference.

## Parsing and Validation

- Raw and fenced JSON objects parse.
- Additional prose, arrays, and malformed JSON fail closed.
- Two-turn and multi-turn messages parse structurally.
- Roles start with user, alternate, and end with assistant when required.
- Format and language labels match the requested job.
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
- Registry listing commands reflect implementation state.
- `run` writes a standard dataset directory.
- `retry` only re-executes selected rejected stages.
- `export` produces the requested derived format.

## Final Gate

~~~bash
uv run pytest -q
~~~

A live smoke against the configured endpoint follows the deterministic suite.

