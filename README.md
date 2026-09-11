# llm-data-gen

A config-driven compiler that turns source corpora into grounded, validated supervised fine-tuning datasets.

## What It Controls

One YAML run configuration selects:

- the input corpus and field mappings;
- a chunking strategy;
- an OpenAI-compatible endpoint and model;
- a prompt pack or explicit QA-format recipes;
- English, Tagalog, or Taglish generation;
- validation rules;
- a resumable output directory.

## Pipeline

~~~text
corpus
  -> normalized source documents
  -> selected chunker
  -> deterministic generation jobs
  -> format + language prompt composition
  -> OpenAI-compatible inference
  -> strict parsing and layered validation
  -> sft_chat_v1 JSONL dataset
~~~

## Setup

~~~bash
uv sync --extra dev
~~~

## Quick Start

Validate configuration without inference:

~~~bash
uv run llm-data-gen validate-config configs/example-customer-service.yaml
~~~

Inspect sources, chunks, recipes, and expected jobs:

~~~bash
uv run llm-data-gen inspect configs/example-customer-service.yaml
~~~

Run generation:

~~~bash
uv run llm-data-gen run configs/example-customer-service.yaml
~~~

Retry selected failed stages:

~~~bash
uv run llm-data-gen retry configs/example-customer-service.yaml --stages generation,parsing
~~~

## Configuration

Paths are resolved relative to the YAML file.

~~~yaml
version: 1
name: example-customer-service-v1

input:
  path: ../data/raw
  format: auto
  recursive: true
  id_field: source_id
  text_field: text
  title_field: title
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
  temperature: 0.2

generation:
  prompt_pack: customer_service_core_v1

validation:
  grounding: exact_evidence
  reject_duplicates: true
  require_assistant_final_turn: true

output:
  directory: ../output/example-customer-service-v1
  format: jsonl
  resume: true
  include_chunk_text: false
~~~

Explicit recipes can replace matching formats from a prompt pack:

~~~yaml
generation:
  prompt_pack: customer_service_core_v1
  recipes:
    - format: factual_qa
      languages: [english]
      num_examples: 5
~~~

## Corpus Readers

Supported input formats:

- text;
- Markdown;
- JSON objects or arrays;
- JSONL;
- CSV;
- Parquet.

A corpus may be one file or a directory. Directory traversal and record order are deterministic. Malformed records are isolated in the rejected artifact.

JSON, JSONL, CSV, and Parquet use the configured ID, title, and text fields. Other fields are retained as source metadata unless an explicit metadata field list is supplied.

## Chunkers

- `document`: one chunk per source record.
- `recursive_text`: paragraph-, line-, sentence-, and character-aware splitting.
- `markdown_sections`: heading-aware Markdown splitting with size fallback.
- `fixed_tokens`: deterministic whitespace-token windows.

List available chunkers:

~~~bash
uv run llm-data-gen list-chunkers
~~~

## Endpoints

The inference client uses the OpenAI-compatible `/chat/completions` contract. llama-swap, vLLM, llama.cpp, or another compatible service can be selected through configuration without changing prompt or pipeline code.

List built-in endpoint profiles:

~~~bash
uv run llm-data-gen list-endpoints
~~~

Secrets are referenced by environment-variable name. Resolved configuration artifacts never contain secret values.

## Formats and Languages

Implemented formats:

1. `factual_qa`
2. `transactional`
3. `troubleshooting`
4. `scenario_response`
5. `multi_turn`
6. `feedback_response`
7. `conflict_resolution`
8. `needs_recommendation`
9. `cross_sell`
10. `intent_response`

Languages:

- `english`
- `tagalog`
- `taglish`

Format and language are independent prompt dimensions. Prompt packs are named groups of recipes; explicit recipes provide per-run overrides.

~~~bash
uv run llm-data-gen list-formats
uv run llm-data-gen list-languages
uv run llm-data-gen list-prompt-packs
~~~

## Canonical Dataset Schema

The canonical physical format is JSONL. Each accepted line validates as `sft_chat_v1`.

~~~json
{
  "schema_version": "sft_chat_v1",
  "example_id": "sha256:...",
  "job_id": "sha256:...",
  "source_id": "doc-001",
  "chunk_id": "doc-001::recursive_text::v1::0001",
  "domain": "customer_service",
  "qa_format": "troubleshooting",
  "language": "taglish",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "evidence": ["Exact quotation from the source chunk."],
  "prompt_name": "troubleshooting",
  "prompt_version": "v1",
  "prompt_hash": "sha256:...",
  "generator_model": "qwen38-27b-chat-rocmfp4",
  "inference_backend": "llama-swap",
  "endpoint_profile": "local-qwen",
  "generation_parameters": {},
  "generated_at": "2026-09-11T00:00:00Z",
  "validation_status": "passed",
  "metadata": {}
}
~~~

## Output Directory

Each run produces:

~~~text
manifest.json
config.resolved.yaml
sources.jsonl
chunks.jsonl
dataset.jsonl
rejected.jsonl
checkpoint.jsonl
~~~

The config hash prevents incompatible runs from sharing an output directory. Checkpoints track terminal job outcomes, and resume executes only missing jobs.

## Validation

The pipeline checks:

- strict JSON and Pydantic schema conformance;
- non-empty structured messages;
- user/assistant role order;
- required final assistant response;
- requested format and language labels;
- format-specific structure;
- exact evidence quotations;
- exact normalized conversation duplicates.

Recommendation formats may return `not_applicable` rather than inventing unsupported products or benefits.

## Exports

JSONL remains canonical. Derived exports are available:

~~~bash
uv run llm-data-gen export output/run/dataset.jsonl output/run/dataset.parquet --format parquet
uv run llm-data-gen export output/run/dataset.jsonl output/run/dataset.csv --format csv
uv run llm-data-gen export output/run/dataset.jsonl output/run/legacy.jsonl --format legacy-jsonl
~~~

Free-form metadata maps are serialized as JSON strings in Parquet to maintain a stable physical schema.

## Compatibility

The original argument-based CLI remains temporarily available:

~~~bash
uv run llm-data-gen --input data/sample_telco_corpus.jsonl --source-id telco-doc-001
~~~

It writes the legacy row schema. New work should use the YAML run workflow.

## Tests

~~~bash
uv run pytest -q
~~~

The suite covers legacy behavior, configuration, readers, chunkers, registries, prompt composition, job expansion, chat parsing, validation, deduplication, output manifests, resume, retries, and exports.

## Deliberate Boundaries

The system remains local and sequential by default. Semantic chunking, fuzzy deduplication, semantic language classification, LLM-as-judge validation, batched generation, and distributed orchestration remain optional future extensions behind the current interfaces.

