from __future__ import annotations

from datetime import datetime
from typing import Any


def iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone().isoformat()


def normalize_chat(chat: Any) -> dict[str, Any]:
    if chat is None:
        return {}
    title: str | None = getattr(chat, "title", None)
    if title is None:
        first_name: str = str(getattr(chat, "first_name", "") or "")
        last_name: str = str(getattr(chat, "last_name", "") or "")
        title = " ".join(part for part in [first_name, last_name] if part).strip() or None
    chat_type: Any = getattr(chat, "type", None)
    return {
        "id": getattr(chat, "id", None),
        "type": str(chat_type) if chat_type is not None else None,
        "title": title,
        "username": getattr(chat, "username", None),
        "is_forum": bool(getattr(chat, "is_forum", False)),
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
        "is_bot": bool(getattr(user, "is_bot", False)),
    }


def normalize_media(message: Any) -> dict[str, Any] | None:
    candidates: list[tuple[str, Any]] = [
        ("photo", getattr(message, "photo", None)),
        ("video", getattr(message, "video", None)),
        ("document", getattr(message, "document", None)),
        ("audio", getattr(message, "audio", None)),
        ("voice", getattr(message, "voice", None)),
        ("video_note", getattr(message, "video_note", None)),
        ("sticker", getattr(message, "sticker", None)),
        ("animation", getattr(message, "animation", None)),
    ]
    for kind, item in candidates:
        if item is not None:
            return {
                "kind": kind,
                "file_id": getattr(item, "file_id", None),
                "file_unique_id": getattr(item, "file_unique_id", None),
                "file_name": getattr(item, "file_name", None),
                "mime_type": getattr(item, "mime_type", None),
                "file_size": getattr(item, "file_size", None),
                "width": getattr(item, "width", None),
                "height": getattr(item, "height", None),
                "duration": getattr(item, "duration", None),
                "has_spoiler": bool(getattr(item, "has_spoiler", False)),
                "has_protected_content": bool(getattr(message, "has_protected_content", False)),
            }
    return None


def normalize_message(message: Any) -> dict[str, Any]:
    media_type: Any = getattr(message, "media", None)
    return {
        "chat": normalize_chat(getattr(message, "chat", None)),
        "message_id": getattr(message, "id", None),
        "date": iso_datetime(getattr(message, "date", None)),
        "from": normalize_user(getattr(message, "from_user", None)),
        "text": getattr(message, "text", None),
        "caption": getattr(message, "caption", None),
        "media_type": str(media_type) if media_type is not None else None,
        "media_group_id": getattr(message, "media_group_id", None),
        "reply_to_message_id": getattr(getattr(message, "reply_to_message", None), "id", None),
        "outgoing": bool(getattr(message, "outgoing", False)),
        "mentioned": bool(getattr(message, "mentioned", False)),
        "has_protected_content": bool(getattr(message, "has_protected_content", False)),
        "link": getattr(message, "link", None),
        "media": normalize_media(message),
    }


def normalize_dialog(dialog: Any) -> dict[str, Any]:
    top_message: Any = getattr(dialog, "top_message", None)
    return {
        "chat": normalize_chat(getattr(dialog, "chat", None)),
        "top_message": normalize_message(top_message) if top_message is not None else None,
        "unread_count": getattr(dialog, "unread_messages_count", None),
        "unread_mentions_count": getattr(dialog, "unread_mentions_count", None),
    }
