# llm-data-gen Frozen V2 Plan and Status

**Status:** implemented, acceptance-validated, and frozen

**Freeze date:** 2026-09-12

**Canonical product contracts:** [spec.md](spec.md) and
[spec/qa-formats.md](spec/qa-formats.md)

**Historical record:**
[IMPLEMENTATION_PLAN_HISTORY_2026-09-12.md](IMPLEMENTATION_PLAN_HISTORY_2026-09-12.md)

## 0. Frozen V2 Baseline (Canonical)

The stable baseline is the config-driven V2 pipeline that converts source corpora
into grounded `sft_chat_v1` interactions. All required implementation and freeze
work is complete; [TODO.md](TODO.md) contains no active required tasks.

The non-negotiable product rule is:

```text
format may change; source language must not
```

The pipeline does not translate, choose a target language, or intentionally add
or remove code-switching. English remains English, Filipino/Tagalog remains
Filipino/Tagalog, and Taglish remains Taglish.

## 1. Frozen Product Contract

A V2 run configuration controls:

1. corpus location, format, and structured field mappings;
2. required default source language and optional structured-record language field;
3. deterministic chunking strategy and settings;
4. OpenAI-compatible endpoint and model;
5. prompt pack or explicit format recipes;
6. grounding, duplicate, and conversation validation;
7. output directory and resume policy.

Language is declared provenance, not a generation dimension:

```text
SourceDocument(language + origin)
  -> Chunk(language + origin)
  -> GenerationJob(language + origin)
  -> SFTRecord(language copied by pipeline)
```

- `input.language` is required and supports `english`, `filipino`, `tagalog`, and
  `taglish`.
- Text and Markdown records inherit the configured default.
- JSON, JSONL, CSV, and Parquet may opt into per-record overrides through
  `input.language_field`. Missing or blank record values use the default;
  unsupported non-empty labels become source-stage rejections.
- Mixed-language structured corpora are supported. Mixed-language plain-text
  directories require separate runs or conversion to structured records.
- Semantic language detection is not claimed. Curators remain responsible for
  correct declarations.

## 2. Implemented Architecture

```text
RunConfig V2
  -> reader registry
  -> chunker registry
  -> format recipe resolution
  -> deterministic job expansion
  -> preservation prompt builder
  -> OpenAI-compatible inference client
  -> strict parser
  -> layered validators
  -> dataset writer and exporters
```

The stable module boundaries are configuration, readers, chunkers, endpoint
client, format definitions, prompt packs, jobs, prompting, parsing, validation,
and output. Endpoint/model selection does not alter prompt or pipeline code.

### Inputs and chunking

Readers normalize text, Markdown, JSON, JSONL, CSV, and Parquet into typed source
records with stable IDs, checksums, language/origin, provenance, domain, text, and
metadata. Malformed records are isolated.

The `document`, `recursive_text`, `markdown_sections`, and `fixed_tokens`
strategies produce stable, provenance-rich chunks.

### Formats, packs, and jobs

All ten format contracts are implemented: `factual_qa`, `transactional`,
`troubleshooting`, `scenario_response`, `multi_turn`, `feedback_response`,
`conflict_resolution`, `needs_recommendation`, `cross_sell`, and
`intent_response`.

Active prompt packs and recipes select format settings and sample counts only.
Jobs expand as:

```text
chunk × format × sample index
```

The inherited source-language label participates in identity integrity but never
multiplies jobs.

### Prompting and accepted rows

Every active prompt requires the model to preserve source language, register,
terminology, and existing code-switching; forbids translation and unsupported
procedures or claims; and permits `not_applicable` when the source cannot support
the requested interaction.

The strict model-facing schema omits `language`. A model-returned language key is
rejected; the pipeline writes the accepted `sft_chat_v1.language` field from
source/chunk/job provenance.

### Validation, output, and resume

Validation covers strict JSON/schema parsing, message structure and role order,
assistant-final policy, format-specific rules, full language-provenance equality,
exact source evidence, and normalized exact duplicates.

Each run writes:

```text
manifest.json
config.resolved.yaml
sources.jsonl
chunks.jsonl
dataset.jsonl
rejected.jsonl
checkpoint.jsonl
```

Row failures remain inspectable. Checkpoints make accepted, rejected, and
`not_applicable` outcomes terminal; normal resume executes only missing jobs.
Targeted retry reopens only explicitly selected rejection stages. Config hashes
prevent incompatible runs from sharing an output directory. CSV, Parquet, and
legacy JSONL are derived exports; JSONL remains canonical.

## 3. Migration and Compatibility Decisions

- Run configs use strict `version: 2`.
- V1 configs and any recipe or prompt pack with `languages` fail before inference
  with actionable `input.language` migration guidance.
- Active built-in/YAML packs use V2 names and have no language arrays.
- The historical V1 target-language pack remains clearly labeled, inactive, and
  intentionally rejected by the V2 loader.
- Prompt, recipe, job, and config identity changes prevent V1/V2 resume mixing.
- V2 live runs use isolated `-source-preserving-v2-r4` directories. Historical
  V1 and intermediate V2 directories remain untouched as local evidence.
- The deprecated argument-based row writer remains a compatibility path and
  requires an explicit source language. New work uses YAML V2.

## 4. Freeze Acceptance Evidence

All required criteria are met:

- `input.language` and structured `language_field` behavior are deterministic and
  tested.
- Recipes/packs have no active target-language axis; job counts are
  chunk × format × sample index.
- Source, chunk, job, and accepted-row language/origin provenance agree.
- Prompts preserve language/register and never request translation.
- The model does not own the accepted language label.
- V1 target-language configurations fail before inference.
- Public configs, CLI terminology, README, specifications, QA-format contract,
  and test contract consistently describe V2.
- The deterministic suite passes with 54 tests, and `compileall` passes for
  source and tests.
- `configs/example-customer-service.yaml` inspects as 1 source, 1 English chunk,
  and 6 jobs. `configs/all-formats-smoke.yaml` inspects as 1 source, 1 English
  chunk, and 10 jobs.
- Final local live evidence contains 10/10 accepted Globe English rows, 5/5
  accepted Proclamation Tagalog rows, and 5/5 accepted QC SSDD Taglish rows.
- Mechanical review passed all 20 rows for schema, exact evidence, roles/turns,
  format limits, unique identities/signatures, prompt version, and language
  provenance.
- Manual review found no obvious unsupported factual claim or preservation
  violation. Normal resume generated zero rows for each final run.

The final evidence directories are:

- `output/globe-broadband-english-source-preserving-v2-r4`
- `output/proclamation-1041-filipino-source-preserving-v2-r4`
- `output/qc-ssdd-online-services-taglish-source-preserving-v2-r4`

The Filipino and Taglish source/config/provenance files are deliberately local
demo material. Their existing exact paths are preserved and excluded only from
the local Git view; they are not publication artifacts.

## 5. Known Limitations

- Provenance labels are structural declarations, not semantic language detection.
- Exact evidence proves that quoted support occurs in the source, not that every
  generated inference is semantically correct.
- Prompt constraints cannot mathematically prove that a model never drifts in
  language; live quality review remains necessary.
- Deterministic deduplication catches normalized exact conversations, not semantic
  paraphrases.
- Some Globe transactional/troubleshooting rows are weak examples of those
  semantic formats, two Filipino factual rows overlap in subject, and one Taglish
  factual question is mildly awkward. These are quality limitations, not
  provenance or translation failures.
- Execution is local and sequential by default.

## 6. Deferred, Non-Binding Enhancements

The following are outside the freeze and require separately approved scope:

- semantic language-preservation evaluation that never replaces declared
  provenance;
- semantic or LLM-as-judge factuality evaluation against human-reviewed fixtures;
- fuzzy or embedding-based deduplication;
- semantic chunking;
- multi-example batching or bounded concurrency;
- distributed orchestration, databases, or queues;
- model training/fine-tuning or a graphical interface.

Any future enhancement must preserve the no-translation contract, deterministic
identity/resume guarantees, row-level failure isolation, and V2 output
compatibility rules.

## 7. Historical Rationale and Evidence

V1 established the config-driven corpus readers, chunker/format registries,
OpenAI-compatible inference abstraction, strict parsing, validation,
deduplication, dataset directory, checkpoints, targeted retry, resume, and
exports. Its original independent format × target-language design was retired
because it created translation work that does not belong in this product.

The complete V1 plan/checklist, original V2 revision §14, implementation phases,
migration reasoning, live retry history, acceptance criteria, material choices,
and qualitative findings were moved intact to the dated
[historical record](IMPLEMENTATION_PLAN_HISTORY_2026-09-12.md). That archive is
evidence, not an active plan or supported contract.
