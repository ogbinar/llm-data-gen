from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .config import GenerationConfig, GenerationRecipe


class PromptPack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    name: str
    recipes: list[GenerationRecipe] = Field(default_factory=list)


BUILTIN_PROMPT_PACKS: dict[str, PromptPack] = {
    "customer_service_core_v1": PromptPack(
        name="customer_service_core_v1",
        recipes=[
            GenerationRecipe(format="factual_qa", languages=["english", "tagalog", "taglish"], num_examples=3),
            GenerationRecipe(format="troubleshooting", languages=["tagalog", "taglish"], num_examples=2),
            GenerationRecipe(format="multi_turn", languages=["taglish"], num_examples=1, max_turns=6),
        ],
    ),
    "all_formats_v1": PromptPack(
        name="all_formats_v1",
        recipes=[
            GenerationRecipe(format=name, languages=["english"], num_examples=1)
            for name in (
                "factual_qa", "transactional", "troubleshooting", "scenario_response", "multi_turn",
                "feedback_response", "conflict_resolution", "needs_recommendation", "cross_sell", "intent_response",
            )
        ],
    ),
}


def load_prompt_pack(name: str, search_root: Path | None = None) -> PromptPack:
    if search_root:
        path = search_root / "prompt_packs" / f"{name}.yaml"
        if path.exists():
            return PromptPack.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    try:
        return BUILTIN_PROMPT_PACKS[name]
    except KeyError as exc:
        raise ValueError(f"unknown prompt pack: {name}") from exc


def resolve_recipes(generation: GenerationConfig, *, search_root: Path | None = None) -> list[GenerationRecipe]:
    base = list(load_prompt_pack(generation.prompt_pack, search_root).recipes) if generation.prompt_pack else []
    explicit_by_format = {recipe.format: recipe for recipe in generation.recipes}
    resolved = [explicit_by_format.pop(recipe.format, recipe) for recipe in base]
    resolved.extend(explicit_by_format.values())
    return resolved


def list_prompt_packs(search_root: Path | None = None) -> list[str]:
    names = set(BUILTIN_PROMPT_PACKS)
    if search_root:
        directory = search_root / "prompt_packs"
        if directory.exists():
            names.update(path.stem for path in directory.glob("*.yaml"))
    return sorted(names)

