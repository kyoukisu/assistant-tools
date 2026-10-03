# assistant-tools

JSON-only CLI toolbox for personal agent workflows.

Main command:

```bash
kit
```

Current command groups:

- `stt` — speech to text through an OpenAI-compatible endpoint (OpenRouter Qwen3 ASR by default); long local media is split on pauses and paged
- `tts` — text to speech through OpenRouter
- `search` — web search via Keenable, Exa, or Parallel with a fallback chain
- `extract` — URL extraction via Keenable, Exa, or Parallel with a fallback chain
- `shardx` — client for a local ShardX-compatible browser control service
- `vtt` — video to text via Supadata
- `video` — local video/GIF to frames plus optional audio transcript
- `tg` — Telegram CLI via Telethon

All command results go to stdout as JSON.

## Install

```bash
uv tool install git+https://github.com/kyoukisu/assistant-tools
```

That installs these commands:

- `kit`
- `assistant-tools`

Upgrade later with:

```bash
uv tool upgrade assistant-tools
```

Check the install:

```bash
kit --version
kit --help
```

## Nix / NixOS / Home Manager

The repository now exposes a Nix flake package.

Build it directly:

```bash
nix build github:kyoukisu/assistant-tools
```

Run it directly:

```bash
nix run github:kyoukisu/assistant-tools -- --help
nix run github:kyoukisu/assistant-tools#kit -- tts "Hello there"
```

Use it from Home Manager:

```nix
{
  inputs.assistant-tools.url = "github:kyoukisu/assistant-tools";

  outputs = { self, nixpkgs, home-manager, assistant-tools, ... }: {
    # ...
    home-manager.users.your-user = { pkgs, ... }: {
      home.packages = [
        assistant-tools.packages.${pkgs.system}.default
      ];
    };
  };
}
```

This path is the recommended declarative install method on NixOS. Runtime state such as Telegram session files and STT caches still lives outside the Nix store.

## Secrets

These are expected via environment variables:

- `OPENROUTER_API_KEY` (STT and TTS; `GROQ_API_KEY` is still accepted for STT)
- `KEENABLE_API_KEY` (required only for the Keenable web provider)
- `PARALLEL_API_KEY` (required only for the Parallel web provider)
- `EXA_API_KEY` (required only for the Exa web provider)
- `SUPADATA_API_KEY`
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`

Optional:

- `TELEGRAM_SESSION_STRING`

DeepInfra (optional):

- `DEEPINFRA_TOKEN`

## Config

Default config path:

```text
~/.config/assistant-tools/config.toml
```

By default, HTTP tools do **not** inherit shell proxy environment variables.
If you want proxying for `stt`, `search`, `extract`, `vtt`, or transcript-enabled `video`, set it explicitly in config:

```toml
[network]
timeout_seconds = 60
proxy = "http://127.0.0.1:7897"
```

### Web providers

`search` and `extract` select a provider independently, with optional `fallback_providers` tried on retryable failures. Override one command with `--provider keenable|exa|parallel` (no fallback).

```toml
[search]
provider = "exa"
exa_type = "auto"
exa_highlights = true
max_results = 5

[extract]
provider = "exa"
full_content = false
max_chars_per_result = 5000
```

The Exa search path requests at most the configured URL results and compact highlights only: it does not request page text, summaries, subpages, or link extras. Exa extraction requests one bounded text representation per URL by default. `--full-content` is the explicit opt-in for unbounded text; Exa does not apply `--objective`, and reports that in result metadata instead of spending on an LLM summary.

## Quick usage

### Speech to text

```bash
kit stt ./voice.ogg
kit stt ./voice.ogg --timestamps segment
kit stt https://example.com/audio.mp3
```

### Text to speech

```bash
kit tts "Hello there."
kit tts "Привет" --voice Kore --language ru --no-play --save
kit tts "Hello there." --output /tmp/hello.wav --no-play
```

Defaults: model `google/gemini-3.8-flash-lite-tts`, voice `Kore`, autoplay on, WAV kept only with `--save` or `--output`.

### Web search

```bash
kit search "parallel ai extract api"
kit search "nixos home manager sops" --provider exa --domain nixos.org --domain github.com
kit search "zed release notes" --after-date 2026-01-01
```

### URL extract

```bash
kit extract https://docs.parallel.ai/getting-started/overview
kit extract https://example.com/post --provider exa
kit extract https://example.com/post --full-content
```

### Video to text

```bash
kit vtt https://www.youtube.com/watch?v=dQw4w9WgXcQ
kit vtt https://youtu.be/dQw4w9WgXcQ --mode native
kit vtt https://youtu.be/dQw4w9WgXcQ --chunks
```

### Local video to frames + transcript

Requires local `ffmpeg` and `ffprobe` on `PATH`.

```bash
kit video ./clip.mp4
kit video ./clip.mp4 --max-frames 30 --seconds-per-frame 2
kit video ./clip.mp4 --timestamps word
kit video ./clip.mp4 --at 3.5 --at 9 --at 12.2
kit video ./clip.mp4 --no-align-to-segments
kit video ./clip.mp4 --no-transcribe
kit video ./animation.gif --max-frames 12
```

Default behavior:

- extracts up to `30` frames
- spreads frames across the full duration
- if transcript segments exist, nudges frame timestamps toward nearby speech segments
- if `--at` is provided, extracts frames exactly near those second offsets instead of auto spreading
- writes a run directory with:
  - `frames/`
  - optional `audio.wav`
  - `manifest.json`
- returns JSON with:
  - frame paths
  - transcript text
  - transcript segments with timestamps
  - a simple `timeline` that pairs each frame with the nearest transcript segment

Config example:

```toml
[video]
output_dir = "~/.local/state/assistant-tools/video"
max_frames = 30
seconds_per_frame = 2.0
frame_format = "jpg"
align_to_segments = true
transcribe = true
timestamps = "segment"
```

Notes for agents:

- `stt`/`video` with `timestamps = "segment"` already returns speech timing.
- For a first pass, call `video` without `--at` and inspect `timeline` plus `frames`.
- If a specific spoken moment matters, call `video --at <seconds>` to fetch only the frame(s) around those timestamps.
- If there is no useful audio, the tool still returns evenly spread frames for visual inspection.

## Telegram

Telegram uses:

- one default profile, usually `main`
- optional named profiles, selected via `--profile`
- one session file per profile

You do **not** need to pre-create profiles manually.

New profile flow:

```bash
kit tg --profile work auth login
```

That creates and uses the `work` session automatically.

### Telegram auth

Default profile:

```bash
kit tg auth login
kit tg auth status
kit tg auth export-session
kit tg auth logout
```

Named profile:

```bash
kit tg --profile work auth login
kit tg --profile work auth status
```

### Telegram reading

```bash
kit tg find-dialog "дд джун" --limit 400 --top 10
kit tg dialogs --limit 20
kit tg resolve me
kit tg resolve username
kit tg history me --limit 20
kit tg get me 1 2 3
kit tg search me "hello" --limit 20
```

### Telegram actions

```bash
kit tg send me "hello"
kit tg send me "reply text" --reply-to 123
kit tg send-file me /tmp/doc.pdf
kit tg send-photo me /tmp/image.png --caption "see this"
kit tg send-voice me /tmp/voice.ogg
kit tg send-voice me /tmp/hello.wav
kit tg speak me "You can feel the shift before you can name it."
kit tg wait-next me --timeout-seconds 30
kit tg react me 123 "🔥"
kit tg copy me 123 another_chat
```

### Telegram Mini Apps

Get a short-lived launch URL for a bot's configured Main Mini App:

```bash
kit tg miniapp main @example_bot
kit tg miniapp main @example_bot --start-param campaign --fullscreen
kit tg miniapp menu @example_bot
```

The returned `data.url` is sensitive, account-bound launch data. Pass it
directly to a WebView and do not log, save, or share it. This command obtains
a Telegram-issued URL; it does not emulate Telegram's WebApp browser bridge.

Notes:

- `send-file` sends a local file as a document.
- `send-photo` sends a local image as a Telegram photo.
- `send-voice` sends a local audio file as a Telegram voice note.
- `speak` synthesizes speech through OpenRouter and sends it as a Telegram voice note in one step.
- `wait-next` waits for the next incoming message in the target chat and requires `--timeout-seconds`.
- If `send-voice` gets a non-ogg/non-opus file such as WAV, it auto-converts it to OGG/Opus with `ffmpeg` before upload.

### Telegram media

```bash
kit tg media-info me 123
kit tg media-download me 123
kit tg media-download me 123 --output-dir /tmp/tg
```

## Telegram profiles

Default profile is controlled by:

```toml
[tg]
default_profile = "main"
```

By default:

- `main` uses `~/.local/state/assistant-tools/tg/main.session`
- other profiles use `~/.local/state/assistant-tools/tg/sessions/<profile>.session`

Examples:

```bash
kit tg auth login
kit tg --profile work auth login
kit tg --profile alt dialogs
```

Optional explicit profile config:

```toml
[tg]
default_profile = "main"
session_file = "~/.local/state/assistant-tools/tg/main.session"
session_dir = "~/.local/state/assistant-tools/tg/sessions"
download_dir = "~/.local/state/assistant-tools/tg/downloads"
cache_dir = "~/.local/state/assistant-tools/tg/cache"

[tg.profiles.work]
session_file = "~/.local/state/assistant-tools/tg/work.session"

[tg.profiles.alt]
session_file = "~/.local/state/assistant-tools/tg/alt.session"
download_dir = "~/.local/state/assistant-tools/tg/downloads-alt"
```

## Output contract

All commands return JSON to stdout.

Success shape:

```json
{
  "ok": true,
  "command": "search",
  "provider": "parallel",
  "data": {},
  "error": null,
  "meta": {}
}
```

Error shape:

```json
{
  "ok": false,
  "command": "tg",
  "provider": "unknown",
  "data": null,
  "error": {
    "type": "missing_env",
    "message": "Missing required environment variable: TELEGRAM_API_ID"
  },
  "meta": {
    "command": "tg"
  }
}
```

## Config example

```toml
[network]
timeout_seconds = 60

[stt]
url = "https://openrouter.ai/api/v1/audio/transcriptions"
model = "qwen/qwen3-asr-flash-2026-02-10"
language = ""
timestamps = "none"
temperature = 0.0
prompt = ""

[search]
mode = "agentic"
max_results = 5
max_chars_per_result = 4000
max_chars_total = 12000

[extract]
full_content = false
max_chars_per_result = 5000

[vtt]
mode = "auto"
text = true
lang = ""
wait = true
poll_interval_seconds = 1.0
wait_timeout_seconds = 180.0

[tts]
model = "google/gemini-3.8-flash-lite-tts"
voice = "Kore"
speed = 1.0
autoplay = true
volume = 45000
output_dir = "~/.local/state/assistant-tools/tts"

[tg]
default_profile = "main"
api_id = 0
api_hash = ""
session_file = "~/.local/state/assistant-tools/tg/main.session"
session_dir = "~/.local/state/assistant-tools/tg/sessions"
download_dir = "~/.local/state/assistant-tools/tg/downloads"
cache_dir = "~/.local/state/assistant-tools/tg/cache"
session_string = ""
proxy = ""
takeout = false
sleep_threshold = 10
hide_password = false

[tg.profiles.work]
session_file = "~/.local/state/assistant-tools/tg/work.session"
```
