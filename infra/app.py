import os

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from stacks import (
    AgentStack,
    DataStack,
    IngestStack,
    ObservabilityStack,
    TransformStack,
    WebStack,
)

# same source as the cli and the justfile: PITADV_ACCOUNT_ID, then this default
ACCOUNT = os.environ.get("PITADV_ACCOUNT_ID", "189575358467")
REGION = "ap-southeast-1"

app = cdk.App()
env_name: str = app.node.try_get_context("env") or "dev"
alert_email: str | None = app.node.try_get_context("alertEmail")
aws_env = cdk.Environment(account=ACCOUNT, region=REGION)

DataStack(app, f"pitadvisor-data-{env_name}", env_name=env_name, env=aws_env)
IngestStack(app, f"pitadvisor-ingest-{env_name}", env_name=env_name, env=aws_env)
TransformStack(app, f"pitadvisor-transform-{env_name}", env_name=env_name, env=aws_env)
# the agent comes first: the dashboard fronts its function url on /api/ask, so cloudfront
# needs the url to exist before the distribution that signs requests to it
agent = AgentStack(app, f"pitadvisor-agent-{env_name}", env_name=env_name, env=aws_env)
WebStack(
    app,
    f"pitadvisor-web-{env_name}",
    env_name=env_name,
    ask_url=agent.url,
    env=aws_env,
)
ObservabilityStack(
    app,
    f"pitadvisor-observability-{env_name}",
    env_name=env_name,
    alert_email=alert_email,
    env=aws_env,
)

cdk.Tags.of(app).add("project", "pit-advisor")
cdk.Tags.of(app).add("env", env_name)
cdk.Validations.of(app).add_plugins(AwsSolutionsChecks(app, verbose=True))

app.synth()
