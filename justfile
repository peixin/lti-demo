install:
    uv sync --locked

platform:
    cd platform && uv run --locked python app.py

exam-tool:
    cd exam-tool && uv run --locked python app.py

dev:
    #!/usr/bin/env bash
    (cd platform && uv run --locked python app.py) &
    (cd exam-tool && uv run --locked python app.py) &
    wait
