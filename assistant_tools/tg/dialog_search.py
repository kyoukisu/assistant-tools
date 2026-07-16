from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import math
from typing import Any
from typing import Literal


_CYRILLIC_TO_LATIN = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "i",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sh",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)
_MIN_TOKEN_SIMILARITY = 0.78


@dataclass(frozen=True)
class RankedDialog:
    chat: dict[str, Any]
    match: Literal["exact", "fuzzy"]
    score: float
    recency_rank: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.chat.get("type"),
            "chat": self.chat,
            "source": "existing-dialog",
            "match": self.match,
            "score": round(self.score, 4),
            "recency_rank": self.recency_rank,
        }


def normalize_words(value: object) -> list[str]:
    text = str(value or "").strip().lower().removeprefix("@").translate(
        _CYRILLIC_TO_LATIN
    )
    text = text.replace("dzh", "j")
    return "".join(character if character.isalnum() else " " for character in text).split()


def _token_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if min(len(left), len(right)) >= 3 and (
        left.startswith(right) or right.startswith(left)
    ):
        return 0.95
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _ordered_match_count(query_words: list[str], value_words: list[str]) -> int:
    previous = [0] * (len(value_words) + 1)
    for query_word in query_words:
        current = [0]
        for index, value_word in enumerate(value_words, start=1):
            if _token_similarity(query_word, value_word) >= _MIN_TOKEN_SIMILARITY:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(current[-1], previous[index]))
        previous = current
    return previous[-1]


def rank_existing_dialogs(
    chats: list[dict[str, Any]], query: str, limit: int
) -> list[dict[str, Any]]:
    query_words = normalize_words(query)
    if not query_words or limit <= 0:
        return []

    query_phrase = "".join(query_words)
    required_matches = 1 if len(query_words) == 1 else math.ceil(len(query_words) * 0.6)
    ranked: list[RankedDialog] = []
    total_chats = max(1, len(chats))

    for recency_rank, chat in enumerate(chats, start=1):
        fields = [chat.get("title"), chat.get("username")]
        field_words = [normalize_words(field) for field in fields if field]
        value_words = [word for words in field_words for word in words]
        if not value_words:
            continue

        value_phrases = ["".join(words) for words in field_words]
        exact = any(
            query_phrase == phrase
            or (
                len(query_phrase) >= 3
                and (query_phrase in phrase or phrase in query_phrase)
            )
            for phrase in value_phrases
        )
        if exact:
            ranked.append(
                RankedDialog(
                    chat=chat,
                    match="exact",
                    score=1.0,
                    recency_rank=recency_rank,
                )
            )
            continue

        best_similarities = [
            max(_token_similarity(query_word, value_word) for value_word in value_words)
            for query_word in query_words
        ]
        strong_matches = sum(
            similarity >= _MIN_TOKEN_SIMILARITY for similarity in best_similarities
        )
        if strong_matches < required_matches:
            continue

        average_similarity = sum(best_similarities) / len(best_similarities)
        ordered_coverage = _ordered_match_count(query_words, value_words) / len(
            query_words
        )
        recency = 1.0 - ((recency_rank - 1) / total_chats)
        score = 0.7 * average_similarity + 0.25 * ordered_coverage + 0.05 * recency
        if score < 0.58:
            continue

        ranked.append(
            RankedDialog(
                chat=chat,
                match="fuzzy",
                score=min(score, 0.9999),
                recency_rank=recency_rank,
            )
        )

    ranked.sort(
        key=lambda candidate: (
            candidate.match == "exact",
            candidate.score,
            -candidate.recency_rank,
        ),
        reverse=True,
    )
    return [candidate.as_dict() for candidate in ranked[:limit]]
