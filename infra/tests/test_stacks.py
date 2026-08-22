from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template
from stacks import DataStack, IngestStack, ObservabilityStack, TransformStack

ACCOUNT = "123456789012"
REGION = "ap-southeast-1"
ENV_NAME = "test"
DATA_STACK = f"pitadvisor-data-{ENV_NAME}"
INGEST_STACK = f"pitadvisor-ingest-{ENV_NAME}"
OBSERVABILITY_STACK = f"pitadvisor-observability-{ENV_NAME}"
TRANSFORM_STACK = f"pitadvisor-transform-{ENV_NAME}"
DATA_BUCKET = f"pit-advisor-data-{ENV_NAME}-{ACCOUNT}"
RESULTS_BUCKET = f"pit-advisor-athena-results-{ENV_NAME}-{ACCOUNT}"
ALERT_EMAIL = "alerts@example.com"

Resource = dict[str, Any]


def build(
    alert_email: str | None = None,
) -> tuple[cdk.App, DataStack, IngestStack, ObservabilityStack, TransformStack]:
    app = cdk.App(context={"env": ENV_NAME})
    aws_env = cdk.Environment(account=ACCOUNT, region=REGION)
    data = DataStack(app, DATA_STACK, env_name=ENV_NAME, env=aws_env)
    ingest = IngestStack(app, INGEST_STACK, env_name=ENV_NAME, env=aws_env)
    observability = ObservabilityStack(
        app, OBSERVABILITY_STACK, env_name=ENV_NAME, alert_email=alert_email, env=aws_env
    )
    cdk.Tags.of(app).add("project", "pit-advisor")
    cdk.Tags.of(app).add("env", ENV_NAME)
    transform = TransformStack(app, TRANSFORM_STACK, env_name=ENV_NAME, env=aws_env)
    return app, data, ingest, observability, transform


def only(template: Template, kind: str, **props: Any) -> tuple[str, Resource]:
    found = template.find_resources(kind, {"Properties": props})
    assert len(found) == 1, f"{kind} matching {props}: {sorted(found)}"
    return found.popitem()


@pytest.fixture
def data_template() -> Template:
    data = build()[1]
    return Template.from_stack(data)


@pytest.fixture
def ingest_template() -> Template:
    ingest = build()[2]
    return Template.from_stack(ingest)


@pytest.fixture
def transform_template() -> Template:
    return Template.from_stack(build()[4])


@pytest.fixture
def observability_template() -> Template:
    observability = build()[3]
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
    observability = build(email)[3]
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
    app = build()[0]
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
        "AWS::IAM::ManagedPolicy",
        {"Properties": {"Users": ["pitadvisor-dev"]}},
    )
    assert len(policies) == 1, sorted(policies)
    return policies.popitem()[1]["Properties"]["PolicyDocument"]["Statement"]


def actions_of(statement: Resource) -> list[str]:
    action = statement["Action"]
    return [action] if isinstance(action, str) else list(action)


def test_dev_user_reads_the_lake_and_the_workgroup(data_template: Template) -> None:
    actions = {action for s in dev_policy(data_template) for action in actions_of(s)}
    assert {"s3:GetObject", "s3:PutObject", "athena:StartQueryExecution"} <= actions
    assert "glue:CreateTable" in actions
    assert not any(str(action).endswith("*") for action in actions)


def test_dev_user_cannot_touch_another_glue_database(data_template: Template) -> None:
    glue = [s for s in dev_policy(data_template) if "glue:CreateTable" in actions_of(s)]
    assert len(glue) == 1
    assert all(
        f"pitadvisor_{ENV_NAME}" in str(arn) or arn.endswith(":catalog")
        for arn in glue[0]["Resource"]
    )


def test_dev_user_writes_only_the_landing_prefixes(data_template: Template) -> None:
    writes = [
        s
        for s in dev_policy(data_template)
        if {"s3:PutObject", "s3:DeleteObject"} & set(s["Action"])
    ]
    lake = [s for s in writes if "AthenaResultsBucket" not in str(s["Resource"])]
    assert len(lake) == 1
    target = str(lake[0]["Resource"])
    for prefix in ("raw/*", "bronze/*", "silver/*", "gold/*", "quarantine/*", "views/*"):
        assert prefix in target
    assert "docs/" not in target
    assert "cache/" not in target


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
        policy["Properties"]["ManagedPolicyName"]
        for template in (data_template, observability_template)
        for policy in template.find_resources(
            "AWS::IAM::ManagedPolicy", {"Properties": {"Users": ["pitadvisor-dev"]}}
        ).values()
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
    app = build()[0]
    assert app.synth().get_stack_by_name(INGEST_STACK).tags == {
        "project": "pit-advisor",
        "env": ENV_NAME,
        "component": "ingest",
    }


def test_the_pipeline_vpc_has_no_nat_gateway(transform_template: Template) -> None:
    assert transform_template.find_resources("AWS::EC2::NatGateway") == {}
    subnets = transform_template.find_resources("AWS::EC2::Subnet")
    assert subnets
    assert all(
        subnet["Properties"].get("MapPublicIpOnLaunch") is True for subnet in subnets.values()
    )


def test_the_pipeline_task_is_arm_and_small(transform_template: Template) -> None:
    _, task = only(transform_template, "AWS::ECS::TaskDefinition")
    assert task["Properties"]["RuntimePlatform"]["CpuArchitecture"] == "ARM64"
    assert task["Properties"]["Cpu"] == "512"
    assert task["Properties"]["RequiresCompatibilities"] == ["FARGATE"]


def test_the_session_step_gets_a_cache_directory(transform_template: Template) -> None:
    _, task = only(transform_template, "AWS::ECS::TaskDefinition")
    environment = task["Properties"]["ContainerDefinitions"][0]["Environment"]
    assert {"Name": "PITADV_FASTF1_CACHE", "Value": "/tmp/fastf1"} in environment


def test_nothing_in_the_transform_stack_runs_a_service(transform_template: Template) -> None:
    assert transform_template.find_resources("AWS::ECS::Service") == {}


def test_every_log_group_expires(transform_template: Template) -> None:
    groups = transform_template.find_resources("AWS::Logs::LogGroup")
    assert len(groups) == 2
    assert all(group["Properties"].get("RetentionInDays") for group in groups.values())


def test_the_pipeline_builds_bronze_then_silver_then_views(transform_template: Template) -> None:
    _, machine = only(transform_template, "AWS::StepFunctions::StateMachine")
    definition = str(machine["Properties"]["DefinitionString"])
    for step in ("IngestJolpica", "QualityGate", "SyncCatalog", "DbtBuild", "CheckLineage"):
        assert step in definition
    assert definition.index("QualityGate") < definition.index("DbtBuild")
    assert "dbt build --project-dir transform --target athena" in definition


def test_a_failed_quality_gate_stops_the_pipeline(transform_template: Template) -> None:
    _, machine = only(transform_template, "AWS::StepFunctions::StateMachine")
    definition = str(machine["Properties"]["DefinitionString"])
    assert "QuarantineHalt" in definition
    assert "QualityGateFailed" in definition


def test_the_pipeline_role_cannot_reach_another_task(transform_template: Template) -> None:
    statements = [
        statement
        for policy in transform_template.find_resources("AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if "ecs:RunTask" in actions_of(statement)
    ]
    assert len(statements) == 1
    assert (
        statements[0]["Resource"]
        == f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/pitadvisor-pipeline-{ENV_NAME}:*"
    )


def test_the_schedule_is_off_until_it_is_asked_for(transform_template: Template) -> None:
    _, rule = only(transform_template, "AWS::Events::Rule", ScheduleExpression=Match.any_value())
    assert rule["Properties"]["State"] == "DISABLED"


def test_the_pipeline_gets_the_lake_policy_from_the_data_stack(
    data_template: Template, transform_template: Template
) -> None:
    _, policy = only(
        data_template,
        "AWS::IAM::ManagedPolicy",
        ManagedPolicyName=f"pitadvisor-pipeline-lake-{ENV_NAME}",
    )
    assert policy["Properties"]["ManagedPolicyName"] == f"pitadvisor-pipeline-lake-{ENV_NAME}"
    _, role = only(
        transform_template,
        "AWS::IAM::Role",
        Description="what the ingest, dbt and view steps are allowed to touch",
    )
    assert str(role["Properties"]["ManagedPolicyArns"]).count(
        f"pitadvisor-pipeline-lake-{ENV_NAME}"
    )


def test_a_laptop_can_push_the_image_and_start_the_pipeline(transform_template: Template) -> None:
    _, policy = only(
        transform_template,
        "AWS::IAM::ManagedPolicy",
        ManagedPolicyName=f"pitadvisor-image-push-{ENV_NAME}",
    )
    statements = policy["Properties"]["PolicyDocument"]["Statement"]
    actions = {action for s in statements for action in actions_of(s)}
    assert {"ecr:PutImage", "states:StartExecution"} <= actions
    unscoped = [s for s in statements if s["Resource"] == "*"]
    assert [actions_of(s) for s in unscoped] == [["ecr:GetAuthorizationToken"]]


def test_the_image_repository_keeps_five_tags(transform_template: Template) -> None:
    _, repository = only(transform_template, "AWS::ECR::Repository")
    assert '"countNumber":5' in repository["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"]
    assert repository["Properties"]["ImageScanningConfiguration"]["ScanOnPush"] is True
