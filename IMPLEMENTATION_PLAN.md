# llm-data-gen System Upgrade Plan

**Status:** Implemented  
**Frozen:** 2026-09-10  
**Completed:** 2026-09-11  
**Canonical requirements:** `spec.md` and `spec/qa-formats.md`

## 1. Goal

Upgrade the current single-document, fixed-prompt V1 into a config-driven synthetic dataset compiler.

A user should be able to:

1. point to a corpus;
2. select and configure a chunking strategy;
3. select an OpenAI-compatible inference endpoint;
4. select a prompt pack or explicit QA-format recipes;
5. select languages independently from QA formats;
6. point to an output directory; and
7. receive a standardized, reproducible, resumable dataset artifact.

The target flow is:

```text
corpus
  -> corpus reader
  -> selected chunker
  -> generation-job expansion
  -> prompt format + language composition
  -> OpenAI-compatible endpoint
  -> parse and validate
  -> standardized dataset directory
```

## 2. Product Contract

The primary interface will be one versioned run configuration:

```bash
uv run llm-data-gen run configs/example-customer-service.yaml
```

The run configuration is the reproducibility contract. It must fully resolve:

- corpus location and field mappings;
- chunking strategy and settings;
- endpoint name, URL, model, and inference parameters;
- prompt pack and recipe overrides;
- target languages;
- validation policy;
- output directory and resume behavior.

The CLI may expose overrides for experimentation, but the resolved configuration written into the output directory remains the source of truth for that run.

## 3. Frozen Design Decisions

### 3.1 Configuration First

- YAML is the user-facing run configuration format.
- Pydantic models validate and normalize configuration before work begins.
- Invalid formats, languages, chunkers, endpoint profiles, and prompt packs fail before inference.
- Secrets are referenced through environment-variable names and never copied into output artifacts.

### 3.2 Corpus Means Multiple Source Records

- The internal reader contract returns an iterator of `SourceDocument` records.
- A corpus path may point to one file or a directory.
- The first migration supports `.txt`, `.md`, `.json`, and `.jsonl` sources.
- JSON and JSONL support configurable `id_field`, `text_field`, and optional metadata fields.
- CSV and Parquet readers are a later adapter milestone because they require tabular dependencies and field-mapping decisions.
- One malformed source record is reported without terminating the whole corpus run.

### 3.3 Chunking Is a Selectable Strategy

All chunkers implement one contract:

```text
SourceDocument + ChunkingConfig -> list[Chunk]
```

Initial strategies:

- `document`: one chunk per source record;
- `recursive_text`: paragraph-aware splitting with sentence/character fallback;
- `markdown_sections`: split on Markdown headings, then apply size limits.

The current paragraph-aware character chunker becomes `recursive_text` so existing behavior has a migration path.

Every chunk retains:

- `source_id`;
- stable `chunk_id`;
- chunk strategy and version;
- chunk index;
- source path;
- source checksum;
- start/end offsets when available;
- source title and metadata;
- token estimate;
- chunk text.

Token-native and semantic chunkers are deferred until the initial strategy interface is stable.

### 3.4 Endpoints Remain OpenAI-Compatible

- QA formats never know whether the endpoint is llama-swap, vLLM, or llama.cpp.
- Endpoint profiles resolve to the existing common inference client.
- A profile contains a display name, backend label, base URL, model, timeout, API-key environment variable, and default generation parameters.
- The first profiles cover local llama-swap and generic OpenAI-compatible endpoints.
- The resolved endpoint configuration is recorded with secrets redacted.

### 3.5 Formats and Languages Are Independent

The generation unit is:

```text
chunk + QA format + language + settings + sample index
```

Initial languages:

- `english`;
- `tagalog`;
- `taglish`.

Language instructions live in a language registry and are composed with format instructions. No format-language combination gets its own duplicated prompt template.

### 3.6 Prompt Formats and Prompt Packs Are Different Concepts

A **format** defines one interaction type. A **prompt pack** selects a useful group of formats and defaults.

The format registry contains all ten planned names:

- `factual_qa`;
- `transactional`;
- `troubleshooting`;
- `scenario_response`;
- `multi_turn`;
- `feedback_response`;
- `conflict_resolution`;
- `needs_recommendation`;
- `cross_sell`;
- `intent_response`.

Each format definition contains:

- stable machine name;
- display name;
- purpose;
- format instruction;
- constraints;
- prompt name and version;
- supported settings;
- structural validation rules;
- implementation status.

Format definitions remain typed Python objects during this milestone. Prompt packs are YAML configuration files that refer to registered formats. This avoids introducing ten loosely validated prompt files while prompt contracts are still changing.

The first fully enabled and tested formats are:

- `factual_qa`;
- `troubleshooting`;
- `multi_turn`.

The remaining seven formats are registered but rejected at config validation until their prompts and tests are complete.

### 3.7 One Deterministic Job Per Example Slot

`num_examples` expands into independent sample slots instead of requiring one model response to contain a batch.

```text
chunk_id::qa_format::language::prompt_version::sample_index::recipe_hash
```

This preserves:

- row-level failure isolation;
- deterministic resume behavior;
- targeted retry;
- simple parsing;
- clear accounting of requested, accepted, and rejected examples.

Multi-example inference batching may be added later behind the same job interface as a throughput optimization.

### 3.8 JSONL Is the Canonical V1 Physical Format

The logical schema is versioned independently from its physical serialization.

- Canonical physical output: JSONL.
- Canonical logical schema: `sft_chat_v1`.
- Parquet becomes an optional derived export after the chat schema is stable.
- Generated training content uses `messages` as the only canonical conversation representation.
- Legacy `input_text` and `output_text` are not duplicated into canonical rows; a compatibility exporter may derive them.

## 4. Run Configuration Contract

The initial configuration shape is:

```yaml
version: 1
name: example-customer-service-v1

input:
  path: data/raw/
  format: auto
  recursive: true
  id_field: source_id
  text_field: text
  domain: customer_service

chunking:
  strategy: recursive_text
  chunk_size: 1200
  chunk_overlap: 150
  unit: characters

endpoint:
  profile: local-qwen
  base_url: http://127.0.0.1:8080/v1
  model: qwen38-27b-chat-rocmfp4
  api_key_env: OPENAI_API_KEY
  timeout_seconds: 120

generation:
  prompt_pack: customer_service_core_v1
  temperature: 0.2
  recipes:
    - format: factual_qa
      languages: [english, tagalog, taglish]
      num_examples: 3

    - format: troubleshooting
      languages: [tagalog, taglish]
      num_examples: 2

    - format: multi_turn
      languages: [taglish]
      num_examples: 1
      max_turns: 6

validation:
  grounding: exact_evidence
  reject_duplicates: true
  require_assistant_final_turn: true

output:
  directory: output/example-customer-service-v1
  format: jsonl
  resume: true
  include_chunk_text: false
```

Explicit `generation.recipes` extend or override the selected prompt pack according to documented merge rules. The resolved recipe list must be visible through config inspection before inference.

## 5. Internal Architecture

The package will evolve toward these responsibilities:

```text
config.py             typed run configuration and config loading
readers.py            corpus-reader registry and source iteration
chunking.py           chunker protocol, registry, and strategies
formats.py            typed QA-format definitions and registry
languages.py          language/style definitions and registry
prompt_packs.py       prompt-pack loading and recipe resolution
prompting.py          composable prompt builder
jobs.py               deterministic generation-job expansion
client.py             OpenAI-compatible inference only
parsing.py            strict model-response parsing
validation.py         layered validators and duplicate detection
output.py             standardized dataset-directory writer
pipeline.py           orchestration only
cli.py                run, inspect, validate, and list commands
```

The main pipeline must depend on registry interfaces, not hard-coded lists of formats or chunking strategies.

## 6. Canonical Output Schema

Each accepted row follows `sft_chat_v1`:

```json
{
  "schema_version": "sft_chat_v1",
  "example_id": "sha256:...",
  "job_id": "sha256:...",
  "source_id": "doc-001",
  "chunk_id": "doc-001::recursive_text::0003",
  "domain": "customer_service",
  "qa_format": "troubleshooting",
  "language": "taglish",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "evidence": ["Exact supporting quotation from the source."],
  "prompt_name": "troubleshooting",
  "prompt_version": "v1",
  "generator_model": "qwen38-27b-chat-rocmfp4",
  "inference_backend": "llama-swap",
  "generation_parameters": {"temperature": 0.2},
  "generated_at": "2026-09-10T00:00:00Z",
  "validation_status": "passed",
  "metadata": {}
}
```

Rules:

- Major comparison dimensions remain top-level.
- `messages` supports two-turn and genuine multi-turn records.
- `evidence` is a list of exact source quotations for the initial grounding validator.
- `metadata` holds format-specific labels such as intent; it does not replace stable top-level fields.
- Source chunk text is omitted from training rows by default but remains recoverable through the chunk/source manifest.
- Example and job IDs are deterministic hashes of normalized identifying inputs.

## 7. Standard Output Directory

Every run writes one self-describing dataset directory:

```text
output/example-customer-service-v1/
  manifest.json
  config.resolved.yaml
  sources.jsonl
  chunks.jsonl
  dataset.jsonl
  rejected.jsonl
  checkpoint.jsonl
```

Responsibilities:

- `manifest.json`: schema version, config hash, counts, endpoint/model labels, prompt versions, start/end times, and run status;
- `config.resolved.yaml`: exact effective configuration with secrets removed;
- `sources.jsonl`: normalized source provenance and checksums;
- `chunks.jsonl`: stable chunk records or references needed for audit;
- `dataset.jsonl`: accepted `sft_chat_v1` rows;
- `rejected.jsonl`: source, generation, parsing, validation, and duplicate failures;
- `checkpoint.jsonl`: terminal job IDs and outcomes used by resume.

If the output directory already contains a different config hash, the command fails unless the user explicitly chooses a new directory. Resume must never silently combine incompatible run configurations.

## 8. Validation Architecture

Validation is layered so stronger checks can be added without rewriting the pipeline:

1. **Parsing:** strict JSON response and Pydantic schema validation.
2. **Conversation:** non-empty messages, valid roles, alternating turns, and assistant final turn.
3. **Recipe contract:** generated format and language match the requested job.
4. **Format structure:** format-specific rules such as multi-turn limits.
5. **Grounding:** required evidence quotations occur in the source chunk.
6. **Deduplication:** normalized message-content hash is unique within the run.

The initial language check verifies the requested language metadata and prompt contract. It does not claim semantic language detection. Fuzzy duplicate detection and LLM-as-judge factual validation remain later extensions.

Formats such as `cross_sell` must be able to return a structured `not_applicable` outcome when the chunk does not support a safe example. This is recorded as an expected skipped outcome, not coerced into invented content.

## 9. CLI Contract

Required commands:

```bash
uv run llm-data-gen run CONFIG_PATH
uv run llm-data-gen validate-config CONFIG_PATH
uv run llm-data-gen inspect CONFIG_PATH
uv run llm-data-gen list-formats
uv run llm-data-gen list-languages
uv run llm-data-gen list-chunkers
uv run llm-data-gen list-prompt-packs
```

`inspect` performs no inference. It reports:

- source-record count;
- chunk count and size distribution;
- resolved recipes;
- total generation jobs;
- expected examples by format and language;
- endpoint/model selection;
- output directory and resume state.

## 10. Implementation Phases

### Phase 1: Freeze Contracts and Protect Current Behavior

- Add config, output-schema, and registry tests before refactoring.
- Preserve the current CLI path as a temporary compatibility mode.
- Add YAML support and Pydantic run-config models.
- Add `validate-config` and resolved-config serialization with secret redaction.

**Exit:** Existing tests pass and the example YAML validates without inference.

### Phase 2: Corpus Reader and Chunker Registries

- Replace single-record loading with source iteration.
- Support file and directory corpus paths.
- Implement `.txt`, `.md`, `.json`, and `.jsonl` readers.
- Add `document`, `recursive_text`, and `markdown_sections` chunkers.
- Add source checksums, stable chunk IDs, overlap handling, and chunk manifests.

**Exit:** `inspect` deterministically reports sources and chunks for fixture corpora under each initial strategy.

### Phase 3: Format, Language, and Prompt-Pack Composition

- Add typed format and language registries.
- Register all ten format names with explicit implementation status.
- Fully implement `factual_qa`, `troubleshooting`, and `multi_turn`.
- Add YAML prompt packs and deterministic recipe merge rules.
- Build prompts from global policy, format instruction, language instruction, output schema, and source chunk.
- Give each format its own prompt name/version.

**Exit:** Prompt snapshots prove that format and language vary independently and disabled formats fail during config validation.

### Phase 4: Job Expansion and Canonical Chat Records

- Add deterministic generation-job expansion.
- Expand `num_examples` into sample-indexed jobs.
- Replace the model-facing legacy schema with chat messages and evidence lists.
- Parse two-turn and multi-turn records into `sft_chat_v1`.
- Keep the inference client format-agnostic.

**Exit:** Static-client tests cover every format/language/example slot in the example configuration.

### Phase 5: Layered Validation and Resume

- Implement conversation, recipe, format, grounding, and duplicate validators.
- Record stable failure stages and reasons.
- Add deterministic job/config hashes.
- Resume from terminal job outcomes without duplicating accepted rows.
- Reject attempts to resume into an output directory with a conflicting config hash.

**Exit:** Interrupted-run tests resume exactly the missing jobs, malformed rows remain isolated, and duplicate records are rejected.

### Phase 6: Standard Dataset Directory

- Replace the single output-file option with an output-directory writer.
- Write manifest, resolved config, source manifest, chunk manifest, accepted records, rejected records, and checkpoints.
- Use atomic manifest updates and append-safe JSONL writes.
- Add a final run summary grouped by format, language, source, and failure stage.

**Exit:** A completed fixture run is self-describing and can be audited without access to process logs.

### Phase 7: End-to-End Live Smoke and Documentation

- Add the requested small customer-service example configuration.
- Run it against the local llama-swap endpoint.
- Verify English, Tagalog, and Taglish factual QA; Tagalog and Taglish troubleshooting; and Taglish multi-turn generation.
- Review accepted and rejected outputs manually for structural quality.
- Update `README.md`, `TODO.md`, and command examples to the new workflow.
- Remove compatibility mode only after the new path is proven.

**Exit:** One command transforms a small corpus into a valid, resumable `sft_chat_v1` dataset directory.

### Phase 8: Complete the Format Catalog

- Implement the remaining seven registered formats one at a time.
- Add format-specific fixtures, prompt snapshots, validation rules, and live samples.
- Enable each format only when its tests and manual quality check pass.

**Exit:** All ten formats can be safely selected individually or through prompt packs.

## 11. Required Tests

### Configuration

- valid and invalid YAML;
- unknown registry names;
- recipe expansion and override behavior;
- environment-secret references and redaction;
- conflicting output-directory config hashes.

### Corpus and Chunking

- multi-record JSONL iteration;
- directory traversal order;
- malformed-record isolation;
- stable source/checksum/chunk IDs;
- each initial chunking strategy;
- overlap and boundary behavior.

### Prompting and Jobs

- format/language independence;
- prompt-pack expansion;
- deterministic job counts and IDs;
- prompt names and versions;
- one slot per requested example.

### Parsing and Validation

- two-turn and multi-turn records;
- invalid JSON;
- invalid or non-alternating roles;
- missing assistant response;
- format/language mismatch;
- missing or invalid evidence;
- exact duplicate detection;
- structured `not_applicable` outcomes.

### Output and Resume

- canonical row schema;
- complete output-directory layout;
- provenance and generation metadata;
- rejected-row diagnostics;
- interrupted-run recovery;
- no duplicate regeneration after resume;
- redacted resolved configuration.

## 12. Acceptance Criteria

The upgrade is complete when:

- one YAML configuration controls corpus, chunking, endpoint, prompt selection, languages, validation, and output;
- one corpus may contain multiple source records and files;
- chunking is selected through a registry and produces stable provenance;
- endpoint selection does not alter prompt or pipeline code;
- prompt packs and explicit recipes resolve deterministically;
- format and language are independent dimensions;
- `factual_qa`, `troubleshooting`, and `multi_turn` are fully tested and live-smoked;
- accepted examples conform to `sft_chat_v1`;
- every run produces the standard dataset directory;
- malformed rows do not terminate the run;
- resume executes only missing jobs;
- output artifacts preserve enough lineage to compare formats, languages, prompts, models, and endpoints;
- the complete test suite passes.

## 13. Deliberate Deferrals

The following are outside this upgrade unless required by evidence from implementation:

- distributed orchestration;
- databases and queues;
- asynchronous or multi-GPU scheduling;
- semantic chunking;
- CSV and Parquet corpus readers;
- Parquet as the canonical writer;
- batched multi-example model responses;
- fuzzy or embedding-based deduplication;
- automatic semantic language classification;
- LLM-as-judge validation;
- model training or fine-tuning;
- graphical UI.

These can be added behind the frozen reader, chunker, endpoint, format, validator, and writer boundaries without changing the user-facing pipeline model.
