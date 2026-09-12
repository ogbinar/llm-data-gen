# llm-data-gen

Turn source corpora into grounded, validated chat datasets through one
reproducible YAML configuration.

> **Frozen V2 rule:** change the interaction format, never the source language.
> The pipeline does not translate, select a target language, or intentionally add
> or remove code-switching.

[Five-minute walkthrough](#five-minute-walkthrough) ·
[Practical reference](#practical-reference) ·
[Project status](#project-status) ·
[GitHub](https://github.com/ogbinar/llm-data-gen)

## Five-Minute Walkthrough

### 1. Problem and project introduction

Useful fine-tuning data needs more than fluent model output: it needs grounding,
consistent structure, traceable provenance, failure isolation, and reproducible
runs. `llm-data-gen` compiles documents into QA and interaction formats while
keeping evidence and source identity attached to every accepted row.

Language is input provenance—not a generation axis. English stays English,
Filipino/Tagalog stays Filipino/Tagalog, and Taglish stays Taglish.

### 2. Workflow

```text
corpus + input.language
  -> sources -> chunks -> format jobs
  -> preserving prompt -> OpenAI-compatible model
  -> parse -> validate -> accept or reject
  -> resumable sft_chat_v1 dataset
```

Jobs expand as `chunk × format × sample index`, never by language. Exact evidence,
format structure, duplicates, and provenance are checked before a row is accepted.

### 3. Architecture

```text
[RunConfig V2]
      |
[readers] -> [chunkers] -> [jobs + prompts]
                                  |
                         [inference client]
                                  |
                    [parser + validators]
                                  |
                    [writer + exporters]
```

Typed stages keep corpus handling, formats, inference, validation, and output
independent. Language and its origin flow `source -> chunk -> job -> row`; the
pipeline supplies the accepted label instead of trusting the model to choose it.
Any OpenAI-compatible endpoint can be configured.

### 4. Configuration

Install with Python 3.11+ and [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
```

Minimal valid V2 YAML, when saved under `configs/`:

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

Paths are relative to the config file. Omitted sections use validated defaults,
including the configurable local endpoint profile. The shipped explicit config
uses llama-swap at `http://127.0.0.1:8080/v1` with
`qwen38-27b-chat-rocmfp4`; neither is an architectural requirement.

Validate and inspect without inference, then run after starting the configured
backend:

```bash
uv run llm-data-gen validate-config configs/example-customer-service.yaml
uv run llm-data-gen inspect configs/example-customer-service.yaml
uv run llm-data-gen run configs/example-customer-service.yaml
```

### 5. English, Tagalog, and Taglish examples

These are short source excerpts and exact accepted factual QA rows from the final
r4 preservation runs.

**English — Globe broadband**

- Input excerpt: “including 300 Mbps, 500 Mbps, 1 Gbps, and 2.5 Gbps”
- User: “What speed tiers are available for GFiber plans on the Globe AT HOME broadband page?”
- Assistant: “The available speed tiers for GFiber plans include 300 Mbps, 500 Mbps, 1 Gbps, and 2.5 Gbps.”

**Tagalog — Proclamation No. 1041**

- Input excerpt:

  > (Sgd.) RUBEN D. TORRES
  > Kalihim Tagapagpaganap

- User: “Sino ang naglagda bilang Kalihim Tagapagpaganap ng Proklamasyon Blg. 1041?”
- Assistant: “Si Ruben D. Torres ang naglagda bilang Kalihim Tagapagpaganap ng Proklamasyon Blg. 1041.”

**Taglish — QC SSDD online services**

- Input excerpt: “may approved QCitizen ID para makapag-upload ng mga required documents online”
- User: “Ano ang kailangan ng mga QCitizen para makapag-upload ng mga required documents online para sa mga serbisyong hatid ng SSDD?”
- Assistant: “Kinakailangan nilang may approved QCitizen ID para makapag-upload ng mga required documents online.”

The examples are illustrative excerpts, not complete documents. Source content
retains its original rights and provenance and is not covered by this software's
MIT license.

## Practical Reference

### Inputs and formats

Readers support text, Markdown, JSON, JSONL, CSV, and Parquet files or directories.
`input.language` is required and accepts `english`, `filipino`, `tagalog`, or
`taglish`. Structured formats may opt into per-record overrides with
`input.language_field`; mixed-language plain-text directories require separate
runs. Semantic language detection is not claimed.

Chunkers: `document`, `recursive_text`, `markdown_sections`, `fixed_tokens`.

Interaction formats: `factual_qa`, `transactional`, `troubleshooting`,
`scenario_response`, `multi_turn`, `feedback_response`, `conflict_resolution`,
`needs_recommendation`, `cross_sell`, and `intent_response`.

List active registries with `list-source-languages`, `list-formats`,
`list-chunkers`, `list-prompt-packs`, or `list-endpoints`.

### Output artifacts

```text
manifest.json          status, counts, timing, model, hashes
config.resolved.yaml   effective secret-free configuration
sources.jsonl          normalized source provenance
chunks.jsonl           chunks and inherited provenance
dataset.jsonl          accepted sft_chat_v1 rows
rejected.jsonl         source and row-level failures
checkpoint.jsonl       append-safe outcomes for resume
```

Validation covers strict schemas, message roles and final turn, format rules,
language-provenance equality, exact source evidence, and normalized exact
duplicates. Unsupported requests may become `not_applicable` instead of invented
content.

### Retry and resume

Inspect `rejected.jsonl`, then retry only relevant stages:

```bash
uv run llm-data-gen retry configs/example-customer-service.yaml --stages generation,parsing
uv run llm-data-gen retry configs/example-customer-service.yaml --stages validation
```

Do not blindly retry `not_applicable` or duplicate outcomes. With `resume: true`,
a normal run executes only jobs without terminal checkpoints. Config hashes block
incompatible output-directory reuse.

### Export and test

JSONL is canonical; CSV, Parquet, and legacy JSONL are derived exports:

```bash
uv run llm-data-gen export output/example-customer-service-source-preserving-v2/dataset.jsonl output/example.parquet --format parquet
uv run llm-data-gen export output/example-customer-service-source-preserving-v2/dataset.jsonl output/example.csv --format csv
uv run llm-data-gen export output/example-customer-service-source-preserving-v2/dataset.jsonl output/example.legacy.jsonl --format legacy-jsonl
uv run pytest -q
uv run python -m compileall -q src tests
```

### Compatibility and limits

Strict config V2 requires `input.language`. V1 configs and recipe/pack `languages`
arrays fail before inference with migration guidance. The deprecated argument CLI
remains available but requires `--source-language`; new work should use YAML V2.

The frozen baseline is local and sequential. Exact evidence is not semantic
factuality scoring, prompt constraints cannot prove zero language drift, and
deduplication is exact rather than semantic. Semantic detection/judging,
fuzzy deduplication, semantic chunking, batching, and distributed orchestration
are deferred.

## Project Status

Source-language-preserving V2 is frozen and acceptance-validated as of 2026-09-12.
There are no active required freeze tasks. See the canonical
[implementation plan](IMPLEMENTATION_PLAN.md), [TODO](TODO.md),
[product specification](spec.md), [QA-format contract](spec/qa-formats.md), and
[test contract](TEST_CASES.md).
