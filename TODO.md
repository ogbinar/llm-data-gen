# llm-data-gen TODO

**Plan:** `IMPLEMENTATION_PLAN.md`  
**Status:** Complete  
**Completed:** 2026-09-11

## Working Rules

- [x] Follow the frozen implementation phases in order.
- [x] Preserve the original V1 compatibility path during migration.
- [x] Add tests alongside each implementation slice.
- [x] Keep JSONL and `sft_chat_v1` canonical.
- [x] Keep formats, languages, chunkers, readers, endpoints, and writers decoupled.
- [x] Prevent incompatible resolved configurations from sharing an output directory.

## Completed V1 Baseline

- [x] Preserve the `uv`-managed package and CLI entrypoint.
- [x] Preserve deterministic legacy chunking and five-family generation.
- [x] Preserve OpenAI-compatible backend switching.
- [x] Preserve strict parsing, grounding validation, skipped-row isolation, and partial-line-safe resume.
- [x] Preserve the legacy argument-based CLI as a documented compatibility mode.

## Phase 1 — Contracts and Run Configuration

- [x] Add YAML and Parquet dependencies.
- [x] Add strict Pydantic models for input, chunking, endpoint, generation, validation, and output.
- [x] Validate numeric bounds, overlap, recipe languages, format-specific settings, and unknown fields.
- [x] Resolve paths relative to the run-config file.
- [x] Resolve endpoint profiles and retain inline overrides.
- [x] Reference API secrets by environment-variable name.
- [x] Serialize secret-free resolved configuration.
- [x] Compute deterministic configuration hashes.
- [x] Implement deterministic prompt-pack and explicit-recipe merging.
- [x] Add `run`, `validate-config`, and compatibility CLI paths.
- [x] Add a validated customer-service example configuration.
- [x] Pass the Phase 1 configuration and compatibility tests.

## Phase 2 — Corpus Readers and Chunkers

- [x] Replace single-record assumptions with iterable corpus reading.
- [x] Support single files and recursively discovered directories.
- [x] Process deterministic sorted source order.
- [x] Implement text, Markdown, JSON, JSONL, CSV, and Parquet readers.
- [x] Support configurable ID, title, text, domain, and metadata fields.
- [x] Generate stable source IDs and SHA-256 checksums.
- [x] Isolate malformed, empty, and duplicate source records.
- [x] Add a common chunker registry and chunk provenance schema.
- [x] Implement `document`.
- [x] Implement `recursive_text` with overlap.
- [x] Implement `markdown_sections`.
- [x] Implement deterministic `fixed_tokens`.
- [x] Add stable strategy-versioned chunk IDs and offsets.
- [x] Add `inspect` and `list-chunkers`.
- [x] Verify inspection performs no inference.
- [x] Pass reader, malformed-record, collision, boundary, overlap, and determinism tests.

## Phase 3 — Formats, Languages, and Prompt Packs

- [x] Replace the fixed family loop with a typed format registry.
- [x] Register and implement all ten formats:
  - [x] `factual_qa`;
  - [x] `transactional`;
  - [x] `troubleshooting`;
  - [x] `scenario_response`;
  - [x] `multi_turn`;
  - [x] `feedback_response`;
  - [x] `conflict_resolution`;
  - [x] `needs_recommendation`;
  - [x] `cross_sell`;
  - [x] `intent_response`.
- [x] Retain compatibility aliases for the five legacy names.
- [x] Add English, Tagalog, and Taglish language definitions.
- [x] Keep language composition independent from QA format.
- [x] Add typed format constraints, settings, versions, and structural rules.
- [x] Add built-in and YAML prompt packs.
- [x] Add `customer_service_core_v1` and `all_formats_v1`.
- [x] Add format, language, and prompt-pack listing commands.
- [x] Replace prompt conditionals with a composable prompt builder.
- [x] Compose grounding policy, format, language, schema, and source independently.
- [x] Generate deterministic prompt hashes.
- [x] Keep prompting independent from readers, chunkers, and inference backends.
- [x] Pass all registry and prompt-composition tests.

## Phase 4 — Jobs and Canonical Chat Records

- [x] Add deterministic `GenerationJob` expansion.
- [x] Expand chunks across recipes, languages, and sample indices.
- [x] Include recipe settings and prompt versions in job identity.
- [x] Add stable job and recipe hashes.
- [x] Report exact job counts by format and language during inspection.
- [x] Add canonical `ChatMessage` and generated-outcome models.
- [x] Support structured `generated` and `not_applicable` responses.
- [x] Add the canonical `sft_chat_v1` accepted-record schema.
- [x] Keep messages as the sole canonical conversation representation.
- [x] Preserve top-level analytical dimensions and flexible metadata.
- [x] Pass inference parameters through the common OpenAI-compatible client.
- [x] Preserve deterministic static-client testing.
- [x] Pass two-turn, multi-turn, malformed-JSON, metadata, job-count, and job-ID tests.

## Phase 5 — Validation, Deduplication, and Resume

- [x] Separate source, generation, parsing, validation, duplicate, and not-applicable failure stages.
- [x] Validate non-empty messages and supported roles.
- [x] Validate user-first alternating conversations.
- [x] Validate the configured final assistant turn.
- [x] Validate requested QA format and language labels.
- [x] Enforce multi-turn message limits and intent metadata.
- [x] Validate one or more exact source evidence quotations.
- [x] Add normalized exact-conversation hashing and deduplication.
- [x] Deduplicate against both current and resumed accepted output.
- [x] Checkpoint accepted, rejected, and not-applicable outcomes.
- [x] Ignore malformed partial checkpoint lines.
- [x] Resume only missing jobs.
- [x] Prevent duplicate accepted rows after restart.
- [x] Add targeted retry by failure stage.
- [x] Make successful retry supersede the previous terminal outcome in manifest counts.
- [x] Pass validation, deduplication, retry, and resume tests.

## Phase 6 — Standard Dataset Directory

- [x] Replace the new-path single output file with a dataset-directory writer.
- [x] Create all standard artifacts:
  - [x] `manifest.json`;
  - [x] `config.resolved.yaml`;
  - [x] `sources.jsonl`;
  - [x] `chunks.jsonl`;
  - [x] `dataset.jsonl`;
  - [x] `rejected.jsonl`;
  - [x] `checkpoint.jsonl`.
- [x] Use append-safe, flushed JSONL writes.
- [x] Use atomic manifest and deterministic manifest-file updates.
- [x] Record schema/config hashes, endpoint/model labels, prompt versions, run timing, and resume state.
- [x] Report requested, accepted, rejected, and not-applicable counts.
- [x] Group accepted rows by format, language, and source.
- [x] Group historical failures by stage.
- [x] Exclude secret values from every artifact.
- [x] Reject output-directory configuration conflicts.
- [x] Pass standard-directory, manifest, provenance, redaction, and conflict tests.

## Phase 7 — End-to-End Validation and Documentation

- [x] Pass the complete deterministic suite with 43 tests.
- [x] Validate and inspect the customer-service example without inference.
- [x] Run the live customer-service pack against local llama-swap.
- [x] Accept 14/14 requested core format-language jobs.
- [x] Verify English, Tagalog, and Taglish factual QA.
- [x] Verify Tagalog and Taglish troubleshooting.
- [x] Verify structured Taglish multi-turn output.
- [x] Confirm all accepted evidence quotations occur in the source.
- [x] Confirm a second normal invocation generates zero duplicate jobs.
- [x] Update README, product specification, QA-format contract, and test contract.
- [x] Document configuration, readers, chunkers, endpoints, formats, languages, output, resume, retry, and exports.

## Phase 8 — Full Format Catalog and Exports

- [x] Add structural fixtures for all ten formats.
- [x] Add prompt and safety constraints for transactional, feedback, conflict, recommendation, cross-sell, and intent formats.
- [x] Require `metadata.intent` for intent responses.
- [x] Support `not_applicable` for unsupported recommendation generation.
- [x] Run all ten formats against local llama-swap.
- [x] Isolate one malformed multi-turn response and one invalid evidence quotation.
- [x] Retry only the failed parsing and validation jobs.
- [x] Finish with 10/10 latest all-format outcomes accepted.
- [x] Confirm another normal invocation generates zero rows.
- [x] Add CSV export.
- [x] Add Parquet export with flexible maps encoded as JSON strings.
- [x] Add legacy JSONL export derived from canonical messages.
- [x] Validate 10-row live exports in all three derived formats.

## Final Acceptance

- [x] One YAML file controls corpus, chunking, endpoint, prompts, languages, validation, and output.
- [x] File and directory corpora process multiple source records.
- [x] Chunking strategy is selected through configuration.
- [x] Endpoint selection requires no pipeline or prompt changes.
- [x] Prompt packs and explicit recipes resolve deterministically.
- [x] QA format and language vary independently.
- [x] Requested examples expand into deterministic, resumable jobs.
- [x] Accepted rows conform to `sft_chat_v1`.
- [x] Every run produces the standard dataset directory.
- [x] Rejected rows retain actionable failure reasons.
- [x] Resume executes only missing jobs.
- [x] Different config hashes cannot silently share an output directory.
- [x] Provenance supports comparison across formats, languages, prompts, models, endpoints, and sources.
- [x] All ten QA formats are enabled, tested, and live-smoked.
- [x] Documentation matches shipped behavior.
- [x] `uv run python -m compileall -q src tests` passes.
- [x] `uv run pytest -q` passes with 43 tests.

## Reviewed Future Extensions

These are deliberately not active completion tasks. The frozen interfaces support them when evidence justifies the added complexity.

- Semantic chunking: defer until deterministic chunkers establish a measurable quality baseline.
- Bounded concurrent inference: defer because the current local workflow is intentionally sequential and model residency is the practical bottleneck.
- Multi-example response batching: defer to preserve per-example isolation and deterministic retry.
- Fuzzy or embedding-based deduplication: recommended next quality experiment because the live smoke exposed near-paraphrase repetition.
- Semantic language classification: defer; current validation records the requested language but does not claim linguistic scoring.
- LLM-as-judge grounding: defer until a fixed human-reviewed evaluation set exists.
- Distributed orchestration: defer until local sequential execution is a measured bottleneck.

