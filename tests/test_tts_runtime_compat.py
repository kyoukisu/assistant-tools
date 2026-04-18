from __future__ import annotations

from assistant_tools.tts import _load_model  # pyright: ignore[reportPrivateUsage]


class _FakeKittenTTS:
    def __init__(self, model: str) -> None:
        self.model = model


def test_load_model_uses_upstream_signature_without_backend() -> None:
    instance = _load_model(_FakeKittenTTS, "KittenML/kitten-tts-micro-0.8")
    assert isinstance(instance, _FakeKittenTTS)
    assert instance.model == "KittenML/kitten-tts-micro-0.8"
