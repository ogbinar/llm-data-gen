from __future__ import annotations

from .models import Chunk, PromptFamily

PROMPT_VERSION = "v1"


def families() -> list[PromptFamily]:
    return [
        PromptFamily.grounded_qa,
        PromptFamily.scenario_response,
        PromptFamily.intent_response,
        PromptFamily.multi_turn_dialogue,
        PromptFamily.troubleshooting_resolution,
    ]


def build_messages(chunk: Chunk, family: PromptFamily) -> list[dict[str, str]]:
    system = (
        "You generate grounded synthetic training examples from one source chunk. "
        "Return strict JSON with keys task_type, input_text, output_text, evidence. "
        "evidence must be an exact short quote copied from the source chunk."
    )
    if family == PromptFamily.grounded_qa:
        instruction = "Write a factual question answerable from the chunk and the matching answer."
    elif family == PromptFamily.scenario_response:
        instruction = "Write a user scenario and the best grounded response."
    elif family == PromptFamily.intent_response:
        instruction = "Write a user intent and the best grounded response."
    elif family == PromptFamily.multi_turn_dialogue:
        instruction = "Write a short grounded two-turn conversation."
    else:
        instruction = "Write a troubleshooting problem and a resolution-oriented reply."

    user = (
        f"Prompt family: {family.value}\n"
        f"Instruction: {instruction}\n\n"
        f"Source title: {chunk.title}\n"
        f"Source chunk:\n{chunk.text}\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

