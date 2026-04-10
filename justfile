set shell := ["bash", "-lc"]

run *args:
    uv run assistant-tools {{args}}

lint:
    uv run ruff check .
    uv run pyright
