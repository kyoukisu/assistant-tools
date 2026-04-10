from __future__ import annotations

from typing import Any

from assistant_tools.http import build_client
from assistant_tools.http import raise_for_error_response


PARALLEL_BASE_URL: str = "https://api.parallel.ai"


def search(
    *,
    api_key: str,
    objective: str,
    timeout_seconds: float,
    mode: str,
    max_results: int,
    after_date: str | None,
    include_domains: list[str],
    max_chars_per_result: int,
    max_chars_total: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "objective": objective,
        "search_queries": [objective],
        "mode": mode,
        "max_results": max_results,
        "excerpts": {
            "max_chars_per_result": max_chars_per_result,
            "max_chars_total": max_chars_total,
        },
    }
    source_policy: dict[str, Any] = {}
    if include_domains:
        source_policy["include_domains"] = include_domains
    if after_date:
        source_policy["after_date"] = after_date
    if source_policy:
        payload["source_policy"] = source_policy

    with build_client(timeout_seconds) as client:
        response = client.post(
            f"{PARALLEL_BASE_URL}/v1beta/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
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
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "urls": urls,
        "full_content": {"max_chars_per_result": max_chars_per_result} if full_content else False,
        "excerpts": False if full_content else {"max_chars_per_result": max_chars_per_result},
    }
    if objective:
        payload["objective"] = objective

    with build_client(timeout_seconds) as client:
        response = client.post(
            f"{PARALLEL_BASE_URL}/v1beta/extract",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
        raise_for_error_response(response)
        parsed: dict[str, Any] = response.json()
        return parsed
