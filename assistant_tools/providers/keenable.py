from __future__ import annotations

from typing import Any

from assistant_tools.http import build_client
from assistant_tools.http import raise_for_error_response
from assistant_tools.utils import AssistantToolsError


KEENABLE_BASE_URL: str = "https://api.keenable.ai"
MAX_FULL_CONTENT_CHARS: int = 50_000


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key, "Content-Type": "application/json"}


def search(
    *,
    api_key: str,
    query: str,
    timeout_seconds: float,
    max_results: int,
    after_date: str | None,
    include_domains: list[str],
    max_chars_per_result: int,
    proxy: str | None,
) -> dict[str, Any]:
    """Return bounded Keenable results with their provider-native excerpts."""
    if len(include_domains) > 1:
        raise AssistantToolsError(
            "Keenable search accepts at most one --domain filter",
            error_type="invalid_request",
            exit_code=2,
        )
    payload: dict[str, Any] = {
        "query": query,
        "max_results": max_results,
        "snippet_max_length": max_chars_per_result,
    }
    if include_domains:
        payload["site"] = include_domains[0]
    if after_date:
        payload["published_after"] = after_date

    with build_client(timeout_seconds, proxy) as client:
        response = client.post(
            f"{KEENABLE_BASE_URL}/v1/search",
            headers=_headers(api_key),
            json=payload,
        )
        raise_for_error_response(response)
        parsed: dict[str, Any] = response.json()
        return parsed


def extract(
    *,
    api_key: str,
    urls: list[str],
    objective: str | None,
    timeout_seconds: float,
    full_content: bool,
    max_chars_per_result: int,
    proxy: str | None,
) -> dict[str, Any]:
    """Fetch bounded clean markdown, once per URL, from Keenable."""
    max_chars: int = MAX_FULL_CONTENT_CHARS if full_content else max_chars_per_result
    results: list[dict[str, Any]] = []
    with build_client(timeout_seconds, proxy) as client:
        for url in urls:
            params: dict[str, str | int] = {"url": url, "max_chars": max_chars}
            if objective:
                params["instruction"] = objective
            response = client.get(
                f"{KEENABLE_BASE_URL}/v1/fetch",
                headers=_headers(api_key),
                params=params,
            )
            raise_for_error_response(response)
            parsed: dict[str, Any] = response.json()
            results.append(parsed)
    return {"results": results}
