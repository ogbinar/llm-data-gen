# llm-data-gen TODO

**Status:** no active required work

The frozen source-language-preserving V2 baseline and the optional Huey + SQLite
queue, including the completed §6.12 reliability remediation, passed acceptance
on 2026-09-12. Inline execution remains the default. The accepted queue scope is
local, single-host SQLite with one consumer process, configurable worker threads,
manual `enqueue CONFIG` reconciliation, and recoverable staged manifest-last
finalization.

Canonical status and final evidence:

- [Implementation plan and status](IMPLEMENTATION_PLAN.md)
- [§6.12 remediation summary](IMPLEMENTATION_PLAN.md#612-completed-pareto-reliability-remediation)
- [Reproducible manual smoke evidence](docs/remediation-smoke-evidence.md)
- [Historical implementation record](IMPLEMENTATION_PLAN_HISTORY_2026-09-12.md)

## Active Required Work

None.

## Optional, Non-Binding Ideas

These are not defects, release blockers, commitments, or approved scope:

- evaluate semantic language preservation and factuality only against reviewed
  human-labeled fixtures;
- evaluate fuzzy or embedding-based deduplication;
- compare semantic chunking with deterministic baselines;
- measure the local queue under representative workloads before considering
  multi-example batching or request/token rate limiting;
- consider an external broker, multiple consumers, or multi-host orchestration
  only if measured requirements exceed the accepted SQLite scope;
- consider automated startup reconciliation, retained-work cleanup, training, or
  a graphical interface as separately designed features.

Any future work must preserve the frozen no-translation/source-language contract,
deterministic identities and output ordering, exact-evidence validation, row-level
failure isolation, inline-default behavior, and `sft_chat_v1` compatibility.
