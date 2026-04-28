install:
    poetry install

platform:
    cd platform && poetry run python app.py

exam-tool:
    cd exam-tool && poetry run python app.py

dev:
    #!/usr/bin/env bash
    (cd platform && poetry run python app.py) &
    (cd exam-tool && poetry run python app.py) &
    wait
