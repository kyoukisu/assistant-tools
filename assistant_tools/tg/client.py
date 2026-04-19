# pyright: reportMissingTypeStubs=false, reportGeneralTypeIssues=false

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import unquote
from urllib.parse import urlparse
from typing import Any

from telethon import TelegramClient
from telethon.sessions import StringSession

from assistant_tools.tg.config import ResolvedTgConfig


def _parse_proxy(proxy_url: str | None) -> tuple[Any, ...] | None:
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    scheme: str = parsed.scheme.lower()
    if scheme not in {"http", "socks5", "socks4"}:
        raise ValueError(f"Unsupported Telegram proxy scheme: {scheme}")
    if parsed.hostname is None or parsed.port is None:
        raise ValueError("Telegram proxy URL must include host and port")

    username: str | None = unquote(parsed.username) if parsed.username else None
    password: str | None = unquote(parsed.password) if parsed.password else None
    rdns: bool = True
    return (scheme, parsed.hostname, parsed.port, rdns, username, password)


def make_client(config: ResolvedTgConfig, *, receive_updates: bool = False) -> TelegramClient:
    session: str | StringSession
    if config.session_string:
        session = StringSession(config.session_string)
    else:
        session = str(config.session_file)

    proxy_config: tuple[Any, ...] | None = _parse_proxy(config.proxy)

    client_kwargs: dict[str, Any] = {
        "session": session,
        "api_id": config.api_id,
        "api_hash": config.api_hash,
        "flood_sleep_threshold": config.sleep_threshold,
        "receive_updates": receive_updates,
    }
    if proxy_config is not None:
        client_kwargs["proxy"] = proxy_config

    return TelegramClient(**client_kwargs)


@asynccontextmanager
async def telegram_client(
    config: ResolvedTgConfig, *, receive_updates: bool = False
) -> AsyncIterator[TelegramClient]:
    client: TelegramClient = make_client(config, receive_updates=receive_updates)
    await client.connect()
    try:
        yield client
    finally:
        disconnect_result = client.disconnect()
        if disconnect_result is not None:
            await disconnect_result


def build_message_link(
    chat_id: int | None, username: str | None, message_id: int | None
) -> str | None:
    if message_id is None:
        return None
    if username:
        return f"https://t.me/{username}/{message_id}"
    if chat_id is not None and str(chat_id).startswith("-100"):
        return f"https://t.me/c/{str(chat_id)[4:]}/{message_id}"
    return None
