from __future__ import annotations

import hashlib
import json

from .formats import resolve_format
from .models import Chunk, GenerationJob


def compose_prompt(chunk: Chunk, job: GenerationJob) -> tuple[list[dict[str, str]], str]:
    qa_format = resolve_format(job.qa_format)
    constraints = "\n".join(f"- {item}" for item in qa_format.constraints)
    turn_rule = ""
    if job.qa_format == "multi_turn":
        turn_rule = (
            f"\n- Produce at least {qa_format.min_messages} messages."
            + (f"\n- Produce no more than {job.max_turns} messages." if job.max_turns else "")
        )
    focus_hint = {
        1: "Prefer a useful fact from the opening third of the source when supported.",
        2: "Prefer a useful fact from the middle third of the source when supported.",
    }.get(job.sample_index, "Prefer a useful fact from the closing third of the source when supported.")

    system = (
        "You create grounded supervised fine-tuning data. "
        "Use only information supported by the supplied source chunk. "
        "Preserve the source language and register exactly: never translate or "
        "intentionally add or remove code-switching. Return exactly one JSON object "
        "and no prose or Markdown."
    )
    user = f"""TASK TYPE
{qa_format.name}: {qa_format.instruction}

SOURCE LANGUAGE PROVENANCE
Declared source language: {job.language}
This label is supplied by the pipeline. Do not return or alter it.

FORMAT CONSTRAINTS
{constraints}{turn_rule}

GENERAL REQUIREMENTS
- This is deterministic variation slot {job.sample_index}; choose a distinct
  source-supported focus and phrasing from other slots for this format.
- Variation focus hint: {focus_hint}
- Make the user side realistic and the assistant side clear and helpful.
- Preserve the source's language, register, and natural style.
- Never translate the source or choose another output language.
- Do not introduce code-switching or remove code-switching present in the source.
- Retain supported technical terms and proper nouns as written when appropriate.
- Copy one or more short supporting quotations into evidence by selecting direct,
  contiguous substrings from SOURCE. Preserve every character and word exactly;
  do not paraphrase, omit opening words, or retype a quotation from memory.
- Do not invent products, policies, prices, procedures, promises, or facts.
- Every factual, procedural, diagnostic, eligibility, and recommendation claim in
  the messages must be directly entailed by SOURCE. Do not combine separate source
  facts into a relationship or mapping that SOURCE does not state.
- If the requested interaction cannot be safely supported, return
  status=not_applicable with a reason rather than filling gaps from general knowledge.
- For generated output, qa_format must be {qa_format.name}.

OUTPUT SCHEMA
Generated:
{{"status":"generated","qa_format":"{qa_format.name}","messages":[{{"role":"user","content":"..."}},{{"role":"assistant","content":"..."}}],"evidence":["exact source quote"],"metadata":{{}}}}

Not applicable:
{{"status":"not_applicable","qa_format":"{qa_format.name}","messages":[],"evidence":[],"metadata":{{}},"reason":"..."}}

SOURCE
Title: {chunk.title}
Source ID: {chunk.source_id}
Chunk ID: {chunk.chunk_id}
Text:
{chunk.text}
"""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    prompt_hash = hashlib.sha256(
        json.dumps(messages, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return messages, f"sha256:{prompt_hash}"
