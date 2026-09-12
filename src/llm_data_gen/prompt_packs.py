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
    "customer_service_core_v2": PromptPack(
        version=2,
        name="customer_service_core_v2",
        recipes=[
            GenerationRecipe(format="factual_qa", num_examples=3),
            GenerationRecipe(format="troubleshooting", num_examples=2),
            GenerationRecipe(format="multi_turn", num_examples=1, max_turns=6),
        ],
    ),
    "all_formats_v2": PromptPack(
        version=2,
        name="all_formats_v2",
        recipes=[
            GenerationRecipe(format=name, num_examples=1)
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
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            recipes = payload.get("recipes") if isinstance(payload, dict) else []
            if any(isinstance(recipe, dict) and "languages" in recipe for recipe in recipes or []):
                raise ValueError(
                    "prompt-pack languages was removed in V2; declare input.language instead"
                )
            pack = PromptPack.model_validate(payload)
            if pack.version != 2:
                raise ValueError(f"incompatible prompt-pack version {pack.version!r}; migrate to version 2")
            return pack
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
            for path in directory.glob("*.yaml"):
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                recipes = payload.get("recipes") if isinstance(payload, dict) else None
                if isinstance(payload, dict) and payload.get("version") == 2 and not any(
                    isinstance(recipe, dict) and "languages" in recipe
                    for recipe in (recipes or [])
                ):
                    names.add(path.stem)
    return sorted(names)
