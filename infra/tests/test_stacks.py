import json
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template
from stacks import (
    AgentStack,
    DataStack,
    IngestStack,
    ObservabilityStack,
    TransformStack,
    WebStack,
)

ACCOUNT = "123456789012"
REGION = "ap-southeast-1"
ENV_NAME = "test"
DATA_STACK = f"pitadvisor-data-{ENV_NAME}"
INGEST_STACK = f"pitadvisor-ingest-{ENV_NAME}"
OBSERVABILITY_STACK = f"pitadvisor-observability-{ENV_NAME}"
TRANSFORM_STACK = f"pitadvisor-transform-{ENV_NAME}"
WEB_STACK = f"pitadvisor-web-{ENV_NAME}"
AGENT_STACK = f"pitadvisor-agent-{ENV_NAME}"
DATA_BUCKET = f"pit-advisor-data-{ENV_NAME}-{ACCOUNT}"
RESULTS_BUCKET = f"pit-advisor-athena-results-{ENV_NAME}-{ACCOUNT}"
SITE_BUCKET = f"pit-advisor-web-{ENV_NAME}-{ACCOUNT}"
ALERT_EMAIL = "alerts@example.com"

Resource = dict[str, Any]


def build(
    alert_email: str | None = None,
) -> tuple[
    cdk.App, DataStack, IngestStack, ObservabilityStack, TransformStack, WebStack, AgentStack
]:
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
    web = WebStack(app, WEB_STACK, env_name=ENV_NAME, env=aws_env)
    agent = AgentStack(app, AGENT_STACK, env_name=ENV_NAME, env=aws_env)
    return app, data, ingest, observability, transform, web, agent


def sole(template: Template, kind: str) -> Resource:
    found = template.find_resources(kind)
    assert len(found) == 1, f"{kind}: {sorted(found)}"
    return found.popitem()[1]


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
def web_template() -> Template:
    return Template.from_stack(build()[5])


@pytest.fixture
def agent_template() -> Template:
    return Template.from_stack(build()[6])


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
    # docs/ joined the list when the corpus builder became a laptop command
    assert "docs/*" in target
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


def test_the_task_names_no_aws_profile(transform_template: Template) -> None:
    _, task = only(transform_template, "AWS::ECS::TaskDefinition")
    environment = task["Properties"]["ContainerDefinitions"][0]["Environment"]
    assert {"Name": "PITADV_AWS_PROFILE", "Value": ""} in environment


def test_nothing_in_the_transform_stack_runs_a_service(transform_template: Template) -> None:
    assert transform_template.find_resources("AWS::ECS::Service") == {}


def test_every_log_group_expires(transform_template: Template) -> None:
    groups = transform_template.find_resources("AWS::Logs::LogGroup")
    assert len(groups) == 3
    assert all(group["Properties"].get("RetentionInDays") for group in groups.values())


def machine_definition(template: Template, name: str) -> str:
    _, machine = only(template, "AWS::StepFunctions::StateMachine", StateMachineName=name)
    return str(machine["Properties"]["DefinitionString"])


def weekend_definition(template: Template) -> str:
    return machine_definition(template, f"pitadvisor-weekend-{ENV_NAME}")


def test_the_backfill_walks_seasons_of_race_sessions(transform_template: Template) -> None:
    definition = machine_definition(transform_template, f"pitadvisor-backfill-{ENV_NAME}")
    assert "pitadv backfill --source fastf1 --from $FROM --to $TO --session race" in definition
    assert '"TimeoutSeconds":21600' in definition
    # it walks the whole range in one task, so it never sees a season or a round
    assert "$.season" not in definition


def test_the_two_machines_share_one_role(transform_template: Template) -> None:
    roles = {
        str(machine["Properties"]["RoleArn"])
        for machine in transform_template.find_resources(
            "AWS::StepFunctions::StateMachine"
        ).values()
    }
    assert len(roles) == 1


def test_the_pipeline_builds_bronze_then_silver_then_views(transform_template: Template) -> None:
    definition = weekend_definition(transform_template)
    for step in ("IngestJolpica", "QualityGate", "SyncCatalog", "DbtBuild", "CheckLineage"):
        assert step in definition
    assert definition.index("QualityGate") < definition.index("DbtBuild")
    assert "dbt build --project-dir transform --target athena_task" in definition
    # a weekend pipeline ingests its own weekend, a season would not fit the quota or the step
    assert "pitadv ingest --source jolpica --season $SEASON --round $ROUND" in definition


def test_the_pipeline_emits_every_view_the_dashboard_reads(transform_template: Template) -> None:
    definition = weekend_definition(transform_template)
    # the default is pipeline only, which leaves the dashboard republishing stale numbers
    assert "pitadv emit-views --views pipeline,weekend,driver,track" in definition
    assert definition.index("CheckLineage") < definition.index("EmitViews")


def test_a_step_cannot_overwrite_the_season_and_round(transform_template: Template) -> None:
    definition = weekend_definition(transform_template)
    assert definition.count('"ResultPath":"$.lastTask"') == 8
    assert "$.season" in definition


def test_a_failed_quality_gate_stops_the_pipeline(transform_template: Template) -> None:
    definition = weekend_definition(transform_template)
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
    writes = [
        statement
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if "s3:PutObject" in actions_of(statement)
    ]
    # the fastf1 cache is written by the pipeline and by nothing else
    assert any("cache/*" in str(statement["Resource"]) for statement in writes)


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


def test_the_site_bucket_is_private(web_template: Template) -> None:
    _, bucket = only(web_template, "AWS::S3::Bucket", BucketName=SITE_BUCKET)
    assert bucket["Properties"]["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }


def test_only_cloudfront_can_read_the_site(web_template: Template) -> None:
    policies = web_template.find_resources("AWS::S3::BucketPolicy")
    statements = [
        statement
        for policy in policies.values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if statement["Effect"] == "Allow"
    ]
    readers = [
        statement
        for statement in statements
        if statement.get("Principal", {}).get("Service") == "cloudfront.amazonaws.com"
    ]
    assert len(readers) == 1
    assert "AWS:SourceArn" in readers[0]["Condition"]["StringEquals"]


def test_the_distribution_redirects_to_https(web_template: Template) -> None:
    distribution = sole(web_template, "AWS::CloudFront::Distribution")
    behaviour = distribution["Properties"]["DistributionConfig"]["DefaultCacheBehavior"]
    assert behaviour["ViewerProtocolPolicy"] == "redirect-to-https"
    assert behaviour["Compress"] is True


def test_a_directory_request_gets_an_index(web_template: Template) -> None:
    function = sole(web_template, "AWS::CloudFront::Function")
    assert "index.html" in function["Properties"]["FunctionCode"]


def test_the_site_carries_a_content_security_policy(web_template: Template) -> None:
    policy = sole(web_template, "AWS::CloudFront::ResponseHeadersPolicy")
    headers = policy["Properties"]["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"]
    assert headers["ContentSecurityPolicy"]["ContentSecurityPolicy"].startswith("default-src")
    assert headers["StrictTransportSecurity"]["AccessControlMaxAgeSec"] == 31536000


def test_the_publish_role_trusts_one_repo_and_one_branch(web_template: Template) -> None:
    roles = web_template.find_resources("AWS::IAM::Role")
    assert len(roles) == 1
    trust = next(iter(roles.values()))["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Action"] == "sts:AssumeRoleWithWebIdentity"
    conditions = trust["Condition"]["StringEquals"]
    assert conditions["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    subject = conditions["token.actions.githubusercontent.com:sub"]
    assert subject.endswith(":ref:refs/heads/main")
    assert subject.startswith("repo:")


def test_the_publish_role_cannot_write_to_the_lake(web_template: Template) -> None:
    policies = web_template.find_resources("AWS::IAM::Policy")
    writes = [
        statement
        for policy in policies.values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        for action in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
        if action in {"s3:PutObject", "s3:DeleteObject"}
    ]
    rendered = json.dumps(writes)
    assert "pit-advisor-data" not in rendered
    assert writes, "the role has to be able to write the site itself"


def test_the_views_grant_is_read_only(data_template: Template) -> None:
    policies = data_template.find_resources(
        "AWS::IAM::ManagedPolicy",
        {"Properties": {"ManagedPolicyName": f"pitadvisor-views-read-{ENV_NAME}"}},
    )
    assert len(policies) == 1
    actions = {
        action
        for policy in policies.values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        for action in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    }
    assert actions == {"s3:ListBucket", "s3:GetObject"}


def agent_role_policies(template: Template) -> list[Resource]:
    return [
        statement
        for policy in template.find_resources("AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
    ]


def test_the_vector_index_matches_the_embedding_model(agent_template: Template) -> None:
    index = sole(agent_template, "AWS::S3Vectors::Index")["Properties"]
    assert index["Dimension"] == 1024
    assert index["DistanceMetric"] == "cosine"
    assert index["DataType"] == "float32"


def test_the_index_points_at_the_bucket_by_arn_not_by_a_ref_that_is_one(
    agent_template: Template,
) -> None:
    index = sole(agent_template, "AWS::S3Vectors::Index")["Properties"]
    assert "VectorBucketName" not in index
    assert index["VectorBucketArn"]


def test_bedrocks_own_metadata_is_kept_out_of_the_filterable_budget(
    agent_template: Template,
) -> None:
    index = sole(agent_template, "AWS::S3Vectors::Index")["Properties"]
    keys = index["MetadataConfiguration"]["NonFilterableMetadataKeys"]
    assert set(keys) == {"AMAZON_BEDROCK_METADATA", "AMAZON_BEDROCK_TEXT"}


def test_the_knowledge_base_stores_its_vectors_in_s3_vectors(agent_template: Template) -> None:
    base = sole(agent_template, "AWS::Bedrock::KnowledgeBase")["Properties"]
    assert base["StorageConfiguration"]["Type"] == "S3_VECTORS"
    embedding = base["KnowledgeBaseConfiguration"]["VectorKnowledgeBaseConfiguration"]
    assert "cohere.embed-english-v3" in str(embedding["EmbeddingModelArn"])


def test_the_knowledge_base_role_cannot_be_borrowed_by_another_account(
    agent_template: Template,
) -> None:
    _, role = only(agent_template, "AWS::IAM::Role", RoleName=f"pitadvisor-kb-{ENV_NAME}")
    statement = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert statement["Principal"]["Service"] == "bedrock.amazonaws.com"
    assert statement["Condition"]["StringEquals"]["aws:SourceAccount"] == ACCOUNT
    assert "knowledge-base/*" in str(statement["Condition"]["ArnLike"]["aws:SourceArn"])


def test_only_the_docs_prefix_is_indexed(agent_template: Template) -> None:
    source = sole(agent_template, "AWS::Bedrock::DataSource")["Properties"]
    s3 = source["DataSourceConfiguration"]["S3Configuration"]
    assert s3["InclusionPrefixes"] == ["docs/"]
    assert DATA_BUCKET in str(s3["BucketArn"])


def test_the_corpus_is_chunked_rather_than_indexed_whole(agent_template: Template) -> None:
    source = sole(agent_template, "AWS::Bedrock::DataSource")["Properties"]
    chunking = source["VectorIngestionConfiguration"]["ChunkingConfiguration"]
    assert chunking["ChunkingStrategy"] == "FIXED_SIZE"
    assert chunking["FixedSizeChunkingConfiguration"]["MaxTokens"] == 300


def test_the_guardrail_denies_staking_advice(agent_template: Template) -> None:
    guardrail = sole(agent_template, "AWS::Bedrock::Guardrail")["Properties"]
    topic = guardrail["TopicPolicyConfig"]["TopicsConfig"][0]
    assert topic["Type"] == "DENY"
    assert "staking" in topic["Definition"].lower()
    assert topic["Examples"]


def test_the_guardrail_checks_grounding_and_relevance(agent_template: Template) -> None:
    guardrail = sole(agent_template, "AWS::Bedrock::Guardrail")["Properties"]
    filters = guardrail["ContextualGroundingPolicyConfig"]["FiltersConfig"]
    assert {item["Type"] for item in filters} == {"GROUNDING", "RELEVANCE"}
    assert all(item["Threshold"] > 0 for item in filters)


def test_both_functions_run_on_arm_with_an_explicit_timeout(agent_template: Template) -> None:
    functions = agent_template.find_resources("AWS::Lambda::Function")
    assert len(functions) == 2
    for function in functions.values():
        assert function["Properties"]["Architectures"] == ["arm64"]
        assert function["Properties"]["Timeout"] > 0
        assert function["Properties"]["PackageType"] == "Image"


def test_every_agent_log_group_expires(agent_template: Template) -> None:
    groups = agent_template.find_resources("AWS::Logs::LogGroup")
    assert groups
    for group in groups.values():
        assert group["Properties"]["RetentionInDays"] == 14


def test_the_ask_url_is_not_open_to_the_internet(agent_template: Template) -> None:
    url = sole(agent_template, "AWS::Lambda::Url")["Properties"]
    assert url["AuthType"] == "AWS_IAM"


def test_the_model_grant_carries_no_wildcard(agent_template: Template) -> None:
    invokes = [
        statement
        for statement in agent_role_policies(agent_template)
        if "bedrock:InvokeModel" in str(statement["Action"])
    ]
    assert invokes
    for statement in invokes:
        assert "*" not in str(statement["Resource"]).replace("global.", "")
    # every grant on the answering model is conditioned on the profile it came through. the
    # one without a condition is the knowledge base embedding its corpus
    answering = [item for item in invokes if "claude" in str(item["Resource"])]
    assert answering
    assert all(item.get("Condition") for item in answering)


def test_the_global_profile_grant_has_all_three_parts(agent_template: Template) -> None:
    invokes = [
        statement
        for statement in agent_role_policies(agent_template)
        if "bedrock:InvokeModel" in str(statement["Action"])
        and "inference-profile" in str(statement["Resource"])
    ]
    assert invokes
    profiles = [statement for statement in invokes if "global." in str(statement["Resource"])]
    assert profiles


def test_the_functions_hold_no_aws_managed_policy(agent_template: Template) -> None:
    for role in agent_template.find_resources("AWS::IAM::Role").values():
        arns = str(role["Properties"].get("ManagedPolicyArns", []))
        assert "iam::aws:policy" not in arns


def test_the_agent_reads_the_lake_through_the_policy_the_data_stack_wrote(
    agent_template: Template, data_template: Template
) -> None:
    named = {
        policy["Properties"]["ManagedPolicyName"]
        for policy in data_template.find_resources("AWS::IAM::ManagedPolicy").values()
    }
    assert f"pitadvisor-agent-lake-{ENV_NAME}" in named
    assert f"pitadvisor-kb-corpus-{ENV_NAME}" in named
    attached = str(
        [
            role["Properties"].get("ManagedPolicyArns", [])
            for role in agent_template.find_resources("AWS::IAM::Role").values()
        ]
    )
    assert f"pitadvisor-agent-lake-{ENV_NAME}" in attached


def test_the_ask_function_knows_which_knowledge_base_and_guardrail_to_use(
    agent_template: Template,
) -> None:
    _, function = only(
        agent_template, "AWS::Lambda::Function", FunctionName=f"pitadvisor-ask-{ENV_NAME}"
    )
    environment = function["Properties"]["Environment"]["Variables"]
    assert "PITADV_KNOWLEDGE_BASE_ID" in environment
    assert "PITADV_GUARDRAIL_ID" in environment
    # no ~/.aws in the image, so a named profile is a ProfileNotFound at the first call
    assert environment["PITADV_AWS_PROFILE"] == ""


def test_the_agent_stack_is_tagged_as_its_own_component(agent_template: Template) -> None:
    _, function = only(
        agent_template, "AWS::Lambda::Function", FunctionName=f"pitadvisor-ask-{ENV_NAME}"
    )
    tags = {tag["Key"]: tag["Value"] for tag in function["Properties"].get("Tags", [])}
    assert tags.get("component") == "agent"
