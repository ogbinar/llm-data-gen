# llm-data-gen Historical V1/V2 Implementation Record

> **Archive notice:** This file preserves the completed V1 plan and the detailed
> V2 migration/acceptance record as they stood at the 2026-09-12 freeze. It is not
> the current plan or backlog. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
> for the canonical frozen baseline and accepted optional queue, and
> [TODO.md](TODO.md) for current work status.

**Status:** V2 implemented, acceptance-validated, and frozen

**V2 freeze date:** 2026-09-12

**Canonical product contracts:** `spec.md` and `spec/qa-formats.md`

**Archived V2 status sources:** the frozen-baseline snapshot below and the
implemented V2 record in
[Section 14](#14-source-language-preservation-revision-implemented-record)

For current status, the canonical source is now
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Section 0 and Section 14 below
are retained snapshots of the original V2 freeze record.

> **Binding product revision — 2026-09-11:** The pipeline is a source reformatter,
> not a translation system. Language is an input/source property and is never a
> generation choice. English remains English, Filipino/Tagalog remains
> Filipino/Tagalog, and Taglish remains Taglish while QA/interaction format may
> vary. There is no translation mode in this product.
>
> The completed V1 history below is retained in place as implementation evidence.
> Every
> statement in historical Sections 1–13 that treats language as a selectable
> target, an independent recipe dimension, or a job-expansion axis is superseded
> by the frozen V2 contract and implementation record. Those statements describe
> history, not supported current behavior.

## 0. Frozen V2 Baseline (Archived Snapshot)

### Freeze scope

The frozen baseline is the config-driven, source-language-preserving V2 pipeline.
It is complete for local sequential corpus-to-dataset generation, deterministic
validation, row-level failure isolation, targeted retry, resume, and export.

The non-negotiable product rule is:

```text
format may change; source language must not
```

- `input.language` is required source provenance.
- Structured JSON, JSONL, CSV, and Parquet inputs may opt into per-record
  provenance through `input.language_field`.
- Active recipes and prompt packs select formats and sample counts only.
- Jobs expand as chunk × format × sample index.
- Prompts require preservation of source language, register, terminology, and
  existing code-switching; they never request translation.
- The pipeline, not the model, writes the accepted `sft_chat_v1.language` label.
- Legacy V1 configs or packs with target-language arrays fail before inference.

### Implemented architecture

```text
RunConfig V2
  -> reader registry -> SourceDocument(language + origin)
  -> chunker registry -> Chunk(language + origin)
  -> format recipes  -> deterministic GenerationJob(language + origin)
  -> prompt builder  -> OpenAI-compatible inference
  -> strict parser   -> provenance/format/evidence/duplicate validation
  -> DatasetWriter   -> manifests + accepted/rejected/checkpoint JSONL
```

The stable boundaries are configuration, readers, chunkers, endpoint client,
format definitions, prompt packs, jobs, parser, validators, and writer/exporters.
Changing an endpoint or model does not require pipeline or prompt code changes.

### Frozen acceptance evidence

- The deterministic suite passed 54 tests; `compileall` passed for `src` and
  `tests`.
- Both shipped public configs validate and inspect without inference.
- Final local r4 runs produced 10/10 accepted Globe English rows, 5/5 accepted
  Proclamation Tagalog rows, and 5/5 accepted QC SSDD Taglish rows.
- All 20 rows passed mechanical schema, exact-evidence, role/turn, identity,
  format-limit, and language-provenance audits.
- Manual review found no obvious unsupported factual claim or preservation
  violation. A normal resumed run generated zero new rows for each dataset.
- Historical V1 and intermediate V2 outputs remain preserved and isolated from
  the final V2 output directories.

Detailed requirements, migration decisions, retries, material implementation
choices, and qualitative findings are retained in Section 14. Sections 1–13 are
the retained historical V1 plan behind this explicit archival boundary.

### Deferred, non-binding work

Semantic language detection, semantic factuality judges, fuzzy/embedding
deduplication, semantic chunking, batching, concurrency, distributed
orchestration, training, and a GUI are not part of the freeze. They require a new
approved scope and must preserve the V2 no-translation contract.

## Historical V1 Plan (Sections 1–13; Archived In Place)

The following sections preserve the original V1 design and completion rationale.
They are not an active backlog. Any language-as-target or language-multiplied-job
statement is superseded by Sections 0 and 14; the remaining material records the
foundation on which V2 was built.

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

## 14. Source-Language-Preservation Revision — Implemented Record

**Revision status:** Implemented and acceptance-validated on 2026-09-12.
**Supersedes:** Historical Sections 1–13 only where they specify target-language
selection, format/language independence, language-multiplied jobs, or a
multilingual-generation smoke test.

### 14.1 Binding product contract

The product transforms source material into different grounded QA and interaction
formats. It must not intentionally translate the source, request a different
output language, or introduce code-switching that is not already characteristic
of the source.

- Language is declared on the input and inherited by every source, chunk, job,
  and accepted row.
- QA format remains selectable and may vary independently of the source language.
- English source produces English interactions; Filipino or Tagalog source
  produces Filipino or Tagalog interactions; Taglish source produces Taglish
  interactions.
- The model is not authoritative for the language label. The pipeline supplies
  the label from source provenance when it constructs `sft_chat_v1`.
- No translation, target-language, localization, or language-conversion mode will
  be added under this revision.

The revised flow is:

```text
corpus + declared source language
  -> normalized SourceDocument(language)
  -> Chunk(language inherited)
  -> chunk × QA format × sample-index jobs
  -> preserve-language prompt
  -> parse and validate grounded interaction
  -> sft_chat_v1(language copied from source provenance)
```

### 14.2 Versioned input and configuration contract

This was a breaking configuration change and uses run-config `version: 2`.
Version 1 remains readable only as historical output provenance; the revised run
path will not execute V1 target-language configurations.

Implemented V2 shape:

```yaml
version: 2
name: source-preserving-example-v2

input:
  path: ../data/raw/source.txt
  format: txt
  language: english

generation:
  prompt_pack: all_formats_v2
  recipes:
    - format: factual_qa
      num_examples: 3
    - format: multi_turn
      num_examples: 1
      max_turns: 6
```

Input language rules:

1. `input.language` is the required default source-language label. Initial
   registered labels are `english`, `filipino`, `tagalog`, and `taglish`;
   `filipino` and `tagalog` remain distinct declared provenance values rather
   than being rewritten by the model.
2. For text and Markdown, `input.language` applies to each discovered document.
   `language_field` has no record to inspect and must either be omitted or fail
   configuration validation with an actionable message.
3. For JSON, JSONL, CSV, and Parquet, optional `input.language_field` names a
   per-record field. There is no implicit field name: when it is configured, a
   non-empty record value overrides the configured default, while a missing or
   blank field uses `input.language`. This explicit choice prevents an unrelated
   field named `language` from silently changing source provenance.
4. An unknown/non-registered record language is a source-stage rejection with
   the path and record index; it must never silently fall back to the default.
5. Mixed-language structured corpora are supported because each source record
   carries its own language, and every derived chunk inherits it. A text or
   Markdown directory using one config must be language-homogeneous under its
   declared default; heterogeneous plain-file directories require separate runs
   (or conversion to structured records with `language_field`). The pipeline
   applies the declared default to every such file and cannot detect a false
   declaration without semantic language detection; responsibility for this
   declaration remains with the input curator.
6. `SourceDocument` and `Chunk` gain a required `language` provenance field.
   Source/chunk manifests retain the declared label and whether it came from the
   config default or a structured record override.

### 14.3 Recipes, prompt packs, and deterministic jobs

- Remove `languages` from `GenerationRecipe`, YAML prompt packs, built-in packs,
  explicit recipe overrides, inspection output, and examples.
- Prompt packs select formats and format-specific settings only. Introduce V2
  pack versions/names where needed so old serialized packs cannot be mistaken for
  the new contract.
- Expand jobs as `chunk × resolved format recipe × sample_index`. Source language
  is inherited once from the chunk and never multiplies the job count.
- Keep inherited language in `GenerationJob` for prompting and provenance, but
  populate it from `Chunk`, not from the recipe.
- Job identity remains deterministic over chunk ID, QA format, prompt version,
  sample index, and recipe hash. The inherited source-language label must also be
  covered by chunk/job identity integrity so changing a language declaration
  invalidates resume, without becoming a Cartesian expansion axis.
- Inspection reports jobs by format and reports source/chunk counts by declared
  language. It must not describe these as requested or target languages.

Legacy failure behavior is deliberate. In V2, any `generation.recipes[*].languages`
or prompt-pack `languages` key must fail before inference with an error such as:
`languages was removed in config version 2; set input.language (and optional
input.language_field) instead`. V1 run configs that contain target-language arrays
must likewise fail loudly with migration guidance; they must never be accepted by
dropping the field or interpreted as permission to translate. The legacy
argument-based CLI must require an explicit source-language option before it can
enter the revised pipeline, or remain isolated as a clearly deprecated V1 path.

### 14.4 Prompting, parsing, output, and validation

Prompt construction will combine grounding policy, format instruction, output
schema, and source chunk. It will no longer compose a target-language instruction.
Every format prompt must include equivalent hard constraints:

- preserve the source's language, register, and existing style;
- do not translate;
- do not add code-switching or remove natural code-switching;
- retain technical terms and proper nouns as supported by the source;
- return `not_applicable` rather than changing language to make a format work.

The model-facing generated-response schema should not ask the model to choose or
certify `language`. The orchestrator writes `SFTRecord.language` from the job's
inherited chunk provenance. `sft_chat_v1` remains the canonical logical schema;
under V2 its top-level `language` field explicitly means declared source language,
not requested model output language. Manifests and resolved configs distinguish
V2 runs so historical V1 rows are not reinterpreted or mixed with them.

Validation will:

1. assert `SourceDocument.language == Chunk.language == GenerationJob.language`;
2. assert the final `sft_chat_v1.language` was copied from that provenance;
3. remove `language` from the strict model-facing schema and reject a
   model-returned `language` key as unexpected rather than trusting it;
4. retain existing conversation, format, grounding, duplicate, and assistant-final
   checks; and
5. test prompt constraints and curated examples for preservation behavior.

This initial revision does **not** claim semantic language detection. It can prove
that labels are propagated correctly and that the model was instructed not to
translate, but it cannot mechanically prove that every generated sentence is in
the declared language. Manual review remains required for live quality checks;
semantic language identification may be evaluated later as a validator, never as
a translation feature.

### 14.5 Migration and output isolation

- Bump run configs to V2, prompt versions for every changed prompt, and recipe/job
  hashes affected by the contract.
- Update built-in packs, YAML packs, shipped config examples, CLI inspection/list
  behavior, tests, README, specifications, and QA-format documentation in their
  implementation phases—not during this planning-only change.
- Never resume a V2 source-preserving run into a V1 output directory. Existing
  config-hash checks remain mandatory, and migrated examples must choose new,
  unused output directories (prefer names ending in `-source-preserving-v2`).
- Preserve historical V1 output directories and manifests as read-only evidence;
  do not rewrite their language semantics.
- Retire the prior target-language/multilingual smoke as a product acceptance
  test. Historical records may be labeled `historical-v1-target-language-smoke`.
  The existing all-format smoke may remain only as historical format-catalog
  evidence, not proof of multilingual generation.
- Replace the retired smoke with a source-preservation regression matrix using
  the three current demo sources conceptually: the Globe broadband source as
  English, Proclamation No. 1041 as Filipino/Tagalog, and QC SSDD Online Services
  as Taglish. Do not alter those fixtures as part of this documentation task.

### 14.6 Completed implementation phases

#### Revision Phase 1 — Configuration and source provenance

- Add strict V2 config models for `input.language` and `language_field`.
- Add actionable rejection of legacy recipe/prompt-pack `languages` arrays.
- Add language and language-origin provenance to sources and chunks.
- Implement homogeneous text/Markdown and mixed structured-corpus rules.

**Exit:** Config/reader tests prove defaults, record overrides, mixed structured
corpora, invalid labels, and loud V1 migration errors without inference.

#### Revision Phase 2 — Recipes, packs, and jobs

- Remove the recipe language axis and migrate built-in/YAML packs to V2.
- Expand only chunk × format × sample index.
- Update deterministic recipe/job identities and inspection summaries.

**Exit:** Job-count tests prove that adding source languages does not multiply a
record's jobs and that changing declared source language invalidates identity.

#### Revision Phase 3 — Prompting, parsing, and validation

- Replace target-language composition with preservation constraints.
- Remove model authority over the output language label.
- Inject `sft_chat_v1.language` from chunk provenance and validate the full
  source-to-row chain.
- Bump affected prompt versions and snapshots.

**Exit:** Static-client tests cover English, Filipino/Tagalog, Taglish, attempted
language mismatches, exact evidence, multi-turn limits, and `not_applicable`.

#### Revision Phase 4 — Shipped migrations and documentation

- Migrate built-in packs and example configs to V2 and new output directories.
- Update CLI help/list/inspect behavior.
- Update README, `spec.md`, `spec/qa-formats.md`, and test-contract documentation.
- Reframe or retire historical multilingual smoke language.

**Exit:** Repository-wide search finds no active user-facing target-language or
recipe `languages` contract outside explicitly marked V1 history/migration tests.

#### Revision Phase 5 — Regression and live quality review

- Run deterministic unit/integration tests and compile checks.
- Validate and inspect the three demo-source configurations.
- Run source-preserving smoke generation only after the implementation is complete.
- Manually review every smoke row for language/register preservation, grounding,
  format structure, naturalness, and unsupported claims; verify resume produces
  zero duplicate jobs.

**Exit:** Each demo source stays in its declared language across multiple formats,
all provenance labels agree, tests pass, and no V1/V2 output is combined.

### 14.7 Revision acceptance criteria

The revision is complete only when:

- configs declare source language under `input`, including structured-record
  overrides through `language_field`;
- text/Markdown defaults and mixed structured-corpus behavior are deterministic
  and documented;
- no recipe or prompt pack accepts a target-language array;
- job expansion is chunk × format × sample index, with no language multiplier;
- every prompt explicitly preserves source language/register and forbids
  translation and introduced code-switching;
- the model does not determine the accepted row's language label;
- `sft_chat_v1.language` matches source/chunk provenance for every row;
- V1 target-language configs fail before inference with actionable migration text;
- migrated runs use new output directories and cannot resume against V1 outputs;
- built-in packs, examples, tests, CLI output, README, specs, and QA-format docs
  consistently express the source-preservation contract;
- the old multilingual smoke is retired or unambiguously historical; and
- English, Filipino/Tagalog, and Taglish regression fixtures demonstrate
  preservation across QA formats, with manual limitations reported honestly.

### 14.8 Explicitly out of scope

- translation or localization in any direction;
- choosing an output/target language separately from the source;
- normalizing Filipino/Tagalog into English or forcing Taglish code-switching;
- automatic semantic language detection or per-chunk language inference in the
  initial revision;
- silently repairing missing/invalid structured language labels;
- automatic rejection/detection of mixed-language plain-text directories under
  one declared default-language config;
- changing `sft_chat_v1` into a translation-pair schema;
- rewriting historical V1 outputs or deleting current demo files;
- model training, inference optimization, semantic factuality judges, or fuzzy
  deduplication as part of this contract migration.

### 14.9 Implementation outcome and material choices

The revision was completed on 2026-09-12 with the following concrete choices and
evidence:

- `language_field` is explicit and defaults to `null`, rather than implicitly
  reading a structured field named `language`. This resolves the planning tension
  between “optional” and “default field”: record overrides occur only when the
  curator opts in, while `input.language` remains the required fallback.
- Plain text and Markdown files all inherit the declared default. The pipeline
  documents but does not claim it can detect a falsely declared heterogeneous
  directory, because semantic language detection remains out of scope.
- The strict model-facing schema omits `language`; an unexpected model-returned
  language key fails parsing. The pipeline alone writes `sft_chat_v1.language`.
- Safe `not_applicable` remains available to every format when a grounded
  interaction cannot be produced. Recommendation formats continue to call it out
  explicitly, but no format is forced to hallucinate merely to fill a slot.
- Resolved recipes and prompt versions participate in the run hash. Source
  language participates in job identity without multiplying job count.
- Prompt version `v5` adds exact-substring evidence instructions, sample-slot
  focus hints, and explicit prohibitions on invented troubleshooting, escalation,
  eligibility, and unstated mappings between plans, prices, speeds, uses, or
  benefits.
- The deterministic suite passes with 54 tests, and `compileall` passes for source
  and tests.
- Final live outputs use fresh `-source-preserving-v2-r4` directories. Earlier V1
  and intermediate V2 directories remain preserved and are not resume-compatible
  with the final configs.
- Final live outcomes are Globe English 10/10, Proclamation Tagalog 5/5, and QC
  SSDD Taglish 5/5. Mechanical audit confirmed schema, exact evidence, roles,
  format limits, unique jobs/signatures, prompt version, and full language
  provenance for all 20 rows. Normal resume produced zero jobs for every run.
- Manual review confirmed language/register preservation and found no remaining
  obvious unsupported factual claims. Some Globe transactional/troubleshooting
  rows are structurally valid but weak examples of those semantic formats, two
  Filipino factual rows still overlap in subject, and one Taglish factual question
  is mildly awkward. These are reported quality limitations, not translation or
  provenance failures.

## Queue Extension History (2026-09-12)

This section preserves the superseded implementation chronology that was removed
from the concise canonical plan. It is evidence and rationale, not an active plan
or current operating contract.

The first optional Huey + SQLite slice extracted shared `prepare_run`,
`execute_job`, and `finalize_run` seams; added immutable run snapshots and
per-job outcomes; added `enqueue`, `worker`, `queue-status`, and `finalize`; and
kept inline execution as the default with Huey confined to optional extras.
Queue messages carried only run/config/job identity, while workers wrote job-owned
outcomes and a deterministic finalizer produced canonical artifacts in planned
order.

Initial evidence was 75 passing tests, source/test compilation, diff checks, a
Huey-free base install, a persisted 50-job SQLite burst, fake-client concurrency
coverage, dequeue-loss reconciliation, bounded uninterrupted retries, redelivery
safety, run isolation, and byte-idempotent finalization. One live single-worker
job completed in 10.5 seconds. That live observation was never a throughput
benchmark.

A post-implementation review then identified five material gaps in the initial
claims: retry budgets were not yet proven across interruption, outcome envelopes
needed full plan-bound validation, the one-consumer process model needed
enforcement, recovery needed to state that `enqueue CONFIG`—not consumer
restart—reconciles a message removed at dequeue, and finalization needed explicit
serialized manifest-last recovery.

The §6.12 remediation closed those gaps. Final acceptance rose to 77 focused and
131 full passing tests, with durable lifetime attempt accounting, full outcome
integrity checks, a database-scoped consumer lock, real CLI reconciliation after
dequeue loss, and staged manifest-last recovery. A three-thread real Huey
consumer reached exactly three concurrent calls only against an instrumented fake
endpoint; no live multi-worker throughput claim was made. Current behavior,
limits, and final evidence are canonical in
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md#6-queue-acceptance-and-remediation)
and [the reproducible smoke evidence](docs/remediation-smoke-evidence.md).
