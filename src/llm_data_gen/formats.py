from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FormatDefinition:
    name: str
    display_name: str
    purpose: str
    instruction: str
    constraints: tuple[str, ...]
    prompt_version: str = "v5"
    implemented: bool = True
    min_messages: int = 2
    supported_settings: tuple[str, ...] = ()
    allows_not_applicable: bool = False


FORMAT_REGISTRY: dict[str, FormatDefinition] = {
    "factual_qa": FormatDefinition(
        "factual_qa", "Factual QA", "Generate a grounded factual question and answer.",
        "Write a realistic question whose answer is directly supported by the source, then answer it clearly.",
        ("Use only facts present in the source.", "Prefer useful, non-trivial questions when the source permits."),
    ),
    "transactional": FormatDefinition(
        "transactional", "Transactional", "Turn source knowledge into a request and correct process response.",
        "Write a realistic customer request and the correct action, process, or instruction supported by the source.",
        ("Do not invent steps, requirements, fees, or timelines.",),
    ),
    "troubleshooting": FormatDefinition(
        "troubleshooting", "Troubleshooting", "Generate a problem and grounded diagnostic or resolution response.",
        "Write a realistic customer problem and a diagnostic or resolution-oriented response supported by the source.",
        (
            "Do not invent troubleshooting steps.",
            "Distinguish possible diagnosis from confirmed facts.",
            "If the source gives no diagnostic or resolution steps, say so explicitly; do not supply common-sense steps such as restarting equipment or checking cables.",
        ),
    ),
    "scenario_response": FormatDefinition(
        "scenario_response", "Scenario Response", "Generate a situation and the best grounded response.",
        "Write a realistic situation and the best response based only on the source.",
        ("Make the situation plausible and the response actionable.",),
    ),
    "multi_turn": FormatDefinition(
        "multi_turn", "Multi-turn Dialogue", "Generate a coherent conversation grounded in the source.",
        "Write a short multi-turn conversation in which later turns depend on information from earlier turns.",
        ("The assistant may ask a clarification question when appropriate.", "Do not create independent question-answer pairs disguised as a conversation."),
        min_messages=4,
        supported_settings=("max_turns",),
    ),
    "feedback_response": FormatDefinition(
        "feedback_response", "Feedback Response", "Generate customer feedback and an appropriate response.",
        "Write realistic customer feedback and a grounded acknowledgment or response.",
        ("Do not promise actions or outcomes absent from the source.",),
    ),
    "conflict_resolution": FormatDefinition(
        "conflict_resolution", "Conflict Resolution", "Generate a difficult interaction and de-escalating response.",
        "Write a realistic complaint and a calm, grounded de-escalation or resolution response.",
        (
            "Do not promise refunds, compensation, exceptions, or escalation outcomes unless supported.",
            "Do not pretend to inspect service, dispatch a technician, or provide undocumented troubleshooting steps.",
            "When the source is insufficient, acknowledge the concern and state the documented limits without inventing a resolution path.",
        ),
    ),
    "needs_recommendation": FormatDefinition(
        "needs_recommendation", "Needs Recommendation", "Generate a need and a source-supported recommendation.",
        "Write a customer need and recommend only a product, service, rule, or option supported by the source.",
        (
            "Return not_applicable when the source supports no recommendation.",
            "Do not map a named plan, price, speed tier, use case, or benefit to another unless the source explicitly connects them.",
        ),
        allows_not_applicable=True,
    ),
    "cross_sell": FormatDefinition(
        "cross_sell", "Cross-sell", "Generate a complementary source-supported recommendation.",
        "Write a current customer or product context and a relevant complementary recommendation supported by the source.",
        (
            "Never invent a product or benefit.",
            "Do not claim upgrade eligibility, availability, or a plan-to-speed mapping unless the source explicitly states it.",
            "Return not_applicable when no complementary recommendation is supported.",
        ),
        allows_not_applicable=True,
    ),
    "intent_response": FormatDefinition(
        "intent_response", "Intent Response", "Generate a message, intent label, and grounded response.",
        "Write a realistic customer message, infer its intent, and provide an appropriate grounded response.",
        ("Store the inferred intent in metadata.intent.",),
    ),
}

FORMAT_ALIASES = {
    "Grounded QA": "factual_qa",
    "Scenario -> response": "scenario_response",
    "Intent -> response": "intent_response",
    "Multi-turn dialogue": "multi_turn",
    "Troubleshooting / resolution": "troubleshooting",
}


def resolve_format(name: str) -> FormatDefinition:
    canonical = FORMAT_ALIASES.get(name, name)
    try:
        definition = FORMAT_REGISTRY[canonical]
    except KeyError as exc:
        raise ValueError(f"unknown QA format: {name}") from exc
    if not definition.implemented:
        raise ValueError(f"QA format is registered but not enabled: {canonical}")
    return definition


def list_formats() -> list[FormatDefinition]:
    return list(FORMAT_REGISTRY.values())
