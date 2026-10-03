from __future__ import annotations

from typing import Any

import pytest

from assistant_tools.cli import _provider_chain
from assistant_tools.cli import _run_with_fallback
from assistant_tools.cli import build_parser
from assistant_tools.providers import keenable
from assistant_tools.utils import AssistantToolsError


class Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    @property
    def is_success(self) -> bool:
        return True

    def json(self) -> dict[str, Any]:
        return self.payload


class Client:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.requests: list[dict[str, Any]] = []

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> Response:
        self.requests.append({"method": "post", "url": url, "headers": headers, "json": json})
        return Response(self.payload)

    def get(
        self, url: str, *, headers: dict[str, str], params: dict[str, str | int]
    ) -> Response:
        self.requests.append(
            {"method": "get", "url": url, "headers": headers, "params": params}
        )
        return Response(self.payload)


def test_cli_exposes_keenable_provider() -> None:
    search_args = build_parser().parse_args(["search", "Keenable", "--provider", "keenable"])
    extract_args = build_parser().parse_args(
        ["extract", "https://docs.keenable.ai", "--provider", "keenable"]
    )

    assert search_args.provider == "keenable"
    assert extract_args.provider == "keenable"


def test_search_uses_bounded_keenable_request(monkeypatch: Any) -> None:
    client = Client({"results": []})
    monkeypatch.setattr(keenable, "build_client", lambda *_: client)

    result = keenable.search(
        api_key="secret",
        query="Keenable API contract",
        timeout_seconds=10,
        max_results=5,
        after_date="2026-01-01",
        include_domains=["docs.keenable.ai"],
        max_chars_per_result=4000,
        proxy=None,
    )

    assert result == {"results": []}
    assert client.requests == [
        {
            "method": "post",
            "url": "https://api.keenable.ai/v1/search",
            "headers": {"X-API-Key": "secret", "Content-Type": "application/json"},
            "json": {
                "query": "Keenable API contract",
                "max_results": 5,
                "snippet_max_length": 4000,
                "site": "docs.keenable.ai",
                "published_after": "2026-01-01",
            },
        }
    ]


def test_search_rejects_multiple_site_filters() -> None:
    with pytest.raises(AssistantToolsError, match="at most one"):
        keenable.search(
            api_key="secret",
            query="Keenable API contract",
            timeout_seconds=10,
            max_results=5,
            after_date=None,
            include_domains=["docs.keenable.ai", "keenable.ai"],
            max_chars_per_result=4000,
            proxy=None,
        )


def test_extract_uses_bounded_fetch_request(monkeypatch: Any) -> None:
    client = Client({"content": "markdown"})
    monkeypatch.setattr(keenable, "build_client", lambda *_: client)

    result = keenable.extract(
        api_key="secret",
        urls=["https://docs.keenable.ai/api-reference/fetch"],
        objective="List limits",
        timeout_seconds=10,
        full_content=False,
        max_chars_per_result=5000,
        proxy=None,
    )

    assert result == {"results": [{"content": "markdown"}]}
    assert client.requests == [
        {
            "method": "get",
            "url": "https://api.keenable.ai/v1/fetch",
            "headers": {"X-API-Key": "secret", "Content-Type": "application/json"},
            "params": {
                "url": "https://docs.keenable.ai/api-reference/fetch",
                "max_chars": 5000,
                "instruction": "List limits",
            },
        }
    ]


def test_configured_provider_chain_deduplicates_and_override_forces_one() -> None:
    assert _provider_chain("keenable", ["exa", "parallel", "exa"], None) == [
        "keenable",
        "exa",
        "parallel",
    ]
    assert _provider_chain("keenable", ["exa", "parallel"], "exa") == ["exa"]


def test_retryable_failure_advances_to_next_provider() -> None:
    calls: list[str] = []

    def invoke(provider: str) -> dict[str, Any]:
        calls.append(provider)
        if provider == "keenable":
            raise AssistantToolsError("rate limited", error_type="http_error", status_code=429)
        return {"results": []}

    provider, result, attempted = _run_with_fallback(["keenable", "exa"], invoke)

    assert provider == "exa"
    assert result == {"results": []}
    assert attempted == ["keenable", "exa"]
    assert calls == ["keenable", "exa"]


def test_terminal_failure_does_not_egress_to_fallback() -> None:
    calls: list[str] = []

    def invoke(provider: str) -> dict[str, Any]:
        calls.append(provider)
        raise AssistantToolsError("invalid key", error_type="http_error", status_code=401)

    with pytest.raises(AssistantToolsError, match="invalid key"):
        _run_with_fallback(["keenable", "exa"], invoke)

    assert calls == ["keenable"]
