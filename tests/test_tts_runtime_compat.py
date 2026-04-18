from __future__ import annotations

import pytest

from assistant_tools.tts import _load_model
from assistant_tools.utils import AssistantToolsError


class _FakeKittenTTS:
    def __init__(self, model: str) -> None:
        self.model = model


def test_load_model_uses_upstream_signature_without_backend() -> None:
    instance = _load_model(_FakeKittenTTS, "KittenML/kitten-tts-micro-0.8", "auto")
    assert isinstance(instance, _FakeKittenTTS)
    assert instance.model == "KittenML/kitten-tts-micro-0.8"


@pytest.mark.parametrize("backend", ["cpu", "cuda", "amd_gpu"])
def test_load_model_rejects_explicit_backend_for_current_kittentts(backend: str) -> None:
    with pytest.raises(AssistantToolsError) as exc:
        _load_model(_FakeKittenTTS, "KittenML/kitten-tts-micro-0.8", backend)

    assert exc.value.error_type == "unsupported_option"
