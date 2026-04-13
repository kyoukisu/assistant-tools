from __future__ import annotations

from typing import Any

from assistant_tools.http import build_client
from assistant_tools.http import raise_for_error_response


def embeddings(
    *,
    api_key: str,
    model: str,
    inputs: list[str],
    timeout_seconds: float,
    proxy: str | None,
) -> list[list[float]]:
    """OpenAI-compatible embeddings via DeepInfra.

    Endpoint:
      https://api.deepinfra.com/v1/openai/embeddings

    Returns a list of embedding vectors in the same order as inputs.
    """

    with build_client(timeout_seconds, proxy) as client:
        resp = client.post(
            "https://api.deepinfra.com/v1/openai/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "input": inputs, "encoding_format": "float"},
        )
        raise_for_error_response(resp)
        data: dict[str, Any] = resp.json()
        items: list[dict[str, Any]] = data["data"]
        if items and all("index" in item for item in items):
            items = sorted(items, key=lambda item: int(item["index"]))
        return [item["embedding"] for item in items]
