install:
    uv sync --locked

platform:
    cd platform && PORT=${LTI_PLATFORM_PORT:-8001} uv run --locked python app.py

exam-tool:
    cd exam-tool && PORT=${LTI_EXAM_TOOL_PORT:-8002} uv run --locked python app.py

dev:
    #!/usr/bin/env bash
    set -m
    cleanup() {
        trap - EXIT INT TERM
        kill -TERM -- "-$platform_pid" "-$exam_tool_pid" 2>/dev/null || true
        wait || true
    }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    (cd platform && PORT=${LTI_PLATFORM_PORT:-8001} exec uv run --locked python app.py) &
    platform_pid=$!
    (cd exam-tool && PORT=${LTI_EXAM_TOOL_PORT:-8002} exec uv run --locked python app.py) &
    exam_tool_pid=$!
    wait
