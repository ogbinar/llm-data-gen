from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageDefinition:
    name: str
    display_name: str


LANGUAGE_REGISTRY: dict[str, LanguageDefinition] = {
    "english": LanguageDefinition("english", "English"),
    "filipino": LanguageDefinition("filipino", "Filipino"),
    "tagalog": LanguageDefinition("tagalog", "Tagalog"),
    "taglish": LanguageDefinition("taglish", "Taglish"),
}


def resolve_language(name: str) -> LanguageDefinition:
    normalized = name.strip().casefold()
    try:
        return LANGUAGE_REGISTRY[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(LANGUAGE_REGISTRY))
        raise ValueError(f"unknown source language {name!r}; supported values: {supported}") from exc


def list_languages() -> list[LanguageDefinition]:
    return list(LANGUAGE_REGISTRY.values())
