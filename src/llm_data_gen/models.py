from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PromptFamily(str, Enum):
    grounded_qa = "Grounded QA"
    scenario_response = "Scenario -> response"
    intent_response = "Intent -> response"
    multi_turn_dialogue = "Multi-turn dialogue"
    troubleshooting_resolution = "Troubleshooting / resolution"


class SourceDocument(BaseModel):
    source_id: str
    title: str
    text: str
    language: str
    language_origin: Literal["input_default", "record_field", "legacy_default"] = "input_default"
    doc_type: str = "document"
    source_kind: str = "local"
    source_format: str = "text"
    source_url: str | None = None
    provenance_note: str | None = None
    provenance_path: str
    source_checksum: str = ""
    domain: str = "general"
    metadata: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    source_id: str
    chunk_id: str
    chunk_index: int
    source_format: str
    text: str
    language: str
    language_origin: Literal["input_default", "record_field", "legacy_default"] = "input_default"
    token_estimate: int
    provenance_path: str
    title: str
    strategy: str = "recursive_text"
    strategy_version: str = "v1"
    source_checksum: str = ""
    start_offset: int | None = None
    end_offset: int | None = None
    domain: str = "general"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class GeneratedExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["generated", "not_applicable"] = "generated"
    qa_format: str
    messages: list[ChatMessage] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "GeneratedExample":
        if self.status == "generated" and not self.messages:
            raise ValueError("generated output requires messages")
        if self.status == "not_applicable" and not self.reason:
            raise ValueError("not_applicable output requires reason")
        return self


class GenerationJob(BaseModel):
    job_id: str
    chunk_id: str
    qa_format: str
    language: str
    language_origin: Literal["input_default", "record_field", "legacy_default"] = "input_default"
    prompt_name: str
    prompt_version: str
    sample_index: int
    recipe_hash: str
    max_turns: int | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class SFTRecord(BaseModel):
    schema_version: Literal["sft_chat_v1"] = "sft_chat_v1"
    example_id: str
    job_id: str
    source_id: str
    chunk_id: str
    domain: str
    qa_format: str
    language: str
    messages: list[ChatMessage]
    evidence: list[str]
    prompt_name: str
    prompt_version: str
    prompt_hash: str
    generator_model: str
    inference_backend: str
    endpoint_profile: str
    generation_parameters: dict[str, Any] = Field(default_factory=dict)
    generated_at: str
    validation_status: Literal["passed"] = "passed"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceFailure(BaseModel):
    source_path: str
    record_index: int | None = None
    failure_stage: Literal["source"]
    failure_reason: str


class RejectedRecord(BaseModel):
    job_id: str | None = None
    source_id: str | None = None
    chunk_id: str | None = None
    qa_format: str | None = None
    language: str | None = None
    failure_stage: str
    failure_reason: str
    raw_output: str | None = None
    duplicate_of: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    recorded_at: str


class CheckpointRecord(BaseModel):
    job_id: str
    outcome: Literal["accepted", "rejected", "not_applicable"]
    example_id: str | None = None
    reason: str | None = None
    recorded_at: str


class RunManifest(BaseModel):
    schema_version: Literal["run_manifest_v1"] = "run_manifest_v1"
    config_name: str
    config_hash: str
    run_status: Literal["running", "completed", "failed"]
    started_at: str
    completed_at: str | None = None
    resumed: bool = False
    endpoint_profile: str
    inference_backend: str
    generator_model: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    source_count: int = 0
    source_failure_count: int = 0
    chunk_count: int = 0
    requested_jobs: int = 0
    accepted_jobs: int = 0
    rejected_jobs: int = 0
    not_applicable_jobs: int = 0
    accepted_by_format: dict[str, int] = Field(default_factory=dict)
    accepted_by_language: dict[str, int] = Field(default_factory=dict)
    accepted_by_source: dict[str, int] = Field(default_factory=dict)
    failures_by_stage: dict[str, int] = Field(default_factory=dict)


class ModelExample(BaseModel):
    task_type: str
    input_text: str
    output_text: str
    evidence: str


class ParsedExample(BaseModel):
    example_id: str
    source_id: str
    chunk_id: str
    prompt_template_id: str
    task_type: str
    input_text: str
    output_text: str
    model_name: str
    backend_name: str
    backend_base_url: str
    prompt_version: str
    generated_at: str
    validation_status: str
    provenance: dict[str, str | int]
    validation_notes: str | None = None


class SkippedExample(BaseModel):
    example_id: str
    source_id: str
    chunk_id: str
    prompt_template_id: str
    task_type: str | None = None
    failure_stage: str
    failure_reason: str
    raw_output: str | None = None
    provenance: dict[str, str | int]
