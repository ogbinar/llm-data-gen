from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from llm_data_gen.config import (
    ChunkingConfig,
    GenerationConfig,
    GenerationRecipe,
    config_hash,
    load_run_config,
)
from llm_data_gen.formats import FORMAT_REGISTRY, resolve_format
from llm_data_gen.languages import LANGUAGE_REGISTRY
from llm_data_gen.prompt_packs import resolve_recipes
from llm_data_gen.prompt_packs import load_prompt_pack
from llm_data_gen.pipeline import validate_config_semantics


def write_config(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "run.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def base_payload(tmp_path: Path) -> dict:
    return {
        "version": 2,
        "name": "test",
        "input": {"path": "corpus.txt", "language": "english"},
        "generation": {
            "recipes": [{"format": "factual_qa", "num_examples": 1}]
        },
        "output": {"directory": "output"},
    }


def test_config_loads_resolves_paths_and_hashes_stably(tmp_path):
    payload = base_payload(tmp_path)
    path = write_config(tmp_path, payload)
    config = load_run_config(path)
    assert config.input.path == (tmp_path / "corpus.txt").resolve()
    assert config.output.directory == (tmp_path / "output").resolve()
    assert config_hash(config) == config_hash(load_run_config(path))


def test_config_rejects_unknown_fields_and_bad_overlap(tmp_path):
    payload = base_payload(tmp_path)
    payload["surprise"] = True
    with pytest.raises(ValidationError):
        load_run_config(write_config(tmp_path, payload))
    with pytest.raises(ValidationError):
        ChunkingConfig(chunk_size=100, chunk_overlap=100)


def test_unknown_endpoint_profile_is_rejected(tmp_path):
    payload = base_payload(tmp_path)
    payload["endpoint"] = {"profile": "missing"}
    with pytest.raises(ValueError, match="unknown endpoint"):
        load_run_config(write_config(tmp_path, payload))


def test_prompt_pack_recipe_override_replaces_matching_format():
    config = GenerationConfig(
        prompt_pack="customer_service_core_v2",
        recipes=[
            GenerationRecipe(
                format="factual_qa",
                num_examples=7,
            )
        ],
    )
    recipes = resolve_recipes(config)
    factual = next(recipe for recipe in recipes if recipe.format == "factual_qa")
    assert factual.num_examples == 7
    assert len(recipes) == 3


def test_all_formats_and_languages_are_registered():
    assert len(FORMAT_REGISTRY) == 10
    assert set(LANGUAGE_REGISTRY) == {"english", "filipino", "tagalog", "taglish"}
    assert resolve_format("Grounded QA").name == "factual_qa"


def test_recipe_rejects_format_specific_settings_on_wrong_format(tmp_path):
    payload = base_payload(tmp_path)
    payload["generation"]["recipes"][0]["max_turns"] = 4
    config = load_run_config(write_config(tmp_path, payload))
    with pytest.raises(ValueError, match="only supported by multi_turn"):
        validate_config_semantics(config)


def test_v1_and_legacy_language_axis_fail_with_migration_guidance(tmp_path):
    payload = base_payload(tmp_path)
    payload["version"] = 1
    payload["generation"]["recipes"][0]["languages"] = ["taglish"]
    with pytest.raises(ValueError, match=r"input\.language.*does not translate"):
        load_run_config(write_config(tmp_path, payload))


def test_input_language_is_required_and_language_field_is_structured_only(tmp_path):
    payload = base_payload(tmp_path)
    del payload["input"]["language"]
    with pytest.raises(ValidationError):
        load_run_config(write_config(tmp_path, payload))

    payload = base_payload(tmp_path)
    payload["input"]["language_field"] = "language"
    with pytest.raises(ValueError, match="structured inputs"):
        load_run_config(write_config(tmp_path, payload))


def test_historical_target_language_pack_fails_loudly():
    with pytest.raises(ValueError, match="languages was removed"):
        load_prompt_pack("customer_service_core_v1", Path("configs"))
