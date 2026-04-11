from __future__ import annotations

import asyncio
from getpass import getpass
from pathlib import Path
from typing import Any
from typing import cast

from pyrogram.errors import FloodWait
from pyrogram.errors import PasswordHashInvalid
from pyrogram.errors import PeerIdInvalid
from pyrogram.errors import PhoneCodeInvalid
from pyrogram.errors import SessionPasswordNeeded

from assistant_tools.models import CommandResult
from assistant_tools.tg.client import make_client
from assistant_tools.tg.client import telegram_client
from assistant_tools.tg.config import ResolvedTgConfig
from assistant_tools.tg.normalize import normalize_chat
from assistant_tools.tg.normalize import normalize_dialog
from assistant_tools.tg.normalize import normalize_media
from assistant_tools.tg.normalize import normalize_message
from assistant_tools.tg.normalize import normalize_user
from assistant_tools.utils import AssistantToolsError


def _ok(command: str, data: dict[str, Any], meta: dict[str, Any]) -> CommandResult:
    return CommandResult(
        ok=True,
        command=command,
        provider="kurigram",
        data=data,
        error=None,
        meta=meta,
    )


def _error(code: str, message: str, *, exit_code: int = 1) -> AssistantToolsError:
    return AssistantToolsError(message, error_type=code, exit_code=exit_code)


async def auth_status(config: ResolvedTgConfig) -> CommandResult:
    try:
        async with telegram_client(config) as client:
            me: Any = await client.get_me()
            return _ok(
                "tg.auth.status",
                {
                    "authorized": True,
                    "session_file": str(config.session_file),
                    "user": normalize_user(me),
                },
                {"session_file": str(config.session_file)},
            )
    except Exception as err:
        raise _error("auth_not_configured", f"Telegram session is not ready: {err}") from err


async def auth_export_session(config: ResolvedTgConfig) -> CommandResult:
    async with telegram_client(config) as client:
        session_string: str = await client.export_session_string()
        return _ok(
            "tg.auth.export-session",
            {"session_string": session_string},
            {"session_file": str(config.session_file)},
        )


async def auth_import_session(config: ResolvedTgConfig, session_string: str) -> CommandResult:
    imported_config: ResolvedTgConfig = ResolvedTgConfig(
        profile=config.profile,
        api_id=config.api_id,
        api_hash=config.api_hash,
        session_file=config.session_file,
        download_dir=config.download_dir,
        cache_dir=config.cache_dir,
        session_string=session_string,
        proxy=config.proxy,
        takeout=config.takeout,
        sleep_threshold=config.sleep_threshold,
        hide_password=config.hide_password,
    )
    async with telegram_client(imported_config) as client:
        me: Any = await client.get_me()
        return _ok(
            "tg.auth.import-session",
            {
                "authorized": True,
                "session_file": str(config.session_file),
                "user": normalize_user(me),
            },
            {"session_file": str(config.session_file)},
        )


async def auth_login(config: ResolvedTgConfig, phone: str | None) -> CommandResult:
    client = make_client(config)
    phone_number: str = phone or input("Phone number: ").strip()
    await client.connect()
    try:
        sent_code: Any = await client.send_code(phone_number)
        code: str = input("Login code: ").strip()
        try:
            await client.sign_in(
                phone_number=phone_number,
                phone_code_hash=sent_code.phone_code_hash,
                phone_code=code,
            )
        except SessionPasswordNeeded:
            password: str = getpass("2FA password: ")
            await client.check_password(password)
        except PhoneCodeInvalid as err:
            raise _error("auth_failed", f"Invalid login code: {err}") from err
        except PasswordHashInvalid as err:
            raise _error("auth_failed", f"Invalid 2FA password: {err}") from err

        me: Any = await client.get_me()
        return _ok(
            "tg.auth.login",
            {
                "authorized": True,
                "session_file": str(config.session_file),
                "user": normalize_user(me),
            },
            {"session_file": str(config.session_file)},
        )
    finally:
        await client.disconnect()


async def auth_logout(config: ResolvedTgConfig) -> CommandResult:
    if config.session_file.exists():
        config.session_file.unlink()
    return _ok(
        "tg.auth.logout",
        {"removed": True, "session_file": str(config.session_file)},
        {"session_file": str(config.session_file)},
    )


async def resolve_peer(config: ResolvedTgConfig, peer: str) -> CommandResult:
    async with telegram_client(config) as client:
        chat: Any = await client.get_chat(peer)
        return _ok("tg.resolve", {"chat": normalize_chat(chat)}, {"peer": peer})


async def dialogs(config: ResolvedTgConfig, limit: int) -> CommandResult:
    async with telegram_client(config) as client:
        items: list[dict[str, Any]] = []
        async for dialog in client.get_dialogs(limit=limit):
            items.append(normalize_dialog(dialog))
        return _ok("tg.dialogs", {"items": items}, {"limit": limit})


async def history(config: ResolvedTgConfig, peer: str, limit: int, offset_id: int) -> CommandResult:
    async with telegram_client(config) as client:
        items: list[dict[str, Any]] = []
        async for message in client.get_chat_history(peer, limit=limit, offset_id=offset_id):
            items.append(normalize_message(message))
        return _ok(
            "tg.history",
            {"items": items},
            {"peer": peer, "limit": limit, "offset_id": offset_id},
        )


async def get_messages(
    config: ResolvedTgConfig, peer: str, message_ids: list[int]
) -> CommandResult:
    async with telegram_client(config) as client:
        messages: Any = await client.get_messages(peer, message_ids)
        if isinstance(messages, list):
            items: list[dict[str, Any]] = []
            typed_messages: list[Any] = cast(list[Any], messages)
            for item in typed_messages:
                if item is not None:
                    items.append(normalize_message(item))
        else:
            items = [normalize_message(messages)] if messages is not None else []
        return _ok("tg.get", {"items": items}, {"peer": peer, "message_ids": message_ids})


async def send_message(
    config: ResolvedTgConfig,
    peer: str,
    text: str,
    reply_to_message_id: int | None,
) -> CommandResult:
    async with telegram_client(config) as client:
        message: Any = await client.send_message(
            peer, text, reply_to_message_id=reply_to_message_id
        )
        return _ok(
            "tg.send",
            {"message": normalize_message(message)},
            {"peer": peer, "reply_to_message_id": reply_to_message_id},
        )


async def react(config: ResolvedTgConfig, peer: str, message_id: int, emoji: str) -> CommandResult:
    async with telegram_client(config) as client:
        await client.send_reaction(peer, message_id, emoji)
        return _ok(
            "tg.react",
            {"success": True},
            {"peer": peer, "message_id": message_id, "emoji": emoji},
        )


async def search_messages(
    config: ResolvedTgConfig, peer: str, query: str, limit: int
) -> CommandResult:
    async with telegram_client(config) as client:
        items: list[dict[str, Any]] = []
        async for message in client.search_messages(peer, query=query, limit=limit):
            items.append(normalize_message(message))
        return _ok(
            "tg.search",
            {"items": items},
            {"peer": peer, "query": query, "limit": limit},
        )


async def media_info(config: ResolvedTgConfig, peer: str, message_id: int) -> CommandResult:
    async with telegram_client(config) as client:
        message: Any = await client.get_messages(peer, message_id)
        media: dict[str, Any] | None = normalize_media(message)
        if media is None:
            raise _error("not_found", "Message has no media")
        return _ok(
            "tg.media-info",
            {"media": media, "message": normalize_message(message)},
            {"peer": peer, "message_id": message_id},
        )


async def media_download(
    config: ResolvedTgConfig,
    peer: str,
    message_id: int,
    output_dir: str | None,
) -> CommandResult:
    target_dir: Path = Path(output_dir).expanduser() if output_dir else config.download_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    async with telegram_client(config) as client:
        message: Any = await client.get_messages(peer, message_id)
        media: dict[str, Any] | None = normalize_media(message)
        if media is None:
            raise _error("not_found", "Message has no media")
        downloaded_any: Any = await client.download_media(message, file_name=str(target_dir))  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportCallIssue]
        downloaded: str | None = downloaded_any if isinstance(downloaded_any, str) else None
        return _ok(
            "tg.media-download",
            {
                "path": str(Path(downloaded).expanduser().resolve()) if downloaded else None,
                "media": media,
                "message": normalize_message(message),
            },
            {"peer": peer, "message_id": message_id, "output_dir": str(target_dir)},
        )


async def copy_message(
    config: ResolvedTgConfig, source_peer: str, message_id: int, target_peer: str
) -> CommandResult:
    async with telegram_client(config) as client:
        message: Any = await client.copy_message(target_peer, source_peer, message_id)
        return _ok(
            "tg.copy",
            {"message": normalize_message(message)},
            {"source_peer": source_peer, "message_id": message_id, "target_peer": target_peer},
        )


def run(coro: Any) -> CommandResult:
    try:
        return asyncio.run(coro)
    except FloodWait as err:
        raise _error(
            "rate_limited", f"Telegram FloodWait: retry after {err.value} seconds"
        ) from err
    except PeerIdInvalid as err:
        raise _error("not_found", f"Telegram peer not found: {err}") from err
