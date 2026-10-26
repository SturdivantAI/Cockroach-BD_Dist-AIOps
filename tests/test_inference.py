"""Offline checks for provider routing and unusable completion handling."""

import httpx
import pytest
from pydantic import SecretStr

from agents import inference


@pytest.mark.parametrize("reason,content", [("length", "partial"), ("stop", ""), ("content_filter", "")])
def test_unusable_completions_fail(reason: str, content: str) -> None:
    with pytest.raises(inference.InferenceError):
        inference._decode_nebius_response({
            "choices": [{"finish_reason": reason, "message": {"content": content}}]
        })


def test_nebius_routing_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inference.settings, "inference_provider", "nebius")
    monkeypatch.setattr(inference.settings, "nebius_api_key", SecretStr("test-only"))

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == inference.NEBIUS_COMPLETIONS_URL
        assert request.headers["Authorization"] == "Bearer test-only"
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            "usage": {"total_tokens": 25},
        })

    real_client = httpx.Client
    monkeypatch.setattr(inference.httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs
    ))
    result = inference.generate("Return JSON", max_tokens=512)
    assert result.text == '{"ok":true}'
    assert result.provider == "nebius"
    assert result.usage["total_tokens"] == 25


def test_missing_key_fails_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inference.settings, "inference_provider", "nebius")
    monkeypatch.setattr(inference.settings, "nebius_api_key", SecretStr(""))
    with pytest.raises(inference.InferenceError, match="NEBIUS_API_KEY"):
        inference.generate("test")
