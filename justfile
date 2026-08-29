export AWS_PROFILE := "pitadvisor"
export AWS_REGION := "ap-southeast-1"

account := "352445792687"

# CI assumes a role by OIDC, so there is no profile to name there
profile := if env_var_or_default("CI", "") == "" { "--profile pitadvisor" } else { "" }

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
    # --push streams to the registry, a local copy of the image needs several gb of disk
    docker buildx build --platform linux/arm64 --push \
      -f infra/docker/pipeline.Dockerfile \
      -t {{account}}.dkr.ecr.{{AWS_REGION}}.amazonaws.com/pitadvisor-pipeline-dev:{{tag}} .
    # the pipeline overrides the command exactly like this, and an entrypoint that swallows
    # it is a step that exits 0 having done nothing
    # PITADV_AWS_PROFILE is empty for the same reason the task sets it empty: there is no
    # ~/.aws in the container, so a named profile is a ProfileNotFound at the first api call
    docker run --rm --platform linux/arm64 -e PITADV_AWS_PROFILE="" \
      {{account}}.dkr.ecr.{{AWS_REGION}}.amazonaws.com/pitadvisor-pipeline-dev:{{tag}} \
      sh -c "pitadv version && dbt --version > /dev/null \
        && python -c 'from pitadvisor.config import boto_session; boto_session().client(\"s3\")'"

# ask the agent one question against the deployed lake
ask question:
    uv run pitadv ask "{{question}}" --tools

# score the agent against the golden set. costs bedrock tokens
evals *args:
    uv run pitadv evals --suite evals/golden.yaml --report results/evals/ {{args}}

# build the knowledge base corpus under docs/
docs-sync *args:
    uv run pitadv docs-sync {{args}}

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

# the dashboard reads emitted views off disk, so they have to be copied in first
web-data:
    aws s3 cp s3://pit-advisor-data-{{env_var_or_default("PITADV_ENV", "dev")}}-{{account}}/views/ \
      web/public/data/ --recursive --exclude "*" --include "*_view.json" \
      {{profile}} --region {{AWS_REGION}}

web-check:
    cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm test && pnpm build

# the html is immutable per build, the view json is not, so they get different cache headers
web-deploy: web-check
    aws s3 sync web/out/ s3://pit-advisor-web-{{env_var_or_default("PITADV_ENV", "dev")}}-{{account}}/ \
      --delete --exclude "data/*" {{profile}} --region {{AWS_REGION}}
    aws s3 sync web/out/data/ s3://pit-advisor-web-{{env_var_or_default("PITADV_ENV", "dev")}}-{{account}}/data/ \
      --delete --cache-control "max-age=300" {{profile}} --region {{AWS_REGION}}
    aws cloudfront create-invalidation --distribution-id $( \
      aws cloudformation describe-stacks --stack-name pitadvisor-web-{{env_var_or_default("PITADV_ENV", "dev")}} \
        --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text \
        {{profile}} --region {{AWS_REGION}}) \
      --paths "/*" {{profile}} --region {{AWS_REGION}}
