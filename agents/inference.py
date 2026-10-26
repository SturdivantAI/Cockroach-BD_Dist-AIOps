"""Provider selection for agent text generation; embeddings remain independent."""

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from agents.config import settings

logger = logging.getLogger(__name__)
NEBIUS_COMPLETIONS_URL = "https://api.tokenfactory.nebius.com/v1/chat/completions"


class InferenceError(RuntimeError):
    """Inference failed or returned an unusable completion."""


@dataclass(frozen=True)
class InferenceResult:
    text: str
    provider: str
    model: str
    elapsed_ms: int
    usage: dict[str, Any]
    finish_reason: str


def _decode_nebius_response(payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Validate completion structure without exposing response content in errors."""
    try:
        choice = payload["choices"][0]
        reason = choice["finish_reason"]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise InferenceError("Nebius returned an invalid completion structure") from None
    if reason != "stop":
        raise InferenceError("Nebius completion did not finish normally")
    if not isinstance(content, str) or not content.strip():
        raise InferenceError("Nebius returned empty text")
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return content.strip(), reason, usage


def generate(
    prompt: str,
    system: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> InferenceResult:
    """Generate text with an explicitly selected provider; never silently fall back."""
    if not prompt.strip() or max_tokens < 1:
        raise ValueError("A nonempty prompt and positive token limit are required")
    started = time.perf_counter()
    provider = settings.inference_provider
    if provider == "bedrock":
        from agents.bedrock_client import bedrock

        text = bedrock.invoke_claude(
            prompt=prompt, system=system, max_tokens=max_tokens, temperature=temperature
        )
        model = settings.bedrock_model_id
        # The legacy adapter returns text only. Do not invent usage or stop metadata.
        usage: dict[str, Any] = {}
        reason = "unreported"
    else:
        key = settings.nebius_api_key.get_secret_value().strip()
        if not key:
            raise InferenceError("NEBIUS_API_KEY must be configured")
        model = settings.nebius_model_id
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            with httpx.Client(timeout=settings.inference_timeout_seconds) as client:
                response = client.post(
                    NEBIUS_COMPLETIONS_URL,
                    headers={"Authorization": "Bearer " + key},
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise InferenceError(f"Nebius HTTP status {exc.response.status_code}") from None
        except httpx.RequestError:
            raise InferenceError("Nebius connection or timeout failure") from None
        except ValueError:
            raise InferenceError("Nebius returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise InferenceError("Nebius returned an invalid response object")
        text, reason, usage = _decode_nebius_response(payload)
    if not isinstance(text, str) or not text.strip():
        raise InferenceError("Inference returned empty text")
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "inference provider=%s model=%s elapsed_ms=%d usage=%s finish_reason=%s",
        provider, model, elapsed_ms, usage, reason,
    )
    return InferenceResult(text, provider, model, elapsed_ms, usage, reason)


def invoke_text(
    prompt: str, system: str = "", max_tokens: int = 4096, temperature: float = 0.3
) -> str:
    """Text-only interface for existing agents."""
    return generate(prompt, system, max_tokens, temperature).text
