from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template
from stacks import DataStack, IngestStack, ObservabilityStack

ACCOUNT = "123456789012"
REGION = "ap-southeast-1"
ENV_NAME = "test"
DATA_STACK = f"pitadvisor-data-{ENV_NAME}"
INGEST_STACK = f"pitadvisor-ingest-{ENV_NAME}"
OBSERVABILITY_STACK = f"pitadvisor-observability-{ENV_NAME}"
DATA_BUCKET = f"pit-advisor-data-{ENV_NAME}-{ACCOUNT}"
RESULTS_BUCKET = f"pit-advisor-athena-results-{ENV_NAME}-{ACCOUNT}"
ALERT_EMAIL = "alerts@example.com"

Resource = dict[str, Any]


def build(
    alert_email: str | None = None,
) -> tuple[cdk.App, DataStack, IngestStack, ObservabilityStack]:
    app = cdk.App(context={"env": ENV_NAME})
    aws_env = cdk.Environment(account=ACCOUNT, region=REGION)
    data = DataStack(app, DATA_STACK, env_name=ENV_NAME, env=aws_env)
    ingest = IngestStack(app, INGEST_STACK, env_name=ENV_NAME, env=aws_env)
    observability = ObservabilityStack(
        app, OBSERVABILITY_STACK, env_name=ENV_NAME, alert_email=alert_email, env=aws_env
    )
    cdk.Tags.of(app).add("project", "pit-advisor")
    cdk.Tags.of(app).add("env", ENV_NAME)
    return app, data, ingest, observability


def only(template: Template, kind: str, **props: Any) -> tuple[str, Resource]:
    found = template.find_resources(kind, {"Properties": props})
    assert len(found) == 1, f"{kind} matching {props}: {sorted(found)}"
    return found.popitem()


@pytest.fixture
def data_template() -> Template:
    _, data, _, _ = build()
    return Template.from_stack(data)


@pytest.fixture
def ingest_template() -> Template:
    _, _, ingest, _ = build()
    return Template.from_stack(ingest)


@pytest.fixture
def observability_template() -> Template:
    _, _, _, observability = build()
    return Template.from_stack(observability)


def test_data_bucket_is_private(data_template: Template) -> None:
    _, bucket = only(data_template, "AWS::S3::Bucket", BucketName=DATA_BUCKET)
    assert bucket["Properties"]["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }


def test_data_bucket_is_encrypted_versioned_and_retained(data_template: Template) -> None:
    _, bucket = only(data_template, "AWS::S3::Bucket", BucketName=DATA_BUCKET)
    sse = bucket["Properties"]["BucketEncryption"]["ServerSideEncryptionConfiguration"]
    assert sse[0]["ServerSideEncryptionByDefault"]["SSEAlgorithm"]
    assert bucket["Properties"]["VersioningConfiguration"] == {"Status": "Enabled"}
    assert bucket["DeletionPolicy"] == "Retain"
    assert bucket["UpdateReplacePolicy"] == "Retain"


def test_data_bucket_denies_plaintext_requests(data_template: Template) -> None:
    bucket_id, _ = only(data_template, "AWS::S3::Bucket", BucketName=DATA_BUCKET)
    data_template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "Bucket": {"Ref": bucket_id},
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Effect": "Deny",
                                "Action": "s3:*",
                                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                            }
                        )
                    ]
                )
            },
        },
    )


def test_workgroup_caps_the_scan_and_writes_to_the_results_bucket(data_template: Template) -> None:
    results_id, _ = only(data_template, "AWS::S3::Bucket", BucketName=RESULTS_BUCKET)
    _, workgroup = only(data_template, "AWS::Athena::WorkGroup")
    config = workgroup["Properties"]["WorkGroupConfiguration"]
    assert config["EnforceWorkGroupConfiguration"] is True
    assert config["BytesScannedCutoffPerQuery"] > 0
    assert config["ResultConfiguration"]["OutputLocation"]["Fn::Join"][1] == [
        "s3://",
        {"Ref": results_id},
        "/athena/",
    ]


def test_glue_database_is_env_suffixed(data_template: Template) -> None:
    _, database = only(data_template, "AWS::Glue::Database")
    assert database["Properties"]["CatalogId"] == ACCOUNT
    assert database["Properties"]["DatabaseInput"]["Name"] == f"pitadvisor_{ENV_NAME}"


def test_project_budget_is_capped_and_tag_filtered(observability_template: Template) -> None:
    _, project = only(
        observability_template,
        "AWS::Budgets::Budget",
        Budget={"BudgetName": "pit-advisor-monthly"},
    )
    budget = project["Properties"]["Budget"]
    assert budget["BudgetType"] == "COST"
    assert budget["TimeUnit"] == "MONTHLY"
    assert budget["BudgetLimit"] == {"Amount": 20, "Unit": "USD"}
    assert budget["CostFilters"] == {"TagKeyValue": ["user:project$pit-advisor"]}


def test_account_budget_watches_the_shared_credits(observability_template: Template) -> None:
    observability_template.resource_count_is("AWS::Budgets::Budget", 2)
    _, account = only(
        observability_template,
        "AWS::Budgets::Budget",
        Budget={"BudgetName": "pit-advisor-account-monthly"},
    )
    budget = account["Properties"]["Budget"]
    assert budget["BudgetType"] == "COST"
    assert budget["TimeUnit"] == "MONTHLY"
    assert budget["BudgetLimit"] == {"Amount": 40, "Unit": "USD"}
    assert "CostFilters" not in budget


@pytest.mark.parametrize("email", [None, ALERT_EMAIL])
def test_budget_alerts_follow_the_alert_email_context(email: str | None) -> None:
    _, _, _, observability = build(email)
    found = Template.from_stack(observability).find_resources("AWS::Budgets::Budget")
    assert len(found) == 2
    for budget in found.values():
        alerts = budget["Properties"].get("NotificationsWithSubscribers")
        if email is None:
            assert alerts is None
            continue
        triggers = [
            (a["Notification"]["NotificationType"], a["Notification"]["Threshold"]) for a in alerts
        ]
        assert triggers == [("ACTUAL", 80), ("FORECASTED", 100)]
        for alert in alerts:
            assert alert["Notification"]["ThresholdType"] == "PERCENTAGE"
            assert alert["Subscribers"] == [{"Address": email, "SubscriptionType": "EMAIL"}]


def test_stacks_are_tagged() -> None:
    app, _, _, _ = build()
    assembly = app.synth()
    # budgets are not taggable, so the stack tag is the only place the observability tags land
    assert assembly.get_stack_by_name(DATA_STACK).tags == {
        "project": "pit-advisor",
        "env": ENV_NAME,
        "component": "data",
    }
    assert assembly.get_stack_by_name(OBSERVABILITY_STACK).tags == {
        "project": "pit-advisor",
        "env": ENV_NAME,
        "component": "observability",
    }


def test_data_resources_are_tagged(data_template: Template) -> None:
    _, bucket = only(data_template, "AWS::S3::Bucket", BucketName=DATA_BUCKET)
    _, workgroup = only(data_template, "AWS::Athena::WorkGroup")
    for resource in (bucket, workgroup):
        assert {t["Key"]: t["Value"] for t in resource["Properties"]["Tags"]} == {
            "project": "pit-advisor",
            "env": ENV_NAME,
            "component": "data",
        }


def dev_policy(template: Template) -> Resource:
    policies = template.find_resources(
        "AWS::IAM::Policy",
        {"Properties": {"Users": ["pitadvisor-dev"]}},
    )
    assert len(policies) == 1, sorted(policies)
    return policies.popitem()[1]["Properties"]["PolicyDocument"]["Statement"]


def actions_of(statement: Resource) -> list[str]:
    action = statement["Action"]
    return [action] if isinstance(action, str) else list(action)


def test_dev_user_reads_the_lake_and_the_workgroup(data_template: Template) -> None:
    actions = {action for s in dev_policy(data_template) for action in actions_of(s)}
    assert actions == {
        "s3:ListBucket",
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "athena:GetWorkGroup",
        "athena:StartQueryExecution",
    }
    assert not any(str(action).endswith("*") for action in actions)


def test_dev_user_writes_only_the_landing_prefixes(data_template: Template) -> None:
    writes = [
        s
        for s in dev_policy(data_template)
        if {"s3:PutObject", "s3:DeleteObject"} & set(s["Action"])
    ]
    lake = [s for s in writes if "AthenaResultsBucket" not in str(s["Resource"])]
    assert len(lake) == 1
    target = str(lake[0]["Resource"])
    for prefix in ("raw/*", "bronze/*", "quarantine/*", "views/*"):
        assert prefix in target
    assert "gold/" not in target
    assert "docs/" not in target


def test_dev_user_gets_cost_explorer_reads(observability_template: Template) -> None:
    statements = dev_policy(observability_template)
    assert len(statements) == 1
    assert set(statements[0]["Action"]) == {
        "ce:GetCostAndUsage",
        "ce:ListCostAllocationTags",
        "ce:UpdateCostAllocationTagsStatus",
    }
    assert statements[0]["Resource"] == "*"


def test_dev_policies_are_named_per_stack(
    data_template: Template, observability_template: Template
) -> None:
    names = [
        next(iter(t.find_resources("AWS::IAM::Policy").values()))["Properties"]["PolicyName"]
        for t in (data_template, observability_template)
    ]
    # an unnamed policy on the same user generates the same physical name in both stacks and
    # the second deploy fails with "already managed by another stack"
    assert len(set(names)) == 2
    assert all(ENV_NAME in name for name in names)


def test_ledger_table_is_on_demand_with_a_ttl(ingest_template: Template) -> None:
    _, table = only(
        ingest_template,
        "AWS::DynamoDB::GlobalTable",
        TableName=f"pitadvisor-ingest-{ENV_NAME}",
    )
    properties = table["Properties"]
    assert properties["BillingMode"] == "PAY_PER_REQUEST"
    assert properties["KeySchema"] == [{"AttributeName": "pk", "KeyType": "HASH"}]
    assert properties["TimeToLiveSpecification"] == {
        "AttributeName": "expires_at",
        "Enabled": True,
    }


def test_ledger_table_is_encrypted(ingest_template: Template) -> None:
    _, table = only(
        ingest_template,
        "AWS::DynamoDB::GlobalTable",
        TableName=f"pitadvisor-ingest-{ENV_NAME}",
    )
    replica = table["Properties"]["Replicas"][0]
    assert table["Properties"]["SSESpecification"] == {"SSEEnabled": True, "SSEType": "KMS"}
    # the ledger is a cache, raw/ is the record, so no point in time recovery is paid for
    assert "PointInTimeRecoverySpecification" not in replica


def test_dev_user_gets_item_level_access_to_the_ledger(ingest_template: Template) -> None:
    statements = dev_policy(ingest_template)
    assert len(statements) == 1
    actions = set(actions_of(statements[0]))
    assert actions == {
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:DescribeTable",
    }
    assert not any(action.endswith("*") for action in actions)
    assert "Scan" not in str(actions)


def test_ingest_stack_is_tagged() -> None:
    app, _, _, _ = build()
    assert app.synth().get_stack_by_name(INGEST_STACK).tags == {
        "project": "pit-advisor",
        "env": ENV_NAME,
        "component": "ingest",
    }
