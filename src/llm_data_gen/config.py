from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackendConfig(BaseModel):
    name: str = "llama-swap"
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "qwen38-27b-chat-rocmfp4"
    api_key: str | None = None
    timeout_seconds: float = 120.0


class AppConfig(BaseModel):
    input_path: Path
    output_path: Path = Path("output/generated_examples.jsonl")
    source_id: str | None = None
    source_language: str = "english"
    chunk_size: int = 1200
    backend: BackendConfig = Field(default_factory=BackendConfig)
    resume: bool = True

    @model_validator(mode="after")
    def validate_source_language(self) -> "AppConfig":
        from .languages import resolve_language

        self.source_language = resolve_language(self.source_language).name
        return self


class InputConfig(StrictModel):
    path: Path
    language: str
    language_field: str | None = None
    format: Literal["auto", "txt", "md", "json", "jsonl", "csv", "parquet"] = "auto"
    recursive: bool = True
    id_field: str = "source_id"
    text_field: str = "text"
    title_field: str = "title"
    metadata_fields: list[str] | None = None
    domain: str = "general"

    @model_validator(mode="after")
    def validate_language(self) -> "InputConfig":
        from .languages import resolve_language

        self.language = resolve_language(self.language).name
        if self.language_field is not None:
            self.language_field = self.language_field.strip()
            if not self.language_field:
                raise ValueError("language_field must not be blank")
        if self.language_field and self.format in {"txt", "md"}:
            raise ValueError("language_field is only supported for structured inputs")
        return self


class ChunkingConfig(StrictModel):
    strategy: Literal[
        "document",
        "recursive_text",
        "markdown_sections",
        "fixed_tokens",
    ] = "recursive_text"
    chunk_size: int = Field(default=1200, gt=0)
    chunk_overlap: int = Field(default=0, ge=0)
    unit: Literal["characters", "tokens"] = "characters"
    version: str = "v1"

    @model_validator(mode="after")
    def validate_overlap(self) -> "ChunkingConfig":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if self.strategy == "fixed_tokens" and self.unit != "tokens":
            raise ValueError("fixed_tokens requires unit=tokens")
        return self


class EndpointConfig(StrictModel):
    profile: str = "local-qwen"
    backend: str = "llama-swap"
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "qwen38-27b-chat-rocmfp4"
    api_key_env: str | None = "OPENAI_API_KEY"
    timeout_seconds: float = Field(default=120.0, gt=0)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, gt=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, gt=0)


class GenerationRecipe(StrictModel):
    format: str
    num_examples: int = Field(default=1, gt=0)
    max_turns: int | None = Field(default=None, ge=2)
    settings: dict[str, Any] = Field(default_factory=dict)


class GenerationConfig(StrictModel):
    prompt_pack: str | None = None
    recipes: list[GenerationRecipe] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source(self) -> "GenerationConfig":
        if not self.prompt_pack and not self.recipes:
            raise ValueError("generation requires prompt_pack or recipes")
        return self


class ValidationConfig(StrictModel):
    grounding: Literal["exact_evidence", "none"] = "exact_evidence"
    reject_duplicates: bool = True
    require_assistant_final_turn: bool = True


class OutputConfig(StrictModel):
    directory: Path
    format: Literal["jsonl"] = "jsonl"
    resume: bool = True
    include_chunk_text: bool = False


class RunConfig(StrictModel):
    version: Literal[2] = 2
    name: str
    input: InputConfig
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    endpoint: EndpointConfig = Field(default_factory=EndpointConfig)
    generation: GenerationConfig
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    output: OutputConfig


ENDPOINT_PROFILES: dict[str, dict[str, Any]] = {
    "local-qwen": {
        "backend": "llama-swap",
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "qwen38-27b-chat-rocmfp4",
        "api_key_env": "OPENAI_API_KEY",
    },
    "llama-swap-local": {
        "backend": "llama-swap",
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "qwen38-27b-chat-rocmfp4",
        "api_key_env": "OPENAI_API_KEY",
    },
    "generic-openai-compatible": {
        "backend": "openai-compatible",
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "local-model",
        "api_key_env": "OPENAI_API_KEY",
    },
}


def load_run_config(path: Path) -> RunConfig:
    config_path = path.resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("run configuration must be a YAML object")

    version = raw.get("version")
    generation = raw.get("generation") or {}
    recipes = generation.get("recipes") or [] if isinstance(generation, dict) else []
    legacy_languages = any(
        isinstance(recipe, dict) and "languages" in recipe for recipe in recipes
    )
    if version != 2 or legacy_languages:
        detail = (
            "generation.recipes[*].languages was removed; " if legacy_languages else ""
        )
        raise ValueError(
            f"incompatible run configuration version {version!r}; {detail}"
            "migrate to version: 2, set input.language (and optional input.language_field) "
            "instead. This pipeline does not translate source content."
        )

    endpoint = raw.get("endpoint") or {}
    if not isinstance(endpoint, dict):
        raise ValueError("endpoint must be an object")
    profile_name = str(endpoint.get("profile") or "local-qwen")
    if profile_name not in ENDPOINT_PROFILES:
        raise ValueError(f"unknown endpoint profile: {profile_name}")
    raw["endpoint"] = {**ENDPOINT_PROFILES[profile_name], **endpoint, "profile": profile_name}

    config = RunConfig.model_validate(raw)
    base = config_path.parent
    source_format = config.input.format
    if source_format == "auto" and config.input.path.suffix:
        source_format = config.input.path.suffix.lower().lstrip(".")
    if config.input.language_field and source_format in {"txt", "md"}:
        raise ValueError("input.language_field is only supported for structured inputs")
    return config.model_copy(
        update={
            "input": config.input.model_copy(update={"path": _resolve_path(config.input.path, base)}),
            "output": config.output.model_copy(
                update={"directory": _resolve_path(config.output.directory, base)}
            ),
        }
    )


def redacted_config_dict(config: RunConfig) -> dict[str, Any]:
    payload = config.model_dump(mode="json")
    payload["endpoint"]["api_key_env"] = config.endpoint.api_key_env
    return payload


def config_hash(config: RunConfig, recipes: list[GenerationRecipe] | None = None) -> str:
    payload = redacted_config_dict(config)
    if recipes is not None:
        from .formats import resolve_format

        payload["_resolved_generation_contract"] = [
            {
                "recipe": recipe.model_dump(mode="json"),
                "prompt_version": resolve_format(recipe.format).prompt_version,
            }
            for recipe in recipes
        ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_path(path: Path, base: Path) -> Path:
    return path if path.is_absolute() else (base / path).resolve()
