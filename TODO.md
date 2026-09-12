# llm-data-gen TODO

**Freeze status:** V2 complete and freeze-ready

**Frozen baseline:** source-language-preserving config V2

**Freeze date:** 2026-09-12

**Canonical plan and evidence:**
[IMPLEMENTATION_PLAN.md — Frozen V2 Baseline](IMPLEMENTATION_PLAN.md#0-frozen-v2-baseline-canonical)

## Current Required Work

None. There are zero active required tasks for the V2 freeze.

The binding product contract is complete: `llm-data-gen` reformats source
material into grounded QA/interaction formats without translating it. Language is
declared provenance inherited through source, chunk, job, and row; it is not a
recipe axis or model-selected target.

## Freeze Completion

- [x] Require strict run-config `version: 2` and `input.language`.
- [x] Support explicit `language_field` overrides for structured corpora.
- [x] Reject legacy target-language arrays with actionable migration errors.
- [x] Expand jobs as chunk × format × sample index only.
- [x] Carry language and language-origin provenance through every pipeline stage.
- [x] Remove language selection from active recipes and prompt packs.
- [x] Prompt every format to preserve source language, register, and existing
  code-switching without translation.
- [x] Keep the model out of the accepted row's authoritative language label.
- [x] Preserve exact-evidence grounding, conversation checks, deduplication,
  `not_applicable`, row-level rejection, targeted retry, and resume behavior.
- [x] Migrate public examples and active packs to V2 with isolated output paths.
- [x] Keep the historical V1 pack explicit, inactive, and loudly rejected by V2.
- [x] Align README, product specification, QA-format contract, test contract,
  shipped configs, and CLI terminology.
- [x] Validate and inspect the shipped public configurations without inference.
- [x] Pass the deterministic tests, compile checks, diff checks, and repository
  consistency audit.

## Acceptance Evidence

- Deterministic suite: 54 tests passed at the V2 acceptance run.
- Compilation: `uv run python -m compileall -q src tests` passed.
- Public configs: `configs/example-customer-service.yaml` and
  `configs/all-formats-smoke.yaml` validate and inspect successfully.
- Final local live evidence:
  - Globe broadband, English: 1 source, 1 chunk, 10 jobs, 10 accepted.
  - Proclamation No. 1041, Tagalog: 1 source, 1 chunk, 5 jobs, 5 accepted.
  - QC SSDD online services, Taglish: 1 source, 1 chunk, 5 jobs, 5 accepted.
- All 20 accepted r4 rows passed mechanical schema, exact-evidence, role,
  format-limit, identity, and language-provenance checks.
- Manual review found no obvious unsupported factual claim or language-preservation
  violation. Normal resume generated zero new rows for all three final runs.
- Historical V1 and intermediate V2 output directories remain preserved as
  ignored local evidence and are never mixed with the frozen V2 runs.

## Known Quality Limits

- Declared language provenance is validated, but semantic language detection is
  not claimed.
- Exact evidence proves quotation presence, not complete semantic factuality.
- Prompt constraints reduce translation and language drift but cannot prove their
  absence mathematically.
- Some accepted demo rows have weak semantic fit or mildly awkward wording even
  though they pass deterministic checks; details remain in the implementation
  plan's acceptance record.

## Optional Non-Binding Backlog

These are ideas, not freeze requirements. Each needs evidence and a separately
approved scope before implementation:

- evaluate fuzzy or embedding-based deduplication against a reviewed dataset;
- add semantic language-preservation evaluation without making the model's label
  authoritative;
- compare semantic chunking with deterministic chunking baselines;
- evaluate an LLM-as-judge only against fixed human-reviewed examples;
- add bounded batching or concurrency if profiling shows inference throughput is
  a practical bottleneck;
- consider distributed orchestration only after local sequential execution is
  demonstrably insufficient.

## Historical Completion Record

V1 was completed on 2026-09-11 and established the config-driven reader,
chunker, format registry, OpenAI-compatible inference, validation, output,
resume/retry, export, and CLI foundations. Its former independent
format × target-language design is historical only and is superseded by the V2
source-language-preservation contract.

The full V1 checklist, original rationale, V2 migration plan, material choices,
live run counts, retries, and qualitative findings remain archived in place in
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). No historical generated output
was deleted or rewritten during the freeze.
