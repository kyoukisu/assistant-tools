from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from typing import AsyncGenerator
from typing import AsyncIterator

from assistant_tools.tg import commands
from assistant_tools.tg.dialog_search import normalize_words
from assistant_tools.tg.dialog_search import rank_existing_dialogs


def _identity_chat(entity: Any) -> dict[str, Any]:
    return entity


def test_normalize_words_transliterates_cyrillic() -> None:
    assert normalize_words("@Серёга Джун") == ["serega", "jun"]


def test_rank_existing_dialogs_finds_latin_name_from_cyrillic_query() -> None:
    chats = [
        {"id": 1, "type": "supergroup", "title": "e/acc chat", "username": "eaccchat"},
        {"id": 2, "type": "private", "title": "Sergea", "username": "sergeaJTK"},
    ]

    matches = rank_existing_dialogs(chats, "серега", 20)

    assert [match["chat"]["id"] for match in matches] == [2]
    assert matches[0]["source"] == "existing-dialog"
    assert matches[0]["match"] == "fuzzy"


def test_rank_existing_dialogs_uses_word_order_to_break_ambiguous_match() -> None:
    chats = [
        {"id": 1, "type": "supergroup", "title": "DD | Junior | GPT", "username": None},
        {"id": 2, "type": "supergroup", "title": "Dorogo DAO | DD и шлюха приват", "username": None},
    ]

    matches = rank_existing_dialogs(chats, "дд дао джун", 20)

    assert [match["chat"]["id"] for match in matches[:2]] == [1, 2]


def test_rank_existing_dialogs_rejects_short_token_false_positives() -> None:
    chats = [
        {"id": 1, "type": "supergroup", "title": "e/acc chat", "username": "eaccchat"},
        {"id": 2, "type": "channel", "title": "Iced's House of Degeneracy", "username": "houseofdegeneracy"},
        {"id": 3, "type": "channel", "title": "Жизнь с Паштетом", "username": "pa_pashtet"},
    ]

    assert rank_existing_dialogs(chats, "серега", 20) == []


def test_find_dialog_returns_existing_match_without_native_search(monkeypatch: Any) -> None:
    class FakeClient:
        native_search_called = False

        async def iter_dialogs(self, limit: int) -> AsyncIterator[Any]:
            assert limit == 500
            for chat in [
                {"id": 1, "type": "channel", "title": "Unrelated", "username": "other"},
                {"id": 2, "type": "private", "title": "Sergea", "username": "sergeaJTK"},
            ]:
                yield SimpleNamespace(entity=chat)

        async def __call__(self, request: Any) -> Any:
            self.native_search_called = True
            raise AssertionError(f"unexpected native search: {request}")

    client = FakeClient()

    @asynccontextmanager
    async def fake_telegram_client(config: Any) -> AsyncGenerator[FakeClient]:
        yield client

    monkeypatch.setattr(commands, "telegram_client", fake_telegram_client)
    monkeypatch.setattr(commands, "normalize_chat", _identity_chat)
    config: Any = SimpleNamespace(profile="main")

    result = asyncio.run(commands.find_dialog(config, query="серега", limit=20))

    assert result.ok
    assert not client.native_search_called
    assert result.data is not None
    assert result.data["searched_dialogs"] == 2
    assert not result.data["fallback_used"]
    assert len(result.data["matches"]) == 1
    match = result.data["matches"][0]
    assert match["type"] == "private"
    assert match["chat"]["id"] == 2
    assert match["source"] == "existing-dialog"
    assert match["match"] == "fuzzy"
    assert match["recency_rank"] == 2
    assert match["score"] >= 0.8


def test_find_dialog_marks_native_fallback_unverified(monkeypatch: Any) -> None:
    class FakeClient:
        async def iter_dialogs(self, limit: int) -> AsyncIterator[Any]:
            if False:
                yield None

        async def __call__(self, request: Any) -> Any:
            return SimpleNamespace(
                users=[{"id": 9, "type": "private", "title": "Global", "username": "global"}],
                chats=[],
            )

    @asynccontextmanager
    async def fake_telegram_client(config: Any) -> AsyncGenerator[FakeClient]:
        yield FakeClient()

    monkeypatch.setattr(commands, "telegram_client", fake_telegram_client)
    monkeypatch.setattr(commands, "normalize_chat", _identity_chat)
    config: Any = SimpleNamespace(profile="main")

    result = asyncio.run(commands.find_dialog(config, query="missing", limit=20))

    assert result.ok
    assert result.data == {
        "matches": [
            {
                "type": "private",
                "chat": {
                    "id": 9,
                    "type": "private",
                    "title": "Global",
                    "username": "global",
                },
                "source": "native-search",
                "match": "native",
            }
        ],
        "searched_dialogs": 0,
        "fallback_used": True,
    }
