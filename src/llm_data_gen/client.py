from __future__ import annotations

import os
from typing import Any

import httpx

from .config import BackendConfig, EndpointConfig


class OpenAICompatibleClient:
    def __init__(self, config: BackendConfig | EndpointConfig):
        self.config = config

    def generate(
        self,
        messages: list[dict[str, str]],
        parameters: dict[str, Any] | None = None,
    ) -> str:
        parameters = parameters or {}
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": parameters.get("temperature", getattr(self.config, "temperature", 0.2)),
        }
        top_p = parameters.get("top_p", getattr(self.config, "top_p", None))
        max_tokens = parameters.get("max_tokens", getattr(self.config, "max_tokens", None))
        if top_p is not None:
            payload["top_p"] = top_p
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        api_key = getattr(self.config, "api_key", None)
        api_key_env = getattr(self.config, "api_key_env", None)
        if not api_key and api_key_env:
            api_key = os.getenv(api_key_env)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        with httpx.Client(
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            headers=headers,
        ) as client:
            response = client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]


class StaticClient:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls: list[list[dict[str, str]]] = []
        self.parameters: list[dict[str, Any]] = []

    def generate(
        self,
        messages: list[dict[str, str]],
        parameters: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append(messages)
        self.parameters.append(parameters or {})
        if not self.responses:
            raise RuntimeError("No static responses left")
        return self.responses.pop(0)

