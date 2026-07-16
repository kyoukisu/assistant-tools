# pyright: reportMissingTypeStubs=false, reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportAttributeAccessIssue=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportArgumentType=false

from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
from getpass import getpass
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from telethon import TelegramClient
from telethon import utils
from telethon.errors import FloodWaitError
from telethon.errors import PasswordHashInvalidError
from telethon.errors import PhoneCodeInvalidError
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.types import InputPeerSelf
from telethon.tl.types import PeerChannel
from telethon.tl.types import PeerChat
from telethon.tl.types import PeerUser

from assistant_tools.models import CommandResult
from assistant_tools.tg.client import make_client
from assistant_tools.tg.client import telegram_client
from assistant_tools.tg.config import ResolvedTgConfig
from assistant_tools.tg.dialog_search import rank_existing_dialogs
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


def _parse_marked_peer(peer: str) -> PeerChannel | PeerChat | PeerUser | None:
    try:
        marked_id: int = int(peer)
    except ValueError:
        return None

    real_id: int
    peer_type: type[Any]
    real_id, peer_type = utils.resolve_id(marked_id)
    if peer_type is PeerChannel:
        return PeerChannel(real_id)
    if peer_type is PeerChat:
        return PeerChat(real_id)
    if peer_type is PeerUser:
        return PeerUser(real_id)
    return None


async def _resolve_peer_entity(client: TelegramClient, peer: str) -> Any:
    marked_peer: PeerChannel | PeerChat | PeerUser | None = _parse_marked_peer(peer)
    if marked_peer is not None:
        try:
            return await client.get_input_entity(marked_peer)
        except ValueError:
            pass

        async for dialog in client.iter_dialogs():
            entity: Any = getattr(dialog, "entity", None)
            if entity is None:
                continue
            if utils.get_peer_id(entity) == int(peer):
                return await client.get_input_entity(entity)

        raise _error(
            "peer_not_found",
            (
                f"Cannot resolve Telegram peer from id {peer}. "
                "This usually means the peer is not in the local Telethon entity cache yet. "
                "Use a username/title first, or refresh dialogs so the entity and access hash are cached."
            ),
            exit_code=3,
        )

    try:
        return await client.get_input_entity(peer)
    except ValueError:
        return await client.get_entity(peer)


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
        entity: Any = await _resolve_peer_entity(client, peer)
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
        entity: Any = await _resolve_peer_entity(client, peer)
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
        entity: Any = await _resolve_peer_entity(client, peer)
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
        entity: Any = await _resolve_peer_entity(client, peer)
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
    config: ResolvedTgConfig, peer: str, text: str, reply_to_message_id: int | None, full: bool, parse_mode: str | None = None
) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await _resolve_peer_entity(client, peer)
        kwargs: dict[str, Any] = {}
        if reply_to_message_id is not None:
            kwargs["reply_to"] = reply_to_message_id
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        message: Any = await client.send_message(entity, text, **kwargs)
        return _ok(
            "tg.send",
            {"message": normalize_message(message, chat_entity=entity, full=full)},
            {
                "peer": peer,
                "reply_to_message_id": reply_to_message_id,
                "profile": config.profile,
                "full": full,
                "parse_mode": parse_mode,
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
        entity: Any = await _resolve_peer_entity(client, peer)
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


async def send_media(
    config: ResolvedTgConfig,
    peer: str,
    path_value: str,
    caption: str | None,
    reply_to_message_id: int | None,
    full: bool,
    force_video: bool = False,
) -> CommandResult:
    input_path: Path = _ensure_local_file(path_value)
    upload_path: Path = input_path
    tmp_path: Path | None = None
    is_video: bool = force_video and input_path.suffix.lower() in (".mp4", ".mkv", ".avi", ".mov", ".webm")

    if is_video:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-i", str(input_path)], capture_output=True, text=True, timeout=30,
        )
        has_audio: bool = "Audio:" in probe.stderr
        if not has_audio:
            tmp = tempfile.NamedTemporaryFile(suffix=f"_{input_path.name}", delete=False)
            tmp.close()
            tmp_path = Path(tmp.name)
            ff_result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(input_path), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                 "-c:v", "copy", "-c:a", "aac", "-shortest", str(tmp_path)],
                capture_output=True, timeout=120,
            )
            if ff_result.returncode == 0:
                upload_path = tmp_path
            else:
                tmp_path.unlink(missing_ok=True)
                tmp_path = None

    async with telegram_client(config) as client:
        entity: Any = await _resolve_peer_entity(client, peer)
        message: Any = await client.send_file(
            entity,
            str(upload_path),
            caption=caption,
            reply_to=reply_to_message_id,
            force_document=False,
            supports_streaming=True,
        )
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        return _ok(
            "tg.send-media",
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
            entity: Any = await _resolve_peer_entity(client, peer)
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
    from telethon.tl.functions.messages import SendReactionRequest
    from telethon.tl.types import ReactionEmoji
    async with telegram_client(config) as client:
        entity: Any = await _resolve_peer_entity(client, peer)
        await client(SendReactionRequest(
            peer=entity,
            msg_id=message_id,
            reaction=[ReactionEmoji(emoticon=emoji)],
        ))
        return _ok(
            "tg.react",
            {"peer": peer, "message_id": message_id, "emoji": emoji},
            {"peer": peer, "message_id": message_id, "profile": config.profile},
        )


async def search_messages(
    config: ResolvedTgConfig, peer: str, query: str, limit: int, full: bool
) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await _resolve_peer_entity(client, peer)
        items: list[dict[str, Any]] = []
        async for message in client.iter_messages(entity, search=query, limit=limit):
            items.append(normalize_message(message, chat_entity=entity, full=full))
        return _ok(
            "tg.search",
            {"items": items},
            {"peer": peer, "query": query, "limit": limit, "profile": config.profile, "full": full},
        )


async def send_album(
    config: ResolvedTgConfig,
    peer: str,
    paths: list[str],
    caption: str | None,
    reply_to_message_id: int | None,
    full: bool,
    force_video: bool = False,
) -> CommandResult:
    input_paths: list[Path] = [_ensure_local_file(p) for p in paths]
    upload_paths: list[Path] = []
    temp_files: list[Path] = []

    if force_video:
        for p in input_paths:
            if p.suffix.lower() in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-i", str(p)], capture_output=True, text=True, timeout=30,
                )
                if "Audio:" not in probe.stderr:
                    tmp = tempfile.NamedTemporaryFile(suffix=f"_{p.name}", delete=False)
                    tmp.close()
                    tmp_path = Path(tmp.name)
                    ff_result = subprocess.run(
                        ["ffmpeg", "-y", "-i", str(p), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                         "-c:v", "copy", "-c:a", "aac", "-shortest", str(tmp_path)],
                        capture_output=True, timeout=120,
                    )
                    if ff_result.returncode == 0:
                        upload_paths.append(tmp_path)
                        temp_files.append(tmp_path)
                    else:
                        tmp_path.unlink(missing_ok=True)
                        upload_paths.append(p)
                    continue
            upload_paths.append(p)
    else:
        upload_paths = input_paths

    async with telegram_client(config) as client:
        entity: Any = await _resolve_peer_entity(client, peer)
        messages: Any = await client.send_file(
            entity,
            [str(p) for p in upload_paths],
            caption=caption,
            reply_to=reply_to_message_id,
        )
        items: list[dict[str, Any]] = []
        for msg in (messages if isinstance(messages, list) else [messages]):
            items.append(normalize_message(msg, chat_entity=entity, full=full))
        for tf in temp_files:
            tf.unlink(missing_ok=True)
        return _ok(
            "tg.send-album",
            {"messages": items},
            {
                "peer": peer,
                "paths": [str(p) for p in input_paths],
                "caption": caption,
                "reply_to_message_id": reply_to_message_id,
                "profile": config.profile,
                "full": full,
            },
        )


async def forward_message(
    config: ResolvedTgConfig, from_peer: str, to_peer: str, message_ids: list[int]
) -> CommandResult:
    async with telegram_client(config) as client:
        from_entity: Any = await _resolve_peer_entity(client, from_peer)
        to_entity: Any = await _resolve_peer_entity(client, to_peer)
        fwd: Any = await client.forward_messages(to_entity, message_ids, from_entity)
        items: list[dict[str, Any]] = []
        for msg in (fwd if isinstance(fwd, list) else [fwd]):
            if msg:
                items.append(normalize_message(msg, chat_entity=to_entity, full=False))
        return _ok(
            "tg.forward",
            {"messages": items},
            {"from_peer": from_peer, "to_peer": to_peer, "message_ids": message_ids, "profile": config.profile},
        )


async def edit_message(
    config: ResolvedTgConfig, peer: str, message_id: int, text: str, parse_mode: str | None = None
) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await _resolve_peer_entity(client, peer)
        kwargs: dict[str, Any] = {}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        message: Any = await client.edit_message(entity, message_id, text, **kwargs)
        return _ok(
            "tg.edit",
            {"message": normalize_message(message, chat_entity=entity, full=False)},
            {"peer": peer, "message_id": message_id, "profile": config.profile},
        )


async def delete_message(
    config: ResolvedTgConfig, peer: str, message_ids: list[int]
) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await _resolve_peer_entity(client, peer)
        await client.delete_messages(entity, message_ids)
        return _ok(
            "tg.delete",
            {"deleted": message_ids},
            {"peer": peer, "message_ids": message_ids, "profile": config.profile},
        )


async def find_dialog(
    config: ResolvedTgConfig,
    *,
    query: str,
    limit: int,
) -> CommandResult:
    """Find a recent existing dialog before using native Telegram search."""
    from telethon.tl.functions.contacts import SearchRequest

    dialog_scan_limit = 500
    async with telegram_client(config) as client:
        chats: list[dict[str, Any]] = []
        async for dialog in client.iter_dialogs(limit=dialog_scan_limit):
            chats.append(normalize_chat(getattr(dialog, "entity", None)))

        matches = rank_existing_dialogs(chats, query, limit)
        fallback_used = not matches
        if fallback_used:
            result: Any = await client(SearchRequest(q=query, limit=limit))
            seen: set[tuple[str | None, int | None]] = set()
            for entity in [*(result.users or []), *(result.chats or [])]:
                chat = normalize_chat(entity)
                identity = (chat.get("type"), chat.get("id"))
                if identity in seen:
                    continue
                seen.add(identity)
                matches.append(
                    {
                        "type": chat.get("type"),
                        "chat": chat,
                        "source": "native-search",
                        "match": "native",
                    }
                )

    return _ok(
        "tg.find-dialog",
        {
            "matches": matches[:limit],
            "searched_dialogs": len(chats),
            "fallback_used": fallback_used,
        },
        {"query": query, "limit": limit, "profile": config.profile},
    )


async def wait_next_message(
    config: ResolvedTgConfig, peers: list[str], timeout_seconds: float, full: bool
) -> CommandResult:
    if timeout_seconds < 0:
        raise _error("invalid_timeout", "timeout_seconds must be >= 0 (0 = infinite)", exit_code=2)

    infinite: bool = timeout_seconds == 0
    started_at: datetime = datetime.now(UTC)

    async with telegram_client(config) as client:
        me: Any = await client.get_me()

        peer_data: list[dict[str, Any]] = []
        for peer in peers:
            entity: Any = await _resolve_peer_entity(client, peer)
            is_self_chat: bool = bool(
                peer.lower() in {"me", "self"}
                or isinstance(entity, InputPeerSelf)
                or (me is not None and getattr(entity, "id", None) == getattr(me, "id", None))
            )
            latest: Any = await client.get_messages(entity, limit=1)
            baseline_id: int = 0
            if latest:
                first: Any = latest[0] if isinstance(latest, list) else latest
                baseline_id = int(getattr(first, "id", 0) or 0)
            peer_data.append({
                "peer": peer,
                "entity": entity,
                "is_self_chat": is_self_chat,
                "baseline_id": baseline_id,
            })

        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        deadline: float = loop.time() + timeout_seconds if not infinite else float("inf")
        while True:
            remaining: float = deadline - loop.time()
            if not infinite and remaining <= 0:
                raise _error(
                    "timeout",
                    f"Timed out waiting for the next incoming message after {timeout_seconds} seconds",
                    exit_code=4,
                )

            for pd in peer_data:
                messages: Any = await client.get_messages(pd["entity"], limit=50)
                candidates: list[Any] = []
                for message in list(messages or []):
                    message_id: int = int(getattr(message, "id", 0) or 0)
                    message_date: datetime | None = getattr(message, "date", None)
                    is_after_baseline: bool = message_id > pd["baseline_id"]
                    is_after_start: bool = bool(
                        message_date is not None and message_date.astimezone(UTC) >= started_at
                    )
                    if not is_after_baseline and not is_after_start:
                        continue
                    if not pd["is_self_chat"] and bool(getattr(message, "out", False)):
                        continue
                    candidates.append(message)

                if candidates:
                    message = min(candidates, key=lambda item: int(getattr(item, "id", 0) or 0))
                    return _ok(
                        "tg.wait-next",
                        {"message": normalize_message(message, chat_entity=pd["entity"], full=full)},
                        {
                            "peer": pd["peer"],
                            "peers": peers,
                            "timeout_seconds": timeout_seconds,
                            "profile": config.profile,
                            "full": full,
                            "baseline_message_id": pd["baseline_id"],
                        },
                    )

            await asyncio.sleep(1.0 if infinite else min(1.0, max(0.0, remaining)))


async def media_info(
    config: ResolvedTgConfig, peer: str, message_id: int, full: bool
) -> CommandResult:
    async with telegram_client(config) as client:
        entity: Any = await _resolve_peer_entity(client, peer)
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
    config: ResolvedTgConfig, peer: str, message_ids: list[int], output_dir: str | None, full: bool
) -> CommandResult:
    target_dir: Path = Path(output_dir).expanduser() if output_dir else config.download_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    async with telegram_client(config) as client:
        entity: Any = await _resolve_peer_entity(client, peer)
        results: list[dict[str, Any]] = []
        for mid in message_ids:
            message: Any = await client.get_messages(entity, ids=mid)
            if not message:
                results.append({"message_id": mid, "error": "not_found"})
                continue
            media: dict[str, Any] | None = normalize_media(message)
            if media is None:
                results.append({"message_id": mid, "error": "no_media"})
                continue
            downloaded_any: Any = await client.download_media(message, file=str(target_dir))
            downloaded: str | None = downloaded_any if isinstance(downloaded_any, str) else None
            entry: dict[str, Any] = {
                "path": str(Path(downloaded).expanduser().resolve()) if downloaded else None,
                "message_id": mid,
                "media": media,
            }
            if full:
                entry["message"] = normalize_message(message, chat_entity=entity, full=True)
            results.append(entry)
        return _ok(
            "tg.media-download",
            {"items": results, "chat": normalize_chat(entity)},
            {
                "peer": peer,
                "message_ids": message_ids,
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
        source_entity: Any = await _resolve_peer_entity(client, source_peer)
        target_entity: Any = await _resolve_peer_entity(client, target_peer)
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
