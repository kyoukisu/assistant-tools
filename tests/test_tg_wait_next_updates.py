from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from assistant_tools.tg import commands
from assistant_tools.tg.config import ResolvedTgConfig


class _FakeClient:
    def __init__(self) -> None:
        self.handler: Any | None = None
        self.builder: Any | None = None

    async def get_me(self) -> Any:
        return SimpleNamespace(id=1)

    async def get_input_entity(self, entity: Any) -> Any:
        return entity

    def add_event_handler(self, handler: Any, builder: Any) -> None:
        self.handler = handler
        self.builder = builder
        loop = asyncio.get_running_loop()
        loop.call_soon(asyncio.create_task, handler(SimpleNamespace(message=_FakeMessage())))

    def remove_event_handler(self, handler: Any, builder: Any) -> None:
        pass


class _FakeMessage:
    id = 123
    date = None
    sender = None
    text = "probe"
    photo = None
    video = None
    document = None
    audio = None
    voice = None
    grouped_id = None
    reply_to = None
    out = False
    mentioned = False
    noforwards = False


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


async def _run_wait_next(calls: list[bool]) -> None:
    fake_client = _FakeClient()
    commands_module: Any = commands

    @asynccontextmanager
    async def fake_telegram_client(config: ResolvedTgConfig, *, receive_updates: bool = False):
        calls.append(receive_updates)
        yield fake_client

    async def fake_resolve_peer_entity(client: Any, peer: str) -> Any:
        return SimpleNamespace(id=1, username="me")

    original_telegram_client = commands_module.telegram_client
    original_resolve_peer_entity = getattr(commands_module, "_resolve_peer_entity")
    try:
        commands_module.telegram_client = fake_telegram_client
        setattr(commands_module, "_resolve_peer_entity", fake_resolve_peer_entity)
        result = await commands.wait_next_message(_config(), "me", 1.0, False)
    finally:
        commands_module.telegram_client = original_telegram_client
        setattr(commands_module, "_resolve_peer_entity", original_resolve_peer_entity)

    assert result.ok is True
    assert result.data is not None
    assert result.data["message"]["message_id"] == 123


def test_wait_next_enables_updates() -> None:
    calls: list[bool] = []
    asyncio.run(_run_wait_next(calls))
    assert calls == [True]
