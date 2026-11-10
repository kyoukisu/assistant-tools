from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from assistant_tools import cli
from assistant_tools.tg import commands
from assistant_tools.tg.config import ResolvedTgConfig


def _check(condition: bool) -> None:
    if not condition:
        raise AssertionError


def _config() -> ResolvedTgConfig:
    return ResolvedTgConfig(
        profile="default",
        api_id=1,
        api_hash="hash",
        session_file=Path("/tmp/test.session"),
        download_dir=Path("/tmp/downloads"),
        cache_dir=Path("/tmp/cache"),
        session_string=None,
        proxy=None,
        takeout=False,
        sleep_threshold=60,
        hide_password=False,
    )


class _FakeClient:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        return SimpleNamespace(url="https://example.invalid/#test-launch-url")


@asynccontextmanager
async def _fake_telegram_client(
    config: ResolvedTgConfig, client: _FakeClient
) -> AsyncGenerator[_FakeClient, None]:
    yield client


def test_miniapp_main_requests_telegram_issued_url(monkeypatch: Any) -> None:
    client = _FakeClient()
    bot = SimpleNamespace(id=42)

    async def fake_resolve_peer_entity(client: Any, peer: str) -> Any:
        _check(peer == "example_bot")
        return bot

    monkeypatch.setattr(commands, "telegram_client", partial(_fake_telegram_client, client=client))
    monkeypatch.setattr(commands, "_resolve_peer_entity", fake_resolve_peer_entity)

    result = asyncio.run(
        commands.miniapp_main_url(_config(), "example_bot", "campaign", "tdesktop", False, True)
    )

    _check(result.ok)
    _check(result.data == {"url": "https://example.invalid/#test-launch-url", "sensitive": True})
    _check(result.meta["sensitive_fields"] == ["data.url"])
    _check(len(client.requests) == 1)
    request = client.requests[0]
    _check(type(request).__name__ == "RequestMainWebViewRequest")
    _check(type(request.peer).__name__ == "InputPeerEmpty")
    _check(request.bot == bot)
    _check(request.start_param == "campaign")
    _check(request.platform == "tdesktop")
    _check(bool(request.fullscreen))
    _check(request.compact is None)


def test_miniapp_menu_requests_telegram_issued_url(monkeypatch: Any) -> None:
    bot = SimpleNamespace(id=42)

    class MenuClient(_FakeClient):
        async def __call__(self, request: Any) -> Any:
            self.requests.append(request)
            if type(request).__name__ == "GetFullUserRequest":
                menu_button = SimpleNamespace(url="https://menu.example.invalid/app")
                bot_info = SimpleNamespace(menu_button=menu_button)
                return SimpleNamespace(full_user=SimpleNamespace(bot_info=bot_info))
            return SimpleNamespace(url="https://example.invalid/#test-menu-launch-url")

    client = MenuClient()

    async def fake_resolve_peer_entity(client: Any, peer: str) -> Any:
        _check(peer == "example_bot")
        return bot

    monkeypatch.setattr(commands, "telegram_client", partial(_fake_telegram_client, client=client))
    monkeypatch.setattr(commands, "_resolve_peer_entity", fake_resolve_peer_entity)

    result = asyncio.run(
        commands.miniapp_menu_url(_config(), "example_bot", None, "tdesktop", True, False)
    )

    _check(result.ok)
    _check(result.data == {"url": "https://example.invalid/#test-menu-launch-url", "sensitive": True})
    _check([type(request).__name__ for request in client.requests] == [
        "GetFullUserRequest",
        "RequestWebViewRequest",
    ])
    request = client.requests[1]
    _check(request.peer == bot)
    _check(request.bot == bot)
    _check(request.url == "https://menu.example.invalid/app")
    _check(bool(request.from_bot_menu))
    _check(bool(request.compact))
    _check(request.fullscreen is None)


def test_miniapp_open_fetches_url_and_opens_identity(monkeypatch: Any) -> None:
    monkeypatch.delenv("BX_OWNER", raising=False)
    bot = SimpleNamespace(id=42)
    shardx_calls: list[tuple[str, str, dict[str, Any] | None]] = []

    class MenuClient(_FakeClient):
        async def __call__(self, request: Any) -> Any:
            self.requests.append(request)
            if type(request).__name__ == "GetFullUserRequest":
                menu_button = SimpleNamespace(url="https://menu.example.invalid/app")
                bot_info = SimpleNamespace(menu_button=menu_button)
                return SimpleNamespace(full_user=SimpleNamespace(bot_info=bot_info))
            return SimpleNamespace(url="https://example.invalid/#test-menu-launch-url")

    client = MenuClient()

    async def fake_resolve_peer_entity(client: Any, peer: str) -> Any:
        _check(peer == "example_bot")
        return bot

    class FakeShardxClient:
        def __init__(self) -> None:
            pass

        def request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            body = kwargs.get("json")
            shardx_calls.append((method, path, body))
            if method == "POST" and path.endswith("/open"):
                return {"status": "ok", "session": "ident"}
            if method == "POST" and path.endswith("/wait"):
                return {"status": "ok"}
            if method == "GET" and path.endswith("/observe"):
                return {
                    "snapshot": "snap-1",
                    "url": "https://example.invalid/#secret-launch-url",
                    "title": "Example App",
                    "controls": [],
                }
            return {"status": "ok"}

        def close(self) -> None:
            pass

    monkeypatch.setattr(commands, "telegram_client", partial(_fake_telegram_client, client=client))
    monkeypatch.setattr(commands, "_resolve_peer_entity", fake_resolve_peer_entity)
    monkeypatch.setattr(
        "assistant_tools.shardx.ShardxClient.from_env", classmethod(lambda cls: FakeShardxClient())
    )

    result = asyncio.run(
        commands.miniapp_open(_config(), "example_bot", "ident", "menu", None, "tdesktop", False, False, 1000, False)
    )

    _check(result.ok)
    data = result.data
    _check(data is not None)
    assert data is not None
    _check(data["session"] == "ident")
    _check(
        data["observation"]
        == {"snapshot": "snap-1", "title": "Example App", "controls": []}
    )
    _check("opened" not in data)
    _check("secret-launch-url" not in str(data))
    _check([type(request).__name__ for request in client.requests] == [
        "GetFullUserRequest",
        "RequestWebViewRequest",
    ])
    _check(shardx_calls[0][0] == "POST")
    _check(shardx_calls[0][1] == "/identities/ident/open")
    _check(shardx_calls[0][2] == {"url": "https://example.invalid/#test-menu-launch-url", "live": False})
    _check(shardx_calls[1][1] == "/sessions/ident/wait")
    _check(shardx_calls[2][1] == "/sessions/ident/observe")


def test_miniapp_open_scopes_owner_and_hides_launch_response(monkeypatch: Any) -> None:
    import httpx

    from assistant_tools.models import CommandResult
    from assistant_tools.shardx import ShardxClient

    monkeypatch.setenv("BX_OWNER", "dsh-task")
    launch = "https://example.invalid/#secret-launch-url"
    calls: list[tuple[str, str, Any]] = []

    async def launch_url(*args: Any) -> CommandResult:
        return CommandResult(ok=True, command="tg.miniapp.main", provider="telegram", error=None, meta={},
                             data={"url": launch})

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        assert request.headers["X-Browser-Owner"] == "dsh-task"
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/workspaces/open":
            return httpx.Response(200, json={"ok": True, "data": {
                "session_id": "ws-owned", "workspace": True, "url": launch,
                "model_text": launch,
            }})
        if request.url.path == "/sessions/ws-owned/wait":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/sessions/ws-owned/observe":
            return httpx.Response(200, json={"snapshot": "snap", "title": "App",
                                            "controls": [], "url": launch, "model_text": launch})
        raise AssertionError(request.url.path)

    original = ShardxClient.from_env.__func__

    def from_env(cls: Any) -> ShardxClient:
        client = original(cls)
        headers = dict(client.client.headers)
        client.client.close()
        client.client = httpx.Client(transport=httpx.MockTransport(handler), headers=headers)
        return client

    monkeypatch.setattr(commands, "miniapp_main_url", launch_url)
    monkeypatch.setattr(ShardxClient, "from_env", classmethod(from_env))
    result = asyncio.run(commands.miniapp_open(
        _config(), "example_bot", "ident", "main", None, "tdesktop", False, False, 200, False
    ))
    assert result.data == {"session": "ws-owned", "identity": "ident", "workspace": True,
                           "browser_session": "ident",
                           "observation": {"snapshot": "snap", "title": "App", "controls": []}}
    assert "secret-launch-url" not in str(result)
    assert calls == [
        ("POST", "/workspaces/open", {"identity": "ident", "url": launch,
                                       "live": False, "view": "status"}),
        ("POST", "/sessions/ws-owned/wait", {"seconds": 0.2}),
        ("GET", "/sessions/ws-owned/observe", None),
    ]


def test_miniapp_bad_workspace_never_falls_back_to_root(monkeypatch: Any) -> None:
    import pytest

    from assistant_tools.models import CommandResult
    from assistant_tools.utils import AssistantToolsError

    monkeypatch.setenv("BX_OWNER", "dsh-task")
    calls: list[str] = []

    async def launch_url(*args: Any) -> CommandResult:
        return CommandResult(ok=True, command="tg.miniapp.main", provider="telegram", error=None, meta={},
                             data={"url": "https://example.invalid/#secret"})

    class FakeShardxClient:
        def request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            calls.append(path)
            return {"ok": True, "data": {"session_id": "ident", "url": "SECRET"}}

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(commands, "miniapp_main_url", launch_url)
    monkeypatch.setattr("assistant_tools.shardx.ShardxClient.from_env",
                        classmethod(lambda cls: FakeShardxClient()))
    with pytest.raises(AssistantToolsError, match="valid task workspace") as error:
        asyncio.run(commands.miniapp_open(
            _config(), "example_bot", "ident", "main", None, "tdesktop", False, False, 0, False
        ))
    assert "SECRET" not in str(error.value)
    assert calls == ["/workspaces/open", "close"]


def test_miniapp_open_parser() -> None:
    args = cli.build_parser().parse_args(
        ["tg", "miniapp", "open", "example_bot", "--identity", "ident", "--wait-ms", "500", "--live"]
    )

    _check(args.miniapp_command == "open")
    _check(args.bot == "example_bot")
    _check(args.identity == "ident")
    _check(args.source == "menu")
    _check(args.wait_ms == 500)
    _check(bool(args.live))


def test_miniapp_main_parser() -> None:
    args = cli.build_parser().parse_args(
        ["tg", "miniapp", "main", "example_bot", "--compact", "--platform", "ios"]
    )

    _check(args.tg_command == "miniapp")
    _check(args.miniapp_command == "main")
    _check(args.bot == "example_bot")
    _check(bool(args.compact))
    _check(not bool(args.fullscreen))
    _check(args.platform == "ios")


def test_miniapp_menu_parser() -> None:
    args = cli.build_parser().parse_args(
        ["tg", "miniapp", "menu", "example_bot", "--fullscreen"]
    )

    _check(args.miniapp_command == "menu")
    _check(args.bot == "example_bot")
    _check(not bool(args.compact))
    _check(bool(args.fullscreen))
