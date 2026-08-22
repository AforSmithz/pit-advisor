export AWS_PROFILE := "pitadvisor"
export AWS_REGION := "ap-southeast-1"

account := "352445792687"

default:
    @just --list

login:
    aws login --profile {{AWS_PROFILE}}

whoami:
    aws sts get-caller-identity --profile {{AWS_PROFILE}}

# spend so far this month, project tag only
cost:
    aws ce get-cost-and-usage \
      --time-period Start=$(date -v1d +%Y-%m-%d),End=$(date -v+1d +%Y-%m-%d) \
      --granularity MONTHLY --metrics UnblendedCost \
      --filter '{"And":[{"Tags":{"Key":"project","Values":["pit-advisor"]}},{"Not":{"Dimensions":{"Key":"RECORD_TYPE","Values":["Credit","Refund"]}}}]}' \
      --group-by Type=DIMENSION,Key=SERVICE \
      --profile {{AWS_PROFILE}} --region us-east-1

synth:
    uv run --directory infra cdk synth

diff:
    uv run --directory infra cdk diff

# budgets are useless without a subscriber, so the address is required to deploy
deploy stack email:
    uv run --directory infra cdk deploy {{stack}} -c alertEmail={{email}} --require-approval broadening

doctor:
    uv run pitadv doctor

# everything below writes to data/local, no aws calls
ingest season round="":
    uv run pitadv ingest --source jolpica --season {{season}} {{ if round == "" { "" } else { "--round " + round } }} --local

backfill from to:
    uv run pitadv backfill --from {{from}} --to {{to}} --local

quality layer="bronze":
    uv run pitadv quality-report --layer {{layer}} --local

views:
    uv run pitadv emit-views --views pipeline --local

# dbt against the local duckdb copy of the lake
transform *args:
    uv run dbt build --project-dir transform --target local {{ args }}

lineage:
    uv run pitadv lineage --check --local

catalog-check:
    uv run pitadv catalog-sync --check

# build and push the pipeline image the fargate steps run
image tag="latest":
    aws ecr get-login-password --profile {{AWS_PROFILE}} --region {{AWS_REGION}} \
      | docker login --username AWS --password-stdin {{account}}.dkr.ecr.{{AWS_REGION}}.amazonaws.com
    docker build --platform linux/arm64 -f infra/docker/pipeline.Dockerfile \
      -t {{account}}.dkr.ecr.{{AWS_REGION}}.amazonaws.com/pitadvisor-pipeline-dev:{{tag}} .
    docker push {{account}}.dkr.ecr.{{AWS_REGION}}.amazonaws.com/pitadvisor-pipeline-dev:{{tag}}

fmt:
    uv run ruff format .
    uv run ruff check . --fix

test:
    uv run pytest

check:
    uv run ruff check .
    uv run ruff format --check .
    uv run pyright src/pitadvisor
    uv run pytest

infra-check:
    uv run --directory infra pytest
    uv run --directory infra cdk synth --quiet

transform-check:
    uv run dbt build --project-dir transform --target local
    uv run pitadv lineage --check --local

check-all: check infra-check transform-check
