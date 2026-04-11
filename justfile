set shell := ["bash", "-lc"]

run *args:
    uv run kit {{args}}

lint:
    uv run ruff check .
    uv run pyright
