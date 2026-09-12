# llm-data-gen Product Specification

## Status

Version 2 is the binding source-language-preserving contract in
`IMPLEMENTATION_PLAN.md` §0. Version 1 is historical; its complete design record
is archived in `IMPLEMENTATION_PLAN_HISTORY_2026-09-12.md`.

## Purpose

Turn local source corpora into grounded, provenance-rich supervised fine-tuning examples through a reproducible configuration.

## User Contract

A run configuration must select:

1. corpus path and source-field mappings;
2. chunking strategy and parameters;
3. OpenAI-compatible endpoint and model;
4. prompt pack or explicit QA-format recipes;
5. required default source language and optional structured-record language field;
6. validation policy;
7. output directory and resume policy.

The primary command is:

~~~bash
uv run llm-data-gen run CONFIG_PATH
~~~

## Inputs

A corpus can be one file or a directory containing text, Markdown, JSON, JSONL, CSV, or Parquet records.

Every valid source record becomes a `SourceDocument` with stable identity,
checksum, declared language and language origin, provenance, domain, text, and
metadata. Text and Markdown use `input.language`. Structured records may override
that default through `language_field`, enabling mixed-language corpora. Invalid
non-empty language values are isolated as source failures.

## Chunking

All chunkers consume `SourceDocument + ChunkingConfig` and return stable `Chunk` records.

Required strategies:

- `document`;
- `recursive_text`;
- `markdown_sections`;
- `fixed_tokens`.

Chunk provenance includes source identity/checksum, inherited language and origin,
strategy/version, index, offsets when available, token estimate, and source metadata.

## Generation

Every requested example is an independent deterministic job:

~~~text
chunk + QA format + prompt version + settings + sample index
~~~

The system expands prompt packs and recipes before inference. Recipes select
formats only; source language never multiplies jobs. Prompts preserve the chunk's
declared language/register and prohibit translation or intentional changes to
code-switching.

All inference passes through one OpenAI-compatible client.

## QA Formats

The format contract is defined in `spec/qa-formats.md`. Ten formats are registered
and implemented. Supported source labels are English, Filipino, Tagalog, and
Taglish. There is no translation mode.

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
- requested format and source/chunk/job language-provenance consistency;
- format-specific rules;
- exact source evidence;
- exact normalized duplicate detection.

Unsupported recommendation-style requests may produce a structured `not_applicable` outcome.

## Reliability

- One failed source or generation does not abort the whole run.
- Terminal job outcomes are checkpointed.
- Resume executes only missing jobs.
- Selected rejected stages can be retried explicitly.
- V1 target-language configs fail before inference with actionable migration
  guidance. The deprecated argument CLI requires explicit source language.

## Exports

JSONL is canonical. CSV, Parquet, and legacy JSONL are derived exports.

## Non-Goals

The current system does not translate, select a target language, or claim semantic
language detection. It also does not require a database, queue, distributed
scheduler, semantic chunker, fuzzy deduplicator, or LLM-as-judge validator.
