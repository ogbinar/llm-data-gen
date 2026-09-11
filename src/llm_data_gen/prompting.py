from __future__ import annotations

import hashlib
import json

from .formats import resolve_format
from .languages import resolve_language
from .models import Chunk, GenerationJob


def compose_prompt(chunk: Chunk, job: GenerationJob) -> tuple[list[dict[str, str]], str]:
    qa_format = resolve_format(job.qa_format)
    language = resolve_language(job.language)
    constraints = "\n".join(f"- {item}" for item in qa_format.constraints)
    turn_rule = ""
    if job.qa_format == "multi_turn":
        turn_rule = (
            f"\n- Produce at least {qa_format.min_messages} messages."
            + (f"\n- Produce no more than {job.max_turns} messages." if job.max_turns else "")
        )

    system = (
        "You create grounded supervised fine-tuning data. "
        "Use only information supported by the supplied source chunk. "
        "Return exactly one JSON object and no prose or Markdown."
    )
    user = f"""TASK TYPE
{qa_format.name}: {qa_format.instruction}

LANGUAGE
{language.instruction}

FORMAT CONSTRAINTS
{constraints}{turn_rule}

GENERAL REQUIREMENTS
- Make the user side realistic and the assistant side clear and helpful.
- Copy one or more short exact supporting quotations into evidence.
- Do not invent products, policies, prices, procedures, promises, or facts.
- If the requested interaction cannot be safely supported, return status=not_applicable with a reason.
- For generated output, qa_format must be {qa_format.name} and language must be {language.name}.

OUTPUT SCHEMA
Generated:
{{"status":"generated","qa_format":"{qa_format.name}","language":"{language.name}","messages":[{{"role":"user","content":"..."}},{{"role":"assistant","content":"..."}}],"evidence":["exact source quote"],"metadata":{{}}}}

Not applicable:
{{"status":"not_applicable","qa_format":"{qa_format.name}","language":"{language.name}","messages":[],"evidence":[],"metadata":{{}},"reason":"..."}}

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

