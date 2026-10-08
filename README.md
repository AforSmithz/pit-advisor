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

All of it, end to end, in one AWS account. Three sources land in the lake: Jolpica results,
qualifying, pit stops and schedules, Open-Meteo weather snapshots per event, and FastF1 race
session laps. They all go through the same path: a conditional HTTP GET under a token bucket, the
response written to `raw/` verbatim with its request metadata, a parse into typed rows, a pydantic
contract per table, and Parquet in `bronze/` partitioned by season and round. Rows that fail their
contract land in `quarantine/` with the reason attached instead of failing the load. The FIA's
stewards' decisions are crawled the same way and parsed into incidents, sanctions and the
regulation articles they cite.

Above bronze, a dbt project builds conformed silver tables with surrogate keys and
amendment-aware deduplication, and gold marts on top of them. The same models run on duckdb
against local Parquet, which is how they are developed and tested, and on Athena as Iceberg
tables with MERGE, which is how they run in the account. `pitadv lineage --check` walks every
gold model back through silver to the bronze sources and on to the raw objects each one was
built from, and fails if any cannot be traced.

On top of the marts sit the metrics: teammate-normalised clean-air race pace, the qualifying to
race delta, two independent track-fit estimators that are shown side by side and never blended,
a weather scenario mix, and a pooled retirement hazard. A Monte Carlo race simulation turns them
into a finishing-position distribution for the next race, and a time-forward backtest judges it.
Each of these is published as a versioned JSON view that passed the quality gate, and a static
Next.js dashboard renders the views. A Bedrock agent answers questions over the same views, the
marts and a regulations and race-report corpus, with citations.

The lake covers 2021 to the current race of 2026. Every Thursday a state machine checks the
calendar, and in a race week it refreshes the weather, the sessions, the marts and every view,
including a short weekend brief at the top of the weekend page.

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
figures that has to be zero. The run that cleared the gate scored 95.1% on numeric exact match
against a 95% floor, 100% on retrieval and 98.6% on tool selection against 90% floors, with zero
ungrounded figures across seventy-one cases. The numeric score sits one case above its floor, and
the two cases it missed are written up in `results/evals/` rather than tuned away. Earlier runs
failed on a single case each, always the same shape: the model subtracts one tool result from
another and states the difference, which is a figure no tool returned. The check withholds that
answer every time, which is the gate doing its job rather than a gate worth lowering.

The forecast clears its own bar too, though by less than a chart would suggest. The holdout is
the last sixty races run, from the 2024 Chinese Grand Prix to the sixteenth round of 2026, so
sixteen of them are in 2026's twenty-two-car field. The simulation's multiclass log loss is
2.589 [2.545, 2.635], against 2.608 for grid position alone, 2.726 for championship standings
and 2.825 for last race's result. Resampled at the race
level, it is separated from standings and from last race, and it is not separated from the grid
on log loss, only on Brier: sixty races cannot tell the simulation and the starting order apart.
That is the finding, and the calibration page leads with it.

Two things are worth knowing about how that number was reached. A race in a twenty-two-car field
is not scored against a baseline that has only ever seen twenty places: each past race is
stretched onto the field being predicted by share of the field, so last of twenty counts as last
of twenty-two. An earlier version gave the baselines a token floor for the two new places
instead, and the simulation came out separated from the grid by a wide margin. Almost all of that
margin was the two places the baselines could not imagine, and it was a bug rather than a result.
The same re-run also closed two small leaks the first backtest had: two spread parameters were
measured over the whole lake rather than as of each race, and a circuit's first race took its
distance from every race in the lake, later ones included. Fixed and re-run on the original sixty
races, they move the simulation's log loss in the fifth decimal place.

## Architecture

```
EventBridge rule (Thursday 06:00 UTC)
  |
  +-- Step Functions  weekend-pipeline        one Fargate task definition, one image
        |
        +-- weekend plan         refresh the calendar, refetch the last result, decide
        +-- read plan            S3 integration, no Lambda
        +-- race week?           no: stop here, one small task spent
        +-- ingest open-meteo    forecast for the coming race
        +-- ingest fastf1        race sessions not yet in the lake, S3-backed cache
        +-- quality gate         contracts, freshness, keys, references
        +-- catalog sync         glue bronze tables from the contracts
        +-- dbt build            silver and gold, Iceberg MERGE
        +-- lineage check        every gold model traced back to raw
        +-- emit views           weekend, driver, track, forecast, brief, pipeline

S3 (one bucket, prefix separated)
  raw/  bronze/  silver/  gold/  views/  quarantine/  docs/  cache/

Glue Data Catalog        table metadata
Athena                   SQL, byte-scan capped workgroup
DynamoDB                 request ledger, run state
Bedrock                  Knowledge Base on S3 Vectors, agent runtime, Guardrails
CloudFront + S3          static Next.js dashboard reading views/*.json
CloudWatch + Budgets     logs, a latency dashboard, the spend ceiling
Cost Explorer            measured spend, read by pitadv cost-report
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
uses the AWS SDK directly rather than an agent framework. Both are the same argument. The loop
is what the eval suite scores, so it has to run under test without an account: the unit suite
drives it with a stubbed Bedrock client on every push, and the CI job holds no AWS credentials. A
managed loop would put the thing being scored inside the account and force either credentials in
CI or a local reimplementation of the loop, which is the loop. Owning it also turns the rule about figures into an assertion: after
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

The weekly run decides before it works. A Fargate step reads the calendar and leaves a small plan
in the lake, the state machine reads it back through its S3 integration, and a week without a
race ends there. The alternative was a Lambda whose only job is a date comparison, or a list of
race dates copied into EventBridge Scheduler, which would put the calendar in two places. The
plan file also means any past Thursday's decision can be read back after the fact.

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

Two commands only make sense against the account. `pitadv weekend-plan` is the first step of the
Thursday run and can be run by hand to see what it would decide, and `pitadv cost-report` reads
Cost Explorer and fails above the ceiling:

```bash
uv run pitadv weekend-plan --no-refresh
uv run pitadv cost-report --month current
```

## Cost

Measured from Cost Explorer by `pitadv cost-report`, not estimated. The figures are usage before
credits, because the account runs on credits and a net figure would read zero every month. The
account hosts nothing but this project, so the whole-account figure is the project's figure.

| | September 2026 | October 2026, to the 7th |
|---|---|---|
| S3 | $0.46 | $0.20 |
| Fargate (ECS) | $0.29 | |
| VPC (public IPv4 for the tasks) | $0.06 | |
| Bedrock embeddings (Cohere) | | $0.24 |
| ECR | $0.01 | $0.02 |
| DynamoDB and Athena | $0.01 | |
| **Total** | **$0.83** | **$0.46** |

September includes refetching the entire lake from upstream after moving accounts, which is the
most expensive thing the system ever does. October so far is mostly the one-off embedding of the
corpus into the knowledge base. Both measured months are under a dollar against a ceiling of
twenty, and both carry one-off work, so neither is a steady state yet; the budgets alarm at eighty
percent of the ceiling. The only line
that can plausibly break that is Bedrock token spend, which is why the answering model is the
cheapest one that passes the eval thresholds and the full eval suite is a deliberate command
rather than a per-push job. `pitadv cost-report` exits non-zero above the ceiling, and its
reports are committed under `results/cost/`.

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
longer exist, so time decay does most of the work of forgetting. 2026 is the hard case: it is a
full regulation reset, and every prior the early-season numbers lean on was fitted on the
previous generation of cars. The intervals widen as evidence thins, but they do not know about
the reset.

The Thursday forecast runs before qualifying, so the grid is not known. Each simulated race
then runs its own qualifying first, drawn from the same pace the race uses plus each driver's
Saturday-to-Sunday conversion, which spreads the favourites further than a grid-conditioned
forecast would. The published backtest is unaffected: it scores races on the grid they actually
had.

The circuit taxonomy is hand-maintained, and a circuit missing from it gets no track fit and no
forecast. Madrid and Sepang joined in 2026. Their geometry and most of their demand bands come
from the circuit owners, Brembo and Pirelli, but nobody publishes a traction or kerb rating for
either, so those sit at the middle band rather than at a guess. Sepang's braking band rests on
Brembo's 2017 figures, the last time Formula 1 raced there.

The agent is built and has passed its gate, but in the account it runs in today it cannot
answer: a new AWS account ships with Bedrock's text-generation quota at zero, and lifting it is a
sales conversation rather than a quota request. Embeddings work, so the knowledge base is
indexed and ready. Until the quota opens, the weekend brief is assembled deterministically from
the views; an agent-written brief is the intended version. None of that timing data is
redistributed here: the repository contains code, infrastructure, tests, and small result
artifacts only.
