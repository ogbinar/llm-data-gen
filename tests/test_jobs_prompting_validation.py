import json

import pytest

from llm_data_gen.chunking import chunk_document
from llm_data_gen.config import ChunkingConfig, GenerationRecipe, ValidationConfig
from llm_data_gen.jobs import expand_jobs
from llm_data_gen.models import GeneratedExample, SourceDocument
from llm_data_gen.parsing import parse_generated_output
from llm_data_gen.prompting import compose_prompt
from llm_data_gen.validation import normalized_message_signature, validate_generated_example


def make_chunk():
    document = SourceDocument(
        source_id="doc",
        title="Example",
        text="The service ID is 12345. Restart the modem before contacting support.",
        language="english",
        provenance_path="/tmp/doc.txt",
    )
    return chunk_document(document, ChunkingConfig(strategy="document"))[0]


def test_job_expansion_is_deterministic_and_not_multiplied_by_language():
    chunk = make_chunk()
    recipes = [
        GenerationRecipe(
            format="factual_qa",
            num_examples=2,
        )
    ]
    first = expand_jobs([chunk], recipes)
    second = expand_jobs([chunk], recipes)
    assert len(first) == 2
    assert [job.job_id for job in first] == [job.job_id for job in second]
    assert {job.language for job in first} == {"english"}


def test_recipe_setting_changes_job_identity():
    chunk = make_chunk()
    first = expand_jobs(
        [chunk],
        [GenerationRecipe(format="multi_turn", max_turns=4)],
    )[0]
    second = expand_jobs(
        [chunk],
        [GenerationRecipe(format="multi_turn", max_turns=6)],
    )[0]
    assert first.job_id != second.job_id


def test_source_language_changes_job_identity_without_multiplying_jobs():
    english = make_chunk()
    tagalog = english.model_copy(update={"language": "tagalog"})
    recipe = GenerationRecipe(format="factual_qa", num_examples=2)
    english_jobs = expand_jobs([english], [recipe])
    tagalog_jobs = expand_jobs([tagalog], [recipe])
    assert len(english_jobs) == len(tagalog_jobs) == 2
    assert [job.job_id for job in english_jobs] != [job.job_id for job in tagalog_jobs]
    assert {job.language for job in tagalog_jobs} == {"tagalog"}


def test_prompt_preserves_source_language_and_model_does_not_label_it():
    chunk = make_chunk()
    job = expand_jobs(
        [chunk],
        [GenerationRecipe(format="troubleshooting")],
    )[0]
    messages, prompt_hash = compose_prompt(chunk, job)
    prompt = messages[1]["content"]
    assert "troubleshooting" in prompt
    assert "Declared source language: english" in prompt
    assert "Never translate" in prompt
    assert "Do not introduce code-switching or remove code-switching" in prompt
    assert "Preserve every character and word exactly" in prompt
    assert '"language"' not in prompt
    assert chunk.text in prompt
    assert '"messages"' in prompt
    assert prompt_hash.startswith("sha256:")


def test_sample_slots_receive_distinct_prompts():
    chunk = make_chunk()
    jobs = expand_jobs(
        [chunk], [GenerationRecipe(format="factual_qa", num_examples=2)]
    )
    first, first_hash = compose_prompt(chunk, jobs[0])
    second, second_hash = compose_prompt(chunk, jobs[1])
    assert "variation slot 1" in first[1]["content"]
    assert "variation slot 2" in second[1]["content"]
    assert first_hash != second_hash


def test_parse_and_validate_multi_turn():
    chunk = make_chunk()
    job = expand_jobs(
        [chunk],
        [GenerationRecipe(format="multi_turn", max_turns=4)],
    )[0]
    payload = {
        "status": "generated",
        "qa_format": "multi_turn",
        "messages": [
            {"role": "user", "content": "My service is unavailable."},
            {"role": "assistant", "content": "Have you restarted the modem?"},
            {"role": "user", "content": "Not yet."},
            {"role": "assistant", "content": "Restart the modem before contacting support."},
        ],
        "evidence": ["Restart the modem before contacting support."],
        "metadata": {},
    }
    example = parse_generated_output(json.dumps(payload))
    assert validate_generated_example(example, job, chunk, ValidationConfig()) == []
    assert normalized_message_signature(example).startswith("sha256:")


def test_validation_rejects_roles_format_provenance_and_evidence():
    chunk = make_chunk()
    job = expand_jobs(
        [chunk],
        [GenerationRecipe(format="factual_qa")],
    )[0]
    job.language = "taglish"
    example = GeneratedExample(
        qa_format="troubleshooting",
        messages=[
            {"role": "assistant", "content": "Answer"},
            {"role": "assistant", "content": "Again"},
        ],
        evidence=["invented"],
    )
    errors = validate_generated_example(example, job, chunk, ValidationConfig())
    assert any("format mismatch" in error for error in errors)
    assert any("language provenance mismatch" in error for error in errors)
    assert any("must alternate" in error for error in errors)
    assert any("evidence not found" in error for error in errors)


def test_not_applicable_requires_reason():
    parsed = parse_generated_output(
        json.dumps(
            {
                "status": "not_applicable",
                "qa_format": "cross_sell",
                "reason": "No complementary product is present.",
            }
        )
    )
    assert parsed.status == "not_applicable"


@pytest.mark.parametrize(
    "qa_format",
    [
        "factual_qa",
        "transactional",
        "troubleshooting",
        "scenario_response",
        "multi_turn",
        "feedback_response",
        "conflict_resolution",
        "needs_recommendation",
        "cross_sell",
        "intent_response",
    ],
)
def test_every_enabled_format_composes_and_validates_structurally(qa_format):
    chunk = make_chunk()
    recipe = GenerationRecipe(
        format=qa_format,
        max_turns=4 if qa_format == "multi_turn" else None,
    )
    job = expand_jobs([chunk], [recipe])[0]
    prompt, _ = compose_prompt(chunk, job)
    assert qa_format in prompt[1]["content"]
    assert "status=not_applicable" in prompt[1]["content"]

    messages = [
        {"role": "user", "content": "What should I do?"},
        {"role": "assistant", "content": "Restart the modem before contacting support."},
    ]
    if qa_format == "multi_turn":
        messages = [
            {"role": "user", "content": "My connection is unavailable."},
            {"role": "assistant", "content": "Have you restarted the modem?"},
            {"role": "user", "content": "Not yet."},
            {"role": "assistant", "content": "Restart the modem before contacting support."},
        ]
    metadata = {"intent": "technical_support"} if qa_format == "intent_response" else {}
    example = GeneratedExample(
        qa_format=qa_format,
        messages=messages,
        evidence=["Restart the modem before contacting support."],
        metadata=metadata,
    )
    assert validate_generated_example(example, job, chunk, ValidationConfig()) == []


def test_model_returned_language_label_is_rejected():
    payload = {
        "status": "generated",
        "qa_format": "factual_qa",
        "language": "taglish",
        "messages": [
            {"role": "user", "content": "Question?"},
            {"role": "assistant", "content": "Answer."},
        ],
        "evidence": ["The service ID is 12345."],
    }
    with pytest.raises(Exception, match="extra"):
        parse_generated_output(json.dumps(payload))
