from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from assistant_tools.providers import groq as groq_provider
from assistant_tools.utils import AssistantToolsError
from assistant_tools.utils import is_url


CHUNK_SECONDS: float = 45.0
MIN_CHUNK_SECONDS: float = 12.0
MAX_CHUNK_SECONDS: float = 55.0
DEFAULT_LIMIT: int = 8
MAX_LIMIT: int = 16
WORKERS: int = 3
PAD_SECONDS: float = 0.15
MIN_SPEECH_RATIO: float = 0.08
SAMPLE_RATE: int = 16_000
SILERO_WINDOW: int = 512
CACHE_ROOT: Path = Path.home() / ".cache" / "assistant-tools" / "stt"
SMART_VERSION: str = "smart-v3"
INLINE_LIMIT_BYTES: int = 180_000


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def probe_duration(path: Path) -> float:
    completed = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    if completed.returncode != 0:
        raise AssistantToolsError(
            completed.stderr.strip() or f"ffprobe failed for {path}",
            error_type="probe_failed",
            exit_code=2,
        )
    try:
        return float(completed.stdout.strip() or 0)
    except ValueError as exc:
        raise AssistantToolsError(
            f"ffprobe returned no duration for {path}",
            error_type="probe_failed",
            exit_code=2,
        ) from exc


def format_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def cache_key(*, path: Path, model: str, language: str, prompt: str, timestamps: str) -> str:
    stat = path.stat()
    raw = "|".join(
        [
            str(path.resolve()),
            str(stat.st_size),
            str(int(stat.st_mtime)),
            model,
            language,
            timestamps,
            prompt,
            SMART_VERSION,
            str(CHUNK_SECONDS),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def prepare_source(source: Path, cache_dir: Path) -> Path:
    """Keep the original file. Chunks seek with ffmpeg -ss; do not re-encode the whole lecture."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    return source


def decode_pcm16(audio: Path, start: float = 0.0, length: float | None = None) -> bytes:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if start > 0:
        command.extend(["-ss", f"{start:.3f}"])
    command.extend(["-i", str(audio)])
    if length is not None:
        command.extend(["-t", f"{length:.3f}"])
    command.extend(
        [
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "pipe:1",
        ]
    )
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode != 0:
        raise AssistantToolsError(
            completed.stderr.decode(errors="replace").strip() or "ffmpeg pcm decode failed",
            error_type="audio_extraction_failed",
            exit_code=2,
        )
    return completed.stdout


def merge_regions(regions: list[tuple[float, float]], max_gap: float) -> list[tuple[float, float]]:
    if not regions:
        return []
    ordered = sorted(regions)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + max_gap:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def pad_regions(
    regions: list[tuple[float, float]], duration: float, pad: float
) -> list[tuple[float, float]]:
    return [
        (max(0.0, start - pad), min(duration, end + pad))
        for start, end in regions
        if end - start >= 0.12
    ]


def invert_to_speech(duration: float, silences: list[tuple[float, float]]) -> list[tuple[float, float]]:
    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        if start > cursor + 0.05:
            speech.append((cursor, start))
        cursor = max(cursor, end)
    if duration > cursor + 0.05:
        speech.append((cursor, duration))
    return speech


def ffmpeg_speech_regions(
    audio: Path, duration: float, origin: float = 0.0, length: float | None = None
) -> list[tuple[float, float]]:
    command = ["ffmpeg", "-hide_banner"]
    if origin > 0:
        command.extend(["-ss", f"{origin:.3f}"])
    command.extend(["-i", str(audio)])
    if length is not None:
        command.extend(["-t", f"{length:.3f}"])
    command.extend(["-af", "silencedetect=noise=-35dB:d=0.3", "-f", "null", "-"])
    completed = _run(command)
    starts: list[float] = []
    silences: list[tuple[float, float]] = []
    for line in (completed.stderr or "").splitlines():
        match = re.search(r"silence_start: ([0-9.]+)", line)
        if match:
            starts.append(float(match.group(1)))
            continue
        match = re.search(r"silence_end: ([0-9.]+)", line)
        if match and starts:
            silences.append((starts.pop(0), float(match.group(1))))
    local_duration = duration if length is None else min(length, max(0.0, duration - origin))
    regions = pad_regions(invert_to_speech(local_duration, silences), local_duration, PAD_SECONDS)
    return [(start + origin, end + origin) for start, end in regions]


def silero_model_path() -> Path | None:
    env = os.environ.get("KIT_SILERO_VAD", "").strip()
    if env:
        path = Path(env)
        if path.exists():
            return path
    cached = Path.home() / ".cache" / "assistant-tools" / "vad" / "silero_vad.onnx"
    if cached.exists():
        return cached
    return None


def silero_speech_regions(
    audio: Path, duration: float, origin: float = 0.0, length: float | None = None
) -> list[tuple[float, float]] | None:
    model_path = silero_model_path()
    if model_path is None:
        return None
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError:
        return None
    samples = np.frombuffer(decode_pcm16(audio, start=origin, length=length), dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return []
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    state = np.zeros((2, 1, 128), dtype=np.float32)
    sr = np.array(SAMPLE_RATE, dtype=np.int64)
    context_size = 64
    context = np.zeros(context_size, dtype=np.float32)
    probs: list[float] = []
    for offset in range(0, len(samples), SILERO_WINDOW):
        chunk = samples[offset : offset + SILERO_WINDOW]
        if chunk.size < SILERO_WINDOW:
            padded = np.zeros(SILERO_WINDOW, dtype=np.float32)
            padded[: chunk.size] = chunk
            chunk = padded
        framed = np.concatenate([context, chunk]).astype(np.float32)
        output, state = session.run(
            None,
            {
                "input": framed.reshape(1, -1),
                "state": state,
                "sr": sr,
            },
        )
        context = chunk[-context_size:]
        probs.append(float(np.asarray(output).reshape(-1)[0]))
    hop = SILERO_WINDOW / SAMPLE_RATE
    regions: list[tuple[float, float]] = []
    in_speech = False
    start = 0.0
    for index, prob in enumerate(probs):
        t0 = origin + index * hop
        if not in_speech and prob >= 0.5:
            in_speech = True
            start = t0
        elif in_speech and prob < 0.35:
            in_speech = False
            regions.append((start, t0))
    local_end = origin + (length if length is not None else duration)
    if in_speech:
        regions.append((start, min(duration, local_end)))
    covered = sum(end - start for start, end in regions)
    span = length if length is not None else duration
    if not regions or covered < 0.15 * max(span, 0.001):
        return None
    return pad_regions(merge_regions(regions, 0.35), duration, PAD_SECONDS)


def speech_regions(
    audio: Path, duration: float, origin: float = 0.0, length: float | None = None
) -> tuple[list[tuple[float, float]], str]:
    silero = silero_speech_regions(audio, duration, origin=origin, length=length)
    if silero:
        return silero, "silero"
    return ffmpeg_speech_regions(audio, duration, origin=origin, length=length), "ffmpeg"


def silence_gaps(duration: float, speech: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not speech:
        return []
    return invert_to_speech(duration, speech)


def speech_ratio(start: float, end: float, speech: list[tuple[float, float]]) -> float:
    length = max(0.001, end - start)
    covered = 0.0
    for speech_start, speech_end in speech:
        overlap = min(end, speech_end) - max(start, speech_start)
        if overlap > 0:
            covered += overlap
    return min(1.0, covered / length)


def snap_cut(target: float, start: float, duration: float, gaps: list[tuple[float, float]]) -> float:
    window_lo = start + MIN_CHUNK_SECONDS
    window_hi = min(duration, start + MAX_CHUNK_SECONDS)
    best: float | None = None
    best_distance = 10.0
    for gap_start, gap_end in gaps:
        if gap_end - gap_start < 0.18:
            continue
        midpoint = (gap_start + gap_end) / 2
        if midpoint < window_lo or midpoint > window_hi:
            continue
        distance = abs(midpoint - target)
        if distance < best_distance:
            best = midpoint
            best_distance = distance
    if best is not None:
        return best
    return min(duration, max(window_lo, target))


def pack_chunks(
    duration: float, speech: list[tuple[float, float]]
) -> list[tuple[int, float, float, float]]:
    gaps = silence_gaps(duration, speech)
    chunks: list[tuple[int, float, float, float]] = []
    cursor = 0.0
    index = 0
    while cursor < duration - 0.2:
        target = min(duration, cursor + CHUNK_SECONDS)
        end = duration if duration - cursor <= MAX_CHUNK_SECONDS else snap_cut(target, cursor, duration, gaps)
        if end - cursor < 0.4:
            break
        chunks.append((index, cursor, end, speech_ratio(cursor, end, speech)))
        index += 1
        cursor = end
    return chunks


def cut_chunk(audio: Path, dest: Path, start: float, length: float) -> None:
    completed = _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{length:.3f}",
            "-i",
            str(audio),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-b:a",
            "32k",
            str(dest),
        ]
    )
    if completed.returncode != 0:
        raise AssistantToolsError(
            completed.stderr.strip() or f"ffmpeg chunk failed @{start}",
            error_type="audio_extraction_failed",
            exit_code=2,
        )


def shift_timestamps(items: Any, offset: float) -> list[dict[str, Any]]:
    shifted: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return shifted
    for unknown in items:
        if not isinstance(unknown, dict):
            continue
        item = dict(unknown)
        for key in ("start", "end"):
            if item.get(key) is None:
                continue
            try:
                item[key] = float(item[key]) + offset
            except (TypeError, ValueError):
                continue
        shifted.append(item)
    return shifted


def transcribe_chunk(
    *,
    audio: Path,
    start: float,
    length: float,
    index: int,
    dest: Path,
    speech_amount: float,
    api_key: str,
    timeout_seconds: float,
    model: str,
    language: str,
    timestamps: str,
    temperature: float,
    prompt: str,
    proxy: str | None,
    url: str | None,
) -> dict[str, Any]:
    if dest.exists():
        existing = json.loads(dest.read_text())
        if existing.get("text") is not None:
            return existing
    if length >= 8 and speech_amount < MIN_SPEECH_RATIO:
        rec = {
            "id": index,
            "start": start,
            "end": start + length,
            "text": "",
            "usage": {"seconds": 0, "cost": 0},
            "skipped": "low_speech",
            "speech_ratio": speech_amount,
        }
        dest.write_text(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
    with tempfile.TemporaryDirectory(prefix="kit-stt-") as tmp:
        chunk_path = Path(tmp) / f"chunk-{index:04d}.mp3"
        cut_chunk(audio, chunk_path, start, length)
        payload = groq_provider.transcribe(
            api_key=api_key,
            source=str(chunk_path),
            timeout_seconds=timeout_seconds,
            model=model,
            language=language,
            timestamps=timestamps,
            temperature=temperature,
            prompt=prompt,
            proxy=proxy,
            url=url,
        )
    rec = {
        "id": index,
        "start": start,
        "end": start + length,
        "text": str(payload.get("text") or "").strip(),
        "usage": payload.get("usage"),
        "speech_ratio": speech_amount,
    }
    if isinstance(payload.get("segments"), list):
        rec["segments"] = shift_timestamps(payload["segments"], start)
    if isinstance(payload.get("words"), list):
        rec["words"] = shift_timestamps(payload["words"], start)
    dest.write_text(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec



def bound_inline_payload(payload: dict[str, Any], cache_dir: str | Path) -> dict[str, Any]:
    """Keep the harness JSON under the tool output cap. Words stay inline unless huge."""
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) <= INLINE_LIMIT_BYTES:
        return payload
    out = dict(payload)
    words = out.pop("words", None)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    if words:
        path = cache / "words.json"
        path.write_text(json.dumps(words, ensure_ascii=False) + "\n")
        out["truncated"] = True
        out["words_truncated"] = True
        out["words_path"] = str(path)
        out["words_count"] = len(words)
        encoded = json.dumps(out, ensure_ascii=False).encode("utf-8")
        if len(encoded) <= INLINE_LIMIT_BYTES:
            return out
    out["truncated"] = True
    out["text"] = str(out.get("text") or "")[:12000]
    out["segments"] = (out.get("segments") or [])[:12]
    out["output_note"] = "Response truncated for the harness tool cap; full chunks stay in cache_dir."
    return out


def transcribe_window(
    *,
    api_key: str,
    source: str,
    timeout_seconds: float,
    model: str,
    language: str,
    timestamps: str,
    temperature: float,
    prompt: str,
    proxy: str | None,
    url: str | None,
    start_seconds: float = 0.0,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    if is_url(source):
        payload = groq_provider.transcribe(
            api_key=api_key,
            source=source,
            timeout_seconds=timeout_seconds,
            model=model,
            language=language,
            timestamps=timestamps,
            temperature=temperature,
            prompt=prompt,
            proxy=proxy,
            url=url,
        )
        payload["window_note"] = "URLs are transcribed in one request; local files are chunked."
        return payload

    path = Path(source).expanduser()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise AssistantToolsError(
            "ffmpeg and ffprobe are required to transcribe long local media",
            error_type="missing_dependency",
            exit_code=2,
        )
    duration = probe_duration(path)
    if start_seconds < 0:
        start_seconds = 0.0
    if start_seconds >= duration:
        raise AssistantToolsError(
            f"start_seconds {start_seconds} is past duration {duration:.1f}",
            error_type="invalid_request",
            exit_code=2,
        )
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    digest = cache_key(
        path=path, model=model, language=language, prompt=prompt, timestamps=timestamps
    )
    cache_dir = CACHE_ROOT / digest
    chunk_dir = cache_dir / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    audio = prepare_source(path, cache_dir)
    span_start = max(0.0, start_seconds)
    span_end = min(duration, start_seconds + (limit * MAX_CHUNK_SECONDS) + MIN_CHUNK_SECONDS)
    plan_path = cache_dir / f"plan-{span_start:.2f}-{span_end:.2f}.json"
    engine = "grid"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        packed = [
            (int(item["id"]), float(item["start"]), float(item["end"]), float(item["speech_ratio"]))
            for item in plan.get("chunks", [])
        ]
        engine = str(plan.get("vad") or "grid")
    else:
        speech, engine = speech_regions(audio, duration, origin=span_start, length=span_end - span_start)
        local = [(max(0.0, start - span_start), max(0.0, end - span_start)) for start, end in speech]
        packed_local = pack_chunks(span_end - span_start, local)
        packed = [
            (index, start + span_start, min(duration, end + span_start), ratio)
            for index, start, end, ratio in packed_local
        ]
        plan_path.write_text(
            json.dumps(
                {
                    "vad": engine,
                    "chunks": [
                        {
                            "id": index,
                            "start": start,
                            "end": end,
                            "speech_ratio": ratio,
                        }
                        for index, start, end, ratio in packed
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    if not packed:
        packed = [(0, span_start, min(duration, span_end), 1.0)]

    selected = [item for item in packed if item[2] > start_seconds + 0.05]
    if not selected:
        selected = packed[-1:]
    selected = selected[:limit]
    window_end = selected[-1][2]
    next_start = None if window_end >= duration - 0.3 else window_end

    results: dict[int, dict[str, Any]] = {}

    def work(index: int, start: float, end: float, ratio: float) -> dict[str, Any]:
        return transcribe_chunk(
            audio=audio,
            start=start,
            length=end - start,
            index=index,
            dest=chunk_dir / f"{start:.3f}.json",
            speech_amount=ratio,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            model=model,
            language=language,
            timestamps=timestamps,
            temperature=temperature,
            prompt=prompt,
            proxy=proxy,
            url=url,
        )

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {
            pool.submit(work, index, start, end, ratio): index
            for index, start, end, ratio in selected
        }
        for fut in as_completed(futs):
            rec = fut.result()
            results[int(rec["id"])] = rec

    segments = [results[index] for index, _, _, _ in selected]
    seconds_billed = 0.0
    cost = 0.0
    skipped = 0
    for rec in segments:
        if rec.get("skipped"):
            skipped += 1
        usage = rec.get("usage") or {}
        if isinstance(usage, dict):
            try:
                seconds_billed += float(usage.get("seconds") or 0)
            except (TypeError, ValueError):
                pass
            try:
                cost += float(usage.get("cost") or 0)
            except (TypeError, ValueError):
                pass
    window_text = "\n".join(
        f"[{format_hms(rec['start'])}–{format_hms(rec['end'])}] {rec['text']}"
        for rec in segments
        if rec.get("text")
    )
    payload = {
        "text": window_text,
        "duration": duration,
        "vad": engine,
        "chunk_seconds": CHUNK_SECONDS,
        "window_start": selected[0][1],
        "window_end": selected[-1][2],
        "next_start": next_start,
        "chunks_returned": len(segments),
        "chunks_total": len(packed),
        "chunks_skipped": skipped,
        "cache_dir": str(cache_dir),
        "usage": {"seconds": seconds_billed, "cost": cost},
        "segments": [
            {
                "id": rec["id"],
                "start": rec["start"],
                "end": rec["end"],
                "text": rec["text"],
                "skipped": rec.get("skipped"),
            }
            for rec in segments
        ],
        "words": [word for rec in segments for word in rec.get("words") or []],
    }
    return bound_inline_payload(payload, cache_dir)


def transcribe_file(
    *,
    api_key: str,
    source: str,
    timeout_seconds: float,
    model: str,
    language: str,
    timestamps: str,
    temperature: float,
    prompt: str,
    proxy: str | None,
    url: str | None,
) -> dict[str, Any]:
    """Transcribe a local file completely by paging windows. URLs stay one-shot."""
    if is_url(source):
        return transcribe_window(
            api_key=api_key,
            source=source,
            timeout_seconds=timeout_seconds,
            model=model,
            language=language,
            timestamps=timestamps,
            temperature=temperature,
            prompt=prompt,
            proxy=proxy,
            url=url,
            start_seconds=0.0,
            limit=MAX_LIMIT,
        )
    start = 0.0
    texts: list[str] = []
    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    seconds_billed = 0.0
    cost = 0.0
    skipped = 0
    engine = "grid"
    duration = 0.0
    cache_dir = ""
    total = 0
    while True:
        window = transcribe_window(
            api_key=api_key,
            source=source,
            timeout_seconds=timeout_seconds,
            model=model,
            language=language,
            timestamps=timestamps,
            temperature=temperature,
            prompt=prompt,
            proxy=proxy,
            url=url,
            start_seconds=start,
            limit=MAX_LIMIT,
        )
        duration = float(window.get("duration") or duration)
        engine = str(window.get("vad") or engine)
        cache_dir = str(window.get("cache_dir") or cache_dir)
        total = int(window.get("chunks_total") or total)
        skipped += int(window.get("chunks_skipped") or 0)
        if window.get("text"):
            texts.append(str(window["text"]))
        segments.extend(window.get("segments") or [])
        words.extend(window.get("words") or [])
        usage = window.get("usage") or {}
        if isinstance(usage, dict):
            try:
                seconds_billed += float(usage.get("seconds") or 0)
            except (TypeError, ValueError):
                pass
            try:
                cost += float(usage.get("cost") or 0)
            except (TypeError, ValueError):
                pass
        nxt = window.get("next_start")
        if nxt is None:
            break
        start = float(nxt)
    payload = {
        "text": "\n".join(texts),
        "duration": duration,
        "vad": engine,
        "segments": segments,
        "words": words,
        "chunks_total": total,
        "chunks_skipped": skipped,
        "cache_dir": cache_dir,
        "usage": {"seconds": seconds_billed, "cost": cost},
    }
    return bound_inline_payload(payload, cache_dir)
