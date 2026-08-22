# the one image every step of the weekend pipeline runs, built for the arm64 task
FROM --platform=linux/arm64 python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --group transform --extra sessions

COPY transform ./transform
# dbt only looks for profiles.yml in cwd or ~/.dbt, never in --project-dir
RUN ln -s transform/profiles.yml profiles.yml

ENTRYPOINT ["sh", "-c"]
CMD ["pitadv --help"]
