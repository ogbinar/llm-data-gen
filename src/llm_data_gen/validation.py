from __future__ import annotations

import hashlib
import json
import re

from .config import ValidationConfig
from .formats import resolve_format
from .models import Chunk, GeneratedExample, GenerationJob, ModelExample


def validate_generated_example(
    example: GeneratedExample,
    job: GenerationJob,
    chunk: Chunk,
    config: ValidationConfig,
) -> list[str]:
    errors: list[str] = []
    if example.status != "generated":
        return errors
    if example.qa_format != job.qa_format:
        errors.append(f"format mismatch: expected {job.qa_format!r}, got {example.qa_format!r}")
    if job.language != chunk.language:
        errors.append(
            f"source language provenance mismatch: job={job.language!r}, chunk={chunk.language!r}"
        )
    if job.language_origin != chunk.language_origin:
        errors.append(
            "source language origin mismatch: "
            f"job={job.language_origin!r}, chunk={chunk.language_origin!r}"
        )
    if not example.messages:
        errors.append("messages is empty")
        return errors

    roles = [message.role for message in example.messages]
    if any(role not in {"user", "assistant"} for role in roles):
        errors.append("generated messages may only use user and assistant roles")
    if roles and roles[0] != "user":
        errors.append("conversation must start with user")
    if any(left == right for left, right in zip(roles, roles[1:])):
        errors.append("conversation roles must alternate")
    if config.require_assistant_final_turn and roles[-1] != "assistant":
        errors.append("conversation must end with assistant")
    if any(not message.content.strip() for message in example.messages):
        errors.append("message content must not be empty")

    definition = resolve_format(job.qa_format)
    if len(example.messages) < definition.min_messages:
        errors.append(f"{job.qa_format} requires at least {definition.min_messages} messages")
    if job.max_turns and len(example.messages) > job.max_turns:
        errors.append(f"conversation exceeds max_turns={job.max_turns}")
    if job.qa_format == "intent_response" and not str(example.metadata.get("intent", "")).strip():
        errors.append("intent_response requires metadata.intent")

    if config.grounding == "exact_evidence":
        if not example.evidence:
            errors.append("missing evidence")
        for evidence in example.evidence:
            if not evidence.strip():
                errors.append("evidence must not be empty")
            elif evidence not in chunk.text:
                errors.append(f"evidence not found in source chunk: {evidence!r}")
    return errors


def normalized_message_signature(example: GeneratedExample) -> str:
    normalized = [
        {
            "role": message.role,
            "content": re.sub(r"\s+", " ", message.content.strip()).casefold(),
        }
        for message in example.messages
    ]
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def second_pass_validate(
    example: ModelExample,
    chunk: Chunk,
    *,
    expected_task_type: str | None = None,
) -> tuple[bool, str | None]:
    if expected_task_type and example.task_type.strip() != expected_task_type.strip():
        return False, f"prompt-family mismatch: expected {expected_task_type!r}, got {example.task_type!r}"
    if not example.input_text.strip():
        return False, "empty input_text"
    if not example.output_text.strip():
        return False, "empty output_text"
    if not example.evidence.strip():
        return False, "missing evidence"
    if example.evidence not in chunk.text:
        return False, "evidence not found in source chunk"
    if len(example.output_text.strip()) < 8:
        return False, "output_text too short"
    return True, None
