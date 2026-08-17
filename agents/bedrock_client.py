"""AWS Bedrock client for LLM inference and embeddings."""

import json
from typing import Any

import boto3
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.config import settings


class BedrockClient:
    """Client for AWS Bedrock model invocations with retry logic."""

    def __init__(self) -> None:
        self._client = boto3.client("bedrock-runtime", region_name=settings.aws_region)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    def invoke_claude(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """Invoke Claude model and return the text response."""
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]

        body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            body["system"] = system

        response = self._client.invoke_model(
            modelId=settings.bedrock_model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )

        result = json.loads(response["body"].read())
        return result["content"][0]["text"]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    def generate_embedding(self, text: str) -> list[float]:
        """Generate a text embedding using Titan Embeddings v2."""
        body = json.dumps({
            "inputText": text,
            "dimensions": 1024,
            "normalize": True,
        })

        response = self._client.invoke_model(
            modelId=settings.bedrock_embedding_model_id,
            contentType="application/json",
            accept="application/json",
            body=body,
        )

        result = json.loads(response["body"].read())
        return result["embedding"]

    def generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        return [self.generate_embedding(text) for text in texts]


# Singleton client
bedrock = BedrockClient()
