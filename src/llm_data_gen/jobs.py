from __future__ import annotations

import hashlib
import json

from .config import GenerationRecipe
from .formats import resolve_format
from .models import Chunk, GenerationJob


def expand_jobs(chunks: list[Chunk], recipes: list[GenerationRecipe]) -> list[GenerationJob]:
    jobs: list[GenerationJob] = []
    for recipe in recipes:
        definition = resolve_format(recipe.format)
        recipe_hash = _recipe_hash(recipe, definition.prompt_version)
        for chunk in chunks:
            for sample_index in range(1, recipe.num_examples + 1):
                identity = {
                    "chunk_id": chunk.chunk_id,
                    "source_language": chunk.language,
                    "qa_format": definition.name,
                    "prompt_version": definition.prompt_version,
                    "sample_index": sample_index,
                    "recipe_hash": recipe_hash,
                }
                job_id = hashlib.sha256(
                    json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                jobs.append(
                    GenerationJob(
                        job_id=f"sha256:{job_id}",
                        chunk_id=chunk.chunk_id,
                        qa_format=definition.name,
                        language=chunk.language,
                        language_origin=chunk.language_origin,
                        prompt_name=definition.name,
                        prompt_version=definition.prompt_version,
                        sample_index=sample_index,
                        recipe_hash=recipe_hash,
                        max_turns=recipe.max_turns,
                        settings=recipe.settings,
                    )
                )
    return jobs


def _recipe_hash(recipe: GenerationRecipe, prompt_version: str) -> str:
    payload = {**recipe.model_dump(mode="json"), "prompt_version": prompt_version}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest[:16]
