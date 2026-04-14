# pyright: reportMissingTypeStubs=false, reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportAttributeAccessIssue=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportArgumentType=false

from __future__ import annotations

import asyncio
from getpass import getpass
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.errors import PasswordHashInvalidError
from telethon.errors import PhoneCodeInvalidError
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from assistant_tools.models import CommandResult
from assistant_tools.tg.client import make_client
from assistant_tools.tg.client import telegram_client
from assistant_tools.tg.config import ResolvedTgConfig
from assistant_tools.tg.normalize import normalize_chat
from assistant_tools.tg.normalize import normalize_dialog
from assistant_tools.tg.normalize import normalize_media
from assistant_tools.tg.normalize import normalize_message
from assistant_tools.tg.normalize import normalize_user
from assistant_tools.providers import deepinfra as deepinfra_provider
from assistant_tools.utils import require_env
from assistant_tools.utils import AssistantToolsError


def _ok(command: str, data: dict[str, Any], meta: dict[str, Any]) -> CommandResult:
    return CommandResult(
        ok=True,
        command=command,
        provider="telethon",
        data=data,
        error=None,
        meta=meta,
    )


def _error(code: str, message: str, *, exit_code: int = 1) -> AssistantToolsError:
    return AssistantToolsError(message, error_type=code, exit_code=exit_code)


def _ensure_local_file(path_value: str) -> Path:
    path: Path = Path(path_value).expanduser()
    if not path.exists():
        raise _error("missing_file", f"Input file does not exist: {path}", exit_code=2)
    if not path.is_file():
        raise _error("invalid_file", f"Input path is not a file: {path}", exit_code=2)
    return path.resolve()


def _voice_upload_path(path: Path) -> tuple[Path, bool]:
    suffix: str = path.suffix.lower()
    if suffix in {".ogg", ".opus"}:
        return path, False

    tmp = tempfile.NamedTemporaryFile(prefix="assistant-tools-voice-", suffix=".ogg", delete=False)
    tmp_path: Path = Path(tmp.name)
    tmp.close()
    command: list[str] = [
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-c:a",
        "libopus",
        "-b:a",
        "48k",
        "-application",
        "voip",
        str(tmp_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as err:
        tmp_path.unlink(missing_ok=True)
        raise _error("missing_runtime", "ffmpeg is not available in PATH", exit_code=5) from err
    except subprocess.CalledProcessError as err:
        tmp_path.unlink(missing_ok=True)
        stderr: str = (err.stderr or "").strip()
        detail: str = f": {stderr}" if stderr else ""
        raise _error(
            "voice_convert_failed", f"ffmpeg voice conversion failed{detail}", exit_code=5
        ) from err
    return tmp_path, True


def _is_incompatible_session_error(err: Exception) -> bool:
    message: str = str(err).lower()
    return (
        "no such column: version" in message
        or "database disk image is malformed" in message
        or "file is not a database" in message
    )


def _remove_session_files(config: ResolvedTgConfig) -> None:
    for candidate in [
        config.session_file,
        Path(f"{config.session_file}-journal"),
        Path(f"{config.session_file}-shm"),
        Path(f"{config.session_file}-wal"),
    ]:
        if candidate.exists():
            candidate.unlink()


async def _connect_with_recovery(client: TelegramClient, config: ResolvedTgConfig) -> None:
    try:
        await client.connect()
    except Exception as err:
        if _is_incompatible_session_error(err):
            _remove_session_files(config)
            await client.connect()
            return
        raise


async def auth_status(config: ResolvedTgConfig) -> CommandResult:
    try:
        async with telegram_client(config) as client:
            me: Any = await client.get_me()
            if me is None:
                raise _error("auth_not_configured", "Telegram session is not authorized")
            return _ok(
                "tg.auth.status",
                {
                    "authorized": True,
                    "session_file": str(config.session_file),
                    "profile": config.profile,
                    "user": normalize_user(me),
                },
                {"session_file": str(config.session_file), "profile": config.profile},
            )
    except AssistantToolsError:
        raise
    except Exception as err:
        if _is_incompatible_session_error(err):
            raise _error(
                "auth_not_configured",
                "Telegram session file is incompatible with the current backend; rerun 'kit tg auth login'",
            ) from err
        raise _error("auth_not_configured", f"Telegram session is not ready: {err}") from err


async def auth_export_session(config: ResolvedTgConfig) -> CommandResult:
    async with telegram_client(config) as client:
        session_string: str = StringSession.save(client.session)
        return _ok(
            "tg.auth.export-session",
            {"session_string": session_string},
            {"session_file": str(config.session_file), "profile": config.profile},
        )


async def auth_import_session(config: ResolvedTgConfig, session_string: str) -> CommandResult:
    imported_client = TelegramClient(StringSession(session_string), config.api_id, config.api_hash)
    await imported_client.connect()
    try:
        me: Any = await imported_client.get_me()
        if me is None:
            raise _error("auth_failed", "Imported session string is not authorized")
        clone_client = TelegramClient(str(config.session_file), config.api_id, config.api_hash)
        await _connect_with_recovery(clone_client, config)
        try:
            clone_client.session.set_dc(
                imported_client.session.dc_id,
                imported_client.session.server_address,
                imported_client.session.port,
            )
            clone_client.session.auth_key = imported_client.session.auth_key
            save_result = clone_client.session.save()  # pyright: ignore[reportArgumentType]
            if save_result is not None:
                await save_result
        finally:
            disconnect_result = clone_client.disconnect()
            if disconnect_result is not None:
                await disconnect_result

        return _ok(
            "tg.auth.import-session",
            {
                "authorized": True,
                "session_file": str(config.session_file),
                "profile": config.profile,
                "user": normalize_user(me),
            },
            {"session_file": str(config.session_file), "profile": config.profile},
        )
    finally:
        disconnect_result = imported_client.disconnect()
        if disconnect_result is not None:
            await disconnect_result


async def auth_login(config: ResolvedTgConfig, phone: str | None) -> CommandResult:
    client: TelegramClient = make_client(config)
    phone_number: str = phone or input("Phone number: ").strip()
    await _connect_with_recovery(client, config)
    try:
        sent_code: Any = await client.send_code_request(phone_number)
        code: str = input("Login code: ").strip()
        try:
            await client.sign_in(
                phone=phone_number, code=code, phone_code_hash=sent_code.phone_code_hash
            )
        except SessionPasswordNeededError:
            password: str = getpass("2FA password: ")
            await client.sign_in(password=password)
        except PhoneCodeInvalidError as err:
            raise _error("auth_failed", f"Invalid login code: {err}") from err
        except PasswordHashInvalidError as err:
            raise _error("auth_failed", f"Invalid 2FA password: {err}") from err

        me: Any = await client.get_me()
        if me is None:
            raise _error("auth_failed", "Login did not produce an authorized session")
        return _ok(
            "tg.auth.login",
            {
                "authorized": True,
                "session_file": str(config.session_file),
                "profile": config.profile,
                "user": normalize_user(me),
            },
            {"session_file": str(config.session_file), "profile": config.profile},
        )
    finally:
        disconnect_result = client.disconnect()
        if disconnect_result is not None:
            await disconnect_result


async def auth_logout(config: ResolvedTgConfig) -> CommandResult:
    removed: bool = any(
        candidate.exists()
        for candidate in [
            config.session_file,
            Path(f"{config.session_file}-journal"),
            Path(f"{config.session_file}-shm"),
            Path(f"{config.session_file}-wal"),
        ]
    )
    _remove_session_files(config)
    return _ok(
        "tg.auth.logout",
        {"removed": removed, "session_file": str(config.session_file), "profile": config.profile},
        {"session_file": str(config.session_file), "profile": config.profile},
    )


async def resolve_peer(config: ResolvedTgConfig, peer: str) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await client.get_entity(peer)
        return _ok(
            "tg.resolve",
            {"chat": normalize_chat(entity)},
            {"peer": peer, "profile": config.profile},
        )


async def dialogs(config: ResolvedTgConfig, limit: int, full: bool) -> CommandResult:
    async with telegram_client(config) as client:
        items: list[dict[str, Any]] = []
        async for dialog in client.iter_dialogs(limit=limit):
            items.append(normalize_dialog(dialog, full=full))
        return _ok(
            "tg.dialogs",
            {"items": items},
            {"limit": limit, "profile": config.profile, "full": full},
        )


async def participants(config: ResolvedTgConfig, peer: str, limit: int) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await client.get_entity(peer)
        items: list[dict[str, Any]] = []
        count: int = 0
        async for user in client.iter_participants(entity):
            items.append(normalize_user(user) or {})
            count += 1
            if limit > 0 and count >= limit:
                break
        return _ok(
            "tg.participants",
            {"items": items},
            {"peer": peer, "limit": limit, "profile": config.profile},
        )


async def history(
    config: ResolvedTgConfig, peer: str, limit: int, offset_id: int, full: bool
) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await client.get_entity(peer)
        items: list[dict[str, Any]] = []
        async for message in client.iter_messages(entity, limit=limit, offset_id=offset_id):
            items.append(normalize_message(message, chat_entity=entity, full=full))
        return _ok(
            "tg.history",
            {"items": items},
            {
                "peer": peer,
                "limit": limit,
                "offset_id": offset_id,
                "profile": config.profile,
                "full": full,
            },
        )


async def get_messages(
    config: ResolvedTgConfig, peer: str, message_ids: list[int], full: bool
) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await client.get_entity(peer)
        messages: Any = await client.get_messages(entity, ids=message_ids)
        if isinstance(messages, list):
            items: list[dict[str, Any]] = [
                normalize_message(message, chat_entity=entity, full=full)
                for message in messages
                if message
            ]
        else:
            items = [normalize_message(messages, chat_entity=entity, full=full)] if messages else []
        return _ok(
            "tg.get",
            {"items": items},
            {"peer": peer, "message_ids": message_ids, "profile": config.profile, "full": full},
        )


async def send_message(
    config: ResolvedTgConfig, peer: str, text: str, reply_to_message_id: int | None, full: bool
) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await client.get_entity(peer)
        if reply_to_message_id is None:
            message: Any = await client.send_message(entity, text)
        else:
            message = await client.send_message(entity, text, reply_to=reply_to_message_id)
        return _ok(
            "tg.send",
            {"message": normalize_message(message, chat_entity=entity, full=full)},
            {
                "peer": peer,
                "reply_to_message_id": reply_to_message_id,
                "profile": config.profile,
                "full": full,
            },
        )


async def send_file(
    config: ResolvedTgConfig,
    peer: str,
    path_value: str,
    caption: str | None,
    reply_to_message_id: int | None,
    full: bool,
) -> CommandResult:
    input_path: Path = _ensure_local_file(path_value)
    async with telegram_client(config) as client:
        entity: Any = await client.get_entity(peer)
        message: Any = await client.send_file(
            entity,
            str(input_path),
            caption=caption,
            reply_to=reply_to_message_id,
            force_document=True,
        )
        return _ok(
            "tg.send-file",
            {
                "path": str(input_path),
                "message": normalize_message(message, chat_entity=entity, full=full),
            },
            {
                "peer": peer,
                "path": str(input_path),
                "caption": caption,
                "reply_to_message_id": reply_to_message_id,
                "profile": config.profile,
                "full": full,
            },
        )


async def send_voice(
    config: ResolvedTgConfig,
    peer: str,
    path_value: str,
    caption: str | None,
    reply_to_message_id: int | None,
    full: bool,
) -> CommandResult:
    input_path: Path = _ensure_local_file(path_value)
    upload_path: Path
    converted: bool
    upload_path, converted = _voice_upload_path(input_path)
    try:
        async with telegram_client(config) as client:
            entity: Any = await client.get_entity(peer)
            message: Any = await client.send_file(
                entity,
                str(upload_path),
                caption=caption,
                reply_to=reply_to_message_id,
                voice_note=True,
                mime_type="audio/ogg",
            )
            return _ok(
                "tg.send-voice",
                {
                    "input_path": str(input_path),
                    "upload_path": str(upload_path),
                    "converted": converted,
                    "message": normalize_message(message, chat_entity=entity, full=full),
                },
                {
                    "peer": peer,
                    "path": str(input_path),
                    "caption": caption,
                    "reply_to_message_id": reply_to_message_id,
                    "profile": config.profile,
                    "full": full,
                    "converted": converted,
                },
            )
    finally:
        if converted:
            upload_path.unlink(missing_ok=True)


async def react(config: ResolvedTgConfig, peer: str, message_id: int, emoji: str) -> CommandResult:
    raise _error(
        "unsupported", f"Telegram reactions are not implemented yet for Telethon backend: {emoji}"
    )


async def search_messages(
    config: ResolvedTgConfig, peer: str, query: str, limit: int, full: bool
) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await client.get_entity(peer)
        items: list[dict[str, Any]] = []
        async for message in client.iter_messages(entity, search=query, limit=limit):
            items.append(normalize_message(message, chat_entity=entity, full=full))
        return _ok(
            "tg.search",
            {"items": items},
            {"peer": peer, "query": query, "limit": limit, "profile": config.profile, "full": full},
        )


def _dialog_text(chat: dict[str, Any]) -> str:
    title: str = str(chat.get("title") or "").strip()
    username: str = str(chat.get("username") or "").strip()
    if username:
        return f"{title} @{username}".strip()
    return title


def _cosine_topk(query_vec: list[float], doc_vecs: list[list[float]], k: int) -> list[int]:
    import math

    qn: float = math.sqrt(sum(x * x for x in query_vec)) + 1e-12
    q: list[float] = [x / qn for x in query_vec]

    scores: list[tuple[float, int]] = []
    for i, v in enumerate(doc_vecs):
        vn: float = math.sqrt(sum(x * x for x in v)) + 1e-12
        dot: float = 0.0
        for a, b in zip(q, v, strict=False):
            dot += a * (b / vn)
        scores.append((dot, i))
    scores.sort(key=lambda t: t[0], reverse=True)
    return [i for _, i in scores[:k]]


async def find_dialog(
    config: ResolvedTgConfig,
    *,
    query: str,
    limit: int,
    top: int,
    model: str,
    timeout_seconds: float,
    proxy: str | None,
) -> CommandResult:
    # Pull dialogs via Telethon, normalize to compact chat objects.
    async with telegram_client(config) as client:
        items: list[dict[str, Any]] = []
        async for dialog in client.iter_dialogs(limit=limit):
            items.append(normalize_dialog(dialog, full=False))

    chats: list[dict[str, Any]] = [it.get("chat") or {} for it in items]
    texts: list[str] = [_dialog_text(c) for c in chats]

    api_key: str = require_env("DEEPINFRA_TOKEN")
    vecs: list[list[float]] = deepinfra_provider.embeddings(
        api_key=api_key,
        model=model,
        inputs=[query, *texts],
        timeout_seconds=timeout_seconds,
        proxy=proxy,
    )
    query_vec: list[float] = vecs[0]
    doc_vecs: list[list[float]] = vecs[1:]
    idxs: list[int] = _cosine_topk(query_vec, doc_vecs, k=top)

    matches: list[dict[str, Any]] = []
    for rank, i in enumerate(idxs, start=1):
        chat: dict[str, Any] = chats[i]
        matches.append({"rank": rank, "chat": chat})

    return _ok(
        "tg.find-dialog",
        {"matches": matches},
        {"query": query, "limit": limit, "top": top, "model": model, "profile": config.profile},
    )


async def media_info(
    config: ResolvedTgConfig, peer: str, message_id: int, full: bool
) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await client.get_entity(peer)
        message: Any = await client.get_messages(entity, ids=message_id)
        if not message:
            raise _error("not_found", "Message not found")
        media: dict[str, Any] | None = normalize_media(message)
        if media is None:
            raise _error("not_found", "Message has no media")
        data: dict[str, Any] = {
            "message_id": message_id,
            "chat": normalize_chat(entity),
            "media": media,
        }
        if full:
            data["message"] = normalize_message(message, chat_entity=entity, full=True)
        return _ok(
            "tg.media-info",
            data,
            {"peer": peer, "message_id": message_id, "profile": config.profile, "full": full},
        )


async def media_download(
    config: ResolvedTgConfig, peer: str, message_id: int, output_dir: str | None, full: bool
) -> CommandResult:
    target_dir: Path = Path(output_dir).expanduser() if output_dir else config.download_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    async with telegram_client(config) as client:
        entity: Any = await client.get_entity(peer)
        message: Any = await client.get_messages(entity, ids=message_id)
        if not message:
            raise _error("not_found", "Message not found")
        media: dict[str, Any] | None = normalize_media(message)
        if media is None:
            raise _error("not_found", "Message has no media")
        downloaded_any: Any = await client.download_media(message, file=str(target_dir))
        downloaded: str | None = downloaded_any if isinstance(downloaded_any, str) else None
        data: dict[str, Any] = {
            "path": str(Path(downloaded).expanduser().resolve()) if downloaded else None,
            "message_id": message_id,
            "chat": normalize_chat(entity),
            "media": media,
        }
        if full:
            data["message"] = normalize_message(message, chat_entity=entity, full=True)
        return _ok(
            "tg.media-download",
            data,
            {
                "peer": peer,
                "message_id": message_id,
                "output_dir": str(target_dir),
                "profile": config.profile,
                "full": full,
            },
        )


async def copy_message(
    config: ResolvedTgConfig,
    source_peer: str,
    message_id: int,
    target_peer: str,
    full: bool,
) -> CommandResult:
    async with telegram_client(config) as client:
        source_entity: Any = await client.get_entity(source_peer)
        target_entity: Any = await client.get_entity(target_peer)
        message: Any = await client.forward_messages(target_entity, message_id, source_entity)
        normalized: dict[str, Any]
        if isinstance(message, list):
            normalized = (
                normalize_message(message[0], chat_entity=target_entity, full=full)
                if message
                else {}
            )
        else:
            normalized = normalize_message(message, chat_entity=target_entity, full=full)
        return _ok(
            "tg.copy",
            {"message": normalized},
            {
                "source_peer": source_peer,
                "message_id": message_id,
                "target_peer": target_peer,
                "profile": config.profile,
                "full": full,
            },
        )


def run(coro: Any) -> CommandResult:
    try:
        return asyncio.run(coro)
    except FloodWaitError as err:
        raise _error(
            "rate_limited", f"Telegram FloodWait: retry after {err.seconds} seconds"
        ) from err
