# Pit Advisor

Pit Advisor is a race-weekend research tool for Formula 1. It replaces the scattered ritual
of checking five sites before a Grand Prix with one dashboard: recent driver form normalized
against teammates, the gap between a car's Saturday pace and its Sunday pace, how well a team
historically fits a circuit of this type, the weather scenarios for each session, reliability
risk, and a finishing-position forecast with intervals. An agent sits on top of the same data
and answers questions about it, including questions about the regulations, with citations.

It is also the reason the repository exists. It is a working example of the parts of a data
platform that usually get skipped in a portfolio project: source contracts, quality gates,
quarantine, replayable raw data, lineage, time-forward backtesting, calibration, and
deterministic evaluation of an LLM agent. The whole thing runs serverless on AWS inside a
twenty dollar per month budget, which is a design constraint rather than an afterthought.

## What exists today

This is early. What works today is the foundation and the ingest half of the pipeline. The
foundation is the Python package with typed configuration and shared key and provenance types,
a `pitadv` CLI, a CDK application covering the lake, the budgets and the ingest ledger, a
pre-commit setup, and CI that lints, type checks, tests and synthesizes the infrastructure.

On top of that, three sources now land in the lake. Jolpica results, qualifying, laps, pit
stops and schedules, Open-Meteo weather snapshots per event, and FastF1 session laps all go
through the same path: a conditional HTTP GET under a token bucket, the response written to
`raw/` verbatim with its request metadata, a parse into typed rows, a pydantic contract per
table, and Parquet in `bronze/` partitioned by season and round. Rows that fail their contract
land in `quarantine/` with the reason attached instead of failing the load. `pitadv
quality-report` checks row counts, freshness, duplicate natural keys, referential integrity
between tables and null rates on the columns that should be nearly always present, and
`pitadv emit-views` writes the first view artifact, `pipeline_view.json`, which carries table
health, quarantine counts by reason and the remaining request quota per source.

Above bronze there is now a transform layer. A dbt project builds conformed silver tables with
surrogate keys and amendment-aware deduplication, and three gold marts on top of them: race
results with the finishing status classified, qualifying gaps to pole and to the teammate, and
pit stop summaries against the event average. The same models run two ways: on duckdb against
the local Parquet, which is how they are developed and tested, and on Athena as Iceberg tables
with MERGE, which is how they run in the account. The Glue tables that Athena reads bronze
through are generated from the same pydantic contracts that validate the rows, so the catalog
cannot drift from the schema. `pitadv lineage --check` reads the dbt manifest and walks every
gold model back through silver to the bronze sources and on to the raw objects each one was
built from; a gold model that cannot be traced to raw fails the command.

Raw and bronze now cover 2021 to 2025 for results, qualifying, laps, pit stops, race-session
timing and weather. A local dbt build of the same models produces 2,278 result rows across the
five seasons; the copy in Athena holds 2024 only, because that is what the pipeline has been
run for, so a question about 2022 is answerable on the laptop and not yet in the account. The
data, ingest, transform and web stacks are deployed. The agent stack synthesizes and has not
been deployed.

## The honesty constraint

Formula 1 is a low-sample, high-variance sport: twenty-four races a year, twenty cars, and a
regulation reset every few seasons that invalidates much of the history. A model built on that
can look impressive on a chart and be worthless out of sample, which is the failure mode of
most hobby forecasting projects.

So the forecast is only allowed to claim value if it beats a deliberately dumb baseline out of
sample. Three are used: grid position alone, championship standings alone, and last race's
result. The comparison runs on a time-forward holdout of at least sixty races, scored with
multiclass log loss and Brier, with bootstrap intervals resampled at the race level rather than
the driver level, because drivers within a race are correlated and resampling them individually
gives intervals that are far too tight. The calibration page is the dashboard's landing route,
not a tab behind the predictions. If the model does not beat the baselines, the dashboard says
so and the forecast tool is removed from the agent. That is a successful outcome, not a failed
one.

Two rules follow. The frontend computes nothing: every number rendered traces back to a backend
artifact that passed the quality gate, because a metric calculated in TypeScript is a metric
nobody tested. And the language model never produces a figure: every number in an agent answer
comes verbatim from a tool result, and if no tool has the answer, the answer is that we do not
have it.

The second rule is enforced in code rather than by prompt. After the model stops, every number in
the answer is checked against the numbers the tools returned, the numbers in the question and the
numbers in the tool arguments, and an answer carrying anything else is withheld and says which
figures were loose. A golden set of sixty-four questions scores the agent: exact match on numeric
answers against the marts, retrieval hit-rate, tool-selection accuracy, and a count of ungrounded
figures that has to be zero. The most recent run scores 97.4%, 100% and 98.4% against floors of
95%, 90% and 90%, and fails the last one by a single case. About once in sixty-four questions the
model subtracts one tool result from another and states the difference, which is a figure no tool
returned. The check catches it and withholds the answer every time, and the agent stays out of the
dashboard until a run comes back clean. That is the gate doing its job rather than a gate worth
lowering.

## Architecture

```
EventBridge rule (weekly, off by default)
  |
  +-- Step Functions  weekend-pipeline        one Fargate task definition, one image
        |
        +-- ingest jolpica       results, quali, laps, pitstops, schedules
        +-- ingest open-meteo    forecast and archive around each session
        +-- ingest fastf1        session laps, heavy deps, S3-backed cache
        +-- quality gate         contracts, freshness, keys, references
        +-- catalog sync         glue bronze tables from the contracts
        +-- dbt build            silver and gold, Iceberg MERGE
        +-- lineage check        every gold model traced back to raw
        +-- emit views           gold into versioned view JSON

S3 (one bucket, prefix separated)
  raw/  bronze/  silver/  gold/  views/  quarantine/  docs/  cache/

Glue Data Catalog        table metadata
Athena                   SQL, byte-scan capped workgroup
DynamoDB                 request ledger, run state
Bedrock                  Knowledge Base on S3 Vectors, agent runtime, Guardrails
CloudFront + S3          static Next.js dashboard reading views/*.json
CloudWatch + Budgets     logs, alarms, the spend ceiling
```

The data flow is a medallion lake. Every upstream response lands in `raw/` verbatim, with its
request metadata, before anything parses it, so every layer above is rebuildable from `raw/`
alone and a parser bug is a replay rather than a refetch. Bronze is that payload parsed and
typed into Parquet with the schema version stamped on it. Silver is conformed Iceberg tables
with surrogate keys and slowly changing dimensions where identity moves, which it does often:
drivers get swapped mid-season, reserve drivers appear, teams get renamed. Gold is a handful
of marts, one per metric family, and those marts are recomputed rather than appended because
race results get amended days later by penalties and disqualifications.

The dashboard never touches Athena. The pipeline emits versioned JSON view artifacts into
`views/`,
CloudFront serves the static Next.js export, and the browser reads JSON, which bounds query cost
and leaves no origin server to run or pay for.

The pipeline steps are all the same container: one ARM64 Fargate task definition running the
`pitadv` CLI and dbt, with the command varying per step. The tasks run in public subnets with a
public IP and no inbound rules, because the alternative is a NAT gateway at roughly thirty
dollars a month, which is more than everything else in this project put together. A failed
quality gate stops the run before dbt touches silver.

On failure the pipeline quarantines rather than corrupts. A row that fails its contract goes to
`quarantine/` with the reason attached, the load continues, and the count by reason is published
on the pipeline health page beside source freshness and remaining API quota. A data product that
hides its own staleness is lying about itself. Upstream, the Jolpica cap of roughly two hundred
requests an hour is enforced by a DynamoDB token bucket and a persisted request ledger rather
than by hoping the schedule stays polite. The ledger matters more than it looks: a Step Functions
retry re-enters the same Lambda, and without consulting the ledger first the hour's quota is
spent twice.

## Data sources

Results, standings, qualifying, grids, laps, pit stops and schedules come from
[Jolpica-F1](https://github.com/jolpica/jolpica-f1), the community successor to Ergast, which
stopped receiving data in early 2025. It keeps an Ergast-compatible response shape, which is
why the bronze schemas look conventional. Its practical limit is the request cap.

Session timing, per-lap times, stints and compounds, weather and track status come from
[FastF1](https://github.com/theOehrly/Fast-F1). It is the only free route to lap-level detail.
It is also slow on a cold cache and heavy in dependencies, which is why it runs as a Fargate
task with an S3-backed cache rather than as a Lambda. FastF1 output stays in a private account
and is not republished; no timing data is committed to this repository.

Weather comes from [Open-Meteo](https://open-meteo.com/), free and keyless, using circuit
coordinates from Jolpica, snapshotted at fetch time so a past prediction can be replayed against
what was actually known then. The regulations corpus is FIA published documents, versioned by
season and cited by title and date wherever the agent quotes them.

Nothing paywalled, nothing behind anti-bot protection, and no undocumented internal endpoints
are used. One file of reference data, `data/reference/circuits.yml`, is hand maintained, because
no API publishes downforce level or pit-lane time loss. Hand-authored reference data is fine;
hand-authored measurements are not, so every numeric field in it is either a published constant
or regenerated from our own history by script.

## Trade-offs worth arguing about

The transform layer is dbt on Athena with Iceberg tables, not Glue with PySpark. The dataset
is on the order of a gigabyte across a few dozen models. Spark would spend most of its runtime
starting up, and dbt brings tests, documentation and lineage without any of them being written
by hand. The cost is that the SQL is portable but the adapter and table format are not free to
swap later.

Those Iceberg tables live in the bucket the lake already owns rather than in an S3 Tables
bucket. S3 Tables would bring managed compaction, and it also brings a second storage charge, a
per-object monitoring charge and a Lake Formation grant model, none of which a single-user
project on a hundred dollars of credits can justify. Everything Iceberg is actually needed for
here, which is MERGE on amended results and schema evolution, works without it.

The same models run on duckdb locally and on Athena in the account. Two adapters is a real cost:
three macros exist purely to paper over the dialects, and a change to either engine's behaviour
is a change to both targets. It buys a build-and-test cycle measured in seconds with no Athena
scan, which is what makes it worth having the models under test at all.

The vector store behind the Bedrock Knowledge Base is S3 Vectors rather than OpenSearch
Serverless. This is the decision the console actively steers you away from, and it is not
close: OpenSearch Serverless bills a capacity floor whether or not anything queries it, which
would consume the entire project budget in about a month and leave nothing for compute or
tokens. The corpus is a few thousand chunks and the latency penalty is invisible to a user
waiting a second or two for an answer.

Credentials are short-term only. The project's IAM user has no access key at all; local
credentials come from `aws login`, which issues session credentials that rotate every fifteen
minutes. CI holds no AWS credentials of any kind, because at this stage it only lints, types,
tests and synthesizes, none of which needs an account. The GitHub OIDC role gets created in
the phase where CI first has something to deploy, rather than existing as a standing trust
relationship to a repository that is not deploying anything yet.

The agent gets typed tools rather than open text-to-SQL for the common questions, with a
single guarded SQL escape hatch for the long tail. The guard parses with sqlglot, allows
SELECT only against an allowlist of gold views, forces a LIMIT, runs under a read-only role,
and executes in an Athena workgroup with a per-query byte-scan cap. Athena bills by bytes
scanned, so an unpartitioned table plus a generated join is the single most plausible way this
budget dies.

The orchestration loop is written here rather than handed to a managed agent runtime, and it
uses the AWS SDK directly rather than an agent framework. Both are the same argument. The eval
suite is what decides whether the agent is allowed in front of anyone, it runs on every push,
and the job that runs it holds no AWS credentials; a managed loop would put the thing being
scored inside the account and force either credentials in CI or a local reimplementation of the
loop, which is the loop. Owning it also turns the rule about figures into an assertion: after
the model stops, every number in the answer is checked against the numbers the tools returned,
the numbers in the question and the numbers in the tool arguments, and an answer carrying
anything else is withheld and says which figures were loose. A framework would have bought
durable execution, human-in-the-loop interrupts and a graph, none of which a single-turn
read-only agent uses, at the cost of sitting between this code and the Converse fields it
actually needs, prompt caching among them.

Six of the nine tools read the published view artifacts rather than the marts, which is a
deviation from the original design and the more defensible answer. Form, clean pace, track fit
and the forecast are fitted quantities, not columns, and the view artifacts are where they are
published after passing the quality gate. Reading them means a figure in an answer and a figure
on the dashboard cannot disagree, and it means no tool contains a second implementation of a
metric. The cost is that those tools speak about the current event and the most recent fits;
anything historical goes through the guarded SQL, and the tools say so rather than guessing.

Some of this is deliberately over-engineered for the data volume. A medallion lake and a dbt
project for two hundred megabytes is more machinery than the problem needs, which is the point:
the parts being practised are the ones that only matter at scale. What is not accepted is
over-engineering that costs money, so NAT gateways, always-on services, managed Airflow and
real-time inference endpoints are excluded by construction.

## Running it locally

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). `just` is optional but is how the
AWS commands stay pinned to the right profile.

```bash
uv sync
uv run pre-commit install

uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright src/pitadvisor
uv run pitadv --help
```

The infrastructure is a separate uv project so that the CDK toolchain does not leak into the
application environment. The CDK CLI is installed as a Python dependency, so there is no npm
step and nothing to install globally.

```bash
uv sync --directory infra
uv run --directory infra cdk synth
```

Synthesis needs no AWS credentials. Anything that talks to the account does, and every such
command names its profile explicitly rather than inheriting one from the shell:

```bash
aws login --profile pitadvisor
uv run pitadv doctor
```

The ingest path runs without an account at all. `--local` swaps the S3 store, the DynamoDB
ledger and the token bucket for files under `data/local`, which is where the backfill, the
quality gate and the view emitter are usually exercised:

```bash
uv run pitadv ingest --source jolpica --season 2024 --dry-run   # prints the plan, fetches nothing
uv run pitadv backfill --from 2023 --to 2024 --local            # resumable, respects the hourly cap
uv run pitadv quality-report --layer bronze --local
uv run pitadv emit-views --local
```

The backfill is safe to interrupt and rerun. A resource whose bronze partition already exists
is skipped without a request, so a second run costs one request per season, and when the hourly
budget runs out the command says where it stopped rather than sleeping through it.

The transform layer runs on the same local lake, on duckdb, with no account involved:

```bash
uv run dbt build --project-dir transform --target local
uv run dbt test --project-dir transform --target local
uv run pitadv lineage --check --local
```

Against the account the same models run on Athena with `--target athena`, after
`pitadv catalog-sync` has pointed the Glue catalog at the bronze prefixes.

## Cost

There is no cost table yet, because nothing has been deployed. It gets filled in from Cost
Explorer at the end of each phase, scoped to the `project=pit-advisor` tag that every resource
carries, and it reports measured spend by service rather than estimates. `just cost` prints the
current month.

The design targets under six dollars a month of infrastructure against a hard ceiling of one
hundred dollars of credits for the life of the project, with a Budgets alarm at twenty dollars a
month. The only line item that can plausibly break that is Bedrock token spend, which is why the
default model is the cheapest one that passes the eval thresholds, prompt caching is on for the
system prompt and tool schemas, and the full eval suite runs on release tags rather than on every
push.

## Limitations

The forecast is a probability distribution over finishing positions and nothing more. It is
not betting advice, it does not size stakes, and the agent refuses questions framed that way.

Teammate normalization, which is the core instrument for separating driver from car, assumes
both cars are the same. Mid-season upgrades, damage and different engine modes break that
assumption, so affected sessions are flagged rather than quietly averaged in.

Clean-air pace is a fitted quantity with exclusions, not a measurement. Laps behind traffic,
under safety car, deleted for track limits, or in and out of the pits are all removed before
the fit. The count of laps dropped per reason is published as a diagnostic, because silently
discarding most of the field's laps produces a very clean model of almost nothing.

Historical coverage is limited by what FastF1 exposes, which is roughly 2018 onward for
lap-level detail, and regulation changes in 2022 and 2026 mean older data describes cars that no
longer exist, so time decay does most of the work of forgetting. None of that timing data is
redistributed here: the repository contains code, infrastructure, tests, and small result
artifacts only.
