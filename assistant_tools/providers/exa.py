from __future__ import annotations

from typing import Any

from assistant_tools.http import build_client
from assistant_tools.http import raise_for_error_response


EXA_BASE_URL: str = "https://api.exa.ai"


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _start_published_date(after_date: str) -> str:
    if "T" in after_date:
        return after_date
    return f"{after_date}T00:00:00.000Z"


def search(
    *,
    api_key: str,
    query: str,
    timeout_seconds: float,
    search_type: str,
    max_results: int,
    after_date: str | None,
    include_domains: list[str],
    highlights: bool,
    proxy: str | None,
) -> dict[str, Any]:
    """Return Exa URL results with optional excerpts, never full text or summaries."""
    payload: dict[str, Any] = {
        "query": query,
        "type": search_type,
        "numResults": max_results,
    }
    if highlights:
        payload["contents"] = {"highlights": True}
    if include_domains:
        payload["includeDomains"] = include_domains
    if after_date:
        payload["startPublishedDate"] = _start_published_date(after_date)

    with build_client(timeout_seconds, proxy) as client:
        response = client.post(
            f"{EXA_BASE_URL}/search",
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
    timeout_seconds: float,
    full_content: bool,
    max_chars_per_result: int,
    proxy: str | None,
) -> dict[str, Any]:
    """Extract only bounded text by default; full text requires an explicit request."""
    text: bool | dict[str, int]
    if full_content:
        text = True
    else:
        text = {"maxCharacters": max_chars_per_result}
    payload: dict[str, Any] = {"urls": urls, "text": text}

    with build_client(timeout_seconds, proxy) as client:
        response = client.post(
            f"{EXA_BASE_URL}/contents",
            headers=_headers(api_key),
            json=payload,
        )
        raise_for_error_response(response)
        parsed: dict[str, Any] = response.json()
        return parsed
