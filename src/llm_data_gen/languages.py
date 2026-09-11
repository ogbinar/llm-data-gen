from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageDefinition:
    name: str
    instruction: str


LANGUAGE_REGISTRY: dict[str, LanguageDefinition] = {
    "english": LanguageDefinition("english", "Use natural, clear English appropriate to the interaction."),
    "tagalog": LanguageDefinition(
        "tagalog",
        "Use natural Filipino/Tagalog. Retain technical or commonly used English terms when Filipino speakers would normally do so.",
    ),
    "taglish": LanguageDefinition(
        "taglish",
        "Use natural conversational Taglish as used by Filipino customers and service representatives. Code-switch naturally; avoid mechanical translation and textbook-style Taglish.",
    ),
}


def resolve_language(name: str) -> LanguageDefinition:
    try:
        return LANGUAGE_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"unknown language: {name}") from exc


def list_languages() -> list[LanguageDefinition]:
    return list(LANGUAGE_REGISTRY.values())

