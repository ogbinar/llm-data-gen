from __future__ import annotations

import json
import re

from .models import GeneratedExample, ModelExample


def _decode_json_object(raw_text: str) -> dict:
    text = raw_text.strip()
    fence = re.escape(chr(96) * 3)
    fenced = re.fullmatch(rf"{fence}(?:json)?\s*(.*?)\s*{fence}", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("model output must be one JSON object")
    return data


def parse_generated_output(raw_text: str) -> GeneratedExample:
    return GeneratedExample.model_validate(_decode_json_object(raw_text))


def parse_model_output(raw_text: str) -> ModelExample:
    return ModelExample.model_validate(_decode_json_object(raw_text))

