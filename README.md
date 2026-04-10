# assistant-tools

JSON-only CLI toolbox for:

- `stt` — speech to text via Groq `whisper-large-v3`
- `search` — web search via Parallel
- `extract` — URL content extraction via Parallel
- `vtt` — video to text via Supadata

## Install

```bash
cd /home/user/assistant-tools
uv sync
```

Run with:

```bash
uv run assistant-tools --help
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
uv run assistant-tools stt ./sample.m4a
uv run assistant-tools search "parallel ai extract api"
uv run assistant-tools extract https://docs.parallel.ai/getting-started/overview
uv run assistant-tools vtt https://www.youtube.com/watch?v=dQw4w9WgXcQ
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
```
