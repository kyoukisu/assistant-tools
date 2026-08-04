from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from assistant_tools.cli import build_parser
from assistant_tools.cli import dispatch
from assistant_tools.config import load_config
from assistant_tools.shardx import ShardxClient
from assistant_tools.utils import AssistantToolsError


def parse(*args: str) -> argparse.Namespace:
    return build_parser().parse_args(["shardx", *args])


def mock_client(handler: Any) -> ShardxClient:
    transport = httpx.MockTransport(handler)
    return ShardxClient("http://shardx.test", client=httpx.Client(transport=transport))


def test_parser_exposes_shardx_commands() -> None:
    parser = build_parser()
    args = parser.parse_args(["shardx", "read", "--session", "s1"])
    assert args.command == "shardx"
    assert args.shardx_command == "read"
    assert args.region == "auto"


def test_open_creates_session_and_returns_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.method == "GET" and request.url.path == "/sessions":
            return httpx.Response(200, json={"sessions": []})
        if request.method == "POST" and request.url.path == "/sessions":
            return httpx.Response(200, json={"session_id": "s1"})
        if request.url.path == "/sessions/s1/open":
            return httpx.Response(200, json={"opened": True})
        if request.url.path == "/sessions/s1/observe":
            return httpx.Response(200, json={"snapshot": "snap-1", "controls": []})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = mock_client(handler)
    monkeypatch.setattr(ShardxClient, "from_env", classmethod(lambda cls: client))
    result = dispatch(
        parse("open", "https://example.com", "--session", "s1"), load_config(None), None
    )

    assert result.ok is True
    assert result.provider == "shardx"
    assert result.data is not None
    assert result.data["session"] == "s1"
    assert result.data["observation"]["snapshot"] == "snap-1"
    assert requests[1] == (
        "POST",
        "/sessions",
        {"session_id": "s1", "live": False},
    )


def test_read_preserves_text_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/s1/read"
        return httpx.Response(200, text="Article text")

    client = mock_client(handler)
    monkeypatch.setattr(ShardxClient, "from_env", classmethod(lambda cls: client))
    result = dispatch(parse("read", "--session", "s1"), load_config(None), None)
    assert result.data == {"session": "s1", "text": "Article text"}


def test_screenshot_writes_binary_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"png-data", headers={"content-type": "image/png"})

    client = mock_client(handler)
    monkeypatch.setattr(ShardxClient, "from_env", classmethod(lambda cls: client))
    output = tmp_path / "shot.png"
    result = dispatch(
        parse("screenshot", "--session", "s1", "--output", str(output)),
        load_config(None),
        None,
    )
    assert output.read_bytes() == b"png-data"
    assert result.data is not None
    assert result.data["path"] == str(output.resolve())
    assert result.data["mime_type"] == "image/png"


def test_http_error_is_structured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"error": {"code": "stale_snapshot", "message": "observe again"}},
        )

    client = mock_client(handler)
    with pytest.raises(AssistantToolsError) as caught:
        client.request_json("POST", "/sessions/s1/act", json={})
    assert caught.value.error_type == "stale_snapshot"
    assert "observe again" in str(caught.value)
