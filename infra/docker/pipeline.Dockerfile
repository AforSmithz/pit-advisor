# the one image the weekend pipeline steps and the agent's two lambdas all run, built arm64
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --group transform --extra sessions --extra lambda

COPY transform ./transform
# the circuit taxonomy only, not data/local: that is the laptop's lake
COPY data/reference ./data/reference
# dbt only looks for profiles.yml in cwd or ~/.dbt, never in --project-dir
RUN ln -s transform/profiles.yml profiles.yml

# no entrypoint: the pipeline steps pass their own ["sh", "-c", "<command>"], and the lambdas
# override both with the runtime interface client plus their handler
CMD ["pitadv", "--help"]
