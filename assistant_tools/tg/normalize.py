# pyright: reportMissingTypeStubs=false

from __future__ import annotations

from datetime import datetime
from typing import Any

from telethon.tl.custom.dialog import Dialog
from telethon.tl.custom.message import Message
from telethon.tl.types import Channel
from telethon.tl.types import Chat
from telethon.tl.types import DocumentAttributeAudio
from telethon.tl.types import DocumentAttributeFilename
from telethon.tl.types import DocumentAttributeSticker
from telethon.tl.types import DocumentAttributeVideo
from telethon.tl.types import User

from assistant_tools.tg.client import build_message_link


def iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone().isoformat()


def _chat_type(entity: Any) -> str | None:
    if isinstance(entity, User):
        return "bot" if entity.bot else "private"
    if isinstance(entity, Channel):
        return "supergroup" if entity.megagroup else "channel"
    if isinstance(entity, Chat):
        return "group"
    return None


def _chat_title(entity: Any) -> str | None:
    if isinstance(entity, User):
        first_name: str = entity.first_name or ""
        last_name: str = entity.last_name or ""
        title: str = " ".join(part for part in [first_name, last_name] if part).strip()
        return title or entity.username
    return getattr(entity, "title", None)


def normalize_chat(entity: Any) -> dict[str, Any]:
    if entity is None:
        return {}
    return {
        "id": getattr(entity, "id", None),
        "type": _chat_type(entity),
        "title": _chat_title(entity),
        "username": getattr(entity, "username", None),
        "is_forum": bool(getattr(entity, "forum", False)),
    }


def normalize_user(user: Any) -> dict[str, Any] | None:
    if user is None:
        return None
    first_name: str = str(getattr(user, "first_name", "") or "")
    last_name: str = str(getattr(user, "last_name", "") or "")
    display_name: str = " ".join(part for part in [first_name, last_name] if part).strip()
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "first_name": first_name or None,
        "last_name": last_name or None,
        "display_name": display_name or getattr(user, "username", None),
        "is_bot": bool(getattr(user, "bot", False)),
    }


def _document_file_name(document: Any) -> str | None:
    attributes: list[Any] = list(getattr(document, "attributes", []) or [])
    for attr in attributes:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return None


def _media_kind(message: Message) -> str | None:
    if message.photo is not None:
        return "photo"
    if message.video is not None:
        return "video"
    if message.document is not None:
        attributes: list[Any] = list(getattr(message.document, "attributes", []) or [])
        for attr in attributes:
            if isinstance(attr, DocumentAttributeSticker):
                return "sticker"
            if isinstance(attr, DocumentAttributeAudio):
                return "voice" if getattr(attr, "voice", False) else "audio"
            if isinstance(attr, DocumentAttributeVideo):
                return "video_note" if getattr(attr, "round_message", False) else "video"
        mime_type: str | None = getattr(message.document, "mime_type", None)
        if mime_type == "image/gif":
            return "animation"
        return "document"
    return None


def normalize_media(message: Message) -> dict[str, Any] | None:
    kind: str | None = _media_kind(message)
    if kind is None:
        return None

    media_obj: Any = (
        message.photo or message.video or message.document or message.audio or message.voice
    )
    if media_obj is None:
        media_obj = message.file

    document: Any = message.document
    file_name: str | None = _document_file_name(document) if document is not None else None
    width: int | None = (
        getattr(message.photo, "sizes", [None])[-1].w
        if message.photo is not None and getattr(message.photo, "sizes", None)
        else None
    )
    height: int | None = (
        getattr(message.photo, "sizes", [None])[-1].h
        if message.photo is not None and getattr(message.photo, "sizes", None)
        else None
    )
    duration: int | None = None
    if document is not None:
        for attr in list(getattr(document, "attributes", []) or []):
            if isinstance(attr, DocumentAttributeVideo):
                duration = int(getattr(attr, "duration", 0) or 0) or None
                width = int(getattr(attr, "w", 0) or 0) or width
                height = int(getattr(attr, "h", 0) or 0) or height
            if isinstance(attr, DocumentAttributeAudio):
                duration = int(getattr(attr, "duration", 0) or 0) or duration

    return {
        "kind": kind,
        "file_id": None,
        "file_unique_id": None,
        "file_name": file_name,
        "mime_type": getattr(document, "mime_type", None) if document is not None else None,
        "file_size": getattr(media_obj, "size", None),
        "width": width,
        "height": height,
        "duration": duration,
        "has_spoiler": False,
        "has_protected_content": bool(getattr(message, "noforwards", False)),
    }


def normalize_message(message: Message, *, chat_entity: Any | None = None) -> dict[str, Any]:
    chat: Any = chat_entity or getattr(message, "chat", None)
    sender: Any = getattr(message, "sender", None)
    chat_id: int | None = getattr(chat, "id", None)
    username: str | None = getattr(chat, "username", None)
    message_id: int | None = getattr(message, "id", None)
    return {
        "chat": normalize_chat(chat),
        "message_id": message_id,
        "date": iso_datetime(getattr(message, "date", None)),
        "from": normalize_user(sender),
        "text": getattr(message, "text", None),
        "caption": getattr(message, "text", None),
        "media_type": _media_kind(message),
        "media_group_id": getattr(message, "grouped_id", None),
        "reply_to_message_id": getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None),
        "outgoing": bool(getattr(message, "out", False)),
        "mentioned": bool(getattr(message, "mentioned", False)),
        "has_protected_content": bool(getattr(message, "noforwards", False)),
        "link": build_message_link(chat_id, username, message_id),
        "media": normalize_media(message),
    }


def normalize_dialog(dialog: Dialog) -> dict[str, Any]:
    top_message: Any = getattr(dialog, "message", None)
    chat_entity: Any = getattr(dialog, "entity", None)
    return {
        "chat": normalize_chat(chat_entity),
        "top_message": normalize_message(top_message, chat_entity=chat_entity)
        if top_message is not None
        else None,
        "unread_count": getattr(dialog, "unread_count", None),
        "unread_mentions_count": getattr(dialog, "unread_mentions_count", None),
    }
