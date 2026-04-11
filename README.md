# assistant-tools

JSON-only CLI toolbox for:

- `stt` — speech to text via Groq `whisper-large-v3`
- `search` — web search via Parallel
- `extract` — URL content extraction via Parallel
- `vtt` — video to text via Supadata
- `tg` — Telegram CLI via Kurigram

## Install

```bash
cd /home/user/assistant-tools
uv sync
```

Run with:

```bash
uv run kit --help
```

## Secrets

Set these via environment variables:

- `GROQ_API_KEY`
- `PARALLEL_API_KEY`
- `SUPADATA_API_KEY`

## Config

Default config path:

```text
~/.config/assistant-tools/config.toml
```

All commands are JSON-only and write structured output to stdout.

## Examples

```bash
uv run kit stt ./sample.m4a
uv run kit search "parallel ai extract api"
uv run kit extract https://docs.parallel.ai/getting-started/overview
uv run kit vtt https://www.youtube.com/watch?v=dQw4w9WgXcQ
uv run kit tg auth status
```

## Config example

```toml
[network]
timeout_seconds = 60

[stt]
model = "whisper-large-v3"
language = ""
timestamps = "none"
temperature = 0.0

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

[tg]
api_id = 0
api_hash = ""
session_file = "~/.local/state/assistant-tools/tg/main.session"
download_dir = "~/.local/state/assistant-tools/tg/downloads"
cache_dir = "~/.local/state/assistant-tools/tg/cache"
session_string = ""
proxy = ""
takeout = false
sleep_threshold = 10
hide_password = false
```
