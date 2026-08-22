from __future__ import annotations

from typing import Any

from assistant_tools.cli import _exa_search_type
from assistant_tools.cli import build_parser
from assistant_tools.providers import exa


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
        self.requests.append({"url": url, "headers": headers, "json": json})
        return Response(self.payload)


def test_legacy_agentic_mode_does_not_enable_exa_deep_search() -> None:
    assert _exa_search_type("agentic", "auto") == "auto"
    assert _exa_search_type("one-shot", "auto") == "auto"
    assert _exa_search_type("fast", "auto") == "fast"


def test_cli_exposes_provider_override_for_search_and_extract() -> None:
    search_args = build_parser().parse_args(["search", "Exa", "--provider", "exa"])
    extract_args = build_parser().parse_args(
        ["extract", "https://exa.ai/docs", "--provider", "parallel"]
    )

    assert search_args.provider == "exa"
    assert extract_args.provider == "parallel"


def test_search_uses_bounded_result_only_request(monkeypatch: Any) -> None:
    client = Client({"results": []})
    monkeypatch.setattr(exa, "build_client", lambda *_: client)

    result = exa.search(
        api_key="secret",
        query="Exa API contract",
        timeout_seconds=10,
        search_type="auto",
        max_results=5,
        after_date="2026-01-01",
        include_domains=["exa.ai"],
        highlights=True,
        proxy=None,
    )

    assert result == {"results": []}
    assert client.requests == [
        {
            "url": "https://api.exa.ai/search",
            "headers": {"Authorization": "Bearer secret", "Content-Type": "application/json"},
            "json": {
                "query": "Exa API contract",
                "type": "auto",
                "numResults": 5,
                "contents": {"highlights": True},
                "includeDomains": ["exa.ai"],
                "startPublishedDate": "2026-01-01T00:00:00.000Z",
            },
        }
    ]


def test_extract_bounds_text_unless_full_content_is_requested(monkeypatch: Any) -> None:
    client = Client({"results": []})
    monkeypatch.setattr(exa, "build_client", lambda *_: client)

    exa.extract(
        api_key="secret",
        urls=["https://exa.ai/docs"],
        timeout_seconds=10,
        full_content=False,
        max_chars_per_result=5000,
        proxy=None,
    )
    exa.extract(
        api_key="secret",
        urls=["https://exa.ai/docs"],
        timeout_seconds=10,
        full_content=True,
        max_chars_per_result=5000,
        proxy=None,
    )

    assert [request["json"] for request in client.requests] == [
        {"urls": ["https://exa.ai/docs"], "text": {"maxCharacters": 5000}},
        {"urls": ["https://exa.ai/docs"], "text": True},
    ]
