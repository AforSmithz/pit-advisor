import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from stacks import DataStack, IngestStack, ObservabilityStack, TransformStack

ACCOUNT = "352445792687"
REGION = "ap-southeast-1"

app = cdk.App()
env_name: str = app.node.try_get_context("env") or "dev"
alert_email: str | None = app.node.try_get_context("alertEmail")
aws_env = cdk.Environment(account=ACCOUNT, region=REGION)

DataStack(app, f"pitadvisor-data-{env_name}", env_name=env_name, env=aws_env)
IngestStack(app, f"pitadvisor-ingest-{env_name}", env_name=env_name, env=aws_env)
TransformStack(app, f"pitadvisor-transform-{env_name}", env_name=env_name, env=aws_env)
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
