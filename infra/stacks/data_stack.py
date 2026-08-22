from typing import Any

from aws_cdk import (
    Acknowledgment,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    Validations,
)
from aws_cdk import (
    aws_athena as athena,
)
from aws_cdk import (
    aws_glue as glue,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct

SCAN_CAP_BYTES = 1024**3

# what a laptop may write. dbt on the athena target needs silver and gold too
LAPTOP_PREFIXES = ("raw", "bronze", "silver", "gold", "quarantine", "views")

WRITE_PREFIXES = (
    "Write access is granted per prefix rather than per object. Keys inside the lake are "
    "generated from source, season, event and session, or by dbt, so an object-level list "
    "would be rewritten on every partition change. docs/ and cache/ stay unwritable from a "
    "laptop: they belong to the knowledge base and to the session ingest."
)

CATALOG_WILDCARD = (
    "dbt creates and drops silver and gold tables on every build and catalog-sync rewrites the "
    "bronze tables from the contracts, so the table grant cannot be enumerated. Scoped to the "
    "pitadvisor database."
)

OBJECT_WILDCARD = (
    "Read access covers the whole bucket by design. This is a lake: keys are generated per "
    "source, season, event and session, so a prefix allowlist would need rewriting on every "
    "schema change. The grant is still scoped to these two buckets and to read actions."
)

NO_ACCESS_LOGS = (
    "Server access logging is deliberately off. This is a single-user analytics account with no "
    "third-party writers; object-level access logs would be a second unbounded copy of the data "
    "bucket's traffic inside a $20/month cap, and the CloudTrail management trail already covers "
    "the control plane. Accepted risk."
)


class DataStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("component", "data")

        bucket_name = (
            self.node.try_get_context("dataBucket") or f"pit-advisor-data-{env_name}-{self.account}"
        )

        self.bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=bucket_name,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-noncurrent",
                    # raw/ is immutable, so a noncurrent version means something overwrote it.
                    # 30 days is long enough to notice and replay, short enough not to accumulate.
                    noncurrent_version_expiration=Duration.days(30),
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                )
            ],
        )

        self.results_bucket = s3.Bucket(
            self,
            "AthenaResultsBucket",
            bucket_name=f"pit-advisor-athena-results-{env_name}-{self.account}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-results",
                    expiration=Duration.days(7),
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                )
            ],
        )

        for bucket in (self.bucket, self.results_bucket):
            Validations.of(bucket).acknowledge(
                Acknowledgment(id="AwsSolutions-S1", reason=NO_ACCESS_LOGS)
            )

        self.database_name = f"pitadvisor_{env_name}"
        self.database = glue.CfnDatabase(
            self,
            "Database",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=self.database_name,
                description="pit advisor medallion catalog",
                location_uri=f"s3://{self.bucket.bucket_name}/",
            ),
        )

        self.workgroup_name = self.node.try_get_context("athenaWorkgroup") or "pitadvisor"
        self.workgroup = athena.CfnWorkGroup(
            self,
            "WorkGroup",
            name=self.workgroup_name,
            description="byte-capped workgroup for pit advisor marts",
            state="ENABLED",
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
                requester_pays_enabled=False,
                bytes_scanned_cutoff_per_query=SCAN_CAP_BYTES,
                engine_version=athena.CfnWorkGroup.EngineVersionProperty(
                    selected_engine_version="AUTO"
                ),
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{self.results_bucket.bucket_name}/athena/",
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_S3"
                    ),
                ),
            ),
        )

        dev_user = iam.User.from_user_name(
            self,
            "DevUser",
            self.node.try_get_context("devUserName") or "pitadvisor-dev",
        )
        # managed, not inline: a user's inline policies share a 2048 byte budget and three
        # stacks attach to this one. the name is per stack, because two stacks attaching an
        # unnamed policy to the same user generate the same physical name
        dev_access = iam.ManagedPolicy(
            self,
            "DevManagedAccess",
            managed_policy_name=f"pitadvisor-lake-access-{env_name}",
            users=[dev_user],
            statements=[
                iam.PolicyStatement(
                    actions=["s3:ListBucket", "s3:GetBucketLocation"],
                    resources=[self.bucket.bucket_arn, self.results_bucket.bucket_arn],
                ),
                iam.PolicyStatement(
                    actions=["s3:GetObject"],
                    resources=[self.bucket.arn_for_objects("*")],
                ),
                iam.PolicyStatement(
                    actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    resources=[self.results_bucket.arn_for_objects("*")],
                ),
                iam.PolicyStatement(
                    actions=["s3:PutObject", "s3:DeleteObject"],
                    resources=[
                        self.bucket.arn_for_objects(f"{prefix}/*") for prefix in LAPTOP_PREFIXES
                    ],
                ),
                iam.PolicyStatement(
                    actions=[
                        "glue:GetDatabase",
                        "glue:GetDatabases",
                        "glue:GetTable",
                        "glue:GetTables",
                        "glue:GetPartition",
                        "glue:GetPartitions",
                        "glue:BatchGetPartition",
                        "glue:CreateTable",
                        "glue:UpdateTable",
                        "glue:DeleteTable",
                        "glue:BatchCreatePartition",
                        "glue:BatchDeletePartition",
                    ],
                    # literal partition: an acknowledgment id cannot hold what ${AWS::Partition}
                    # renders to
                    resources=[
                        f"arn:aws:glue:{self.region}:{self.account}:catalog",
                        f"arn:aws:glue:{self.region}:{self.account}:database/{self.database_name}",
                        f"arn:aws:glue:{self.region}:{self.account}:table/{self.database_name}/*",
                    ],
                ),
                iam.PolicyStatement(
                    actions=[
                        "athena:GetWorkGroup",
                        "athena:StartQueryExecution",
                        "athena:GetQueryExecution",
                        "athena:GetQueryResults",
                        "athena:StopQueryExecution",
                    ],
                    resources=[
                        self.format_arn(
                            service="athena",
                            resource="workgroup",
                            resource_name=self.workgroup_name,
                        )
                    ],
                ),
            ],
        )
        Validations.of(dev_access).acknowledge(
            Acknowledgment(
                id="AwsSolutions-IAM5[Resource::<DataBucketE3889A50.Arn>/*]",
                reason=OBJECT_WILDCARD,
            ),
            Acknowledgment(
                id="AwsSolutions-IAM5[Resource::<AthenaResultsBucket879938FA.Arn>/*]",
                reason=OBJECT_WILDCARD,
            ),
            *(
                Acknowledgment(
                    id=f"AwsSolutions-IAM5[Resource::<DataBucketE3889A50.Arn>/{prefix}/*]",
                    reason=WRITE_PREFIXES,
                )
                for prefix in LAPTOP_PREFIXES
            ),
            Acknowledgment(
                id=(
                    "AwsSolutions-IAM5[Resource::arn:aws:glue:"
                    f"{self.region}:{self.account}:table/{self.database_name}/*]"
                ),
                reason=CATALOG_WILDCARD,
            ),
        )

        # the pipeline's role lives in the transform stack, but the grant has to be written
        # where the bucket is, so it is a managed policy that stack attaches by name
        self.pipeline_policy_name = f"pitadvisor-pipeline-lake-{env_name}"
        pipeline_access = iam.ManagedPolicy(
            self,
            "PipelineLakeAccess",
            managed_policy_name=self.pipeline_policy_name,
            description="what the weekend pipeline may read and write in the lake",
            statements=[
                iam.PolicyStatement(
                    actions=["s3:ListBucket", "s3:GetBucketLocation"],
                    resources=[self.bucket.bucket_arn, self.results_bucket.bucket_arn],
                ),
                iam.PolicyStatement(
                    actions=["s3:GetObject"],
                    resources=[self.bucket.arn_for_objects("*")],
                ),
                iam.PolicyStatement(
                    actions=["s3:PutObject", "s3:DeleteObject"],
                    resources=[
                        self.bucket.arn_for_objects(f"{prefix}/*")
                        for prefix in ("raw", "bronze", "silver", "gold", "quarantine", "views")
                    ],
                ),
                iam.PolicyStatement(
                    actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    resources=[self.results_bucket.arn_for_objects("*")],
                ),
            ],
        )
        Validations.of(pipeline_access).acknowledge(
            Acknowledgment(
                id="AwsSolutions-IAM5[Resource::<DataBucketE3889A50.Arn>/*]", reason=OBJECT_WILDCARD
            ),
            Acknowledgment(
                id="AwsSolutions-IAM5[Resource::<AthenaResultsBucket879938FA.Arn>/*]",
                reason=OBJECT_WILDCARD,
            ),
            *(
                Acknowledgment(
                    id=f"AwsSolutions-IAM5[Resource::<DataBucketE3889A50.Arn>/{prefix}/*]",
                    reason=WRITE_PREFIXES,
                )
                for prefix in ("raw", "bronze", "silver", "gold", "quarantine", "views")
            ),
        )

        CfnOutput(self, "DataBucketName", value=self.bucket.bucket_name)
        CfnOutput(self, "AthenaResultsBucketName", value=self.results_bucket.bucket_name)
        CfnOutput(self, "GlueDatabaseName", value=self.database_name)
        CfnOutput(self, "AthenaWorkGroupName", value=self.workgroup_name)
