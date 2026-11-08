"""Thin Bedrock Converse helper for Dispatch agents."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL_ID = "us.meta.llama4-scout-17b-instruct-v1:0"


def bedrock_ready() -> bool:
    if not (
        os.getenv("AWS_ACCESS_KEY_ID")
        and os.getenv("AWS_SECRET_ACCESS_KEY")
        and os.getenv("AWS_SESSION_TOKEN")
    ):
        return False
    try:
        import boto3  # noqa: F401
    except ImportError:
        return False
    return True


def _client():
    import boto3

    return boto3.client(
        service_name="bedrock-runtime",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )


def _extract_text(response: dict[str, Any]) -> str:
    blocks = response["output"]["message"]["content"]
    texts: list[str] = []
    for block in blocks:
        if block.get("text"):
            texts.append(block["text"])
    return "\n".join(texts).strip()


def _parse_json_payload(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", cleaned, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def converse_json(
    *,
    prompt: str,
    system: str | None = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
) -> Any:
    """Send a prompt to Bedrock and parse a JSON object/array response."""
    if not bedrock_ready():
        raise RuntimeError("AWS Bedrock credentials are not set")

    model_id = os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    messages = [
        {
            "role": "user",
            "content": [{"text": prompt}],
        }
    ]
    kwargs: dict[str, Any] = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": {
            "maxTokens": max_tokens,
            "temperature": temperature,
            "topP": 0.9,
        },
    }
    if system:
        kwargs["system"] = [{"text": system}]

    response = _client().converse(**kwargs)
    text = _extract_text(response)
    return _parse_json_payload(text)
