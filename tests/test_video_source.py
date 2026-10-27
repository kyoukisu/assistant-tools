from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from assistant_tools import video
from assistant_tools.utils import AssistantToolsError


def test_is_remote_source_distinguishes_urls_and_paths() -> None:
    assert video.is_remote_source("https://youtube.com/shorts/abc")
    assert video.is_remote_source(" HTTP://example.com/clip.mp4 ")
    assert not video.is_remote_source("./clip.mp4")
    assert not video.is_remote_source(Path("/tmp/clip.mp4").as_posix())
    assert not video.is_remote_source("ftp://example.com/clip.mp4")


def test_source_slug_keeps_url_and_path_tails() -> None:
    assert video.source_slug("https://youtube.com/shorts/Rfrsxszvibw?t=4") == "Rfrsxszvibw"
    assert video.source_slug("https://example.com/clips/party.mp4#t=3") == "party.mp4"
    assert video.source_slug("https://example.com/") == "example.com"
    assert video.source_slug("/tmp/some clip.mp4") == "some clip"


def test_read_video_metadata_truncates_description_and_tags(tmp_path: Path) -> None:
    info_path: Path = tmp_path / "source.info.json"
    info_path.write_text(
        json.dumps(
            {
                "id": "abc",
                "title": "That game is so fun whilst being drunk",
                "channel": "ClipGoblin",
                "upload_date": "20260928",
                "duration": 22.5,
                "description": "x" * 5000,
                "tags": [f"tag{index}" for index in range(40)],
                "webpage_url": "https://youtube.com/shorts/abc",
            }
        )
    )

    metadata: dict[str, Any] = video._read_video_metadata(info_path)

    assert metadata["title"] == "That game is so fun whilst being drunk"
    assert metadata["channel"] == "ClipGoblin"
    assert metadata["duration_seconds"] == 22.5
    assert metadata["description"] == "x" * video.METADATA_DESCRIPTION_LIMIT
    assert metadata["description_truncated"] is True
    assert len(metadata["tags"]) == video.METADATA_TAG_LIMIT


def test_read_video_metadata_tolerates_missing_and_broken_files(tmp_path: Path) -> None:
    assert video._read_video_metadata(tmp_path / "absent.json") == {}
    broken: Path = tmp_path / "broken.info.json"
    broken.write_text("{not json")
    assert video._read_video_metadata(broken) == {}
    wrong_shape: Path = tmp_path / "list.info.json"
    wrong_shape.write_text("[]")
    assert video._read_video_metadata(wrong_shape) == {}


def test_download_remote_video_uses_bounded_flags_and_reads_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        destination: Path = Path(command[command.index("--output") + 1]).parent
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "source.mp4").write_bytes(b"media")
        (destination / "source.info.json").write_text(json.dumps({"title": "Clip", "id": "abc"}))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(video.subprocess, "run", fake_run)

    media_path, metadata = video.download_remote_video(
        source="https://youtube.com/shorts/abc",
        destination=tmp_path / "source",
        proxy="http://127.0.0.1:7897",
    )

    command: list[str] = captured["command"]
    assert command[1:3] == ["-m", "yt_dlp"]
    assert "--no-playlist" in command
    assert command[command.index("--max-filesize") + 1] == str(video.DOWNLOAD_MAX_BYTES)
    assert f"duration<?{video.DOWNLOAD_MAX_DURATION_SECONDS}" in command
    assert command[-1] == "https://youtube.com/shorts/abc"
    assert media_path.name == "source.mp4"
    assert metadata["title"] == "Clip"


def test_download_remote_video_ignores_sidecar_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        destination: Path = Path(command[command.index("--output") + 1]).parent
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "source.info.json").write_text("{}")
        (destination / "source.webm").write_bytes(b"media")
        (destination / "source.webm.part").write_bytes(b"partial")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(video.subprocess, "run", fake_run)

    media_path, _ = video.download_remote_video(
        source="https://example.com/clip", destination=tmp_path / "source"
    )

    assert media_path.name == "source.webm"


def test_download_reports_missing_downloader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="/usr/bin/python3: No module named yt_dlp"
        )

    monkeypatch.setattr(video.subprocess, "run", fake_run)

    with pytest.raises(AssistantToolsError) as excinfo:
        video.download_remote_video(source="https://example.com/clip", destination=tmp_path)

    assert excinfo.value.error_type == "missing_dependency"
    assert "yt-dlp" in str(excinfo.value)


def test_download_failure_surfaces_last_stderr_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="noise\nerror: Video unavailable"
        )

    monkeypatch.setattr(video.subprocess, "run", fake_run)

    with pytest.raises(AssistantToolsError) as excinfo:
        video.download_remote_video(source="https://example.com/clip", destination=tmp_path)

    assert excinfo.value.error_type == "download_failed"
    assert "Video unavailable" in str(excinfo.value)


def test_contact_sheet_tiles_frames_in_timestamp_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        Path(command[-1]).write_bytes(b"sheet")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(video.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(video.subprocess, "run", fake_run)

    frames: list[video.FrameArtifact] = [
        video.FrameArtifact(index=index, timestamp_seconds=float(index), timestamp_hms="", path=f"/f{index}.jpg")
        for index in range(1, 6)
    ]

    sheet: video.ContactSheet | None = video.build_contact_sheet(
        frames=frames,
        output_path=tmp_path / "sheet.jpg",
        columns=3,
    )

    assert sheet is not None
    assert sheet.columns == 3
    assert sheet.rows == 2
    assert sheet.frame_indexes == [1, 2, 3, 4, 5]
    command: list[str] = captured["command"]
    filter_graph: str = command[command.index("-filter_complex") + 1]
    assert "hstack=inputs=3" in filter_graph
    assert "hstack=inputs=2" in filter_graph
    assert "vstack=inputs=2" in filter_graph


def test_contact_sheet_skips_single_frame(tmp_path: Path) -> None:
    frames: list[video.FrameArtifact] = [
        video.FrameArtifact(index=1, timestamp_seconds=0.0, timestamp_hms="", path="/f1.jpg")
    ]

    assert video.build_contact_sheet(frames=frames, output_path=tmp_path / "sheet.jpg") is None


def test_build_contact_sheets_chunks_long_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        Path(command[-1]).write_bytes(b"sheet")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(video.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(video.subprocess, "run", fake_run)

    frames: list[video.FrameArtifact] = [
        video.FrameArtifact(index=index, timestamp_seconds=float(index), timestamp_hms="", path=f"/f{index}.jpg")
        for index in range(1, 6)
    ]

    sheets: list[video.ContactSheet] = video.build_contact_sheets(
        frames=frames,
        output_dir=tmp_path,
        max_frames_per_sheet=3,
    )

    assert [sheet.path for sheet in sheets] == [
        str((tmp_path / "contact-sheet-1.jpg").resolve()),
        str((tmp_path / "contact-sheet-2.jpg").resolve()),
    ]
    assert sheets[0].frame_indexes == [1, 2, 3]
    assert sheets[1].frame_indexes == [4, 5]
