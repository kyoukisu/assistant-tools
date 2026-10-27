from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
from typing import cast

from assistant_tools import stt as stt_provider
from assistant_tools.utils import AssistantToolsError
from assistant_tools.utils import ensure_path_exists
from assistant_tools.utils import require_stt_api_key


REMOTE_SCHEMES: tuple[str, ...] = ("http://", "https://")
DOWNLOAD_MAX_DURATION_SECONDS: int = 1200
DOWNLOAD_MAX_BYTES: int = 512 * 1024 * 1024
DOWNLOAD_MAX_HEIGHT: int = 720
DOWNLOAD_TIMEOUT_SECONDS: float = 300.0
METADATA_DESCRIPTION_LIMIT: int = 1200
METADATA_TAG_LIMIT: int = 20
SHEET_COLUMNS: int = 4
SHEET_CELL_WIDTH: int = 360
SHEET_MAX_FRAMES: int = 16


@dataclass(slots=True)
class MediaProbe:
    duration_seconds: float
    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None


@dataclass(slots=True)
class FrameArtifact:
    index: int
    timestamp_seconds: float
    timestamp_hms: str
    path: str


@dataclass(slots=True)
class ContactSheet:
    index: int
    path: str
    columns: int
    rows: int
    frame_indexes: list[int]


@dataclass(slots=True)
class SourceInfo:
    kind: str
    url: str | None
    metadata: dict[str, Any]


@dataclass(slots=True)
class TranscriptSegment:
    index: int
    start_seconds: float
    end_seconds: float
    start_hms: str
    end_hms: str
    text: str


def require_binary(name: str) -> str:
    resolved: str | None = shutil.which(name)
    if resolved is None:
        raise AssistantToolsError(
            f"Required binary is not installed or not on PATH: {name}",
            error_type="missing_dependency",
            exit_code=2,
        )
    return resolved


def format_timestamp_hms(seconds: float) -> str:
    total_millis: int = max(0, int(round(seconds * 1000)))
    hours: int = total_millis // 3_600_000
    minutes: int = (total_millis % 3_600_000) // 60_000
    secs: int = (total_millis % 60_000) // 1000
    millis: int = total_millis % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def probe_media(input_path: Path) -> MediaProbe:
    ffprobe_path: str = require_binary("ffprobe")
    command: list[str] = [
        ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(input_path),
    ]
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr: str = completed.stderr.strip()
        raise AssistantToolsError(
            f"ffprobe failed for {input_path}: {stderr or 'unknown error'}",
            error_type="probe_failed",
            exit_code=2,
        )

    raw: dict[str, Any] = json.loads(completed.stdout)
    streams: list[dict[str, Any]] = [
        item for item in raw.get("streams", []) if isinstance(item, dict)
    ]
    format_section: dict[str, Any] = raw.get("format", {}) if isinstance(raw.get("format"), dict) else {}

    video_streams: list[dict[str, Any]] = [
        stream for stream in streams if stream.get("codec_type") == "video"
    ]
    audio_streams: list[dict[str, Any]] = [
        stream for stream in streams if stream.get("codec_type") == "audio"
    ]
    primary_video: dict[str, Any] | None = video_streams[0] if video_streams else None
    primary_audio: dict[str, Any] | None = audio_streams[0] if audio_streams else None

    duration_seconds: float = _parse_duration_seconds(format_section, streams)
    width: int | None = int(primary_video["width"]) if primary_video and primary_video.get("width") else None
    height: int | None = int(primary_video["height"]) if primary_video and primary_video.get("height") else None
    video_codec: str | None = (
        str(primary_video["codec_name"]) if primary_video and primary_video.get("codec_name") else None
    )
    audio_codec: str | None = (
        str(primary_audio["codec_name"]) if primary_audio and primary_audio.get("codec_name") else None
    )

    return MediaProbe(
        duration_seconds=duration_seconds,
        has_video=bool(video_streams),
        has_audio=bool(audio_streams),
        width=width,
        height=height,
        video_codec=video_codec,
        audio_codec=audio_codec,
    )


def _parse_duration_seconds(
    format_section: dict[str, Any],
    streams: list[dict[str, Any]],
) -> float:
    format_duration: Any = format_section.get("duration")
    if format_duration not in (None, ""):
        return max(float(format_duration), 0.0)

    stream_durations: list[float] = []
    for stream in streams:
        duration_value: Any = stream.get("duration")
        if duration_value in (None, ""):
            continue
        stream_durations.append(max(float(duration_value), 0.0))
    if stream_durations:
        return max(stream_durations)
    return 0.0


def extract_transcript_segments(transcript_payload: dict[str, Any] | None) -> list[TranscriptSegment]:
    if not transcript_payload:
        return []
    raw_segments_value: Any = transcript_payload.get("segments")
    if not isinstance(raw_segments_value, list):
        return []
    raw_segments: list[Any] = cast(list[Any], raw_segments_value)

    segments: list[TranscriptSegment] = []
    for raw_index, unknown_item in enumerate(raw_segments):
        if not isinstance(unknown_item, dict):
            continue
        item: dict[str, Any] = cast(dict[str, Any], unknown_item)
        start_value: Any = item.get("start")
        end_value: Any = item.get("end")
        if start_value is None or end_value is None:
            continue
        start_seconds: float = float(start_value)
        end_seconds: float = float(end_value)
        if end_seconds < start_seconds:
            continue
        text_value: str = str(item.get("text", "")).strip()
        segments.append(
            TranscriptSegment(
                index=raw_index,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                start_hms=format_timestamp_hms(start_seconds),
                end_hms=format_timestamp_hms(end_seconds),
                text=text_value,
            )
        )
    return segments


def extract_segment_midpoints(transcript_segments: list[TranscriptSegment]) -> list[float]:
    midpoints: list[float] = []
    for segment in transcript_segments:
        midpoints.append((segment.start_seconds + segment.end_seconds) / 2.0)
    return midpoints


def compute_frame_timestamps(
    *,
    duration_seconds: float,
    max_frames: int,
    seconds_per_frame: float,
    align_to_segments: bool,
    segment_midpoints: list[float],
    requested_timestamps: list[float] | None,
) -> list[float]:
    if max_frames < 1:
        raise AssistantToolsError(
            "max_frames must be at least 1",
            error_type="invalid_argument",
            exit_code=2,
        )
    if seconds_per_frame <= 0:
        raise AssistantToolsError(
            "seconds_per_frame must be greater than 0",
            error_type="invalid_argument",
            exit_code=2,
        )

    safe_duration: float = max(duration_seconds, 0.0)
    if safe_duration == 0.0:
        return [0.0]

    if requested_timestamps:
        unique_timestamps: list[float] = []
        seen_keys: set[int] = set()
        max_timestamp: float = max(0.0, safe_duration - 0.05)
        for raw_seconds in requested_timestamps:
            clipped_seconds: float = min(max(raw_seconds, 0.0), max_timestamp)
            key: int = int(round(clipped_seconds * 1000))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_timestamps.append(clipped_seconds)
        unique_timestamps.sort()
        if unique_timestamps:
            return unique_timestamps

    estimated_count: int = max(1, int(math.ceil(safe_duration / seconds_per_frame)))
    frame_count: int = min(max_frames, estimated_count)
    interval_seconds: float = safe_duration / frame_count
    max_timestamp: float = max(0.0, safe_duration - 0.05)

    targets: list[float] = []
    for index in range(frame_count):
        target_seconds: float = min((index + 0.5) * interval_seconds, max_timestamp)
        targets.append(target_seconds)

    if not align_to_segments or not segment_midpoints:
        return targets

    used_segment_indexes: set[int] = set()
    aligned_targets: list[float] = []
    for target_seconds in targets:
        window_seconds: float = max(interval_seconds * 0.5, 0.25)
        best_index: int | None = None
        best_distance: float | None = None
        for segment_index, midpoint_seconds in enumerate(segment_midpoints):
            if segment_index in used_segment_indexes:
                continue
            distance_seconds: float = abs(midpoint_seconds - target_seconds)
            if distance_seconds > window_seconds:
                continue
            if best_distance is None or distance_seconds < best_distance:
                best_index = segment_index
                best_distance = distance_seconds
        if best_index is None:
            aligned_targets.append(target_seconds)
            continue
        used_segment_indexes.add(best_index)
        aligned_seconds: float = min(max(segment_midpoints[best_index], 0.0), max_timestamp)
        aligned_targets.append(aligned_seconds)
    return aligned_targets


def extract_frames(
    *,
    input_path: Path,
    output_dir: Path,
    timestamps: list[float],
    frame_format: str,
) -> list[FrameArtifact]:
    ffmpeg_path: str = require_binary("ffmpeg")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[FrameArtifact] = []
    for frame_index, timestamp_seconds in enumerate(timestamps, start=1):
        suffix: str = frame_format.lower()
        frame_path: Path = output_dir / (
            f"frame-{frame_index:03d}-{format_timestamp_hms(timestamp_seconds).replace(':', '-')}.{suffix}"
        )
        command: list[str] = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{timestamp_seconds:.3f}",
            "-i",
            str(input_path),
            "-frames:v",
            "1",
        ]
        if suffix in {"jpg", "jpeg"}:
            command.extend(["-q:v", "2"])
        command.append(str(frame_path))
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            stderr: str = completed.stderr.strip()
            raise AssistantToolsError(
                f"ffmpeg frame extraction failed at {timestamp_seconds:.3f}s: {stderr or 'unknown error'}",
                error_type="frame_extraction_failed",
                exit_code=2,
            )
        artifacts.append(
            FrameArtifact(
                index=frame_index,
                timestamp_seconds=timestamp_seconds,
                timestamp_hms=format_timestamp_hms(timestamp_seconds),
                path=str(frame_path.resolve()),
            )
        )
    return artifacts


def extract_audio_track(*, input_path: Path, output_path: Path) -> Path:
    ffmpeg_path: str = require_binary("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command: list[str] = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr: str = completed.stderr.strip()
        raise AssistantToolsError(
            f"ffmpeg audio extraction failed: {stderr or 'unknown error'}",
            error_type="audio_extraction_failed",
            exit_code=2,
        )
    return output_path


def transcribe_audio(
    *,
    audio_path: Path,
    timeout_seconds: float,
    model: str,
    language: str,
    timestamps: str,
    temperature: float,
    prompt: str,
    proxy: str | None,
    api_key: str = "",
    url: str | None = None,
) -> dict[str, Any]:
    resolved_api_key: str = require_stt_api_key(api_key)
    return stt_provider.transcribe_file(
        api_key=resolved_api_key,
        source=str(audio_path),
        timeout_seconds=max(float(timeout_seconds), 180.0),
        model=model,
        language=language,
        timestamps=timestamps,
        temperature=temperature,
        prompt=prompt,
        proxy=proxy,
        url=url,
    )


def find_nearest_segment(
    *,
    timestamp_seconds: float,
    transcript_segments: list[TranscriptSegment],
) -> TranscriptSegment | None:
    best_segment: TranscriptSegment | None = None
    best_distance: float | None = None
    for segment in transcript_segments:
        if segment.start_seconds <= timestamp_seconds <= segment.end_seconds:
            return segment
        if timestamp_seconds < segment.start_seconds:
            distance_seconds: float = segment.start_seconds - timestamp_seconds
        else:
            distance_seconds = timestamp_seconds - segment.end_seconds
        if best_distance is None or distance_seconds < best_distance:
            best_segment = segment
            best_distance = distance_seconds
    return best_segment


def is_remote_source(source: str) -> bool:
    return source.strip().lower().startswith(REMOTE_SCHEMES)


def source_slug(source: str) -> str:
    """Stable, filesystem-safe run directory name for a local path or URL."""
    if not is_remote_source(source):
        return Path(source).expanduser().stem or "media"
    stripped: str = source.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    tail: str = stripped.rsplit("/", 1)[-1] or "remote"
    return tail or "remote"


def _sanitize_slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


def _read_video_metadata(info_path: Path) -> dict[str, Any]:
    try:
        raw: Any = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entry: dict[str, Any] = cast(dict[str, Any], raw)
    description: str = str(entry.get("description") or "")
    raw_tags: Any = entry.get("tags")
    tags: list[str] = []
    if isinstance(raw_tags, list):
        tags = [str(tag) for tag in cast(list[Any], raw_tags)][:METADATA_TAG_LIMIT]
    duration: Any = entry.get("duration")
    metadata: dict[str, Any] = {
        "video_id": entry.get("id"),
        "title": entry.get("title"),
        "channel": entry.get("channel") or entry.get("uploader"),
        "upload_date": entry.get("upload_date"),
        "duration_seconds": float(duration) if isinstance(duration, (int, float)) else None,
        "description": description[:METADATA_DESCRIPTION_LIMIT],
        "description_truncated": len(description) > METADATA_DESCRIPTION_LIMIT,
        "tags": tags,
        "webpage_url": entry.get("webpage_url") or entry.get("original_url"),
        "thumbnail_url": entry.get("thumbnail"),
    }
    return metadata


def _ytdlp_command() -> list[str]:
    return [sys.executable, "-m", "yt_dlp"]


def download_remote_video(
    *,
    source: str,
    destination: Path,
    proxy: str | None = None,
    cookies_from_browser: str | None = None,
    cookies_file: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Fetch one bounded video and its metadata into destination."""
    destination.mkdir(parents=True, exist_ok=True)
    command: list[str] = _ytdlp_command() + [
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "--write-info-json",
        "--format",
        f"bv*[height<={DOWNLOAD_MAX_HEIGHT}]+ba/b[height<={DOWNLOAD_MAX_HEIGHT}]/b",
        "--merge-output-format",
        "mp4",
        "--max-filesize",
        str(DOWNLOAD_MAX_BYTES),
        # duration<?N passes when the site reports no duration (direct media links).
        "--match-filter",
        f"duration<?{DOWNLOAD_MAX_DURATION_SECONDS}",
        "--retries",
        "1",
        "--socket-timeout",
        "20",
    ]
    if proxy:
        command.extend(["--proxy", proxy])
    if cookies_from_browser:
        command.extend(["--cookies-from-browser", cookies_from_browser])
    if cookies_file:
        command.extend(["--cookies", cookies_file])
    command.extend(["--output", str(destination / "source.%(ext)s"), source])

    try:
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as err:
        raise AssistantToolsError(
            f"Video download exceeded {DOWNLOAD_TIMEOUT_SECONDS:.0f}s: {source}",
            error_type="download_timeout",
            exit_code=2,
        ) from err

    if completed.returncode != 0:
        detail: str = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else ""
        if "Sign in to confirm" in completed.stderr or "not a bot" in completed.stderr:
            raise AssistantToolsError(
                f"YouTube requires account cookies from this network: {source}. "
                "Set [video] cookies_from_browser = \"firefox\" or pass --cookies-from-browser / --cookies.",
                error_type="download_requires_cookies",
                exit_code=2,
            )
        if "No module named yt_dlp" in completed.stderr:
            raise AssistantToolsError(
                "yt-dlp is not installed for this Python environment; reinstall kit with its declared dependencies",
                error_type="missing_dependency",
                exit_code=2,
            )
        raise AssistantToolsError(
            f"Video download failed for {source}: {detail or 'unknown error'} "
            f"(bounds: {DOWNLOAD_MAX_DURATION_SECONDS}s, {DOWNLOAD_MAX_BYTES // (1024 * 1024)} MiB)",
            error_type="download_failed",
            exit_code=2,
        )

    media_files: list[Path] = [
        path
        for path in sorted(destination.glob("source.*"))
        if not path.name.endswith((".info.json", ".part", ".ytdl"))
    ]
    if not media_files:
        raise AssistantToolsError(
            f"Video download produced no media file for {source}; "
            f"the {DOWNLOAD_MAX_DURATION_SECONDS}s / {DOWNLOAD_MAX_BYTES // (1024 * 1024)} MiB bounds may have skipped it",
            error_type="download_failed",
            exit_code=2,
        )
    metadata: dict[str, Any] = _read_video_metadata(destination / "source.info.json")
    return media_files[0], metadata


def build_contact_sheet(
    *,
    frames: list[FrameArtifact],
    output_path: Path,
    columns: int = SHEET_COLUMNS,
    cell_width: int = SHEET_CELL_WIDTH,
    cell_height: int | None = None,
) -> ContactSheet | None:
    """Compose already extracted frames into one timestamp-ordered grid image."""
    if len(frames) < 2:
        return None
    ffmpeg_path: str = require_binary("ffmpeg")
    sheet_columns: int = max(1, min(columns, len(frames)))
    rows: int = math.ceil(len(frames) / sheet_columns)
    width: int = max(64, cell_width)
    height: int = max(64, cell_height if cell_height is not None else width)

    command: list[str] = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y"]
    for frame in frames:
        command.extend(["-i", frame.path])

    filters: list[str] = []
    for index in range(len(frames)):
        filters.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101010[c{index}]"
        )

    row_labels: list[str] = []
    for row_index in range(rows):
        members: list[str] = [
            f"c{index}" for index in range(len(frames)) if index // sheet_columns == row_index
        ]
        if len(members) == 1:
            row_labels.append(members[0])
            continue
        inputs: str = "".join(f"[{member}]" for member in members)
        filters.append(f"{inputs}hstack=inputs={len(members)}[r{row_index}]")
        row_labels.append(f"r{row_index}")

    if len(row_labels) == 1:
        filters.append(f"[{row_labels[0]}]null[out]")
    else:
        inputs = "".join(f"[{label}]" for label in row_labels)
        filters.append(f"{inputs}vstack=inputs={len(row_labels)}[out]")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(output_path),
        ]
    )
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr: str = completed.stderr.strip()
        raise AssistantToolsError(
            f"ffmpeg contact sheet failed: {stderr or 'unknown error'}",
            error_type="contact_sheet_failed",
            exit_code=2,
        )
    return ContactSheet(
        index=1,
        path=str(output_path.resolve()),
        columns=sheet_columns,
        rows=rows,
        frame_indexes=[frame.index for frame in frames],
    )


def build_contact_sheets(
    *,
    frames: list[FrameArtifact],
    output_dir: Path,
    columns: int = SHEET_COLUMNS,
    cell_width: int = SHEET_CELL_WIDTH,
    cell_height: int | None = None,
    max_frames_per_sheet: int = SHEET_MAX_FRAMES,
) -> list[ContactSheet]:
    sheets: list[ContactSheet] = []
    for offset in range(0, len(frames), max_frames_per_sheet):
        chunk: list[FrameArtifact] = frames[offset : offset + max_frames_per_sheet]
        sheet: ContactSheet | None = build_contact_sheet(
            frames=chunk,
            output_path=output_dir / f"contact-sheet-{len(sheets) + 1}.jpg",
            columns=columns,
            cell_width=cell_width,
            cell_height=cell_height,
        )
        if sheet is None:
            continue
        sheets.append(
            ContactSheet(
                index=len(sheets) + 1,
                path=sheet.path,
                columns=sheet.columns,
                rows=sheet.rows,
                frame_indexes=sheet.frame_indexes,
            )
        )
    return sheets


def create_run_directory(base_output_dir: Path, slug: str) -> Path:
    timestamp_slug: str = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_stem: str = _sanitize_slug(slug) or "media"
    run_dir: Path = base_output_dir.expanduser() / f"{safe_stem}-{timestamp_slug}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def analyze_video(
    *,
    source: str,
    output_dir: str,
    max_frames: int,
    seconds_per_frame: float,
    frame_format: str,
    align_to_segments: bool,
    requested_timestamps: list[float] | None,
    transcribe: bool,
    timeout_seconds: float,
    model: str,
    language: str,
    timestamps: str,
    temperature: float,
    prompt: str,
    proxy: str | None,
    contact_sheet: bool = True,
    cookies_from_browser: str | None = None,
    cookies_file: str | None = None,
    api_key: str = "",
    url: str | None = None,
) -> dict[str, Any]:
    run_dir: Path = create_run_directory(Path(output_dir), source_slug(source))
    source_info: SourceInfo = SourceInfo(kind="local", url=None, metadata={})
    if is_remote_source(source):
        input_path, metadata = download_remote_video(
            source=source,
            destination=run_dir / "source",
            proxy=proxy,
            cookies_from_browser=cookies_from_browser,
            cookies_file=cookies_file,
        )
        source_info = SourceInfo(kind="url", url=source.strip(), metadata=metadata)
    else:
        input_path = ensure_path_exists(source)

    probe: MediaProbe = probe_media(input_path)
    if not probe.has_video:
        raise AssistantToolsError(
            f"Input file does not contain a video stream: {input_path}",
            error_type="unsupported_media",
            exit_code=2,
        )

    transcript_payload: dict[str, Any] | None = None
    audio_artifact_path: Path | None = None
    if transcribe and probe.has_audio:
        audio_artifact_path = extract_audio_track(
            input_path=input_path,
            output_path=run_dir / "audio.wav",
        )
        transcript_payload = transcribe_audio(
            audio_path=audio_artifact_path,
            timeout_seconds=timeout_seconds,
            model=model,
            language=language,
            timestamps=timestamps,
            temperature=temperature,
            prompt=prompt,
            proxy=proxy,
            api_key=api_key,
            url=url,
        )

    transcript_segments: list[TranscriptSegment] = extract_transcript_segments(transcript_payload)
    segment_midpoints: list[float] = extract_segment_midpoints(transcript_segments)
    frame_timestamps: list[float] = compute_frame_timestamps(
        duration_seconds=probe.duration_seconds,
        max_frames=max_frames,
        seconds_per_frame=seconds_per_frame,
        align_to_segments=align_to_segments,
        segment_midpoints=segment_midpoints,
        requested_timestamps=requested_timestamps,
    )
    frame_artifacts: list[FrameArtifact] = extract_frames(
        input_path=input_path,
        output_dir=run_dir / "frames",
        timestamps=frame_timestamps,
        frame_format=frame_format,
    )

    timeline: list[dict[str, Any]] = []
    for frame in frame_artifacts:
        nearest_segment: TranscriptSegment | None = find_nearest_segment(
            timestamp_seconds=frame.timestamp_seconds,
            transcript_segments=transcript_segments,
        )
        timeline_item: dict[str, Any] = {
            "frame_index": frame.index,
            "timestamp_seconds": frame.timestamp_seconds,
            "timestamp_hms": frame.timestamp_hms,
            "path": frame.path,
            "nearest_transcript_segment": None,
        }
        if nearest_segment is not None:
            timeline_item["nearest_transcript_segment"] = {
                "index": nearest_segment.index,
                "start_seconds": nearest_segment.start_seconds,
                "end_seconds": nearest_segment.end_seconds,
                "start_hms": nearest_segment.start_hms,
                "end_hms": nearest_segment.end_hms,
                "text": nearest_segment.text,
            }
        timeline.append(timeline_item)

    contact_sheets: list[ContactSheet] = []
    if contact_sheet:
        cell_height: int | None = None
        if probe.width and probe.height:
            cell_height = max(64, round(SHEET_CELL_WIDTH * probe.height / probe.width))
        contact_sheets = build_contact_sheets(
            frames=frame_artifacts,
            output_dir=run_dir,
            cell_height=cell_height,
        )

    manifest_path: Path = run_dir / "manifest.json"
    payload: dict[str, Any] = {
        "input": str(input_path.resolve()),
        "source": {
            "kind": source_info.kind,
            "url": source_info.url,
            "metadata": source_info.metadata,
        },
        "run_dir": str(run_dir.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "probe": asdict(probe),
        "sampling": {
            "max_frames": max_frames,
            "seconds_per_frame": seconds_per_frame,
            "align_to_segments": align_to_segments,
            "requested_timestamps": requested_timestamps or [],
            "actual_frames": len(frame_artifacts),
            "frame_format": frame_format.lower(),
        },
        "audio": {
            "transcribe_requested": transcribe,
            "has_audio": probe.has_audio,
            "audio_artifact_path": str(audio_artifact_path.resolve()) if audio_artifact_path else None,
        },
        "frames": [asdict(item) for item in frame_artifacts],
        "contact_sheets": [asdict(sheet) for sheet in contact_sheets],
        "timeline": timeline,
        "transcript": {
            "text": str(transcript_payload.get("text", "")) if transcript_payload else "",
            "segments": [asdict(item) for item in transcript_segments],
            "payload": transcript_payload,
        },
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload
