from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from typing import Any

from pyrogram.client import Client

from assistant_tools.tg.config import ResolvedTgConfig


def _session_name(config: ResolvedTgConfig) -> str:
    return config.session_file.stem or "main"


def make_client(config: ResolvedTgConfig) -> Client:
    proxy_config: dict[str, Any] | None = None
    return Client(
        name=_session_name(config),
        api_id=config.api_id,
        api_hash=config.api_hash,
        session_string=config.session_string,
        workdir=str(config.session_file.parent),
        proxy=proxy_config,
        no_updates=True,
        takeout=config.takeout,
        sleep_threshold=config.sleep_threshold,
        hide_password=config.hide_password,
    )


@asynccontextmanager
async def telegram_client(config: ResolvedTgConfig) -> AsyncIterator[Client]:
    client: Client = make_client(config)
    await client.start()
    try:
        yield client
    finally:
        await client.stop()
