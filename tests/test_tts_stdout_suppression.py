from __future__ import annotations

from _pytest.capture import CaptureFixture

from assistant_tools.tts import _silence_upstream  # pyright: ignore[reportPrivateUsage]


def test_silence_upstream_suppresses_stdout_and_stderr(capsys: CaptureFixture[str]) -> None:
    def noisy() -> str:
        print("stdout noise")
        import sys

        print("stderr noise", file=sys.stderr)
        return "ok"

    result = _silence_upstream(noisy)
    captured = capsys.readouterr()

    assert result == "ok"
    assert captured.out == ""
    assert captured.err == ""
