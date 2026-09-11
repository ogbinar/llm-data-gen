# llm-data-gen Product Specification

## Status

Version 1 of the config-driven dataset compiler contract is frozen in `IMPLEMENTATION_PLAN.md`.

## Purpose

Turn local source corpora into grounded, provenance-rich supervised fine-tuning examples through a reproducible configuration.

## User Contract

A run configuration must select:

1. corpus path and source-field mappings;
2. chunking strategy and parameters;
3. OpenAI-compatible endpoint and model;
4. prompt pack or explicit QA-format recipes;
5. target languages;
6. validation policy;
7. output directory and resume policy.

The primary command is:

~~~bash
uv run llm-data-gen run CONFIG_PATH
~~~

## Inputs

A corpus can be one file or a directory containing text, Markdown, JSON, JSONL, CSV, or Parquet records.

Every valid source record becomes a `SourceDocument` with stable identity, checksum, provenance, domain, text, and metadata. Malformed records must not terminate processing of the remaining corpus.

## Chunking

All chunkers consume `SourceDocument + ChunkingConfig` and return stable `Chunk` records.

Required strategies:

- `document`;
- `recursive_text`;
- `markdown_sections`;
- `fixed_tokens`.

Chunk provenance includes source identity/checksum, strategy/version, index, offsets when available, token estimate, and source metadata.

## Generation

Every requested example is an independent deterministic job:

~~~text
chunk + QA format + language + prompt version + settings + sample index
~~~

The system expands prompt packs and recipes before inference. Format instructions, language instructions, output schema, and source chunk are composed independently.

All inference passes through one OpenAI-compatible client.

## QA Formats

The format contract is defined in `spec/qa-formats.md`. Ten formats are registered and implemented. Languages are English, Tagalog, and Taglish.

## Output

The canonical logical schema is `sft_chat_v1`. The canonical physical format is JSONL.

Every run writes a self-describing directory containing:

- resolved secret-free configuration;
- run manifest;
- source manifest;
- chunk manifest;
- accepted dataset;
- rejected outcomes;
- job checkpoints.

A config hash prevents accidental mixing of incompatible runs.

## Validation

Required layers:

- parsing and schema;
- conversation roles and structure;
- requested format/language contract;
- format-specific rules;
- exact source evidence;
- exact normalized duplicate detection.

Unsupported recommendation-style requests may produce a structured `not_applicable` outcome.

## Reliability

- One failed source or generation does not abort the whole run.
- Terminal job outcomes are checkpointed.
- Resume executes only missing jobs.
- Selected rejected stages can be retried explicitly.
- Existing legacy CLI behavior remains available during migration.

## Exports

JSONL is canonical. CSV, Parquet, and legacy JSONL are derived exports.

## Non-Goals

The current system does not require a database, queue, distributed scheduler, semantic chunker, fuzzy deduplicator, semantic language detector, or LLM-as-judge validator.

